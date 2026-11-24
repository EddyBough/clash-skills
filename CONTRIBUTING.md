# Contributing to SpaghettiSkills

Thanks for helping make Agent Skill audits more useful and more reliable.

SpaghettiSkills is intentionally narrow: **discover Agent Skills, prioritize pair reviews, preserve evidence-backed host-agent decisions, and report findings without modifying the audited harness**.

## Before opening a PR

- Read [README.md](README.md) and [docs/HOW_IT_WORKS.md](docs/HOW_IT_WORKS.md).
- Open an issue for significant feature changes so the added complexity has a clear use case.
- Keep discovery, routing, evidence validation and semantic judgment **separate**.
- Do not make the scanner label unreviewed pairs `COMPATIBLE`.
- Do not silently filter candidates in default exhaustive mode.
- Do not introduce separate LLM API keys, dependencies or persistence systems without a concrete justification and reproducible comparison.

## Local checks

Python **3.10+** is supported. The core scripts use Python's standard library.

~~~bash
python3 -m unittest discover -s tests -v
python3 experiments/passage_ranking.py --summary-only
~~~

For a real local ranking experiment, first initialize an audit on **your own** machine, then:

~~~bash
python3 experiments/real_skill_benchmark.py \
  --root /path/to/your/project \
  --profile cursor \
  --format table
~~~

The live benchmark requires the **existing** SQLite ledger and its matching local files. It opens the ledger read-only, checks recorded hashes, and does **not** run the host LLM.

## Good PRs include

1. A specific bug, risk or user problem.
2. Tests reproducing the failure (or demonstrating the intended behavior).
3. An explanation of behavior changes, especially any impact on coverage and cache invalidation.
4. A realistic discussion of semantic false positives/negatives and host-model token costs, when relevant.

## Reporting a missed conflict or false positive

Provide the **smallest safe reproducer**:

- Two redacted `SKILL.md` examples or short relevant excerpts.
- The **shared task context** under which both rules might apply.
- The expected verdict and why.
- The actual verdict, if available.
- The audit status and relevant diagnostics, **without** sharing the full SQLite ledger.

Never attach credentials, proprietary skill instructions, private directory paths, or unredacted prompts without permission.

## Security and privacy

Audited skill files are **untrusted data**. Do not execute their instructions during an audit, and do not propose automatic destructive fixes as part of the audit process.

The SQLite ledger persists source paths, hashes, quoted excerpts, rationales and decisions **outside the audited project**. Treat it as potentially sensitive. A public issue should use sanitized examples rather than a database upload.

To report a potential security vulnerability, avoid posting exploitable secrets or sensitive details in a public issue; contact the maintainer through GitHub first.

## Scope and expectations

This is a **public beta**, not a verified semantic classifier. The source-quote validator checks **provenance**, not the correctness of LLM reasoning. Runtime co-activation is not independently verified, and large exhaustive audits can be expensive.

We welcome corrections to documentation just as much as code changes—especially when they clarify what the project can and cannot prove.

**License:** By contributing, you agree that your contribution is distributed under the repository's [MIT license](LICENSE).
