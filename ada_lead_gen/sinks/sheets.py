"""Write leads, AI insights, and outreach drafts to Google Sheets."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import gspread
from google.oauth2.service_account import Credentials
from loguru import logger

from ada_lead_gen import config

_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

_LEADS_HEADERS = [
    "Business Name", "Website", "Final URL", "City", "Industry", "Category",
    "Phone", "Emails", "Best Contact", "Contact Confidence",
    "Critical", "Serious", "Moderate", "Minor", "Total Violations",
    "Top Violation Rules", "Screenshot Path",
    "Risk Score", "Lead Score", "Tier", "Scanned At",
]

_INSIGHTS_HEADERS = [
    "Domain", "Business Snapshot", "Pain Point Angle", "Personalization Hooks",
    "Industry Lawsuit Context", "Objection Preempt", "Recommended Tone", "Red Flags",
    "Headline", "Top 3 Issues", "Legal Exposure", "User Impact", "Tone Hook",
    "Generated At",
]

_DRAFTS_HEADERS = [
    "Domain", "Send-To Email", "Subject", "Body V1", "Body V2",
    "Status", "Scheduled Send", "Last Updated", "Notes",
]


def _get_client() -> gspread.Client:
    creds = Credentials.from_service_account_file(
        config.GOOGLE_SERVICE_ACCOUNT_JSON, scopes=_SCOPES
    )
    return gspread.authorize(creds)


def _get_or_create_sheet(spreadsheet: gspread.Spreadsheet, title: str, headers: list[str]) -> gspread.Worksheet:
    """Return existing worksheet or create it with header row."""
    try:
        ws = spreadsheet.worksheet(title)
    except gspread.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=title, rows=1000, cols=len(headers))
        ws.append_row(headers, value_input_option="RAW")
        logger.info("Created sheet tab: {}", title)
    return ws


def _domain_index(ws: gspread.Worksheet) -> dict[str, int]:
    """Return {domain: row_number} for existing rows (1-indexed)."""
    records = ws.get_all_values()
    if len(records) <= 1:
        return {}
    index: dict[str, int] = {}
    for i, row in enumerate(records[1:], start=2):
        if row:
            index[row[0].lower()] = i
    return index


class SheetsWriter:
    """Idempotent writer for all three Google Sheets tabs."""

    def __init__(self) -> None:
        client = _get_client()
        self._spreadsheet = client.open_by_key(config.GOOGLE_SHEETS_ID)
        self._leads_ws = _get_or_create_sheet(self._spreadsheet, "Leads", _LEADS_HEADERS)
        self._insights_ws = _get_or_create_sheet(self._spreadsheet, "AI Insights", _INSIGHTS_HEADERS)
        self._drafts_ws = _get_or_create_sheet(self._spreadsheet, "Outreach Drafts", _DRAFTS_HEADERS)
        logger.info("SheetsWriter connected to spreadsheet {}", config.GOOGLE_SHEETS_ID)

    def _upsert_row(self, ws: gspread.Worksheet, domain: str, row: list[str]) -> None:
        """Insert new row or update existing one, keyed on domain (col A)."""
        idx = _domain_index(ws)
        if domain.lower() in idx:
            row_num = idx[domain.lower()]
            ws.update(f"A{row_num}", [row])
            logger.debug("Updated row {} for {}", row_num, domain)
        else:
            ws.append_row(row, value_input_option="RAW")
            logger.debug("Appended new row for {}", domain)

    def write_lead(self, lead: dict[str, Any]) -> None:
        """Write or update a lead row in the Leads tab."""
        emails_str = "; ".join(
            e.get("email", "") if isinstance(e, dict) else str(e)
            for e in lead.get("emails", [])
        )
        best = lead.get("best_contact") or {}
        top_rules = ", ".join(
            v.get("id", "") if isinstance(v, dict) else str(v)
            for v in lead.get("violations", [])[:5]
        )
        domain = _extract_domain(lead.get("final_url") or lead.get("website", ""))

        row = [
            lead.get("name", ""),
            lead.get("website", ""),
            lead.get("final_url", ""),
            lead.get("city", ""),
            lead.get("industry", ""),
            lead.get("category", ""),
            lead.get("phone", ""),
            emails_str,
            best.get("contact", "") if best else "",
            str(best.get("confidence", "")) if best else "",
            str(lead.get("critical", 0)),
            str(lead.get("serious", 0)),
            str(lead.get("moderate", 0)),
            str(lead.get("minor", 0)),
            str(lead.get("total_violations", 0)),
            top_rules,
            lead.get("screenshot_path", ""),
            str(lead.get("risk_score", "")),
            str(lead.get("lead_score", "")),
            lead.get("tier", ""),
            lead.get("scanned_at", datetime.utcnow().isoformat()),
        ]
        self._upsert_row(self._leads_ws, domain, row)

    def write_insights(self, domain: str, insights: dict[str, Any], summary: dict[str, Any]) -> None:
        """Write or update an AI Insights row."""
        row = [
            domain,
            insights.get("business_snapshot", ""),
            insights.get("pain_point_angle", ""),
            " | ".join(insights.get("personalization_hooks", [])),
            insights.get("industry_lawsuit_context", ""),
            insights.get("objection_preempt", ""),
            insights.get("recommended_tone", ""),
            ", ".join(insights.get("red_flags", [])),
            summary.get("headline", ""),
            " | ".join(summary.get("top_3_issues", [])),
            summary.get("legal_exposure", ""),
            summary.get("user_impact", ""),
            summary.get("tone_hook", ""),
            insights.get("generated_at", datetime.utcnow().isoformat()),
        ]
        self._upsert_row(self._insights_ws, domain, row)

    def write_draft_placeholder(self, domain: str, best_email: str) -> None:
        """Create an empty Outreach Drafts row for manual completion."""
        idx = _domain_index(self._drafts_ws)
        if domain.lower() in idx:
            return  # already exists, don't overwrite
        row = [
            domain,
            best_email,
            "",  # Subject — fill manually
            "",  # Body V1
            "",  # Body V2
            "draft",
            "",  # Scheduled Send
            datetime.utcnow().isoformat(),
            "",  # Notes
        ]
        self._drafts_ws.append_row(row, value_input_option="RAW")
        logger.debug("Created draft placeholder for {}", domain)


def _extract_domain(url: str) -> str:
    from urllib.parse import urlparse
    parsed = urlparse(url)
    return (parsed.netloc or url).replace("www.", "").lower()
