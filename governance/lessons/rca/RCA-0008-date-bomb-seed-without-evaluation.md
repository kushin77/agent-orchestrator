# RCA-0008 — the date bomb: a fixture that pins the seed and not the evaluation (issue #506)

| Field | Value |
|---|---|
| RCA id | `RCA-0008` |
| Incident | `INC-0008` |
| Origin | `#506` — Chat FinOps: per-turn attribution, budget caps and prompt-cache accounting |
| Severity | `high` |
| Owner | the `telemetry/chat` lane that shipped #506 and carries the fix (PR #1026); recorded and followed up by the lessons lane, issue #1033 (parent #1028) |
| Reviewed | `2026-09-17` |

## Impact

`#506` closed on 2026-09-14 with `47 passed in 0.59s` from its own `Verify:`
command (`python3 -m pytest telemetry/chat -q -p no:cacheprovider`). The two
tests that command *is* the proof of were red every day after. Measured
first-hand on 2026-09-17:

```
$ python3 -m pytest telemetry/chat -q -p no:cacheprovider    # origin/master dc92ad1
FAILED telemetry/chat/tests/test_budget_guard.py::test_a_quota_exhausted_tenant_never_reaches_the_provider
FAILED telemetry/chat/tests/test_negative_controls.py::test_each_refusal_path_refuses_and_still_meters[quota]
2 failed, 45 passed in 1.44s
```

and identically at the closing squash itself (`eae061d`, measured in a detached
worktree of that commit: `2 failed, 45 passed in 1.01s`, the same two node ids).
So this is not a later regression: it is a green that expired with the calendar.

The substantive harm is what the failing assertion says, not that a test is red:
the guard **allowed** a quota-exhausted tenant — `assert result.allowed is False`
failing with `provider_called=True`. The criterion is inverted, so for three days
the fleet's record said a spend control was proven by test while the test
asserted the opposite of the guard, and the issue read `completed`. It is also a
statement about evidence shelf-life: a green that cites a pinned date is true
only on that date.

The operator's own framing is quoted rather than paraphrased — this is
*"a verification defect, not a proven production bug"*: `TurnBudgetGuard.check`
and `evaluate` do accept `day`/`month`; the runner declines to thread them, and
judging a live turn against the live day is arguably correct. What is not
arguable is that a clause reading "proven by test" was unproven for three days.

## Detection

Detected 2026-09-17 by re-running the issue's own `Verify:` command against a
clean `origin/master` — by hand, three days late, and only because the issue was
being re-read. Nothing alerted.

What should have caught it, each measured:

- **No gate of record runs this suite.** `make verify`'s `pytest-chat` entry runs
  `gateway/chat/tests` (`scripts/verify.sh:526`) — a different directory.
  `telemetry/chat` *is* declared in `scripts/pytest-suites.txt:114`, and that
  manifest is run in full by `make gate` / `make tests`
  (`scripts/gate.sh:85` → `scripts/run-pytest-suites.sh`) but **not** by
  `make verify`; the repo's own comment at `scripts/verify.sh:510-513` states
  that split. A red suite behind a green composite is invisible to every lane
  that runs the gate of record.
- **Nothing re-runs closure evidence.** The evidence comment on #506 was genuine
  when written and no check ever re-executed it.
- **Nothing refuses a fixture constant that has fallen into the past.**
  `DAY = "2026-09-14"` is a literal; no gate asks whether a date a fixture names
  can still be reached by the code under test.

## Root cause

The mechanism is a bucket mismatch between what the fixture pins and what the
code reads:

- The fixture pins the **seed** — `telemetry/chat/tests/conftest.py:38-40`
  (`TURN_TS = "2026-09-14T09:00:00Z"`, `DAY = "2026-09-14"`,
  `MONTH = "2026-09"`) — and the quota rail is seeded for that day
  (fixture world at `conftest.py:175`; policy `free`, `soft_limit=10`,
  `hard_limit=20`, `calls=20`).
- The subject resolves its **evaluation** bucket from the live clock:
  `GuardedTurnRunner.run` (`telemetry/chat/budget_guard.py:288-303`) takes no
  `day`/`month` and calls `self.guard.check(...)` at `budget_guard.py:301`
  without them, so `preflight(day=None)` reaches `QuotaEnforcer.evaluate`, whose
  own docstring reads *"'day' pins the evaluation day bucket for reproducible
  checks"*. The bucket is therefore `2026-09-17`; the `2026-09-14` seed reads as
  `0` usage; the tenant is not exhausted; the turn is allowed.
- The sibling budget test passes because its fixture pins the month **at
  evaluation time** (`budget_with_shipped_policies(world.month, 120.0)`). That
  is the proof that the variable is not "dates in tests" but **which bucket the
  evaluation reads**.

The missing control is therefore not "assert harder". It is the invariant: *the
subject under test must derive its bucket from the entity under test*. The
runner was holding the information (`turn.ts`) and nothing mechanical asked for
it — the GR-12 shape, a rule that existed only as an intention.

The three-day delay has a second, independent cause: the suite carrying the
acceptance proof is named by no gate of record, so its rot could not surface
through the gate every lane runs. An artefact no gate runs is a formality.

## Corrective actions

- `CA-0010` — **judge a turn in its own bucket.** Derive `day`/`month` from the
  turn's own normalized timestamp at the runner boundary, so the pinned seed
  means something, and put a mutation-proved control on the gate of record
  (`scripts/check-chat-finops.sh`, control `turn-date-scope`) that fails **by
  name** — *"the evaluation bucket is not the turn's own (#506)"* — when the
  derivation is removed. **Open**, carried by PR
  [#1026](https://github.com/kushin77/agent-orchestrator/pull/1026) (head
  `cc284e8`), which closes #506.
- `CA-0011` — **consolidate the seam**, issue
  [#1025](https://github.com/kushin77/agent-orchestrator/issues/1025): 11 raw
  live-clock reads behind 6 duplicated seams, so a surface cannot resolve its own
  bucket differently from the entity it judges. **Open**.
- `CA-0012` — **publish the shape as canon**, issue
  [#1028](https://github.com/kushin77/agent-orchestrator/issues/1028): the first
  Python pattern entry, `PP-1` — *a fixture that pins a seed but not the
  evaluation* — with `scripts/check-python-patterns.sh` refusing it mechanically
  under per-pattern provocation. **Open**; this is the half of #1028 this lane
  does not own.

None of the three is closed and none is claimed as done here.

## Lessons

- `SUGGEST-0005` (open) — **a green carrying a pinned date is valid only on that
  date: ask whether the *evaluation* bucket is pinned, not just the seed.**
  Owner: the lessons lane (#1028); the remediation is landing `PP-1` with its
  mechanical refusal.
- `SUGGEST-0006` (open) — **a suite the gate of record does not run cannot make
  the gate red**: a declared suite's expiry is invisible until someone re-runs it
  by hand. Owner: the gate lane.

Neither is recorded as a `LESSON-*`, because a closed lesson must name the commit
that ships the change and both changes are still in flight (PR #1026 is open, the
pattern canon is unlanded). Recording them as open suggestions is the honest
form; no sha was invented to close them.

## Evidence

- The reproduction, first-hand on 2026-09-17, at `origin/master`
  `dc92ad1e9c65ef4a04dc584deb32d1265a374fd8` and at the closing squash
  `eae061df5a55058c5a98987e099ff990e5d3a8f0` (detached worktree):
  `python3 -m pytest telemetry/chat -q -p no:cacheprovider` → `2 failed,
  45 passed` at both, the same two node ids.
- `eae061d` — `feat(telemetry): per-turn chat attribution, budget caps and
  prompt-cache accounting (#506)`, 16 files / 3532 insertions, an ancestor of
  `origin/master`: the tree the bomb shipped in.
- The closing evidence comment on #506 (`2026-09-14T16:02:12Z`, `47 passed in
  0.59s`) and the operator's reopen of 2026-09-17 that measured the same red and
  named the mechanism. #506 is `open` on the committed board snapshot.
- The wiring: `scripts/verify.sh:526` (`pytest-chat` → `gateway/chat/tests`),
  `scripts/verify.sh:510-513` (the declared manifest runs under `make gate` /
  `make tests`), `scripts/gate.sh:85`, `scripts/pytest-suites.txt:114`
  (`telemetry/chat`).
- The fixture and the call site: `telemetry/chat/tests/conftest.py:38-40,175`
  and `telemetry/chat/budget_guard.py:288-303`.

## Follow-up

- `CA-0010`, `CA-0011` and `CA-0012` are open, and each names the issue that
  carries it (#506 via PR #1026, #1025, #1028).
- `INC-0008` therefore stays **open**: the instance is not on `master` yet, and
  an incident is not closed on an unmerged fix. It closes when PR #1026 merges
  and its control is observed refusing by name with the derivation removed.
- **Ids are allocated against `origin/master`, not against the branch's base.** This RCA is
`RCA-0008` and its incident `INC-0008` because master's ledger **already held** `INC-0007`,
`RCA-0007`, `CA-0009` and `LESSON-0005` — landed by issue #1029
(`RCA-0007-declared-not-exercised-golive.md`, origins #607/#1029) *after* this branch was cut. The
first allocation was `INC-0007` / `RCA-0009` (chosen then to dodge the unlanded `docs/rca` writeup's
`RCA-0007`/`RCA-0008`); merging it would have put two authoritative lines under one id and stopped
`ledger.jsonl` being the single record. The reallocation is mechanical — the next free number per prefix
**on master** (`INC-0008`, `RCA-0008`, `CA-0010..0012`) — and was verified by re-reading the file and
re-running the gate. What surfaced it was `scripts/check-cross-repo-lessons.sh`, which reports
`local-record-undisclosed INC-0007` against a clean `origin/master` while this branch calls the same id
its own. Durable rule: an id space a concurrent lane can consume must be allocated at **land** time,
against the ref being merged into.
