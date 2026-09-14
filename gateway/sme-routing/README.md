# SME-squad routing + capability / route / tier FinOps surface

Declared, schema-validated **routing policy** for the control plane: which SME
handles which domain, which fleet carries which role, and how complexity and
risk route a task to an agent chain and a model tier — with per-tier cost
controls and an escalation ladder that terminates at a human/advisor.

Issue #149 (child of EPIC #144). Full doctrine, provenance ledger and blast
radius: `docs/SME-ROUTING.md`.

## Layout

| Path                   | What it is                                              |
|------------------------|---------------------------------------------------------|
| `schema.yaml`          | JSON Schema (draft-07) for the three declared policies   |
| `policies/`            | the declared configuration (ported, provenance-stamped)  |
| `jsonschema_lite.py`   | stdlib-only JSON-Schema subset validator                 |
| `smeroute_config.py`   | loader: schema validation + the semantic invariants      |
| `router.py`            | the engine: `route`, `dispatch`, classification          |
| `cli.py`               | offline CLI, tri-state exit codes (0 / 1 / 2)            |
| `tests/`               | behavioural tests + mutation controls                    |

## Run it

```bash
python3 gateway/sme-routing/cli.py validate
python3 gateway/sme-routing/cli.py demo
python3 gateway/sme-routing/cli.py route --type doc_update --text "README wording"
python3 gateway/sme-routing/cli.py dispatch --text "refactor the loader" --tokens 900
python3 gateway/sme-routing/cli.py sme --text "rotate the terraform state bucket"
python3 -m pytest gateway/sme-routing/tests -q
bash scripts/check-sme-routing.sh
```

## The three lines that matter

1. **Unknown work takes the deep path.** A task type the policy does not declare
   (or a task with no positive signal) routes to the deep chain, never the cheap
   one. `dispatch_defaults.unknown_task_type` must stay `deep`; the loader
   refuses the policy if it does not.
2. **A cap breach is explicit.** Exceeding a tier's timeout or token cap is an
   outcome, never a silent pass: `refused` when escalation is not authorised,
   otherwise the ladder climbs the declared fallbacks.
3. **Escalation ends at a human.** When the top tier cannot take the request
   there is no higher tier, and the router returns the distinct `human_advisor`
   outcome — a bounded, declared terminal, not an exception and not a loop.

The hyphenated directory cannot be an importable package, so — like
`control-plane/instructions` — the entry points arrange `sys.path` themselves
and the suite's `conftest.py` does the same for the tests.
