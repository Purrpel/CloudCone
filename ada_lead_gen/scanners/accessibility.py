"""Playwright + axe-core WCAG 2.1 AA accessibility scanner."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from loguru import logger
from playwright.async_api import async_playwright, TimeoutError as PWTimeout

from ada_lead_gen import config

AXE_PATH = Path(__file__).parent.parent.parent / "axe" / "axe.min.js"
SCREENSHOTS_DIR = Path("screenshots")

_AXE_RUNNER = """
async () => {
    return await new Promise((resolve) => {
        axe.run(
            document,
            { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa'] } },
            (err, results) => {
                if (err) resolve({ violations: [], error: String(err) });
                else resolve(results);
            }
        );
    });
}
"""


@dataclass
class Violation:
    id: str
    impact: str
    description: str
    help_url: str
    nodes: list[str] = field(default_factory=list)


@dataclass
class AccessibilityResult:
    url: str
    critical: int = 0
    serious: int = 0
    moderate: int = 0
    minor: int = 0
    total: int = 0
    violations: list[Violation] = field(default_factory=list)
    screenshot_path: str = ""
    error: str = ""


def _domain(url: str) -> str:
    return urlparse(url).netloc.replace("www.", "")


def _parse_violations(axe_violations: list[dict]) -> tuple[list[Violation], dict[str, int]]:
    violations: list[Violation] = []
    counts: dict[str, int] = {"critical": 0, "serious": 0, "moderate": 0, "minor": 0}
    for v in axe_violations:
        impact = v.get("impact", "minor")
        if impact in counts:
            counts[impact] += 1
        # Grab up to 3 affected HTML snippets
        nodes = [
            n.get("html", "")[:300]
            for n in v.get("nodes", [])[:3]
        ]
        violations.append(Violation(
            id=v.get("id", ""),
            impact=impact,
            description=v.get("description", ""),
            help_url=v.get("helpUrl", ""),
            nodes=nodes,
        ))
    return violations, counts


async def scan_accessibility(url: str) -> AccessibilityResult:
    """
    Launch a headless Chromium browser, inject local axe-core, run WCAG 2.1 AA,
    and take a screenshot.

    Handles nav errors, infinite redirects, bot walls, and cert errors gracefully.
    """
    if not AXE_PATH.exists():
        raise FileNotFoundError(
            f"axe.min.js not found at {AXE_PATH}. "
            "Download it and place it at axe/axe.min.js"
        )

    SCREENSHOTS_DIR.mkdir(exist_ok=True)
    domain = _domain(url)
    screenshot_path = str(SCREENSHOTS_DIR / f"{domain}.png")
    result = AccessibilityResult(url=url)

    axe_js = AXE_PATH.read_text()

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                ignore_https_errors=True,
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
            )
            page = await context.new_page()

            try:
                await page.goto(
                    url,
                    timeout=config.PLAYWRIGHT_TIMEOUT_S * 1000,
                    wait_until="domcontentloaded",
                )
            except PWTimeout:
                result.error = "navigation_timeout"
                await browser.close()
                return result
            except Exception as exc:
                result.error = str(exc)[:200]
                await browser.close()
                return result

            # Screenshot before running axe (page may change state)
            try:
                await page.screenshot(path=screenshot_path, full_page=False)
                result.screenshot_path = screenshot_path
            except Exception as exc:
                logger.warning("Screenshot failed for {}: {}", url, exc)

            # Inject and run axe-core
            try:
                await page.evaluate(axe_js)
                axe_result = await page.evaluate(_AXE_RUNNER)
            except Exception as exc:
                result.error = f"axe_error:{exc}"
                await browser.close()
                return result

            await browser.close()

    except Exception as exc:
        result.error = str(exc)[:200]
        return result

    raw_violations = axe_result.get("violations", [])
    violations, counts = _parse_violations(raw_violations)

    result.violations = violations
    result.critical = counts["critical"]
    result.serious = counts["serious"]
    result.moderate = counts["moderate"]
    result.minor = counts["minor"]
    result.total = len(violations)

    logger.info(
        "axe scan {} → critical={} serious={} moderate={} minor={} total={}",
        domain, result.critical, result.serious, result.moderate, result.minor, result.total,
    )
    return result


if __name__ == "__main__":
    import sys
    url = sys.argv[1] if len(sys.argv) > 1 else "https://example.com"
    r = asyncio.run(scan_accessibility(url))
    print(f"URL: {r.url}")
    print(f"Error: {r.error or 'none'}")
    print(f"Critical={r.critical} Serious={r.serious} Moderate={r.moderate} Minor={r.minor} Total={r.total}")
    for v in r.violations[:5]:
        print(f"  [{v.impact}] {v.id}: {v.description[:80]}")
