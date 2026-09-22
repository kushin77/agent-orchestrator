# backend.tf
#
# ---knowledge---
# module_id: infra.terraform.backend
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
terraform {
  backend "gcs" {
    bucket = "agent-orchestrator-tfstate"
    prefix = "control-plane"
  }
}
