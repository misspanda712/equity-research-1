import os

import anthropic

from src.models import Transcript

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 1500

SYSTEM_PROMPT = (
    "You are a senior equity research analyst specializing in oil & gas, "
    "with deep expertise in Canadian oil sands and US E&P names. "
    "Your job is to identify material shifts in management language between two earnings calls "
    "and translate those shifts into investment-relevant signals for fundamental investors."
)

USER_PROMPT_TEMPLATE = """Compare the two earnings call transcripts below and produce a structured markdown diff note.

**Prior Quarter Transcript** ({prior_label}):
{prior_text}

---

**Current Quarter Transcript** ({current_label}):
{current_text}

---

For each of the following five topics, write a section with:
- A `## Topic` header
- **Prior Quarter:** one or two bullet points summarizing management's language/guidance on this topic
- **Current Quarter:** one or two bullet points summarizing the updated language/guidance
- **Signal:** one of `Bullish`, `Bearish`, `Neutral`, or `Watch`, followed by a single sentence explaining why the shift (or lack thereof) matters to a fundamental investor

Topics to cover:
1. Capex Guidance
2. Production / Volume Outlook
3. Buyback / Dividend Policy
4. Hedging Posture
5. New Risk Factors

Be precise and analytical. Quote specific figures where management cited them. If a topic was not addressed in one of the calls, note that explicitly rather than speculating."""


def compare_transcripts(current: Transcript, prior: Transcript) -> str:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise EnvironmentError(
            "ANTHROPIC_API_KEY is not set. Add it to your .env file or environment."
        )

    client = anthropic.Anthropic(api_key=api_key)

    prior_label = f"{prior.ticker} {prior.quarter} {prior.year}"
    current_label = f"{current.ticker} {current.quarter} {current.year}"

    user_content = [
        {
            "type": "text",
            "text": f"**Prior Quarter Transcript** ({prior_label}):\n{prior.text}\n\n---",
            "cache_control": {"type": "ephemeral"},
        },
        {
            "type": "text",
            "text": (
                f"\n\n**Current Quarter Transcript** ({current_label}):\n{current.text}\n\n---\n\n"
                f"For each of the following five topics, write a section with:\n"
                f"- A `## Topic` header\n"
                f"- **Prior Quarter:** one or two bullet points summarizing management's language/guidance on this topic\n"
                f"- **Current Quarter:** one or two bullet points summarizing the updated language/guidance\n"
                f"- **Signal:** one of `Bullish`, `Bearish`, `Neutral`, or `Watch`, followed by a single sentence "
                f"explaining why the shift (or lack thereof) matters to a fundamental investor\n\n"
                f"Topics to cover:\n"
                f"1. Capex Guidance\n"
                f"2. Production / Volume Outlook\n"
                f"3. Buyback / Dividend Policy\n"
                f"4. Hedging Posture\n"
                f"5. New Risk Factors\n\n"
                f"Be precise and analytical. Quote specific figures where management cited them. "
                f"If a topic was not addressed in one of the calls, note that explicitly rather than speculating."
            ),
        },
    ]

    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": user_content,
            }
        ],
    )

    return response.content[0].text
