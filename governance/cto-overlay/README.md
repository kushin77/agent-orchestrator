# CTO overlay (drop-in governance)

Four-layer governance that travels with the repository: executive,
engineering, devops and support, each declaring its checks and whether it
blocks, plus four non-negotiable signals that always run. Contract, tier
table, provenance: [docs/CTO-OVERLAY.md](../../docs/CTO-OVERLAY.md).

## Files

| File            | Purpose                                                   |
|-----------------|-----------------------------------------------------------|
| `config.yaml`   | This repository's overlay config (layers, tiers, signals)  |
| `schema.yaml`   | JSON Schema (draft 2020-12) the config is validated against |
| `overlay.py`    | The engine: loads, validates, runs, tallies, verdicts      |
| `tests/`        | Behavioural suite for the engine and its signals           |

## Run it

```bash
python3 governance/cto-overlay/overlay.py run --tier standard   # 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS
python3 governance/cto-overlay/overlay.py validate              # schema-validate the config
python3 governance/cto-overlay/overlay.py list-layers --tier critical
python3 governance/cto-overlay/overlay.py self-test             # negative control: prove it can fail
```

## Apply it to another repository

```bash
python3 governance/cto-overlay/overlay.py apply --target /path/to/repo --dry-run
python3 governance/cto-overlay/overlay.py apply --target /path/to/repo
```

`apply` copies this directory's artifacts, rewrites `repo.name` / `repo.owner`
for the target and re-validates the config there. It refuses to apply the
overlay onto its own source checkout.

## Reading a verdict

A `FAIL` count of zero is not the whole story, by design:

* `PASS` — the check ran and the repository satisfies it.
* `FAIL` — the check ran and the repository does not.
* `INDET` — the check could not be run (missing tool, no checkout). Never a
  pass; at blocking severity it decides the exit code.
* `SKIP` — the check is below the selected tier, belongs to a disabled layer,
  or its layer was not selected. Always visible in the report.
