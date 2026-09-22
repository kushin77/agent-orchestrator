# governance/controls — spine → control coverage map (issue #890)

Full rule-spine coverage map: every rule id in `AGENTS.md` / `docs/GOLDEN-RULES.md`
(Parts A/B/C, AO-GR-1..27) plus the hub (`kushin77/CMR`) rules this repo cites by
number (GR-2..GR-10, GR-12), mapped to the control and gate that enforces it.
`scripts/control-coverage.tsv` (#874) only ever mapped Part B — this closes the
rest of the spine, so a reviewer can walk from any rule id to its enforcing gate.

## Layout

| File | Role |
|---|---|
| [`spine-coverage.yaml`](spine-coverage.yaml) | the full rule → control → gate map, one row per rule id |
| [`check_spine_coverage.py`](check_spine_coverage.py) | validates the map against the repository (never against prose) |
| [`tests/`](tests/) | fixtures and self-test coverage for the checker |

## What the checker validates

* every rule id parsed out of `AGENTS.md` and `docs/GOLDEN-RULES.md` has exactly
  one row under `rules:` — no missing rule, no stale row for a retired rule id;
* every `gate` entry that isn't `-` or `suite:...` names a file that exists and
  is executable;
* a `suite:<dir>` gate is declared in `scripts/pytest-suites.txt`, the manifest
  `scripts/run-pytest-suites.sh` actually runs;
* no row claims `covered: true` while its gate is `-` or missing — the exact
  false-green shape this checker exists to refuse;
* a `GAP` status is reported honestly, never silently folded into OK.

## Usage

```bash
python3 governance/controls/check_spine_coverage.py
python3 governance/controls/check_spine_coverage.py --self-test
```

`--self-test` runs the real map (must pass), then a mutated copy with a
falsified `covered: true` row, which must be refused (GR-12: a check that
cannot fail is a formality).

## Related

Issue #890, lane L11 of EPIC #878. `spine-coverage.yaml` also folds in issue
#803 (branch protection, required checks, CODEOWNERS as delivery controls) via
rows tagged `delivery: true`.
