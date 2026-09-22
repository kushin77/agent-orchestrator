# Google provider for the control-plane environment.
#
# No credentials are hard-coded (GR-6): the provider uses Application Default
# Credentials. The only intended apply identity is the flag-gated deployer
# service account (see modules/deployer-sa) driven by the Cloud Build apply
# pipeline — never a console click and never a personal token.
# ---knowledge---
# module_id: infra.terraform.providers
# system: infra
# app: terraform
# solution_class: class
# patterns: [pre-standard-snapshot]
# derives_from: null
# owner_sme: iac-sme
# tier: L1
# interfaces: []
# invariants: ""
# gotchas: ""
# related: ["#1911"]
# do_not_duplicate: null
# ---knowledge---
provider "google" {
  project = var.project_id
  region  = var.region
}
