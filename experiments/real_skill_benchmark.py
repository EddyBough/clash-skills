#!/usr/bin/env python3
"""Read-only real-corpus ranking benchmark against an existing ClashSkills ledger.

This experiment reads SKILL.md files from the SAME machine where the audit was
initialized; it never issues review tickets, changes priorities or writes SQLite.
Known ledger verdicts are a biased *observed* sample, not independent ground truth.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import audit
import passage_ranking as passage

POSITIVE = {"CONFLICT", "DUPLICATE", "OVERLAP"}


def read_ledger(path: Path) -> tuple[dict, list[dict]]:
    if not path.is_file():
        raise ValueError(f"Audit ledger not found: {path}")
    # Read-only URI: no schema migration, no tickets, no new databases.
    conn = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        cfg = audit.meta_get(conn, "config")
        if not cfg:
            raise ValueError("No initialized audit found in this ledger")
        pairs = [dict(row) for row in conn.execute(
            "SELECT * FROM pairs WHERE active=1 ORDER BY pair_id"
        )]
        return cfg, pairs
    finally:
        conn.close()


def topk_summary(ordered: list[dict], observed: set[str], reviewed: set[str],
                 limits: list[int]) -> list[dict]:
    entries = []
    for limit in limits:
        selected = ordered[:limit]
        positives = sum(p["pair_id"] in observed for p in selected)
        checked = sum(p["pair_id"] in reviewed for p in selected)
        entries.append({
            "top_k": limit,
            "observed_positive_pairs_found": positives,
            "observed_positive_recall": round(positives / len(observed), 4) if observed else None,
            "already_reviewed_in_top_k": checked,
        })
    return entries


def benchmark(state: Path, top_k: list[int], max_display: int = 10) -> dict:
    config, rows = read_ledger(state)
    if not rows:
        raise ValueError("Ledger has no candidate pairs")
    files: dict[str, dict] = {}
    for row in rows:
        for suffix in ("a", "b"):
            path = row["path_" + suffix]
            if path in files:
                continue
            raw = Path(path).read_bytes()
            actual = hashlib.sha256(raw).hexdigest()
            expected = row["hash_" + suffix]
            if actual != expected:
                raise ValueError(f"Skill changed since ledger snapshot: {path}. "
                                 "Refresh the normal audit before benchmarking.")
            text = raw.decode("utf-8")
            body = audit.full_body(text)
            files[path] = {
                "text": body,
                "passages": passage.passages(body),
                "characters": len(body),
            }

    # Keep native same-name and same-body priorities in BOTH variants.
    results = []
    for row in rows:
        a = files[row["path_a"]]
        b = files[row["path_b"]]
        tier = row["priority_tier"]
        maxscore = 0.0
        best_a = ""
        best_b = ""
        # Read-only Python computation; no model or network calls.
        if tier >= 2:
            for x in a["passages"]:
                for y in b["passages"]:
                    score = passage.passage_similarity(x, y)
                    if score > maxscore:
                        maxscore = score
                        best_a, best_b = x.text, y.text
        results.append({
            "pair_id": row["pair_id"],
            "skill_a": row["name_a"],
            "skill_b": row["name_b"],
            "tier": tier,
            "metadata_score": row["priority_score"] if tier >= 2 else 1.0,
            "passage_score": round(maxscore, 6) if tier >= 2 else 1.0,
            "excerpt_a": best_a,
            "excerpt_b": best_b,
            "body_characters": a["characters"] + b["characters"],
            "excerpt_characters": len(best_a) + len(best_b),
            "status": row["status"],
            "verdict": row["verdict"],
        })

    def rank(p: dict, method: str) -> tuple:
        # Same-name and same-body first, faithfully preserving established tiers;
        # differences only inside metadata-tier pairs.
        val = p["metadata_score"] if method == "metadata" else p["passage_score"]
        return (p["tier"], -val, p["pair_id"])
    original = sorted(results, key=lambda p: rank(p, "metadata"))
    experimental = sorted(results, key=lambda p: rank(p, "passages"))

    positives = {p["pair_id"] for p in results if p["status"] == "REVIEWED"
                 and p["verdict"] in POSITIVE}
    reviewed = {p["pair_id"] for p in results if p["status"] == "REVIEWED"}
    known = []
    base_positions = {p["pair_id"]: i for i, p in enumerate(original, 1)}
    proposed_positions = {p["pair_id"]: i for i, p in enumerate(experimental, 1)}
    for p in results:
        if p["pair_id"] in positives:
            known.append({
                "skill_pair": f"{p['skill_a']} ↔ {p['skill_b']}",
                "verdict": p["verdict"],
                "current_rank": base_positions[p["pair_id"]],
                "passage_rank": proposed_positions[p["pair_id"]],
            })
    known.sort(key=lambda p: (p["current_rank"], p["skill_pair"]))
    preview = [{
        "skill_pair": f"{p['skill_a']} ↔ {p['skill_b']}",
        "passage_score": p["passage_score"],
        "metadata_rank": base_positions[p["pair_id"]],
        "passage_rank": proposed_positions[p["pair_id"]],
        "excerpt_a": p["excerpt_a"][:180],
        "excerpt_b": p["excerpt_b"][:180],
    } for p in experimental if p["tier"] >= 2][:max_display]
    return {
        "project_root": config["root"],
        "skill_count": len(files),
        "candidate_pair_count": len(rows),
        "already_reviewed_count": len(reviewed),
        "known_positive_count": len(positives),
        "note": ("Observational, biased known verdicts (originally found using current ranking). "
                 "This compares priority ordering, not semantic verdict precision or billed tokens."),
        "metadata": topk_summary(original, positives, reviewed, top_k),
        "passages": topk_summary(experimental, positives, reviewed, top_k),
        "known_findings": known,
        "top_passage_candidates": preview,
        "no_excerpt_in_top_k": [
            {"top_k": n, "pairs": sum(not p["excerpt_a"] and p["tier"] >= 2
                                      for p in experimental[:n])} for n in top_k
        ],
    }


def render_table(result: dict) -> str:
    lines = [
        "# ClashSkills — Comparaison sur skills locaux",
        "",
        f"**{result['skill_count']} skills · {result['candidate_pair_count']} paires · "
        f"{result['already_reviewed_count']} verdicts existants "
        f"(dont {result['known_positive_count']} collisions signalées)**",
        "",
        "| Premières paires | Classement actuel | Classement par passages |",
        "| ---: | ---: | ---: |",
    ]
    for baseline, proposed in zip(result["metadata"], result["passages"]):
        total = result["known_positive_count"]
        lines.append(
            f"| {baseline['top_k']} | "
            f"{baseline['observed_positive_pairs_found']}/{total} | "
            f"{proposed['observed_positive_pairs_found']}/{total} |"
        )
    lines.extend([
        "",
        "## Collisions déjà connues : à quel rang ?",
        "",
        "| Skills | Verdict du ledger | Rang actuel | Rang passages |",
        "| --- | --- | ---: | ---: |",
    ])
    for f in result["known_findings"]:
        lines.append(f"| {f['skill_pair'].replace('|', '/')} | {f['verdict']} | "
                     f"{f['current_rank']} | {f['passage_rank']} |")
    lines.extend([
        "",
        "## Premiers candidats issus des passages",
        "",
        "| Paire | Score lexical des passages | Rang actuel | Rang passages |",
        "| --- | ---: | ---: | ---: |",
    ])
    for p in result["top_passage_candidates"]:
        lines.append(f"| {p['skill_pair'].replace('|', '/')} | {p['passage_score']:.3f} | "
                     f"{p['metadata_rank']} | {p['passage_rank']} |")
    lines.extend([
        "",
        "**Lecture :** Un rang plus petit est une analyse plus précoce, "
        "**pas** la preuve d'un conflit. Les paires sans mots communs "
        "restent présentes, mais peuvent se retrouver en fin de liste.",
        "",
        "**Limites :** seuls les verdicts déjà acceptés sont utilisés comme repères. "
        "Ils favorisent le classement actuel, qui les a fait découvrir. "
        "Le benchmark n'appelle aucun LLM et ne mesure pas les économies réelles de tokens. "
        "Il ne modifie ni les skills ni le registre SQLite.",
    ])
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare real-skill passage ranking vs current metadata priority")
    parser.add_argument("--root", type=Path, default=Path("."), help="Same project root as existing audit")
    parser.add_argument("--profile", default="cursor", help="Existing audit profile")
    parser.add_argument("--state", type=Path, help="Explicit path to an existing SQLite ledger")
    parser.add_argument("--top-k", default="10,25,50,100,250",
                        help="Comma-separated review cutoffs")
    parser.add_argument("--format", choices=("table", "json"), default="table")
    args = parser.parse_args()
    try:
        limits = [int(v) for v in args.top_k.split(",")]
        if not limits or any(n <= 0 for n in limits):
            raise ValueError("All --top-k values must be positive integers")
        root = args.root.expanduser().resolve()
        state = args.state.expanduser().resolve() if args.state else audit.default_state(root, args.profile)
        result = benchmark(state, limits)
        if str(root) != result["project_root"]:
            raise ValueError("Project root does not match the initialized audit ledger")
        if args.format == "json":
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            print(render_table(result))
        return 0
    except (ValueError, OSError, sqlite3.Error, UnicodeDecodeError) as exc:
        print(f"Benchmark unavailable: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
