# Earnings Call Transcript Differ

## Purpose

This tool fetches the two most recent earnings call transcripts for a given equity ticker from Motley Fool, then uses the Claude API (claude-sonnet-4-6) to produce a markdown diff note highlighting material shifts in management language across five key topics: capex guidance, production/volume outlook, buyback/dividend policy, hedging posture, and new risk factors.

Primary users are equity research analysts covering oil sands and energy names (IMO, CVE, FANG, MUR, LNG, NEXT).

## Stack

- Python 3.11+
- `anthropic` SDK (>=0.40.0) for Claude API calls
- `requests` + `beautifulsoup4` for Motley Fool scraping
- `python-dotenv` for environment variable loading
- `pytest` for tests

## Project Layout

```
differ.py           # CLI entry point
src/
  fetcher.py        # Motley Fool transcript scraper
  analyzer.py       # Claude API comparison logic
  models.py         # Transcript dataclass
tests/
  fixtures/         # Realistic fake transcripts for offline testing
  test_fetcher.py   # Unit tests for parser and loader
output/             # Generated markdown reports land here
```

## Configuration

- API keys go in `.env` (copy `.env.example` to `.env` and fill in)
- Never log or print `ANTHROPIC_API_KEY` or any credential
- `.env` is gitignored; `.env.example` is committed as a template

## Conventions

- HTTP requests: 30-second timeout, 3 retries on 5xx with exponential backoff
- `User-Agent` header on all requests to Motley Fool: `Mozilla/5.0 (compatible; equity-research-bot/1.0)`
- Outputs written to `./output/{TICKER}_{YYYY-MM-DD}.md`
- Prompt caching: set `cache_control: {"type": "ephemeral"}` on the prior-quarter transcript block (stable input) to reduce API cost on repeated runs for the same ticker
- Model: `claude-sonnet-4-6` — do not change without updating tests and docs
- Max tokens for analysis response: 1500

## Testing

- Every parser function in `fetcher.py` must have corresponding unit tests
- Tests use fixtures from `tests/fixtures/` — no live network calls in tests
- Run: `pytest tests/`

## Adding a New Ticker

No code changes needed. Tickers are passed at runtime: `python differ.py --ticker CVE`

## Scraping Notes

Motley Fool renders search results server-side but the SOLR search endpoint returns JSON. The fetcher tries `https://www.fool.com/search/solr.aspx` first. If that fails (JS-rendered or rate-limited), it falls back gracefully with a clear error message. The `--use-fixtures` flag bypasses all network calls and is the recommended mode for development and testing.
