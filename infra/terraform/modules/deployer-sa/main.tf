# Flag-gated deployer service account (IaC only — no console path).

# ---knowledge---
# module_id: infra.terraform.modules.deployer-sa.main
# system: infra
# app: terraform
# solution_class: class
# patterns: [pre-standard-snapshot]
# derives_from: null
# owner_sme: iac-sme
# tier: L1
# interfaces: [google_service_account, google_project_iam_member]
# invariants: ""
# gotchas: ""
# related: ["#1911"]
# do_not_duplicate: null
# ---knowledge---
locals {
  create = var.enabled ? 1 : 0
}

resource "google_service_account" "deployer" {
  count        = local.create
  account_id   = var.account_id
  display_name = coalesce(var.display_name, "agent-orchestrator control-plane deployer")
  project      = var.project_id
}

# Roles are granted only for the set requested at promotion (empty by default),
# so the account is created with no permissions until a reviewed go-live.
resource "google_project_iam_member" "deployer_roles" {
  for_each = var.enabled ? toset(var.roles) : toset([])

  project = var.project_id
  role    = each.key
  member  = "serviceAccount:${google_service_account.deployer[0].email}"
}
