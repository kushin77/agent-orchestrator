# `infra/terraform/modules/web-surface/` — the public console surface (issue #881, lane L2 of EPIC #878)

This module declares the public web surface (`ai.purebliss.app`): the Cloud Run
v2 service that serves the portal, its dedicated runtime identity, and the
console's auth-gate environment — projected from Secret Manager, never baked
into the image or restated as a literal (GR-6).

Everything here is gated on `var.enabled` (default `false`). With the flag
closed, every resource is `count = 0`: `terraform plan` shows no diff, and
`terraform apply` creates nothing. Promotion is a reviewed flip of that one
variable.

## One declaration, not two lists

The auth-gate environment — which env names the console reads, which Secret
Manager id backs each, and how each is delivered — is declared **once**, in
[`auth-env.json`](./auth-env.json), which ships **inside this module
directory** on purpose: `main.tf` reads it with
`file("${path.module}/auth-env.json")`, the one path expression that keeps
resolving wherever the root module is invoked. This module **projects** that
declaration — it never restates an env name, a secret id or a mount path as a
Terraform literal:

```hcl
auth_env         = jsondecode(file("${path.module}/auth-env.json"))
auth_env_secrets = { for secret in local.auth_env.secrets : secret.env => secret }
```

The same declaration is the manifest [`infra/portal/auth_env.py`](../../../portal/auth_env.py)
validates against the console's own source (`portal/server/sso.py`) — so the
python side (what the console reads) and the Terraform side (what the deploy
supplies) cannot drift apart: there is exactly one file that can be wrong, and
`scripts/check-portal-auth-env.sh` / `infra/portal/tests/test_auth_env.py`
refuse it, by name, the moment it does.

| Field | Meaning |
|---|---|
| `env` | The environment variable name the console reads. |
| `code_constant` | The name of the constant in `portal/server/sso.py` that must equal `env` — a renamed constant with a stale declaration is refused as `constant-drift`. |
| `delivery` | `file` (mounted, for a `*_FILE` variable the console `open()`s) or `value` (injected via `value_source.secret_key_ref`). |
| `secret_id` | The Secret Manager secret this module grants `roles/secretmanager.secretAccessor` on and reads from — an id only, never a value. |
| `version` | Always `"latest"` here — a pinned version number is refused as `pinned-secret-version` (it would turn a key rollover into a redeploy). |
| `volume` / `mount_dir` / `filename` | For a `file` delivery: the Cloud Run volume, its mount path (must be absolute and outside a reserved tree), and the bare filename the console opens. |

This module owns no `google_secret_manager_secret_version` resource: the
secret **container** and its versions are published out-of-band by
`infra/portal/mirror-auth-gate-jwks.sh` (`gcloud secrets versions add ...
--data-file=-`, payload on stdin, never on argv or in Terraform state). Owning
a secret *version* in Terraform is refused tree-wide as `secret-material` —
the value would then live in this repository's plan/state — so this module
only ever references ids.

## What promotion wires

With `enabled = true`:

- a dedicated runtime service account (`<name>-runtime`), whose only grant is
  `roles/secretmanager.secretAccessor` on the two declared secrets — no other
  role, anywhere;
- the Cloud Run v2 service's container gets one `env` block per declared
  secret, `file`-delivered entries pointed at their mount path and
  `value`-delivered entries injected via `env.value_source.secret_key_ref`;
- one `volumes` + `volume_mounts` pair per `file`-delivered secret, pinned to
  `version = "latest"`.

## Verify

```bash
terraform -chdir=infra/terraform/modules/web-surface init -backend=false
terraform -chdir=infra/terraform/modules/web-surface validate
terraform -chdir=infra/terraform/modules/web-surface fmt -check
bash scripts/check-portal-auth-env.sh
python3 -m pytest infra/portal/tests/test_auth_env.py -q
```

`terraform plan` needs cloud credentials this environment does not have; with
`enabled` at its committed default (`false`) every resource here is
`count = 0`, so a plan run with credentials shows no diff.

## Related

- `infra/portal/README.md` — the deploy-side contract in full (why the
  console fails closed, what each file under `infra/portal/` is).
- `docs/EDGE-CUTOVER.md` — why `create_gcp_edge_route` defaults `false`
  (unrelated to the auth-gate environment, but gates this same module).
