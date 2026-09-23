---
description: "Use when: editing or reviewing anything under infra/terraform/ (Terraform HCL, variables, master flag gates, backends, provider config), declaring new infrastructure, adding a feature flag, or running terraform init/validate/plan. Covers the IaC mandate, flag-gated-OFF default, and the flag-gate/apply gates."
applyTo: "infra/terraform/**"
---

# Terraform / infra — CMR

> Copilot-discoverable rendering. Canonical sources: `GOLDEN-RULES.md` GR-5
> (everything declared in IaC; no console clicks — enforced by `make tf-flag-gate`,
> `scripts/tf-flag-gate.sh`, in the default `make verify` chain) and
> `docs/FEATURE-FLAGS.md` (flag declaration + precedence, ADR-0003/ADR-0004). The
> always-on posture is in `.github/copilot-instructions.md` → "Hard floor — never".

## The IaC mandate

1. **Everything is declared in IaC.** Repo governance is Terraform (GitHub provider);
   infra changes are **PR → `terraform plan` → code-native apply**. Never click a console,
   never hand-edit live state, never author branch-protection/ruleset changes via ad-hoc
   `gh api` (#372).
2. **New infra ships flag-gated OFF.** Every master flag-gate variable must default to
   `false`; `scripts/tf-flag-gate.sh` rejects any that does not. New machinery is
   declared, testable, and inert until the code-native runner flips it.
3. **Never ad-hoc `terraform apply`.** The sanctioned path is
   `ops/run.sh gate_apply --confirm` (`controller/gate-apply.sh`); `--confirm` remains the
   required authorization signal and every run (plan or apply) is logged to
   `controller/apply-ledger.tsv`. See `docs/DEFAULTS.md` (Terraform row).
4. **Drift is measured, not assumed.** `make tf-drift` (`scripts/tf-drift-check.sh`) runs a
   read-only plan against live state and honestly SKIPs when no token/backend is
   configured — a SKIP is not a pass.

## Feature flags (`docs/FEATURE-FLAGS.md`)

- A module declares toggles in `module.json` (`catalog/schemas/module.schema.json`):
  `features[]` with `id`, `default: "on"|"off"`, `flags[]`; `integrations[]` are **always
  opt-in** and carry no `default`.
- Flag keys are `<feature-id>.enabled`, declared in `flags[]` — the array is the single
  source of truth; consumers never invent keys.
- Resolution precedence (lowest → highest): manifest default → config file → environment
  variable. Env always wins. v1 is env/config only; there is no external flag service.
- The same key, default, and precedence across every language (env key is the UPPER_SNAKE
  form, e.g. `rbac.enabled` → `RBAC_ENABLED`).

## Environment mechanics

`infra/terraform/**` contains a root-owned `.terraform` that uid 1000 cannot write, so
init/validate must use an isolated data dir **passed per command** —
`env TF_DATA_DIR="$(mktemp -d)" terraform init` — and never `export`ed, because an
exported value leaks into the shared shell and turns a later `make verify` red on a
non-existent bug. Offline plugin cache:
`TF_PLUGIN_CACHE_DIR=/home/akushnir/.terraform.d/plugin-cache`.

## Verification before claiming done

```bash
make tf-flag-gate        # every master flag-gate defaults to false
make tf-drift            # read-only plan (honest SKIP without token/backend)
make verify              # gate of record
```
