# governance/onboarding — per-consumer onboarding instances

Owns onboarding-flow instances: a shared template/schema/vocabulary rendered
into a concrete, per-consumer instance. Each subdirectory is one consumer's
onboarding instance; see [`shared-frontend/README.md`](shared-frontend/README.md)
for the one delivered so far (template, schema, seed provenance, render path,
and its own gate).

## Layout

| Path | Role |
|---|---|
| [`shared-frontend/`](shared-frontend/) | the shared-frontend consumer's onboarding instance (template, schema, seed data, renderer, tests) |

Adding a new consumer's onboarding instance is a new sibling directory here,
following the same shape (`template.yaml` / `schema.yaml` / `instance.yaml` /
`vocabulary.yaml` / `render.py` / `tests/`) — see `shared-frontend/` as the
worked example.
