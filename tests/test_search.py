from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from literature_brief.config import load_config
from literature_brief.search import evaluate_paper, summarize_searches, validate_search_config


ROOT = Path(__file__).resolve().parents[1]


def configuration():
    return json.loads((ROOT / "config.json").read_text())


class FocusedSearchTests(unittest.TestCase):
    def setUp(self):
        self.search = configuration()["search"]

    def test_or_within_blocks_and_and_between_selected_blocks(self):
        result = evaluate_paper("DG44 uses OXPHOS", "SLC35B1 affects ATP supply.", self.search)
        self.assertIn("DG44", result["matched_blocks"]["host"])
        self.assertIn("OXPHOS", result["matched_blocks"]["mitochondria"])
        self.assertEqual(result["matched_combinations"], ["host_mitochondria_er_atp"])
        for text in ("CHO cells", "NRF1", "AXER", "SoNar", "CHO cells with NRF1"):
            with self.subTest(text=text):
                self.assertEqual(evaluate_paper(text, "", self.search)["matched_combinations"], [])

    def test_separate_combinations_do_not_require_all_six_blocks(self):
        cases = {
            "CHO cells NRF1 AXER": "host_mitochondria_er_atp",
            "CHO cells NRF1 AOX": "host_mitochondria_energy",
            "CHO cells NRF1 qP": "host_mitochondria_phenotype",
            "CHO cells AXER AOX": "host_er_atp_energy",
            "CHO cells AXER qP": "host_er_atp_phenotype",
            "CHO cells AOX qP": "host_energy_phenotype",
            "CHO cells PercevalHR": "host_biosensors",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(evaluate_paper(text, "", self.search)["matched_combinations"], [expected])

    def test_gene_symbols_and_short_terms_cannot_match_substrings(self):
        text = "CHOP cells NFE2L1 NRF10 NRF1A SLC35B10 AXER2 TFAM1 qPCR GEMcitabine SoNars ERAT40"
        self.assertEqual(evaluate_paper(text, "", self.search)["matched_blocks"], {})
        actual = evaluate_paper("CHO cells: SLC35B1, TFAM, qP, GEM, ERAT4.", "", self.search)
        self.assertIn("SLC35B1", actual["matched_blocks"]["er_atp"])
        self.assertIn("qP", actual["matched_blocks"]["phenotype"])

    def test_unicode_greek_digits_charges_and_hyphens_have_ascii_equivalents(self):
        unicode_text = "CHO‑K1 PGC‑1α Ca²⁺‑ATPase NADH/NAD⁺ ratio engineering ¹³C metabolic flux analysis"
        ascii_text = "CHO-K1 PGC1alpha Ca2+-ATPase NADH/NAD+ ratio engineering 13C metabolic flux analysis"
        unicode_result = evaluate_paper(unicode_text, "", self.search)
        ascii_result = evaluate_paper(ascii_text, "", self.search)
        self.assertEqual(unicode_result, ascii_result)
        self.assertIn("PGC-1α", unicode_result["matched_blocks"]["mitochondria"])
        self.assertIn("¹³C metabolic flux analysis", unicode_result["matched_blocks"]["phenotype"])
        for alias in ("PGC-1β", "PGC1beta", "PGC-1b"):
            with self.subTest(alias=alias):
                self.assertIn("mitochondria", evaluate_paper(alias, "", self.search)["matched_blocks"])

    def test_phrase_does_not_bridge_title_and_abstract(self):
        self.assertNotIn("host", evaluate_paper("Chinese hamster", "ovary cells", self.search)["matched_blocks"])
        self.assertIn("host", evaluate_paper("Chinese hamster ovary", "", self.search)["matched_blocks"])

    def test_nrf2_never_substitutes_for_gabpa(self):
        for alias in ("NRF2", "NRF-2", "NFE2L2", "NRF-2/NFE2L2"):
            with self.subTest(alias=alias):
                result = evaluate_paper("CHO cells " + alias, "Specific productivity increased.", self.search)
                self.assertNotIn("mitochondria", result["matched_blocks"])
                self.assertEqual(result["matched_combinations"], [])
        self.assertIn("mitochondria", evaluate_paper("GABPA (NRF-2)", "", self.search)["matched_blocks"])

    def test_redox_readout_alone_does_not_imply_biosensor_method(self):
        result = evaluate_paper("CHO cells NADH/NAD⁺ ratio engineering", "", self.search)
        self.assertIn("energy", result["matched_blocks"])
        self.assertNotIn("biosensors", result["matched_blocks"])
        self.assertNotIn("host_biosensors", result["matched_combinations"])
        self.assertIn("host_biosensors", evaluate_paper("CHO cells SoNar (NADH/NAD⁺)", "", self.search)["matched_combinations"])

    def test_hspa5_chaperone_alias_matches_without_atpase_phrase(self):
        result = evaluate_paper("CHO cells HSPA5 qP", "", self.search)
        self.assertIn("HSPA5", result["matched_blocks"]["er_atp"])
        self.assertIn("host_er_atp_phenotype", result["matched_combinations"])
        self.assertNotIn("er_atp", evaluate_paper("HSPA50", "", self.search)["matched_blocks"])

    def test_nrf1_collision_is_retained_with_explicit_identity_note(self):
        bare = evaluate_paper("CHO cells NRF1 qP", "", self.search)
        self.assertIn("host_mitochondria_phenotype", bare["matched_combinations"])
        self.assertIn("NFE2L1", bare["ambiguity_notes"][0])
        self.assertEqual(evaluate_paper("NFE2L1", "", self.search)["matched_blocks"], {})
        respiratory = evaluate_paper("CHO cells NRF-1 and mitochondrial biogenesis qP", "", self.search)
        self.assertEqual(respiratory["ambiguity_notes"], [])
        collision = evaluate_paper("CHO cells NRF1/NFE2L1 in mitochondrial stress qP", "", self.search)
        self.assertIn("NFE2L1", collision["ambiguity_notes"][0])

    def test_prc_ambiguity_is_visible_without_discarding_match(self):
        result = evaluate_paper("CHO cells PRC qP", "", self.search)
        self.assertIn("host_mitochondria_phenotype", result["matched_combinations"])
        self.assertIn("PRC is an ambiguous abbreviation", result["ambiguity_notes"][0])
        self.assertEqual(evaluate_paper("PRC affects mitochondrial respiration", "", self.search)["ambiguity_notes"], [])

    def test_summary_counts_overlapping_searches_once_per_paper_and_preserves_config(self):
        before = deepcopy(self.search)
        papers = [
            {"title": "CHO cells NRF1 AXER AOX qP", "abstract": "Mitochondrial biogenesis."},
            {"title": "CHO cells PercevalHR", "abstract": ""},
            {"title": "An unrelated paper", "abstract": ""},
        ]
        papers[0]["focused_search"] = evaluate_paper(papers[0]["title"], papers[0]["abstract"], self.search)
        summary = summarize_searches(papers, self.search)
        self.assertEqual(summary["matched_papers"], 2)
        self.assertEqual({combo["id"]: combo["match_count"] for combo in summary["combinations"]},
                         {combo["id"]: 1 for combo in self.search["combinations"]})
        self.assertEqual(summary["blocks"], self.search["blocks"])
        summary["blocks"][0]["terms"].append("changed")
        self.assertEqual(self.search, before)

    def test_absent_search_metadata_is_explicitly_empty(self):
        self.assertEqual(evaluate_paper("CHO cells AXER", "", None), {
            "matched_blocks": {}, "matched_combinations": [], "ambiguity_notes": [],
        })
        self.assertEqual(summarize_searches([], None), {"blocks": [], "combinations": [], "matched_papers": 0})


class SearchConfigurationTests(unittest.TestCase):
    def load(self, config):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(config))
            with patch.dict("os.environ", {}, clear=True):
                return load_config(path)

    def test_existing_configs_without_search_remain_valid(self):
        config = configuration()
        config.pop("search")
        self.assertNotIn("search", self.load(config))

    def test_default_config_has_six_blocks_and_seven_independent_combinations(self):
        config = self.load(configuration())
        self.assertEqual([block["id"] for block in config["search"]["blocks"]],
                         ["host", "mitochondria", "er_atp", "energy", "phenotype", "biosensors"])
        self.assertEqual(len(config["search"]["combinations"]), 7)

    def test_rejects_malformed_blocks_and_combination_references(self):
        valid = configuration()["search"]
        invalid = [[], {}, {"blocks": [], "combinations": []}]
        for field, value in (("id", "Bad-ID"), ("label", ""), ("terms", "NRF1"),
                             ("terms", [""]), ("terms", [123]), ("terms", ["---"])):
            candidate = deepcopy(valid)
            candidate["blocks"][0][field] = value
            invalid.append(candidate)
        duplicate_block = deepcopy(valid)
        duplicate_block["blocks"][1]["id"] = duplicate_block["blocks"][0]["id"]
        invalid.append(duplicate_block)
        for selected in (["host"], ["host", "unknown"], ["host", "host"], ["host", []]):
            candidate = deepcopy(valid)
            candidate["combinations"][0]["blocks"] = selected
            invalid.append(candidate)
        duplicate_combo = deepcopy(valid)
        duplicate_combo["combinations"][1]["id"] = duplicate_combo["combinations"][0]["id"]
        invalid.append(duplicate_combo)
        for search in invalid:
            with self.subTest(search=search), self.assertRaises(ValueError):
                validate_search_config(search)
            config = configuration()
            config["search"] = search
            with self.subTest(load=search), self.assertRaises(ValueError):
                self.load(config)


if __name__ == "__main__":
    unittest.main()
