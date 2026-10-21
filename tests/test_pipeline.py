from copy import deepcopy
from datetime import date
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from literature_brief.__main__ import main
from literature_brief.config import load_config
from literature_brief.http import HttpError
from literature_brief.models import Paper
from literature_brief.pipeline import build_report, publication_window


ROOT = Path(__file__).resolve().parents[1]


def configuration():
    config = json.loads((ROOT / "config.json").read_text())
    config["llm"]["workers"] = 1
    return config


def paper(doi="10.1234/example", abstract="An abstract with methods and findings."):
    return Paper("Example paper", ["Researcher"], "https://doi.org/" + doi, abstract,
                 "Test Journal", "2026-10-18", doi, "Crossref")


def assessment():
    return {"relevance": 9, "methodology": "Test method.", "key_findings": "Test findings.",
            "rationale": "Directly relevant to the configured field."}


def sources_ok():
    return [{"name": "bioRxiv", "status": "ok", "fetched": 1, "message": "Fetched."}]


class PipelineTests(unittest.TestCase):
    def test_window_has_exactly_seven_complete_days_across_year_boundary(self):
        self.assertEqual(publication_window(date(2026, 1, 3)), (date(2025, 12, 27), date(2026, 1, 3)))
        self.assertEqual(publication_window(date(2024, 3, 1))[0], date(2024, 2, 23))

    @patch("literature_brief.pipeline.fetch_all")
    @patch("literature_brief.pipeline.Scorer")
    def test_scores_every_paper_with_abstract_but_never_fabricates_missing_one(self, scorer, fetch):
        fetch.return_value = ([paper(), paper("10.1234/absent", "")], sources_ok())
        scorer.return_value.score.return_value = assessment()
        report, result = build_report(configuration(), end=date(2026, 10, 19))
        self.assertEqual(result, 0)
        self.assertEqual(scorer.return_value.score.call_count, 1)
        self.assertEqual(report["stats"]["missing_abstract"], 1)
        self.assertEqual(report["stats"]["scored"], 1)
        self.assertEqual({p["status"] for p in report["papers"]}, {"scored", "missing_abstract"})

    @patch("literature_brief.pipeline.fetch_all")
    @patch("literature_brief.pipeline.Scorer")
    def test_opt_in_call_cap_preserves_metadata(self, scorer, fetch):
        fetch.return_value = ([paper(), paper("10.1234/other")], sources_ok())
        scorer.return_value.score.return_value = assessment()
        config = configuration()
        config["max_papers"] = 1
        report, result = build_report(config)
        self.assertEqual(result, 0)
        self.assertEqual(len(report["papers"]), 2)
        self.assertEqual(report["stats"]["capped"], 1)

    @patch("literature_brief.pipeline.fetch_all")
    def test_auxiliary_success_never_hides_total_discovery_failure(self, fetch):
        fetch.return_value = ([], [
            {"name": "bioRxiv", "status": "error", "fetched": 0, "message": "Unavailable."},
            {"name": "Europe PMC abstract enrichment", "status": "ok", "fetched": 0, "message": "Nothing to enrich."},
        ])
        _, result = build_report(configuration(), fetch_only=True)
        self.assertEqual(result, 2)

    @patch("literature_brief.pipeline.fetch_all")
    def test_successful_empty_collection_is_distinct_from_failure(self, fetch):
        fetch.return_value = ([], sources_ok())
        report, result = build_report(configuration())
        self.assertEqual(result, 0)
        self.assertEqual(report["stats"]["discovered"], 0)

    @patch("literature_brief.pipeline.fetch_all")
    @patch("literature_brief.pipeline.Scorer")
    def test_total_scoring_failure_blocks_deploy_but_keeps_diagnostic_records(self, scorer, fetch):
        fetch.return_value = ([paper()], sources_ok())
        scorer.return_value.score.side_effect = HttpError("HTTP 401 from api.openai.com.", 401)
        report, result = build_report(configuration())
        self.assertEqual(result, 3)
        self.assertEqual(report["stats"]["scoring_failed"], 1)

    @patch("literature_brief.pipeline.fetch_all")
    @patch("literature_brief.pipeline.Scorer")
    def test_provider_error_disables_new_requests_but_allows_later_cache_lookup(self, scorer, fetch):
        fetch.return_value = ([paper(), paper("10.1234/other")], sources_ok())
        scorer.return_value.score.side_effect = [HttpError("HTTP 401 from api.openai.com.", 401), assessment()]
        report, result = build_report(configuration())
        self.assertEqual(result, 0)
        calls = scorer.return_value.score.call_args_list
        self.assertTrue(calls[0].kwargs["allow_request"])
        self.assertFalse(calls[1].kwargs["allow_request"])
        self.assertEqual(report["stats"]["scored"], 1)

    @patch("literature_brief.pipeline.fetch_all")
    @patch("literature_brief.pipeline.Scorer")
    def test_fetch_only_never_constructs_scorer(self, scorer, fetch):
        fetch.return_value = ([paper()], sources_ok())
        report, result = build_report(configuration(), fetch_only=True)
        scorer.assert_not_called()
        self.assertEqual(result, 0)
        self.assertEqual(report["papers"][0]["status"], "not_scored")

    def test_demo_cli_builds_portable_outputs_without_api_credentials(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            with patch.dict("os.environ", {}, clear=True):
                result = main(["--config", str(ROOT / "config.json"), "--output", str(output),
                               "--demo", "--end-date", "2026-10-19"])
            self.assertEqual(result, 0)
            self.assertIn("SYNTHETIC DEMO", (output / "index.html").read_text())
            self.assertTrue((output / ".nojekyll").exists())
            self.assertTrue(json.loads((output / "brief.json").read_text())["demo"])


class ConfigurationTests(unittest.TestCase):
    def load(self, config, env=None):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.json"
            path.write_text(json.dumps(config))
            with patch.dict("os.environ", env or {}, clear=True):
                return load_config(path)

    def test_switching_provider_selects_appropriate_default_model(self):
        config = self.load(configuration(), {"LLM_PROVIDER": "anthropic"})
        self.assertEqual(config["llm"]["model"], "claude-haiku-4-5")

    def test_case_normalization_preserves_custom_model(self):
        config = configuration()
        config["llm"].update(provider="OpenAI", model="custom-model")
        self.assertEqual(self.load(config)["llm"]["model"], "custom-model")

    def test_empty_workflow_variables_preserve_configured_values(self):
        config = self.load(configuration(), {"LLM_PROVIDER": "", "LLM_MODEL": ""})
        self.assertEqual(config["llm"]["model"], "gpt-4.1-mini")

    def test_invalid_limits_and_source_settings_fail_early(self):
        for field, value in (("max_papers", -1), ("min_relevance", 11), ("top_n", True)):
            config = configuration()
            config[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.load(config)
        config = configuration()
        config["sources"]["crossref"][0]["prefix"] = "not-a-prefix"
        with self.assertRaises(ValueError):
            self.load(config)


if __name__ == "__main__":
    unittest.main()
