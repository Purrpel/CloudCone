"""SQLite layer: dedup, run state, LLM cost tracking, opt-outs, Places cache."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Generator

from loguru import logger

DB_PATH = Path("ada_leads.db")


@contextmanager
def get_conn() -> Generator[sqlite3.Connection, None, None]:
    """Yield a connection with row_factory set to Row."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Create all tables if they don't exist."""
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS scanned_domains (
                domain          TEXT PRIMARY KEY,
                scanned_at      TEXT NOT NULL,
                qualified       INTEGER NOT NULL DEFAULT 0,
                lead_score      INTEGER,
                tier            TEXT
            );

            CREATE TABLE IF NOT EXISTS opt_outs (
                email           TEXT PRIMARY KEY,
                added_at        TEXT NOT NULL,
                reason          TEXT
            );

            CREATE TABLE IF NOT EXISTS llm_calls (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id          TEXT NOT NULL,
                timestamp       TEXT NOT NULL,
                provider        TEXT NOT NULL,
                model           TEXT NOT NULL,
                purpose         TEXT NOT NULL,
                input_tokens    INTEGER NOT NULL DEFAULT 0,
                output_tokens   INTEGER NOT NULL DEFAULT 0,
                cost_usd        REAL NOT NULL DEFAULT 0.0
            );

            CREATE TABLE IF NOT EXISTS places_cache (
                cache_key       TEXT PRIMARY KEY,
                response_json   TEXT NOT NULL,
                cached_at       TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS runs (
                run_id          TEXT PRIMARY KEY,
                started_at      TEXT NOT NULL,
                finished_at     TEXT,
                city            TEXT,
                industry        TEXT,
                found           INTEGER DEFAULT 0,
                alive           INTEGER DEFAULT 0,
                qualified       INTEGER DEFAULT 0,
                written         INTEGER DEFAULT 0,
                total_cost_usd  REAL DEFAULT 0.0,
                status          TEXT DEFAULT 'running'
            );
        """)
    logger.info("Database initialised at {}", DB_PATH)


# ── Scanned domains ───────────────────────────────────────────────────────────

def was_recently_scanned(domain: str, cooldown_days: int) -> bool:
    """Return True if domain was scanned within cooldown_days."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT scanned_at FROM scanned_domains WHERE domain = ?", (domain,)
        ).fetchone()
    if row is None:
        return False
    scanned_at = datetime.fromisoformat(row["scanned_at"])
    return datetime.utcnow() - scanned_at < timedelta(days=cooldown_days)


def mark_scanned(domain: str, qualified: bool, lead_score: int | None = None, tier: str | None = None) -> None:
    """Upsert a domain scan record."""
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO scanned_domains (domain, scanned_at, qualified, lead_score, tier)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(domain) DO UPDATE SET
                 scanned_at = excluded.scanned_at,
                 qualified  = excluded.qualified,
                 lead_score = excluded.lead_score,
                 tier       = excluded.tier""",
            (domain, datetime.utcnow().isoformat(), int(qualified), lead_score, tier),
        )


# ── Opt-outs ──────────────────────────────────────────────────────────────────

def add_opt_out(email: str, reason: str = "") -> None:
    """Add an email address to the opt-out list."""
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO opt_outs (email, added_at, reason)
               VALUES (?, ?, ?)
               ON CONFLICT(email) DO NOTHING""",
            (email.lower().strip(), datetime.utcnow().isoformat(), reason),
        )
    logger.info("Opt-out recorded: {}", email)


def is_opted_out(email: str) -> bool:
    """Return True if the email is on the opt-out list."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM opt_outs WHERE email = ?", (email.lower().strip(),)
        ).fetchone()
    return row is not None


# ── LLM cost tracking ─────────────────────────────────────────────────────────

def log_llm_call(
    run_id: str,
    provider: str,
    model: str,
    purpose: str,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float,
) -> None:
    """Record an LLM call to the tracking table."""
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO llm_calls
               (run_id, timestamp, provider, model, purpose, input_tokens, output_tokens, cost_usd)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                run_id,
                datetime.utcnow().isoformat(),
                provider,
                model,
                purpose,
                input_tokens,
                output_tokens,
                cost_usd,
            ),
        )


def get_run_spend(run_id: str) -> float:
    """Return total LLM spend in USD for a run."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0.0) as total FROM llm_calls WHERE run_id = ?",
            (run_id,),
        ).fetchone()
    return float(row["total"])


# ── Places cache ──────────────────────────────────────────────────────────────

def get_cached_places(cache_key: str, max_age_days: int) -> list[dict] | None:
    """Return cached Places API response or None if stale/missing."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT response_json, cached_at FROM places_cache WHERE cache_key = ?",
            (cache_key,),
        ).fetchone()
    if row is None:
        return None
    cached_at = datetime.fromisoformat(row["cached_at"])
    if datetime.utcnow() - cached_at > timedelta(days=max_age_days):
        return None
    return json.loads(row["response_json"])


def set_cached_places(cache_key: str, data: list[dict]) -> None:
    """Store a Places API response in the cache."""
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO places_cache (cache_key, response_json, cached_at)
               VALUES (?, ?, ?)
               ON CONFLICT(cache_key) DO UPDATE SET
                 response_json = excluded.response_json,
                 cached_at     = excluded.cached_at""",
            (cache_key, json.dumps(data), datetime.utcnow().isoformat()),
        )


# ── Runs ──────────────────────────────────────────────────────────────────────

def start_run(run_id: str, city: str, industry: str) -> None:
    """Create a run record."""
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO runs (run_id, started_at, city, industry)
               VALUES (?, ?, ?, ?)""",
            (run_id, datetime.utcnow().isoformat(), city, industry),
        )


def finish_run(run_id: str, stats: dict[str, Any]) -> None:
    """Update run record with final stats."""
    with get_conn() as conn:
        conn.execute(
            """UPDATE runs SET
                 finished_at    = ?,
                 found          = ?,
                 alive          = ?,
                 qualified      = ?,
                 written        = ?,
                 total_cost_usd = ?,
                 status         = 'done'
               WHERE run_id = ?""",
            (
                datetime.utcnow().isoformat(),
                stats.get("found", 0),
                stats.get("alive", 0),
                stats.get("qualified", 0),
                stats.get("written", 0),
                stats.get("total_cost_usd", 0.0),
                run_id,
            ),
        )


def get_last_run_stats() -> dict[str, Any] | None:
    """Return stats dict for the most recent completed run."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM runs WHERE status = 'done' ORDER BY finished_at DESC LIMIT 1"
        ).fetchone()
    if row is None:
        return None
    return dict(row)


if __name__ == "__main__":
    init_db()
    print("DB tables created.")

    # Basic smoke tests
    mark_scanned("example.com", qualified=True, lead_score=75, tier="A")
    assert was_recently_scanned("example.com", cooldown_days=30)
    assert not was_recently_scanned("unknown.com", cooldown_days=30)

    add_opt_out("test@example.com", reason="manual")
    assert is_opted_out("test@example.com")
    assert not is_opted_out("other@example.com")

    run_id = "test-run-001"
    start_run(run_id, "Austin, TX", "dentist")
    log_llm_call(run_id, "anthropic", "claude-haiku-4-5", "classify", 100, 50, 0.0001)
    spend = get_run_spend(run_id)
    assert spend > 0, f"Expected spend > 0, got {spend}"
    finish_run(run_id, {"found": 10, "alive": 8, "qualified": 3, "written": 3, "total_cost_usd": spend})

    last = get_last_run_stats()
    assert last is not None
    assert last["city"] == "Austin, TX"

    set_cached_places("austin_dentist_0", [{"name": "Test Dental"}])
    cached = get_cached_places("austin_dentist_0", max_age_days=7)
    assert cached is not None and cached[0]["name"] == "Test Dental"

    print("All db.py smoke tests passed.")
