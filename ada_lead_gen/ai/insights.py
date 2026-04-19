"""Generate persistent AI insights per lead — the personalization backbone."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from loguru import logger

from ada_lead_gen.ai.client import LLMClient

_SYSTEM = (
    "You are a sharp B2B sales strategist researching prospects for an ADA web "
    "accessibility remediation service called BizStreamPro. "
    "Respond ONLY with a valid JSON object. No prose, no markdown fences."
)

_PROMPT = """
Analyse this business and produce rich sales intelligence JSON:

{{
  "business_snapshot": "<2 sentences: what they do, who they serve, vibe>",
  "pain_point_angle": "<most compelling reason THIS specific business should care — not generic ADA fear>",
  "personalization_hooks": [
    "<specific verifiable detail from their site — product, location, event, tagline>",
    "<hook 2>",
    "<hook 3>"
  ],
  "industry_lawsuit_context": "<a true, current stat or case for their industry — omit if uncertain>",
  "objection_preempt": "<most likely reason they'll ignore outreach + a one-line counter>",
  "recommended_tone": "formal"|"warm"|"direct"|"technical",
  "red_flags": ["<bad_fit reason if applicable>"] or []
}}

Red flag triggers (mark bad_fit if any apply):
- Non-profit, government, education, healthcare patient portal
- Already has an accessibility statement or overlay widget
- Massive enterprise (500+ reviews suggests large chain)
- Competitor to BizStreamPro

Business data:
- Name: {name}
- Website: {website}
- Industry: {category}
- Address: {address}
- Rating: {rating} ({review_count} reviews)
- ADA violations: critical={critical}, serious={serious}, total={total}
- Top violation rules: {top_rules}
- Risk score: {risk_score}/10
- Is lawsuit prone: {lawsuit_prone}
- Top contact: {best_contact}
"""


@dataclass
class Insights:
    business_snapshot: str = ""
    pain_point_angle: str = ""
    personalization_hooks: list[str] = field(default_factory=list)
    industry_lawsuit_context: str = ""
    objection_preempt: str = ""
    recommended_tone: str = "direct"
    red_flags: list[str] = field(default_factory=list)
    generated_at: str = ""


def generate_insights(lead: dict[str, Any], llm: LLMClient) -> Insights:
    """
    One cheap-model call producing persistent personalization fuel.

    The result is saved to the AI Insights sheet tab AND fed into outreach drafts.
    Never skip this step — it is the heart of the pipeline.
    """
    best_contact = lead.get("best_contact", {})
    top_rules = ", ".join(
        v.get("id", "") for v in lead.get("violations", [])[:5]
    )

    prompt = _PROMPT.format(
        name=lead.get("name", ""),
        website=lead.get("website", ""),
        category=lead.get("category", ""),
        address=lead.get("address", ""),
        rating=lead.get("rating", "N/A"),
        review_count=lead.get("review_count", "N/A"),
        critical=lead.get("critical", 0),
        serious=lead.get("serious", 0),
        total=lead.get("total_violations", 0),
        top_rules=top_rules or "none",
        risk_score=lead.get("risk_score", 5),
        lawsuit_prone=lead.get("is_lawsuit_prone", False),
        best_contact=best_contact.get("contact", "none") if best_contact else "none",
    )

    data = llm.call(prompt, purpose="insights", system=_SYSTEM)

    result = Insights(
        business_snapshot=data.get("business_snapshot", ""),
        pain_point_angle=data.get("pain_point_angle", ""),
        personalization_hooks=data.get("personalization_hooks", []),
        industry_lawsuit_context=data.get("industry_lawsuit_context", ""),
        objection_preempt=data.get("objection_preempt", ""),
        recommended_tone=data.get("recommended_tone", "direct"),
        red_flags=data.get("red_flags", []),
        generated_at=datetime.utcnow().isoformat(),
    )
    logger.info("Insights generated for {} (tone={}, red_flags={})", lead.get("name"), result.recommended_tone, result.red_flags)
    return result
