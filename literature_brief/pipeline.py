"""Coordinate discovery and assessment while keeping incomplete results visible."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
import logging
from pathlib import Path
import threading

from .http import HttpClient, HttpError
from .personal import fetch_seed_context, personal_order, rank_records, validate_personal_config
from .scoring import Scorer, ScoringError
from .search import evaluate_paper, summarize_searches
from .sources import deduplicate, fetch_all

LOGGER = logging.getLogger(__name__)


def publication_window(end: date | None = None) -> tuple[date, date]:
    """Seven complete UTC dates; end is exclusive because sources have day precision."""
    end = end or datetime.now(timezone.utc).date()
    return end - timedelta(days=7), end


def _personal_summary(personal: dict | None, seeds: list) -> dict | None:
    if not personal:
        return None
    return {"top_n": personal["top_n"], "min_score": personal["min_score"],
            "seed_papers": [{"doi": seed["doi"], "title": seed["title"]} for seed in seeds],
            "watch_authors": list(personal.get("watch_authors", []))}


def build_report(config: dict, *, end: date | None = None, cache_dir: Path = Path(".cache/scores"),
                 fetch_only: bool = False, client: HttpClient | None = None) -> tuple[dict, int]:
    start, end = publication_window(end)
    client = client or HttpClient()
    papers, statuses = fetch_all(config, start, end, client)
    personal = config.get("personal")
    validate_personal_config(personal)  # Fills defaults for callers that skip load_config.
    citing: dict = {}
    seeds: list = []
    if personal:
        seeds, cited_papers, citing, seed_statuses = fetch_seed_context(config, start, end, client)
        statuses.extend(seed_statuses)
        # Configured-source metadata comes first, so it wins on a shared DOI.
        papers = deduplicate(papers + cited_papers)
    papers.sort(key=lambda paper: (paper.published, paper.title.casefold()), reverse=True)
    records = []
    for paper in papers:
        record = asdict(paper)
        record["focused_search"] = evaluate_paper(paper.title, paper.abstract, config.get("search"))
        records.append(record)
    rank_records(records, config, seeds, citing)
    limit = config.get("max_papers", 0)
    # Never drop metadata when the user explicitly opts into a paid-call cap.
    eligible = [(paper, record) for paper, record in zip(papers, records) if paper.abstract.strip()]
    cap_reason = "Not scored because the configured max_papers limit was reached."
    if personal and personal.get("ai_shortlist"):
        # Free-tier friendly: only the strongest personal matches reach the LLM.
        by_score = {id(record): paper for paper, record in eligible}
        ranked = [record for record in personal_order([record for _, record in eligible]) if record["personal_score"] > 0]
        limit = min(limit, personal["ai_shortlist"]) if limit else personal["ai_shortlist"]
        eligible = [(by_score[id(record)], record) for record in ranked]
        cap_reason = "Not sent for AI assessment: outside your personal shortlist."
    selected_ids = {id(paper) for paper, _ in (eligible[:limit] if limit else eligible)}
    scorer = None if fetch_only else Scorer(config, client, cache_dir)
    provider_stopped = threading.Event()

    def assess(item):
        paper, record = item
        record.update(relevance=None, methodology="", key_findings="", rationale="", error="")
        if not paper.abstract.strip():
            record.update(status="missing_abstract", error="No abstract available from the configured sources.")
        elif fetch_only:
            record.update(status="not_scored", error="Discovery only; no LLM calls were requested.")
        elif id(paper) not in selected_ids:
            record.update(status="capped", error=cap_reason)
        else:
            try:
                record.update(scorer.score(paper, allow_request=not provider_stopped.is_set()), status="scored")
            except HttpError as exc:
                record.update(status="scoring_failed", error=str(exc))
                if exc.permanent:
                    provider_stopped.set()
            except (ScoringError, OSError) as exc:
                record.update(status="scoring_failed", error=str(exc))
        return record

    with ThreadPoolExecutor(max_workers=config["llm"].get("workers", 3)) as executor:
        records = list(executor.map(assess, zip(papers, records)))
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
        "focused_searches": summarize_searches(records, config.get("search")),
        "personal": _personal_summary(personal, seeds),
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
