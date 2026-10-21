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


def _normalize(value: str) -> str:…1655 tokens truncated…aper.get("focused_search")
        if not isinstance(evidence, dict):
            evidence = evaluate_paper(paper.get("title", ""), paper.get("abstract", ""), search_config)
        matches = set(evidence.get("matched_combinations", [])) & by_id.keys()
        matched_papers += bool(matches)
        for identifier in matches:
            by_id[identifier]["match_count"] += 1
    return {"blocks": deepcopy(search_config["blocks"]), "combinations": combinations,
            "matched_papers": matched_papers}
