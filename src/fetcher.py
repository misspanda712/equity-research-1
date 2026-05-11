import re
import sys
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

EDGAR_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
EDGAR_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
EDGAR_ARCHIVE_BASE = "https://www.sec.gov/Archives/edgar/data"
EDGAR_HEADERS = {
    "User-Agent": "equity-research-bot/1.0 research@example.com",
    "Accept": "application/json, text/html, */*",
}
CANADIAN_TICKERS = {"IMO", "CVE", "SU", "CNQ", "ERF", "TOU"}


def _get_with_retry(url: str, params: dict = None, headers: dict = None) -> requests.Response:
    _headers = headers if headers is not None else HEADERS
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(url, headers=_headers, params=params, timeout=TIMEOUT)
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


def _get_cik(ticker: str) -> str:
    resp = _get_with_retry(EDGAR_TICKERS_URL, headers=EDGAR_HEADERS)
    data = resp.json()
    ticker_upper = ticker.upper()
    for entry in data.values():
        if entry.get("ticker", "").upper() == ticker_upper:
            return str(entry["cik_str"]).zfill(10)
    raise ValueError(f"Ticker {ticker} not found in SEC EDGAR")


def _get_edgar_filings(cik: str, form_types: list[str], n: int) -> list[dict]:
    url = EDGAR_SUBMISSIONS_URL.format(cik=cik)
    resp = _get_with_retry(url, headers=EDGAR_HEADERS)
    data = resp.json()
    recent = data.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    accessions = recent.get("accessionNumber", [])
    primary_docs = recent.get("primaryDocument", [])
    results = []
    for form, date, accession, primary_doc in zip(forms, dates, accessions, primary_docs):
        if form in form_types:
            results.append({"form": form, "date": date, "accession": accession, "primary_doc": primary_doc})
        if len(results) >= n:
            break
    return results


def _fetch_edgar_exhibit(cik: str, accession: str) -> tuple[str, str]:
    accession_nodash = accession.replace("-", "")
    cik_stripped = cik.lstrip("0")
    index_url = f"{EDGAR_ARCHIVE_BASE}/{cik_stripped}/{accession_nodash}/index.json"
    resp = _get_with_retry(index_url, headers=EDGAR_HEADERS)
    index_data = resp.json()
    items = index_data.get("directory", {}).get("item", [])
    filename = None
    for item in items:
        if item.get("type") == "EX-99.1":
            filename = item["name"]
            break
    if filename is None:
        for item in items:
            name = item.get("name", "")
            if name.endswith(".htm") or name.endswith(".txt"):
                filename = name
                break
    if filename is None and items:
        filename = items[0].get("name", "")
    doc_url = f"{EDGAR_ARCHIVE_BASE}/{cik_stripped}/{accession_nodash}/{filename}"
    doc_resp = _get_with_retry(doc_url, headers=EDGAR_HEADERS)
    return doc_resp.text, filename


def _parse_press_release(html: str, ticker: str, filing_date: str, url: str) -> Transcript:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    body_tag = soup.find("body")
    text = body_tag.get_text(separator="\n", strip=True) if body_tag else soup.get_text(separator="\n", strip=True)

    quarter = ""
    year = 0
    text_lower = text.lower()
    quarter_map = {"first": "Q1", "second": "Q2", "third": "Q3", "fourth": "Q4"}
    for word, q in quarter_map.items():
        pattern = rf"{word}\s+quarter\s+(\d{{4}})"
        m = re.search(pattern, text_lower)
        if m:
            quarter = q
            year = int(m.group(1))
            break
    if not quarter:
        m = re.search(r"q([1-4])\s+(\d{4})", text_lower)
        if m:
            quarter = f"Q{m.group(1)}"
            year = int(m.group(2))
    if not quarter:
        m = re.search(r"(\d{4})\s+q([1-4])", text_lower)
        if m:
            year = int(m.group(1))
            quarter = f"Q{m.group(2)}"

    h1 = soup.find("h1")
    title_tag = soup.find("title")
    if h1:
        company_name = h1.get_text(strip=True)
    elif title_tag:
        company_name = title_tag.get_text(strip=True)
    else:
        company_name = ticker

    return Transcript(
        ticker=ticker.upper(),
        company_name=company_name,
        quarter=quarter,
        year=year,
        date=filing_date,
        url=url,
        text="SOURCE: SEC EDGAR (earnings press release)\n\n" + text,
    )


def fetch_transcripts_edgar(ticker: str, n: int = 2) -> list[Transcript]:
    form_types = ["6-K"] if ticker.upper() in CANADIAN_TICKERS else ["8-K"]
    cik = _get_cik(ticker)
    filings = _get_edgar_filings(cik, form_types, n * 4)
    if len(filings) < n:
        raise ValueError(
            f"Found only {len(filings)} {form_types[0]} filing(s) for {ticker} on SEC EDGAR. Need at least {n}."
        )
    transcripts = []
    for filing in filings[:n]:
        accession = filing["accession"]
        cik_stripped = cik.lstrip("0")
        accession_nodash = accession.replace("-", "")
        url = f"{EDGAR_ARCHIVE_BASE}/{cik_stripped}/{accession_nodash}/{filing['primary_doc']}"
        html, _ = _fetch_edgar_exhibit(cik, accession)
        transcript = _parse_press_release(html, ticker, filing["date"], url)
        transcripts.append(transcript)
    return transcripts


def fetch_transcripts_auto(ticker: str, n: int = 2) -> list[Transcript]:
    try:
        return fetch_transcripts_edgar(ticker, n)
    except Exception as e:
        sys.stderr.write(f"EDGAR fetch failed ({e}), falling back to Motley Fool\n")
        return fetch_transcripts(ticker, n)


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
