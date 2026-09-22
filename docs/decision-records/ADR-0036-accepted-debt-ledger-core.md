---
id: ADR-0036
status: accepted
date: 2026-09-21
deciders: [owner]
req: []
supersedes: []
---

# ADR-0036: the accepted-debt ledger core contract is shared; each repo's ledger shape is an adapter

## Status

`accepted` — decided by issue #1892 (child of EPIC #1510), which asked for a
**recorded** choice between one shared mechanism and per-repo implementation,
with the tradeoff and the measured cost of each option. The decision is
*unify the core contract, keep the shapes*. It is recorded with a live check on
the real ledgers (§Evidence), because the issue's own contract forbids closing
on a prose recommendation.

The subject is the "accepted pre-existing debt" ledger — the artefact that holds
a grandfather list, a written reason per row, a shrink-only ratchet, and a gate
that *refuses* a placeholder reason so that raising debt is a written decision
and never a keystroke. That contract was invented once and re-implemented per
repo; issue #1892 measured three implementations and asked whether a fourth repo
should write a fifth copy.

## Context

### The claim, and the measurement that qualifies it

Issue #1892 states the three implementations carry "identical semantics". The
implementations were re-measured on 2026-09-21 and the claim is **true for the
core and measurably false for one arm of it**:

| repo | artefact | shape / key | extra guard beyond the core | refuses an **empty** reason | refuses a **placeholder** reason |
|---|---|---|---|---|---|
| agent-orchestrator | `scripts/code-headers-baseline.tsv` (288 rows) | TSV `path ⇥ sha256:… ⇥ tracked-by ⇥ reason`, content-keyed (line-shift-proof) | `--record` refuses a path that did not exist at the ledger's own commit — "a lane can never grant its own new file an amnesty" | **yes** — a 4-cell row with an empty cell is refused by name as `baseline-malformed` | **no** — measured: a row whose reason is exactly `TODO` is loaded, excused, and the gate exits 0 without ever naming it |
| shared-frontend | `scripts/guard/baselines/tsc-ratchet-baseline.json` (schema `tsc-ratchet/v1`) | JSON, `by_file_code` `file:TSxxxx` fingerprint counts + a top-level reason | the source-file **denominator** must never shrink | **yes** | **yes** — `PLACEHOLDER_RE='^[[:space:]]*(TODO\|FIXME\|TBD)([^[:alnum:]]\|$)'` on `--enforce` |
| shared-frontend | `scripts/guard/baselines/mount-coverage-baseline.json` (schema `mount-coverage-baseline/v1`) | JSON, `tier` grade per surface (`mounted > wired > exists > unregistered`) | tier regression and an `unregistered` surface are refused (R1/R3) | **yes** | **yes** — R4 (a row's reason) and R5 (the top-level reason) |
| shared-services | `docs/.doc-links-baseline.txt`, `docs/.doc-naming-baseline.txt`, `.governance/coe-baseline-shared-services.json` | text `source-doc::link-target` pairs; JSON per-surface offender counts | per-surface counts, warn→enforce mode | (not measured here; out of this lane's venue) | (not measured here) |

So the shared *intent* is real — record-with-reason, shrink-only, key by
identity/content and never by line — and the fleet's own `check-duplicates.sh`
already encodes the doctrine that *two implementations of one rule disagree
silently*. The divergence is not hypothetical: **the placeholder arm is
implemented twice and missing once**, and the missing copy is the one this repo
owns. A lane can hand-write `reason = TODO` into `code-headers-baseline.tsv`
today and the gate passes — measured, §Evidence (C).

### Why this is a canonical-copy question, not a new pattern

[ADR-0010](ADR-0010-canonical-copy-ownership.md) already decided the shape of
the answer for fleet-shared content: **one declared canonical home, everything
else references it or is checked against it, never forks**, with the product
consuming the canon through a pinned submodule. The accepted-debt core is the
same *kind* of artefact — a small, fleet-wide rule — so it takes the same
pattern rather than a second one.

### The tradeoff, with the measured cost of each option

**Option 1 — one hub-owned core + per-repo adapters.**
- *Gain:* one reason validator, one placeholder rule, one monotonic comparison;
  the divergence above becomes impossible by construction; a fourth repo gets
  the contract by declaration.
- *Cost (measured):* the three **shapes** are genuinely different keys — a file
  content hash, per-`file:TSxxxx` diagnostic counts plus a denominator, and tier
  grades over executed addon ids — so the core cannot own a shape. It must be a
  small generic contract (identity key · measure · written reason · monotonic
  compare) with three adapters. The adapters are the *large* part of each
  consumer; the core is the *small* part. Wiring cost is one hub declaration
  plus three pin updates.

**Option 2 — per-repo by explicit decision.**
- *Gain:* no adapter layer; each ledger keeps its own shape, its own extra guard
  (agent-orchestrator's anchor rule, shared-frontend's denominator rule), and no
  cross-repo pin to maintain.
- *Cost (measured):* it **preserves the hole**. Agent-orchestrator's ledger
  accepts a `TODO` reason today (§Evidence C), so "per-repo" is not a neutral
  choice — it is a choice to leave the placeholder arm unimplemented in this
  repo while the other two implement it. It also does not avoid the work: the
  missing arm has to be written either way. It merely guarantees that a fourth
  repo writes a fifth copy.

The decision therefore turns on a measured asymmetry: the *shared* part is
small, and it is exactly the part that has already diverged, while the *shape*
part is large and is exactly the part that is legitimately repo-specific.
Extracting the small part costs little and closes a live hole; extracting the
large part would flatten well-documented per-repo semantics.

## Decision

1. **The accepted-debt core contract is shared, and declared once.** Its single
   canonical home is the hub (`kushin77/CMR`), per
   [ADR-0010](ADR-0010-canonical-copy-ownership.md): "one canonical home per
   doc; every other copy references or mirrors with provenance, never forks."
   Each repo consumes it by pin/reference and does not re-implement it.
2. **The core carries exactly these clauses, and no shape:**
   - a row names an **identity key** that survives an unrelated edit (content
     hash, or a file+diagnostic-code fingerprint — never a line number);
   - a row carries a **written reason**;
   - the ledger is **shrink-only** — the measure may fall at any time and may
     never rise without a written decision;
   - the gate **refuses** a reason that is missing, empty, **or a placeholder**
     (`TODO`/`FIXME`/`TBD`). This is the clause whose absence in one repo
     prompted this ADR; it is now part of the core rather than a per-repo habit.
3. **Each repo keeps its ledger shape as an adapter, and keeps its own extra
   guards.** The three shapes stay as they are; agent-orchestrator's
   "a lane cannot record its own new file" anchor and shared-frontend's
   "the denominator must never shrink" rule are *additional* guards, not core,
   and unifying the core must not drop either.
4. **Adoption is by declaration, not by copy.** A new repo declares that its
   ledger implements the core and names its adapter; it does not write a new
   reader, writer and comparison.
5. **The gate must be able to fail.** Per AO-GR-4 / GR-12, each clause above is
   demonstrable by a provocation that fires — the placeholder clause included,
   which is the clause agent-orchestrator currently fails to provoke.

## Consequences

- **Positive:** the placeholder rule exists once, so a repo cannot implement
  the contract minus an arm and still claim the contract; the measured
  divergence (agent-orchestrator accepting `reason = TODO` while
  shared-frontend refuses it) is closed by construction; a fourth repo adopts
  by declaration; the three well-documented shapes are untouched.
- **Negative:** the core's home is the hub, so adopting it means a cross-repo
  pin for consumers — the same friction ADR-0010 already accepted for the
  standards trio. Agent-orchestrator's adapter must gain the placeholder arm,
  which is a change to a gate (not a doc) and is therefore **not** made by this
  decision's own lane.
- **Neutral:** the ledgers themselves do not move, re-key or re-format; no
  existing row is invalidated by this decision; the core is small enough that
  "unify" here does not mean "rewrite the consumers".
- **Follow-ups:**
  1. File the hub direction record that declares the core contract and its home
     (the hub is another repo — this lane does not edit it; NG4,
     [`../CROSS-REPO-EXECUTION-BOUNDARY.md`](../CROSS-REPO-EXECUTION-BOUNDARY.md) §1).
  2. File the agent-orchestrator follow-up to add the placeholder arm to
     `scripts/check-code-headers.sh` and to provoke it in its own `--self-test`
     (the ledger's own hygiene section already proves the empty-reason refusal,
     so the harness is one arm short of the core).
  3. When the core lands, each consumer replaces its local reason validator with
     a call to the core and records that it did, or records a named exemption.

## Evidence

Measured 2026-09-21 against the real ledgers. Full output is pasted on issue
#1892 and in the pull request that lands this record.

**A — the sweep is idempotent** (`scripts/check-code-headers.sh --prune-stale`,
agent-orchestrator's own ledger, run twice):

```
$ bash scripts/check-code-headers.sh --prune-stale
pruned 0 stale row(s): none
$ bash scripts/check-code-headers.sh --prune-stale
pruned 0 stale row(s): none
tree dirty after two sweeps: 0
```

**B — negative control: a genuine shrink is accepted**, so the mechanism does
not reject all change. A row was planted for a file that now carries its block
(stale by definition), and the sweep removed exactly that row:

```
$ bash scripts/check-code-headers.sh --prune-stale
pruned 1 stale row(s): scripts/check-code-headers.sh
$ bash scripts/check-code-headers.sh --prune-stale
pruned 0 stale row(s): none
tree dirty vs pristine after the shrink: 0 file(s)
```

**C — the placeholder arm, where it exists and where it does not.**
Agent-orchestrator's ledger, with the reason cell of a well-formed row set to
exactly `TODO`: the **full gate exits 0**, its verdict is
`code-headers: OK — every in-scope file carries a valid block or is recorded
debt`, and it never names the row (`grep -c -i TODO` over the gate output = 0).
The same ledger's own provocation does refuse the *empty* case
(`self-test` arm: `OK a malformed row is refused by name`). Both halves of the
contract therefore live one arm short in this repo.

The clause's live refusal is demonstrated on the implementation that already
carries it — shared-frontend's `scripts/verify-mount-coverage.sh`, offline and
read-only, `--self-test` over fixture trees driving the real generator and the
real verdict:

```
  PASS  S7 --update-baseline with no --reason exits 0
  PASS  S7 the written baseline carries the TODO placeholder
  PASS  S7 a TODO baseline is REJECTED => exit 1
  PASS  S7 says the reason is a placeholder
SELF-TEST: PASS (21 checks: 4 measure/silence · 8 fire · 5 UNKNOWN · 4 no-laundering)
```

That is the arm this decision makes part of the core, and the gap §Evidence C
measures on agent-orchestrator is the reason it is not left per-repo.
