"""Score a lead 0-100 and assign tier A/B/C."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from loguru import logger

from ada_lead_gen.ai.client import LLMClient

_SYSTEM = (
    "You are a sales qualification analyst for an ADA website remediation service. "
    "Respond only with valid JSON — no prose, no markdown."
)

_PROMPT = """
Score this lead's buy-likelihood for an ADA website remediation service (0-100).
Assign tier: A (80-100, hot), B (60-79, warm), C (40-59, cool).

Factors to weigh:
- Accessibility severity (critical/serious violations carry most weight)
- Industry lawsuit risk (retail, restaurants, dental, medical = very high)
- Business size signals (review count, site complexity)
- Contact quality (decision-maker email > generic > phone only)

Return JSON:
{{
  "score": <integer 0-100>,
  "tier": "A"|"B"|"C",
  "reasoning": "<2 sentences>"
}}

Lead data:
- Business: {name}
- Industry category: {category}
- Risk score: {risk_score}/10
- Critical violations: {critical}
- Serious violations: {serious}
- Total violations: {total}
- Review count: {review_count}
- Best contact confidence: {contact_confidence}
- Contact type: {contact_type}
- Red flags: {red_flags}
"""


@dataclass
class LeadScore:
    score: int
    tier: str
    reasoning: str


def score_lead(lead: dict[str, Any], llm: LLMClient) -> LeadScore:
    """
    Score a fully enriched lead record.

    lead should contain keys from the pipeline's assembled lead dict.
    """
    best_contact = lead.get("best_contact", {})
    prompt = _PROMPT.format(
        name=lead.get("name", ""),
        category=lead.get("category", ""),
        risk_score=lead.get("risk_score", 5),
        critical=lead.get("critical", 0),
        serious=lead.get("serious", 0),
        total=lead.get("total_violations", 0),
        review_count=lead.get("review_count", "unknown"),
        contact_confidence=best_contact.get("confidence", 0) if best_contact else 0,
        contact_type=best_contact.get("type", "none") if best_contact else "none",
        red_flags=", ".join(lead.get("red_flags", [])) or "none",
    )

    data = llm.call(prompt, purpose="lead_score", system=_SYSTEM)

    result = LeadScore(
        score=int(data.get("score", 50)),
        tier=data.get("tier", "C"),
        reasoning=data.get("reasoning", ""),
    )
    logger.debug("Scored {} → {}/100 tier={}", lead.get("name"), result.score, result.tier)
    return result
