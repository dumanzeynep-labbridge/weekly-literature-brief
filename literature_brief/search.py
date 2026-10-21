"""Annotate discovered papers with explicit, reproducible focused searches.

Search blocks use OR between their terms; combinations require every selected
block. These annotations never exclude papers from discovery or assessment.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy
from functools import lru_cache
import re
import unicodedata


_ID = re.compile(r"[a-z][a-z0-9_]*\Z")
_GREEK = dict(zip(
    "αβγδεζηθικλμνξοπρστυφχψω",
    ("alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta",
     "iota", "kappa", "lambda", "mu", "nu", "xi", "omicron", "pi", "rho",
     "sigma", "tau", "upsilon", "phi", "chi", "psi", "omega"),
))


def _normalize(value: str) -> str:
    # Compatibility normalization handles superscript/subscript digits and
    # charges; spelling out Greek letters also accepts plain ASCII aliases.
    value = unicodedata.normalize("NFKC", value).casefold()
    value = "".join(" " + _GREEK[char] + " " if char in _GREEK else char for char in value)
    return re.sub(r"[\W_]+", " ", value, flags=re.UNICODE).strip()


@lru_cache(maxsize=2048)
def _pattern(term: str) -> re.Pattern:
    normalized = _normalize(term)
    pieces = []
    for index, char in enumerate(normalized):
        if char == " ":
            before, after = normalized[index - 1], normalized[index + 1]
            pieces.append(r"\s*" if before.isdigit() != after.isdigit() else r"\s+")
        else:
            if index and normalized[index - 1] != " " and normalized[index - 1].isdigit() != char.isdigit():
                pieces.append(r"\s*")
            pieces.append(re.escape(char))
    # Boundaries stay outside the full symbol: NRF1 cannot match NRF10 or NRF1A,
    # qP cannot match qPCR, and GEM cannot match a longer word such as gemcitabine.
    return re.compile(r"(?<!\w)" + "".join(pieces) + r"(?!\w)")


def validate_search_config(search_config: dict | None) -> None:
    """Validate optional search settings without changing the supplied object."""
    if search_config is None:
        return
    if not isinstance(search_config, dict):
        raise ValueError("search must be an object.")
    blocks = search_config.get("blocks")
    combinations = search_config.get("combinations")
    if not isinstance(blocks, list) or not blocks:
        raise ValueError("search.blocks must be a nonempty array.")
    if not isinstance(combinations, list) or not combinations:
        raise ValueError("search.combinations must be a nonempty array.")

    def identity(item, kind):
        if not isinstance(item, dict):
            raise ValueError(f"Each search {kind} must be an object.")
        identifier, label = item.get("id"), item.get("label")
        if not isinstance(identifier, str) or not _ID.fullmatch(identifier):
            raise ValueError(f"Each search {kind} needs an id using lowercase letters, numbers, and underscores.")
        if not isinstance(label, str) or not label.strip():
            raise ValueError(f"Each search {kind} needs a nonempty label.")
        return identifier

    block_ids = set()
    for block in blocks:
        identifier = identity(block, "block")
        if identifier in block_ids:
            raise ValueError("Search block ids must be unique.")
        block_ids.add(identifier)
        terms = block.get("terms")
        if not isinstance(terms, list) or not terms or any(
            not isinstance(term, str) or not _normalize(term) for term in terms
        ):
            raise ValueError("Each search block needs a nonempty array of text terms.")

    combination_ids = set()
    for combination in combinations:
        identifier = identity(combination, "combination")
        if identifier in combination_ids:
            raise ValueError("Search combination ids must be unique.")
        combination_ids.add(identifier)
        selected = combination.get("blocks")
        if not isinstance(selected, list) or len(selected) < 2:
            raise ValueError("Each focused search combination must require at least two blocks.")
        if any(not isinstance(reference, str) or reference not in block_ids for reference in selected):
            raise ValueError("Search combinations must reference known block ids.")
        if len(set(selected)) != len(selected):
            raise ValueError("A search combination cannot reference the same block twice.")


def evaluate_paper(title: str, abstract: str, search_config: dict | None) -> dict:
    """Return block evidence and AND-combination matches for a title/abstract."""
    result = {"matched_blocks": {}, "matched_combinations": [], "ambiguity_notes": []}
    if not search_config:
        return result
    # Keep the fields separate so a phrase cannot be fabricated by joining the
    # final word of a title to the first word of its abstract.
    texts = tuple(_normalize(value or "") for value in (title, abstract))
    for block in search_config["blocks"]:
        terms = [term for term in block["terms"] if any(_pattern(term).search(text) for text in texts)]
        if terms:
            result["matched_blocks"][block["id"]] = terms
    for combination in search_config["combinations"]:
        if all(block in result["matched_blocks"] for block in combination["blocks"]):
            result["matched_combinations"].append(combination["id"])

    matched_terms = {
        re.sub(r"\s+", "", _normalize(term))
        for terms in result["matched_blocks"].values() for term in terms
    }
    combined = " ".join(texts)
    respiratory_context = bool(re.search(
        r"\b(?:mitochondri\w*|respirat\w*|oxphos|mtdna)\b", combined
    )) or any(_pattern(term).search(combined) for term in ("oxidative phosphorylation", "electron transport chain"))
    if "nrf1" in matched_terms:
        if _pattern("NFE2L1").search(combined):
            result["ambiguity_notes"].append(
                "NRF1 is also an alias for NFE2L1, which is mentioned here; confirm whether this paper concerns nuclear respiratory factor 1."
            )
        elif not respiratory_context:
            result["ambiguity_notes"].append(
                "NRF1 is ambiguous between nuclear respiratory factor 1 and the NFE2L1 alias; no mitochondrial or respiratory context was found."
            )
    if "prc" in matched_terms and not respiratory_context:
        result["ambiguity_notes"].append(
            "PRC is an ambiguous abbreviation; no mitochondrial or respiratory context was found, so confirm the intended coactivator."
        )
    return result


def summarize_searches(papers: Iterable[Mapping], search_config: dict | None) -> dict:
    """Count focused matches while retaining complete search definitions."""
    if not search_config:
        return {"blocks": [], "combinations": [], "matched_papers": 0}
    combinations = [dict(deepcopy(combination), match_count=0) for combination in search_config["combinations"]]
    by_id = {combination["id"]: combination for combination in combinations}
    matched_papers = 0
    for paper in papers:
        evidence = paper.get("focused_search")
        if not isinstance(evidence, dict):
            evidence = evaluate_paper(paper.get("title", ""), paper.get("abstract", ""), search_config)
        matches = set(evidence.get("matched_combinations", [])) & by_id.keys()
        matched_papers += bool(matches)
        for identifier in matches:
            by_id[identifier]["match_count"] += 1
    return {"blocks": deepcopy(search_config["blocks"]), "combinations": combinations,
            "matched_papers": matched_papers}
