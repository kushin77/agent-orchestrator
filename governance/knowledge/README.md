# Knowledge index — CMR/GDC institutional knowledge (issue #139)

The authoritative catalogue of this repository's institutional knowledge: its
golden rules, governance, architecture, ADRs, policy, pattern/template
definitions and live issue metadata — each with the provenance needed to trace it
back to a revision.

Nothing here is hand-maintained. The catalogue is generated from
[`sources.py`](sources.py) and the files it points at, so there is exactly one
place to add a source and no second knowledge store to drift out of sync.

## Why it exists

Discovery and compliance used to depend on knowing where to look. This index makes
the question answerable: *which artefact states this rule, which revision is it
from, who owns it, and is it still current?*

## Usage

```bash
# Build the catalogue and refresh the report
python3 governance/knowledge/cli.py build

# Validate (the gate of record); exit 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS
python3 governance/knowledge/cli.py validate

# Search, with source-backed evidence and compliance context
python3 governance/knowledge/cli.py query --text egress --kind policy

# Per-kind coverage for board reporting
python3 governance/knowledge/cli.py coverage
```

`make knowledge-index` runs the validation check (the same one `make verify`
runs, via `scripts/check-knowledge-index.sh`); `make knowledge-index-build`
regenerates the catalogue.

## What is indexed

| Kind | Source | Owner |
|------|--------|-------|
| `golden-rules` | `GOLDEN-RULES.md`, `docs/GOLDEN-RULES.md` | platform-governance |
| `governance` | `AGENTS.md`, `CONTRIBUTING.md`, `RELEASING.md`, `SECURITY.md`, `docs/GOVERNANCE.md`, `docs/GIT-TEMPLATES-GAP-ANALYSIS.md`, … | platform-governance / governance-pmo |
| `architecture` | `docs/ARCHITECTURE.md`, `docs/QA-GATE.md`, `docs/spikes/*.md` | architecture |
| `adr` | `docs/decision-records/ADR-*.md` | architecture |
| `policy` | `guardrails/policy/controls.yaml`, bundles, schemas | security |
| `pattern-template` | `docs/decision-records/template.md`, `registry/**/*.schema.json`, consumer-repo scaffold, `.github/ISSUE_TEMPLATE/**`, `.github/PULL_REQUEST_TEMPLATE.md`, `.gitmessage` | platform / platform-governance |
| `issue-metadata` | `.board/snapshot.json` — one item per issue | governance-pmo |
| `lessons`, `rca` | `vendor/CMR` (CMR hub) | governance-pmo |

Two git-ecosystem sources are declared but not yet present —
`docs/SHELL-PATTERNS.md` (lane #621) and `docs/GIT-ENV-VARIABLES.md` (lane
#627) — so they are registered `required=False` and join the index on the build
after their lane lands, with no second edit to
[`sources.py`](sources.py).

## Provenance

Every item carries: originating repo, origin path, owner, version (the last commit
that touched the path), content hash, size, line count, retrieval mode and, for
vendored assets, the upstream repo. `make verify` fails if any item's provenance
is incomplete — an untraceable catalogue entry is worse than a missing one.

## Secret policy

An asset registered as institutional knowledge is what people and agents copy
from, so it must not carry credentials. [`secretpolicy.py`](secretpolicy.py)
scans every indexed asset; a hit is an **error** that fails the build. Findings
report the rule and line number and never echo the matched value — a finding must
not become the leak it exists to report. Documented placeholders (`sk-...`,
`changeme`, `<token>`) are allowlisted so the examples that teach the pattern do
not trip it.

## Coverage, gaps and drift

`coverage` reports per kind. A **required** kind with no items is an error. The
`lessons` and `rca` kinds are sourced from the CMR hub via the `vendor/CMR`
submodule: when it is not checked out they are reported `unavailable` as a
**warning** with the unreachable paths named — an honest gap, never a silent pass.

Drift (assets changed, added or removed since the recorded catalogue) is reported
as a **warning** naming each asset, because sources legitimately change and a gate
that blocked every document edit would be disabled within a week.

## Cross-reference spine

The catalogue records *nodes*; the cross-reference spine records the *edges*
between them as a `relationships` list in `catalog.json`. [`crossref.py`](crossref.py)
is the deterministic builder — sorted, deduplicated, and byte-stable across
rebuilds because edges carry no timestamps — and the gate of record is
[`../../scripts/check-cross-reference.sh`](../../scripts/check-cross-reference.sh)
(the `cross-reference` target).

Edges are drawn from four sources and typed by a closed vocabulary
(`supersedes`, `parent-of`, `blocked-by`, `caused-by`, `mitigates`, `origin`,
`refs`, `part-of`, `implements` — see `RELATIONSHIP_TYPES` in
[`model.py`](model.py)):

* ADR front-matter `supersedes:` → `supersedes`;
* `.board/snapshot.json` `parent` / `blocked_by` → `parent-of` / `blocked-by`;
* `governance/lessons/ledger.jsonl` → `caused-by` (RCA → incident),
  `mitigates` (corrective action → RCA; lesson → incident) and `origin`
  (RCA/incident → its source reference);
* `cmr-refs:` markers in tracked markdown → `refs`.

### `cmr-refs:` markers

A markdown line beginning with `cmr-refs:` declares a comma-separated list of
targets the document references:

```text
cmr-refs: ADR-0012, GR-11, RCA-0001
```

Target forms are closed: `ADR-NNNN`, `GR-N`, `#N` (issue), `RCA-NNNN`,
`LESSON-NNNN`, `INC-NNNN`, or a repo-relative path in backticks. Every target
must resolve (the file exists, or the entity id is present in the catalogue,
board snapshot or ledger); a target that cannot be validated is a FAIL, never a
skip. The normative convention is
[`../../docs/CROSS-REFERENCE-SPINE.md`](../../docs/CROSS-REFERENCE-SPINE.md).

## Maintenance and refresh cadence

| When | Action |
|------|--------|
| On demand | `make knowledge-index` |
| After a change to an indexed source | re-run `build` so drift stays at zero |
| Scheduled | the ops runner invokes `build` daily (code-native; no workflow files) |
| Before a release | `validate` must be green, with the report attached as evidence |

Outputs: [`catalog.json`](catalog.json) (the tracked catalogue) and
`.verify/knowledge-index-report.json` (the per-run report).

To add a source, edit [`sources.py`](sources.py) — one declarative entry with a
kind, glob, owner and whether it is required. Nothing else needs touching.

## Layout

| File | Role |
|------|------|
| [`model.py`](model.py) | kinds, provenance, findings, coverage |
| [`sources.py`](sources.py) | the declarative source catalogue |
| [`indexer.py`](indexer.py) | build, coverage, drift |
| [`crossref.py`](crossref.py) | cross-reference spine: relationship builder + target resolution |
| [`secretpolicy.py`](secretpolicy.py) | credential scanning for indexed assets |
| [`query.py`](query.py) | search with source-backed evidence |
| [`cli.py`](cli.py) | build / validate / query / coverage |

## Provenance of this module

Implements issue #139 (milestone M24), parent #138. Sources named in
[`sources.py`](sources.py) are the canonical artefacts themselves, not copies.
