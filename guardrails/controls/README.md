# guardrails/controls — server-side guardrail control surface (issue #343)

The **server-side half** of the console Controls view. The shell owns no policy
state: it calls this surface, and this surface decides and records. A toggle
here is a real policy flip that a second reader can observe — not a
presentation-only switch.

This lane *consumes* two in-repo vocabularies and never redefines them:

- [`../policy/controls.py`](../policy/controls.py) — the authoritative
  default-OFF controls registry that parses and schema-validates
  [`../policy/controls.yaml`](../policy/controls.yaml) (issue #26).
- [`../../portal/server/controls.py`](../../portal/server/controls.py) — the
  server-side `CONTROL_POLICY_MAP` the console toggles are bound to (issue #39).
  This lane reuses `build_control_policy_map` and cross-checks that every
  guardrail control is present in it.

## Model

`model.py` defines the `PolicyControl` record and the closed status vocabulary:

| Symbol | Value | Meaning |
|--------|-------|---------|
| `STATUS_PASSED` | `246` | the guardrail passed (control OFF) |
| `STATUS_BLOCKED` | `446` | the guardrail blocked (control ON) |

The pair is a `frozenset`-closed vocabulary (Portkey guardrail semantics):
`status_name()` refuses any other code, so a control cannot publish a status a
consumer would not understand.

Two invariants are structural, not advisory:

- **Default OFF.** A `PolicyControl` refuses construction with
  `default_enabled=True` (AO-GR-6). A control that ships ON is not a policy
  choice the model will accept.
- **No silent creation.** `ControlSet.toggle()` refuses an unknown control id
  and writes **exactly one** append-only audit record per flip.

## Files

- `model.py` — `PolicyControl`, `ControlState`, `ControlSet` (state + single
  `toggle()`), the 246/446 vocabulary, and `assert_refuses_default_on()`.
- `registry.py` — loads the control set from `guardrails/policy/controls.yaml`
  (via `policy.controls`), reuses the portal `CONTROL_POLICY_MAP`, and reads /
  writes the persisted state a second reader observes.
- `audit.py` — append-only audit sinks (`InMemoryControlAuditLog`,
  `JsonlControlAuditLog`); one record per toggle, no update/delete surface.
- `cli.py` — `list`, `get`, `toggle`, `check-report`, `self-test`.

## CLI

```bash
python3 guardrails/controls/cli.py list
python3 guardrails/controls/cli.py get model-call-budget
python3 guardrails/controls/cli.py toggle model-call-budget --on --actor ops
python3 guardrails/controls/cli.py check-report
python3 guardrails/controls/cli.py self-test
```

`check-report` prints `246 PASSED` / `446 BLOCKED` per control and exits `0`
when every control is default-OFF, `1` when any control is enabled, and `2` when
the report cannot be built. `self-test` runs every invariant above, including
the self-mutating negative control.

## Gate

[`../../scripts/check-guardrail-controls.sh`](../../scripts/check-guardrail-controls.sh)
is wired into `make verify` (after `gateway-catalog-parity`). It exits
tri-state: `0 OK / 1 NOT-OK / 2 CANNOT-ASSESS`. It asserts every control
defaults OFF, an unknown control is refused without an audit record, a toggle
writes exactly one audit record and flips state a second reader sees
(`246 → 446`), the 246/446 vocabulary is closed, and a control that ships ON is
refused — if that mutant is ever accepted, the gate names it and exits non-zero.
