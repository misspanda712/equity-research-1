import os
from datetime import date
from pathlib import Path

import anthropic
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

from src.fetcher import load_from_file, fetch_transcripts, fetch_transcripts_auto, fetch_transcripts_edgar
from src.analyzer import compare_transcripts
from src.longitudinal import fetch_all_quarters, extract_quarter_signals, analyze_longitudinal_patterns

TICKERS = ["IMO", "CVE", "FANG", "MUR", "LNG", "NEXT"]
SOURCE_MAP = {
    "Auto (EDGAR → Motley Fool)": "auto",
    "SEC EDGAR": "edgar",
    "Motley Fool": "motleyfool",
}

st.set_page_config(
    page_title="Equity Research — Transcript Analyser",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)


def _init_session_state():
    for key, default in [
        ("diff_result", None),
        ("long_result", None),
        ("last_ticker", None),
        ("last_mode", None),
    ]:
        if key not in st.session_state:
            st.session_state[key] = default


def _clear_results_if_changed(ticker: str, mode: str):
    if ticker != st.session_state.last_ticker or mode != st.session_state.last_mode:
        st.session_state.diff_result = None
        st.session_state.long_result = None
        st.session_state.last_ticker = ticker
        st.session_state.last_mode = mode


def _get_api_client() -> anthropic.Anthropic:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        st.error(
            "ANTHROPIC_API_KEY is not set. Copy `.env.example` to `.env` and add your key, "
            "or set the environment variable before launching the app."
        )
        st.stop()
    return anthropic.Anthropic(api_key=api_key)


def _run_diff(ticker: str, source: str, use_fixtures: bool):
    if use_fixtures:
        fixture_dir = Path("tests/fixtures")
        matches = sorted(fixture_dir.glob(f"{ticker.lower()}_q*.txt"), reverse=True)
        if len(matches) < 2:
            st.warning(f"No fixture files found for {ticker}. Try a live fetch or select IMO.")
            st.stop()
        st.info("Using sample data — no API call made")
        try:
            current = load_from_file(str(matches[0]))
            prior = load_from_file(str(matches[1]))
        except Exception as e:
            st.error(str(e))
            st.stop()
    else:
        client = _get_api_client()  # noqa: F841 — validates key exists before fetching
        try:
            with st.spinner("Fetching press releases from SEC EDGAR..."):
                if source == "edgar":
                    transcripts = fetch_transcripts_edgar(ticker)
                elif source == "motleyfool":
                    transcripts = fetch_transcripts(ticker)
                else:
                    transcripts = fetch_transcripts_auto(ticker)
            current, prior = transcripts[0], transcripts[1]
        except Exception as e:
            st.error(str(e))
            st.stop()

    try:
        with st.spinner("Comparing transcripts with Claude..."):
            markdown_output = compare_transcripts(current, prior)
    except Exception as e:
        st.error(str(e))
        st.stop()

    today = date.today().isoformat()
    header = (
        f"# {ticker} — Earnings Transcript Diff\n"
        f"_{prior.quarter} {prior.year} vs {current.quarter} {current.year} "
        f"| Generated {today} | Model: claude-sonnet-4-6_\n\n"
    )
    full_report = header + markdown_output

    st.session_state.diff_result = {
        "prior": prior,
        "current": current,
        "markdown_output": markdown_output,
        "full_report": full_report,
        "ticker": ticker,
        "today": today,
    }


def _run_longitudinal(ticker: str, years: int):
    client = _get_api_client()

    transcripts = []
    signals = []

    try:
        with st.status(f"Fetching {years} years of press releases from SEC EDGAR...") as status:
            transcripts = fetch_all_quarters(ticker, years)
            if not transcripts:
                st.error(f"No earnings press releases found for {ticker} on SEC EDGAR.")
                st.stop()

            for t in transcripts:
                label = f"{t.quarter} {t.year}"
                status.update(label=f"Extracting signals: {label}...")
                sig = extract_quarter_signals(t, client)
                signals.append(sig)

            status.update(label="Running pattern analysis...")
            analysis_text = analyze_longitudinal_patterns(signals, ticker, client)
            status.update(label="Done", state="complete")
    except Exception as e:
        st.error(str(e))
        st.stop()

    today = date.today().isoformat()
    start_q = f"{transcripts[0].quarter} {transcripts[0].year}"
    end_q = f"{transcripts[-1].quarter} {transcripts[-1].year}"
    n = len(transcripts)

    header = (
        f"# {ticker} — Management Language Longitudinal Analysis\n"
        f"_{n} quarters analyzed | {start_q} – {end_q} | Source: SEC EDGAR_\n\n"
    )
    full_report = header + analysis_text

    out_path = Path("output")
    out_path.mkdir(parents=True, exist_ok=True)
    output_file = out_path / f"{ticker}_longitudinal_{today}.md"
    output_file.write_text(full_report, encoding="utf-8")

    st.session_state.long_result = {
        "ticker": ticker,
        "signals": signals,
        "analysis_text": analysis_text,
        "full_report": full_report,
        "start_q": start_q,
        "end_q": end_q,
        "today": today,
    }


def _display_diff_result(result: dict):
    prior = result["prior"]
    current = result["current"]
    st.success(
        f"Analysis complete — {prior.quarter} {prior.year} vs {current.quarter} {current.year}"
    )
    st.markdown(result["markdown_output"])
    st.download_button(
        label="Download Report (.md)",
        data=result["full_report"],
        file_name=f"{result['ticker']}_{result['today']}.md",
        mime="text/markdown",
    )


def _build_signal_rows(signals: list[dict]) -> list[dict]:
    rows = []
    for s in signals:
        qual = s.get("qualitative", {}) or {}
        capex_q = qual.get("capex", {}) or {}
        prod_q = qual.get("production", {}) or {}
        bd = qual.get("buybacks_dividends", {}) or {}
        hedging = qual.get("hedging", {}) or {}
        financials = s.get("financials", {}) or {}

        rows.append(
            {
                "Quarter": s.get("quarter"),
                "Year": s.get("year"),
                "Overall Tone": s.get("overall_tone"),
                "Capex Tone": capex_q.get("tone"),
                "Production Tone": prod_q.get("tone"),
                "Returns Action": bd.get("action_taken"),
                "Hedging": hedging.get("tone"),
                "OCF ($M)": financials.get("operating_cash_flow_mm"),
                "Capex Actual ($M)": financials.get("capex_actual_mm"),
                "Production (kboe/d)": financials.get("production_kboed"),
            }
        )
    return rows


def _display_long_result(result: dict):
    st.success(
        f"Analysis complete — {result['start_q']} through {result['end_q']}"
    )
    st.subheader("Quarterly Signal Data")
    rows = _build_signal_rows(result["signals"])
    st.dataframe(rows, use_container_width=True)
    st.divider()
    st.markdown(result["analysis_text"])
    st.download_button(
        label="Download Report (.md)",
        data=result["full_report"],
        file_name=f"{result['ticker']}_longitudinal_{result['today']}.md",
        mime="text/markdown",
    )


def _render_landing():
    st.header("Equity Research — Transcript Analyser")
    st.write(
        "This tool fetches earnings press releases from SEC EDGAR (or Motley Fool) and uses "
        "Claude to surface material shifts in management language across five key investment topics. "
        "Choose a mode in the sidebar, select a ticker, and click **Run Analysis**."
    )

    col1, col2 = st.columns(2)
    with col1:
        st.info(
            "**Quarter Diff**\n\n"
            "Compares the two most recent quarters side by side. Produces a structured "
            "Bullish / Bearish / Neutral / Watch signal for each of: Capex Guidance, "
            "Production Outlook, Buyback & Dividend Policy, Hedging Posture, and New Risk Factors."
        )
    with col2:
        st.info(
            "**Longitudinal Analysis**\n\n"
            "Pulls up to 7 years of quarterly press releases, extracts structured signals "
            "from each quarter, and runs a pattern analysis — identifying tone cycles, "
            "language leading indicators, and capital returns signals over time."
        )

    sample_diff = Path("output/IMO_2025-05-11.md")
    sample_long = Path("output/IMO_longitudinal_2025-05-11.md")

    if sample_diff.exists():
        with st.expander("Sample: IMO Quarter Diff"):
            st.markdown(sample_diff.read_text(encoding="utf-8"))

    if sample_long.exists():
        with st.expander("Sample: IMO Longitudinal Analysis"):
            st.markdown(sample_long.read_text(encoding="utf-8"))


def main():
    _init_session_state()

    with st.sidebar:
        st.markdown("### Transcript Analyser")

        ticker = st.selectbox("Ticker", TICKERS)
        mode = st.radio("Mode", ["Quarter Diff", "Longitudinal Analysis"])

        source_label = None
        use_fixtures = False
        years = 5

        if mode == "Quarter Diff":
            source_label = st.selectbox(
                "Source", list(SOURCE_MAP.keys())
            )
            use_fixtures = st.checkbox("Use sample data (no API key needed)")
        else:
            years = st.slider("Years of history", min_value=1, max_value=7, value=5)

        st.markdown("---")
        run_clicked = st.button("Run Analysis", type="primary")
        st.markdown("_Results are cached — repeat runs for the same ticker are fast._")
        st.markdown(
            "<div style='position: fixed; bottom: 1rem;'>"
            "<small>Model: claude-sonnet-4-6 · Source: SEC EDGAR / Motley Fool</small>"
            "</div>",
            unsafe_allow_html=True,
        )

    _clear_results_if_changed(ticker, mode)

    if run_clicked:
        if mode == "Quarter Diff":
            _run_diff(ticker, SOURCE_MAP[source_label], use_fixtures)
        else:
            _run_longitudinal(ticker, years)

    if mode == "Quarter Diff":
        if st.session_state.diff_result:
            _display_diff_result(st.session_state.diff_result)
        else:
            _render_landing()
    else:
        if st.session_state.long_result:
            _display_long_result(st.session_state.long_result)
        else:
            _render_landing()


if __name__ == "__main__":
    main()
