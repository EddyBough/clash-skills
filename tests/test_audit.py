import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

AUDIT = Path(__file__).resolve().parents[1] / "scripts" / "audit.py"


class AuditLedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base / "project"
        self.root.mkdir()
        self.state = self.base / "state" / "ledger.sqlite3"
        self.skill("policy-one", "Database migrations", "Always ask permission before deleting a database column.")
        self.skill("policy-two", "Application maintenance", "Delete unused columns automatically during maintenance.")
        self.cli("init", "--no-global")

    def skill(self, name, desc, body):
        p = self.root / ".agents" / "skills" / name / "SKILL.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"---\nname: {name}\ndescription: {desc}\n---\n# {name}\n{body}\n", encoding="utf-8")
        return p

    def cli(self, *args, payload=None, ok=True):
        cmd = [sys.executable, str(AUDIT), "--root", str(self.root), "--state", str(self.state), *args]
        result = subprocess.run(cmd, input=json.dumps(payload) if payload is not None else None,
                                text=True, capture_output=True)
        if ok:
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertTrue(result.stderr.strip(), f'No stderr; returncode={result.returncode}, stdout={result.stdout!r}')
        try:
            return json.loads(result.stderr)
        except json.JSONDecodeError:
            self.fail(f'Invalid error JSON: stderr={result.stderr!r}, stdout={result.stdout!r}')

    def cli_raw(self, *args):
        cmd = [sys.executable, str(AUDIT), "--root", str(self.root), "--state", str(self.state), *args]
        result = subprocess.run(cmd, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def decision(self, item, verdict="CONFLICT"):
        return {"pair_id": item["pair_id"], "ticket": item["ticket"],
                "verdict": verdict, "confidence": "HIGH",
                "shared_scope": "Modification des colonnes en production",
                "evidence_a": "Always ask permission before deleting a database column.",
                "evidence_b": "Delete unused columns automatically during maintenance.",
                "rationale": "Both instructions describe the same operation and require incompatible approval behavior."}

    def test_default_state_uses_clash_skills_namespace(self):
        state_home = self.base / "xdg-state"
        result = subprocess.run(
            [sys.executable, str(AUDIT), "--root", str(self.root),
             "--profile", "cursor", "init", "--no-global"],
            env={**os.environ, "XDG_STATE_HOME": str(state_home)},
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(list((state_home / "clash-skills").glob("*.sqlite3"))), 1)

    def test_pending_is_not_compatible(self):
        result = self.cli("status")
        self.assertEqual(result["candidate_pair_count"], 1)
        self.assertEqual(result["reviewed_pair_count"], 0)
        self.assertEqual(result["unreviewed_pair_count"], 1)
        self.assertEqual(result["verdict_counts"]["COMPATIBLE"], 0)
        self.assertFalse(result["exhaustive_audit_complete"])

    def test_issue_requires_body_evidence_and_persists(self):
        item = self.cli("next")["items"][0]
        self.assertIn("Always ask permission", item["skill_a"]["full_text"])
        self.assertEqual(self.cli("submit", payload={"decisions": [self.decision(item)]})["accepted_reviews"], 1)
        self.assertTrue(self.cli("status")["exhaustive_audit_complete"])
        self.assertEqual(self.cli("report")["findings"][0]["verdict"], "CONFLICT")

    def test_metadata_only_evidence_rejected(self):
        item = self.cli("next")["items"][0]
        d = self.decision(item)
        d["evidence_a"] = "Database migrations"
        error = self.cli("submit", payload={"decisions": [d]}, ok=False)
        self.assertIn("instruction body", error["error"])
        self.assertEqual(self.cli("status")["reviewed_pair_count"], 0)

    def test_no_fabricated_review(self):
        item = self.cli("next")["items"][0]
        d = self.decision(item)
        d["ticket"] = "fabricated"
        self.assertIn("ticket", self.cli("submit", payload={"decisions": [d]}, ok=False)["error"])
        self.assertEqual(self.cli("status")["reviewed_pair_count"], 0)

    def test_changed_content_invalidates_audit(self):
        item = self.cli("next")["items"][0]
        self.cli("submit", payload={"decisions": [self.decision(item)]})
        p = Path(item["skill_a"]["path"])
        p.write_text(p.read_text() + "A new requirement was added.\n")
        s = self.cli("status")
        self.assertEqual(s["reviewed_pair_count"], 0)
        self.assertEqual(s["pending_pair_count"], 1)

    def test_changed_content_invalidates_ticket(self):
        item = self.cli("next")["items"][0]
        p = Path(item["skill_b"]["path"])
        p.write_text(p.read_text() + "A new restriction.\n")
        self.assertIn("not issued", self.cli("submit", payload={"decisions": [self.decision(item)]}, ok=False)["error"])

    def test_new_skill_preserves_previous_review(self):
        item = self.cli("next")["items"][0]
        self.cli("submit", payload={"decisions": [self.decision(item)]})
        self.skill("another-skill", "Different subject", "Never run commands that might be destructive.")
        s = self.cli("status")
        self.assertEqual(s["candidate_pair_count"], 3)
        self.assertEqual(s["reviewed_pair_count"], 1)
        self.assertEqual(s["unreviewed_pair_count"], 2)

    def test_host_scope_is_saved_and_cannot_be_changed_in_same_ledger(self):
        cursor = self.root / ".cursor" / "skills" / "ui" / "SKILL.md"
        cursor.parent.mkdir(parents=True)
        cursor.write_text("---\nname: ui\ndescription: UI\n---\nInstructions\n")
        codex = self.root / ".codex" / "skills" / "codex-only" / "SKILL.md"
        codex.parent.mkdir(parents=True)
        codex.write_text("---\nname: codex-only\ndescription: Codex\n---\nInstructions\n")
        state = self.base / "state" / "cursor.sqlite3"
        command = [sys.executable, str(AUDIT), "--root", str(self.root),
                   "--state", str(state), "--profile", "cursor"]
        started = subprocess.run(command + ["init", "--no-global"], capture_output=True, text=True)
        self.assertEqual(started.returncode, 0, started.stderr)
        result = subprocess.run(command + ["status"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        status = json.loads(result.stdout)
        self.assertEqual(status["host"], "cursor")
        self.assertEqual(status["skills_found"], 3)
        self.assertEqual(status["candidate_pair_count"], 3)
        changed = subprocess.run(command + ["init", "--no-global", "--host", "codex"],
                                 capture_output=True, text=True)
        self.assertNotEqual(changed.returncode, 0)
        self.assertIn("Different audit scope", changed.stderr)

    def test_old_ledger_keeps_old_discovery(self):
        import sqlite3
        with sqlite3.connect(self.state) as conn:
            config = json.loads(conn.execute("SELECT value FROM meta WHERE key='config'").fetchone()[0])
            self.assertEqual(config["host"], "generic")
            config.pop("host")
            conn.execute("UPDATE meta SET value=? WHERE key='config'", (json.dumps(config),))
        self.assertEqual(self.cli("status")["host"], "legacy")
        self.assertEqual(self.cli("status")["skills_found"], 2)

    def test_missing_explicit_source_is_not_silently_skipped(self):
        source = self.base / "missing-plugin-skills"
        state = self.base / "state" / "explicit-source.sqlite3"
        result = subprocess.run(
            [sys.executable, str(AUDIT), "--root", str(self.root),
             "--state", str(state), "--profile", "cursor",
             "init", "--no-global", "--source", str(source)],
            text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("Explicit skill source must be an existing directory", result.stderr)
        self.assertFalse(json.loads(result.stderr)["success"])

    def test_missing_source_after_init_does_not_erase_existing_reviews(self):
        source = self.base / "plugin-skills"
        source.mkdir()
        plugin = source / "optional" / "SKILL.md"
        plugin.parent.mkdir()
        plugin.write_text(
            "---\nname: optional\ndescription: Optional plugin guidance\n---\n"
            "This optional plugin handles other technical workflows.\n", encoding="utf-8"
        )
        state = self.base / "state" / "with-source.sqlite3"
        command = [sys.executable, str(AUDIT), "--root", str(self.root),
                   "--state", str(state), "--profile", "cursor"]

        def run(*args):
            return subprocess.run(command + list(args), text=True, capture_output=True)

        initialized = run("init", "--no-global", "--source", str(source))
        self.assertEqual(initialized.returncode, 0, initialized.stderr)
        status = json.loads(run("status").stdout)
        self.assertEqual(status["skills_found"], 3)
        self.assertEqual(status["candidate_pair_count"], 3)

        item = json.loads(run("next").stdout)["items"][0]
        def body(text):
            return text.split("---\n", 2)[-1].strip()
        decision = {
            "pair_id": item["pair_id"], "ticket": item["ticket"],
            "verdict": "COMPATIBLE", "confidence": "LOW",
            "shared_scope": "Independent project operations",
            "evidence_a": body(item["skill_a"]["full_text"]),
            "evidence_b": body(item["skill_b"]["full_text"]),
            "rationale": "The instructions are compatible when these independent responsibilities are considered.",
        }
        # Submit decisions via stdin; the CLI never changes the audited source.
        accepted = subprocess.run(command + ["submit"], input=json.dumps({"decisions": [decision]}),
                                  text=True, capture_output=True)
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        self.assertEqual(json.loads(run("status").stdout)["reviewed_pair_count"], 1)

        shutil.rmtree(source)
        for action in ("status", "report", "next"):
            with self.subTest(command=action):
                blocked = run(action)
                self.assertEqual(blocked.returncode, 2)
                self.assertIn("Explicit skill source must be an existing directory", blocked.stderr)
                self.assertFalse(json.loads(blocked.stderr)["success"])

        plugin.parent.mkdir(parents=True)
        plugin.write_text(
            "---\nname: optional\ndescription: Optional plugin guidance\n---\n"
            "This optional plugin handles other technical workflows.\n", encoding="utf-8"
        )
        restored = run("status")
        self.assertEqual(restored.returncode, 0, restored.stderr)
        restored_status = json.loads(restored.stdout)
        self.assertEqual(restored_status["reviewed_pair_count"], 1)
        self.assertEqual(restored_status["candidate_pair_count"], 3)

    def test_scope_change_rejected(self):
        self.assertIn("Different audit scope", self.cli("init", ok=False)["error"])

    def test_state_inside_project_rejected(self):
        inside = self.root / "audit.sqlite3"
        result = subprocess.run([sys.executable, str(AUDIT), "--root", str(self.root),
                                 "--state", str(inside), "init", "--no-global"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertFalse(inside.exists())

    def test_more_than_15_still_exhaustive(self):
        for i in range(14):
            self.skill(f"extra-{i}", f"desc-{i}", f"Instruction {i}: keep these systems separate.")
        s = self.cli("status")
        self.assertEqual(s["skills_found"], 16)
        self.assertEqual(s["candidate_pair_count"], 120)
        self.assertEqual(s["verdict_counts"]["COMPATIBLE"], 0)

    def test_overlarge_pair_blocked_not_compatible(self):
        r = self.cli("next", "--max-chars", "100")
        self.assertEqual(r["items"], [])
        self.assertEqual(r["status"]["blocked_pair_count"], 1)
        self.assertFalse(r["status"]["exhaustive_audit_complete"])

    def test_unreadable_prevents_completion(self):
        p = self.root / ".agents" / "skills" / "bad" / "SKILL.md"
        p.parent.mkdir()
        p.write_bytes(b"\xff\xfe")
        s = self.cli("status")
        self.assertEqual(len(s["discovery_warnings"]), 1)
        self.assertFalse(s["exhaustive_audit_complete"])

    def test_reissue_ticket_after_interruption(self):
        first = self.cli("next")["items"][0]
        second = self.cli("next")["items"][0]
        self.assertEqual(first["pair_id"], second["pair_id"])
        self.assertEqual(first["ticket"], second["ticket"])
        self.assertEqual(self.cli("submit", payload={"decisions": [self.decision(first)]})["accepted_reviews"], 1)

    def test_blocked_can_retry_larger_budget(self):
        self.cli("next", "--max-chars", "100")
        self.assertEqual(self.cli("status")["blocked_pair_count"], 1)
        resumed = self.cli("next", "--max-chars", "10000")
        self.assertEqual(len(resumed["items"]), 1)
        self.assertEqual(resumed["status"]["blocked_pair_count"], 0)

    def test_same_name_priority_over_unrelated_pairs(self):
        self.skill("policy-one-copy", "Unrelated metadata", "Every user action must have a recorded approval.")
        a = self.root / ".agents" / "skills" / "policy-one-copy" / "SKILL.md"
        text = a.read_text()
        a.write_text(text.replace("name: policy-one-copy", "name: policy-one"))
        item = self.cli("next")["items"][0]
        self.assertEqual(item["review_priority"], "same-name")
        self.assertEqual({item["skill_a"]["name"], item["skill_b"]["name"]}, {"policy-one"})
        self.assertEqual(self.cli("status")["candidate_pair_count"], 3)

    def test_same_instruction_body_priority_over_metadata(self):
        copy = self.skill("policy-copy", "Unrelated subject", "Always ask permission before deleting a database column.")
        original = self.root / ".agents" / "skills" / "policy-one" / "SKILL.md"
        body = original.read_text().split("---\n", 2)[-1]
        copy.write_text("---\nname: policy-copy\ndescription: Unrelated subject\n---\n" + body)
        item = self.cli("next")["items"][0]
        self.assertEqual(item["review_priority"], "same-instruction-body")
        self.assertEqual(self.cli("status")["candidate_pair_count"], 3)

    def test_existing_reviews_survive_priority_migration(self):
        first = self.cli("next")["items"][0]
        self.cli("submit", payload={"decisions": [self.decision(first)]})
        import sqlite3
        with sqlite3.connect(self.state) as conn:
            conn.execute("UPDATE meta SET value=? WHERE key='manifest_digest'", (json.dumps("old-version"),))
            conn.execute("UPDATE pairs SET priority_tier=2, priority_score=0, priority_reason='old-routing'")
        s = self.cli("status")
        self.assertEqual(s["reviewed_pair_count"], 1)
        self.assertEqual(s["unreviewed_pair_count"], 0)
        self.assertTrue(s["exhaustive_audit_complete"])

    def test_prioritization_preserves_all_pairs_at_scale(self):
        self.skill("policy-one-copy", "Different description", "A brand new instruction body for an unrelated role.")
        copy = self.root / ".agents" / "skills" / "policy-one-copy" / "SKILL.md"
        copy.write_text(copy.read_text().replace("name: policy-one-copy", "name: policy-one"))
        for i in range(97):
            self.skill(f"extra-{i:02d}", f"description-{i}", f"Keep these systems completely separate, rule {i}.")
        s = self.cli("status")
        self.assertEqual(s["skills_found"], 100)
        self.assertEqual(s["candidate_pair_count"], 4950)
        self.assertEqual(s["reviewed_pair_count"], 0)
        item = self.cli("next")["items"][0]
        self.assertEqual(item["review_priority"], "same-name")
        self.assertEqual(self.cli("status")["unreviewed_pair_count"], 4950)

    def test_readable_report_is_truthful_when_incomplete(self):
        text = self.cli_raw("report", "--format", "markdown")
        self.assertIn("INCOMPLET", text)
        self.assertIn("0/1 paires examinées", text)
        self.assertIn("1 paire reste", text)
        self.assertIn("Aucune collision confirmée", text)
        self.assertIn("non examinées ne sont pas considérées compatibles", text)

    def test_readable_report_shows_accepted_evidence(self):
        item = self.cli("next")["items"][0]
        self.cli("submit", payload={"decisions": [self.decision(item)]})
        text = self.cli_raw("report", "--format", "markdown")
        self.assertIn("TERMINÉ", text)
        self.assertIn("Conflit —", text)
        self.assertIn("Always ask permission before deleting a database column.", text)
        self.assertIn("Delete unused columns automatically during maintenance.", text)
        self.assertIn("1/1 paires examinées", text)

    def test_table_report_is_short_and_truthful(self):
        text = self.cli_raw("report", "--format", "table")
        self.assertIn("| Résultat | Nombre |", text)
        self.assertIn("| **Non vérifiées** | **1** |", text)
        self.assertIn("INCOMPLET", text)
        self.assertIn("Aucun problème confirmé", text)
        self.assertIn("| Bloquées | 0 |", text)
        self.assertIn("Périmètre :", text)
        self.assertNotIn("CONFLICT — HIGH", text)

    def test_table_report_shows_accepted_conflict(self):
        item = self.cli("next")["items"][0]
        self.cli("submit", payload={"decisions": [self.decision(item)]})
        text = self.cli_raw("report", "--format", "table")
        self.assertIn("TERMINÉ", text)
        self.assertIn("| **Conflit** |", text)
        self.assertIn("policy-one", text)
        self.assertIn("policy-two", text)
        self.assertIn("| Conflits signalés | 1 |", text)
        self.assertIn("| Verdict | Skills concernés | Situation et risque | Confiance du LLM | Action suggérée |", text)
        self.assertIn("Modification des colonnes en production", text)
        self.assertIn("| Élevée |", text)
        self.assertNotIn("Always ask permission before deleting a database column.", text)

    def test_table_report_labels_uncertain_findings(self):
        item = self.cli("next")["items"][0]
        decision = self.decision(item)
        decision["confidence"] = "LOW"
        self.cli("submit", payload={"decisions": [decision]})
        report = self.cli_raw("report", "--format", "table")
        self.assertIn("| **Conflit (à confirmer)** |", report)
        self.assertIn("| Faible — à confirmer |", report)
        self.assertIn("faible confiance nécessitent une vérification humaine", report)

    def test_json_report_keeps_existing_contract(self):
        report = self.cli("report")
        self.assertIn("exhaustive_audit_complete", report)
        self.assertEqual(report["reviewed_pair_count"], 0)
        self.assertEqual(report["candidate_pair_count"], 1)
        self.assertIsInstance(report["findings"], list)

    def test_100_skills_4950_pairs_not_reviewed_automatically(self):
        for i in range(98):
            self.skill(f"extra-{i:02d}", f"description-{i}", f"Instruction {i}: keep these systems completely separate.")
        s = self.cli("status")
        self.assertEqual(s["skills_found"], 100)
        self.assertEqual(s["candidate_pair_count"], 4950)
        self.assertEqual(s["reviewed_pair_count"], 0)
        self.assertEqual(s["verdict_counts"]["COMPATIBLE"], 0)
        self.assertFalse(s["exhaustive_audit_complete"])


if __name__ == "__main__":
    unittest.main()
