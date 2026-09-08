# guardrails/honesty — guard honesty: tri-state status + no-false-green + negative controls

Issue **#28** (EPIC-00, Phase 4, guardrails pillar).  This subtree is the
**honesty MODEL** every gate and guard in this repo is governed by: a guard's
status is a tri-state, a guard that cannot fail is a *formality* and is worse
than none, and a guard that ships no negative control proves nothing.  The
honesty layer is self-contained: it adds no dependency on the `Makefile` or
`scripts/` gate, it runs fully offline, and a later lane wires it into the QA
stack.

This file is the codified doctrine for all repo gates (no-false-green,
AO-GR-4; guard honesty, AO-GR-19).  It binds the guards this repo runs and the
guards the platform will enforce on tenants.

---

## 1. The tri-state status contract

Every guard reports exactly one of three statuses, and **the exit code is the
wire format**:

| Status | Exit code | Meaning |
|---|---|---|
| **OK** | `0` | the property the guard checks holds (PASS) |
| **NOT-OK** | `1` | the property is violated (FAIL) |
| **CANNOT-ASSESS** | `2` | the guard could not determine the property; also `124` (timed out) and any non-contract exit code. **Never reads as a pass.** |

`PASS` / `FAIL` / `UNKNOWN` are accepted aliases for OK / NOT-OK /
CANNOT-ASSESS (fleet umbrella wording).  This status axis is the *honesty* of
a guard; the gate engine's runtime decision (BLOCK / WARN / LOG, AO-GR-19) is
a different axis that **consumes** this one.

The contract is enforced by [`tristate.py`](tristate.py):

* `from_exit_code(0|1|2|124|…)` — fail-closed mapping; an unexpected exit code
  means the guard is broken or was killed, so it maps to CANNOT-ASSESS, never
  OK.
* `aggregate(verdicts)` — a single NOT-OK fails the gate; otherwise any
  CANNOT-ASSESS keeps the aggregate from reading PASS; only an all-OK set
  aggregates to OK.  **UNKNOWN is never a pass.**
* `parse` accepts both vocabularies; an unrecognized status raises rather than
  silently passing.

## 2. No-false-green (why this exists)

A check whose pass path and gave-up path produce the same exit code is a
**formality**: it reads as coverage while guaranteeing none.  A gate that
cannot fail protects nothing — it is *worse* than no gate, because it looks
like coverage while guaranteeing none.  Therefore:

1. **Every gate can genuinely fail.**  New checks are probed on a known-bad
   input and observed to exit nonzero (AO-GR-4).
2. **SKIP is not a pass.**  On an authoritative surface, a check that skips a
   thing it was required to check has failed, not passed.
3. **CANNOT-ASSESS is not a pass.**  A guard that could not assess attests
   nothing; its verdict keeps the aggregate from green.
4. **Mechanism, not message.**  Checks anchor on what the code does (exit
   codes, counters, actual artifacts), never on prose that merely *says* it
   checked something.

### Formality shapes gallery

The anti-formality scanner ([`analyzer.py`](analyzer.py)) statically flags the
documented shapes:

| Shape | Rule | What it looks like |
|---|---|---|
| Identical-exit-code paths | `never_fails_function` / `never_fails_script` | a check-named function/script that can `return 0` / `exit 0` but has no failing exit anywhere |
| SKIP counted as PASS | `skip_counted_as_pass` | a check function whose SKIP path returns 0 and that has no failing exit at all |
| Unguarded skip | `uncounted_skip` | `[ -e "$f" ] \|\| continue` with no counter anywhere in the file — a run that examines ZERO entries still reports success |
| Absence-gated check | `never_fails_script` + optional else | blocking gate runs only when an artifact exists; when every artifact is absent the `else` echoes "optional" and the guard still exits 0 |
| Self-matching pattern | `self_match` | a grep whose quoted pattern also appears in a nearby comment explaining that grep — the check can match its own documentation |

Findings are **review aids, not verdicts**: a guarded `continue` beside a real
counter is usually correct, and the scanner cannot always see the counter.
Each finding answers one question — *run it against the thing it is supposed
to catch: what is the exit code?*  A line that has been reviewed carries its
reason inline and is suppressed:

```sh
[ -e "$f" ] || continue   # formality-ok: entries are tallied at the end
```

Run the scan in strict (gate) mode with:

```bash
python3 -m honesty analyze fixtures corpus --strict
```

## 3. Negative controls + blockproof

A guard that only tests the happy path proves nothing; a guard with a control
that correctly FAILS proves it discriminates.  **Every guard ships a negative
control** — a planted mutation it MUST fail on — plus a positive control
(clean input it must pass) and, where relevant, a CANNOT-ASSESS control
(proving the UNKNOWN path never reads as a pass).

Controls are declared in [`manifest.negative.yaml`](manifest.negative.yaml)
and run by [`negative_control.py`](negative_control.py).  Each run records a
**blockproof** — the actual exit code, the observed verdict, the expected
verdict, and the output — so a guard's claim to block is evidenced, not
vibes:

```bash
python3 -m honesty negative manifest.negative.yaml --report /tmp/blockproofs.json
```

A guard that **cannot fail fails its own negative control**: when a control
expects NOT-OK and the guard returns OK on the planted violation, the control
does not pass and the runner exits nonzero.  That is the no-false-green
guarantee made executable.

## 4. Guard attestation (evidence, not vibes)

Every guard verdict is recorded as a [`GuardAttestation`](attestation.py):
guard id, tri-state verdict (derived from the exit code, never asserted by
hand), raw exit code, the actual output that produced the verdict, UTC
timestamp, git sha/branch, provenance, and the negative controls that prove
the guard can fail.

```bash
python3 -m honesty attest --guard-id check_blocklist \
  --rc 1 --evidence "violation present in fixtures/inputs/violation.txt" \
  --controls blocklist_violation -o /tmp/attestation.json
```

A verdict is attached to each merge/verdict; a merge whose guard reports
CANNOT-ASSESS, or whose evidence is empty, is a merge without evidence and is
rejected by policy.

## 5. Corpus of real incident texts

The analyzer and its tests are tuned and validated against the **real
captured artifacts** in [`corpus/`](corpus/) (`fail/` = formalities caught in
production incidents; `pass/` = honest guards that must be left alone), with
provenance recorded in [`corpus/README.md`](corpus/README.md).  The synthetic
fixtures under [`fixtures/`](fixtures/) cover the same shapes for unit tests;
the corpus proves the tool works on real text, not only invented examples.

## 6. Guard-author contract

A guard added anywhere in this repo must, to be honest:

1. Return a tri-state exit code from `{0, 1, 2}` (and `124` on timeout) and
   never let a gave-up path share the success exit code.
2. Return `2` (CANNOT-ASSESS) when it cannot assess — never `0`.
3. Treat SKIP as FAIL on surfaces where the thing was required.
4. Ship a negative control (a case it must FAIL) + a blockproof in its test
   suite; CI runs them.
5. Count what it skips/examines whenever it loops over artifacts.
6. Anchor on mechanism (exit codes, artifacts, counters), not on message text.
7. Clear `python3 -m honesty analyze <its own files> --strict`.

## 7. Layout

| Path | Purpose |
|---|---|
| [`tristate.py`](tristate.py) | TriState model, exit-code contract, fail-closed aggregation, JSON |
| [`analyzer.py`](analyzer.py) | Anti-formality/self-match scanner (review aid + `--strict`) |
| [`negative_control.py`](negative_control.py) | Negative-control + blockproof runner |
| [`attestation.py`](attestation.py) | Guard attestation records |
| [`cli.py`](cli.py) / [`__main__.py`](__main__.py) | `python3 -m honesty …` CLI |
| [`manifest.negative.yaml`](manifest.negative.yaml) | Declared controls for the fixture guards |
| [`fixtures/`](fixtures/) | Synthetic honest/formality/self-match fixtures + inputs |
| [`corpus/`](corpus/) | Real incident artifacts (fail + pass) with provenance |
| [`tests/`](tests/) | pytest suite (positive + negative, both directions) |

## 8. Verification

```bash
cd guardrails/honesty
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests -q            # honest gates + negatives
python3 -m honesty analyze fixtures/honest corpus/pass --strict # shipped guards are clean
python3 -m honesty negative manifest.negative.yaml             # negative controls
python3 -m honesty status 1 && python3 -m honesty aggregate OK NOT-OK CANNOT-ASSESS
```

`fixtures/formality`, `fixtures/selfmatch` and `corpus/fail` are deliberate
negative-test material (guards that must be CAUGHT); the pytest suite asserts
the analyzer catches every one of them and clears every honest one.

All offline, standard library + PyYAML only.
