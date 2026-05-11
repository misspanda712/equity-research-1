import json
import os
import re
import sys
import time
from datetime import date
from pathlib import Path

import anthropic

from src.fetcher import (
    CANADIAN_TICKERS,
    _fetch_edgar_exhibit,
    _get_cik,
    _get_edgar_filings,
    _parse_press_release,
)
from src.models import Transcript

MODEL = "claude-sonnet-4-6"

EARNINGS_KEYWORDS = [
    "earnings per share",
    "results of operations",
    "cash flow from operations",
    "net income",
    "production",
]

EXTRACT_SYSTEM = (
    "You are a structured data extraction engine for equity research. "
    "Extract information from earnings press releases as JSON. "
    "Be precise. Use null for any field not found in the document."
)

EXTRACT_SCHEMA = """{
  "ticker": "string",
  "quarter": "Q1|Q2|Q3|Q4",
  "year": 2024,
  "date": "YYYY-MM-DD",
  "qualitative": {
    "capex": {
      "tone": "bullish|cautious|neutral|not_mentioned",
      "specificity": "high|medium|low",
      "guidance_given": true,
      "key_phrases": ["up to 3 verbatim short phrases from the document"],
      "yoy_direction": "increase|decrease|stable|not_mentioned"
    },
    "production": {
      "tone": "bullish|cautious|neutral|not_mentioned",
      "specificity": "high|medium|low",
      "guidance_given": true,
      "key_phrases": ["up to 3 phrases"],
      "yoy_direction": "increase|decrease|stable|not_mentioned"
    },
    "buybacks_dividends": {
      "tone": "bullish|cautious|neutral|not_mentioned",
      "key_phrases": ["up to 3 phrases"],
      "action_taken": "increase|decrease|maintained|none|not_mentioned"
    },
    "hedging": {
      "tone": "active|passive|none|not_mentioned",
      "key_phrases": ["up to 3 phrases"]
    },
    "risks": {
      "new_risks_mentioned": ["list of new risk themes, empty array if none"],
      "recurring_risks": ["list of recurring risk themes"],
      "overall_risk_tone": "elevated|normal|reduced|not_mentioned"
    }
  },
  "overall_tone": "confident|cautious|mixed|defensive",
  "uncertainty_markers_count": 0,
  "macro_themes": ["list of macro themes mentioned, e.g. tariffs, OPEC, WCS differentials"],
  "financials": {
    "production_kboed": null,
    "operating_cash_flow_mm": null,
    "capex_actual_mm": null,
    "capex_guidance_mm": null,
    "net_income_mm": null,
    "dividend_per_share": null,
    "buyback_mm": null
  }
}"""


def _raw_cache_path(ticker: str, year: int, quarter: str) -> Path:
    return Path("cache") / ticker / "raw" / f"{year}_{quarter}.txt"


def _signals_cache_path(ticker: str, year: int, quarter: str) -> Path:
    return Path("cache") / ticker / "signals" / f"{year}_{quarter}.json"


def _load_all_cached_quarters(ticker: str) -> list[Transcript]:
    raw_dir = Path("cache") / ticker / "raw"
    if not raw_dir.exists():
        return []
    cached = []
    for cache_file in raw_dir.glob("*.txt"):
        stem = cache_file.stem
        m = re.match(r"(\d{4})_(Q[1-4])$", stem, re.IGNORECASE)
        if not m:
            continue
        year = int(m.group(1))
        quarter = m.group(2).upper()
        text = cache_file.read_text(encoding="utf-8")
        cached.append(
            Transcript(
                ticker=ticker,
                company_name=ticker,
                quarter=quarter,
                year=year,
                date="",
                url="",
                text=text,
            )
        )
    return cached


def fetch_all_quarters(ticker: str, years: int = 5) -> list[Transcript]:
    ticker_upper = ticker.upper()
    form_types = ["6-K"] if ticker_upper in CANADIAN_TICKERS else ["8-K"]
    n_filings = years * 6

    cached_transcripts = _load_all_cached_quarters(ticker_upper)
    seen_quarter_years: set[tuple[str, int]] = {(t.quarter, t.year) for t in cached_transcripts}

    if len(cached_transcripts) >= years * 4:
        cached_transcripts.sort(key=lambda t: (t.year, t.quarter))
        return cached_transcripts[: years * 4]

    cik = _get_cik(ticker_upper)
    filings = _get_edgar_filings(cik, form_types, n_filings)

    new_transcripts: list[Transcript] = []

    for filing in filings:
        accession = filing["accession"]
        filing_date = filing["date"]
        cik_stripped = cik.lstrip("0")
        accession_nodash = accession.replace("-", "")
        url = f"https://www.sec.gov/Archives/edgar/data/{cik_stripped}/{accession_nodash}/{filing['primary_doc']}"

        try:
            html, _ = _fetch_edgar_exhibit(cik, accession)
            time.sleep(0.15)
            transcript = _parse_press_release(html, ticker_upper, filing_date, url)
        except Exception:
            continue

        text_lower = transcript.text.lower()
        keyword_hits = sum(1 for kw in EARNINGS_KEYWORDS if kw in text_lower)
        if keyword_hits < 2:
            continue

        if not transcript.quarter or not transcript.year:
            continue

        key = (transcript.quarter, transcript.year)
        if key in seen_quarter_years:
            continue
        seen_quarter_years.add(key)

        cache_path = _raw_cache_path(ticker_upper, transcript.year, transcript.quarter)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(transcript.text, encoding="utf-8")

        new_transcripts.append(transcript)

    all_transcripts = cached_transcripts + new_transcripts
    all_transcripts.sort(key=lambda t: (t.year, t.quarter))
    max_results = years * 4
    return all_transcripts[:max_results]


def _load_raw_cache(ticker: str, year: int, quarter: str) -> str | None:
    path = _raw_cache_path(ticker.upper(), year, quarter)
    if path.exists():
        return path.read_text(encoding="utf-8")
    return None


def extract_quarter_signals(transcript: Transcript, client: anthropic.Anthropic) -> dict:
    ticker = transcript.ticker.upper()
    cache_path = _signals_cache_path(ticker, transcript.year, transcript.quarter)

    if cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))

    user_prompt = (
        f"Extract the following information from this earnings press release for {ticker} "
        f"{transcript.quarter} {transcript.year}.\n\n"
        f"Return ONLY valid JSON matching this exact schema — no prose, no markdown fences:\n"
        f"{EXTRACT_SCHEMA}\n\n"
        f"Press release text:\n{transcript.text}"
    )

    response = client.messages.create(
        model=MODEL,
        max_tokens=800,
        system=EXTRACT_SYSTEM,
        messages=[{"role": "user", "content": user_prompt}],
    )

    raw = response.content[0].text.strip()

    if raw.startswith("```"):
        lines = raw.splitlines()
        start = 1 if lines[0].startswith("```") else 0
        end = len(lines)
        for i in range(len(lines) - 1, -1, -1):
            if lines[i].strip() == "```":
                end = i
                break
        raw = "\n".join(lines[start:end]).strip()

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        sys.stderr.write(
            f"Warning: could not parse JSON for {ticker} {transcript.quarter} {transcript.year}\n"
        )
        result = {
            "ticker": ticker,
            "quarter": transcript.quarter,
            "year": transcript.year,
        }

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def analyze_longitudinal_patterns(
    signals: list[dict], ticker: str, client: anthropic.Anthropic
) -> str:
    system = (
        f"You are a senior equity research analyst. You have been given a time-series dataset of "
        f"management language signals and financial metrics for {ticker} over multiple quarters. "
        f"Identify patterns, correlations, and investment-relevant insights."
    )

    signals_json = json.dumps(signals, indent=2)

    user_prompt = (
        f"Here is the time-series dataset for {ticker}:\n\n"
        f"```json\n{signals_json}\n```\n\n"
        f"Produce a markdown analysis with these exact sections:\n\n"
        f"## Executive Summary\n"
        f"3-4 bullet points of the most important patterns found.\n\n"
        f"## Tone Cycle\n"
        f"How overall management confidence tracked against financial outcomes. "
        f"Identify quarters where tone shifted before financials did (potential leading indicator).\n\n"
        f"## Capex Language vs. Actuals\n"
        f"Did guidance language predict actual spending? Note any consistent over/under-promising.\n\n"
        f"## Production Cadence\n"
        f"Patterns in production guidance language vs. actual delivery.\n\n"
        f"## Capital Returns Signal\n"
        f"How language around buybacks/dividends correlated with free cash flow.\n\n"
        f"## Risk Factor Evolution\n"
        f"How the risk register changed over the period. Note any risks that appeared in language "
        f"before appearing in reported numbers.\n\n"
        f"## Language Patterns Worth Tracking\n"
        f"Specific phrases or patterns in this company's communication style that have historically "
        f"been meaningful (bullish or bearish signals)."
    )

    response = client.messages.create(
        model=MODEL,
        max_tokens=2000,
        system=system,
        messages=[{"role": "user", "content": user_prompt}],
    )

    return response.content[0].text


def _format_signal_table(signals: list[dict]) -> str:
    header = (
        "| Quarter | Tone | Capex | Production | Returns | Hedging | FCF ($M) | Capex ($M) |\n"
        "|---------|------|-------|------------|---------|---------|----------|------------|\n"
    )
    rows = []
    for s in signals:
        quarter = s.get("quarter", "—")
        year = s.get("year", "—")
        label = f"{quarter} {year}"

        tone = s.get("overall_tone", "—") or "—"

        qual = s.get("qualitative", {}) or {}
        capex_q = qual.get("capex", {}) or {}
        capex_tone = capex_q.get("tone", "—") or "—"
        capex_spec = capex_q.get("specificity", "—") or "—"
        capex_col = f"{capex_tone}/{capex_spec}" if capex_tone != "—" else "—"

        prod_q = qual.get("production", {}) or {}
        prod_tone = prod_q.get("tone", "—") or "—"
        prod_spec = prod_q.get("specificity", "—") or "—"
        prod_col = f"{prod_tone}/{prod_spec}" if prod_tone != "—" else "—"

        bd = qual.get("buybacks_dividends", {}) or {}
        returns_col = bd.get("action_taken", "—") or "—"

        hedging = qual.get("hedging", {}) or {}
        hedge_col = hedging.get("tone", "—") or "—"

        financials = s.get("financials", {}) or {}
        fcf = financials.get("operating_cash_flow_mm")
        capex_act = financials.get("capex_actual_mm")

        fcf_col = str(fcf) if fcf is not None else "—"
        capex_act_col = str(capex_act) if capex_act is not None else "—"

        rows.append(
            f"| {label} | {tone} | {capex_col} | {prod_col} | {returns_col} | {hedge_col} | {fcf_col} | {capex_act_col} |"
        )

    return header + "\n".join(rows)


def run_longitudinal_analysis(
    ticker: str, years: int = 5, output_dir: str = "./output"
) -> str:
    ticker_upper = ticker.upper()
    today = date.today().isoformat()

    sys.stderr.write(f"Fetching quarters for {ticker_upper}...\n")
    transcripts = fetch_all_quarters(ticker_upper, years)

    if not transcripts:
        raise ValueError(
            f"No earnings press releases found for {ticker_upper} on SEC EDGAR."
        )

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise EnvironmentError(
            "ANTHROPIC_API_KEY is not set. Add it to your .env file or environment."
        )
    client = anthropic.Anthropic(api_key=api_key)

    signals = []
    for transcript in transcripts:
        label = f"{transcript.quarter} {transcript.year}"
        sys.stderr.write(f"Processing {label}...\n")
        sig = extract_quarter_signals(transcript, client)
        signals.append(sig)

    sys.stderr.write("Running meta-analysis...\n")
    meta_analysis = analyze_longitudinal_patterns(signals, ticker_upper, client)

    start_quarter = f"{transcripts[0].quarter} {transcripts[0].year}" if transcripts else "—"
    end_quarter = f"{transcripts[-1].quarter} {transcripts[-1].year}" if transcripts else "—"
    n_quarters = len(transcripts)

    table = _format_signal_table(signals)

    report = (
        f"# {ticker_upper} — Management Language Longitudinal Analysis\n"
        f"_{n_quarters} quarters analyzed | {start_quarter} – {end_quarter} | Source: SEC EDGAR_\n\n"
        f"{meta_analysis}\n\n"
        f"---\n\n"
        f"## Quarterly Signal Data\n\n"
        f"{table}\n\n"
        f"---\n\n"
        f"## Raw Signals\n"
        f"_Full extracted data stored in `cache/{ticker_upper}/signals/`_\n"
    )

    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    output_file = out_path / f"{ticker_upper}_longitudinal_{today}.md"
    output_file.write_text(report, encoding="utf-8")

    sys.stderr.write(f"Report written to: {output_file}\n")
    return str(output_file)
