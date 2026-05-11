import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.longitudinal import (
    _format_signal_table,
    extract_quarter_signals,
    fetch_all_quarters,
    run_longitudinal_analysis,
)
from src.models import Transcript

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _make_transcript(quarter="Q4", year=2024, ticker="IMO", text="some text"):
    return Transcript(
        ticker=ticker,
        company_name="Imperial Oil Limited",
        quarter=quarter,
        year=year,
        date="2025-02-07",
        url="https://example.com",
        text=text,
    )


MINIMAL_SIGNAL = {
    "ticker": "IMO",
    "quarter": "Q4",
    "year": 2024,
    "date": "2025-02-07",
    "qualitative": {
        "capex": {"tone": "bullish", "specificity": "high", "guidance_given": True, "key_phrases": [], "yoy_direction": "increase"},
        "production": {"tone": "bullish", "specificity": "high", "guidance_given": True, "key_phrases": [], "yoy_direction": "increase"},
        "buybacks_dividends": {"tone": "bullish", "key_phrases": [], "action_taken": "increase"},
        "hedging": {"tone": "active", "key_phrases": []},
        "risks": {"new_risks_mentioned": [], "recurring_risks": [], "overall_risk_tone": "normal"},
    },
    "overall_tone": "confident",
    "uncertainty_markers_count": 3,
    "macro_themes": ["tariffs"],
    "financials": {
        "production_kboed": 438,
        "operating_cash_flow_mm": 890,
        "capex_actual_mm": 520,
        "capex_guidance_mm": 2100,
        "net_income_mm": 620,
        "dividend_per_share": 0.22,
        "buyback_mm": 220,
    },
}


class TestFetchAllQuartersUsesCache:
    def test_fetch_all_quarters_uses_cache(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        cache_file = tmp_path / "cache" / "IMO" / "raw" / "2024_Q4.txt"
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_text = (
            "earnings per share increased. results of operations were strong. "
            "net income grew. production volumes up. cash flow from operations positive."
        )
        cache_file.write_text(cache_text, encoding="utf-8")

        with patch("src.longitudinal._get_cik") as mock_cik, \
             patch("src.longitudinal._get_edgar_filings") as mock_filings, \
             patch("src.longitudinal._fetch_edgar_exhibit") as mock_exhibit:

            mock_cik.return_value = "0000049196"
            mock_filings.return_value = []

            result = fetch_all_quarters("IMO", years=1)

        mock_exhibit.assert_not_called()
        assert len(result) == 1
        assert result[0].quarter == "Q4"
        assert result[0].year == 2024

    def test_fetch_all_quarters_filters_non_earnings(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        with patch("src.longitudinal._get_cik", return_value="0000049196"), \
             patch("src.longitudinal._get_edgar_filings", return_value=[
                 {"form": "6-K", "date": "2025-02-07", "accession": "0000049196-25-000001", "primary_doc": "doc.htm"},
             ]), \
             patch("src.longitudinal._fetch_edgar_exhibit", return_value=(
                 "<html><body><p>This is a routine update with no financial details.</p></body></html>",
                 "doc.htm",
             )), \
             patch("src.longitudinal.time.sleep"):

            result = fetch_all_quarters("IMO", years=1)

        assert result == []


class TestExtractQuarterSignals:
    def test_extract_quarter_signals_uses_cache(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        cache_file = tmp_path / "cache" / "IMO" / "signals" / "2024_Q4.json"
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(MINIMAL_SIGNAL), encoding="utf-8")

        mock_client = MagicMock()
        transcript = _make_transcript()

        result = extract_quarter_signals(transcript, mock_client)

        mock_client.messages.create.assert_not_called()
        assert result["ticker"] == "IMO"
        assert result["quarter"] == "Q4"

    def test_extract_quarter_signals_parses_json(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=json.dumps(MINIMAL_SIGNAL))]
        mock_client.messages.create.return_value = mock_response

        transcript = _make_transcript()
        result = extract_quarter_signals(transcript, mock_client)

        mock_client.messages.create.assert_called_once()
        assert result["ticker"] == "IMO"
        assert result["quarter"] == "Q4"
        assert result["year"] == 2024
        assert result["financials"]["operating_cash_flow_mm"] == 890

    def test_extract_quarter_signals_strips_code_fences(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        mock_client = MagicMock()
        wrapped = f"```json\n{json.dumps(MINIMAL_SIGNAL)}\n```"
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=wrapped)]
        mock_client.messages.create.return_value = mock_response

        transcript = _make_transcript()
        result = extract_quarter_signals(transcript, mock_client)

        assert result["ticker"] == "IMO"
        assert result["financials"]["capex_actual_mm"] == 520

    def test_extract_quarter_signals_handles_parse_error(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="sorry I can't do that")]
        mock_client.messages.create.return_value = mock_response

        transcript = _make_transcript()
        result = extract_quarter_signals(transcript, mock_client)

        assert "ticker" in result
        assert "quarter" in result
        assert "year" in result
        assert result["ticker"] == "IMO"


class TestRunLongitudinalAnalysis:
    def test_run_longitudinal_analysis_writes_output(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-123")

        t1 = _make_transcript("Q3", 2024, text="earnings per share net income production results of operations cash flow from operations")
        t2 = _make_transcript("Q4", 2024, text="earnings per share net income production results of operations cash flow from operations")

        signal1 = dict(MINIMAL_SIGNAL, quarter="Q3")
        signal2 = dict(MINIMAL_SIGNAL, quarter="Q4")

        with patch("src.longitudinal.fetch_all_quarters", return_value=[t1, t2]), \
             patch("src.longitudinal.extract_quarter_signals", side_effect=[signal1, signal2]), \
             patch("src.longitudinal.analyze_longitudinal_patterns", return_value="# Test Analysis"), \
             patch("src.longitudinal.anthropic.Anthropic"):

            output_path = run_longitudinal_analysis("IMO", years=2, output_dir=str(tmp_path))

        assert Path(output_path).exists()
        content = Path(output_path).read_text(encoding="utf-8")
        assert "IMO" in content
        assert "# Test Analysis" in content
        assert "Quarterly Signal Data" in content


class TestFormatSignalTable:
    def test_quarterly_signal_table_renders(self):
        signals = [
            {
                "ticker": "IMO",
                "quarter": "Q3",
                "year": 2024,
                "overall_tone": "confident",
                "qualitative": {
                    "capex": {"tone": "bullish", "specificity": "high"},
                    "production": {"tone": "bullish", "specificity": "high"},
                    "buybacks_dividends": {"action_taken": "maintained"},
                    "hedging": {"tone": "active"},
                },
                "financials": {
                    "operating_cash_flow_mm": 800,
                    "capex_actual_mm": 470,
                },
            },
            {
                "ticker": "IMO",
                "quarter": "Q4",
                "year": 2024,
                "overall_tone": "confident",
                "qualitative": {
                    "capex": {"tone": "bullish", "specificity": "high"},
                    "production": {"tone": "bullish", "specificity": "high"},
                    "buybacks_dividends": {"action_taken": "increase"},
                    "hedging": {"tone": "active"},
                },
                "financials": {
                    "operating_cash_flow_mm": 890,
                    "capex_actual_mm": 520,
                },
            },
        ]

        table = _format_signal_table(signals)

        assert "| Quarter |" in table
        assert "Q3 2024" in table
        assert "Q4 2024" in table
        assert "confident" in table
        assert "bullish/high" in table
        assert "800" in table
        assert "890" in table
        assert "520" in table
        assert table.count("|") > 10

    def test_table_uses_dash_for_null_financials(self):
        signals = [
            {
                "ticker": "IMO",
                "quarter": "Q1",
                "year": 2023,
                "overall_tone": "cautious",
                "qualitative": {
                    "capex": {"tone": "cautious", "specificity": "low"},
                    "production": {"tone": "cautious", "specificity": "low"},
                    "buybacks_dividends": {"action_taken": "maintained"},
                    "hedging": {"tone": "none"},
                },
                "financials": {
                    "operating_cash_flow_mm": None,
                    "capex_actual_mm": None,
                },
            }
        ]

        table = _format_signal_table(signals)
        rows = [line for line in table.splitlines() if "Q1 2023" in line]
        assert len(rows) == 1
        assert "—" in rows[0]
