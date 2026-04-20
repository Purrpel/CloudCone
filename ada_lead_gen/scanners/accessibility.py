"""Playwright + axe-core WCAG 2.1 AA accessibility scanner.

Also extracts basic page content (title, meta, H1, nav) for downstream insight
generation and detects pre-existing accessibility overlay widgets so we don't
pitch people who already bought a competing solution.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from loguru import logger
from playwright.async_api import async_playwright, TimeoutError as PWTimeout

from ada_lead_gen import config

AXE_PATH = Path(__file__).parent.parent.parent / "axe" / "axe.min.js"
SCREENSHOTS_DIR = Path("screenshots")

# Signatures for common accessibility overlay products. A match on ANY of these
# flags the site as already-overlaid, which is a bad_fit red flag.
_OVERLAY_SIGNATURES: dict[str, list[str]] = {
    "UserWay":   ["userway.org", "userway_buttons", "userway-widget"],
    "AccessiBe": ["accessibe.com", "acsb-trigger", "accessibeapp"],
    "EqualWeb":  ["equalweb.com", "nagich-widget"],
    "AudioEye":  ["audioeye.com", "ae-init", "aeInit"],
    "MaxAccess": ["maxaccess.io"],
    "Recite":    ["reciteme.com"],
    "Siteimprove": ["siteimprove.com/accessibility"],
    "accessiBe-free": ["acsb.accessibe.com"],
}

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

_EXTRACT_CONTENT = """
() => {
    const title = document.title || '';
    const metaDesc =
        document.querySelector('meta[name="description"]')?.content || '';
    const ogDesc =
        document.querySelector('meta[property="og:description"]')?.content || '';
    const h1s = Array.from(document.querySelectorAll('h1'))
        .map(h => (h.innerText || '').trim())
        .filter(Boolean)
        .slice(0, 3);
    const h2s = Array.from(document.querySelectorAll('h2'))
        .map(h => (h.innerText || '').trim())
        .filter(Boolean)
        .slice(0, 5);
    const navItems = Array.from(document.querySelectorAll('nav a, header a'))
        .map(a => (a.innerText || '').trim())
        .filter(Boolean)
        .slice(0, 15);
    return {
        title: title.slice(0, 200),
        meta_description: (metaDesc || ogDesc).slice(0, 300),
        h1: h1s,
        h2: h2s,
        nav: navItems,
    };
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
class PageContent:
    title: str = ""
    meta_description: str = ""
    h1: list[str] = field(default_factory=list)
    h2: list[str] = field(default_factory=list)
    nav: list[str] = field(default_factory=list)

    def as_prompt_block(self) -> str:
        """Render for LLM prompts — keeps token cost bounded."""
        lines = []
        if self.title:
            lines.append(f"Title: {self.title}")
        if self.meta_description:
            lines.append(f"Meta description: {self.meta_description}")
        if self.h1:
            lines.append(f"H1: {' | '.join(self.h1)}")
        if self.h2:
            lines.append(f"H2: {' | '.join(self.h2)}")
        if self.nav:
            lines.append(f"Nav: {', '.join(self.nav)}")
        return "\n".join(lines) or "(no extracted content)"


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
    overlay_detected: str = ""  # product name if detected, else ""
    content: PageContent = field(default_factory=PageContent)


def _domain(url: str) -> str:
    return urlparse(url).netloc.replace("www.", "")


def _parse_violations(axe_violations: list[dict]) -> tuple[list[Violation], dict[str, int]]:
    violations: list[Violation] = []
    counts: dict[str, int] = {"critical": 0, "serious": 0, "moderate": 0, "minor": 0}
    for v in axe_violations:
        impact = v.get("impact", "minor")
        if impact in counts:
            counts[impact] += 1
        nodes = [n.get("html", "")[:300] for n in v.get("nodes", [])[:3]]
        violations.append(Violation(
            id=v.get("id", ""),
            impact=impact,
            description=v.get("description", ""),
            help_url=v.get("helpUrl", ""),
            nodes=nodes,
        ))
    return violations, counts


def _detect_overlay(html: str) -> str:
    """Return overlay product name if any known signature is present in the page."""
    low = html.lower()
    for product, sigs in _OVERLAY_SIGNATURES.items():
        for sig in sigs:
            if sig.lower() in low:
                return product
    return ""


async def scan_accessibility(url: str) -> AccessibilityResult:
    """
    Launch a headless Chromium browser, inject local axe-core, run WCAG 2.1 AA,
    take a screenshot, extract page content, and detect overlay widgets.

    Handles nav errors, infinite redirects, bot walls, and cert errors gracefully.
    """
    if not AXE_PATH.exists():
        raise FileNotFoundError(
            f"axe.min.js not found at {AXE_PATH}. "
            "Download it and place it at axe/axe.min.js "
            "(or run: npm install axe-core && cp node_modules/axe-core/axe.min.js axe/)"
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
                # Let lazy content, deferred scripts, and overlay widgets settle
                try:
                    await page.wait_for_load_state("networkidle", timeout=5000)
                except PWTimeout:
                    pass  # networkidle not reached — scan what we have
            except PWTimeout:
                result.error = "navigation_timeout"
                await browser.close()
                return result
            except Exception as exc:
                result.error = str(exc)[:200]
                await browser.close()
                return result

            # Snapshot page HTML for overlay detection
            try:
                html_snapshot = await page.content()
                result.overlay_detected = _detect_overlay(html_snapshot)
                if result.overlay_detected:
                    logger.info(
                        "Overlay widget detected on {}: {}",
                        domain, result.overlay_detected,
                    )
            except Exception as exc:
                logger.debug("Overlay detection skipped: {}", exc)

            # Extract content for insights prompts
            try:
                content_dict = await page.evaluate(_EXTRACT_CONTENT)
                result.content = PageContent(
                    title=content_dict.get("title", ""),
                    meta_description=content_dict.get("meta_description", ""),
                    h1=content_dict.get("h1", []),
                    h2=content_dict.get("h2", []),
                    nav=content_dict.get("nav", []),
                )
            except Exception as exc:
                logger.debug("Content extraction failed: {}", exc)

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
                result.error = f"axe_error:{str(exc)[:150]}"
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
        "axe scan {} → critical={} serious={} moderate={} minor={} total={} overlay={}",
        domain, result.critical, result.serious, result.moderate, result.minor,
        result.total, result.overlay_detected or "none",
    )
    return result


if __name__ == "__main__":
    import sys
    url = sys.argv[1] if len(sys.argv) > 1 else "https://example.com"
    r = asyncio.run(scan_accessibility(url))
    print(f"URL: {r.url}")
    print(f"Error: {r.error or 'none'}")
    print(f"Overlay: {r.overlay_detected or 'none'}")
    print(f"Critical={r.critical} Serious={r.serious} Moderate={r.moderate} Minor={r.minor} Total={r.total}")
    print(f"Content:\n{r.content.as_prompt_block()}")
    for v in r.violations[:5]:
        print(f"  [{v.impact}] {v.id}: {v.description[:80]}")
