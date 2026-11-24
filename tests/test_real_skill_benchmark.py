import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "experiments" / "real_skill_benchmark.py"
spec = importlib.util.spec_from_file_location("real_skill_benchmark", SCRIPT)
benchmark = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = benchmark
assert spec.loader is not None
spec.loader.exec_module(benchmark)


class RealSkillBenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "project"
        self.root.mkdir()
        self.state = Path(self.temp.name) / "ledger.sqlite3"
        self.a = self.add("frontend-style", "Visual interface", "Always require confirmation before deletion.")
        self.b = self.add("maintenance-tools", "Database care", "Delete records without confirmation.")
        self.c = self.add("ui-colors", "Typography, palette", "Use a consistent color palette and typography.")
        self.cli("init", "--no-global")

    def add(self, name, description, body):
        p = self.root / ".agents" / "skills" / name / "SKILL.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"---\nname: {name}\ndescription: {description}\n---\n{body}\n",
                     encoding="utf-8")
        return p

    def cli(self, *args):
        argv = [sys.executable, str(ROOT / "scripts" / "audit.py"),
                "--root", str(self.root), "--state", str(self.state), *args]
        run = subprocess.run(argv, capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        return json.loads(run.stdout)

    def test_complete_pair_coverage_without_writing_ledger(self):
        before = self.state.read_bytes()
        result = benchmark.benchmark(self.state, [1, 3], max_display=3)
        self.assertEqual(self.state.read_bytes(), before)
        self.assertEqual(result["skill_count"], 3)
        self.assertEqual(result["candidate_pair_count"], 3)
        self.assertEqual(result["known_positive_count"], 0)
        self.assertEqual(len(result["metadata"]), 2)
        self.assertIn("| Premières paires |", benchmark.render_table(result))
        self.assertIn("aucun LLM", benchmark.render_table(result))

    def test_reviewed_findings_appear_in_both_rankings(self):
        nxt = self.cli("next")["items"][0]
        decision = {
            "pair_id": nxt["pair_id"], "ticket": nxt["ticket"],
            "verdict": "CONFLICT", "confidence": "MEDIUM",
            "shared_scope": "Both govern file deletion behavior",
            "evidence_a": nxt["skill_a"]["full_text"].split("---\n")[-1].strip(),
            "evidence_b": nxt["skill_b"]["full_text"].split("---\n")[-1].strip(),
            "rationale": "Both skills give contradicting recommendations for the same user deletion workflow.",
        }
        run = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "audit.py"),
             "--root", str(self.root), "--state", str(self.state), "submit"],
            input=json.dumps({"decisions": [decision]}),
            capture_output=True, text=True,
        )
        self.assertEqual(run.returncode, 0, run.stderr)
        result = benchmark.benchmark(self.state, [1, 3])
        self.assertEqual(result["known_positive_count"], 1)
        self.assertEqual(result["metadata"][-1]["observed_positive_pairs_found"], 1)
        self.assertEqual(result["passages"][-1]["observed_positive_pairs_found"], 1)

    def test_stale_instructions_halt_benchmark_not_reclassify(self):
        self.a.write_text(self.a.read_text(encoding="utf-8") + "\nnew rule\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "changed since ledger snapshot"):
            benchmark.benchmark(self.state, [3])

    def test_one_file_with_bad_bytes_halts_benchmark(self):
        self.b.write_bytes(b"\xff")
        with self.assertRaisesRegex(ValueError, "changed since ledger snapshot"):
            benchmark.benchmark(self.state, [3])


if __name__ == "__main__":
    unittest.main()
