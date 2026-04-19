"""Centralised configuration — all tunables loaded from env with safe defaults."""

import os
from dotenv import load_dotenv

load_dotenv()


def _float(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, default))
    except (TypeError, ValueError):
        return default


def _int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, default))
    except (TypeError, ValueError):
        return default


# ── LLM ──────────────────────────────────────────────────────────────────────
LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "anthropic")
CHEAP_MODEL: str = os.getenv("CHEAP_MODEL", "claude-haiku-4-5")
PREMIUM_MODEL: str = os.getenv("PREMIUM_MODEL", "claude-opus-4-7")
MAX_RUN_COST_USD: float = _float("MAX_RUN_COST_USD", 5.00)

# ── Pipeline behaviour ────────────────────────────────────────────────────────
MAX_CONCURRENT_SITES: int = _int("MAX_CONCURRENT_SITES", 5)
REQUEST_DELAY_SECONDS: float = _float("REQUEST_DELAY_SECONDS", 1.0)
RESCAN_COOLDOWN_DAYS: int = _int("RESCAN_COOLDOWN_DAYS", 30)
PLACES_CACHE_DAYS: int = 7

# ── Qualification thresholds (all overridable via env) ────────────────────────
MIN_CRITICAL: int = _int("MIN_CRITICAL", 1)
MIN_SERIOUS: int = _int("MIN_SERIOUS", 3)
MIN_TOTAL_VIOLATIONS: int = _int("MIN_TOTAL_VIOLATIONS", 15)
MIN_LEAD_SCORE: int = _int("MIN_LEAD_SCORE", 60)

# ── Scanner timeouts ──────────────────────────────────────────────────────────
LIVENESS_TIMEOUT_S: int = _int("LIVENESS_TIMEOUT_S", 10)
PLAYWRIGHT_TIMEOUT_S: int = _int("PLAYWRIGHT_TIMEOUT_S", 30)

# ── Google ────────────────────────────────────────────────────────────────────
GOOGLE_MAPS_API_KEY: str = os.getenv("GOOGLE_MAPS_API_KEY", "")
GOOGLE_SHEETS_ID: str = os.getenv("GOOGLE_SHEETS_ID", "")
GOOGLE_SERVICE_ACCOUNT_JSON: str = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "./creds.json")

# ── Business identity ─────────────────────────────────────────────────────────
MY_BUSINESS_NAME: str = os.getenv("MY_BUSINESS_NAME", "BizStreamPro")
MY_BUSINESS_ADDRESS: str = os.getenv("MY_BUSINESS_ADDRESS", "")
MY_SENDER_NAME: str = os.getenv("MY_SENDER_NAME", "")
MY_SENDER_EMAIL: str = os.getenv("MY_SENDER_EMAIL", "")
MY_UNSUBSCRIBE_URL: str = os.getenv("MY_UNSUBSCRIBE_URL", "")
MY_CALENDAR_LINK: str = os.getenv("MY_CALENDAR_LINK", "")
MY_WEBSITE: str = os.getenv("MY_WEBSITE", "")

# ── ADA lawsuit-prone industries ──────────────────────────────────────────────
HIGH_RISK_INDUSTRIES: list[str] = [
    "retail", "restaurant", "dental", "medical", "legal",
    "hospitality", "real estate", "auto dealer", "fitness",
    "ecommerce", "spa", "veterinary", "accounting",
]
