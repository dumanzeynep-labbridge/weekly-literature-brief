"""Free personal ranking, citation alerts, email, and Gemini tests; no network or keys."""

from __future__ import annotations

from copy import deepcopy
from datetime import date
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from literature_brief.mailer import build_message, email_picks, send
from literature_brief.models import Paper
from literature_brief.personal import fetch_seed_context, rank_records, validate_personal_config
from literature_brief.pipeline import build_report
from literature_brief.render import render_brief, select_personal
from literature_brief.scoring import Scorer
from literature_brief.search import evaluate_paper
from literature_brief.sources import fetch_all


START, END = date(2026, 10, 12), date(2026, 10, 19)
SEARCH = {
    "blocks": [
        {"id": "host", "label": "CHO hosts", "terms": ["CHO cells", "Chinese hamster ovary", "fed-batch"]},
        {"id": "er_atp", "label": "ER ATP supply", "terms": ["AXER", "SLC35B1", "ER stress"]},
    ],
    "combinations": [{"id": "host_er", "label": "Host + ER ATP", "blocks": ["host", "er_atp"]}],
}


def personal(**overrides):
    value = {"block_weights": {"er_atp": 4, "host": 3}, "weak_terms": ["ER stress", "fed-batch"],
             "watch_authors": ["Richard Zimmermann"], "exclude_terms": ["zebrafish"], "seed_papers": []}
    value.update(overrides)
    validate_personal_config(value)
    return value


def record(title, abstract="", authors=(), doi="10.1/x", published="2026-10-14"):
    return {"title": title, "abstract": abstract, "authors": list(authors), "doi": doi, "published": published,
            "focused_search": evaluate_paper(title, abstract, SEARCH)}


class QueueClient:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def get_json(self, url, params=None, headers=None):
        self.calls.append((url, dict(params or {})))
        if not self.responses:
            raise AssertionError("Unexpected extra request")
        return self.responses.pop(0)


class RankingTests(unittest.TestCase):
    def rank(self, records, seeds=(), citing=None, **overrides):
        config = {"search": SEARCH, "personal": personal(**overrides)}
        rank_records(records, config, list(seeds), citing or {})
        return records

    def test_title_terms_outrank_abstract_terms_and_weak_terms_count_little(self):
        title, abstract, weak = self.rank([
            record("AXER controls ER ATP import"),
            record("A transporter study", "We characterised AXER.", doi="10.1/b"),
            record("A stress study", "Cells showed ER stress.", doi="10.1/c"),
        ])
        self.assertEqual(title["personal_score"], 6.0)
        self.assertEqual(abstract["personal_score"], 4.0)
        self.assertAlmostEqual(weak["personal_score"], 1.2)
        self.assertIn("ambiguous terms only", weak["personal_reasons"][0])

    def test_combination_author_citation_and_exclusion_are_explained(self):
        (paper,) = self.rank(
            [record("SLC35B1 in CHO cells", "Zebrafish were not used.", authors=["Zimmermann, R."], doi="10.1/cite")],
            citing={"10.1/cite": ["AXER is an ATP/ADP exchanger"]},
        )
        reasons = " | ".join(paper["personal_reasons"])
        for expected in ("Combination: Host + ER ATP", "Watched author: Zimmermann, R. (initial-only match)",
                         "Cites your seed paper: AXER", "Penalised for excluded term: zebrafish"):
            self.assertIn(expected, reasons)
        # 4.5 + 6 title blocks, +3 combination, +3 initial-only author, +8 citation, -6 exclusion.
        self.assertEqual(paper["personal_score"], 18.5)

    def test_watched_author_needs_matching_initial(self):
        (paper,) = self.rank([record("Unrelated", authors=["Anna Zimmermann"])])
        self.assertEqual(paper["personal_score"], 0)

    def test_common_family_names_need_more_than_a_shared_initial(self):
        watched = ["Jae Seong Lee", "Wei-Shou Hu", "Dong-Yup Lee", "Véronique Chotteau"]
        cases = {"Lee, J.": 0, "Lee, J. S.": 6, "Wei Hu": 0, "Hu, W.-S.": 6, "Dong Yup Lee": 6, "Veronique Chotteau": 6}
        for author, expected in cases.items():
            (paper,) = self.rank([record("Unrelated", authors=[author])], watch_authors=watched)
            self.assertEqual(paper["personal_score"], expected, author)

    def test_similarity_to_seed_abstract_scores_without_keywords(self):
        seed = {"id": "W1", "doi": "10.1/seed", "title": "Mitochondrial creatine shuttle feeds secretory folding",
                "abstract": "A creatine phosphate shuttle couples mitochondrial respiration to secretory folding capacity."}
        papers = [record("Creatine phosphate shuttle sustains secretory folding", "Mitochondrial respiration and creatine shuttle."),
                  record("Bird migration timing", "Migratory birds left earlier.", doi="10.1/bird")]
        near, far = self.rank(papers, seeds=[seed])
        self.assertGreater(near["personal_score"], 4)
        self.assertIn("Similar to seed paper", near["personal_reasons"][-1])
        self.assertEqual(far["personal_score"], 0)

    def test_invalid_seed_doi_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_personal_config({"seed_papers": ["not-a-doi"]})


class OpenAlexTests(unittest.TestCase):
    def test_seed_lookup_and_citation_alerts_build_papers_and_citing_map(self):
        client = QueueClient(
            {"id": "https://openalex.org/W1", "display_name": "AXER seed",
             "abstract_inverted_index": {"ATP": [1], "AXER": [0]}},
            {"results": [{
                "doi": "https://doi.org/10.1/NEW", "display_name": "A new ER ATP paper", "publication_date": "2026-10-13",
                "authorships": [{"author": {"display_name": "Ada Researcher"}}], "type": "article",
                "primary_location": {"source": {"display_name": "Cell Reports"}},
                "abstract_inverted_index": {"New": [0], "work": [1]},
                "referenced_works": ["https://openalex.org/W1"],
            }], "meta": {"next_cursor": None}},
        )
        config = {"personal": personal(seed_papers=[{"doi": "10.1234/seed"}]), "sources": {"contact_email": "me@example.org"}}
        with patch.dict(os.environ, {"OPENALEX_API_KEY": "test-openalex-key"}):
            seeds, papers, citing, statuses = fetch_seed_context(config, START, END, client)
        self.assertEqual(seeds[0]["abstract"], "AXER ATP")
        self.assertEqual(papers[0].doi, "10.1/new")
        self.assertEqual(papers[0].journal, "Cell Reports")
        self.assertEqual(citing, {"10.1/new": ["AXER seed"]})
        self.assertIn("cites:W1,from_publication_date:2026-10-12,to_publication_date:2026-10-18", client.calls[1][1]["filter"])
        self.assertEqual(client.calls[0][1]["api_key"], "test-openalex-key")
        self.assertEqual([status["status"] for status in statuses], ["ok", "ok"])

    def test_no_seeds_makes_no_requests(self):
        self.assertEqual(fetch_seed_context({"personal": personal()}, START, END, QueueClient()), ([], [], {}, []))


class PipelineTests(unittest.TestCase):
    def config(self, **overrides):
        return {"title": "Brief", "interests": ["CHO"], "llm": {"provider": "gemini", "model": "m", "workers": 1},
                "min_relevance": 6, "top_n": 20, "max_papers": 0, "search": SEARCH,
                "sources": {"biorxiv": False, "crossref": [{"name": "J", "issn": "1234-5678"}]},
                "personal": personal(ai_shortlist=1, **overrides)}

    @patch("literature_brief.pipeline.fetch_all")
    @patch("literature_brief.pipeline.Scorer")
    def test_only_the_personal_shortlist_reaches_the_llm(self, scorer, fetch):
        papers = [Paper("AXER in CHO cells", [], "https://doi.org/10.1/a", "AXER abstract", "J", "2026-10-13", "10.1/a"),
                  Paper("Bird migration", [], "https://doi.org/10.1/b", "Birds", "J", "2026-10-14", "10.1/b")]
        fetch.return_value = (papers, [{"name": "J", "status": "ok", "fetched": 2, "message": ""}])
        scorer.return_value.score.return_value = {"relevance": 8, "methodology": "M.", "key_findings": "F.", "rationale": "R."}
        report, result = build_report(self.config(), end=END)
        self.assertEqual(result, 0)
        self.assertEqual(scorer.return_value.score.call_count, 1)
        statuses = {paper["title"]: paper["status"] for paper in report["papers"]}
        self.assertEqual(statuses, {"AXER in CHO cells": "scored", "Bird migration": "capped"})
        self.assertEqual(report["personal"]["top_n"], 10)


class BiorxivCategoryTests(unittest.TestCase):
    def test_categories_outside_allowlist_are_excluded_and_counted(self):
        item = {"title": "T", "authors": "A", "date": "2026-10-12", "version": "1", "abstract": "x"}
        client = QueueClient({"messages": [{"status": "ok", "total": "2"}], "collection": [
            dict(item, doi="10.1101/keep", category="Cell Biology"), dict(item, doi="10.1101/drop", category="neuroscience")]})
        config = {"sources": {"biorxiv": True, "crossref": [], "enrich_abstracts": False, "request_interval_seconds": 0,
                              "biorxiv_categories": ["cell biology"]}}
        papers, statuses = fetch_all(config, START, END, client)
        self.assertEqual([paper.doi for paper in papers], ["10.1101/keep"])
        self.assertIn("1 outside configured bioRxiv categories", statuses[0]["message"])
        self.assertEqual(statuses[0]["status"], "ok")


def report_with(papers, **overrides):
    value = {"title": "My <Brief>", "window_start": "2026-10-12", "window_end": "2026-10-19", "fetch_only": True,
             "papers": papers, "personal": {"top_n": 2, "min_score": 4}, "source_statuses": [], "stats": {}}
    value.update(overrides)
    return value


def paper(title, score, **extra):
    value = {"title": title, "url": "https://doi.org/10.1/" + title, "journal": "J", "published": "2026-10-13",
             "abstract": "abstract", "personal_score": score, "personal_reasons": [f"Reason for {title}"], "status": "not_scored"}
    value.update(extra)
    return value


class RenderAndEmailTests(unittest.TestCase):
    def test_free_brief_features_top_matches_and_folds_the_rest(self):
        report = report_with([paper("low", 1), paper("best", 9), paper("good", 5), paper("third", 4.5)])
        picks, others = select_personal(report)
        self.assertEqual([item["title"] for item in picks], ["best", "good"])
        self.assertEqual([item["title"] for item in others], ["third", "low"])
        with tempfile.TemporaryDirectory() as folder:
            html = render_brief(report, Path(folder)).read_text(encoding="utf-8")
        self.assertIn("Your top matches this week", html)
        self.assertIn("Free personal ranking; no AI was used.", html)
        self.assertIn("Why it is in your brief", html)
        self.assertIn("Other papers this week, closest first <span>2</span>", html)
        self.assertLess(html.index("Reason for best"), html.index("Reason for good"))

    def test_email_lists_picks_escapes_text_and_links_full_brief(self):
        report = report_with([paper("<b>best</b>", 9), paper("low", 1)])
        subject, text, html = build_message(report, "https://example.github.io/brief/")
        self.assertEqual(subject, "My <Brief>: 1 paper for you (week of 12 Oct 2026)")
        self.assertIn("1. <b>best</b>", text)
        self.assertIn("&lt;b&gt;best&lt;/b&gt;", html)
        self.assertNotIn("low", text)
        self.assertIn('href="https://example.github.io/brief/"', html)

    def test_ai_scores_take_priority_in_email(self):
        report = report_with([paper("ai", 2, status="scored", relevance=9, rationale="For your project."),
                              paper("free", 9)], fetch_only=False, min_relevance=6)
        self.assertEqual([item["title"] for item in email_picks(report)], ["ai"])

    def test_send_is_skipped_without_smtp_settings(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(send("s", "t", "<p>h</p>"))


class GeminiTests(unittest.TestCase):
    def test_gemini_uses_openai_compatible_endpoint_and_profile(self):
        result = {"relevance": 9, "methodology": "M.", "key_findings": "F.", "rationale": "For your project, R."}

        class Client:
            def request_json(self, method, url, headers=None, payload=None, params=None):
                self.url, self.headers, self.payload = url, headers, payload
                return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(result)}}]}

        client = Client()
        config = {"llm": {"provider": "gemini", "model": "gemini-3.5-flash-lite"}, "interests": ["CHO"],
                  "research_profile": "AXER and ER ATP in CHO cells."}
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"GEMINI_API_KEY": "test-gemini-key"}):
            scored = Scorer(config, client, Path(folder)).score(
                Paper("T", [], "https://doi.org/10.1/t", "Abstract", "J", "2026-10-13", "10.1/t"))
        self.assertEqual(scored, result)
        self.assertTrue(client.url.endswith("/v1beta/openai/chat/completions"))
        self.assertEqual(client.headers["Authorization"], "Bearer test-gemini-key")
        self.assertEqual(client.payload["response_format"]["type"], "json_schema")
        self.assertIn("AXER and ER ATP in CHO cells.", client.payload["messages"][1]["content"])


if __name__ == "__main__":
    unittest.main()
