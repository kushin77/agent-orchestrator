# RCA-0007 — a shared tmpfs filled to 100 %, and a 0-byte write passed as evidence

| Field | Value |
|---|---|
| RCA id | `RCA-0007` |
| Incident | `INC-0007` |
| Origin | `event: tmpfs-full-2026-09-14` — the ledger records an **event** origin because the committed board snapshot does not carry #729 (see [`RCA-0012`](RCA-0012-stale-snapshot-no-trigger.md)) |
| Severity | `high` |
| Owner | infra-limits lane — issue #729 (EPIC #708) |
| Reviewed | `2026-09-14` |

## Impact

The box ran out of ephemeral storage and **said nothing**. `/tmp` is a 16 GiB
tmpfs shared by every lane; it reached 16G/16G (100 %) and the fill was one
orphan: a 14.8 GB scratch log that no process held. Two consequences, both
measured:

1. **A write that never happened was treated as evidence.** A redirected write
   on a full filesystem fails leaving a 0-byte file. `gh issue create --body-file
   f` then returned a URL for an **empty body** — success reported for an
   artifact that did not exist.
2. **The box starved.** Four lanes and eight gate processes were already sharing
   it (measured load ~85–107); a full tmpfs turns unrelated lanes' scratch
   writes into silent failures, so the damage surfaces far from the cause.

Nothing was corrupted and nothing was lost permanently — but the episode
produced *false evidence*, which is worse than a loud failure, because the
record would have shown a write that never landed.

## Detection

Late, and by accident: a shell write produced a 0-byte file, and a GitHub
artifact came back empty. There was no gate on this dimension at all — no
free-space floor, no ceiling on a single scratch file, and no rule requiring an
artifact to be read back before it is trusted. `/tmp` usage has been measurable
with `df` the whole time; the failure is that nothing measured it.

## Root cause

The ephemeral-storage dimension had **no control**, in four specific places:

- **Shared resource treated as private.** `$TMPDIR` and `/tmp` were handled as
  per-lane scratch. They are one tmpfs shared by every lane on the box, so one
  lane's leak is every lane's outage.
- **No ceiling on a single file.** One orphan log could consume the whole 16 GiB
  because nothing bounded a scratch file's size. The hog was invisible until
  something else failed.
- **No read-back on artifact-producing writes.** "The file exists" was the test,
  and a failed redirect leaves exactly that — a file that exists and is empty.
  A write and a failed write were indistinguishable downstream.
- **No gate, so no signal.** The state went from fine to 100 % with nothing in
  between: no check compared the free space, the used percentage, or the largest
  scratch file against any declared limit. This is the GR-12 shape — a rule that
  cannot fail is a formality, and here the rule did not exist to fail.

## Corrective actions

- `CA-0009` — deliver the machine-checkable guard
  [`../../../scripts/check-infra-limits.sh`](../../../scripts/check-infra-limits.sh)
  (free-space floor and used-percent ceiling, an orphan-hog ceiling, and the
  write rule enforced on its own evidence path) together with the contract
  [`../../../docs/INFRA-LIMITS.md`](../../../docs/INFRA-LIMITS.md), linked from
  [`../../../AGENTS.md`](../../../AGENTS.md). Tracked by **#729**; the check is
  registered in `scripts/verify.sh` by that single-writer file's owner, so until
  that line lands the artifact is delivered but inert (`RCA-0011`).

## Lessons

`SUGGEST-0005` — **an infra limit is not a limit until a gate measures it.**
A shared resource needs (a) a declared floor, (b) a ceiling on any single
consumer, and (c) a read-back rule on anything produced from it, because the
failure mode of a full filesystem is *silence*, not an error. Recorded as an
open suggestion until #729 merges.

## Evidence

- `df -h /tmp` → a 16 GiB tmpfs at 100 % during the episode; the hog was a
  14.8 GB scratch log with no process holding it.
- Re-measured for this delivery:
  `df -h /tmp` → `16G 2.5G 13G 17% /tmp` (12 885 MiB free — the box recovered
  once the hog was truncated).
- The guard's own output (`.verify/infra-limits-report.json`, 296 bytes,
  verified with `wc -c`), including four provoked controls.
- Ledger: `INC-0007`, `RCA-0007`, `CA-0009`, `SUGGEST-0005`.

## Follow-up

The incident closes with `CA-0009`, i.e. when #729 merges **and** the check is
wired into the gate of record. Until then the
`corrective-action-open` deviation in the lessons report keeps this visible.
