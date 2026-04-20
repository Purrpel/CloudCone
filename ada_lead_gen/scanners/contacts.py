"""Scrape emails and phone numbers from business websites.

Strategy:
1. Prefer explicit `<a href="mailto:">` and `<a href="tel:">` links — these are
   authoritative and avoid regex false positives (ZIP codes, SKUs, timestamps).
2. Fall back to regex over visible text for sites that don't use proper links.
3. Visit homepage + canonical contact paths, deduplicate, respect robots.txt.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup
from loguru import logger

from ada_lead_gen import config

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

_EMAIL_RE = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"
)

# Phone regex requires an explicit US-style separator or (area) wrap so we
# don't match arbitrary 10-digit runs (ZIP+4, SKUs, order numbers).
_PHONE_STRICT_RE = re.compile(
    r"(?:\+?1[-.\s]?)?"
    r"(?:\(\d{3}\)\s*|\d{3}[-.\s])"
    r"\d{3}[-.\s]\d{4}"
)

_JUNK_EMAIL_PATTERNS = re.compile(
    r"(wixpress|sentry|example\.com|test@|sample@|@2x|noreply|no-reply|"
    r"privacy@|legal@|hr@|careers@|abuse@|postmaster@|support@example|"
    r"\.png|\.jpg|\.gif|\.svg|\.webp|u003e|u003c)",
    re.IGNORECASE,
)

_CONTACT_PATHS = ["/", "/contact", "/contact-us", "/about", "/about-us", "/team"]

# Per-origin robots.txt cache — avoids re-fetching on every path
_robots_cache: dict[str, RobotFileParser | None] = {}


@dataclass
class ContactInfo:
    email: str
    context: str = ""
    confidence: float = 0.5


@dataclass
class PhoneInfo:
    number: str
    normalized: str
    context: str = ""


@dataclass
class ContactsResult:
    emails: list[ContactInfo] = field(default_factory=list)
    phones: list[PhoneInfo] = field(default_factory=list)
    error: str = ""


def _normalize_phone(raw: str) -> str:
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 10:
        return "+1" + digits
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    return ""


def _is_junk_email(email: str) -> bool:
    return bool(_JUNK_EMAIL_PATTERNS.search(email))


async def _load_robots(origin: str) -> RobotFileParser | None:
    """Fetch + parse robots.txt once per origin, cached. Non-blocking."""
    if origin in _robots_cache:
        return _robots_cache[origin]

    def _fetch() -> RobotFileParser | None:
        rp = RobotFileParser()
        rp.set_url(urljoin(origin, "/robots.txt"))
        try:
            rp.read()
            return rp
        except Exception:
            return None  # treat unreachable robots as "allowed"

    rp = await asyncio.get_event_loop().run_in_executor(None, _fetch)
    _robots_cache[origin] = rp
    return rp


async def _robots_allows(origin: str, path: str) -> bool:
    rp = await _load_robots(origin)
    if rp is None:
        return True
    return rp.can_fetch(_USER_AGENT, urljoin(origin, path))


def _extract_contacts(
    html: str, source_url: str
) -> tuple[list[ContactInfo], list[PhoneInfo]]:
    """
    Pull emails and phones from a page, preferring anchor tags over regex.
    """
    soup = BeautifulSoup(html, "html.parser")

    emails: dict[str, ContactInfo] = {}
    phones: dict[str, PhoneInfo] = {}

    # 1. Preferred: <a href="mailto:..."> and <a href="tel:...">
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        anchor_text = a.get_text(" ", strip=True)[:120]
        if href.lower().startswith("mailto:"):
            addr = href[7:].split("?", 1)[0].strip().lower()
            if addr and not _is_junk_email(addr) and addr not in emails:
                emails[addr] = ContactInfo(
                    email=addr, context=anchor_text or "mailto link",
                    confidence=0.9,  # anchor-sourced = high confidence
                )
        elif href.lower().startswith("tel:"):
            normalized = _normalize_phone(href[4:])
            if normalized and normalized not in phones:
                phones[normalized] = PhoneInfo(
                    number=href[4:], normalized=normalized,
                    context=anchor_text or "tel link",
                )

    # 2. Fallback: regex over visible text (lower confidence)
    text = soup.get_text(separator=" ")
    for m in _EMAIL_RE.finditer(text):
        email = m.group(0).lower()
        if _is_junk_email(email) or email in emails:
            continue
        start = max(0, m.start() - 80)
        end = min(len(text), m.end() + 80)
        emails[email] = ContactInfo(
            email=email, context=text[start:end].strip(), confidence=0.6,
        )

    for m in _PHONE_STRICT_RE.finditer(text):
        raw = m.group(0)
        normalized = _normalize_phone(raw)
        if not normalized or normalized in phones:
            continue
        start = max(0, m.start() - 60)
        end = min(len(text), m.end() + 60)
        phones[normalized] = PhoneInfo(
            number=raw, normalized=normalized,
            context=text[start:end].strip(),
        )

    return list(emails.values()), list(phones.values())


async def scrape_contacts(base_url: str) -> ContactsResult:
    """
    Fetch homepage + common contact pages, extract emails and phones.

    Respects robots.txt (cached per origin) and enforces REQUEST_DELAY_SECONDS
    between sequential requests to the same domain.
    """
    parsed = urlparse(base_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    base_path = parsed.path.rstrip("/") or "/"

    # Deduplicate: the homepage may already be the starting URL
    paths_to_try: list[str] = []
    for p in _CONTACT_PATHS:
        full = urljoin(origin, p)
        if full in paths_to_try:
            continue
        paths_to_try.append(full)
    # Ensure base_url is scanned too (liveness may have redirected to a subpath)
    if base_url not in paths_to_try:
        paths_to_try.insert(0, base_url)

    all_emails: dict[str, ContactInfo] = {}
    all_phones: dict[str, PhoneInfo] = {}
    headers = {"User-Agent": _USER_AGENT}
    timeout = httpx.Timeout(config.LIVENESS_TIMEOUT_S)

    async with httpx.AsyncClient(
        follow_redirects=True, timeout=timeout, headers=headers
    ) as client:
        for target in paths_to_try:
            path = urlparse(target).path or "/"

            if not await _robots_allows(origin, path):
                logger.debug("robots.txt disallows {}", target)
                continue

            try:
                resp = await client.get(target)
                if resp.status_code != 200:
                    continue
                emails, phones = _extract_contacts(resp.text, target)
                for ci in emails:
                    existing = all_emails.get(ci.email)
                    if not existing or ci.confidence > existing.confidence:
                        all_emails[ci.email] = ci
                for pi in phones:
                    all_phones.setdefault(pi.normalized, pi)
            except Exception as exc:
                logger.debug("Contact fetch failed for {}: {}", target, exc)

            await asyncio.sleep(config.REQUEST_DELAY_SECONDS)

    result = ContactsResult(
        emails=sorted(all_emails.values(), key=lambda c: c.confidence, reverse=True),
        phones=list(all_phones.values()),
    )
    logger.info(
        "Contacts for {}: {} emails, {} phones",
        parsed.netloc, len(result.emails), len(result.phones),
    )
    return result


if __name__ == "__main__":
    import sys
    url = sys.argv[1] if len(sys.argv) > 1 else "https://httpbin.org"
    r = asyncio.run(scrape_contacts(url))
    print(f"Emails ({len(r.emails)}): {[(e.email, e.confidence) for e in r.emails]}")
    print(f"Phones ({len(r.phones)}): {[p.normalized for p in r.phones]}")
