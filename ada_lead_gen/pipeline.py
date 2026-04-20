"""Main pipeline orchestrator."""

from __future__ import annotations

import asyncio
import dataclasses
import uuid
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from loguru import logger

from ada_lead_gen import config
from ada_lead_gen.ai.classifier import classify_business
from ada_lead_gen.ai.client import LLMClient
from ada_lead_gen.ai.contact_ranker import rank_contacts
from ada_lead_gen.ai.insights import generate_insights
from ada_lead_gen.ai.lead_scorer import score_lead
from ada_lead_gen.ai.violations_summary import summarize
from ada_lead_gen.db import (
    finish_run,
    init_db,
    is_opted_out,
    mark_scanned,
    start_run,
    was_recently_scanned,
)
from ada_lead_gen.scanners.accessibility import scan_accessibility
from ada_lead_gen.scanners.contacts import scrape_contacts
from ada_lead_gen.scanners.liveness import check_liveness
from ada_lead_gen.sinks.sheets import SheetsWriter, _extract_domain
from ada_lead_gen.sources.google_places import find_businesses


# Red flags that immediately disqualify a lead (from insights.py taxonomy).
_DISQUALIFYING_FLAGS: frozenset[str] = frozenset({
    "non_profit", "government", "education", "healthcare_portal",
    "enterprise", "has_overlay", "competitor", "too_small",
    # Legacy / generic fallback
    "bad_fit",
})


def _is_disqualifying_flag(flag: str) -> bool:
    """Match any known bad-fit reason, being lenient about LLM phrasing."""
    low = flag.lower().strip().replace(" ", "_").replace("-", "_")
    if low in _DISQUALIFYING_FLAGS:
        return True
    # Substring match for variations ("non_profit_organization", etc.)
    return any(f in low for f in _DISQUALIFYING_FLAGS)


def _qualifies(lead: dict[str, Any]) -> tuple[bool, str]:
    """
    Apply hard qualification rules BEFORE running insights.
    Red-flag checks happen separately after insights run.
    """
    if not lead.get("alive"):
        return False, "not_alive"

    # Hard overlay disqualification — if the scanner already detected one,
    # skip everything. These prospects have already bought a competing tool.
    if lead.get("overlay_detected"):
        return False, f"overlay_detected:{lead['overlay_detected']}"

    critical = lead.get("critical", 0)
    serious = lead.get("serious", 0)
    total = lead.get("total_violations", 0)

    a11y_ok = (
        critical >= config.MIN_CRITICAL
        or serious >= config.MIN_SERIOUS
        or total >= config.MIN_TOTAL_VIOLATIONS
    )
    if not a11y_ok:
        return False, f"a11y_below_threshold(c={critical},s={serious},t={total})"

    has_contact = bool(lead.get("emails")) or bool(lead.get("phones"))
    if not has_contact:
        return False, "no_contact"

    if lead.get("lead_score", 0) < config.MIN_LEAD_SCORE:
        return False, f"score_too_low({lead.get('lead_score')})"

    return True, "ok"


async def _process_one(
    business: Any,
    llm: LLMClient,
    semaphore: asyncio.Semaphore,
    stats: dict[str, int],
) -> dict[str, Any] | None:
    """Process a single business through the full pipeline."""
    domain = _extract_domain(business.website)

    if was_recently_scanned(domain, config.RESCAN_COOLDOWN_DAYS):
        logger.debug("Skipping recently scanned: {}", domain)
        return None

    async with semaphore:
        # 1. Liveness
        liveness = await check_liveness(business.website)
        if not liveness.alive:
            logger.info("Dead site: {} ({})", domain, liveness.reason)
            mark_scanned(domain, qualified=False)
            return None
        stats["alive"] += 1

        # 2. Accessibility
        a11y = await scan_accessibility(liveness.final_url)
        if a11y.error:
            logger.warning("Accessibility scan error for {}: {}", domain, a11y.error)

        # 3. Contacts
        contacts_result = await scrape_contacts(liveness.final_url)

    # Filter opted-out emails
    clean_emails = [
        e for e in contacts_result.emails
        if not is_opted_out(e.email)
    ]

    # Assemble partial lead for AI calls
    lead: dict[str, Any] = {
        "name": business.name,
        "website": business.website,
        "final_url": liveness.final_url,
        "city": business.city,
        "industry": business.industry,
        "phone": business.phone,
        "address": business.address,
        "rating": business.rating,
        "review_count": business.review_count,
        "alive": liveness.alive,
        "critical": a11y.critical,
        "serious": a11y.serious,
        "moderate": a11y.moderate,
        "minor": a11y.minor,
        "total_violations": a11y.total,
        "violations": [dataclasses.asdict(v) for v in a11y.violations],
        "screenshot_path": a11y.screenshot_path,
        "overlay_detected": a11y.overlay_detected,
        "site_content": a11y.content.as_prompt_block(),
        "emails": [dataclasses.asdict(e) for e in clean_emails],
        "phones": [dataclasses.asdict(p) for p in contacts_result.phones],
        "scanned_at": datetime.utcnow().isoformat(),
        "red_flags": [],
    }

    # Early exit: overlay-widget disqualification happens BEFORE any LLM spend.
    if a11y.overlay_detected:
        logger.info(
            "Skipping {} — already has {} overlay widget",
            domain, a11y.overlay_detected,
        )
        mark_scanned(domain, qualified=False)
        return None

    # 4. Classify
    try:
        classification = classify_business(lead, llm)
        lead["risk_score"] = classification.risk_score
        lead["is_lawsuit_prone"] = classification.is_lawsuit_prone
        lead["category"] = classification.category
    except Exception as exc:
        logger.warning("Classify failed for {}: {}", domain, exc)
        lead["risk_score"] = 5
        lead["is_lawsuit_prone"] = False
        lead["category"] = business.industry

    # 5. Violations summary
    try:
        vsummary = summarize(lead["violations"], business.name, llm)
        lead["violations_summary"] = dataclasses.asdict(vsummary)
    except Exception as exc:
        logger.warning("Violations summary failed for {}: {}", domain, exc)
        lead["violations_summary"] = {}

    # 6. Contact ranking
    try:
        ranked = rank_contacts(lead["emails"], lead["phones"], llm)
        lead["ranked_contacts"] = [dataclasses.asdict(r) for r in ranked]
        lead["best_contact"] = dataclasses.asdict(ranked[0]) if ranked else {}
    except Exception as exc:
        logger.warning("Contact rank failed for {}: {}", domain, exc)
        lead["ranked_contacts"] = []
        lead["best_contact"] = {}

    # 7. Lead score
    try:
        ls = score_lead(lead, llm)
        lead["lead_score"] = ls.score
        lead["tier"] = ls.tier
    except Exception as exc:
        logger.warning("Lead score failed for {}: {}", domain, exc)
        lead["lead_score"] = 0
        lead["tier"] = "C"

    # 8. Qualify
    qualifies, reason = _qualifies(lead)
    if not qualifies:
        logger.info("Disqualified {}: {}", domain, reason)
        mark_scanned(domain, qualified=False, lead_score=lead.get("lead_score"))
        return None

    # 9. Insights
    try:
        insights = generate_insights(lead, llm)
        lead["insights"] = dataclasses.asdict(insights)
        lead["red_flags"] = insights.red_flags
        # Re-check bad-fit after insights — any disqualifying flag bumps it
        bad_flags = [f for f in insights.red_flags if _is_disqualifying_flag(f)]
        if bad_flags:
            logger.info("Bad fit after insights: {} | flags={}", domain, bad_flags)
            mark_scanned(domain, qualified=False)
            return None
    except Exception as exc:
        logger.warning("Insights failed for {}: {}", domain, exc)
        lead["insights"] = {}

    mark_scanned(domain, qualified=True, lead_score=lead.get("lead_score"), tier=lead.get("tier"))
    stats["qualified"] += 1
    return lead


async def run_pipeline(
    city: str,
    industry: str,
    limit: int = 25,
    run_id: str | None = None,
) -> dict[str, Any]:
    """
    Full pipeline: discover → scan → qualify → enrich → write.

    Returns end-of-run stats dict.
    """
    init_db()
    run_id = run_id or str(uuid.uuid4())[:8]
    start_run(run_id, city, industry)
    llm = LLMClient(run_id=run_id)

    logger.info("=== Run {} | {} | {} | limit={} ===", run_id, city, industry, limit)

    stats: dict[str, int] = {
        "found": 0, "alive": 0, "qualified": 0, "written": 0,
    }

    # Discover
    try:
        businesses = find_businesses(city, industry, limit)
    except Exception as exc:
        logger.error("Discovery failed: {}", exc)
        finish_run(run_id, {**stats, "total_cost_usd": llm.get_run_spend()})
        return stats

    stats["found"] = len(businesses)
    logger.info("Discovered {} businesses", stats["found"])

    # Process concurrently (max MAX_CONCURRENT_SITES Playwright instances)
    semaphore = asyncio.Semaphore(config.MAX_CONCURRENT_SITES)
    tasks = [_process_one(biz, llm, semaphore, stats) for biz in businesses]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Surface any silently-swallowed task exceptions so we can see real bugs
    for biz, r in zip(businesses, results):
        if isinstance(r, Exception):
            logger.error(
                "Task failed for {}: {}: {}",
                biz.website, type(r).__name__, r,
            )

    qualified_leads = [r for r in results if isinstance(r, dict)]

    # Write to Sheets — isolate failures per lead so one bad row doesn't
    # stop the rest
    if qualified_leads:
        try:
            writer = SheetsWriter()
        except Exception as exc:
            logger.error("Sheets connection failed: {}", exc)
        else:
            for lead in qualified_leads:
                try:
                    domain = _extract_domain(
                        lead.get("final_url") or lead.get("website", "")
                    )
                    writer.write_lead(lead)
                    writer.write_insights(
                        domain,
                        lead.get("insights", {}),
                        lead.get("violations_summary", {}),
                    )
                    best = lead.get("best_contact", {})
                    best_email = (
                        best.get("contact", "")
                        if best and best.get("type") == "email"
                        else ""
                    )
                    writer.write_draft_placeholder(domain, best_email)
                    stats["written"] += 1
                except Exception as exc:
                    logger.error(
                        "Sheets write failed for {}: {}",
                        lead.get("name"), exc,
                    )

    total_cost = llm.get_run_spend()
    avg_cost = total_cost / max(stats["qualified"], 1)

    summary = {
        **stats,
        "total_cost_usd": total_cost,
        "avg_cost_per_lead": avg_cost,
    }
    finish_run(run_id, summary)

    logger.info(
        "=== Run complete | found={} alive={} qualified={} written={} "
        "cost=${:.4f} avg_per_lead=${:.4f} ===",
        stats["found"], stats["alive"], stats["qualified"], stats["written"],
        total_cost, avg_cost,
    )
    return summary
