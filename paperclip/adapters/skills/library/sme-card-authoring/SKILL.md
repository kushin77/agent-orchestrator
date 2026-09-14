---
id: sme-card-authoring
kind: skill
name: SME card authoring (harvested pattern)
description: Author an SME persona card in the registry discipline, mirroring the CMR agent-profile card shape.
provenance:
  repo: kushin77/CMR
  path: onboarding/agent-profiles/role.schema.json
  license: internal (no LICENSE file in the checkout)
  verdict: REFERENCE
requires:
  capabilities:
    - docs-authoring
---

# SME card authoring

A **harvested pattern**, recorded with its origin and terms (GR-10) and
re-implemented here as a declaration — no upstream file is vendored and no
upstream code becomes a dependency.

## What it declares

- Origin: `kushin77/CMR`, `onboarding/agent-profiles/role.schema.json`. The
  verdict is REFERENCE: the shape informed doctrine, and the canonical vocabulary
  is frozen in this repo at `registry/parity/canonical/`.
- The registry discipline it mirrors: one declaration, one owner, one gate
  (`registry/profiles/` seeds, `registry/personas/` cards).
- The capability an author must already hold: `docs-authoring`.

## What it refuses

A profile that does not grant `docs-authoring` cannot load it. The skill narrows
what the profile already allows; it never adds to it.
