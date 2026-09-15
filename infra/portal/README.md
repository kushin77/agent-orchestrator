# `infra/portal/` — the console's auth-gate environment (issue #730)

The console has **no login of its own**: it establishes a session only from a
verified shared-frontend `os-session-token`, checked offline against a mirror of
the auth gate's published JWKS, and it decides `super_admin` from a local
allowlist. Both are **config**, and with neither present it refuses every session
(`portal/server/sso.py` raises `no auth-gate JWKS is configured; refusing every
session`) — so a deploy that never supplies them serves nothing, however green
its healthcheck is.

This directory is the **deploy side** of that contract. It holds no secret: it
names the secrets, and the deploy reads them from Secret Manager (GR-6).

| File | What it is |
|---|---|
| `auth-env.json` | The declaration: the env names, the secret ids, the delivery shape and the mount path — the single source of truth both the deploy and the gate read. |
| `auth_env.py` | The validator. Reads the declaration, the Terraform module and the console's own source, and refuses every way they can drift apart (`check`), validates a key set with the console's own predicate (`check-jwks`), scans one file (`scan-file`), and prints the wiring (`describe`). |
| `mirror-auth-gate-jwks.sh` | The mirror job: fetch the auth gate's published key set, refuse it if the console could not use it, and publish it into Secret Manager **on stdin**. |

`infra/terraform/modules/web-surface` **projects** the declaration into the Cloud
Run service: the mirror arrives as a **mounted file** (the console opens that
variable as a path), the allowlist as an injected **secret version**, read by a
dedicated runtime identity that holds no other role.

## Why one declaration, and not literals in the Terraform

The env names exist in one non-code place. The module reads
`file("${path.module}/auth-env.json")` and never restates a name — the gate
refuses a restatement as `env-name-restated`, so the deploy and the console
cannot drift apart in a way that only a live deploy would reveal. The names
themselves are checked against `portal/server/sso.py`, and every variable that
module reads must be declared **or exempted with a reason**, so a new variable
cannot ship unsupplied.

## Go-live order (the container and its contents are not the pipeline's to invent)

Secrets first, infrastructure second. Nothing here writes a secret value into the
repository, and the apply pipeline never invents one.

```bash
PROJECT=<your-project>

# 1. the containers (once). Terraform does NOT own these: a secret whose
#    lifecycle is in state is a secret a `terraform destroy` can delete.
gcloud secrets create portal-auth-gate-jwks --project "$PROJECT" --replication-policy=automatic
gcloud secrets create portal-root-admin-emails --project "$PROJECT" --replication-policy=automatic

# 2. the mirror — fetched from the gate and validated with the console's own
#    predicate, then published on stdin. Dry run by default; `--apply` writes.
bash infra/portal/mirror-auth-gate-jwks.sh --gate-url https://<auth-gate-origin> --project "$PROJECT"
bash infra/portal/mirror-auth-gate-jwks.sh --gate-url https://<auth-gate-origin> --project "$PROJECT" --apply

# 3. the allowlist — the super-admin emails, one per comma, no trailing newline.
printf '%s' 'root@<your-tenant-domain>' |
  gcloud secrets versions add portal-root-admin-emails --project "$PROJECT" --data-file=-

# 4. then the surface, through the only apply route there is (PR -> plan ->
#    code-native apply, flag-gated): flip `enable_web` and let
#    infra/cloudbuild/apply.yaml apply it as the deployer service account.
```

Rotation is the same two commands: publish a new version of the mirror secret and
the next revision mounts it, because both references are `latest`. A pinned
version number is refused by the gate — a key rollover must not need a redeploy.

## What the gate enforces

```bash
bash scripts/check-portal-auth-env.sh   # in `make verify`, discovered by name
```

It runs the validator over the real tree, then **provokes** every refusal against
a mutated copy whose unmodified twin the same invocation accepts: an env the
console does not read, a `*_FILE` variable delivered as a value, a pinned
version, a mount in a path the runtime owns, an undeclared console variable, a
name restated in the module, a module with no secret version / volume / accessor
grant, an allowlist value carried in the tree, a `*_FILE` variable pointed at an
unmounted path, an inline key set, a private key, and Terraform owning a secret
version.

It then drives the mirror job with a real generated key set and a stub `gcloud`,
and asserts that the payload reaches Secret Manager **on stdin and never on
argv** — a `--data-file=<payload>` form is indistinguishable, to a mechanical
secret scan, from a hardcoded credential.

## The one thing this cannot prove

The mount path (`/etc/ao/auth-gate-jwks.json`) is checked for *coherence* — the
env equals the mount directory plus the item's filename, and the same path is
what `docs/OPERATOR-ACCESS.md`, `portal/README.md` and the compose route already
document — but no offline check can prove Cloud Run accepts a mount at
`/etc/ao`. If the platform refuses it, the apply fails loudly at revision
creation, and the fix is a one-line change here (the declaration), not a hunt
through the Terraform.
