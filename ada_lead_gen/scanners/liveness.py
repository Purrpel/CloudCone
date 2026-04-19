"""Check whether a business website is live and worth scanning."""

from __future__ import annotations

from dataclasses import dataclass

import httpx
from loguru import logger

from ada_lead_gen import config

_DEAD_TITLE_KEYWORDS = ("suspended", "parked", "expired", "account suspended",
                        "domain for sale", "buy this domain", "coming soon")

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


@dataclass
class LivenessResult:
    alive: bool
    final_url: str
    status_code: int | None
    html_length: int
    title: str
    reason: str = ""


def _extract_title(html: str) -> str:
    start = html.lower().find("<title>")
    end = html.lower().find("</title>")
    if start == -1 or end == -1:
        return ""
    return html[start + 7:end].strip()


async def check_liveness(url: str) -> LivenessResult:
    """
    Async GET to determine whether a site is live.

    Dead conditions: non-200 status, HTML < 500 bytes, or title containing
    suspension/parking keywords.
    """
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    headers = {"User-Agent": _USER_AGENT}
    timeout = httpx.Timeout(config.LIVENESS_TIMEOUT_S)

    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=timeout,
            headers=headers,
        ) as client:
            resp = await client.get(url)
    except httpx.TimeoutException:
        return LivenessResult(False, url, None, 0, "", reason="timeout")
    except httpx.TooManyRedirects:
        return LivenessResult(False, url, None, 0, "", reason="too_many_redirects")
    except Exception as exc:
        return LivenessResult(False, url, None, 0, "", reason=str(exc)[:120])

    final_url = str(resp.url)
    html = resp.text
    html_length = len(html)
    title = _extract_title(html)

    if resp.status_code != 200:
        return LivenessResult(False, final_url, resp.status_code, html_length, title,
                              reason=f"status_{resp.status_code}")

    if html_length < 500:
        return LivenessResult(False, final_url, resp.status_code, html_length, title,
                              reason="html_too_short")

    title_lower = title.lower()
    for kw in _DEAD_TITLE_KEYWORDS:
        if kw in title_lower:
            return LivenessResult(False, final_url, resp.status_code, html_length, title,
                                  reason=f"dead_title:{kw}")

    logger.debug("Alive: {} ({}b)", final_url, html_length)
    return LivenessResult(True, final_url, resp.status_code, html_length, title)


if __name__ == "__main__":
    import asyncio

    async def _test() -> None:
        cases = [
            ("https://example.com", True),
            ("https://httpbin.org/status/404", False),
        ]
        for url, expected in cases:
            r = await check_liveness(url)
            status = "OK" if r.alive == expected else "FAIL"
            print(f"[{status}] {url} -> alive={r.alive} reason={r.reason!r}")

    asyncio.run(_test())
