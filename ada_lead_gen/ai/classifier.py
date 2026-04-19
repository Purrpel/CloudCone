"""Industry ADA-risk classification using the cheap LLM."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from loguru import logger

from ada_lead_gen.ai.client import LLMClient
from ada_lead_gen import config

_HIGH_RISK = ", ".join(config.HIGH_RISK_INDUSTRIES)

_SYSTEM = (
    "You are an ADA website compliance risk analyst. "
    "Respond only with a valid JSON object — no prose."
)

_PROMPT_TEMPLATE = """
Assess the ADA lawsuit risk for this business and return JSON with these exact keys:

{{
  "risk_score": <integer 1-10, 10 = highest risk>,
  "reasoning": "<one sentence>",
  "is_lawsuit_prone": <true|false>,
  "category": "<single best industry label>"
}}

High-risk industries: {high_risk}

Business data:
- Name: {name}
- Website: {website}
- Categories: {categories}
- Industry query: {industry}
- Rating: {rating}
- Review count: {review_count}
- Address: {address}
"""


@dataclass
class ClassificationResult:
    risk_score: int
    reasoning: str
    is_lawsuit_prone: bool
    category: str


def classify_business(business_data: dict[str, Any], llm: LLMClient) -> ClassificationResult:
    """
    Score a business's ADA lawsuit risk (1-10).

    Uses cheap_model. Returns a ClassificationResult.
    """
    prompt = _PROMPT_TEMPLATE.format(
        high_risk=_HIGH_RISK,
        name=business_data.get("name", ""),
        website=business_data.get("website", ""),
        categories=", ".join(business_data.get("categories", [])),
        industry=business_data.get("industry", ""),
        rating=business_data.get("rating", "N/A"),
        review_count=business_data.get("review_count", "N/A"),
        address=business_data.get("address", ""),
    )

    data = llm.call(prompt, purpose="classify", system=_SYSTEM)

    result = ClassificationResult(
        risk_score=int(data.get("risk_score", 5)),
        reasoning=data.get("reasoning", ""),
        is_lawsuit_prone=bool(data.get("is_lawsuit_prone", False)),
        category=data.get("category", business_data.get("industry", "")),
    )
    logger.debug("Classified {} → risk={} lawsuit_prone={}", business_data.get("name"), result.risk_score, result.is_lawsuit_prone)
    return result
