"""Single-file entry point for the ADA Lead Gen pipeline.

Usage:
    1. Copy .env.example → .env and fill it in.
    2. python run.py

Reads cities.txt and industries.txt from the project root and runs the full
discover → scan → qualify → enrich → write pipeline for every
(city × industry) pair.

Dedup (re-running this file is safe):
    * `scanned_domains` table skips any website scanned in the last
      RESCAN_COOLDOWN_DAYS (default 30).
    * `places_cache` table reuses Google Places results for PLACES_CACHE_DAYS.
    * Opt-outs in `opt_outs` are filtered before any email is written.
    * Sheets writer keys on the domain, so re-writing the same lead updates
      its existing row instead of duplicating.

Env knobs:
    LIMIT_PER_PAIR      Max businesses per (city, industry). Default 25.
    CITIES_FILE         Path to cities list. Default ./cities.txt.
    INDUSTRIES_FILE     Path to industries list. Default ./industries.txt.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from loguru import logger

from ada_lead_gen import config
from ada_lead_gen.db import init_db
from ada_lead_gen.pipeline import run_pipeline


def _read_list(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [line.strip() for line in path.read_text().splitlines() if line.strip()]


def _preflight() -> None:
    """Fail loudly before any network I/O if setup is incomplete."""
    errors: list[str] = []

    if not config.GOOGLE_MAPS_API_KEY:
        errors.append("GOOGLE_MAPS_API_KEY is empty in .env")
    if not config.GOOGLE_SHEETS_ID:
        errors.append("GOOGLE_SHEETS_ID is empty in .env")
    if not Path(config.GOOGLE_SERVICE_ACCOUNT_JSON).exists():
        errors.append(
            f"Google service account file not found: {config.GOOGLE_SERVICE_ACCOUNT_JSON}"
        )

    key_env = "ANTHROPIC_API_KEY" if config.LLM_PROVIDER == "anthropic" else "OPENAI_API_KEY"
    if not os.getenv(key_env):
        errors.append(f"{key_env} is empty (detected provider: {config.LLM_PROVIDER})")

    axe_path = Path(__file__).parent / "axe" / "axe.min.js"
    if not axe_path.exists():
        errors.append(
            f"axe-core not found at {axe_path}. "
            "Run: npm install axe-core && cp node_modules/axe-core/axe.min.js axe/"
        )

    if errors:
        logger.error("Pre-flight failed:")
        for e in errors:
            logger.error("  - {}", e)
        sys.exit(1)


async def _run_all(limit_per_pair: int) -> None:
    cities_path = Path(os.getenv("CITIES_FILE", "cities.txt"))
    industries_path = Path(os.getenv("INDUSTRIES_FILE", "industries.txt"))
    cities = _read_list(cities_path)
    industries = _read_list(industries_path)

    if not cities or not industries:
        logger.error(
            "Empty lists: {} cities, {} industries — fill {} and {}",
            len(cities), len(industries), cities_path, industries_path,
        )
        sys.exit(1)

    init_db()

    total_pairs = len(cities) * len(industries)
    logger.info(
        "Launching {} cities × {} industries = {} runs | limit={}/run | "
        "provider={} cheap={} premium={}",
        len(cities), len(industries), total_pairs, limit_per_pair,
        config.LLM_PROVIDER, config.CHEAP_MODEL, config.PREMIUM_MODEL,
    )

    grand = {"found": 0, "alive": 0, "qualified": 0, "written": 0, "cost": 0.0}
    for idx, city in enumerate(cities, start=1):
        for jdx, industry in enumerate(industries, start=1):
            pair_num = (idx - 1) * len(industries) + jdx
            logger.info(
                ">>> Pair {}/{}: {} | {}", pair_num, total_pairs, city, industry,
            )
            try:
                summary = await run_pipeline(city, industry, limit_per_pair)
            except Exception as exc:
                logger.error("Run failed ({}, {}): {}", city, industry, exc)
                continue
            grand["found"]     += summary.get("found", 0)
            grand["alive"]     += summary.get("alive", 0)
            grand["qualified"] += summary.get("qualified", 0)
            grand["written"]   += summary.get("written", 0)
            grand["cost"]      += summary.get("total_cost_usd", 0.0)

    logger.info(
        "=== ALL RUNS COMPLETE | found={} alive={} qualified={} written={} "
        "total_cost=${:.4f} ===",
        grand["found"], grand["alive"], grand["qualified"],
        grand["written"], grand["cost"],
    )


def main() -> None:
    limit = int(os.getenv("LIMIT_PER_PAIR", "25"))
    _preflight()
    asyncio.run(_run_all(limit))


if __name__ == "__main__":
    main()
