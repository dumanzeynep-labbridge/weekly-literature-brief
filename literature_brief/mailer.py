"""Email the week's top picks through the user's own SMTP account (free).

Run after deployment:  python -m literature_brief.mailer --brief site/brief.json --site-url URL
--brief also accepts the published https URL of brief.json. Settings come from
environment variables only: SMTP_HOST, SMTP_PORT (587 STARTTLS or 465 SSL),
SMTP_USERNAME, SMTP_PASSWORD, EMAIL_TO (comma-separated), and optional EMAIL_FROM.
Without SMTP_HOST, SMTP_PASSWORD, or EMAIL_TO the step is skipped successfully.
"""

from __future__ import annotations

import argparse
from email.message import EmailMessage
from html import escape
import json
import logging
import os
from pathlib import Path
import smtplib
import ssl
import sys

from .http import HttpClient, HttpError
from .render import _date_label, _personal_score, _safe_url, _score, select_featured, select_personal

LOGGER = logging.getLogger(__name__)


def email_picks(report: dict) -> list[dict]:
    """AI-assessed picks when available, otherwise the free personal ranking."""
    settings = report.get("personal") or {}
    limit = int(settings.get("top_n", report.get("top_n", 10)) or 10)
    if not report.get("fetch_only"):
        # Same order as the page: AI picks first, then free-ranking picks.
        return select_featured(report)[:limit]
    if settings:
        return select_personal(report)[0]
    return []


def _subject(report: dict, picks: list[dict]) -> str:
    title = str(report.get("title") or "Weekly literature brief")
    week = _date_label(report.get("window_start"))
    return f"{title}: {len(picks)} paper{'s' if len(picks) != 1 else ''} for you (week of {week})"


def build_message(report: dict, site_url: str = "") -> tuple[str, str, str]:
    """Return subject, plain text, and HTML bodies."""
    picks = email_picks(report)
    site = _safe_url(site_url) or ""
    window = f'{_date_label(report.get("window_start"))} to {_date_label(report.get("window_end"))} (end exclusive)'
    lines = [str(report.get("title") or "Weekly literature brief"), window, ""]
    blocks = []
    if not picks:
        lines.append("No paper reached your match threshold this week.")
        blocks.append("<p>No paper reached your match threshold this week.</p>")
    for rank, paper in enumerate(picks, 1):
        url = _safe_url(paper.get("url")) or ""
        score = _score(paper)
        badge = f"{score}/10 relevance" if score is not None else f'{_personal_score(paper) or 0:g} match points'
        meta = " · ".join(filter(None, [str(paper.get("journal") or ""), _date_label(paper.get("published")),
                                        "preprint" if paper.get("is_preprint") else "", badge]))
        reasons = list(paper.get("personal_reasons") or [])[:3]
        rationale = str(paper.get("rationale") or "") if score is not None else ""
        lines += [f"{rank}. {paper.get('title') or 'Untitled paper'}", f"   {meta}"]
        if url:
            lines.append(f"   {url}")
        if rationale:
            lines.append(f"   Why it matters: {rationale}")
        lines += [f"   - {reason}" for reason in reasons] + [""]
        title_html = escape(str(paper.get("title") or "Untitled paper"))
        if url:
            title_html = f'<a href="{escape(url)}" style="color:#136c61;text-decoration:none">{title_html}</a>'
        reason_html = "".join(f"<li>{escape(reason)}</li>" for reason in reasons)
        blocks.append(
            '<div style="margin:0 0 22px;padding:0 0 18px;border-bottom:1px solid #dcded5">'
            f'<div style="font-size:12px;color:#607072">{rank:02d} · {escape(meta)}</div>'
            f'<div style="font:18px/1.35 Georgia,serif;margin:4px 0 8px">{title_html}</div>'
            + (f'<p style="font-size:14px;margin:0 0 6px"><strong>Why it matters:</strong> {escape(rationale)}</p>' if rationale else "")
            + (f'<ul style="font-size:13px;color:#384a4c;margin:0;padding-left:18px">{reason_html}</ul>' if reason_html else "")
            + "</div>"
        )
    if site:
        lines.append(f"Full brief, search and all other papers: {site}")
    footer = (f'<p style="font-size:13px"><a href="{escape(site)}" style="color:#136c61">Open the full brief</a> '
              "for search, abstracts, and every other paper this week.</p>" if site else "")
    html = ('<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"></head><body>'
            '<div style="max-width:640px;margin:auto;font-family:system-ui,-apple-system,Segoe UI,sans-serif;color:#202f31">'
            f'<h1 style="font:28px Georgia,serif;margin:0 0 4px">{escape(str(report.get("title") or "Weekly literature brief"))}</h1>'
            f'<p style="font-size:13px;color:#607072;margin:0 0 24px">{escape(window)}</p>'
            + "".join(blocks) + footer + "</div></body></html>")
    return _subject(report, picks), "\n".join(lines) + "\n", html


def load_brief(location: str) -> dict:
    if location.startswith("https://"):
        return HttpClient(timeout=60).get_json(location)
    return json.loads(Path(location).read_text(encoding="utf-8"))


def send(subject: str, text: str, html: str) -> bool:
    host = os.environ.get("SMTP_HOST", "").strip()
    password = os.environ.get("SMTP_PASSWORD", "")
    recipients = [value.strip() for value in os.environ.get("EMAIL_TO", "").split(",") if value.strip()]
    if not host or not password or not recipients:
        LOGGER.info("Email skipped: set SMTP_HOST, SMTP_PASSWORD, and EMAIL_TO to enable it.")
        return False
    username = os.environ.get("SMTP_USERNAME", "").strip() or recipients[0]
    port = int(os.environ.get("SMTP_PORT", "").strip() or 587)
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = os.environ.get("EMAIL_FROM", "").strip() or username
    message["To"] = ", ".join(recipients)
    message.set_content(text)
    message.add_alternative(html, subtype="html")
    context = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=context, timeout=60) as server:
            server.login(username, password)
            server.send_message(message)
    else:
        with smtplib.SMTP(host, port, timeout=60) as server:
            server.starttls(context=context)
            server.login(username, password)
            server.send_message(message)
    LOGGER.info("Emailed %s recipient(s).", len(recipients))
    return True


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Email the week's top papers.")
    parser.add_argument("--brief", default="site/brief.json", help="Path or https URL of brief.json.")
    parser.add_argument("--site-url", default="", help="Link to the published brief.")
    parser.add_argument("--preview", type=Path, help="Write the HTML email here instead of sending it.")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    try:
        subject, text, html = build_message(load_brief(args.brief), args.site_url)
        if args.preview:
            args.preview.write_text(html, encoding="utf-8")
            LOGGER.info("Subject: %s. Preview written to %s", subject, args.preview)
            return 0
        send(subject, text, html)
        return 0
    except (OSError, ValueError, HttpError, smtplib.SMTPException) as exc:
        # Never echo credentials; SMTP errors name the server response only.
        LOGGER.error("Email failed: %s", exc.__class__.__name__ if isinstance(exc, smtplib.SMTPAuthenticationError) else exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
