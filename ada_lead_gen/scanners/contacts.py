"""Scrape emails and phone numbers from business websites."""

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
_PHONE_RE = re.compile(
    r"(\+1[-.\s]?)?(\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4})"
)

_JUNK_EMAIL_PATTERNS = re.compile(
    r"(wixpress|sentry|example\.com|test@|sample@|@2x|noreply|no-reply|"
    r"privacy@|legal@|hr@|careers@|abuse@|postmaster@|support@example|"
    r"\.png|\.jpg|\.gif|\.svg)",
    re.IGNORECASE,
)

_CONTACT_PATHS = ["/", "/contact", "/contact-us", "/about", "/about-us", "/team"]


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


def _robots_allows(base_url: str, path: str) -> bool:
    robots_url = urljoin(base_url, "/robots.txt")
    rp = RobotFileParser()
    rp.set_url(robots_url)
    try:
        rp.read()
    except Exception:
        return True  # If robots.txt is unreachable, assume allowed
    return rp.can_fetch(_USER_AGENT, urljoin(base_url, path))


def _extract_contacts(html: str, source_url: str) -> tuple[list[ContactInfo], list[PhoneInfo]]:
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(separator=" ")

    emails: dict[str, ContactInfo] = {}
    phones: dict[str, PhoneInfo] = {}

    # Emails
    for m in _EMAIL_RE.finditer(text):
        email = m.group(0).lower()
        if _is_junk_email(email):
            continue
        if email not in emails:
            start = max(0, m.start() - 80)
            end = min(len(text), m.end() + 80)
            emails[email] = ContactInfo(email=email, context=text[start:end].strip())

    # Phones
    for m in _PHONE_RE.finditer(text):
        raw = m.group(0)
        normalized = _normalize_phone(raw)
        if not normalized:
            continue
        if normalized not in phones:
            start = max(0, m.start() - 60)
            end = min(len(text), m.end() + 60)
            phones[normalized] = PhoneInfo(
                number=raw, normalized=normalized,
                context=text[start:end].strip()
            )

    return list(emails.values()), list(phones.values())


async def scrape_contacts(base_url: str) -> ContactsResult:
    """
    Fetch homepage + common contact pages, extract emails and phones.

    Respects robots.txt and enforces REQUEST_DELAY_SECONDS between requests.
    """
    parsed = urlparse(base_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"

    all_emails: dict[str, ContactInfo] = {}
    all_phones: dict[str, PhoneInfo] = {}
    headers = {"User-Agent": _USER_AGENT}
    timeout = httpx.Timeout(config.LIVENESS_TIMEOUT_S)

    async with httpx.AsyncClient(
        follow_redirects=True, timeout=timeout, headers=headers
    ) as client:
        for path in _CONTACT_PATHS:
            target = urljoin(origin, path)

            if not _robots_allows(origin, path):
                logger.debug("robots.txt disallows {}", target)
                continue

            try:
                resp = await client.get(target)
                if resp.status_code != 200:
                    continue
                emails, phones = _extract_contacts(resp.text, target)
                for ci in emails:
                    all_emails.setdefault(ci.email, ci)
                for pi in phones:
                    all_phones.setdefault(pi.normalized, pi)
            except Exception as exc:
                logger.debug("Contact fetch failed for {}: {}", target, exc)

            await asyncio.sleep(config.REQUEST_DELAY_SECONDS)

    result = ContactsResult(
        emails=list(all_emails.values()),
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
    print(f"Emails ({len(r.emails)}): {[e.email for e in r.emails]}")
    print(f"Phones ({len(r.phones)}): {[p.normalized for p in r.phones]}")
