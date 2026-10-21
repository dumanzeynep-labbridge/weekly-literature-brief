"""Coordinate discovery and assessment while keeping incomplete results visible."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
import logging
from pathlib import Path
import threading

from .http import HttpClient, HttpError
from .scoring import Scorer, ScoringError
from .sources import fetch_all

LOGGER = logging.getLogger(__name__)


def publication_window(end: date | None = None) -> tuple[date, date]:
    """Seven complete UTC dates; end is exclusive because sources have day precision."""
    end = end or datetime.now(timezone.utc).date()
    return end - timedelta(days=7), end


def build_report(config: dict, *, end: date | None = None, cache_dir: Path = Path(".cache/scores"),
                 fetch_only: bool = False, client: HttpClient | None = None) -> tuple[dict, int]:
    start, end = publication_window(end)
    client = client or HttpClient()
    papers, statuses = fetch_all(config, start, end, client)
    papers.sort(key=lambda paper: (paper.published, paper.title.casefold()), reverse=True)
    limit = config.get("max_papers", 0)
    # Never drop metadata when the user explicitly opts into a paid-call cap.
    eligible = [paper for paper in papers if paper.abstract.strip()]
    selected_ids = {id(paper) for paper in (eligible[:limit] if limit else eligible)}
    scorer = None if fetch_only else Scorer(config, client, cache_dir)
    provider_stopped = threading.Event()

    def assess(paper):
        record = asdict(paper)
        record.update(relevance=None, methodology="", key_findings="", rationale="", error="")
        if not paper.abstract.strip():
            record.update(status="missing_abstract", error="No abstract available from the configured sources.")
        elif fetch_only:
            record.update(status="not_scored", error="Discovery only; no LLM calls were requested.")
        elif id(paper) not in selected_ids:
            record.update(status="capped", error="Not scored because the configured max_papers limit was reached.")
        else:
            try:
                record.update(scorer.score(paper, allow_request=not provider_stopped.is_set()), status="scored")
            except HttpError as exc:
                record.update(status="scoring_failed", error=str(exc))
                if exc.status in {400, 401, 402, 403, 404}:
                    provider_stopped.set()
            except (ScoringError, OSError) as exc:
                record.update(status="scoring_failed", error=str(exc))
        return record

    with ThreadPoolExecutor(max_workers=config["llm"].get("workers", 3)) as executor:
        records = list(executor.map(assess, papers))
    stats = {"discovered": len(records)}
    for status in ("scored", "missing_abstract", "scoring_failed", "capped", "not_scored"):
        stats[status] = sum(paper["status"] == status for paper in records)
    report = {
        "title": config["title"], "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_start": start.isoformat(), "window_end": end.isoformat(),
        "interests": config["interests"], "provider": config["llm"]["provider"],
        "interest_labels": config.get("interest_labels", config["interests"]),
        "model": config["llm"]["model"], "demo": False, "fetch_only": fetch_only,
        "min_relevance": config["min_relevance"], "top_n": config["top_n"],
        "papers": records, "source_statuses": statuses, "stats": stats,
    }
    for status in statuses:
        if status["status"] != "ok":
            LOGGER.warning("Source %s: %s", status["name"], status["message"])
    LOGGER.info("Discovered %s papers; scored %s; missing abstracts %s; scoring failures %s.",
                len(records), stats["scored"], stats["missing_abstract"], stats["scoring_failed"])
    # A failed collection must not replace the previous published edition.
    discovery_names = {spec["name"] for spec in config["sources"].get("crossref", [])}
    if config["sources"].get("biorxiv", True):
        discovery_names.add("bioRxiv")
    discovery = [status for status in statuses if status["name"] in discovery_names]
    all_sources_failed = not discovery or all(status["status"] == "error" for status in discovery)
    all_sources_failed |= not papers and not any(status["status"] == "ok" for status in discovery)
    no_assessments = not fetch_only and bool(selected_ids) and stats["scored"] == 0
    return report, 2 if all_sources_failed else (3 if no_assessments else 0)
