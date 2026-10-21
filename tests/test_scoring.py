"""Provider contract and failure tests. These never contact a paid API."""

from copy import deepcopy
from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from literature_brief.http import HttpError
from literature_brief.models import Paper
from literature_brief.scoring import (
    MissingAPIKeyError,
    MissingAbstractError,
    Scorer,
    ScoringError,
)


ASSESSMENT = {
    "relevance": 9,
    "methodology": "The authors compared engineered CHO cells in fed batch cultures.",
    "key_findings": "The engineered cells produced more recombinant protein under the tested conditions.",
    "rationale": "The study directly tests a CHO production-cell engineering strategy.",
}


def openai_response(result=None):
    return {
        "status": "completed",
        "output": [{
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": json.dumps(result or ASSESSMENT)}],
        }],
    }


def anthropic_response(result=None):
    return {
        "stop_reason": "end_turn",
        "content": [{"type": "text", "text": json.dumps(result or ASSESSMENT)}],
    }


class FakeClient:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def request_json(self, method, url, **kwargs):
        self.calls.append((method, url, deepcopy(kwargs)))
        if not self.responses:
            raise AssertionError("Unexpected API request: no mocked response remains.")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return deepcopy(response)


class ScoringTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.cache = Path(self.temporary.name) / "scores"
        self.environment = patch.dict(os.environ, {
            "OPENAI_API_KEY": "test-openai-key",
            "ANTHROPIC_API_KEY": "test-anthropic-key",
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.paper = Paper(
            title="Engineering CHO metabolism for recombinant protein production",
            authors=["A. Researcher", "B. Scientist"],
            url="https://example.org/paper",
            abstract="Engineered CHO cells were compared in fed batch cultures. Protein production increased.",
            journal="Biotechnology and Bioengineering",
            published="2026-10-20",
            doi="10.0000/example",
            source="Europe PMC",
        )
        self.config = {"interests": ["CHO bioprocessing", "Metabolic engineering"], "llm": {}}

    def test_openai_uses_responses_structured_output_and_untrusted_source_prompt(self):
        client = FakeClient(openai_response())
        result = Scorer(self.config, client, self.cache).score(self.paper)
        self.assertEqual(result, ASSESSMENT)
        method, url, options = client.calls[0]
        self.assertEqual((method, url), ("POST", "https://api.openai.com/v1/responses"))
        self.assertEqual(options["headers"]["Authorization"], "Bearer test-openai-key")
        body = options["payload"]
        self.assertEqual(body["model"], "gpt-4.1-mini")
        self.assertEqual(body["max_output_tokens"], 1000)
        self.assertIs(body["store"], False)
        self.assertEqual(body["text"]["format"]["type"], "json_schema")
        self.assertIs(body["text"]["format"]["strict"], True)
        self.assertFalse(body["text"]["format"]["schema"]["additionalProperties"])
        source = json.loads(body["input"][0]["content"])
        self.assertEqual(source["paper"]["abstract"], self.paper.abstract)
        self.assertEqual(source["research_interests"], self.config["interests"])
        self.assertIn("untrusted source data", body["instructions"])
        self.assertIn("solely from the abstract", body["instructions"])
        self.assertIn("transferring microbial", body["instructions"])

    def test_anthropic_uses_messages_structured_output(self):
        config = deepcopy(self.config)
        config["llm"] = {"provider": "anthropic", "max_output_tokens": 1200}
        client = FakeClient(anthropic_response())
        self.assertEqual(Scorer(config, client, self.cache).score(self.paper), ASSESSMENT)
        method, url, options = client.calls[0]
        self.assertEqual((method, url), ("POST", "https://api.anthropic.com/v1/messages"))
        self.assertEqual(options["headers"]["x-api-key"], "test-anthropic-key")
        self.assertEqual(options["headers"]["anthropic-version"], "2023-06-01")
        body = options["payload"]
        self.assertEqual(body["model"], "claude-haiku-4-5")
        self.assertEqual(body["max_tokens"], 1200)
        self.assertEqual(body["output_config"]["format"]["type"], "json_schema")
        self.assertEqual(json.loads(body["messages"][0]["content"])["paper"]["title"], self.paper.title)

    def test_valid_cache_avoids_second_paid_request_even_without_key(self):
        client = FakeClient(openai_response())
        scorer = Scorer(self.config, client, self.cache)
        first = scorer.score(self.paper)
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            second = scorer.score(self.paper)
        self.assertEqual(first, second)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(len(list(self.cache.glob("*.json"))), 1)
        self.assertEqual(list(self.cache.glob("*.tmp")), [])
        self.assertNotIn("test-openai-key", next(self.cache.glob("*.json")).read_text())

    def test_stopped_provider_still_reuses_cache_but_never_requests_uncached_papers(self):
        client = FakeClient(openai_response())
        scorer = Scorer(self.config, client, self.cache)
        scorer.score(self.paper)
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            self.assertEqual(scorer.score(self.paper, allow_request=False), ASSESSMENT)
            uncached = replace(self.paper, title="A different uncached paper")
            with self.assertRaisesRegex(ScoringError, "Scoring stopped"):
                scorer.score(uncached, allow_request=False)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(len(list(self.cache.glob("*.json"))), 1)

    def test_metadata_abstract_interests_model_provider_and_prompt_changes_invalidate_cache(self):
        client = FakeClient(*(openai_response() for _ in range(7)), anthropic_response())
        scorer = Scorer(self.config, client, self.cache)
        scorer.score(self.paper)
        scorer.score(replace(self.paper, abstract=self.paper.abstract + " A follow-up experiment was reported."))
        scorer.score(replace(self.paper, title="A changed source title"))
        scorer.score(replace(self.paper, authors=["Different Author"]))
        interests_config = deepcopy(self.config)
        interests_config["interests"] = ["Cell therapy manufacturing"]
        Scorer(interests_config, client, self.cache).score(self.paper)
        model_config = deepcopy(self.config)
        model_config["llm"]["model"] = "a-different-explicit-model"
        Scorer(model_config, client, self.cache).score(self.paper)
        with patch("literature_brief.scoring.PROMPT_VERSION", "future-v2"):
            scorer.score(self.paper)
        provider_config = deepcopy(self.config)
        provider_config["llm"]["provider"] = "anthropic"
        Scorer(provider_config, client, self.cache).score(self.paper)
        self.assertEqual(len(client.calls), 8)

    def test_corrupted_or_invalid_cache_is_replaced_with_valid_result(self):
        client = FakeClient(openai_response(), openai_response(), openai_response())
        scorer = Scorer(self.config, client, self.cache)
        scorer.score(self.paper)
        path = next(self.cache.glob("*.json"))
        path.write_text("incomplete JSON", encoding="utf-8")
        self.assertEqual(scorer.score(self.paper), ASSESSMENT)
        cached = json.loads(path.read_text())
        cached["result"]["relevance"] = 99
        path.write_text(json.dumps(cached), encoding="utf-8")
        self.assertEqual(scorer.score(self.paper), ASSESSMENT)
        self.assertEqual(len(client.calls), 3)
        self.assertEqual(json.loads(path.read_text())["result"], ASSESSMENT)

    def test_missing_abstract_never_calls_llm(self):
        client = FakeClient()
        scorer = Scorer(self.config, client, self.cache)
        for abstract in ("", " \n\t", None):
            with self.subTest(abstract=abstract), self.assertRaises(MissingAbstractError):
                scorer.score(replace(self.paper, abstract=abstract))
        self.assertEqual(client.calls, [])

    def test_missing_key_has_safe_error_and_never_calls_llm(self):
        client = FakeClient()
        with patch.dict(os.environ, {"OPENAI_API_KEY": " "}):
            with self.assertRaisesRegex(MissingAPIKeyError, "OPENAI_API_KEY"):
                Scorer(self.config, client, self.cache).score(self.paper)
        self.assertEqual(client.calls, [])

    def test_authentication_error_keeps_status_and_is_not_cached(self):
        client = FakeClient(HttpError("HTTP 401 from api.openai.com.", status=401))
        with self.assertRaises(HttpError) as caught:
            Scorer(self.config, client, self.cache).score(self.paper)
        self.assertEqual(caught.exception.status, 401)
        self.assertFalse(self.cache.exists())

    def test_openai_incomplete_and_refusal_are_never_cached(self):
        responses = [
            {"status": "incomplete", "output": openai_response()["output"]},
            {"status": "failed", "error": {"message": "some provider error"}},
            {"status": "completed", "output": [{
                "type": "message", "content": [{"type": "refusal", "refusal": "Refused."}],
            }]},
            {"status": "completed", "output": []},
        ]
        for response in responses:
            with self.subTest(response=response):
                client = FakeClient(response)
                with self.assertRaises(ScoringError):
                    Scorer(self.config, client, self.cache).score(self.paper)
                self.assertFalse(self.cache.exists())

    def test_anthropic_truncation_refusal_and_invalid_content_are_never_cached(self):
        config = deepcopy(self.config)
        config["llm"]["provider"] = "anthropic"
        responses = [
            {"stop_reason": "max_tokens", "content": anthropic_response()["content"]},
            {"stop_reason": "refusal", "content": []},
            {"stop_reason": "end_turn", "content": [{"type": "refusal"}]},
            {"stop_reason": "end_turn", "content": []},
            {"stop_reason": "end_turn", "content": [None]},
        ]
        for response in responses:
            with self.subTest(response=response), self.assertRaises(ScoringError):
                Scorer(config, FakeClient(response), self.cache).score(self.paper)
            self.assertFalse(self.cache.exists())

    def test_malformed_json_or_wrong_fields_scores_and_strings_are_rejected(self):
        invalid_results = [
            "not JSON",
            json.dumps({**ASSESSMENT, "relevance": True}),
            json.dumps({**ASSESSMENT, "relevance": 9.0}),
            json.dumps({**ASSESSMENT, "relevance": "9"}),
            json.dumps({**ASSESSMENT, "relevance": 0}),
            json.dumps({**ASSESSMENT, "relevance": 11}),
            json.dumps({**ASSESSMENT, "extra": "unrequested"}),
            json.dumps({"relevance": 9}),
            json.dumps({**ASSESSMENT, "methodology": "   "}),
            json.dumps({**ASSESSMENT, "key_findings": ["wrong type"]}),
            json.dumps([ASSESSMENT]),
        ]
        for text in invalid_results:
            response = openai_response()
            response["output"][0]["content"][0]["text"] = text
            with self.subTest(text=text), self.assertRaises(ScoringError):
                Scorer(self.config, FakeClient(response), self.cache).score(self.paper)
            self.assertFalse(self.cache.exists())

    def test_invalid_configuration_fails_before_a_paid_request(self):
        configs = [
            {"llm": {"provider": "unknown"}},
            {"llm": {"model": " "}},
            {"llm": {"max_output_tokens": 12}},
            {"llm": {"max_output_tokens": True}},
            {"llm": []},
            {"interests": []},
            {"interests": "CHO"},
            {"interests": ["CHO", " "]},
        ]
        for config in configs:
            with self.subTest(config=config), self.assertRaises(ScoringError):
                Scorer(config, FakeClient(), self.cache)


if __name__ == "__main__":
    unittest.main()
