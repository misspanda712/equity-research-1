import re
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from src.models import Transcript

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; equity-research-bot/1.0)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}
TIMEOUT = 30
MAX_RETRIES = 3
SOLR_URL = "https://www.fool.com/search/solr.aspx"


def _get_with_retry(url: str, params: dict = None) -> requests.Response:
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(url, headers=HEADERS, params=params, timeout=TIMEOUT)
            if resp.status_code < 500:
                resp.raise_for_status()
                return resp
            if attempt < MAX_RETRIES - 1:
                time.sleep(2 ** attempt)
        except requests.exceptions.Timeout:
            if attempt < MAX_RETRIES - 1:
                time.sleep(2 ** attempt)
                continue
            raise
    raise requests.exceptions.RetryError(f"Failed after {MAX_RETRIES} attempts: {url}")


def _parse_transcript_page(html: str, url: str, ticker: str) -> Transcript:
    soup = BeautifulSoup(html, "html.parser")

    title_tag = soup.find("h1")
    title = title_tag.get_text(strip=True) if title_tag else ""

    company_name = ticker
    quarter = ""
    year = 0
    date = ""

    q_match = re.search(r"Q([1-4])\s+(\d{4})", title, re.IGNORECASE)
    if q_match:
        quarter = f"Q{q_match.group(1)}"
        year = int(q_match.group(2))

    date_tag = soup.find("time")
    if date_tag and date_tag.get("datetime"):
        date = date_tag["datetime"][:10]
    elif date_tag:
        date = date_tag.get_text(strip=True)

    article_selectors = [
        {"class": "article-body"},
        {"class": "foolish-article-content"},
        {"id": "article-body"},
        {"class": "content-block"},
    ]
    body = None
    for sel in article_selectors:
        body = soup.find("div", sel)
        if body:
            break
    if body is None:
        body = soup.find("article") or soup.find("main")

    text = body.get_text(separator="\n", strip=True) if body else soup.get_text(separator="\n", strip=True)

    if not company_name or company_name == ticker:
        name_match = re.match(r"^(.+?)\s+\(", title)
        if name_match:
            company_name = name_match.group(1).strip()

    return Transcript(
        ticker=ticker.upper(),
        company_name=company_name,
        quarter=quarter,
        year=year,
        date=date,
        url=url,
        text=text,
    )


def _search_solr(ticker: str, n: int) -> list[dict]:
    params = {
        "q": f"{ticker} earnings call transcript",
        "collection": "foolcom_articles",
        "sort": "date",
        "rows": str(max(n * 3, 10)),
    }
    resp = _get_with_retry(SOLR_URL, params=params)
    data = resp.json()

    docs = data.get("response", {}).get("docs", [])
    results = []
    for doc in docs:
        url = doc.get("url", "")
        headline = doc.get("headline", "")
        if "earnings" in headline.lower() and "transcript" in headline.lower():
            results.append({"url": url, "title": headline, "date": doc.get("publishDate", "")})
        if len(results) >= n:
            break
    return results


def fetch_transcripts(ticker: str, n: int = 2) -> list[Transcript]:
    search_results = _search_solr(ticker, n)

    if not search_results:
        raise ValueError(
            f"No earnings call transcripts found for {ticker} on Motley Fool. "
            "Try --use-fixtures for offline testing."
        )

    transcripts = []
    for result in search_results[:n]:
        url = result["url"]
        if not url.startswith("http"):
            url = "https://www.fool.com" + url
        resp = _get_with_retry(url)
        transcript = _parse_transcript_page(resp.text, url, ticker)
        transcripts.append(transcript)

    if len(transcripts) < 2:
        raise ValueError(
            f"Found only {len(transcripts)} transcript(s) for {ticker}. "
            "Need at least 2 to produce a diff. Try --use-fixtures."
        )

    return transcripts


def load_from_file(path: str) -> Transcript:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Fixture file not found: {path}")

    text = p.read_text(encoding="utf-8")

    filename = p.stem
    ticker_match = re.match(r"^([a-z]+)_", filename, re.IGNORECASE)
    ticker = ticker_match.group(1).upper() if ticker_match else "UNKNOWN"

    q_match = re.search(r"_(q[1-4])_(\d{4})", filename, re.IGNORECASE)
    quarter = q_match.group(1).upper() if q_match else ""
    year = int(q_match.group(2)) if q_match else 0

    company_match = re.search(r"^Company:\s*(.+)$", text, re.MULTILINE)
    company_name = company_match.group(1).strip() if company_match else ticker

    date_match = re.search(r"^Date:\s*(\S+)", text, re.MULTILINE)
    date = date_match.group(1) if date_match else ""

    return Transcript(
        ticker=ticker,
        company_name=company_name,
        quarter=quarter,
        year=year,
        date=date,
        url=f"file://{p.resolve()}",
        text=text,
    )
