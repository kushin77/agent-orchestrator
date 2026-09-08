# QA gate stack — `make gate`, qa-loop, merge gate

Issue **#29** (EPIC-00, Phase 4, guardrails pillar, work item 25 — top-level
gate integration lane). This lane owns the **top-level gate integration**: the
`Makefile`, `scripts/**` and this document. It CONSUMES — never redefines — the
guardrails/honesty model (issue #28, tri-state + negative controls) and the
guardrails/policy module (issue #26), and it wires the repo doctrine
([`AGENTS.md`](../AGENTS.md),
[`GOLDEN-RULES.md`](GOLDEN-RULES.md) AO-GR-3/4/11/19) into executables.

The stack is fully offline: shell + Python stdlib + PyYAML + pytest. No
network, no containers.

---

## 1. Why this exists

`make verify` is the repo's 8-check composite gate of record. But the product
has accumulated pytest suites across the pillars that **individually pass yet
cannot run in one combined `pytest` invocation** — sibling suites collide on
`conftest.py` sys.path bootstrap (`from conftest import cfg`) and on duplicate
test-module basenames (`test_budget.py`, `test_events.py`, `test_config.py`,
…). A combined run aborts during collection (observed: 27 collection errors).
So the QA gate must:

1. run **every** suite, each **in isolation**, aggregating honestly;
2. never be a formality — a missing or failing suite must fail the gate and an
   empty/absent result must never read as a pass (AO-GR-4, issue #28);
3. gate merges: nothing merges unless verification is green **and** attested
   (AO-GR-11, GR-12).

## 2. The stack at a glance

| Entry point | What it runs | Evidence |
|---|---|---|
| `make verify` | 8-check composite (unchanged): shell-syntax, yaml-lint, json-lint, docs-lint, secrets, feature-flags, cloudbuild, terraform | `.verify/verify.log`, `.verify/attestation.json` |
| `make gate` | `scripts/gate.sh` — verify composite + policy-schema + guard negative-controls + **per-suite tests** + drift + merge-gate wiring; tri-state aggregate + attestation | `.verify/gate.log`, `.verify/gate-attestation.json`, `.verify/test-results.json` |
| `make qa-loop` | `scripts/qa-loop.sh` — fix → verify → re-check until green (or no auto-fix left) | reuses gate evidence |
| `make merge-gate` | `scripts/merge-gate.sh run` — pre-merge contract (refuses dirty tree, full gate, writes commit-named attestation) | `.verify/merge-attestation.json`, `.verify/merge-gate.log` |
| `make tests` | `scripts/run-pytest-suites.sh` — every declared suite in isolation | `.verify/test-results.json` |

### `make gate` signal table (per-signal evidence)

The issue #29 acceptance criteria name these signals. `make gate` reports each
one, mapped as follows:

| Issue signal | Gate signal | Mechanism (can genuinely fail) |
|---|---|---|
| lint / syntax | `verify` → shell-syntax + yaml-lint + json-lint | `scripts/check-shell-syntax.sh`, `check-yaml.py`, `check-json.sh` |
| secrets scan | `verify` → secrets | `scripts/check-secrets.sh` |
| doc refs | `verify` → docs-lint | `scripts/check-docs.sh` |
| policy schema validate | `policy-schema` | `scripts/check-policy-schema.sh` → `python3 guardrails/policy/cli.py validate` (issue #26 startup validation over shipped bundles + controls) |
| guard negative-controls | `guard-negative-controls` | `scripts/check-negative-controls.sh` → `python3 -m honesty negative honesty/manifest.negative.yaml` + `python3 -m honesty analyze … --strict` (issue #28) |
| tests | `tests` | `scripts/run-pytest-suites.sh` — every declared suite run in **isolation** (see §4) |
| drift | `drift` | `scripts/check-drift.sh` — suite manifest ↔ tree coherence (a declared suite may not vanish; a suite may not run undeclared) |
| merge-gate | `merge-gate` | `scripts/merge-gate.sh --wiring` — pre-merge machinery present **and** proven able to block |

`feature-flags`, `cloudbuild` and `terraform` remain additional `verify`
sub-checks inside the `verify` signal.

### Tri-state aggregation (issue #28, consumed not redefined)

Every signal exits with the guard contract: `0` = OK, `1` = NOT-OK, `2` =
CANNOT-ASSESS (`124` = timeout ⇒ CANNOT-ASSESS). `scripts/gate.sh` maps each
signal's exit code to a status with `python3 -m honesty status <rc>` and
aggregates with `python3 -m honesty aggregate <statuses…>`:

- any **NOT-OK** fails the gate;
- otherwise any **CANNOT-ASSESS** keeps the gate from green;
- only an all-**OK** set reads `GATE: PASS`.

If the honesty model itself cannot run, gate.sh falls back to the *identical*
fail-closed mapping (0→OK, 1→NOT-OK, everything else→CANNOT-ASSESS) and still
never reports green. CANNOT-ASSESS is never a pass.

> **Invocation note (correction):** the `honesty` package lives at
> `guardrails/honesty` and is importable **only from its parent**
> (`guardrails/` has no `__init__.py` and `honesty` is not nested under
> itself). It must be run as `cd guardrails && python3 -m honesty …`, **not**
> `cd guardrails/honesty && python3 -m honesty …` (that fails with
> `No module named honesty`). `check-negative-controls.sh` encodes the correct
> form.

## 3. Exit codes and honesty guarantees

| Code | Meaning | Caller action |
|---|---|---|
| 0 | gate green, all signals OK | may merge (with attestation) |
| 1 | NOT-OK — a signal found a real defect | fix the cause; never loosen the check |
| 2 | CANNOT-ASSESS — no determinate verdict | never treated as a pass; investigate |

No-false-green (AO-GR-4) is enforced three ways:

1. every signal can genuinely fail (each maps to a real exit code);
2. `SKIP`/empty/absent results are never passes (`run-pytest-suites.sh` exits
   NOT-OK on an empty manifest; `check-drift.sh` exits NOT-OK if a declared
   suite vanished);
3. `scripts/merge-gate.sh --self-test` is a negative control that proves the
   merge gate **can refuse** (a gate that never refuses is a formality).

## 4. Why per-suite isolation, and how a new suite is added

The test corpus is declared in the authoritative manifest
[`scripts/pytest-suites.txt`](../scripts/pytest-suites.txt) — one
repository-root-relative module per line (its suite lives at `<module>/tests`).

`make gate` → `run-pytest-suites.sh` runs each declared suite **alone**:
`timeout $SUITE_TIMEOUT python3 -m pytest -p no:cacheprovider -q <module>/tests`
(from the repo root), then aggregates:

- suite exit 0 → OK;
- suite exit ≠ 0 (failed tests, collection error, no-tests) → NOT-OK;
- suite timed out / killed (124/137) → CANNOT-ASSESS (no verdict was reached);
- declared suite directory missing → NOT-OK (coverage silently lost).

Each suite's own `tests/conftest.py` handles its `sys.path` bootstrap, so the
isolated invocation is correct for all current suites (verified: 39/39 pass on
master after the issue-#46 E2E lane registered `e2e`, `governance/sync` and
`infra/rollout`). `scripts/check-drift.sh` additionally warns about committed
`tests/` dirs that are not declared, so coverage can never grow off the books.

**To add a new suite:**

1. create `<module>/tests/` with `conftest.py` (sys.path bootstrap) + tests;
2. add `<module>` to [`scripts/pytest-suites.txt`](../scripts/pytest-suites.txt);
3. run `make tests` — the new suite must pass in isolation;
4. run `make gate` — `drift` confirms the manifest matches the tree.

## 5. The merge gate (GR-12 doctrine, wired to AGENTS.md)

[`AGENTS.md`](../AGENTS.md) is canonical doctrine and is **not** edited by this
lane; this section documents how the doctrine is enforced by
`scripts/merge-gate.sh`:

- **Every change lands via a PR and nothing merges on a self-report** — the
  gate produces actual output (GR-12) and `merge-gate.sh run` refuses to run
  on a dirty working tree, because an attestation names a **commit** (a tree
  that is not committed is not what anyone else will ever fetch).
- **Merge only on green** — `merge-gate.sh run` runs verify + drift + every
  declared suite in isolation + guard negative-controls + policy-schema and
  writes `.verify/merge-attestation.json` naming the commit. A merge whose
  gate reports CANNOT-ASSESS is a merge without a verdict and is equally
  rejected (AO-GR-11).
- **Wiring is not assumed** — `make gate`'s `merge-gate` signal runs
  `merge-gate.sh --wiring`, which checks every gate component is present and
  that the merge gate passes its own negative self-test (it can block).

Workflow: implement → `make verify` → `make gate` → commit → push → open PR
(`Closes #29`-style) → run `make merge-gate` on the committed branch → attach
its output to the PR/issue → merge → close the issue with evidence.

## 6. qa-loop — fix → verify → re-check

`scripts/qa-loop.sh` runs `make gate` and, on failure, applies the documented
**mechanically-safe** auto-fixes (see `--list-fixes`), then re-runs until the
gate is green or no auto-fix can make progress. It never loosens a check to get
green; a failure outside the auto-fix classes is reported for manual work.

```bash
bash scripts/qa-loop.sh              # continuous loop
bash scripts/qa-loop.sh --once       # single gate run
bash scripts/qa-loop.sh --list-fixes # document the auto-fix classes
```

The only auto-fix class implemented is `TRAILING-WS` (strip trailing blank
space from files docs-lint flags), and it is restricted to files the **current
change-set** already touches — never another lane's files, so parallel lanes
cannot be clobbered.

## 7. Provenance (cannibalized shapes, per `docs/CANNIBALIZATION.md` doctrine)

| Shape | Source | Adapted as |
|---|---|---|
| honest gate exit-code contract; UNKNOWN never a pass; name the failing suite | leaderboard `scripts/qa/qa-gatekeeper.sh` + `signals.d/tests.sh` + `run-test-suites.sh` (READY-TO-REUSE) | `scripts/gate.sh`, `scripts/run-pytest-suites.sh` |
| fix → verify → re-check loop; `--once` / `--list-fixes`; auto-fix only mechanically safe classes | leaderboard `scripts/qa/qa-loop.sh` | `scripts/qa-loop.sh` |
| attestation names a commit; refuse on dirty tree; negative self-test | leaderboard `scripts/qa/gate-attest.sh` | `scripts/merge-gate.sh` (refusal + `--self-test`) |
| negative controls prove a guard can fail (blockproof) | guardrails/honesty (issue #28) | consumed via `check-negative-controls.sh` |
| `make gate` = full check set; `make verify` composite; GR-12 verify-with-output | CMR `Makefile` (`gate: verify`), hub `GOLDEN-RULES.md` GR-12 | `Makefile` targets; `scripts/verify.sh` unchanged |
| guard honesty doctrine (BLOCK/WARN/LOG, tri-state, negative controls) | AO-GR-19, issue #28 | aggregation model in `scripts/gate.sh` |
| per-suite budget + tri-state on hang (124/137 = no verdict) | leaderboard `run-test-suites.sh` (#1992) | per-suite timeout handling in `run-pytest-suites.sh` |

Sources were read from the read-only research/vendor mirrors
(`.research/leaderboard/scripts/qa/**`, `.research/CMR/Makefile`); nothing was
copied verbatim — shapes were adapted to this repo's pytest model.

## 8. Verification (both directions)

```bash
make verify          # 8/8 green (gate of record, unchanged)
make gate            # GATE: PASS — verify + policy-schema + controls + tests + drift + merge-gate
bash scripts/qa-loop.sh --once
make merge-gate      # MERGE-GATE: PASS on a clean committed tree
```

Negative proof (the gate genuinely fails): plant a failing suite by adding a
throwaway `<module>` with a deliberately failing test to
`scripts/pytest-suites.txt` → `make gate` exits nonzero naming the suite →
remove the throwaway + manifest line → `make gate` green again. Both directions
are asserted before this stack is merged.
