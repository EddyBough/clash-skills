import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "experiments" / "passage_ranking.py"
spec = importlib.util.spec_from_file_location("passage_ranking", SCRIPT)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
assert spec.loader
spec.loader.exec_module(module)
FIXTURE = ROOT / "tests/fixtures/passage_benchmark.json"


class PassageRankingExperimentTests(unittest.TestCase):
    def test_exact_shared_terms_raise_passage_score(self):
        match = module.best_passage_match(
            "Always require user approval before deleting customer records.",
            "Delete customer records automatically without user approval.")
        self.assertGreater(match["passage_score"], 0)
        self.assertIn("customer records", match["passage_a"])
        self.assertIn("customer records", match["passage_b"])

    def test_lexical_synonym_blind_spot_is_visible(self):
        # No magic semantic understanding: this example should remain a miss.
        match = module.best_passage_match(
            "Always get permission before removing outdated information.",
            "Purge obsolete entries autonomously during scheduled maintenance.")
        self.assertEqual(match["passage_score"], 0.0)
        self.assertEqual(match["passage_a"], "")
        self.assertEqual(match["passage_b"], "")

    def test_rankings_include_all_pairs_without_verdicts(self):
        fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        ranked = module.rank_fixture(fixture["skills"])
        count = len(fixture["skills"]) * (len(fixture["skills"]) - 1) // 2
        self.assertEqual(len(ranked["metadata"]), count)
        self.assertEqual(len(ranked["passages"]), count)
        self.assertEqual(
            {tuple(sorted((p["a"], p["b"]))) for p in ranked["metadata"]},
            {tuple(sorted((p["a"], p["b"]))) for p in ranked["passages"]},
        )
        self.assertTrue(all("verdict" not in p for p in ranked["passages"]))

    def test_benchmark_does_not_claim_compatibility_for_zero_score(self):
        fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        result = module.evaluate(fixture, [5, 10, 20])
        self.assertEqual(result["pair_count"], 66)
        self.assertEqual(result["known_related_pair_count"], 4)
        self.assertGreaterEqual(result["methods"]["passages"]["top_k"]["20"]["related_found"], 1)
        self.assertTrue(any(x["passage_score"] == 0 for x in result["methods"]["passages"]["ranked"]))

    def test_passages_extract_multiple_bullets(self):
        body = "- Never delete backups automatically.\n- Require explicit approval for schema removal."
        items = module.passages(body)
        self.assertEqual(len(items), 2)
        self.assertIn("Never delete", items[0].text)


if __name__ == "__main__":
    unittest.main()
