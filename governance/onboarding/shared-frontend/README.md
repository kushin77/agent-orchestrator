# governance/onboarding/shared-frontend — the mandatory onboarding lane

> Owner lane: `governance/onboarding/shared-frontend/**` (issue
> `kushin77/agent-orchestrator#703`). Authority:
> `vendor/CMR/catalog/mandatory.tsv` row 3 (`shared-frontend`, GR-18 /
> CMR:ONBOARD-0003). Pattern: `vendor/CMR/docs/MANDATORY-MODULES.md`
> §"Two-part surface: `shared-frontend`", part 1.

## What this is (30 seconds)

`shared-frontend` is the hub's **third mandatory module**: it ships to every repo
CMR governs, and its declared consumer assets are the shared `--os-`
design-token twin (`tokens.json`) plus a `gdc-manifest.yaml`
`shared-frontend.tokens` module pin. This lane renders BOTH assets for this repo
from ONE parameterized template and ONE instance file — the onboarding is a
reviewed render, never a copy-paste:

| Root asset | Rendered from | Rule |
| --- | --- | --- |
| [`tokens.json`](../../../tokens.json) | `seeds/tokens.json` | `verbatim` — the twin is tenant-invariant, so the render is a byte-identity assertion against the pinned digest |
| [`gdc-manifest.yaml`](../../../gdc-manifest.yaml) | `template.yaml` `assets[].document` | `interpolate` — `repo:` plus the `x-onboarding` org/tenant/domain binding |

## Layout

```text
governance/onboarding/shared-frontend/
├── README.md            # this file
├── schema.yaml          # JSON-Schema (draft-07) bundle: instance + vocabulary + template
├── vocabulary.yaml      # the DECLARED org / tenant / domain set (closed; unknown is refused)
├── instance.yaml        # the ONE hand-edited file: org, tenant, domain, repo
├── template.yaml        # the PARAMETERIZED template (never hand-edited per repo)
├── render.py            # renderer + gate (stdlib + PyYAML only)
├── seeds/
│   ├── tokens.json      # the byte-twin of the pinned upstream token set
│   └── PROVENANCE.md    # GR-10 provenance, licence, ledger row
└── tests/               # renderer + gate behaviour (pytest, offline)
```

## Commands

```bash
python3 governance/onboarding/shared-frontend/render.py check            # the gate: 0 / 1 / 2
python3 governance/onboarding/shared-frontend/render.py check --json     # machine-readable
python3 governance/onboarding/shared-frontend/render.py render --out DIR # write the assets
bash scripts/check-shared-frontend-onboarding.sh                         # the gate + its self-proof
python3 -m pytest governance/onboarding/shared-frontend/tests -q
```

## Exit-code contract

`0` OK · `1` NOT-OK (a real defect, named on stderr) · `2` CANNOT-ASSESS (the
contract itself could not be read — a lane input is missing or does not match
`schema.yaml`; the seed is missing; PyYAML is unavailable). A CANNOT-ASSESS never
reads as a pass.

Two refusal families, deliberately different: an **unknown** org/tenant/domain
(and a missing key) is a real defect and lands in NOT-OK… except a missing key
in `instance.yaml`, which violates `schema.yaml` and so is CANNOT-ASSESS — both
name the offending field. What is never done is guessing: an org, tenant or
domain that [`vocabulary.yaml`](vocabulary.yaml) does not declare is refused by
name, with the declared set printed beside it.

## Adding a tenant (the point of the template)

1. Add the tenant (and, if it has none yet, an empty `domains: []`) to
   [`vocabulary.yaml`](vocabulary.yaml), with a `domains_source` naming where the
   host is evidenced — a tenant with no evidenced host is a declaration of a gap,
   not a hole.
2. Point [`instance.yaml`](instance.yaml) at it.
3. Run `render --out .` and commit the two rendered assets plus the instance.
4. `bash scripts/check-shared-frontend-onboarding.sh` — green, or it names the
   field that drifted.

## Self-proving

`bash scripts/check-shared-frontend-onboarding.sh` stages scratch copies of the
tree and REQUIRES each refusal to fire: a deleted `tokens.json`, a mutated one, a
dropped mandatory pin, an unknown tenant, and an unparseable lane input (which
must land in CANNOT-ASSESS). A check that cannot fail is rejected (GR-12 /
AO-GR-19), so the three-state contract is exercised on every real-tree run.

## Local-code-first

The renderer is a pure function of the template bytes, the instance bytes, the
vocabulary bytes and `seeds/`: no network, no clock, no environment, no
randomness. For a fixed (org, tenant, domain, repo) tuple the render is
byte-identical on every run — `tests/test_render.py` proves it, and the gate
enforces that the committed assets are that render.
