# Terraform + provider pins for the self-hosted paperclip runtime module.
#
# Pinned to the same versions as the root control-plane environment so the
# offline provider cache that scripts/check-terraform.sh uses resolves this
# module too (the root environment references it — see infra/terraform/main.tf).
# ---knowledge---
# module_id: infra.paperclip.terraform.versions
# system: infra
# app: paperclip
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
