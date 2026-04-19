"""Translate raw axe violations into plain-English summaries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ada_lead_gen.ai.client import LLMClient

_SYSTEM = (
    "You are an accessibility consultant writing for non-technical business owners. "
    "Respond only with valid JSON — no prose, no markdown."
)

_PROMPT = """
Given these ADA/WCAG accessibility violations for "{name}", produce a JSON summary:

{{
  "headline": "<one-sentence email subject hook — mention something specific about the site>",
  "top_3_issues": ["<plain English, no jargon>", "<issue 2>", "<issue 3>"],
  "legal_exposure": "<lay-terms lawsuit risk, 1-2 sentences>",
  "user_impact": "<who is harmed and how, 1-2 sentences>",
  "tone_hook": "<a human angle, e.g. 'their site blocks screen readers on the entire menu'>"
}}

Violations (impact | rule | description):
{violations_text}

Counts: critical={critical}, serious={serious}, moderate={moderate}, minor={minor}, total={total}
"""


@dataclass
class ViolationsSummary:
    headline: str
    top_3_issues: list[str]
    legal_exposure: str
    user_impact: str
    tone_hook: str


def summarize(violations: list[dict[str, Any]], business_name: str, llm: LLMClient) -> ViolationsSummary:
    """
    Convert axe violation list into a plain-English summary dict.

    violations: list of Violation dataclass instances serialised as dicts.
    """
    counts = {"critical": 0, "serious": 0, "moderate": 0, "minor": 0}
    lines = []
    for v in violations[:20]:  # cap to keep prompt size sane
        impact = v.get("impact", "minor")
        if impact in counts:
            counts[impact] += 1
        lines.append(f"{impact} | {v.get('id', '')} | {v.get('description', '')[:100]}")

    violations_text = "\n".join(lines) if lines else "No violations detected."

    prompt = _PROMPT.format(
        name=business_name,
        violations_text=violations_text,
        **counts,
        total=len(violations),
    )

    data = llm.call(prompt, purpose="violations_summary", system=_SYSTEM)

    return ViolationsSummary(
        headline=data.get("headline", ""),
        top_3_issues=data.get("top_3_issues", []),
        legal_exposure=data.get("legal_exposure", ""),
        user_impact=data.get("user_impact", ""),
        tone_hook=data.get("tone_hook", ""),
    )
