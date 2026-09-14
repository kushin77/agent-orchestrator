---
id: board-sync-plugin
kind: plugin
name: Board sync plugin
description: Expose the fleet board claim loop as a loadable plugin that the orchestrator profile already powers.
provenance:
  repo: kushin77/leaderboard
  path: docker/worker-fleet/personas.yaml
  license: Proprietary (kushin77, All Rights Reserved)
  verdict: PATTERN
requires:
  capabilities:
    - task-claim
  tools:
    - board_sync
    - shell_exec
---

# Board sync plugin

A **harvested pattern** recorded with its origin and terms (GR-10). Upstream's
persona file describes a claim loop that never idles; this plugin declares that
shape against the fleet's own tool vocabulary — it copies no upstream code.

## What it declares

- Origin: `kushin77/leaderboard`, `docker/worker-fleet/personas.yaml`. Verdict
  PATTERN: the shape is worth re-implementing here; the bash machinery is not.
- The capability it needs: `task-claim`.
- The internal tools it wires: `board_sync` and `shell_exec`. Both must already
  be in the loading profile's `toolAllowlist`.

## What it refuses

A profile that does not grant `task-claim`, `board_sync` or `shell_exec` is
refused at load, naming the missing grant. A plugin can only use tooling the
profile already powers.
