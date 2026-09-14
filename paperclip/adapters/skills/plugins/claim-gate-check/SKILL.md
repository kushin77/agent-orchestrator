---
id: claim-gate-check
kind: plugin
name: Claim gate check
description: Run the repo's claim-gate check as a plugin, using only tooling the loading profile already grants.
provenance:
  repo: kushin77/agent-orchestrator
  path: governance/dispatch/README.md
  license: MIT
  verdict: AUTHORED
requires:
  capabilities:
    - test-run
  tools:
    - shell_exec
---

# Claim gate check

A **declaration plus a reference** (GR-10): it names the authority
(`governance/dispatch/`) and the tool it needs, and ships no implementation of
its own.

## What it declares

- The authority: `governance/dispatch/cli.py` and its README (claim before
  working; the ledger refuses an out-of-order claim).
- The capability it needs: `test-run`.
- The internal tool it wires: `shell_exec`, which the loading profile must
  already list in its `toolAllowlist`.

## What it refuses

A profile that does not grant `test-run` or `shell_exec` is refused at load,
naming the missing grant and the profile. The plugin's requirements can only
narrow what the profile already allows.
