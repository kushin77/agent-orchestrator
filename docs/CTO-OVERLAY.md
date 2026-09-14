# CTO Overlay — per-repo drop-in governance

The **CTO overlay** gives a repository a four-layer governance team —
executive, engineering, devops, support — as a versioned artifact that travels
with the tree. It is schema-validated, tiered, and its verdict can genuinely
fail: a layer declared `blocking` stops the run, a `warning` layer reports and
lets it pass, and a check that could not be run is never counted as clean.

- Engine: [`governance/cto-overlay/overlay.py`](../governance/cto-overlay/overlay.py)
- Config: [`governance/cto-overlay/config.yaml`](../governance/cto-overlay/config.yaml)
- Schema: [`governance/cto-overlay/schema.yaml`](../governance/cto-overlay/schema.yaml)
- Short guide: [`governance/cto-overlay/README.md`](../governance/cto-overlay/README.md)
- Gate: [`scripts/check-cto-overlay.sh`](../scripts/check-cto-overlay.sh)

Tracked by issue #147 (parent #144). Ported from `kushin77/leaderboard` — see
[Provenance](#provenance).

## The four layers

| Layer       | Domain                                   | Checks                                                     | Declared severity |
|-------------|------------------------------------------|------------------------------------------------------------|-------------------|
| `executive` | Architecture governance                  | `adr-check`, `dep-policy`, `security-baseline`             | blocking          |
| `engineering` | Code quality                           | `syntax`, `coverage`, `sast`                               | blocking          |
| `devops`    | Deploy safety                            | `dockerfile-lint`, `compose-validate`, `terraform-fmt`     | warning           |
| `support`   | Self-healing readiness                   | `log-harvest`, `self-heal`, `incident-response`            | warning           |

`blocking` is declared per layer in `config.yaml` (`blocking: true|false`) and
may be overridden per tier. A layer with `enabled: false` does not run at all
and is reported as `DISABLED`; it is never silently omitted.

### What each check asserts

| Check                | Tier gate   | Assertion (fails when)                                                        |
|----------------------|-------------|-------------------------------------------------------------------------------|
| `adr-check`          | always      | `docs/decision-records/` tracks at least one `ADR-*.md` record                 |
| `dep-policy`         | always      | no vendored/generated tree is tracked; every declared submodule is a gitlink   |
| `security-baseline`  | `critical`  | no tracked private key material and no bare `.env` file                        |
| `syntax`             | always      | every tracked `*.sh` outside `vendor/`, `.research/`, `node_modules/` parses    |
| `coverage`           | `standard`  | every suite declared in `scripts/pytest-suites.txt` exists with `test_*.py`     |
| `sast`               | `critical`  | a SAST tool is present; if it is not, the result is INDETERMINATE               |
| `dockerfile-lint`    | always      | every tracked Dockerfile declares a base image                                 |
| `compose-validate`   | `standard`  | every tracked compose file parses as YAML                                      |
| `terraform-fmt`      | `standard`  | `terraform fmt -check -recursive` is clean (offline; INDET when absent)         |
| `log-harvest`        | always      | `telemetry/` exists and tracks at least one Python module                       |
| `self-heal`          | `standard`  | the reconcile worker is tracked and its gate script parses                      |
| `incident-response`  | `critical`  | the RCA template and the fleet watchdog are tracked                            |

`min_tier` is declared per check in `config.yaml`, so the roster is readable
without opening the engine. A check below the selected tier is recorded as
`SKIP` and stays visible in the report.

## BLOCKING / WARNING contract

Severity is resolved in this order, and it decides the exit code:

1. `layers.<layer>.enabled: false` → `disabled` (the layer does not run);
2. `tiers.<tier>.<layer>` when the tier names that layer → that severity;
3. otherwise `layers.<layer>.blocking` — `true` → `blocking`, `false` → `warning`.

The default tier is `standard`. `tiers.standard` must agree with the layer's
own `blocking` flag; a config whose two declarations contradict each other is
refused outright (`CANNOT-ASSESS`), because silently picking one of two
answers is how a blocking layer becomes decorative.

| Layer       | experimental | standard | critical |
|-------------|:------------:|:--------:|:--------:|
| executive   | advisory     | blocking | blocking |
| engineering | warning      | blocking | blocking |
| devops      | advisory     | warning  | blocking |
| support     | advisory     | warning  | blocking |

A `FAIL` or `INDET` at `blocking` severity decides the run. A `FAIL` or
`INDET` at `warning` or `advisory` severity is reported in the layer summary
(`WARNING`) without changing the exit code.

## Non-negotiable signals

Four signals always run, whatever the tier and whether or not their layer is
selected. They are the baseline an operator can rely on in every run:

| Signal            | What it measures                                                       |
|-------------------|------------------------------------------------------------------------|
| `shell_syntax`    | every tracked `*.sh` parses (`bash -n`), exclusions declared in config  |
| `secret_scan`     | tracked text files carry no secret shape (shapes mirrored from `scripts/check-secrets.sh`) |
| `protected_files` | each declared protected path exists, is tracked, is unmodified, and matches `--diff-base` when given |
| `path_integrity`  | tracked and declared paths stay inside the repository root (no traversal, no absolute paths) |

The four are required by the schema: a config that drops or substitutes one is
refused (`CANNOT-ASSESS`), so the baseline cannot be narrowed by editing the
config.

## No-false-green

Carried over from the upstream defect and its fix
(`kushin77/leaderboard#1721`), where ten checks were declared `blocking` and
none of them could block — the overlay found a defect, printed it, and passed.

1. **A verdict is recorded, not printed.** Every check reports through one
   tally; nothing else may affect the exit code, so a later check cannot
   quietly become unable to fail.
2. **INDETERMINATE is a state.** A missing tool is not a clean run and an
   unrun check is not a pass. At blocking severity an unrun check decides the
   exit code, and the code is never `0`.
3. **The tally counts every declared layer exactly once**, including disabled
   and unselected layers, and refuses a duplicate record. A layer that cannot
   be found in the tally is a bug, not a skip.
4. **The engine ships its own negative control.** `self-test` builds a scratch
   checkout, plants real defects, and requires the gate to go red — a gate that
   has never been observed failing is not known to work.

### Exit codes

| Code | Verdict         | Meaning                                                                              |
|:----:|-----------------|--------------------------------------------------------------------------------------|
| 0    | `OK`            | every blocking check and every non-negotiable signal passed; warnings may exist       |
| 1    | `NOT-OK`        | the run completed and something definite failed: a blocking check or a signal         |
| 2    | `CANNOT-ASSESS` | the run is incomplete: config missing/unparseable/invalid/contradictory, unknown check or signal, or a blocking check / signal could not be run |

A definite failure outranks an incomplete assessment: when both are present the
exit code is `1`, because the repository is known bad regardless of what else
could not be measured.

## Running it

```bash
python3 governance/cto-overlay/overlay.py run --tier standard        # 0 / 1 / 2
python3 governance/cto-overlay/overlay.py run --format json          # machine-readable tally
python3 governance/cto-overlay/overlay.py run --layers executive     # signals still run
python3 governance/cto-overlay/overlay.py validate                   # schema-validate the config
python3 governance/cto-overlay/overlay.py list-layers --tier critical
python3 governance/cto-overlay/overlay.py self-test                  # negative control
bash scripts/check-cto-overlay.sh                                    # the standalone gate
```

`run` accepts `--root` (the checkout to assess), `--diff-base` (protect the
declared files against a ref, e.g. the merge base of a pull request) and
`--layers` (a comma-separated subset; the non-negotiable signals run anyway).

## Applying the overlay to a repository

The overlay is a drop-in artifact; applying it copies four files plus a
one-line adaptation of the repository identity, then re-validates the config in
the target:

```bash
python3 governance/cto-overlay/overlay.py apply --target /path/to/repo --dry-run
python3 governance/cto-overlay/overlay.py apply --target /path/to/repo
```

`apply` writes only `governance/cto-overlay/{schema.yaml,config.yaml,overlay.py,README.md}`,
refuses to apply the overlay onto its own source checkout, refuses to overwrite
an existing overlay without `--force`, and exits `CANNOT-ASSESS` when the
artifacts or the target directory are not usable.

To add a check: implement it in `overlay.py`, register it in `CHECKS`, declare
it under the owning layer in `config.yaml` with a `min_tier`, and add a
behavioural test that shows it failing and passing. A check declared in the
config but unknown to the engine is a hard `CANNOT-ASSESS` — an unimplemented
check is not a passing one.

## Provenance

This artifact is a port, not a new design. The vocabulary — layer ids, tier
names, BLOCKING/WARNING semantics, the four signal names — is harvested, and
where the mature shape was bash- and GitHub-Actions-bound it was re-implemented
in Python for this repository (GR-15: no GitHub Actions; gates run from
`make`/cron and Python here).

| Source repo                | Path                                              | Harvested                                                       | Verdict |
|----------------------------|---------------------------------------------------|------------------------------------------------------------------|---------|
| `kushin77/leaderboard`     | `docs/CTO_OVERLAY.md`                             | four-layer model, BLOCKING/WARNING contract, tier table, four non-negotiable signals | harvested as the architecture vocabulary |
| `kushin77/leaderboard`     | `.cto/config.yaml`                                | layer/tier/signal names and severities                            | harvested; re-shaped to add a per-layer `blocking:` flag and a declarative `checks:` roster |
| `kushin77/leaderboard`     | `.cto/schema.yaml`                               | JSON Schema (draft 2020-12) shape for the overlay config          | harvested; the upstream `pattern` values had lost their escapes (`^d+.d+.d+`), so they matched almost anything — repaired here and covered by a test |
| `kushin77/leaderboard`     | `.cto/orchestrator.yaml`                          | master workflow as the single entry point                         | pattern only: it dispatches GitHub Actions, which this repository forbids; the CLI is the invocation path here |
| `kushin77/leaderboard`     | `.cto/{executive,engineering,devops,support}/run.sh` | layer entry points referenced by the orchestrator              | upstream directories carry only a README — the referenced `run.sh` files do not exist, so the documented path was already broken; the four layer gates are completed here as real, tested checks |
| `kushin77/leaderboard`     | `scripts/cto/apply-overlay.sh`                    | bootstrap: copy artifacts, adapt repository identity, validate    | harvested; re-implemented as `overlay.py apply` |
| `kushin77/leaderboard`     | `scripts/cto/config-engine.sh`                    | severity resolution `tiers.<tier>.<layer>` → `layers.<layer>.severity` → advisory | harvested; re-implemented in Python, plus the standard-tier consistency rule |
| `kushin77/leaderboard`     | `scripts/cto/cto-orchestrator.sh` at `7a67d0b4` (closes `#1721`) | the `_verdict` tally, INDET as a first-class state, and the `self-test` negative control | harvested — this is the fix that made the overlay able to fail |
| `kushin77/leaderboard`     | `docs/DS_CTO_AUTONOMY_PROGRAM.md`                 | tiered autonomy, "trust comes from mechanical checks, never from the LLM tier" | principle only; no code path taken |
| `kushin77/agent-orchestrator` | `scripts/check-secrets.sh`                     | the eight high-signal secret shapes and the placeholder-exemption idea | harvested so there is one secret taxonomy, not two; the overlay's scan is scoped to tracked files |
| `kushin77/agent-orchestrator` | `scripts/pytest-suites.txt`                    | declared-suite manifest (authoritative per `docs/QA-GATE.md`)     | used as the subject of the `coverage` check |

Local harvest sources, read at `2026-09-13`: `/home/akushnir/leaderboard`
(`HEAD` `f7bc4715`) and this checkout. The cannibalization index row for this
issue is recorded by the orchestrator in `CANNIBALIZATION.md`, which this issue
does not edit (single-writer convention).

## Verification

- `python3 -m pytest governance/cto-overlay/tests -q` — behavioural suite:
  blocking-fails, warning-passes, malformed config, unknown check, tally
  counting, signal coverage, CLI contract, drop-in apply.
- `bash scripts/check-cto-overlay.sh` — the gate: artifacts, schema validation,
  a standard-tier run on this checkout, three independent probes in a scratch
  checkout (clean → 0, planted defect → non-zero naming the file, dropped
  signal → 2) and the engine's built-in negative control.
- `python3 governance/cto-overlay/overlay.py self-test` — six controls,
  including the config-contradiction and schema-keyword cases.
