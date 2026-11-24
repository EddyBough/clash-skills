# Experiment: passage-level lexical ranking

**Status: research only. No changes to `scripts/scan.py`, `scripts/audit.py`, SQLite state, or `SKILL.md`.**

## Question

Does looking for shared vocabulary in short `SKILL.md` instruction passages surface more genuinely related skill pairs in the first K comparisons than the current Jaccard score on names and descriptions?

## Reproduce

```bash
python3 experiments/passage_ranking.py --summary-only --top-k 5,10,20
python3 -m unittest discover -s tests -v
```

The fixture `tests/fixtures/passage_benchmark.json` is **small, synthetic and written for this experiment**. It contains four separately labeled positive skill pairs, including two deliberately difficult paraphrased cases, and same-topic but different-purpose controls. This is not an independently annotated benchmark or evidence of general accuracy.

The comparison uses the same deterministic tokenization as `scripts/scan.py`:

- **Baseline:** Jaccard over each pair's names and descriptions.
- **Experiment:** split instruction bodies by paragraphs/list items/sentences; find the **highest** Jaccard match among passages using at least two shared meaningful words.
- Rank **every** pair by score, never discard pairs or create `COMPATIBLE` verdicts.
- Calculate top-K recall and precision against the fixture's positive pairs, separately for each ranking.
- Display raw character counts as **context-volume proxies only**; they are not measured LLM tokens, billing, complete prompt sizes or semantic accuracy.

## Important failure modes

1. **Paraphrase gap:** `Always get permission before removing outdated information` versus `Purge obsolete entries autonomously` can share zero terms. Passage Jaccard cannot infer shared responsibility.
2. **False positive:** instructions that mention React and accessibility can refer to complementary rather than competing concerns. Lexical similarity is never a collision verdict.
3. **Missing context:** a short passage may omit a preceding exception, instruction hierarchy, or conditional. A host LLM would need surrounding context or full text before committing to a verdict.
4. **Splitting limitations:** Markdown code blocks, tables, headings, abbreviations and negations may not split cleanly. This prototype does not claim reliable general-purpose instruction chunking.
5. **Ranking bias:** synthetic positives chosen in the same project are inadequate to validate generalization.
6. **No measured cost:** character counts are not token accounting. A true token benchmark must run the same host model and account for repeated tool I/O, context caching and output.

## First reproducible result (synthetic fixture)

GitHub Actions ran the benchmark on November 24, 2026. There are 12 skill descriptions/instruction bodies, 66 possible pairs and 4 labeled relevant pairs. Results:

| K reviewed first | Baseline metadata matches / 4 | Passage matches / 4 |
| ---: | ---: | ---: |
| 5 | 1 (25%) | 2 (50%) |
| 10 | 1 (25%) | 2 (50%) |
| 20 | 1 (25%) | 2 (50%) |

The passage version improves ranking on this *constructed* fixture but still fails to surface both lexically dissimilar paraphrase cases within K=20. In the passage top 5, 2 of the 5 pairs had **no qualifying lexical passage match**, illustrating the brittleness of a fixed two-shared-terms rule.

**Do not interpret the reported excerpt character counts as token savings.** The experiment never invoked an LLM, and for zero-scoring pairs it emits no excerpt; such empty excerpts cannot support a verdict and would require fallback full-text reading. No precision/recall claim about LLM verdicts follows from these ranking results.

## Observational test on an installed skill corpus (24 November 2026)

A subsequent **local, read-only benchmark** compared ranking strategies using 100 installed skills and an existing audit ledger of 4,950 candidate pairs.

At the time of measurement, the ledger held **31 accepted verdicts**, including **16 previously flagged relationships**. The known positives were found while using the baseline prioritization, so the benchmark has **selection bias**.

| Pairs reviewed first | Existing name/body/metadata order | Experimental passage order |
| ---: | ---: | ---: |
| 10 | 10 / 16 | 10 / 16 |
| 25 | **16 / 16** | 13 / 16 |
| 50 | **16 / 16** | 13 / 16 |
| 100 | **16 / 16** | 13 / 16 |
| 250 | **16 / 16** | 15 / 16 |

The first 13 shared positions came from **same-name / identical-body priority tiers**, not the experimental lexical passage method.

Three previously identified overlaps moved back in the passage ranking: `benchmark-sandbox ↔ vercel-plugin-eval` (19 → 125), `env-vars ↔ vercel-cli` (24 → 162), and `plugin-audit ↔ vercel-plugin-eval` (20 → 1,510).

Some unrelated-looking pairs received a passage score of **1.0**, illustrating that a perfect lexical match of two short passages is not proof of semantic overlap.

**Outcome:** Do **not** replace production prioritization with the passage-ranking experiment on this evidence. This observational test does **not** establish unseen-collision recall, semantic classification quality or real host-model token savings. Reproduce with:

~~~bash
python3 experiments/real_skill_benchmark.py \
  --root /path/to/audited/project --profile cursor --format table
~~~

The project root/profile must match a prior audit ledger and the recorded local files must be present unchanged. The benchmark calls no LLM, never modifies skills and reads the ledger in read-only mode.

## Decision gate

Do **not** integrate the passage method into the production ledger on this result alone. Test with independently annotated, diverse real-world skills, especially synonyms and contradictory rules with little shared vocabulary. Measure top-K recall, false-positive burden and actual model token usage. Only adopt if gains justify higher implementation complexity and missed-context risk.
