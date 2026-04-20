"""Discover businesses via Google Places Text Search + Place Details."""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any

import googlemaps
from loguru import logger

from ada_lead_gen import config
from ada_lead_gen.db import get_cached_places, set_cached_places


@dataclass
class Business:
    name: str
    website: str
    phone: str
    address: str
    place_id: str
    categories: list[str] = field(default_factory=list)
    rating: float | None = None
    review_count: int | None = None
    city: str = ""
    industry: str = ""


def _page_key(city: str, industry: str, page: int) -> str:
    """Deterministic cache key for a Text Search page."""
    raw = f"search|{city.lower()}|{industry.lower()}|p{page}"
    return hashlib.md5(raw.encode()).hexdigest()


def _detail_key(place_id: str) -> str:
    """Deterministic cache key for Place Details (not tied to city/industry)."""
    return "detail:" + hashlib.md5(place_id.encode()).hexdigest()


def _extract_business(place: dict[str, Any], city: str, industry: str) -> Business | None:
    """Map a Place Details dict to a Business. Return None if no website."""
    website = place.get("website", "").strip()
    if not website:
        return None
    return Business(
        name=place.get("name", ""),
        website=website,
        phone=place.get("formatted_phone_number", ""),
        address=place.get("formatted_address", ""),
        place_id=place.get("place_id", ""),
        categories=place.get("types", []),
        rating=place.get("rating"),
        review_count=place.get("user_ratings_total"),
        city=city,
        industry=industry,
    )


def find_businesses(city: str, industry: str, limit: int = 60) -> list[Business]:
    """
    Discover businesses using Google Places Text Search.

    Results are cached in SQLite for PLACES_CACHE_DAYS to avoid duplicate API spend.
    Returns up to `limit` businesses that have a website URL.
    """
    if not config.GOOGLE_MAPS_API_KEY:
        raise ValueError("GOOGLE_MAPS_API_KEY is not set")

    client = googlemaps.Client(key=config.GOOGLE_MAPS_API_KEY)
    query = f"{industry} in {city}"
    businesses: list[Business] = []
    page_token: str | None = None
    page = 0

    while len(businesses) < limit:
        page_ck = _page_key(city, industry, page)
        cached_page = get_cached_places(page_ck, config.PLACES_CACHE_DAYS)

        if cached_page is not None:
            logger.debug("Places cache hit: {} page {}", query, page)
            # Stored format: [{"results": [...], "next_page_token": "..."}]
            entry = cached_page[0] if cached_page else {}
            results = entry.get("results", [])
            page_token = entry.get("next_page_token") or None
        else:
            logger.info("Places API call: '{}' page {}", query, page)
            kwargs: dict[str, Any] = {"query": query}
            if page_token:
                # Google requires ~2s between page requests
                time.sleep(2.1)
                kwargs["page_token"] = page_token
            try:
                resp = client.places(**kwargs)
            except Exception as exc:
                logger.warning("Places Text Search failed: {}", exc)
                break
            results = resp.get("results", [])
            page_token = resp.get("next_page_token") or None
            set_cached_places(page_ck, [{"results": results, "next_page_token": page_token}])

        for place in results:
            if len(businesses) >= limit:
                break
            place_id = place.get("place_id", "")
            if not place_id:
                continue

            detail_ck = _detail_key(place_id)
            cached_detail = get_cached_places(detail_ck, config.PLACES_CACHE_DAYS)

            if cached_detail:
                detail = cached_detail[0] if cached_detail else {}
            else:
                try:
                    detail_resp = client.place(
                        place_id,
                        fields=[
                            "name", "website", "formatted_phone_number",
                            "formatted_address", "place_id", "types",
                            "rating", "user_ratings_total",
                        ],
                    )
                    detail = detail_resp.get("result", {})
                    set_cached_places(detail_ck, [detail])
                except Exception as exc:
                    logger.warning("Place Details failed for {}: {}", place_id, exc)
                    continue

            biz = _extract_business(detail, city, industry)
            if biz:
                businesses.append(biz)

        if not page_token:
            break
        page += 1

    logger.info("Found {} businesses with websites for '{}' in '{}'", len(businesses), industry, city)
    return businesses


if __name__ == "__main__":
    # Smoke test — requires GOOGLE_MAPS_API_KEY in .env
    results = find_businesses("Austin, TX", "dentist", limit=5)
    for b in results:
        print(f"  {b.name} | {b.website} | {b.phone}")
    print(f"Total: {len(results)}")
