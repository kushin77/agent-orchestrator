# Declarative import for resources that already exist in GCP but were never
# recorded in the real GCS-backed state — because infra/terraform/backend.tf
# did not exist as a committed file until this same change (issue #411/#1136).
# Every prior `apply.yaml` run used Terraform's default LOCAL backend inside
# the ephemeral Cloud Build container, so state was discarded when each build
# ended; Terraform had no memory of anything between runs, and every apply
# tried to re-create resources that were already real, failing with
# "already exists" (google_artifact_registry_repository.ao_images) or
# leaving newly-created-for-real resources permanently untracked
# (module.deployer.google_service_account.deployer[0], created by the apply
# that finally reached that step — build a0aec8c4).
#
# An `import` block (Terraform >= 1.5) is the declarative, code-native way to
# reconcile this: committed, versioned, and applied automatically by the next
# `terraform apply` — never an ad-hoc `terraform import` command run by hand.
# Both blocks are safe to leave in the tree permanently; importing an already-
# imported resource is a no-op.

# ---knowledge---
# module_id: infra.terraform.import
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
import {
  to = google_artifact_registry_repository.ao_images
  id = "projects/purebliss-ghl/locations/us-central1/repositories/ao-images"
}

import {
  to = module.deployer.google_service_account.deployer[0]
  id = "projects/purebliss-ghl/serviceAccounts/control-plane-deployer@purebliss-ghl.iam.gserviceaccount.com"
}
