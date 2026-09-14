# control-plane/fleet-template — per-repo agent fleet template

> Owner lane: `control-plane/fleet-template/**` (issue `kushin77/agent-orchestrator#146`).
> Parent: EPIC #144. Contract in [`../../docs/FLEET-TEMPLATE.md`](../../docs/FLEET-TEMPLATE.md);
> doctrine in [`../../AGENTS.md`](../../AGENTS.md).

## What this is (30 seconds)

One parameterized template renders a **complete per-repo fleet** — five roles,
one SME per declared domain, lens routing, FinOps ceilings and a run-state block
— from that repository's own params file. Nothing is hand-edited per instance:
if a committed instance and a fresh render of its params disagree, that is drift
and the gate fails.

## Layout

```text
control-plane/fleet-template/
├── README.md                  # this file
├── schema.yaml                # JSON-Schema bundle: $defs + template/params/observations/instance
├── template.yaml              # the PARAMETERIZED template (never hand-edited per instance)
├── render.py                  # renderer + schema validation + drift/isolation gate (stdlib + PyYAML)
├── pilots/                    # two pilots, same template, different params
│   ├── agent-orchestrator.params.yaml        # pilot A inputs
│   ├── agent-orchestrator.observations.yaml  # pilot A recorded run state
│   ├── agent-orchestrator.fleet.yaml         # pilot A RENDERED instance (generated)
│   ├── shared-frontend.params.yaml           # pilot B inputs
│   ├── shared-frontend.observations.yaml     # pilot B recorded run state
│   └── shared-frontend.fleet.yaml            # pilot B RENDERED instance (generated)
└── tests/                     # behavioural suite (rendering, parameterization,
                               # isolation, schema failures, drift, run state,
                               # CLI exit codes, negative controls, real-JSON-Schema cross-check)
```

## Commands

```bash
python3 control-plane/fleet-template/render.py check                 # the gate: 0 / 1 / 2
python3 control-plane/fleet-template/render.py check --json
python3 control-plane/fleet-template/render.py report --params pilots/<repo>.params.yaml
python3 control-plane/fleet-template/render.py render --params pilots/<repo>.params.yaml --out pilots/<repo>.fleet.yaml
python3 control-plane/fleet-template/render.py validate-instance pilots/<repo>.fleet.yaml
bash scripts/check-fleet-template.sh
python3 -m pytest control-plane/fleet-template/tests -q
```

## Exit-code contract

`0` OK · `1` NOT-OK (drift, invariant violation, missing/mislabelled pilot,
shared namespace) · `2` CANNOT-ASSESS (missing/unparseable input, or a document
that violates `schema.yaml`). A schema violation is never reported as `0`.

## Local-code-first

Debug against this checkout. The renderer is a pure function of the template
bytes, the params bytes and the observations; it opens no network socket and
starts no process. If the gate is red, `render.py check` prints the offending
JSON path.
