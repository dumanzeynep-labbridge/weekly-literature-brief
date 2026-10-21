"""Load a portable configuration and apply explicit environment overrides."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

DEFAULT_MODELS = {"openai": "gpt-4.1-mini", "anthropic": "claude-haiku-4-5"}


def load_config(path: Path) -> dict:
    config = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError("Configuration must be a JSON object.")
    llm = config.setdefault("llm", {})
    sources = config.setdefault("sources", {})
    if not isinstance(llm, dict) or not isinstance(sources, dict):
        raise ValueError("llm and sources must be objects.")
    original_provider = llm.get("provider", "openai")
    if isinstance(original_provider, str):
        original_provider = original_provider.lower()
    provider = os.environ.get("LLM_PROVIDER", "").strip() or original_provider
    if not isinstance(provider, str) or provider.lower() not in DEFAULT_MODELS:
        raise ValueError("LLM_PROVIDER / llm.provider must be openai or anthropic.")
    provider = provider.lower()
    llm["provider"] = provider
    override_model = os.environ.get("LLM_MODEL", "").strip()
    if override_model:
        llm["model"] = override_model
    elif provider != original_provider or not llm.get("model"):
        llm["model"] = DEFAULT_MODELS[provider]
    if not isinstance(llm.get("model"), str) or not llm["model"].strip():
        raise ValueError("llm.model must be a model identifier.")
    if os.environ.get("CONTACT_EMAIL", "").strip():
        sources["contact_email"] = os.environ["CONTACT_EMAIL"].strip()
    if not isinstance(sources.get("contact_email", ""), str):
        raise ValueError("sources.contact_email must be a string.")
    interval = sources.setdefault("request_interval_seconds", 0.15)
    if type(interval) not in {int, float} or not 0 <= interval <= 60:
        raise ValueError("sources.request_interval_seconds must be a number between 0 and 60.")
    interests = config.get("interests")
    if not isinstance(interests, list) or not interests or any(
        not isinstance(value, str) or not value.strip() for value in interests
    ):
        raise ValueError("interests must contain at least one nonempty topic.")
    for key, default, low, high in (
        ("min_relevance", 6, 1, 10), ("top_n", 20, 0, 10000),
        ("max_papers", 0, 0, 1000000),
    ):
        value = config.setdefault(key, default)
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f"{key} must be an integer between {low} and {high}.")
    for key, default, low, high in (
        ("max_output_tokens", 1000, 256, 16000), ("workers", 3, 1, 10),
    ):
        value = llm.setdefault(key, default)
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f"llm.{key} must be an integer between {low} and {high}.")
    for key in ("biorxiv", "enrich_abstracts"):
        if type(sources.setdefault(key, True)) is not bool:
            raise ValueError(f"sources.{key} must be true or false.")
    entries = sources.setdefault("crossref", [])
    if not isinstance(entries, list):
        raise ValueError("sources.crossref must be an array.")
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
            raise ValueError("Each Crossref source needs a name.")
        has_issn, has_prefix = bool(entry.get("issn")), bool(entry.get("prefix"))
        if has_issn == has_prefix:
            raise ValueError("Each Crossref source needs exactly one issn or prefix.")
        if has_issn and not re.fullmatch(r"\d{4}-\d{3}[\dX]", str(entry["issn"])):
            raise ValueError("ISSNs must use the form 1234-567X.")
        if has_prefix and not re.fullmatch(r"10\.\d{4,9}", str(entry["prefix"])):
            raise ValueError("DOI prefixes must use the form 10.1234.")
    if not sources["biorxiv"] and not entries:
        raise ValueError("Enable at least one paper source.")
    config.setdefault("title", "The Bioprocess Brief")
    if not isinstance(config["title"], str) or not config["title"].strip():
        raise ValueError("title must be nonempty text.")
    labels = config.get("interest_labels", interests)
    if not isinstance(labels, list) or any(not isinstance(label, str) for label in labels):
        raise ValueError("interest_labels must be an array of text labels.")
    return config
