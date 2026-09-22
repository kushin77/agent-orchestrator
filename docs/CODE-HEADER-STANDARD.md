# Code header standard — the machine-parseable knowledge block

Every governed source file carries its knowledge **as structured data**, not as
prose a reader has to interpret: what this module is, which system and app it
belongs to, the CMR quality rung it is held to, which patterns it implements,
where it derives from, who owns it, which model tier authored it, what it
exposes, and what must never change. This document is the **schema authority**
for that block: `scripts/check-code-headers.sh` (the linter) and
`scripts/draft-code-header.sh` (the drafter) both READ this document rather than
carrying a copy of the field list, so the schema is declared exactly once
(ADR-0012: two things must not quietly both be authoritative).

## Why this exists (the RCA, measured)

Knowledge is not missing from this repository; it is **unstructured**.
`git log --oneline -- '**/*.py' '**/*.sh' | head -50` shows a codebase whose
authors did document their work, and a sample of existing headers
(`guardrails/chat/envelope.py:1-12`) shows real, careful prose — a contract, a
JSON shape, a fail-closed rule. What was never defined is a **schema**: no gate
could ask "does this file declare the pattern it implements?" because nothing
declared what such a declaration looks like, and no indexer could group files by
`solution_class` or `patterns` because those fields did not exist as fields.

Measured 2026-09-20 at `origin/master` `e66008e3`: **0 of 1181** in-scope files
carry a knowledge block, and `grep -rn 'code-header\|knowledge-block'` over the
tree returned nothing — the schema had never been written down anywhere.

The consumer already exists: the indexer's `context_pack.py` / `front_load()`
(group H, #1527/#1532) needs a header it can parse. This standard defines the
schema those tools read; it does not build a new indexer.

## The block

One mechanical convention, adapted only in the **comment decoration** of each
language. The block is delimited by two lines whose content is exactly
`---knowledge---`, and the lines between them are a **flat YAML mapping**.

| Language               | Where the block lives                          | Line decoration          |
|------------------------|------------------------------------------------|--------------------------|
| `.py`                  | inside the module docstring (first statement)  | none                     |
| `.sh`, `.tf`, `.yml`   | inside the file's leading comment banner        | `#` + one optional space |
| `.ts`, `.js`           | inside the file's leading block comment         | `*` + one optional space |

Parsing rules, in order — each is mechanical, and the linter refuses by name
when one fails:

1. strip the language's line decoration (at most one `#`, or one `*`, plus one
   optional following space) from every line that carries it;
2. find the lines whose stripped content is exactly `---knowledge---`;
3. there must be exactly **two**, in that order — an opening and a closing
   delimiter (`block-delimiter-count`);
4. for `.py` both delimiters must lie **inside the module docstring**, which
   must itself be parseable Python (`block-not-in-docstring`); for the comment
   languages both must lie in the file's **leading comment banner** — the run of
   blank/comment lines at the top of the file (`block-outside-banner`);
5. the payload between them must parse as a flat YAML mapping: one `key: value`
   per line, no nesting, no block sequences, `#` outside quotes starts a
   comment (`payload-unparsed`);
6. the resulting mapping is validated against the field table below.

The block is valid **YAML** (checked against PyYAML when it is importable), so an
indexer may use any YAML reader; the linter itself uses a dependency-free subset
reader so the gate is hermetic and offline.

### `.py` — inside the module docstring

```python
"""guardrails.chat.envelope — the grounding/citations envelope this lane consumes.

Long prose stays exactly as it is: the block is additive.

---knowledge---
module_id: guardrails.chat.envelope
system: guardrails
app: chat
solution_class: enterprise
patterns: [contract-first, fail-closed]
derives_from: null
owner_sme: security-sme
tier: L1
interfaces: [Envelope.parse, Envelope.fragments]
invariants: "an envelope this lane cannot interpret is undecidable, never empty"
gotchas: "the grounding lane imports nothing from here on purpose"
related: ["#504"]
do_not_duplicate: null
---knowledge---
"""




from __future__ import annotations
```

### `.sh` — leading comment banner

```bash
#!/usr/bin/env bash
# check-example.sh — one line on what this gate refuses.
#
# ---knowledge---
# module_id: scripts.check-example
# system: governance
# app: gates
# solution_class: enterprise
# patterns: [self-proving-gate, no-false-green]
# derives_from: scripts/check-shell-patterns.sh
# owner_sme: qa-sme
# tier: L1
# interfaces: [exit 0 OK, exit 1 NOT-OK, exit 2 CANNOT-ASSESS]
# invariants: "every refusal names the file, the field and the value"
# gotchas: ""
# related: ["#1535"]
# do_not_duplicate: ""
# ---knowledge---
set -u
```

### `.ts` / `.js` — leading block comment

```ts
/**
 * portal/src/view.ts — the console read model.
 *
 * ---knowledge---
 * module_id: portal.view
 * system: control-plane
 * app: portal
 * solution_class: faang
 * patterns: [read-model, additive-only]
 * derives_from: null
 * owner_sme: frontend-sme
 * tier: L1
 * interfaces: [PortalView, PortalView.props]
 * invariants: "hydration is additive only"
 * gotchas: ""
 * related: ["#1489"]
 * do_not_duplicate: null
 * ---knowledge---
 */
```

### `.tf` — leading comment banner

```hcl
# infra/example/main.tf — the flag-gated module shell.
#
# ---knowledge---
# module_id: infra.example
# system: iac
# app: example
# solution_class: enterprise
# patterns: [flag-gated-off, declared-never-clicked]
# derives_from: null
# owner_sme: iac-sme
# tier: L1
# interfaces: [module output "service_uri"]
# invariants: "every new resource ships enabled by default (AO-GR-6)"
# gotchas: ""
# related: ["#1535"]
# do_not_duplicate: null
# ---knowledge---
```

### `.yml` — leading comment banner

```yaml
# .github/ISSUE_TEMPLATE/epic.yml — one line on what this template collects.
#
# ---knowledge---
# module_id: github.issue-template.epic
# system: governance
# app: board
# solution_class: pattern
# patterns: [issue-first]
# derives_from: null
# owner_sme: pmo-sme
# tier: L0
# interfaces: [the epic issue body fields]
# invariants: ""
# gotchas: "the number field is a string, not an integer"
# related: ["#1535"]
# do_not_duplicate: null
# ---knowledge---
```

## The fields

This table is the machine-readable schema. `scripts/check-code-headers.sh` finds
it **by this header row** and validates every block against it, so a field added
here binds the gate in the same commit — and a field the gate validates but this
table does not declare is itself a refusal.

The issue that ordered this work names **13 fields** in its steps and **11** in
its acceptance line; this table defines all **13** it enumerates, and the
discrepancy is recorded here rather than silently resolved by dropping two.

`kind` is a closed vocabulary: `string` (non-empty scalar), `id` (a
`^[a-z][a-z0-9._-]*$` slug), `list` (a flow list of scalars, possibly empty),
`nullable-string` (`null` or a non-empty scalar), `enum` (membership in the
authority named by `allowed`), `sme` (an SME card id, or `unassigned`).

`allowed` is `-` for a freeform field, or an authority reference the gate READS:
`ladder:<path>#<key>` (membership in a declared ladder) or `sme-card`.

| field | required | kind | allowed | meaning |
|---|---|---|---|---|
| `module_id` | yes | id | - | This file's stable identity, dotted and path-derived (`gateway.chat.envelope`). |
| `system` | yes | id | - | The system the file belongs to (pillar, or the fleet system it serves). |
| `app` | yes | id | - | The deployable app inside that system (`chat`, `portal`, `gates`). |
| `solution_class` | yes | enum | ladder:governance/conformance/policy.yaml#ladder | The CMR quality rung this file is held to. |
| `patterns` | yes | list | - | Named patterns the file implements; may be empty, never invented. |
| `derives_from` | yes | nullable-string | - | The template/pattern/canonical file this one extends, or `null`. |
| `owner_sme` | yes | sme | sme-card | The SME card that owns this file, or `unassigned`. |
| `tier` | yes | enum | ladder:gateway/finops/tiers.yaml#ladder | The cheapest-capable model tier that may author this file. |
| `interfaces` | yes | list | - | What this file exports and who consumes it; best-effort, may be empty. |
| `invariants` | no | string | - | Rules that must never change; may be empty, never `null`. |
| `gotchas` | no | string | - | Traps a future editor must not re-discover; may be empty. |
| `related` | no | list | - | Issue and RCA numbers, quoted so the `#` is literal YAML. |
| `do_not_duplicate` | no | nullable-string | - | The canonical home if this file is a known near-duplicate, else `null`. |

An absent optional field reads as empty (`[]` or `null`); a **required** field
that is absent is a refusal, and so is a present field whose value fails its kind.

## Borrowed authorities — never re-declared

Three of the fields are memberships, and each has exactly one authority in this
repository. The gate reads the authority and proves the value against it; it
never carries its own copy of a vocabulary (ADR-0012, GR-29).

| Field | Authority | Reader |
|---|---|---|
| `solution_class` | `governance/conformance/policy.yaml` `ladder:` | `governance/conformance/model.py` |
| `tier` | `gateway/finops/tiers.yaml` `ladder:` (keys `L0`/`L1`/`L2`) | `gateway/finops/loader.py` |
| `owner_sme` | `registry/personas/cards/*.yaml` `id:` (plus `unassigned`) | the cards themselves |

The ladder text itself is pinned from `kushin77/CMR`
(`vendor/CMR/docs/SOLUTION-CLASSES.md`, ADR-0031) and is **not** copied here.

## The linter's verdicts

`scripts/check-code-headers.sh` reads every in-scope file and classifies it.
The classes are not interchangeable — which one a file falls into is what makes
the gate able to fail:

| Class | Meaning | Verdict |
|---|---|---|
| `conformant` | the block parses and every field validates | OK |
| `unrecorded` | no block, and the baseline does not record the file | **REFUSED, any tier** |
| `invalid` | a block is present but a rule above fails | **REFUSED, any tier** |
| `recorded` | no block, and the baseline records this exact content | excused |
| `drifted` | no block, a baseline row names the path but its fingerprint differs | REFUSED at tier ≥ 1, NOTE at tier 0 |

The baseline never excuses **invalidity** — only absence. A file that carries a
block is judged on the block.

## The baseline

`scripts/code-headers-baseline.tsv` is the recorded pre-standard debt: one row
per legacy file, keyed by **content**, never by line number.

```text
<path>	<sha256:…>	<tracked-by>	<reason>
```

The fingerprint is `sha256` over the file's content with blank lines dropped and
trailing whitespace stripped from each line — so a **pure line shift** (a blank
line inserted above the content) leaves the key unchanged and does **not**
re-flag the file, while any real content change moves it. That is the whole
point of keying on content: a line-numbered ledger would have to be re-recorded
every time someone inserted a blank line, and would silently excuse whatever
happened to sit at the recorded line afterwards.

Hygiene, checked in both directions so the ledger cannot rot into a fiction:

* a row whose file now carries a **valid** block, or whose file is gone, is a
  **stale** row: counted and reported by name, and prunable by
  `--prune-stale` (an explicit, reviewed edit). Staleness is deliberately NOT
  fatal — the measured precedent is `governance/reconcile`'s real-tree baseline
  (#740): punishing the cleanup the ledger exists to prompt is worse than
  tolerating it for a pass;
* a **malformed** row, or a **duplicate** row for one path, is **REFUSED**;
* `--record <path> --tracked-by <ref>` adds a row for a file the backfill cannot
  header, and **refuses** a path that did not exist at the baseline file's own
  last commit — a lane can never grant its own new file an amnesty.

## The tier seam

The epic that ordered this work (EPIC #1510) gates new checks by a tier switch
(its group A, the `A2` design): a check that cannot yet bind the whole fleet
runs **advisory below tier 1**. That switch does not exist in this repository
yet, so this check reads a single declared seam and implements no tier policy of
its own: `AO_CODE_HEADERS_TIER` (or `--tier N`), default `0`.

At tier 0 the **legacy** class (`drifted`) is reported, not refused; at tier ≥ 1
it is refused. The **new-file** class (`unrecorded`) is refused at every tier —
a file nobody ever recorded as debt has no excuse. When the group A switch
lands, this seam is deleted and the check reads that switch instead.

## Scope, and the boundaries named rather than hidden

* Extensions, closed and exactly the six the issue names: `.py`, `.sh`, `.ts`,
  `.js`, `.tf`, `.yml`. **`.yaml` is deliberately NOT in scope** (this repo's
  YAML is mostly `.yaml`): widening the set is a decision for a follow-up issue,
  and it is named here rather than folded in silently.
* `vendor/` is excluded (a pinned submodule, not ours to lint) and so are
  `tests/`, `fixtures/`, `plants/` and `rendered/` trees — expectations and
  generated output are not modules, the same boundary
  `scripts/check-tier-vocabulary.sh` draws.
* The gate scans the **whole in-scope tree**, not only changed files. The issue
  proposed a changed-file scope "to keep it fast"; measured, the full scan reads
  1181 files in **0.024 s**, and a changed-file scope cannot be venue-robust (it
  depends on a resolvable base ref, which the CI venue's clone does not always
  offer) nor can it tell a new file from a legacy one without the baseline doing
  that work anyway. The deviation is recorded here, with the measurement.

## Usage

```bash
bash scripts/check-code-headers.sh                 # the gate: provocation + the tree
bash scripts/check-code-headers.sh --self-test      # the provocation alone
bash scripts/check-code-headers.sh --files F...     # scan exactly these files
bash scripts/check-code-headers.sh --list           # the schema this document declares
bash scripts/check-code-headers.sh --record P --tracked-by REF   # add a debt row
bash scripts/check-code-headers.sh --prune-stale    # drop stale rows
bash scripts/draft-code-header.sh <file>            # draft a block on stdout
```

`draft-code-header.sh` is a **draft tool, never an auto-committer**: it prints a
best-effort block derived from the file's existing docstring to stdout and
writes nothing, so a human or the `code-review-sme` persona reviews the fields
that need judgment (`solution_class`, `patterns`) before they are committed.

Exit codes are the repository's honesty tri-state: `0` OK, `1` NOT-OK,
`2` CANNOT-ASSESS — never a pass.
