# Cross-reference spine (EPIC #138, issue #384)

The knowledge index records *nodes* — the catalogue in
[`catalog.json`](../governance/knowledge/catalog.json). The **cross-reference
spine** records the *edges* between those nodes: typed, machine-checkable
relationships that make the graph queryable instead of a bag of files. The spine
is emitted by [`governance/knowledge/crossref.py`](../governance/knowledge/crossref.py)
and enforced by [`scripts/check-cross-reference.sh`](../scripts/check-cross-reference.sh).

A relationship is a closed-vocabulary edge `from_id → type → to_id`, with an
optional `via` naming the artifact that declared it. Edges carry **no
timestamps**; two builds over one revision must produce byte-identical
`relationships`, which is what makes drift review meaningful.

## `cmr-refs:` marker syntax

A marker is a markdown line whose first non-whitespace characters are the token
`cmr-refs:` followed by a comma-separated list of targets:

    cmr-refs: ADR-0012, GR-11, RCA-0001

Targets are one of:

| Form | Resolves to |
|------|-------------|
| `ADR-NNNN` | an ADR under `docs/decision-records/` |
| `GR-N` | a golden rule in [`GOLDEN-RULES.md`](../GOLDEN-RULES.md) / [`docs/GOLDEN-RULES.md`](GOLDEN-RULES.md) |
| `#N` | an issue in the committed [`.board/snapshot.json`](../.board/snapshot.json) |
| `RCA-NNNN` | an RCA record in the [lessons ledger](../governance/lessons/ledger.jsonl) |
| `LESSON-NNNN` | a closed lesson record in the lessons ledger |
| `SUGGEST-NNNN` | an open suggestion record in the lessons ledger (a ticket of kind `suggestion`) |
| `INC-NNNN` | an incident record in the lessons ledger |
| backtick-wrapped path | a repo-relative file path (backticks required) |

Each target is resolved against the repo at gate time: the file must exist, or
the entity id must be present in the catalogue, the board snapshot or the
ledger. There is no way to write a marker that the gate does not check.

**`LESSON-NNNN` and `SUGGEST-NNNN` are addressable ticket kinds** (issue #402).
The ticket contract ([ADR-0014](decision-records/ADR-0014-ticket-single-join-node-contract-v2.md))
gives the improvement register a `kind`, so an open `SUGGEST-*` is a ticket of
kind `suggestion` — dispatchable, blocked-by, closable and, now, a valid marker
target. Before #402 a `SUGGEST-NNNN` was a valid ledger id but an invalid
`cmr-refs:` target, which left the open elite register unreachable from the
document graph. A `CA-NNNN` stays invalid: a corrective action is a sub-part of
an RCA, not a node the graph addresses.

## Where markers are valid

A marker may appear in any tracked `.md` file **outside** `vendor/`, `.research/`
and `.git/`. The gate scans every such file, so a marker in a doc that is not
your lane's own file still must resolve — seed markers only where you can stand
behind the target. This file seeds the markers below, on targets it owns.

## The closed relationship vocabulary

The spine accepts exactly these nine types (see `RELATIONSHIP_TYPES` in
[`model.py`](../governance/knowledge/model.py)):

| Type | Meaning | Emitted from |
|------|---------|--------------|
| `supersedes` | an ADR replaces an earlier ADR | ADR front-matter `supersedes:` |
| `parent-of` | an issue is the parent of another | board snapshot `parent` |
| `blocked-by` | an issue is blocked by another | board snapshot `blocked_by` |
| `caused-by` | an RCA analyzes an incident | the ticket graph (`RCA-*` caused-by `INC-*`) |
| `mitigates` | a corrective action or lesson mitigates | the ticket graph |
| `origin` | an RCA/incident traces to its source ref | the ticket graph |
| `refs` | a document references a node | `cmr-refs:` markers |
| `part-of` | a node belongs to a larger node | (reserved) |
| `implements` | a node implements a decision | (reserved) |

`part-of` and `implements` are part of the vocabulary but currently emitted by
no source: they are reserved so a future source cannot invent an ungoverned
edge type. The gate rejects any edge whose type is not in this table.

## Lessons are an ordinary edge source (issue #402)

The lessons register is not a branch inside the builder any more: it **declares**
its typed ticket edges ([`governance/lessons/edges.py`](../governance/lessons/edges.py))
and [`crossref.py`](../governance/knowledge/crossref.py) consumes them exactly
like the ADR front-matter, the board snapshot and the markers. The ticket graph's
edge vocabulary is closed at four types — `caused-by`, `origin`, `mitigates`,
`remediation-of` — and the spine carries the subset its nine types admit
(`remediation-of` stays a ticket-graph edge). The typed edge is the **single**
place a lessons cross-record reference becomes a node id, so no reader narrows
`origin` or `remediation_issue` for itself.

## No silent skips

A marker target that cannot be validated is a **FAIL**, never a skip. The gate
refuses, by file and line, when:

* a marker is malformed (an empty target list, or a target that is not one of
  the eight forms above — including `CA-NNNN`, which is a valid ledger id but
  not an addressable ticket kind);
* a marker names a target that does not resolve;
* the catalogue lacks a `relationships` list, an edge type is outside the
  vocabulary, or an edge endpoint does not resolve;
* two builds over one revision differ (determinism).

The gate mutates its own convention doc (one target is swapped for a bogus id)
and requires the mutation to be refused, so the check cannot pass vacuously.

## Declared relationships (this document)

This document declares the references it owns, exercising each marker form:

cmr-refs: ADR-0012, GR-11, RCA-0001
cmr-refs: LESSON-0001, SUGGEST-0002, INC-0001, #138
cmr-refs: `docs/ARCHITECTURE.md`, ADR-0013
