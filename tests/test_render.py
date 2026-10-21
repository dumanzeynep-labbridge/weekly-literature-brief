"""Behavioral checks for the static brief and its untrusted source text."""

import json
from pathlib import Path
import tempfile
import unittest

from literature_brief.render import render_brief


def paper(title="CHO process", score=8, **overrides):
    value = {
        "title": title, "authors": ["A. Researcher", "B. Scientist"],
        "url": "https://example.org/paper", "abstract": "A study of engineered cells.",
        "journal": "Biotechnology Journal", "published": "2026-10-20", "doi": "10.1/test",
        "source": "Europe PMC", "is_preprint": False, "relevance": score,
        "methodology": "Compared engineered and control cells.",
        "key_findings": "Engineered cells improved yield.",
        "rationale": "Relevant to CHO engineering.", "status": "scored", "error": "",
    }
    value.update(overrides)
    return value


def report(papers=None, **overrides):
    value = {
        "generated_at": "2026-10-21T07:00:00Z", "window_start": "2026-10-14",
        "window_end": "2026-10-21", "title": "The weekly bioengineering brief",
        "interests": ["Bioprocessing", "CHO cell engineering"], "provider": "openai",
        "model": "test-model", "demo": False, "min_relevance": 6, "top_n": 2,
        "papers": papers or [], "source_statuses": [], "stats": {},
    }
    value.update(overrides)
    return value


class RenderBriefTests(unittest.TestCase):
    def render(self, data):
        with tempfile.TemporaryDirectory() as folder:
            html = render_brief(data, Path(folder)).read_text(encoding="utf-8")
            saved = json.loads((Path(folder) / "brief.json").read_text(encoding="utf-8"))
        return html, saved

    def test_escapes_external_text_everywhere_and_preserves_json(self):
        attack = '<script>alert("unsafe")</script>'
        record = paper(title=attack, abstract=attack, authors=[attack],
                       methodology=attack, key_findings=attack, rationale=attack)
        html, saved = self.render(report([record], title=attack, interests=[attack],
                                         source_statuses=[{"name": attack, "status": "error", "fetched": 0, "message": attack}]))
        self.assertNotIn(attack, html)
        self.assertIn('&lt;script&gt;alert(&quot;unsafe&quot;)&lt;/script&gt;', html)
        self.assertEqual(saved["papers"][0]["title"], attack)

    def test_only_safe_web_urls_become_paper_links(self):
        for url in ["javascript:alert(1)", "data:text/html,hello", "//example.org/paper",
                    "https://example.org\n/evil", "https://user:secret@example.org/paper", "https://[bad"]:
            with self.subTest(url=url):
                html, _ = self.render(report([paper(url=url)]))
                self.assertNotIn('target="_blank"', html)
        html, _ = self.render(report([paper(url='https://example.org/paper?a=1&b="x"')]))
        self.assertIn('href="https://example.org/paper?a=1&amp;b=&quot;x&quot;"', html)
        self.assertIn('rel="noopener noreferrer"', html)

    def test_exactly_two_labeled_bullets_for_each_assessed_paper(self):
        html, _ = self.render(report([paper()]))
        findings = html.split('<ul class="findings">', 1)[1].split('</ul>', 1)[0]
        self.assertEqual(findings.count('<li>'), 2)
        self.assertIn('<strong>Methodology</strong>', findings)
        self.assertIn('<strong>Findings</strong>', findings)

    def test_ranking_then_date_and_no_records_lost_to_top_n(self):
        html, saved = self.render(report([
            paper("Older high", 9, published="2026-10-18"),
            paper("Low", 3), paper("Newer high", 9), paper("Medium", 8),
            paper("Missing abstract", None, abstract="", status="missing_abstract"),
        ], top_n=1))
        self.assertLess(html.index('>Newer high<span'), html.index('>Older high<span'))
        self.assertLess(html.index('>Older high<span'), html.index('>Medium<span'))
        self.assertIn('More relevant papers <span>2</span>', html)
        self.assertIn('Below the relevance threshold <span>1</span>', html)
        self.assertIn('Papers awaiting assessment <span>1</span>', html)
        self.assertEqual(html.count('<article class="paper"'), 5)
        self.assertEqual(len(saved["papers"]), 5)

    def test_zero_top_n_displays_all_eligible_papers(self):
        html, _ = self.render(report([paper("One"), paper("Two")], top_n=0))
        self.assertEqual(html.count('<article class="paper"'), 2)
        self.assertNotIn('More relevant papers <span>', html)

    def test_empty_and_partial_source_states_are_explained(self):
        html, _ = self.render(report(source_statuses=[{
            "name": "A source", "status": "partial", "fetched": 0, "message": "Retrieval cap reached",
        }]))
        self.assertIn('No featured papers this week', html)
        self.assertIn('does not establish that no relevant work was published', html)
        self.assertIn('Coverage is incomplete.', html)
        self.assertIn('Retrieval cap reached', html)
        self.assertIn('end exclusive', html)

    def test_demo_is_prominent_and_disables_paper_links(self):
        html, _ = self.render(report([paper()], demo=True))
        self.assertIn('SYNTHETIC DEMO', html)
        self.assertIn('fictional papers and assessments', html)
        self.assertIn('paper links disabled', html)
        self.assertNotIn('target="_blank"', html)

    def test_unscored_papers_are_never_given_summaries_or_validated_scores(self):
        html, _ = self.render(report([
            paper("API failed", 9, status="scoring_failed"),
            paper("No abstract", None, status="missing_abstract", abstract=""),
        ]))
        self.assertNotIn('<ul class="findings">', html)
        self.assertIn('The relevance assessment could not be completed.', html)
        self.assertIn('An abstract was unavailable', html)
        self.assertEqual(html.count('<span class="tag unscored-tag">Unscored</span>'), 2)

    def test_capped_and_discovery_records_have_accurate_reasons(self):
        html, _ = self.render(report([
            paper("Capped", None, status="capped"),
            paper("Discovery", None, status="not_scored"),
        ]))
        self.assertIn("configured assessment limit was reached", html)
        self.assertIn("retrieved for discovery only; no AI assessment was requested", html)
        self.assertNotIn("The relevance assessment could not be completed", html)

    def test_fetch_only_mode_explains_no_ai_was_requested(self):
        html, _ = self.render(report([paper(score=None, status="not_scored"),
                                     paper("No abstract", None, status="missing_abstract", abstract="")], fetch_only=True))
        self.assertIn("Discovery only; no AI assessment was requested.", html)
        self.assertIn("Papers discovered this week", html)
        self.assertNotIn("Papers awaiting assessment", html)
        self.assertNotIn("none has a completed assessment", html)
        self.assertNotIn("Relevance scores and the two-bullet summaries are AI-generated", html)
        self.assertIn('<strong>1</strong><span>abstracts available</span>', html)
        self.assertIn('<strong>1</strong><span>abstracts unavailable</span>', html)
        self.assertIn('<span>focused matches</span>', html)
        self.assertNotIn('<span>assessed</span>', html)
        self.assertNotIn('<span>unscored</span>', html)
        self.assertNotIn('at relevance ', html)
        self.assertNotIn('<div class="score"', html)
        self.assertIn('Discovery only · no LLM used', html)
        self.assertNotIn('openai / test-model', html)
        self.assertIn('Complete metadata &amp; keyword matches (JSON)', html)
        self.assertEqual(html.count('Metadata only</span>'), 2)
        self.assertEqual(html.count('<article class="paper"'), 2)
        self.assertNotIn('<details class="paper-group"', html)

    def test_empty_fetch_only_edition_has_no_scoring_or_featured_claim(self):
        html, _ = self.render(report(fetch_only=True))
        self.assertIn('No papers discovered this week', html)
        self.assertIn('does not establish that no relevant work was published', html)
        self.assertNotIn('No featured papers', html)

    def test_concise_interest_labels_and_retrieval_step_label(self):
        html, saved = self.render(report(interest_labels=["CHO cells"], interests=["A detailed scoring interest"],
                                         source_statuses=[{"name": "Enrichment", "status": "ok", "fetched": 0}]))
        self.assertIn('<span class="interest">CHO cells</span>', html)
        self.assertNotIn("A detailed scoring interest", html)
        self.assertEqual(saved["interests"], ["A detailed scoring interest"])
        self.assertIn("1 retrieval steps", html)
        self.assertNotIn("configured sources", html)

    def test_partial_assessments_notice_is_visible(self):
        html, _ = self.render(report([paper(), paper("Failed", None, status="scoring_failed")], stats={"scoring_failed": 1}))
        self.assertIn("Some AI assessments are incomplete.", html)
        self.assertIn("1 paper(s) could not be assessed", html)

    def test_focused_search_index_links_existing_cards_and_preserves_json(self):
        match = {"matched_blocks": {"host": ["CHO cells"], "mitochondria": ["NRF1"]},
                 "matched_combinations": ["host_mito"], "ambiguity_notes": ["Check NRF1 versus NFE2L1."]}
        summary = {"blocks": [
            {"id": "host", "label": "Host / system", "terms": ["CHO cells"]},
            {"id": "mitochondria", "label": "Mitochondria", "terms": ["NRF1"]},
        ], "combinations": [{"id": "host_mito", "label": "Host + mitochondria", "blocks": ["host", "mitochondria"], "match_count": 1}],
            "matched_papers": 1}
        record = paper("CHO NRF1", None, abstract="", status="missing_abstract", focused_search=match)
        html, saved = self.render(report([record, paper("Other paper")], focused_searches=summary))
        self.assertIn('Focused search matches', html)
        self.assertIn('Host + mitochondria <span>1</span>', html)
        self.assertIn('href="#paper-1">CHO NRF1</a>', html)
        self.assertIn('(title match; abstract unavailable)', html)
        self.assertIn('<details class="paper-group" open>', html)
        self.assertIn('data-combinations="host_mito"', html)
        self.assertIn('Matched terms in title / abstract', html)
        self.assertIn('<strong>Host / system</strong>: CHO cells', html)
        self.assertIn('Check NRF1 versus NFE2L1.', html)
        self.assertEqual(html.count('<article class="paper"'), 2)
        self.assertEqual(saved["papers"][0]["focused_search"], match)
        self.assertEqual(saved["focused_searches"], summary)

    def test_zero_focused_matches_explains_scope_and_retains_papers(self):
        html, saved = self.render(report([paper()], focused_searches={
            "blocks": [], "combinations": [{"id": "focus", "label": "A focused combination", "blocks": [], "match_count": 0}],
            "matched_papers": 0,
        }))
        self.assertIn('No matches found in the titles and available abstracts of the configured sources for this publication window.', html)
        self.assertIn('0 distinct paper(s) matched', html)
        self.assertEqual(html.count('<article class="paper"'), 1)
        self.assertEqual(len(saved["papers"]), 1)

    def test_focused_search_text_is_escaped(self):
        attack = '<img src=x onerror="alert(1)">'
        html, _ = self.render(report([paper(focused_search={
            "matched_blocks": {"host": [attack]}, "matched_combinations": ["focus"], "ambiguity_notes": [attack],
        })], focused_searches={"blocks": [{"id": "host", "label": attack, "terms": [attack]}],
                              "combinations": [{"id": "focus", "label": attack, "blocks": ["host"], "match_count": 1}],
                              "matched_papers": 1}))
        self.assertNotIn(attack, html)
        self.assertIn('&lt;img src=x onerror=&quot;alert(1)&quot;&gt;', html)


if __name__ == "__main__":
    unittest.main()
