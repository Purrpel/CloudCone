"""CLI entry point: python -m ada_lead_gen <command>"""

from __future__ import annotations

import asyncio
import csv
import sys
from datetime import datetime
from pathlib import Path

import click
from loguru import logger

from ada_lead_gen.db import (
    add_opt_out,
    get_conn,
    get_last_run_stats,
    init_db,
)


@click.group()
def cli() -> None:
    """BizStreamPro ADA Lead Generation Pipeline."""
    pass


@cli.command()
@click.option("--city", default="", help="Single city, e.g. 'Austin, TX'")
@click.option("--industry", default="", help="Single industry, e.g. 'dentist'")
@click.option("--limit", default=25, show_default=True, help="Max businesses to discover")
@click.option("--cities-file", default="", help="Path to cities.txt")
@click.option("--industries-file", default="", help="Path to industries.txt")
def scan(city: str, industry: str, limit: int, cities_file: str, industries_file: str) -> None:
    """Run the lead generation pipeline."""
    from ada_lead_gen.pipeline import run_pipeline

    pairs: list[tuple[str, str]] = []

    if cities_file and industries_file:
        cities = Path(cities_file).read_text().splitlines()
        industries = Path(industries_file).read_text().splitlines()
        cities = [c.strip() for c in cities if c.strip()]
        industries = [i.strip() for i in industries if i.strip()]
        for c in cities:
            for i in industries:
                pairs.append((c, i))
    elif city and industry:
        pairs = [(city, industry)]
    else:
        click.echo("Provide --city + --industry OR --cities-file + --industries-file")
        sys.exit(1)

    for c, i in pairs:
        click.echo(f"Scanning: {i} in {c} (limit={limit})")
        summary = asyncio.run(run_pipeline(c, i, limit))
        click.echo(
            f"  found={summary['found']} alive={summary['alive']} "
            f"qualified={summary['qualified']} written={summary['written']} "
            f"cost=${summary['total_cost_usd']:.4f}"
        )


@cli.command()
def report() -> None:
    """Show stats from the last completed run."""
    init_db()
    stats = get_last_run_stats()
    if not stats:
        click.echo("No completed runs found.")
        return
    click.echo(f"Last run: {stats['run_id']}  ({stats['finished_at']})")
    click.echo(f"  City: {stats['city']}  Industry: {stats['industry']}")
    click.echo(f"  Found: {stats['found']}  Alive: {stats['alive']}  "
               f"Qualified: {stats['qualified']}  Written: {stats['written']}")
    click.echo(f"  Total LLM cost: ${stats['total_cost_usd']:.4f}")


@cli.command()
@click.option("--since", required=True, help="ISO date, e.g. 2026-04-01")
@click.option("--output", default="leads_export.csv", show_default=True)
def export(since: str, output: str) -> None:
    """Export qualified leads to CSV."""
    init_db()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM scanned_domains WHERE qualified=1 AND scanned_at >= ?",
            (since,),
        ).fetchall()
    if not rows:
        click.echo("No qualified leads found since " + since)
        return
    with open(output, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["domain", "scanned_at", "lead_score", "tier"])
        for r in rows:
            writer.writerow([r["domain"], r["scanned_at"], r["lead_score"], r["tier"]])
    click.echo(f"Exported {len(rows)} leads to {output}")


@cli.command("add-optout")
@click.option("--email", required=True, help="Email to opt out")
@click.option("--reason", default="manual", help="Reason")
def add_optout(email: str, reason: str) -> None:
    """Add an email to the opt-out list."""
    init_db()
    add_opt_out(email, reason)
    click.echo(f"Opted out: {email}")


@cli.command("regenerate-insights")
@click.option("--domain", required=True, help="Domain to reprocess")
def regenerate_insights(domain: str) -> None:
    """Rerun AI insights for a single lead (requires lead data in DB)."""
    click.echo(f"Re-generating insights for {domain} — requires manual lead record lookup.")
    click.echo("Tip: pull the lead record from Sheets, update it, then re-run pipeline with --limit 1.")


@cli.command("draft-email")
@click.option("--domain", required=True, help="Domain to draft for")
def draft_email(domain: str) -> None:
    """Placeholder — email drafting disabled (outreach module not active)."""
    click.echo(f"Email drafting is disabled. Open the Outreach Drafts tab in Google Sheets for {domain} and fill it manually.")


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
