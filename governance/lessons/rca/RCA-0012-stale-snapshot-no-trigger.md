# RCA-0012 — a committed snapshot with no trigger, and consumers that cannot resolve

| Field | Value |
|---|---|
| RCA id | `RCA-0012` |
| Incident | `INC-0012` |
| Origin | `event: stale-snapshot-no-trigger-2026-09-14` — the committed board snapshot does not carry #727, which is itself the finding |
| Severity | `medium` |
| Owner | dispatch lane — issue #727 (EPIC #708) |
| Reviewed | `2026-09-14` |

## Impact

`.board/snapshot.json` is the reference a growing set of controls resolve
through — the claim gate's frontier, `check-gate-coverage.sh`'s tracker state,
the lessons ledger's `origin` and `goal`. It is a committed file whose frontier
is far behind the work. Measured on this tree at delivery time:

```text
snapshot issues: 142   max: 559   generated_at: 2026-09-14T20:02:35Z
 #708 in snapshot: False   #723..#727, #729, #730 in snapshot: False
```

Three measured consequences, all of them the same defect wearing different
clothes:

1. **A legitimate issue reference does not resolve.** The lessons gate refuses an
   `origin` naming `#729` with `origin-unresolved` ("not on the committed board
   snapshot",
   [`../checker.py`](../checker.py)). The six EPIC #708 incidents recorded with
   this delivery therefore carry an `event` origin instead of the issue that
   ordered the work, and `edges.pmo_rows` cannot derive their epic `goal`. The
   record is honest and less useful than it should be, because of the snapshot.
2. **An honest deferral cannot be recorded.** `check-gate-coverage.sh` reads
   issue state from this same file and fails a baseline row whose tracker is
   absent from it. A lane that defers wiring to an issue the snapshot does not
   carry must therefore either misreport or leave the artifact unwired — and the
   baseline's own rows (including the one for the RC gates, tracker #559) are the
   measured precedent.
3. **A stale answer is refused with no way to clear the refusal.** The dispatch
   path *does* fail closed on staleness: `governance/dispatch/claims.py` refuses
   a snapshot older than its threshold with `snapshot-stale`, and
   `DEFAULT_STALENESS_MINUTES` lives in `governance/dispatch/snapshot.py`. That
   is the correct posture (refuse rather than answer) — but a refusal nobody can
   clear is a stopped lane, not a safeguard.

## Detection

By noticing that a correct reference does not resolve — manually. The snapshot
carries `generated_at`, so staleness is computable to the second; no gate fails
on the distance between that timestamp and the work being done, and no consumer
reports "your reference is newer than my world".

## Root cause

The snapshot has a **timestamp but no freshness rule anyone acts on**, and no
writer that any event can trigger. Measured on this tree:

```text
grep -n "^board" Makefile                       -> board-gate: (no refresh target)
governance/board/cli.py subcommands             -> check, exceptions,
                                                   export-boundary, boundary-check
grep -n "def .*refresh\|regenerate\|capture" governance/dispatch/snapshot.py
                                                -> no matches
grep -rn "snapshot\|board" fleet/cron.py        -> no matches
```

So the producer of a widely-read committed artifact is not in this repository and
is not scheduled: refresh is a manual act (or one performed outside the
checkout). The missing control is a **trigger** — the event that invalidates the
snapshot (an issue opened, closed or relabelled) does not cause it to be
refreshed, and nothing states the age a consumer may tolerate. `INC-0005` /
`CA-0007` fixed the *reader* (refuse a stale snapshot); this incident is the
*writer* side of the same artifact. A declared freshness threshold with no
refresh path converts a data-staleness bug into an availability bug.

## Corrective actions

- `CA-0014` — refresh and trigger on the dispatch path (issue **#727**): make the
  snapshot refresh a scheduled/evented action rather than a manual one, and make
  each consumer state the age it tolerates and refuse (or warn) beyond it, so
  staleness is a measured outcome and not a surprise. Tracked by **#727**.

## Lessons

`SUGGEST-0010` — **a committed snapshot needs a trigger, or its freshness rule
becomes an outage.** Any point-in-time artifact that other controls resolve
through must have three things together: a timestamp (it has one), a stated
tolerance (the claim gate has one), and a refresh the invalidating event itself
triggers (nothing has it). Two of three is worse than none, because the refusal
is correct and unactionable. Recorded as an open suggestion until #727 lands.

## Evidence

- `.board/snapshot.json` — 142 issues, `generated_at` `2026-09-14T20:02:35Z`,
  highest issue **#559**; `#708`, `#723`, `#724`, `#725`, `#726`, `#727`, `#729`,
  `#730` all absent (measured with a membership check over the snapshot).
- [`../checker.py`](../checker.py) — `origin-unresolved` for an issue ref not on
  the snapshot.
- `scripts/check-gate-coverage.sh` — tracker state read offline from the same
  file; an absent tracker fails.
- `governance/dispatch/claims.py`, `governance/dispatch/snapshot.py` — the
  fail-closed staleness rule (`snapshot-stale`, `DEFAULT_STALENESS_MINUTES`) and
  no refresh entry point.
- Ledger: `INC-0012`, `RCA-0012`, `CA-0014`, `SUGGEST-0010`; the earlier instance
  of the class is `INC-0005` / `CA-0007` / `SUGGEST-0002` (#170).

## Follow-up

The incident closes with `CA-0014` (#727). `INC-0005` and `SUGGEST-0002` stay open
against the same artifact on the reader side; the two are separate controls over
one file, and neither subsumes the other.
