#!/usr/bin/env python3
import argparse
import os
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare two earnings call transcripts and produce a markdown diff note.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python differ.py --ticker IMO\n"
            "  python differ.py --ticker IMO --use-fixtures\n"
            "  python differ.py --ticker CVE --output-dir ./reports\n"
        ),
    )
    parser.add_argument(
        "--ticker",
        required=True,
        type=str,
        help="Stock ticker symbol (e.g. IMO, CVE, FANG)",
    )
    parser.add_argument(
        "--output-dir",
        default="./output",
        help="Directory to write the output markdown file (default: ./output)",
    )
    parser.add_argument(
        "--use-fixtures",
        action="store_true",
        help="Load transcripts from tests/fixtures/ instead of fetching from Motley Fool",
    )
    return parser


def load_fixture_transcripts(ticker: str):
    from src.fetcher import load_from_file

    fixtures_dir = Path(__file__).parent / "tests" / "fixtures"
    pattern = f"{ticker.lower()}_*.txt"
    files = sorted(fixtures_dir.glob(pattern), reverse=True)

    if len(files) < 2:
        print(
            f"Error: need at least 2 fixture files matching '{pattern}' in {fixtures_dir}",
            file=sys.stderr,
        )
        sys.exit(1)

    current = load_from_file(str(files[0]))
    prior = load_from_file(str(files[1]))
    return current, prior


def fetch_live_transcripts(ticker: str):
    from src.fetcher import fetch_transcripts

    print(f"Fetching transcripts for {ticker.upper()} from Motley Fool...")
    transcripts = fetch_transcripts(ticker)
    current = transcripts[0]
    prior = transcripts[1]
    return current, prior


def main():
    parser = build_parser()
    args = parser.parse_args()

    ticker = args.ticker.upper()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.use_fixtures:
        print(f"Loading fixture transcripts for {ticker}...")
        current, prior = load_fixture_transcripts(ticker)
    else:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            print(
                "Error: ANTHROPIC_API_KEY is not set. "
                "Copy .env.example to .env and add your key, or set the environment variable.",
                file=sys.stderr,
            )
            sys.exit(1)
        try:
            current, prior = fetch_live_transcripts(ticker)
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            print(f"Error fetching transcripts: {e}", file=sys.stderr)
            sys.exit(1)

    print(f"Comparing {prior.quarter} {prior.year} vs {current.quarter} {current.year}...")

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "Error: ANTHROPIC_API_KEY is not set. "
            "Copy .env.example to .env and add your key.",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        from src.analyzer import compare_transcripts
        markdown = compare_transcripts(current, prior)
    except EnvironmentError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error running analysis: {e}", file=sys.stderr)
        sys.exit(1)

    today = date.today().isoformat()
    output_filename = f"{ticker}_{today}.md"
    output_path = output_dir / output_filename

    header = (
        f"# {ticker} Earnings Call Diff: {prior.quarter} {prior.year} vs {current.quarter} {current.year}\n\n"
        f"**Company:** {current.company_name}  \n"
        f"**Prior:** [{prior.quarter} {prior.year}]({prior.url})  \n"
        f"**Current:** [{current.quarter} {current.year}]({current.url})  \n"
        f"**Generated:** {today}\n\n"
        "---\n\n"
    )

    output_path.write_text(header + markdown, encoding="utf-8")
    print(f"Report written to: {output_path}")


if __name__ == "__main__":
    main()
