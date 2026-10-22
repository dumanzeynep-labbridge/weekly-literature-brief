"""Free, explainable personal ranking: no LLM or paid API is involved.

Every paper receives a points score from four transparent signals:

* weighted keyword blocks (strong terms count more in the title; terms listed
  in ``weak_terms`` count only a little, because they are ambiguous alone),
* complete focused combinations and named exclusion terms,
* watched authors, and
* the configured seed papers: new papers that cite them (OpenAlex) and text
  similarity to their titles and abstracts (TF-IDF, computed locally).

Each point source is recorded as a human-readable reason on the paper.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, timedelta
import logging
import math
import os
import re
from typing import Any, Iterable
import unicodedata

from .http import HttpClient, HttpError
from .models import Paper
from .search import _normalize, _pattern
from .sources import normalize_doi

LOGGER = logging.getLogger(__name__)
_OPENALEX = "https://api.openalex.org"
_DOI = re.compile(r"10\.\d{4,9}/\S+\Z")
_STOPWORDS = frozenset("""
a about above after again against all also an and any are as at be been before being between both
but by can could did do does during each few for from further had has have having here how however
i if in into is it its itself just may more most no nor not of on once only or other our out over
own same she should so some such than that the their them then there these they this those through
to too under until up very was we were what when where which while who whom why will with within
without would you your using used use based study studies show shows shown here we our results
result found find findings data analysis analyses method methods novel new role roles via
""".split())

DEFAULTS = {
    "top_n": 10,
    "min_score": 4,
    "ai_shortlist": 30,
    "ai_min_score": 1,
    "title_multiplier": 1.5,
    "weak_term_points": 0.3,
    "combination_points": 3,
    "watch_author_points": 6,
    "cites_seed_points": 8,
    "similarity_points": 12,
    "similarity_floor": 0.12,
    "exclude_penalty": 6,
}


def validate_personal_config(personal: dict | None) -> None:
    if personal is None:
        return
    if not isinstance(personal, dict):
        raise ValueError("personal must be an object.")
    for key, default in DEFAULTS.items():
        value = personal.setdefault(key, default)
        if type(value) not in {int, float} or value < 0:
            raise ValueError(f"personal.{key} must be a nonnegative number.")
    for key in ("top_n", "ai_shortlist"):
        if type(personal[key]) is not int:
            raise ValueError(f"personal.{key} must be an integer.")
    weights = personal.setdefault("block_weights", {})
    if not isinstance(weights, dict) or any(
        not isinstance(name, str) or type(value) not in {int, float} or value < 0
        for name, value in weights.items()
    ):
        raise ValueError("personal.block_weights must map block ids to nonnegative numbers.")
    for key in ("weak_terms", "exclude_terms", "watch_authors"):
        values = personal.setdefault(key, [])
        if not isinstance(values, list) or any(not isinstance(value, str) or not _normalize(value) for value in values):
            raise ValueError(f"personal.{key} must be an array of nonempty text.")
    seeds = personal.setdefault("seed_papers", [])
    if not isinstance(seeds, list):
        raise ValueError("personal.seed_papers must be an array.")
    for seed in seeds:
        doi = normalize_doi(seed.get("doi") if isinstance(seed, dict) else seed)
        if not _DOI.fullmatch(doi):
            raise ValueError("Each personal.seed_papers entry needs a DOI such as 10.1038/s41467-018-06003-9.")
    if type(personal.setdefault("citation_alerts", True)) is not bool:
        raise ValueError("personal.citation_alerts must be true or false.")


def seed_dois(personal: dict) -> list[str]:
    return [normalize_doi(seed.get("doi") if isinstance(seed, dict) else seed) for seed in personal.get("seed_papers", [])]


# --- OpenAlex: seed metadata and new citing papers ------------------------------------------

def _openalex_params(config: dict) -> dict:
    params = {}
    key = os.environ.get("OPENALEX_API_KEY", "").strip()
    if key:
        params["api_key"] = key
    email = config.get("sources", {}).get("contact_email", "")
    if email:
        params["mailto"] = email
    return params


def _inverted_abstract(index: Any) -> str:
    if not isinstance(index, dict):
        return ""
    positions = []
    for word, places in index.items():
        if isinstance(word, str) and isinstance(places, list):
            positions.extend((place, word) for place in places if type(place) is int)
    return " ".join(word for _, word in sorted(positions))


def _openalex_paper(work: dict) -> Paper | None:
    doi = normalize_doi(work.get("doi"))
    title = str(work.get("display_name") or work.get("title") or "").strip()
    published = str(work.get("publication_date") or "")
    if not doi or not title or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", published):
        return None
    authors = []
    for authorship in work.get("authorships") or []:
        author = authorship.get("author") if isinstance(authorship, dict) else None
        if isinstance(author, dict) and author.get("display_name"):
            authors.append(str(author["display_name"]))
    location = work.get("primary_location") if isinstance(work.get("primary_location"), dict) else {}
    source = location.get("source") if isinstance(location.get("source"), dict) else {}
    return Paper(
        title=title, authors=authors, url=f"https://doi.org/{doi}",
        abstract=_inverted_abstract(work.get("abstract_inverted_index")),
        journal=str(source.get("display_name") or "Journal not recorded"),
        published=published, doi=doi, source="OpenAlex citation alert",
        is_preprint=work.get("type") == "preprint",
    )


def fetch_seed_context(config: dict, start: date, end: date, client: HttpClient) -> tuple[list[dict], list[Paper], dict, list[dict]]:
    """Return seed records, papers citing a seed in the window, citing map, and statuses."""
    personal = config.get("personal") or {}
    dois = seed_dois(personal)
    if not dois:
        return [], [], {}, []
    params = _openalex_params(config)
    seeds, failures = [], []
    for doi in dois:
        try:
            work = client.get_json(f"{_OPENALEX}/works/doi:{doi}", params=dict(
                params, select="id,doi,display_name,abstract_inverted_index"))
        except HttpError as exc:
            failures.append(f"{doi}: {exc}")
            continue
        seeds.append({"id": str(work.get("id") or "").rsplit("/", 1)[-1], "doi": doi,
                      "title": str(work.get("display_name") or doi),
                      "abstract": _inverted_abstract(work.get("abstract_inverted_index"))})
    message = f"Resolved {len(seeds)} of {len(dois)} seed papers."
    statuses = [{"name": "OpenAlex seed papers", "status": "partial" if failures else "ok",
                 "fetched": len(seeds), "message": " ".join([message] + failures)}]
    citing: dict[str, list[str]] = {}
    papers: list[Paper] = []
    ids = [seed["id"] for seed in seeds if seed["id"].startswith("W")]
    if not personal.get("citation_alerts", True) or not ids:
        return seeds, papers, citing, statuses
    titles = {seed["id"]: seed["title"] for seed in seeds}
    query = dict(params, cursor="*", **{"per-page": 200}, filter=(
        f"cites:{'|'.join(ids)},from_publication_date:{start.isoformat()},"
        f"to_publication_date:{(end - timedelta(days=1)).isoformat()}"),
        select="id,doi,display_name,publication_date,authorships,primary_location,type,"
               "abstract_inverted_index,referenced_works")
    error = ""
    try:
        for _ in range(20):
            payload = client.get_json(f"{_OPENALEX}/works", params=dict(query))
            results = payload.get("results")
            if not isinstance(results, list):
                raise ValueError("OpenAlex returned an invalid works response.")
            for work in results:
                paper = _openalex_paper(work) if isinstance(work, dict) else None
                if paper is None:
                    continue
                references = {str(ref).rsplit("/", 1)[-1] for ref in work.get("referenced_works") or []}
                citing[paper.doi] = [titles[seed] for seed in ids if seed in references]
                papers.append(paper)
            cursor = (payload.get("meta") or {}).get("next_cursor")
            if not cursor or not results:
                break
            query["cursor"] = cursor
    except (HttpError, ValueError, TypeError, AttributeError) as exc:
        error = str(exc)
    statuses.append({"name": "OpenAlex citation alerts", "status": "partial" if error else "ok",
                     "fetched": len(papers),
                     "message": f"{len(papers)} paper(s) in the window cite a seed paper. {error}".strip()})
    return seeds, papers, citing, statuses


# --- Local text similarity -----------------------------------------------------------------

def _tokens(text: str) -> list[str]:
    return [token for token in _normalize(text).split() if len(token) > 2 and token not in _STOPWORDS and not token.isdigit()]


class _Similarity:
    """TF-IDF cosine similarity between this week's papers and the seed papers."""

    def __init__(self, documents: Iterable[str], seeds: list[dict]):
        self.seeds = [seed for seed in seeds if seed.get("title") or seed.get("abstract")]
        counts = Counter()
        total = 0
        for text in list(documents) + [f'{seed["title"]} {seed["abstract"]}' for seed in self.seeds]:
            counts.update(set(_tokens(text)))
            total += 1
        self.idf = {token: math.log((1 + total) / (1 + count)) + 1 for token, count in counts.items()}
        self.vectors = [self.vector(f'{seed["title"]} {seed["abstract"]}') for seed in self.seeds]

    def vector(self, text: str) -> dict[str, float]:
        frequencies = Counter(_tokens(text))
        vector = {token: (1 + math.log(count)) * self.idf.get(token, 1.0) for token, count in frequencies.items()}
        norm = math.sqrt(sum(value * value for value in vector.values())) or 1.0
        return {token: value / norm for token, value in vector.items()}

    def best(self, text: str) -> tuple[float, str]:
        if not self.vectors:
            return 0.0, ""
        vector = self.vector(text)
        scores = [sum(weight * seed.get(token, 0.0) for token, weight in vector.items()) for seed in self.vectors]
        index = max(range(len(scores)), key=scores.__getitem__)
        return scores[index], self.seeds[index]["title"]


# --- Scoring -------------------------------------------------------------------------------

def _plain(value: str) -> str:
    """Casefold and strip accents so Véronique matches Veronique."""
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(char for char in decomposed if not unicodedata.combining(char)).casefold()


def _person(name: str) -> tuple[str, str, str, str]:
    """Return (family, first full given name, joined given names, initials).

    Accepts 'Given Middle Family' (Crossref) and 'Family, G. M.' (bioRxiv).
    Hyphenated given names stay whole for the full-name check, so Wei Hu is
    not Wei-Shou Hu, but contribute one initial per part (W.-S. -> ws).
    """
    name = _plain(name.strip())
    if "," in name:
        family, given = name.split(",", 1)
    else:
        parts = name.split()
        family, given = (parts[-1], " ".join(parts[:-1])) if parts else ("", "")
    family = re.sub(r"[^\w]", "", family)
    words = [re.sub(r"[^\w]", "", word) for word in re.split(r"[\s.]+", given)]
    words = [word for word in words if word]
    full = next((word for word in words if len(word) > 1), "")
    initials = "".join(part[0] for part in re.split(r"[\s.\-‐]+", given) if part and part[0].isalnum())
    return family, full, "".join(words), initials


def _author_match(author: str, watch: tuple[str, str, str, str]) -> str | None:
    """'full' for a confident match, 'initial' for a single-initial match, else None."""
    family, full, joined, initials = _person(author)
    watch_family, watch_full, watch_joined, watch_initials = watch
    if not family or family != watch_family or not initials:
        return None
    if full:
        return "full" if full == watch_full or joined == watch_joined else None
    # Initials only: an extra middle initial is fine; a missing one is not ("Lee, J." is not Jae Seong Lee).
    if initials.startswith(watch_initials):
        shared = len(watch_initials)
    elif len(initials) >= 2 and watch_initials.startswith(initials):
        shared = len(initials)
    else:
        return None
    return "full" if shared >= 2 else "initial"


def _matches_author(authors: list[str], watched: list[tuple]) -> tuple[list[str], bool]:
    """Matched author names, and whether any match was confident."""
    found, confident = [], False
    for author in authors:
        kinds = {_author_match(author, watch) for watch in watched} - {None}
        if kinds:
            found.append(author if "full" in kinds else f"{author} (initial-only match)")
            confident |= "full" in kinds
    return found, confident


def _shorten(text: str, limit: int = 80) -> str:
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def rank_records(records: list[dict], config: dict, seeds: list[dict], citing: dict) -> None:
    """Add ``personal_score`` and ``personal_reasons`` to every record in place."""
    personal = config.get("personal")
    if not personal:
        return
    search = config.get("search") or {}
    labels = {block["id"]: block["label"] for block in search.get("blocks", [])}
    combination_labels = {combo["id"]: combo["label"] for combo in search.get("combinations", [])}
    weights = personal.get("block_weights", {})
    weak = {_normalize(term) for term in personal.get("weak_terms", [])}
    excludes = personal.get("exclude_terms", [])
    watched = [_person(name) for name in personal.get("watch_authors", [])]
    similarity = _Similarity((f'{record.get("title", "")} {record.get("abstract", "")}' for record in records), seeds)
    for record in records:
        score, reasons = 0.0, []
        title = _normalize(record.get("title") or "")
        evidence = record.get("focused_search") or {}
        for block, terms in (evidence.get("matched_blocks") or {}).items():
            weight = float(weights.get(block, 1))
            strong = [term for term in terms if _normalize(term) not in weak]
            label = labels.get(block, block)
            if strong:
                in_title = any(_pattern(term).search(title) for term in strong)
                points = weight * (personal["title_multiplier"] if in_title else 1.0)
                reasons.append(f'{label}{" (in title)" if in_title else ""}: {", ".join(strong[:4])}')
            else:
                points = weight * personal["weak_term_points"]
                reasons.append(f'{label} (ambiguous terms only): {", ".join(terms[:3])}')
            score += points
        combinations = evidence.get("matched_combinations") or []
        if combinations:
            score += personal["combination_points"] * min(len(combinations), 2)
            reasons.append("Combination: " + "; ".join(combination_labels.get(c, c) for c in combinations[:2]))
        authors, confident = _matches_author(record.get("authors") or [], watched) if watched else ([], False)
        if authors:
            # A lone shared initial (e.g. "Young, J.") is weak evidence: half points.
            score += personal["watch_author_points"] * (1 if confident else 0.5)
            reasons.append("Watched author: " + ", ".join(authors[:3]))
        cited = citing.get(normalize_doi(record.get("doi")))
        if cited:
            score += personal["cites_seed_points"]
            reasons.append("Cites your seed paper: " + "; ".join(_shorten(title) for title in cited[:2]))
        text = f'{record.get("title", "")} {record.get("abstract", "")}'
        value, seed_title = similarity.best(text)
        if value >= personal["similarity_floor"]:
            score += personal["similarity_points"] * value
            reasons.append(f"Similar to seed paper ({value:.2f}): {_shorten(seed_title)}")
        normalized = (title, _normalize(record.get("abstract") or ""))
        excluded = [term for term in excludes if any(_pattern(term).search(part) for part in normalized)]
        if excluded:
            score -= personal["exclude_penalty"]
            reasons.append("Penalised for excluded term: " + ", ".join(excluded[:3]))
        record["personal_score"] = round(max(score, 0.0), 1)
        record["personal_reasons"] = reasons


def personal_order(records: list[dict]) -> list[dict]:
    """Highest personal score first; newest first on ties."""
    return sorted(records, key=lambda record: (record.get("personal_score") or 0, str(record.get("published") or "")),
                  reverse=True)
