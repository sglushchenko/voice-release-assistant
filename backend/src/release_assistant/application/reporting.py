"""Turns analytics results into text a person can read. Pure formatting, no I/O."""

import calendar
from decimal import Decimal

from release_assistant.application.analytics import MonthlyReport, MonthSummary
from release_assistant.application.engagement import EngagementReport


def _label(summary: MonthSummary) -> str:
    return f"{calendar.month_name[summary.month]} {summary.year}"


def _money(amounts: dict[str, Decimal]) -> str:
    if not amounts:
        return "none"
    return ", ".join(f"{amount:,.2f} {currency}" for currency, amount in amounts.items())


def _change(pct: float | None) -> str:
    return "no comparison available" if pct is None else f"{pct:+.1f}%"


def render_markdown(report: MonthlyReport) -> str:
    """Markdown for chat, email or an artifact.

    The caveat is always the second line: the figures are estimates and, for a current
    month, incomplete. Someone skimming the report must not mistake them for payouts.
    """
    current, previous = report.current, report.previous
    scope = report.app.name if report.app else "All apps"
    caveat = "Estimated figures from Sales and Trends; final payouts come from Finance reports."
    if current.is_partial:
        through = current.last_data_day.isoformat() if current.last_data_day else "no data yet"
        caveat += f" Data is partial (last day with data: {through})."

    lines = [
        f"# {_label(current)}: {scope}",
        "",
        f"> {caveat}",
        "",
        f"- Downloads: {current.downloads} ({_change(report.downloads_change_pct)} "
        f"vs {_label(previous)}: {previous.downloads})",
        f"- Updates: {current.updates}",
        f"- In-app purchases: {current.in_app_purchases}",
        f"- Estimated proceeds: {_money(current.proceeds)}",
    ]
    if report.top_territories:
        lines += ["", "## Top territories", "", "| Territory | Downloads |", "|---|---|"]
        lines += [f"| {t.territory} | {t.downloads} |" for t in report.top_territories]
    if report.by_app:
        lines += ["", "## By app", "", "| App | Downloads | Estimated proceeds |", "|---|---|---|"]
        lines += [f"| {a.app.name} | {a.downloads} | {_money(a.proceeds)} |" for a in report.by_app]
    return "\n".join(lines) + "\n"


def _rate(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1f}%"


def render_engagement_markdown(report: EngagementReport) -> str:
    """Markdown for the funnel report. Coverage problems are stated right under the title:
    a figure computed from a partly published period must not read as final."""
    scope = report.app.name if report.app else "All apps"
    totals, previous, changes = report.totals, report.previous, report.changes
    lines = [
        f"# Engagement {report.start} to {report.end}: {scope}",
        "",
        "> Source: App Store Analytics, published 1-2 days late. Downloads here can differ "
        "from Sales and Trends.",
    ]
    if not report.is_complete:
        first, last = report.coverage.first_day, report.coverage.last_day
        published = f"{first} to {last}" if first and last else "nothing yet"
        lines.append(f"> Incomplete: published data covers {published}, not the whole period.")
    elif not report.previous_is_complete:
        lines.append("> The comparison period is not fully published, so changes are unreliable.")
    versus = f"vs {report.previous_start} to {report.previous_end}"
    lines += [
        "",
        f"- Impressions: {totals.impressions:,} ({_change(changes.impressions)} {versus}: "
        f"{previous.impressions:,})",
        f"- Product page views: {totals.product_page_views:,} "
        f"({_change(changes.product_page_views)}: {previous.product_page_views:,})",
        f"- Downloads: {totals.downloads:,} ({_change(changes.downloads)}: {previous.downloads:,})",
        f"- Tap-through (page views / impressions): {_rate(report.tap_through_pct)}",
        f"- Conversion (downloads / page views): {_rate(report.conversion_pct)}",
    ]
    if report.by_app:
        lines += [
            "",
            "## By app",
            "",
            "| App | Impressions | Page views | Downloads |",
            "|---|---|---|---|",
        ]
        lines += [
            f"| {a.app.name} | {a.totals.impressions:,} | {a.totals.product_page_views:,} "
            f"| {a.totals.downloads:,} |"
            for a in report.by_app
        ]
    return "\n".join(lines) + "\n"
