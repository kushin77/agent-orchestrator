# outputs.tf
#
# ---knowledge---
# module_id: infra.terraform.modules.control-plane-service.outputs
# system: infra
# app: terraform
# solution_class: class
# patterns: [pre-standard-snapshot]
# derives_from: null
# owner_sme: iac-sme
# tier: L1
# interfaces: [service_name, service_uri, service_id]
# invariants: ""
# gotchas: ""
# related: ["#1911"]
# do_not_duplicate: null
# ---knowledge---
output "service_name" {
  description = "Cloud Run service name, or null while the flag is OFF."
  value       = var.enabled ? google_cloud_run_v2_service.this[0].name : null
}

output "service_uri" {
  description = "Cloud Run service URI, or null while the flag is OFF."
  value       = var.enabled ? google_cloud_run_v2_service.this[0].uri : null
}

output "service_id" {
  description = "Cloud Run service id, or null while the flag is OFF."
  value       = var.enabled ? google_cloud_run_v2_service.this[0].id : null
}
