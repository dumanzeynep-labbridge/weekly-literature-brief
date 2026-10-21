"""Discovery tests use recorded-shape API responses; no network or API keys."""

from datetime import date
import unittest
from unittest.mock import patch

from literature_brief.http import HttpError
from literature_brief.models import Paper
from literature_brief.sources import deduplicate, fetch_all, plain_text, publication_date


START = date(2026, 10, 12)
END = date(2026, 10, 19)


def config(*specs, biorxiv=False, enrich=False):
    return {
        "sources": {
            "biorxiv": biorxiv,
            "crossref": list(specs),
            "enrich_abstracts": enrich,
            "contact_email": "reader@example.org",
            "request_interval_seconds": 0,
        },
    }


def crossref_item(doi, day=12, **overrides):
    item = {
        "DOI": doi,
        "title": ["CHO cell engineering"],
        "container-title": ["Biotechnology and Bioengineering"],
        "author": [{"given": "Ada", "family": "Researcher"}, {"name": "CHO Consortium"}],
        "published-online": {"date-parts": [[2026, 10, day]]},
        "abstract": "<jats:p>Cells showed <jats:italic>improved</jats:italic> productivity.</jats:p>",
    }
    item.update(overrides)
    return item


def biorxiv_item(doi, day=12, version="1"):
    return {
        "doi": doi,
        "title": "Preprint CHO engineering",
        "authors": "Ada Researcher; Bob Scientist",
        "date": f"2026-10-{day:02d}",
        "version": version,
        "abstract": "A preprint abstract.",
    }


class QueueClient:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def get_json(self, url, params=None, headers=None):
        self.calls.append((url, params or {}))
        if not self.responses:
            raise AssertionError("Unexpected extra request")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class PublicationDateTests(unittest.TestCase):
    def test_online_first_date_excludes_recent_print_of_older_article(self):
        item = crossref_item("10.1002/old", **{
            "published-online": {"date-parts": [[2026, 8, 3]]},
            "published-print": {"date-parts": [[2026, 10, 14]]},
            "created": {"date-parts": [[2026, 10, 15]]},
        })
        self.assertEqual(publication_date(item), date(2026, 8, 3))
        client = QueueClient({"message": {"items": [item]}})
        papers, statuses = fetch_all(config({"name": "Test", "issn": "1097-0290"}), START, END, client)
        self.assertEqual(papers, [])
        self.assertEqual(statuses[0]["status"], "ok")
        self.assertIn("outside first-publication", statuses[0]["message"])

    def test_registry_activity_cannot_supply_publication_date(self):
        item = {"created": {"date-parts": [[2026, 10, 13]]}, "deposited": {"date-parts": [[2026, 10, 14]]}}
        self.assertIsNone(publication_date(item))

    def test_no_day_is_invented_for_imprecise_earlier_online_date(self):
        item = crossref_item("10.1002/imprecise", **{
            "published-online": {"date-parts": [[2026, 10]]},
            "published-print": {"date-parts": [[2026, 10, 14]]},
        })
        self.assertIsNone(publication_date(item))
        client = QueueClient({"message": {"items": [item]}})
        papers, statuses = fetch_all(config({"name": "Test", "issn": "1097-0290"}), START, END, client)
        self.assertFalse(papers)
        self.assertEqual(statuses[0]["status"], "partial")
        self.assertIn("imprecise publication date", statuses[0]["message"])

    def test_future_imprecise_print_date_does_not_hide_exact_online_day(self):
        item = crossref_item("10.1002/future", **{"published-print": {"date-parts": [[2026, 11]]}})
        self.assertEqual(publication_date(item), START)


class FetchTests(unittest.TestCase):
    def test_crossref_cursor_pagination_and_exclusive_end(self):
        client = QueueClient(
            {"message": {"items": [crossref_item("10.1002/a"), crossref_item("10.1002/b", 18)], "next-cursor": "cursor2"}},
            {"message": {"items": [crossref_item("10.1002/end", 19)]}},
        )
        with patch("literature_brief.sources._CROSSREF_PAGE_SIZE", 2):
            papers, statuses = fetch_all(config({"name": "Test", "issn": "1097-0290"}), START, END, client)
        self.assertEqual({paper.doi for paper in papers}, {"10.1002/a", "10.1002/b"})
        self.assertEqual(papers[0].authors, ["Ada Researcher", "CHO Consortium"])
        self.assertEqual(papers[0].abstract, "Cells showed improved productivity.")
        self.assertEqual(client.calls[1][1]["cursor"], "cursor2")
        self.assertIn("until-pub-date:2026-10-18", client.calls[0][1]["filter"])
        self.assertEqual(client.calls[0][1]["mailto"], "reader@example.org")
        self.assertEqual(statuses[0]["status"], "ok")

    def test_reused_cursor_is_valid_when_the_page_advances(self):
        client = QueueClient(
            {"message": {"items": [crossref_item("10.1002/a")], "next-cursor": "same"}},
            {"message": {"items": [crossref_item("10.1002/b")], "next-cursor": "same"}},
            {"message": {"items": []}},
        )
        with patch("literature_brief.sources._CROSSREF_PAGE_SIZE", 1):
            papers, statuses = fetch_all(config({"name": "Nature", "prefix": "10.1038"}), START, END, client)
        self.assertEqual(len(papers), 2)
        self.assertEqual(statuses[0]["status"], "ok")
        self.assertIn("/prefixes/10.1038/works", client.calls[0][0])

    def test_pagination_failure_retains_records_and_reports_partial(self):
        client = QueueClient(
            {"message": {"items": [crossref_item("10.1002/a")], "next-cursor": "next"}},
            HttpError("HTTP 503 from api.crossref.org.", 503),
        )
        with patch("literature_brief.sources._CROSSREF_PAGE_SIZE", 1):
            papers, statuses = fetch_all(config({"name": "Test", "issn": "1097-0290"}), START, END, client)
        self.assertEqual(len(papers), 1)
        self.assertEqual(statuses[0]["status"], "partial")
        self.assertIn("503", statuses[0]["message"])

    def test_repeated_page_stops_with_visible_partial_status(self):
        response = {"message": {"items": [crossref_item("10.1002/a")], "next-cursor": "next"}}
        client = QueueClient(response, response)
        with patch("literature_brief.sources._CROSSREF_PAGE_SIZE", 1):
            papers, statuses = fetch_all(config({"name": "Test", "issn": "1097-0290"}), START, END, client)
        self.assertEqual(len(papers), 1)
        self.assertEqual(statuses[0]["status"], "partial")
        self.assertIn("repeated a page", statuses[0]["message"])

    def test_one_source_failure_does_not_prevent_another_source(self):
        client = QueueClient(HttpError("Connection failed"), {"message": {"items": [crossref_item("10.1002/a")]}})
        papers, statuses = fetch_all(config({"name": "Test", "issn": "1097-0290"}, biorxiv=True), START, END, client)
        self.assertEqual(len(papers), 1)
        self.assertEqual([status["status"] for status in statuses], ["error", "ok"])

    def test_enrichment_does_not_mask_total_discovery_failure(self):
        client = QueueClient(HttpError("Connection failed"))
        papers, statuses = fetch_all(config(biorxiv=True, enrich=True), START, END, client)
        self.assertFalse(papers)
        self.assertEqual(len(statuses), 1)
        self.assertTrue(all(status["status"] == "error" for status in statuses))

    def test_biorxiv_explicit_empty_result_is_successful(self):
        client = QueueClient({"messages": [{"status": "no papers found"}], "collection": []})
        papers, statuses = fetch_all(config(biorxiv=True), START, END, client)
        self.assertFalse(papers)
        self.assertEqual(statuses[0]["status"], "ok")

    def test_biorxiv_pagination_includes_new_papers_excludes_revisions(self):
        client = QueueClient(
            {"messages": [{"status": "ok", "total": "3"}], "collection": [biorxiv_item("10.1101/a"), biorxiv_item("10.1101/old", version="3")]},
            {"messages": [{"status": "ok", "total": "3"}], "collection": [biorxiv_item("10.1101/b", 18)]},
        )
        papers, statuses = fetch_all(config(biorxiv=True), START, END, client)
        self.assertEqual({paper.doi for paper in papers}, {"10.1101/a", "10.1101/b"})
        self.assertTrue(all(paper.is_preprint for paper in papers))
        self.assertTrue(client.calls[0][0].endswith("/2026-10-12/2026-10-18/0"))
        self.assertTrue(client.calls[1][0].endswith("/2"))
        self.assertIn("preprint revisions", statuses[0]["message"])

    def test_biorxiv_early_empty_page_is_partial_not_silent_truncation(self):
        client = QueueClient(
            {"messages": [{"status": "ok", "total": "3"}], "collection": [biorxiv_item("10.1101/a")]},
            {"messages": [{"status": "ok", "total": "3"}], "collection": []},
        )
        papers, statuses = fetch_all(config(biorxiv=True), START, END, client)
        self.assertEqual(len(papers), 1)
        self.assertEqual(statuses[0]["status"], "partial")
        self.assertIn("1 of 3", statuses[0]["message"])


class EnrichmentTests(unittest.TestCase):
    def test_missing_abstracts_are_batched_and_matched_by_doi(self):
        client = QueueClient(
            {"message": {"items": [crossref_item("10.1002/a", abstract=""), crossref_item("10.1002/b", abstract=""), crossref_item("10.1002/c")]}},
            {"resultList": {"result": [{"doi": "10.1002/A", "abstractText": "<p>Recovered A.</p>", "source": "MED", "id": "1"}, {"doi": "10.1002/unrequested", "abstractText": "Wrong abstract"}]}},
        )
        papers, statuses = fetch_all(config({"name": "Test", "issn": "1097-0290"}, enrich=True), START, END, client)
        by_doi = {paper.doi: paper for paper in papers}
        self.assertEqual(by_doi["10.1002/a"].abstract, "Recovered A.")
        self.assertEqual(by_doi["10.1002/b"].abstract, "")
        self.assertEqual(by_doi["10.1002/c"].abstract, "Cells showed improved productivity.")
        self.assertEqual(len(client.calls), 2)
        self.assertIn('DOI:"10.1002/a" OR DOI:"10.1002/b"', client.calls[1][1]["query"])
        self.assertEqual(client.calls[1][1]["resultType"], "core")
        self.assertEqual(client.calls[0][1]["mailto"], "reader@example.org")
        self.assertEqual(client.calls[1][1]["email"], "reader@example.org")
        self.assertEqual(statuses[-1]["status"], "partial")
        self.assertIn("1 papers still lack", statuses[-1]["message"])

    def test_enrichment_failure_keeps_usable_metadata_and_empty_abstract(self):
        client = QueueClient({"message": {"items": [crossref_item("10.1002/a", abstract="")]}}, HttpError("Europe PMC unavailable"))
        papers, statuses = fetch_all(config({"name": "Test", "issn": "1097-0290"}, enrich=True), START, END, client)
        self.assertEqual(papers[0].title, "CHO cell engineering")
        self.assertFalse(papers[0].abstract)
        self.assertEqual(statuses[-1]["status"], "partial")
        self.assertIn("Europe PMC unavailable", statuses[-1]["message"])

    def test_enrichment_paginates_and_continues_after_failed_batch(self):
        client = QueueClient(
            {"message": {"items": [crossref_item("10.1002/a", abstract=""), crossref_item("10.1002/b", abstract=""), crossref_item("10.1002/c", abstract="")]}},
            HttpError("First batch unavailable"),
            {"resultList": {"result": [{"doi": "10.1002/c", "abstractText": "Recovered C.", "id": "3"}]}, "nextCursorMark": "next", "hitCount": 2},
            {"resultList": {"result": [{"doi": "10.1002/c", "abstractText": "Recovered C.", "id": "4"}]}, "nextCursorMark": "next2", "hitCount": 2},
            {"resultList": {"result": []}},
        )
        with patch("literature_brief.sources._EUROPE_PMC_BATCH_SIZE", 2), patch("literature_brief.sources._EUROPE_PMC_PAGE_SIZE", 1):
            papers, statuses = fetch_all(config({"name": "Test", "issn": "1097-0290"}, enrich=True), START, END, client)
        by_doi = {paper.doi: paper for paper in papers}
        self.assertEqual(by_doi["10.1002/c"].abstract, "Recovered C.")
        self.assertEqual(by_doi["10.1002/a"].abstract, "")
        self.assertEqual(client.calls[3][1]["cursorMark"], "next")
        self.assertEqual(statuses[-1]["status"], "partial")
        self.assertIn("First batch unavailable", statuses[-1]["message"])

    def test_same_doi_prefers_journal_and_keeps_versions_abstracts_separate(self):
        preprint = Paper("Preprint", ["Ada"], "https://doi.org/10.1002/a", "Preprint results", "bioRxiv", "2026-10-12", "10.1002/A", "bioRxiv", True)
        journal = Paper("Published article", ["Ada"], "https://doi.org/10.1002/a", "", "Journal", "2026-10-13", "10.1002/a", "Crossref", False)
        papers = deduplicate([preprint, journal, journal])
        self.assertEqual(len(papers), 1)
        self.assertEqual(papers[0].title, "Published article")
        self.assertFalse(papers[0].is_preprint)
        self.assertEqual(papers[0].abstract, "")

    def test_jats_cleanup_preserves_inline_scientific_text(self):
        self.assertEqual(plain_text("<jats:p>CHO &amp; HEK293: CO<sub>2</sub>.</jats:p><jats:p>Next.</jats:p>"), "CHO & HEK293: CO2. Next.")


if __name__ == "__main__":
    unittest.main()
