"""Shared metadata passed between discovery, scoring, and presentation."""

from dataclasses import dataclass


@dataclass
class Paper:
    title: str
    authors: list[str]
    url: str
    abstract: str
    journal: str
    published: str
    doi: str = ""
    source: str = ""
    is_preprint: bool = False
