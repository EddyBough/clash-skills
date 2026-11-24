#!/usr/bin/env python3
"""Experimental passage-level lexical ranking, not a semantic classifier.

This module is deliberately independent of audit.py and its SQLite ledger.
No verdicts are inferred and no candidate pair is dropped.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import scan

# Keep negations: "allow" vs "never allow" may reverse an instruction.
MIN_SHARED_TERMS = 2
MAX_PASSAGE_CHARS = 500


def passage_terms(text: str) -> frozenset[str]:
    """Normalize terminal punctuation in passage text, retaining substantive terms."""
    normalized = {term.strip(".+#_-") for term in scan.tokenize(text)}
    return frozenset(term for term in normalized if len(term) > 2 and term not in scan.STOPWORDS)


@dataclass(frozen=True)
class Passage:
    text: str
    terms: frozenset[str]


def passages(body: str) -> list[Passage]:
    """Produce short passages with no external NLP dependencies.

    Split along paragraph/list boundaries first, then sentence punctuation.
    This prototype is optimized for prose, not every Markdown/code layout.
    """
    parts: list[str] = []
    for block in re.split(r"\n\s*\n|\n(?=\s*[-*]\s+|\s*\d+\.\s+)", body):
        block = re.sub(r"(?m)^\s{0,3}(?:#{1,6}\s*|[-*]\s+|\d+\.\s+)", "", block).strip()
        if not block:
            continue
        for sentence in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", block):
            sentence = " ".join(sentence.split())
            if not sentence:
                continue
            # Overlong passages are chunked; no tokens are silently discarded.
            for start in range(0, len(sentence), MAX_PASSAGE_CHARS):
                part = sentence[start:start + MAX_PASSAGE_CHARS]
                if part.strip():
                    parts.append(part)
    return [Passage(text=p, terms=passage_terms(p)) for p in parts]


def passage_similarity(a: Passage, b: Passage) -> float:
    common = len(a.terms & b.terms)
    if common < MIN_SHARED_TERMS:
        return 0.0
    union = len(a.terms | b.terms)
    return common / union if union else 0.0


def best_passage_match(body_a: str, body_b: str) -> dict:
    pa, pb = passages(body_a), passages(body_b)
    best_score = 0.0
    best_a = ""
    best_b = ""
    for a in pa:
        for b in pb:
            score = passage_similarity(a, b)
            if score > best_score:
                best_score, best_a, best_b = score, a.text, b.text
    return {
        "passage_score": round(best_score, 6),
        "passage_a": best_a,
        "passage_b": best_b,
    }


def rank_fixture(skills: list[dict]) -> dict:
    """Rank all pairs with both methods; ground truth is never read here."""
    items = []
    for i, a in enumerate(skills):
        for b in skills[i + 1:]:
            pair = {
                "a": a["id"], "b": b["id"],
                "full_body_chars": len(a["body"]) + len(b["body"]),
                "metadata_score": round(
                    len(set(scan.tokenize(a["name"] + " " + a["description"])) &
                        set(scan.tokenize(b["name"] + " " + b["description"]))) /
                    max(1, len(set(scan.tokenize(a["name"] + " " + a["description"])) |
                               set(scan.tokenize(b["name"] + " " + b["description"])))),
                    6,
                ),
                **best_passage_match(a["body"], b["body"]),
            }
            items.append(pair)
    def ranked(key: str):
        return sorted(items, key=lambda p: (-p[key], p["a"], p["b"]))
    return {"metadata": ranked("metadata_score"), "passages": ranked("passage_score")}


def evaluate(fixture: dict, limits: list[int]) -> dict:
    skills = fixture["skills"]
    ids = [s["id"] for s in skills]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate skill IDs in fixture")
    if len(ids) < 2:
        raise ValueError("At least two skills required")
    known = set(ids)
    positives = set()
    for p in fixture["known_related_pairs"]:
        if len(p) != 2 or p[0] not in known or p[1] not in known or p[0] == p[1]:
            raise ValueError(f"Bad ground-truth pair: {p}")
        positives.add(tuple(sorted(p)))
    if not positives:
        raise ValueError("Fixture needs known related pairs")
    results = rank_fixture(skills)
    output = {
        "fixture": fixture.get("title", "untitled"),
        "skill_count": len(skills),
        "pair_count": len(results["metadata"]),
        "known_related_pair_count": len(positives),
        "note": "Lexical ranking benchmark only. Not LLM verdict accuracy or real billed tokens.",
        "methods": {},
    }
    for method, ranked in results.items():
        summary = {"top_k": {}, "ranked": ranked}
        for k in limits:
            selected = ranked[:k]
            found = sorted([list((p["a"], p["b"])) for p in selected
                            if tuple(sorted((p["a"], p["b"]))) in positives])
            summary["top_k"][str(k)] = {
                "selected": len(selected),
                "related_found": len(found),
                "recall": round(len(found) / len(positives), 4),
                "precision": round(len(found) / len(selected), 4) if selected else 0,
                "related_pairs": found,
                "full_body_chars_for_selected_pairs": sum(p["full_body_chars"] for p in selected),
                "selected_passage_chars": sum(
                    len(p["passage_a"]) + len(p["passage_b"]) for p in selected
                ) if method == "passages" else None,
                "selected_with_no_lexical_passage_match": sum(
                    p["passage_score"] == 0 for p in selected
                ) if method == "passages" else None,
            }
        output["methods"][method] = summary
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Standalone sentence/passage vs metadata ranking benchmark")
    parser.add_argument("--fixture", type=Path, default=Path(__file__).resolve().parents[1] /
                        "tests/fixtures/passage_benchmark.json")
    parser.add_argument("--top-k", default="5,10,20", help="Comma-separated ranking cutoffs")
    parser.add_argument("--summary-only", action="store_true", help="Omit ranked pair details")
    args = parser.parse_args()
    limits = [int(x) for x in args.top_k.split(",")]
    if not limits or any(x <= 0 for x in limits):
        parser.error("--top-k values must be positive integers")
    data = json.loads(args.fixture.read_text(encoding="utf-8"))
    result = evaluate(data, limits)
    if args.summary_only:
        for method in result["methods"].values():
            method.pop("ranked")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
