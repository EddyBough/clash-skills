# How ClashSkills works

> A code-aligned walkthrough of the **current implementation**, not a description of planned features.

[← README](../README.md) · [Agent protocol](../SKILL.md) · [Ranking research](EXPERIMENT_PASSAGE_RANKING.md)

## One-minute mental model

**Python discovers and schedules. The host agent interprets. SQLite verifies provenance and remembers accepted judgments.**

~~~mermaid
flowchart LR
  A["SKILL.md files"] --> B["scan.py<br/>paths + metadata"]
  B --> C["audit.py<br/>pairs + priority"]
  C --> D["SQLite<br/>pending queue"]
  D --> E["next<br/>full source texts"]
  E --> F["Host LLM<br/>semantic verdict"]
  F --> G["submit<br/>ticket + quote + hash checks"]
  G --> D
  D --> H["report<br/>table / markdown / JSON"]
~~~

There is **no embedded embedding model, vector database, RAG system, standalone model API client or daemon**.

## 1. File discovery — scan.py

New audits inspect native skill directories for the chosen host, not the whole project: Cursor (`.agents/skills`, `.cursor/skills`), Codex (project `.agents/skills`, user `.agents/skills` and `$CODEX_HOME/skills`), Claude Code (`.claude/skills`), or generic (`.agents/skills`). User and project directories are resolved separately. Additional directories are opt-in via `--source`, including plugin-managed skills when their active path is known.

Within selected roots, the scanner recursively finds `SKILL.md` and skips generated/dependency directories such as `node_modules`, `.git`, `.next`, `dist` and `build`. Paths are de-duplicated. Host-native discovery is only an inventory, not proof the host loaded a skill. Existing ledgers without a host selector preserve the earlier search scope.

Each discovered file supplies:

| Field | Role |
| --- | --- |
| Path/source | Where this copy was found |
| Name | Frontmatter `name`, or parent directory as fallback |
| Description | Frontmatter `description`, when present |
| Tokens | Lowercased, deduplicated lexical terms from name + description |

The frontmatter parser is intentionally lightweight: it is **not** full YAML support. Unreadable inputs become discovery warnings and prevent a clean completeness claim.

**Discovered ≠ active.** A file in an upstream plugin cache may never be loaded by the host agent. Identifying such a file is not proof of a runtime conflict.

## 2. Candidate pairs and review order

For `n` discovered skills the default exhaustive mode generates `n × (n − 1) / 2` pairs. For 100 skills, that is **4,950**.

Python computes the Jaccard similarity of token sets extracted from the **name + description**:

~~~text
Jaccard(A, B) = |tokens(A) ∩ tokens(B)| / |tokens(A) ∪ tokens(B)|
~~~

Examples: 0 means no shared terms; 1 means the token sets are identical. **Neither value is a semantic verdict.**

The persistent audit orders pairs using the following tiers:

| Priority | Rule | Meaning |
| ---: | --- | --- |
| 0 | Same normalized name | Candidate copies of one named skill |
| 1 | Identical instruction-body SHA-256 | Candidate duplicate content under different names |
| 2 | Higher metadata Jaccard first | Other likely related skills |
| — | Remaining pairs | Still present: not marked compatible |

Within a tier, priority score and stable pair ID determine the order.

An experimental `--mode filtered` can reduce the number of candidates but **may miss true collisions**. It is not the default and its independent recall has not been established.

## 3. Ledger lifecycle — audit.py + SQLite

The standard flow is `init → next → submit → report`.

~~~mermaid
stateDiagram-v2
  [*] --> PENDING: pair discovered
  PENDING --> REVIEWED: accepted, source-backed submission
  PENDING --> BLOCKED: unreadable / over size limit
  BLOCKED --> PENDING: retry after issue resolved
  REVIEWED --> PENDING: a source changes
~~~

The ledger stores a stable pair ID, source paths, file hashes, priority, review status and (if accepted) verdict, source excerpts, rationale, confidence and review timestamp.

### init

Initializes or resumes the audit for a specific **project root and profile**. If the scope differs from the existing ledger configuration, the command rejects the change instead of mixing incompatible audit runs.

### next

Selects the highest-priority pending pair by default (up to five with `--limit`), **re-reads** both original files, verifies their SHA-256 hashes, checks the size/body requirements and issues a ticket. The response contains both **complete texts** so the coding agent can evaluate context, exceptions and conditions.

If a pair cannot be issued, it is marked **BLOCKED**, not compatible.

### Host LLM judgment

The coding agent examines both full instruction bodies as **untrusted data**. It assesses:

- Could both skills govern the same task?
- Are the instructions substantially identical?
- Do they share a meaningful responsibility?
- Are any requirements incompatible *in that shared situation*?
- Are differences actually complementary or context-dependent?

It must select one of `DUPLICATE`, `OVERLAP`, `CONFLICT` or `COMPATIBLE`. The LLM also supplies the shared context, excerpts from both instruction bodies, reasoning and a confidence label.

### submit

The ledger accepts a verdict only when:

1. The pair is active, pending and was issued by `next`.
2. Its ticket matches.
3. Both source files still match their recorded hashes.
4. Both quoted excerpts occur **verbatim in the instruction bodies**, not only frontmatter, and meet length requirements.
5. The decision has valid verdict, confidence, shared scope and rationale fields.

Submission uses a database transaction; rejected submissions do not become reviewed decisions. Accepted decisions persist across agent sessions.

> [!IMPORTANT]
> **Evidence-checked is not accuracy-certified.** The ledger proves that cited snippets existed in the expected files when accepted. It cannot prove the LLM attended to every word, interpreted the rule correctly, or assigned the right verdict.

### Source changes and refresh

Commands refresh file discovery and SHA-256 metadata. If a source changes, previous verdicts for affected pairs return to **PENDING**. Accepted verdicts for unchanged pairs survive. The ledger keeps its own state **outside** the audited project (typically `~/.local/state/clash-skills/`).

Files can be read multiple times: once for discovery metadata, again for hashing and on later `next` / `submit` operations. Full texts are **not** globally cached for all host LLM comparisons. Repeated LLM context delivery is one contributor to audit cost.

## 4. Reporting

`audit.py status` and `audit.py report` calculate coverage from the SQLite ledger. Counts are **not** inferred from the agent's conversational memory.

| Format | Best for |
| --- | --- |
| `report --format table` | Final user-facing table and honest coverage |
| `report --format markdown` | Per-finding quoted evidence, paths and rationale |
| `report` | Stable JSON fields for integrations |

The report distinguishes **PENDING**, **BLOCKED** and **REVIEWED**, and only counts accepted decisions as reviewed. A scan over all files is **not** an exhaustive semantic audit.

## 5. Cost and correctness

| Property | Current behavior | Limitation |
| --- | --- | --- |
| Discovery and ordering | Deterministic Python | Depends on filesystem visibility and frontmatter quality |
| Semantic classification | Existing host LLM | Model-dependent judgment can be wrong |
| Evidence | Exact passages + SHA-256 + ticket | Verifies provenance, not reasoning correctness |
| Continuation | Persistent SQLite | Large audits can require many sessions |
| Exhaustiveness | All pairs retained by default | Quadratic count, potentially expensive in host-model tokens |
| Agent activity | On-disk sources with host context | No independent runtime co-activation proof |

In a local observational test, the current metadata-based ranking found **16 already-known flagged relationships within its first 25 comparisons**, versus 13 for an experimental passage-based ranking. **These known findings were originally discovered with the current ranking**: the test is biased and cannot establish performance on unseen conflicts or quantify token savings.

Reproduce the ranking-only benchmark on your own already-initialized local ledger:

~~~bash
python3 ~/.agents/skills/clash-skills/experiments/real_skill_benchmark.py \
  --root . --profile cursor --format table
~~~

No LLM is called and the benchmark opens the existing SQLite ledger in read-only mode. See [experimental methodology](EXPERIMENT_PASSAGE_RANKING.md) for synthetic fixtures and failure cases.

## 6. Threat model

The scanned `SKILL.md` files may contain instructions intended for a different task—or malicious attempts to override the auditor. The agent protocol says to treat **all audited file instructions as evidence**, not executable commands.

ClashSkills does not modify audited skills, agent rules, or project configuration. It does write its own ledger under the user's state directory, including paths and excerpts. Do not publish raw ledgers without inspecting and redacting them.

Host-model prompt-injection resilience has **not** been formally proven.

---

For detailed command usage, see the [README](../README.md#manual-workflow). For the actual host-agent rules, read [SKILL.md](../SKILL.md).
