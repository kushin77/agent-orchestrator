# Outputs for the self-hosted paperclip runtime. Gated on the flag: while the
# runtime is OFF the URI is null, so nothing can read a value for a process
# that does not exist.

# ---knowledge---
# module_id: infra.paperclip.terraform.outputs
# system: infra
# app: paperclip
# solution_class: class
# patterns: [pre-standard-snapshot]
# derives_from: null
# owner_sme: iac-sme
# tier: L1
# interfaces: [service_uri, health_url]
# invariants: ""
# gotchas: ""
# related: ["#1911"]
# do_not_duplicate: null
# ---knowledge---
output "service_uri" {
  description = "URI of the paperclip runtime; null until enable_paperclip is ON."
  value       = var.enabled ? google_cloud_run_v2_service.this[0].uri : null
}

output "health_url" {
  description = "The health URL the deploy probe hits; null until enable_paperclip is ON."
  value       = var.enabled ? "${google_cloud_run_v2_service.this[0].uri}${var.health_path}" : null
}
