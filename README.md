# Earnings Call Transcript Differ

A command-line tool for equity research analysts that fetches the two most recent earnings call transcripts for a given ticker from Motley Fool and uses the Claude API to produce a structured markdown diff note — highlighting material shifts in management language across five key investment topics.

Covered names: IMO, CVE, FANG, MUR, LNG, NEXT.

## Install

```
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and add your Anthropic API key:

```
cp .env.example .env
# edit .env and set ANTHROPIC_API_KEY=...
```

## Usage

Fetch live transcripts and run the analysis:

```
python differ.py --ticker IMO
```

Run against local fixture files (no network calls, useful for development and testing):

```
python differ.py --ticker IMO --use-fixtures
```

Write output to a custom directory:

```
python differ.py --ticker CVE --output-dir ./reports
```

Output is written to `{output_dir}/{TICKER}_{YYYY-MM-DD}.md`. The path is printed on completion.

Run tests:

```
pytest tests/
```

## Web Interface

```
streamlit run app.py
```

Opens a browser UI with the same Quarter Diff and Longitudinal Analysis modes as the CLI, plus an interactive signal dataframe and one-click report downloads.

## How It Works

The fetcher queries the Motley Fool SOLR search endpoint (`https://www.fool.com/search/solr.aspx`) with a `{ticker} earnings call transcript` query, filters results by headline, and retrieves the two most recent transcript pages. Each page is parsed with BeautifulSoup to extract the article body. All HTTP requests use a 30-second timeout with up to three retries on server errors (exponential backoff). If the SOLR endpoint fails or returns no results, the tool exits with a clear error message. The `--use-fixtures` flag bypasses all network calls and loads transcripts from `tests/fixtures/`, which is the recommended mode during development.

The analyzer sends both transcripts to Claude (`claude-sonnet-4-6`) in a single API call. The prior-quarter transcript block carries a `cache_control: ephemeral` annotation so repeated runs for the same ticker benefit from prompt caching and lower API cost. The system prompt positions Claude as a senior oil and gas equity research analyst. The user prompt requests a structured comparison across five topics — capex guidance, production outlook, buyback and dividend policy, hedging posture, and new risk factors — with a Prior Quarter / Current Quarter / Signal format per topic. Signal values are Bullish, Bearish, Neutral, or Watch, each followed by a one-sentence explanation of why the shift matters to a fundamental investor.

The CLI assembles a markdown report with a header block (ticker, company name, quarter links, generation date) prepended to the Claude output and writes it to the output directory. The file is named `{TICKER}_{YYYY-MM-DD}.md`. A sample output for IMO comparing Q3 2024 to Q4 2024 is included at `output/IMO_2025-05-11.md`.

## What's Next

- Add support for a `--tickers` flag to batch-run multiple names and produce a summary table.
- Integrate a second transcript source (e.g., Seeking Alpha) as a fallback when Motley Fool search returns no results.
- Add a `--since` flag to diff any two arbitrary quarters rather than always using the two most recent.
- Persist raw transcript text to a local cache directory to avoid redundant fetches across runs.
- Build a simple HTML output mode suitable for pasting into research distribution systems.
