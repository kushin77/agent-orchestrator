# outputs.tf
#
# ---knowledge---
# module_id: infra.terraform.modules.deployer-sa.outputs
# system: infra
# app: terraform
# solution_class: class
# patterns: [pre-standard-snapshot]
# derives_from: null
# owner_sme: iac-sme
# tier: L1
# interfaces: [service_account_email, service_account_id]
# invariants: ""
# gotchas: ""
# related: ["#1911"]
# do_not_duplicate: null
# ---knowledge---
output "service_account_email" {
  description = "Deployer service account email, or null while deployer_enabled is OFF."
  value       = var.enabled ? google_service_account.deployer[0].email : null
}

output "service_account_id" {
  description = "Deployer service account id, or null while deployer_enabled is OFF."
  value       = var.enabled ? google_service_account.deployer[0].id : null
}
