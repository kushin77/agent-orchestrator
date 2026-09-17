# RCA-0017 — the RCA id double-booking + prose-count defect

| Field | Value |
|---|---|
| RCA id | `RCA-0017` |
| Incident | `INC-0017` |
| Origin | `#1052` |
| Severity | medium |
| Owner | governance lane (`governance/lessons/`) |
| Reviewed | 2026-09-17 |

## Impact

`docs/rca/2026-09-16-pr-queue-clearing.md` titled itself `RCA-0007 / RCA-0008`
while the ledger already held both ids for a different incident
(`INC-0007`/#607/declared-not-exercised-golive and
`INC-0008`/#506/date-bomb-seed-without-evaluation) — two unrelated pieces of
content claiming the same id, discoverable only by reading both artifacts.
Separately, PR #1036 conflicted on `governance/lessons/ledger.jsonl` +
`README.md`: append-only JSONL rows should never need a hand union, and the
README's hand-written incident-count sentence ("Thirteen"/"Eight" →
hand-merged to "Fourteen") had to be reconciled by a human because nothing
asserted it against the ledger.

## Detection

Read by a human while triaging RCA prose ahead of a promotion (this issue);
nothing in `scripts/check-lessons.sh` refused a `docs/rca/*.md` heading that
mints an id the ledger already uses for something else, and nothing asserted
the README's incident count against the ledger.

## Root cause

Two related gaps in the gate:

1. `docs/rca/` writeups are explicitly out of the ledger's enforced pipeline
   (see that directory's own README), so nothing stopped a doc from minting
   an `RCA-NNNN` id the ledger's own numbering had already spent on a
   different incident — there was no single id authority the gate enforced
   across both surfaces.
2. The README's "Fourteen real incidents..." sentence is hand-authored prose
   describing a count the ledger already knows exactly — a derived fact
   stored as free text, which is exactly what forced PR #1036's hand-merge.

## Corrective actions

- `CA-0022` (this issue, #1052) — `scripts/check-lessons.sh` now refuses (a)
  a duplicate ledger id (pre-existing `duplicate-id` rule, unchanged), (b) an
  `RCA-NNNN` token in `docs/rca/*.md` that is not a ledger id, or that a
  `docs/rca/*.md` heading MINTS (starts with) while the ledger's `artifact`
  for that id names a different file, and (c) a README incident-count
  sentence whose number does not match the ledger's actual incident count.
  Each refusal is provoked with a plant + mutant in
  `governance/lessons/negative_control.py`.

## Lessons

- `SUGGEST-0013` (owner: governance lane) — extend the id-authority rule to
  every other ledger-adjacent id space this repository grows (for example a
  future `governance/conformance` finding id), so "one register, one
  authority" is a repo-wide pattern rather than a one-off fix scoped to
  RCA ids. Close it as a `LESSON-<n>` once a second id space adopts the same
  gate shape.

## Evidence

- `pr` `#1052` — this fix: the two new checker rules, the negative-control
  probes, the RCA-0015/RCA-0016 promotion, and the `docs/rca/` heading
  rename.
- `artifact`
  `docs/rca/2026-09-16-pr-queue-clearing.md` — the renamed headings (no
  longer mint `RCA-0007`/`RCA-0008`; they cite `RCA-0015`/`RCA-0016`).

## Follow-up

`SUGGEST-0013` (above) is open, tracked on this same issue's lane.
