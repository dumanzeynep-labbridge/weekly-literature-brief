"""Retrieve publication metadata without confusing publication and deposit dates.

APIs: https://api.biorxiv.org/,
https://www.crossref.org/documentation/retrieve-metadata/rest-api/,
https://europepmc.org/RestfulWebService.

Every source is paginated to exhaustion. API failures preserve usable records and
produce an explicit status; absent abstracts stay absent rather than invented.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import replace
from datetime import date, timedelta
from html.parser import HTMLParser
import re
import time
from typing import Any
from urllib.parse import quote

from .http import HttpClient, HttpError
from .models import Paper


_CROSSREF_PAGE_SIZE = 1000
_BIORXIV_PAGE_SIZE = 30
_EUROPE_PMC_BATCH_SIZE = 40
_EUROPE_PMC_PAGE_SIZE = 1000


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag.split(":")[-1] in {"p", "title", "sec", "br"}:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag.split(":")[-1] in {"p", "title", "sec"}:
            self.parts.append(" ")


def plain_text(value: Any) -> str:
    """Remove JATS/HTML markup while preserving scientific inline text."""
    if not isinstance(value, str):
        return ""
    parser = _TextExtractor()
    parser.feed(value)
    return " ".join("".join(parser.parts).split())


def normalize_doi(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", value.strip(), flags=re.I).lower()


def _iso_day(value: Any) -> date | None:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _date_parts(value: Any) -> list[int] | None:
    if not isinstance(value, dict):
        return None
    parts = value.get("date-parts")
    if not isinstance(parts, list) or not parts or not isinstance(parts[0], list):
        return None
    first = parts[0]
    if not first or len(first) > 3 or any(type(number) is not int for number in first):
        return None
    try:
        date(*(first + [1] * (3 - len(first))))
    except ValueError:
        return None
    return first


def publication_date(item: dict) -> date | None:
    """Earliest known publication day, giving online/print dates priority.

    `created`, `deposited`, and `indexed` describe registry activity and are
    deliberately never used. Incomplete earlier publication dates are excluded
    because assigning the first day of a month would fabricate a publication day.
    """
    explicit = [_date_parts(item.get(key)) for key in ("published-online", "published-print")]
    known = [parts for parts in explicit if parts]
    if not known:
        # The generic fields are fallbacks, often duplicating an issue date.
        known = [parts for key in ("published", "issued") if (parts := _date_parts(item.get(key)))]
    if not known:
        return None
    exact = [date(*parts) for parts in known if len(parts) == 3]
    if not exact:
        return None
    earliest = min(exact)
    for parts in known:
        if len(parts) < 3:
            # An imprecise date can only be disregarded when it is certainly
            # later than an exact date (for example, a future print month).
            lower_bound = date(*(parts + [1] * (3 - len(parts))))
            if lower_bound <= earliest:
                return None
    return earliest


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _sleep(config: dict) -> None:
    # The HTTP client owns retries/Retry-After; requests here are sequential.
    delay = float(config.get("request_interval_seconds", 0.15))
    if delay > 0:
        time.sleep(delay)


def _status(name: str, papers: list[Paper], read: int, skipped: Counter, error: str = "") -> dict:
    notes = [f"Read {read} records; retained {len(papers)} in the publication window."]
    if skipped:
        notes.append("Excluded: " + "; ".join(f"{count} {reason}" for reason, count in sorted(skipped.items())) + ".")
    if error:
        notes.append(error)
    incomplete = bool(skipped["missing or imprecise publication date"] or skipped["missing title or DOI"])
    return {
        "name": name,
        "status": "partial" if (error and read) or incomplete else "error" if error else "ok",
        "fetched": len(papers),
        "message": " ".join(notes),
    }


def _crossref_paper(item: dict, name: str, start: date, end: date, skipped: Counter) -> Paper | None:
    published = publication_date(item)
    if published is None:
        skipped["missing or imprecise publication date"] += 1
        return None
    if not start <= published < end:
        skipped["outside first-publication window"] += 1
        return None
    titles = item.get("title", [])
    title = plain_text(titles[0]) if isinstance(titles, list) and titles else ""
    doi = normalize_doi(item.get("DOI", ""))
    if not title or not doi:
        skipped["missing title or DOI"] += 1
        return None
    authors = []
    for author in item.get("author", []) or []:
        if isinstance(author, dict):
            full_name = plain_text(author.get("name")) or " ".join(
                filter(None, (plain_text(author.get("given")), plain_text(author.get("family"))))
            )
            if full_name:
                authors.append(full_name)
    containers = item.get("container-title", [])
    journal = plain_text(containers[0]) if isinstance(containers, list) and containers else name
    return Paper(
        title=title,
        authors=authors,
        url=f"https://doi.org/{doi}",
        abstract=plain_text(item.get("abstract", "")),
        journal=journal,
        published=published.isoformat(),
        doi=doi,
        source="Crossref",
        is_preprint=False,
    )


def _fetch_crossref(spec: dict, start: date, end: date, client: HttpClient, config: dict) -> tuple[list[Paper], dict]:
    name = str(spec.get("name") or spec.get("issn") or spec.get("prefix") or "Crossref")
    papers: list[Paper] = []
    skipped: Counter = Counter()
    read = 0
    if bool(spec.get("issn")) == bool(spec.get("prefix")):
        return papers, _status(name, papers, read, skipped, "Configure exactly one ISSN or DOI prefix.")
    endpoint = "journals" if spec.get("issn") else "prefixes"
    identifier = quote(str(spec.get("issn") or spec["prefix"]), safe="")
    url = f"https://api.crossref.org/{endpoint}/{identifier}/works"
    params: dict[str, Any] = {
        "filter": f"type:journal-article,from-pub-date:{start.isoformat()},until-pub-date:{(end - timedelta(days=1)).isoformat()}",
        "rows": _CROSSREF_PAGE_SIZE,
        "cursor": "*",
        # References and other unused metadata can dwarf the actual abstracts.
        "select": "DOI,title,author,abstract,container-title,published-online,published-print,published,issued",
    }
    email = config.get("contact_email", "")
    if email:
        params["mailto"] = email
    seen_pages: set[tuple] = set()
    try:
        while True:
            _sleep(config)
            payload = client.get_json(url, params=params.copy())
            message = payload.get("message")
            if not isinstance(message, dict) or not isinstance(message.get("items"), list):
                raise ValueError("Crossref returned an invalid works response.")
            items = message["items"]
            fingerprint = tuple((item.get("DOI"), str(item.get("title")), str(item.get("published"))) for item in items if isinstance(item, dict))
            if items and fingerprint in seen_pages:
                raise ValueError("Crossref repeated a page; retrieval stopped before completion.")
            seen_pages.add(fingerprint)
            for item in items:
                read += 1
                if not isinstance(item, dict):
                    skipped["missing title or DOI"] += 1
                    continue
                paper = _crossref_paper(item, name, start, end, skipped)
                if paper:
                    papers.append(paper)
            # Crossref documents a short page as the end of a cursor sequence.
            if len(items) < _CROSSREF_PAGE_SIZE:
                break
            cursor = message.get("next-cursor")
            if not cursor:
                total = _int(message.get("total-results"))
                if total is not None and read >= total:
                    break
                raise ValueError("Crossref omitted its next cursor before completion.")
            # Some scroll backends reuse a cursor while advancing server state;
            # page content, rather than cursor equality, detects a stuck loop.
            params["cursor"] = cursor
    except (HttpError, ValueError, TypeError, KeyError) as exc:
        return papers, _status(name, papers, read, skipped, str(exc))
    return papers, _status(name, papers, read, skipped)


def _fetch_biorxiv(start: date, end: date, client: HttpClient, config: dict) -> tuple[list[Paper], dict]:
    papers: list[Paper] = []
    skipped: Counter = Counter()
    read = 0
    cursor = 0
    seen_pages: set[tuple] = set()
    categories = {str(value).strip().casefold() for value in config.get("biorxiv_categories") or []}
    try:
        while True:
            _sleep(config)
            url = f"https://api.biorxiv.org/details/biorxiv/{start.isoformat()}/{(end - timedelta(days=1)).isoformat()}/{cursor}"
            payload = client.get_json(url)
            messages = payload.get("messages", [])
            metadata = messages[0] if isinstance(messages, list) and messages and isinstance(messages[0], dict) else {}
            items = payload.get("collection")
            if not isinstance(items, list):
                raise ValueError("bioRxiv returned an invalid collection.")
            api_status = str(metadata.get("status", "ok")).lower()
            empty_success = not items and "no papers found" in api_status and _int(metadata.get("total")) in {None, 0}
            if api_status not in {"ok", "success"} and not empty_success:
                raise ValueError(f"bioRxiv API status: {metadata.get('status')}")
            fingerprint = tuple((item.get("doi"), str(item.get("version")), item.get("date")) for item in items if isinstance(item, dict))
            if items and fingerprint in seen_pages:
                raise ValueError("bioRxiv repeated a page; retrieval stopped before completion.")
            seen_pages.add(fingerprint)
            for item in items:
                read += 1
                if not isinstance(item, dict):
                    skipped["missing title or DOI"] += 1
                    continue
                # Updated old preprints are not new publications this week.
                if categories and str(item.get("category") or "").strip().casefold() not in categories:
                    skipped["outside configured bioRxiv categories"] += 1
                    continue
                if _int(item.get("version")) != 1:
                    skipped["preprint revisions or unknown version"] += 1
                    continue
                published = _iso_day(item.get("date"))
                if published is None:
                    skipped["missing or imprecise publication date"] += 1
                    continue
                if not start <= published < end:
                    skipped["outside first-publication window"] += 1
                    continue
                title = plain_text(item.get("title"))
                doi = normalize_doi(item.get("doi"))
                if not title or not doi:
                    skipped["missing title or DOI"] += 1
                    continue
                papers.append(Paper(
                    title=title,
                    authors=[author.strip() for author in str(item.get("authors") or "").split(";") if author.strip()],
                    url=f"https://doi.org/{doi}",
                    abstract=plain_text(item.get("abstract")),
                    journal="bioRxiv",
                    published=published.isoformat(),
                    doi=doi,
                    source="bioRxiv",
                    is_preprint=True,
                ))
            cursor += len(items)
            total = _int(metadata.get("total"))
            if total is not None and cursor >= total:
                break
            if not items:
                if total is not None and cursor < total:
                    raise ValueError(f"bioRxiv ended at {cursor} of {total} reported records.")
                break
            if total is None and len(items) < _BIORXIV_PAGE_SIZE:
                break
    except (HttpError, ValueError, TypeError, KeyError) as exc:
        return papers, _status("bioRxiv", papers, read, skipped, str(exc))
    return papers, _status("bioRxiv", papers, read, skipped)


def deduplicate(papers: list[Paper]) -> list[Paper]:
    """Merge repeated DOI records, preferring journal metadata over preprints."""
    by_key: dict[str, Paper] = {}
    for paper in papers:
        key = normalize_doi(paper.doi) or paper.url.lower().rstrip("/")
        previous = by_key.get(key)
        if previous is None:
            by_key[key] = paper
            continue
        preferred, fallback = (paper, previous) if previous.is_preprint and not paper.is_preprint else (previous, paper)
        # Never transfer a preprint abstract onto a published article: versions
        # can differ. Metadata duplicate records of the same type may be merged.
        same_type = preferred.is_preprint == fallback.is_preprint
        by_key[key] = replace(
            preferred,
            abstract=preferred.abstract or (fallback.abstract if same_type else ""),
            authors=preferred.authors or fallback.authors,
        )
    return list(by_key.values())


def _enrich_abstracts(papers: list[Paper], client: HttpClient, config: dict) -> tuple[list[Paper], dict]:
    missing = {normalize_doi(paper.doi) for paper in papers if not paper.abstract and paper.doi}
    enriched: dict[str, str] = {}
    failures: list[str] = []
    missing_dois = sorted(missing)
    for offset in range(0, len(missing_dois), _EUROPE_PMC_BATCH_SIZE):
        batch = missing_dois[offset:offset + _EUROPE_PMC_BATCH_SIZE]
        # Escape query syntax even though valid publisher DOIs rarely need it.
        query = " OR ".join('DOI:"' + doi.replace("\\", "\\\\").replace('"', '\\"') + '"' for doi in batch)
        params: dict[str, Any] = {
            "query": f"({query})",
            "format": "json",
            "resultType": "core",
            "pageSize": _EUROPE_PMC_PAGE_SIZE,
            "cursorMark": "*",
        }
        if config.get("contact_email"):
            params["email"] = config["contact_email"]
        seen_pages: set[tuple] = set()
        batch_read = 0
        try:
            while True:
                _sleep(config)
                payload = client.get_json("https://www.ebi.ac.uk/europepmc/webservices/rest/search", params=params.copy())
                results = payload.get("resultList", {}).get("result")
                if not isinstance(results, list):
                    raise ValueError("Europe PMC returned an invalid search response.")
                fingerprint = tuple((item.get("source"), item.get("id"), item.get("doi")) for item in results if isinstance(item, dict))
                if results and fingerprint in seen_pages:
                    raise ValueError("Europe PMC repeated a page before completion.")
                seen_pages.add(fingerprint)
                batch_read += len(results)
                for result in results:
                    if not isinstance(result, dict):
                        continue
                    doi = normalize_doi(result.get("doi"))
                    abstract = plain_text(result.get("abstractText"))
                    if doi in missing and abstract:
                        enriched[doi] = abstract
                next_cursor = payload.get("nextCursorMark")
                if len(results) < _EUROPE_PMC_PAGE_SIZE:
                    break
                if not next_cursor or next_cursor == params["cursorMark"]:
                    total = _int(payload.get("hitCount"))
                    if total is not None and batch_read >= total:
                        break
                    raise ValueError("Europe PMC omitted a usable cursor before completion.")
                params["cursorMark"] = next_cursor
        except (HttpError, ValueError, TypeError, KeyError, AttributeError) as exc:
            failures.append(f"DOI batch {offset // _EUROPE_PMC_BATCH_SIZE + 1}: {exc}")
    output = [replace(paper, abstract=enriched.get(normalize_doi(paper.doi), paper.abstract)) for paper in papers]
    remaining = sum(not paper.abstract for paper in output)
    message = f"Recovered {len(enriched)} abstracts from {len(missing_dois)} DOI lookups; {remaining} papers still lack an abstract."
    if failures:
        message += " " + " ".join(failures)
    return output, {
        "name": "Europe PMC abstract enrichment",
        "status": "partial" if failures or remaining else "ok",
        "fetched": len(enriched),
        "message": message,
    }


def fetch_all(config: dict, start: date, end: date, client: HttpClient) -> tuple[list[Paper], list[dict]]:
    """Fetch configured sources in the inclusive-start/exclusive-end date range.

    `config['sources']` contains `biorxiv`, a `crossref` list of `name` plus
    `issn`/`prefix`, `enrich_abstracts`, `contact_email`, and an optional
    `request_interval_seconds`. No record cap is applied.
    """
    if start >= end:
        raise ValueError("The publication window must have start before end.")
    sources = config.get("sources", {})
    # Nested source settings are canonical; permit top-level settings for callers
    # using earlier standalone examples without overriding nested preferences.
    settings = {**config, **sources}
    papers: list[Paper] = []
    statuses: list[dict] = []
    if sources.get("biorxiv", True):
        found, status = _fetch_biorxiv(start, end, client, settings)
        papers.extend(found)
        statuses.append(status)
    for spec in sources.get("crossref", []):
        found, status = _fetch_crossref(spec, start, end, client, settings)
        papers.extend(found)
        statuses.append(status)
    papers = deduplicate(papers)
    missing = sum(not paper.abstract for paper in papers)
    if sources.get("enrich_abstracts", True) and missing:
        papers, status = _enrich_abstracts(papers, client, settings)
        statuses.append(status)
    elif missing:
        statuses.append({"name": "Abstract availability", "status": "partial", "fetched": 0, "message": f"{missing} papers lack abstracts; Europe PMC enrichment is disabled."})
    return sorted(papers, key=lambda paper: (paper.published, paper.doi), reverse=True), statuses
