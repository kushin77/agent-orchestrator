# fleet-cron module

Declares the agent-orchestrator fleet-cron container **pair** — 2 replicas,
active-active — on the two nodes of the shared-services on-prem HA cluster
(`192.168.168.31` / `192.168.168.42`), per issue #900 (parent #706, lane L5
#884).

Flag-gated OFF by default (`enabled = false`, GR-5). With the flag closed
this module declares but creates nothing — `terraform plan` shows zero
resources.

## Why `null_resource` + `remote-exec`, not `kreuzwerker/docker`

The peer repo (`shared-services`) already has real Terraform for the sibling
container on these same two nodes: `docker/cronrunner`, managed by
`shared-services/infra/modules/host-platform/main.tf`, resource
`null_resource.cronrunner` (~L6229). It provisions over `remote-exec`/`file`
with an `ssh` `connection` block — not the `kreuzwerker/docker` provider.
This module matches that pattern (GR-10 provenance) so the two containers on
the same cluster are managed the same way.

Two deliberate divergences from the peer pattern:

- **No `docker build` on the node.** The peer builds the cronrunner image
  in-place from an uploaded source tree. This module instead takes a
  promoted image reference (`var.image`) built by the image pipeline
  (#709/#710) — the deploy target only pulls and runs it.
- **No secret value on the command line.** The peer's `cronrunner` resource
  interpolates `var.keydb_password` directly into the `docker run -e
  KEYDB_PASSWORD=...` line, which bakes the secret into both Terraform state
  and `docker inspect`'s recorded command. EPIC #706 explicitly rules this
  class of leak out ("never `env_file` — bakes tokens into `docker
  inspect`"). This module instead takes `keydb_password_secret_ref`: a path
  to a node-side env file (provisioned out-of-band, e.g. by a secrets agent),
  passed to the container via `docker run --env-file <path>`. No secret
  value is ever known to Terraform.

## Locking

Active-active arbitration uses the same KeyDB `scheduler:lock:<job-id>`
SETNX scheme documented in
`shared-services/docker/cronrunner/README.md` — both replicas are safe to
run concurrently because only the lock holder for a given job id executes
it.

## Health

Each replica is health-checked on `GET /health` (peer contract) via Docker's
built-in `--health-cmd`; `/metrics` is exposed on the same port per the peer
contract but is not separately probed here.

## Variables

See `variables.tf`. Notably: `enabled` (default `false`), `image` (no
default — required at promotion), `nodes` (defaults to the `.31`/`.42`
pair), `ssh_private_key_path` (a path, never key material),
`keydb_password_secret_ref` (a secret reference, never a value).
