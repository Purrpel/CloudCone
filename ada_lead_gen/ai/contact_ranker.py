"""Rank scraped contacts to surface likely decision-makers."""

from __future__ import annotations

import json
from dataclasses import dataclass

from loguru import logger

from ada_lead_gen.ai.client import LLMClient

_SYSTEM = (
    "You are a B2B sales data analyst. "
    "Respond only with valid JSON — no prose, no markdown."
)

_PROMPT = """
Rank these contacts to surface likely decision-makers (owner, founder, GM, manager).
Filter out role-based addresses (noreply, privacy, legal, hr, careers, abuse, postmaster).

Return JSON:
{{
  "ranked": [
    {{
      "contact": "<email or phone>",
      "type": "email|phone",
      "confidence": <0.0-1.0>,
      "reasoning": "<brief>"
    }}
  ]
}}

Emails and context:
{emails_block}

Phones:
{phones_block}
"""


@dataclass
class RankedContact:
    contact: str
    type: str
    confidence: float
    reasoning: str


def rank_contacts(
    emails: list[dict],
    phones: list[dict],
    llm: LLMClient,
) -> list[RankedContact]:
    """
    Rank contacts by decision-maker likelihood.

    emails: list of dicts with keys 'email', 'context'.
    phones: list of dicts with keys 'normalized', 'context'.
    Returns ranked list, decision-makers first.
    """
    emails_block = "\n".join(
        f"  {e.get('email', '')} | {e.get('context', '')[:120]}" for e in emails[:15]
    ) or "none"
    phones_block = "\n".join(
        f"  {p.get('normalized', '')} | {p.get('context', '')[:80]}" for p in phones[:10]
    ) or "none"

    prompt = _PROMPT.format(emails_block=emails_block, phones_block=phones_block)
    data = llm.call(prompt, purpose="contact_rank", system=_SYSTEM)

    ranked: list[RankedContact] = []
    for item in data.get("ranked", []):
        ranked.append(RankedContact(
            contact=item.get("contact", ""),
            type=item.get("type", "email"),
            confidence=float(item.get("confidence", 0.5)),
            reasoning=item.get("reasoning", ""),
        ))

    ranked.sort(key=lambda c: c.confidence, reverse=True)
    logger.debug("Ranked {} contacts, top: {}", len(ranked), ranked[0].contact if ranked else "none")
    return ranked
