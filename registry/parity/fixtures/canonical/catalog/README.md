# Canonical CMR catalog (parity fixture)

Hermetic stand-in for `vendor/CMR/catalog/`. The parity gate requires the
canonical catalog directory to exist next to the canonical role schema, so this
directory (and its README) is what `registry/parity/tests` points at when the
real `vendor/CMR` submodule is absent.

It carries no vocabulary of its own: the role, model-tier, worker-model and
lane vocabularies the gate compares live in
`../onboarding/agent-profiles/role.schema.json`.
