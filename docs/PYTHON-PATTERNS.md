# Python patterns — the shapes this repository refuses (issue #1028)

> **This canon is language-scoped to Python.** A *shell* shape is not a pattern
> here and a Python shape is not a pattern there: the shell canon is its own
> file, [`SHELL-PATTERNS.md`](SHELL-PATTERNS.md), and neither file is a superset
> of the other. A shape that arrives in shell belongs to that doctrine; a shape
> that arrives in Python belongs to this one.
>
> **Every pattern here was harvested from a failure this repository paid for.**
> The one this canon opens with is the **#506 date bomb** — a green that expired
> with the calendar, not with a commit.
>
> **This file ships no gate, and that is a decision rather than an oversight.**
> The pattern below is not a line shape: the *repair* for it and the *defect* it
> came from carry **byte-identical** source lines, so no static scan of the
> fixture can separate them (measured — see **The measured refusal**, below).
> The enforcement therefore lives where the defect actually lives: a
> *behavioural*, mutation-proved control on the gate of record,
> [`scripts/check-chat-finops.sh`](../scripts/check-chat-finops.sh)'s
> `turn-date-scope`. Read that section before writing a detector for `PP-1`.

## Provenance (GR-10)

Adapted, not copied.

| | |
|---|---|
| Source of the *form* | [`docs/SHELL-PATTERNS.md`](SHELL-PATTERNS.md) — the shape of this file (a provenance note, a measured pattern table, a BAD/GOOD section per pattern, an honest DECLARED section) is that doctrine's; this one mirrors it and stays Python-scoped |
| The shell canon at | blob `f72cbb68126f345a8213cb371bc1df262c12dda3` (landed by `ddacd269`, "shell-pattern doctrine + the gate that refuses it by name (#621)") |
| Source of the *pattern* | this repository's own measured failure, issue **#506** (see [the measured instance](#pp-1--a-fixture-that-pins-a-seed-but-not-the-evaluation)) |
| Measured against | `origin/master` `ffe3f9d`, 2026-09-16 |
| Adapted by | issue #1028 |
| License | nothing was copied from the hub; the pattern is this repository's own failure and the *form* is this repository's own doc |

**What was deliberately not carried over.** The shell doctrine ends every pattern
with a gate that refuses it by name. This one does **not**, because the
measurement below says a gate for `PP-1` would refuse the fix as loudly as the
defect. Shipping it anyway would be the exact formality the shell doctrine's own
§"How the gate proves itself" exists to deny (GR-12, AO-GR-4). What is carried
over is the honest half: the pattern, the failure, the good shape, and the
measurement that says where enforcement belongs.

## The pattern

| id | the shape | the failure it prevents | measured on `master` (2026-09-16) |
|----|-----------|-------------------------|-----------------------------------|
| `PP-1` | **a fixture that pins a *seed* but not the *evaluation*** — the fixture writes its data under one time bucket while the subject under test resolves the bucket it *judges in* somewhere else, most often from the live clock | the green expires with the calendar: it is correct on the day the fixture was written and wrong every day after, with no commit in between | **2 modules** — `telemetry/chat/tests/test_budget_guard.py`, `telemetry/chat/tests/test_negative_controls.py`: `2 failed, 45 passed` on `master` `ffe3f9d`, reproduced today (lane **#506**, PR **#1026**, repairs them) |

**Enforcer**, for that row: not a static scan — a behavioural, mutation-proved
control. See [what enforces this today](#what-enforces-this-today).

### `PP-1` — a fixture that pins a seed but not the evaluation

**What it is.** A time-bucketed budget/quota rail (a day bucket, a month bucket)
is *seeded* by a fixture under one instant, and the code under test *judges* a
turn in a bucket it resolved by itself. The two agree only by coincidence — and
the coincidence is the calendar.

**The failure it prevents.** Measured, in this repository, and reproduced today
against `origin/master`:

```
$ cd /home/akushnir/ao-worktrees/ao-1028-3ec9daca      # at origin/master ffe3f9d
$ python3 -m pytest telemetry/chat -q -p no:cacheprovider
FAILED telemetry/chat/tests/test_budget_guard.py::test_a_quota_exhausted_tenant_never_reaches_the_provider
FAILED telemetry/chat/tests/test_negative_controls.py::test_each_refusal_path_refuses_and_still_meters[quota]
2 failed, 45 passed in 0.75s
```

The mechanism is not "a test with a date in it". It is this asymmetry:

* the fixture seeds the rail for a **pinned day** —
  `StaticLedger(calls={(world.tenant, world.day): calls})` — and the rails
  resolve an unset bucket from the **live** clock
  ([`telemetry/budgets/ledger.py`](../telemetry/budgets/ledger.py):
  `daily_calls(..., day=None)` → `day or today_utc()`);
* the subject under test was invoked **without** a bucket and did **not** derive
  one — so it consulted *today's* bucket while the seed sat in *the fixture's*.

Those two agree on exactly one calendar day. Before that day the test proves
nothing; after it the seed misses, an **exhausted tenant is allowed**, the turn
reaches a provider with `provider_called=True`, and the refusal control fails.
The green was a function of the date, not of the code.

```python
# BAD — the seed is pinned, the evaluation is not: the green expires with the calendar
DAY = "2026-09-14"
rail = StaticLedger(calls={(tenant, DAY): 20})   # seeded on the pinned day
runner.run(turn)                                 # judged against *today* → 0 usage → allowed

# GOOD — the subject derives its bucket from the entity under test, so the two agree
#        because of what the turn says and never because of which day the suite runs
runner.run(turn)   # the guard derives day/month from turn.normalized_ts
```

The good shape is not "pass a bucket to the rail". It is **the subject under
test resolving the bucket from the entity it was handed** — `turn.normalized_ts`
here — and the test then *asserting the window the verdict names*, so a subject
that silently fell back to the clock is caught. That is what the repair does,
and what its sibling control proves.

**The measured instance.** `telemetry/chat`, issue **#506**: its two
quota-refusal tests were green on **2026-09-14** — the day the lane ran — and
red every day since, identically at that issue's own closing squash. The
calendar, not a commit, turned the gate red. Repaired in lane #506 (PR #1026),
which threads the turn's own bucket through the guard and puts a mutation-proved
control on the gate of record.

**The class is wider than the instance.** The populating census, measured
2026-09-16 over the 1609 tracked `*.py` files (the pinned `vendor/` submodule is
a gitlink and `.research/` is gitignored, so neither contributes a tracked file):

| measured shape | sites |
|----------------|-------|
| test modules (files under `tests/`, `test_*.py`, `conftest.py`) | 807 |
| ... of those, containing a pinned `YYYY-MM`-style date literal | **47** |
| a live-clock **bucket fallback** (`x or today_utc()`, `x or this_month_utc()`, `x or datetime.now(...)`) | **40** |
| a bucket/timestamp name assigned or returned **from** a live clock | **143** |
| a raw clock read (`datetime.now` / `utcnow` / `date.today` / `time.time()`) in a **non-test** module | **124** |

Those last three rows are why there is no broad rule here: a check that flags 40,
124 or 143 files refuses everything, so it cannot fail for the right reason. The
seam consolidation the numbers point at is **#1025** (the money path reads the
live clock in 11 places behind 6 duplicate seams) — not this canon.

## The measured refusal — why no `scripts/check-python-patterns.sh` ships

The brief for #1028 asks for a `scripts/check-python-patterns.sh` in the shape of
[`scripts/check-shell-patterns.sh`](../scripts/check-shell-patterns.sh):
auto-discovered into `make verify` by
[`scripts/discover-checks.sh`](../scripts/discover-checks.sh) (#698), tri-state
(0 clean / 1 refused / **2 CANNOT-ASSESS** — never 0), a `REFUSED PP-1 <label>`
that names the pattern, a `--files` seam so a plant outside the repository can be
scanned, and both halves provoked. Every formulation was built and measured.
None survived, and the reason is not that the pattern is hard to *write down* —
it is that **the shape is not in the fixture**.

### What was tried, and what it flagged

| formulation | measured result | why it is not an enforcement |
|-------------|-----------------|------------------------------|
| **A** — a module-level date-ish constant used as a **dict key** (a seed), in a module that passes no bucket keyword (`day=`/`month=`/`now=`/`ts=`) | **0 files** | it does not contain the measured instance. #506's fixture builds its seed from a `World` dataclass attribute (`world.day`), never from a bare key, so the rule that was asked for cannot see the failure it is named after — a rule that finds nothing is not a gate on this pattern |
| **B** — a *dated seed key* (a dotted bucket such as `world.day`/`world.month`, a date constant, or a quoted date literal) in a module that passes no bucket keyword | **4 files**, of which **3 are false positives** in non-test code (`isinstance(value, datetime.date)`, a positional `args.date`, a docstring fragment) and the fourth is the instance — while the *sibling* instance in the same package is skipped only because an unrelated `ts=world.ts` appears elsewhere in it | the verdict turns on incidental text rather than on the shape: adding a keyword to an unrelated constructor changes the answer. That is not a measurement of the defect |
| **C** — any pinned date literal at all | **47 of 807** test modules | the formality the brief names: flags everything, so it cannot fail for the right reason |
| **D** — any live-clock bucket fallback, or any raw clock read on the money path | **40** / **143** / **124** sites | same, and it would redden `master` on land on code that is *correct*: the ledger's fallback is the seam that serves live traffic |

### The falsification that settles it

The strongest form of B — the tightest rule that still catches the measured
instance — was built as a throwaway probe (`/tmp/ao1028-pp1-probe.sh`, outside
this repository) and run against the defect and against **its own repair**:

```
=== PROBE on the BOMB (origin/master ffe3f9d) ===
REFUSED PP-1 seed-without-pin .../BOMB_test_budget_guard.py (lines 61 173 )
REFUSED PP-1 seed-without-pin .../BOMB_test_negative_controls.py (lines 45 61 )
probe rc=1

=== PROBE on the REPAIRED / good code (origin/issue-506, PR #1026) ===
REFUSED PP-1 seed-without-pin .../REPAIRED_test_budget_guard.py (lines 82 142 206 )
REFUSED PP-1 seed-without-pin .../REPAIRED_test_negative_controls.py (lines 48 64 )
probe rc=1
```

The rule refuses the defect **and the fix**, and the lines it refuses are the
*same statements* — because **the seed line is byte-identical in both**:

```
$ git show origin/master:telemetry/chat/tests/test_budget_guard.py | grep -n StaticLedger
61:    ledger = StaticLedger(calls={(world.tenant, world.day): calls})

$ git show origin/issue-506:telemetry/chat/tests/test_budget_guard.py | grep -n StaticLedger
82:    ledger = StaticLedger(calls={(world.tenant, world.day): calls})
```

The fixture was never what was wrong. `world.day` and `world.ts` come from one
fixture and agree by construction; the defect was that the *subject* ignored the
entity it was handed and asked the clock instead. A detector aimed at the
fixture therefore cannot distinguish the broken code from the repaired code —
and a gate that refuses the repair is worse than no gate: it would redden
`master` the moment #1026 lands, and the only ways out would be to allowlist the
fix or to soften the rule until it matched nothing.

**That is a measured refusal, not a failure to write a regex.** The pattern is
real, the instance is real, and the enforcement exists — it is simply not here,
because it is not a property of this file's subject matter.

## What enforces this today

The defect is a property of the **subject**, so the control is behavioural and
belongs with the subject's own gate. Lane #506 (PR **#1026**) puts it on the gate
of record in [`scripts/check-chat-finops.sh`](../scripts/check-chat-finops.sh):

* **control `turn-date-scope`** — a rail exhausted *only* on a **literal past
  day**, and two turns dated by literal: the turn dated the exhausted day must be
  **refused**, the turn dated another day must be **served**. It reads no clock,
  so neither half can expire, and it fails **by name** —
  *"the evaluation bucket is not the turn's own (#506), so a refusal rail can
  never bite on a past-dated turn"*;
* **the mutant** — the same control run against a guard that judges by the live
  clock (the pre-fix behaviour) must be **caught by that same name**. A control
  that cannot fail for the right reason is a formality, and this half is what
  stops the proof from being one.

So the doctrine's instruction for this shape is behavioural, not static:

1. **The subject derives its bucket from the entity.** A rail handed no bucket
   must not fall back to the clock on a path that already carries the entity's
   own instant — derive it (`turn.normalized_ts`), and let an explicit argument
   *override* the derivation rather than *enable* it.
2. **The verdict names its window.** An outcome that reports only a decision
   leaves the window to be inferred from when the check ran. Carry the bucket
   the verdict was judged in, and assert it.
3. **A test that pins a time bucket dates its entity from a literal past
   instant, and asserts the window the verdict names.** Nothing in it reads the
   clock, so neither half can expire.

## Declared, measured, not enforced

The honest counterpart of the refusal above: real shapes in this family that
this canon does **not** mechanically refuse, each named with its measurement and
its reason rather than left silently uncovered.

| id | the shape | measured | why it is declared, not enforced |
|----|-----------|----------|----------------------------------|
| `PP-2` | a fixture whose pinned bucket is derived **from the live clock** (`DAY = today_utc()`), so seed and evaluation move together and the test can neither expire **nor** catch a subject that ignores the entity | **0 sites** in the narrow form (`PP-1`'s mirror) | 0 sites today is not the same as unambiguously wrong: a test that deliberately exercises **today's** window — which the #506 repair ships, to prove live behaviour is unchanged — is legitimate, so the rule would refuse correct code the first time someone writes that test honestly |
| `PP-3` | a rail whose unset bucket falls back to the live clock (`x or today_utc()`) | **40** sites box-wide | it is the *seam*, not the defect: it is what serves live traffic, and the #506 repair keeps it while stopping the *subject* from reaching it unset. Retiring it means consolidating the duplicate seams in **#1025**, in one lane that owns those modules |
| `PP-4` | a raw clock read on the money path | **124** sites (non-test), **11** on the metering/FinOps path per #1025 | same: **#1025** is the lane, and until it lands a gate here would be red on `master` — a gate that is red on `master` is disabled or ignored, which is strictly worse than no gate |

## Using this canon

1. **When you write a test that pins a time bucket**, pin the *entity's* instant
   too and assert the window the verdict reports — then the test is a function of
   the code and not of the calendar.
2. **When you write a subject that resolves a bucket**, resolve it from the
   entity you were handed. An unset bucket defaulting to `now` is how the next
   date bomb is built.
3. **When you add a pattern to this file**, bring all three: (a) a failure this
   repository paid for, measured; (b) the shape written down precisely enough
   that a reader can avoid it; and (c) — if you claim it is *enforced* — the
   gate, the refusal string, and the provocation that shows it fires, **including
   the half that proves it does not fire on the good shape**. A pattern that can
   meet (a) and (b) but not (c) belongs in the DECLARED table above with its
   measurement, never in a gate that cannot fail for the right reason (GR-12).

See [`GIT-TEMPLATES-GAP-ANALYSIS.md`](GIT-TEMPLATES-GAP-ANALYSIS.md) for the gap
that created the shell canon's lane (#608), and issue **#1025** for the class
this entry is an instance of.
