# versions.tf
#
# ---knowledge---
# module_id: infra.terraform.modules.deployer-sa.versions
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
  required_version = ">= 1.6"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }
}
