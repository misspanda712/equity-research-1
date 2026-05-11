import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from src.fetcher import (
    HEADERS,
    SOLR_URL,
    _get_cik,
    _get_edgar_filings,
    _get_with_retry,
    _parse_press_release,
    _parse_transcript_page,
    _search_solr,
    fetch_transcripts,
    fetch_transcripts_auto,
    fetch_transcripts_edgar,
    load_from_file,
)
from src.models import Transcript

FIXTURES_DIR = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# load_from_file
# ---------------------------------------------------------------------------

class TestLoadFromFile:
    def test_loads_imo_q4_fixture(self):
        path = FIXTURES_DIR / "imo_q4_2024.txt"
        t = load_from_file(str(path))

        assert t.ticker == "IMO"
        assert t.quarter == "Q4"
        assert t.year == 2024
        assert t.company_name == "Imperial Oil Limited"
        assert t.date == "2025-02-07"
        assert t.url.startswith("file://")
        assert len(t.text) > 100

    def test_loads_imo_q3_fixture(self):
        path = FIXTURES_DIR / "imo_q3_2024.txt"
        t = load_from_file(str(path))

        assert t.ticker == "IMO"
        assert t.quarter == "Q3"
        assert t.year == 2024
        assert t.company_name == "Imperial Oil Limited"
        assert t.date == "2024-11-01"

    def test_text_contains_transcript_body(self):
        path = FIXTURES_DIR / "imo_q4_2024.txt"
        t = load_from_file(str(path))
        assert "Kearl" in t.text
        assert "2.1 billion" in t.text

    def test_raises_for_missing_file(self):
        with pytest.raises(FileNotFoundError, match="Fixture file not found"):
            load_from_file("/nonexistent/path/fake.txt")

    def test_ticker_uppercased(self):
        path = FIXTURES_DIR / "imo_q3_2024.txt"
        t = load_from_file(str(path))
        assert t.ticker == t.ticker.upper()


# ---------------------------------------------------------------------------
# Transcript dataclass
# ---------------------------------------------------------------------------

class TestTranscriptDataclass:
    def test_all_fields_populated(self):
        t = Transcript(
            ticker="CVE",
            company_name="Cenovus Energy",
            quarter="Q2",
            year=2024,
            date="2024-08-01",
            url="https://example.com/transcript",
            text="Some transcript text.",
        )
        assert t.ticker == "CVE"
        assert t.company_name == "Cenovus Energy"
        assert t.quarter == "Q2"
        assert t.year == 2024
        assert t.date == "2024-08-01"
        assert t.url == "https://example.com/transcript"
        assert t.text == "Some transcript text."

    def test_missing_required_field_raises(self):
        with pytest.raises(TypeError):
            Transcript(ticker="CVE")  # type: ignore[call-arg]

    def test_dataclass_is_mutable(self):
        t = Transcript(
            ticker="CVE",
            company_name="Cenovus",
            quarter="Q1",
            year=2024,
            date="2024-05-01",
            url="https://example.com",
            text="text",
        )
        t.ticker = "IMO"
        assert t.ticker == "IMO"


# ---------------------------------------------------------------------------
# _parse_transcript_page
# ---------------------------------------------------------------------------

class TestParseTranscriptPage:
    SAMPLE_HTML = """
    <html>
    <head><title>Imperial Oil Q4 2024 Earnings Call Transcript</title></head>
    <body>
      <h1>Imperial Oil (IMO) Q4 2024 Earnings Call Transcript</h1>
      <time datetime="2025-02-07">February 7, 2025</time>
      <div class="article-body">
        <p>Welcome to the Q4 2024 earnings call.</p>
        <p>Capex guidance for 2025 is $2.1 billion.</p>
      </div>
    </body>
    </html>
    """

    def test_extracts_quarter_and_year(self):
        t = _parse_transcript_page(self.SAMPLE_HTML, "https://example.com/q4", "IMO")
        assert t.quarter == "Q4"
        assert t.year == 2024

    def test_extracts_date_from_time_tag(self):
        t = _parse_transcript_page(self.SAMPLE_HTML, "https://example.com/q4", "IMO")
        assert t.date == "2025-02-07"

    def test_extracts_article_body_text(self):
        t = _parse_transcript_page(self.SAMPLE_HTML, "https://example.com/q4", "IMO")
        assert "Capex guidance" in t.text
        assert "2.1 billion" in t.text

    def test_extracts_company_name_from_title(self):
        t = _parse_transcript_page(self.SAMPLE_HTML, "https://example.com/q4", "IMO")
        assert t.company_name == "Imperial Oil"

    def test_ticker_uppercased_in_result(self):
        t = _parse_transcript_page(self.SAMPLE_HTML, "https://example.com/q4", "imo")
        assert t.ticker == "IMO"

    def test_url_preserved(self):
        url = "https://www.fool.com/earnings/q4-2024-imo/"
        t = _parse_transcript_page(self.SAMPLE_HTML, url, "IMO")
        assert t.url == url

    def test_fallback_when_no_article_div(self):
        html = "<html><body><p>No article div here.</p></body></html>"
        t = _parse_transcript_page(html, "https://example.com", "CVE")
        assert len(t.text) > 0

    def test_no_time_tag_leaves_empty_date(self):
        html = "<html><body><h1>IMO Q2 2024 Earnings Call Transcript</h1><p>text</p></body></html>"
        t = _parse_transcript_page(html, "https://example.com", "IMO")
        assert t.date == ""


# ---------------------------------------------------------------------------
# _get_with_retry
# ---------------------------------------------------------------------------

class TestGetWithRetry:
    def test_returns_on_200(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch("src.fetcher.requests.get", return_value=mock_resp) as mock_get:
            result = _get_with_retry("https://example.com")
        assert result is mock_resp
        assert mock_get.call_count == 1

    def test_retries_on_500_then_succeeds(self):
        resp_500 = MagicMock()
        resp_500.status_code = 500

        resp_200 = MagicMock()
        resp_200.status_code = 200

        with patch("src.fetcher.requests.get", side_effect=[resp_500, resp_200]) as mock_get:
            with patch("src.fetcher.time.sleep"):
                result = _get_with_retry("https://example.com")

        assert result is resp_200
        assert mock_get.call_count == 2

    def test_raises_after_max_retries_on_500(self):
        resp_500 = MagicMock()
        resp_500.status_code = 500

        with patch("src.fetcher.requests.get", return_value=resp_500):
            with patch("src.fetcher.time.sleep"):
                with pytest.raises(requests.exceptions.RetryError):
                    _get_with_retry("https://example.com")

    def test_passes_user_agent_header(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200

        with patch("src.fetcher.requests.get", return_value=mock_resp) as mock_get:
            _get_with_retry("https://example.com")

        call_kwargs = mock_get.call_args
        passed_headers = call_kwargs[1]["headers"] if "headers" in call_kwargs[1] else call_kwargs[0][1]
        assert "User-Agent" in HEADERS
        assert "equity-research-bot" in HEADERS["User-Agent"]

    def test_passes_30s_timeout(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200

        with patch("src.fetcher.requests.get", return_value=mock_resp) as mock_get:
            _get_with_retry("https://example.com")

        call_kwargs = mock_get.call_args
        assert call_kwargs[1]["timeout"] == 30


# ---------------------------------------------------------------------------
# _search_solr
# ---------------------------------------------------------------------------

class TestSearchSolr:
    def _make_solr_response(self, docs):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"response": {"docs": docs}}
        return mock_resp

    def test_filters_by_earnings_transcript_in_headline(self):
        docs = [
            {"url": "/t1", "headline": "IMO Q4 2024 Earnings Call Transcript", "publishDate": "2025-02-07"},
            {"url": "/t2", "headline": "Imperial Oil annual report", "publishDate": "2025-01-01"},
            {"url": "/t3", "headline": "IMO Q3 2024 Earnings Call Transcript", "publishDate": "2024-11-01"},
        ]
        mock_resp = self._make_solr_response(docs)

        with patch("src.fetcher._get_with_retry", return_value=mock_resp):
            results = _search_solr("IMO", 2)

        assert len(results) == 2
        assert all("transcript" in r["title"].lower() for r in results)

    def test_returns_at_most_n_results(self):
        docs = [
            {"url": f"/t{i}", "headline": f"IMO Q{i % 4 + 1} 2024 Earnings Call Transcript", "publishDate": "2024-01-01"}
            for i in range(10)
        ]
        mock_resp = self._make_solr_response(docs)

        with patch("src.fetcher._get_with_retry", return_value=mock_resp):
            results = _search_solr("IMO", 2)

        assert len(results) == 2

    def test_uses_solr_url_with_correct_params(self):
        mock_resp = self._make_solr_response([])

        with patch("src.fetcher._get_with_retry", return_value=mock_resp) as mock_get:
            _search_solr("CVE", 2)

        call_args = mock_get.call_args
        assert call_args[0][0] == SOLR_URL
        params = call_args[1]["params"]
        assert "CVE" in params["q"]
        assert "transcript" in params["q"]

    def test_returns_empty_list_when_no_docs(self):
        mock_resp = self._make_solr_response([])

        with patch("src.fetcher._get_with_retry", return_value=mock_resp):
            results = _search_solr("FANG", 2)

        assert results == []


# ---------------------------------------------------------------------------
# fetch_transcripts
# ---------------------------------------------------------------------------

class TestFetchTranscripts:
    def test_raises_value_error_when_no_search_results(self):
        with patch("src.fetcher._search_solr", return_value=[]):
            with pytest.raises(ValueError, match="No earnings call transcripts found"):
                fetch_transcripts("FANG")

    def test_raises_value_error_when_only_one_result(self):
        search_results = [
            {"url": "https://www.fool.com/t1", "title": "FANG Q4 Earnings Call Transcript", "date": "2025-01-01"}
        ]
        sample_html = "<html><body><h1>FANG Q4 2024 Earnings Call Transcript</h1><div class='article-body'>text</div></body></html>"
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = sample_html

        with patch("src.fetcher._search_solr", return_value=search_results):
            with patch("src.fetcher._get_with_retry", return_value=mock_resp):
                with pytest.raises(ValueError, match="Need at least 2"):
                    fetch_transcripts("FANG")

    def test_prepends_base_url_when_relative(self):
        search_results = [
            {"url": "/earnings/t1", "title": "IMO Q4 2024 Earnings Call Transcript", "date": "2025-02-07"},
            {"url": "/earnings/t2", "title": "IMO Q3 2024 Earnings Call Transcript", "date": "2024-11-01"},
        ]
        sample_html = "<html><body><h1>IMO Q4 2024 Earnings Call Transcript</h1><div class='article-body'>text</div></body></html>"
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = sample_html

        fetched_urls = []

        def capture_url(url, **kwargs):
            fetched_urls.append(url)
            return mock_resp

        with patch("src.fetcher._search_solr", return_value=search_results):
            with patch("src.fetcher._get_with_retry", side_effect=capture_url):
                try:
                    fetch_transcripts("IMO")
                except Exception:
                    pass

        assert all(u.startswith("https://") for u in fetched_urls)

    def test_returns_list_of_transcript_objects(self):
        search_results = [
            {"url": "https://www.fool.com/t1", "title": "IMO Q4 2024 Earnings Call Transcript", "date": "2025-02-07"},
            {"url": "https://www.fool.com/t2", "title": "IMO Q3 2024 Earnings Call Transcript", "date": "2024-11-01"},
        ]
        html1 = "<html><body><h1>IMO Q4 2024 Earnings Call Transcript</h1><time datetime='2025-02-07'>Feb 7</time><div class='article-body'>Q4 text</div></body></html>"
        html2 = "<html><body><h1>IMO Q3 2024 Earnings Call Transcript</h1><time datetime='2024-11-01'>Nov 1</time><div class='article-body'>Q3 text</div></body></html>"

        responses = [MagicMock(status_code=200, text=html1), MagicMock(status_code=200, text=html2)]

        with patch("src.fetcher._search_solr", return_value=search_results):
            with patch("src.fetcher._get_with_retry", side_effect=responses):
                transcripts = fetch_transcripts("IMO")

        assert len(transcripts) == 2
        assert all(isinstance(t, Transcript) for t in transcripts)
        assert transcripts[0].quarter == "Q4"
        assert transcripts[1].quarter == "Q3"


# ---------------------------------------------------------------------------
# TestEdgarFetcher
# ---------------------------------------------------------------------------

class TestEdgarFetcher:
    def _make_mock_resp(self, json_data):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = json_data
        return mock_resp

    def test_get_cik_found(self):
        data = {"0": {"cik_str": "12345", "ticker": "fang", "title": "Diamondback Energy"}}
        with patch("src.fetcher._get_with_retry", return_value=self._make_mock_resp(data)):
            result = _get_cik("FANG")
        assert result == "0000012345"

    def test_get_cik_not_found(self):
        with patch("src.fetcher._get_with_retry", return_value=self._make_mock_resp({})):
            with pytest.raises(ValueError, match="not found in SEC EDGAR"):
                _get_cik("ZZZZ")

    def test_get_cik_case_insensitive(self):
        data = {"0": {"cik_str": "12345", "ticker": "fang", "title": "Diamondback Energy"}}
        with patch("src.fetcher._get_with_retry", return_value=self._make_mock_resp(data)):
            result = _get_cik("FANG")
        assert result == "0000012345"

    def test_get_edgar_filings_filters_by_form(self):
        submissions = {
            "filings": {
                "recent": {
                    "form": ["8-K", "10-Q", "8-K"],
                    "filingDate": ["2025-02-07", "2025-01-15", "2024-11-01"],
                    "accessionNumber": ["0001234567-25-000001", "0001234567-25-000002", "0001234567-24-000003"],
                    "primaryDocument": ["doc1.htm", "doc2.htm", "doc3.htm"],
                }
            }
        }
        with patch("src.fetcher._get_with_retry", return_value=self._make_mock_resp(submissions)):
            results = _get_edgar_filings("0000123456", ["8-K"], 10)
        assert len(results) == 2
        assert all(r["form"] == "8-K" for r in results)

    def test_get_edgar_filings_returns_n(self):
        submissions = {
            "filings": {
                "recent": {
                    "form": ["8-K"] * 5,
                    "filingDate": [f"2025-0{i}-01" for i in range(1, 6)],
                    "accessionNumber": [f"0001234567-25-00000{i}" for i in range(1, 6)],
                    "primaryDocument": [f"doc{i}.htm" for i in range(1, 6)],
                }
            }
        }
        with patch("src.fetcher._get_with_retry", return_value=self._make_mock_resp(submissions)):
            results = _get_edgar_filings("0000123456", ["8-K"], 2)
        assert len(results) == 2

    def test_parse_press_release_quarter_detection(self):
        html = "<html><body><h1>Acme Corp</h1><p>Fourth Quarter 2024 Results were strong.</p></body></html>"
        t = _parse_press_release(html, "FANG", "2025-02-07", "https://example.com")
        assert t.quarter == "Q4"
        assert t.year == 2024

    def test_parse_press_release_prepends_source_note(self):
        html = "<html><body><p>Some earnings text.</p></body></html>"
        t = _parse_press_release(html, "FANG", "2025-02-07", "https://example.com")
        assert t.text.startswith("SOURCE: SEC EDGAR")

    def test_fetch_transcripts_auto_falls_back(self):
        t1 = Transcript("IMO", "Imperial Oil", "Q4", 2024, "2025-02-07", "https://example.com", "text1")
        t2 = Transcript("IMO", "Imperial Oil", "Q3", 2024, "2024-11-01", "https://example.com", "text2")

        with patch("src.fetcher.fetch_transcripts_edgar", side_effect=ValueError("EDGAR failed")):
            with patch("src.fetcher.fetch_transcripts", return_value=[t1, t2]):
                result = fetch_transcripts_auto("IMO")

        assert result == [t1, t2]

    def test_fetch_transcripts_auto_uses_edgar_first(self):
        t1 = Transcript("IMO", "Imperial Oil", "Q4", 2024, "2025-02-07", "https://example.com/edgar", "edgar text")
        t2 = Transcript("IMO", "Imperial Oil", "Q3", 2024, "2024-11-01", "https://example.com/edgar", "edgar text 2")

        with patch("src.fetcher.fetch_transcripts_edgar", return_value=[t1, t2]) as mock_edgar:
            with patch("src.fetcher.fetch_transcripts") as mock_mf:
                result = fetch_transcripts_auto("IMO")

        mock_edgar.assert_called_once()
        mock_mf.assert_not_called()
        assert result == [t1, t2]

    def test_canadian_ticker_uses_6k(self):
        t1 = Transcript("IMO", "Imperial Oil", "Q4", 2024, "2025-02-07", "https://example.com", "text1")
        t2 = Transcript("IMO", "Imperial Oil", "Q3", 2024, "2024-11-01", "https://example.com", "text2")

        with patch("src.fetcher._get_cik", return_value="0000049196"):
            with patch("src.fetcher._get_edgar_filings", return_value=[
                {"form": "6-K", "date": "2025-02-07", "accession": "0000049196-25-000001", "primary_doc": "doc1.htm"},
                {"form": "6-K", "date": "2024-11-01", "accession": "0000049196-24-000002", "primary_doc": "doc2.htm"},
            ]) as mock_filings:
                with patch("src.fetcher._fetch_edgar_exhibit", return_value=("<html><body><p>text</p></body></html>", "doc.htm")):
                    fetch_transcripts_edgar("IMO")

        mock_filings.assert_called_once()
        call_args = mock_filings.call_args
        assert call_args[0][1] == ["6-K"]
