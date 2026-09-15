# RCA-0006 — a go-live designed against a retired host

| Field | Value |
|---|---|
| RCA id | `RCA-0006` |
| Incident | `INC-0006` |
| Origin | `#800` |
| Severity | `medium` |
| Owner | the AgentConsole product lane |
| Reviewed | `2026-09-14` |

## Impact

The operator was told the AgentConsole go-live was "one deploy away" via
agent-orchestrator's own GCP Cloud Run apply pipeline (epic #607). That pipeline
is **not** the live host: the fleet's live-hosting contract fixes the remote
**shared-services cluster** as the only host, and the Cloud Run config was
deliberately removed (owner directive 2026-09-04). Design effort and a published
boundary rested on the retired model.

## Detection

A human — the operator — named the missing scavenge: *"GCP project and creds are
documented in shared-services and shared-frontend — why are you not scavenging
everything needed from our modules"*. The documentation existed the whole time;
nothing in the repo catches that a go-live design ignored a peer module's
deployment contract.

## Root cause

The go-live was derived from the repository the work happened in
(agent-orchestrator's `infra/terraform` + `infra/cloudbuild`) while the peer
modules that own the **run** half were treated as an external credential
boundary rather than as documented inputs. The missing control is a **scavenge
step**: a go-live brief must read the peer modules' deployment contracts before
a host is chosen.

## Corrective actions

- `CA-0008` — the hosting contract is now scavenged and declared:
  `docs/AGENTCONSOLE-HOSTING.md` (PR #806, merge `4d9bae8`) plus the run-half
  direction issue `kushin77/shared-services#4192`.

## Lessons

- `SUGGEST-0004` — scavenge a peer module's deployment contract before choosing
  a host.

## Evidence

- `docs/AGENTCONSOLE-HOSTING.md` (PR #806, merge `4d9bae8`).
- `kushin77/shared-services#4192`.
- The scavenge sources: `shared-frontend/docs/DEPLOYMENT.md`,
  `shared-frontend/contrib/shared-services/`, and
  `shared-services/infra/modules/cloudflare-tunnel-ingress/`.
