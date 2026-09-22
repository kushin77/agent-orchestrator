# Web-surface outputs. Every output is null while the flag is OFF, so
# downstream automation cannot read a value for a surface that does not exist.

# ---knowledge---
# module_id: infra.terraform.modules.web-surface.outputs
# system: infra
# app: terraform
# solution_class: class
# patterns: [pre-standard-snapshot]
# derives_from: null
# owner_sme: iac-sme
# tier: L1
# interfaces: [service_name, service_uri, domain, domain_mapping_name, runtime_service_account, auth_gate_env_secrets]
# invariants: ""
# gotchas: ""
# related: ["#1911"]
# do_not_duplicate: null
# ---knowledge---
output "service_name" {
  description = "Web Cloud Run service name, or null while the flag is OFF."
  value       = var.enabled ? google_cloud_run_v2_service.this[0].name : null
}

output "service_uri" {
  description = "Web Cloud Run service URI, or null while the flag is OFF."
  value       = var.enabled ? google_cloud_run_v2_service.this[0].uri : null
}

output "domain" {
  description = "Public hostname served by the web surface, or null while the flag is OFF."
  value       = var.enabled ? var.domain : null
}

output "domain_mapping_name" {
  description = "Cloud Run domain mapping resource name, or null when no mapping is created — the flag is OFF, or the retired GCP edge route is not declared."
  # The splat is required, not stylistic (issue #731): `web[0]` is an INVALID
  # INDEX as soon as `create_gcp_edge_route` gates this resource off, so the
  # everyday promoted posture (enabled = true, route retired) would have failed
  # at plan time. `one()` reads null for the mapping that was deliberately not
  # created, which is the contract this file already states for a flag-OFF run.
  value = one(google_cloud_run_domain_mapping.web[*].name)
}

output "runtime_service_account" {
  description = "The web surface's runtime identity — the service account whose only grants are reads on the declared auth-gate secrets, or null while the flag is OFF."
  value       = var.enabled ? google_service_account.portal[0].email : null
}

output "auth_gate_env_secrets" {
  description = "The Secret Manager secrets the runtime reads, by the env each supplies; ids only, never a value (GR-6). Null while the flag is OFF. The apply log therefore records what was wired, so 'promoted but never supplied' is visible rather than silent."
  value       = var.enabled ? { for secret in local.auth_env.secrets : secret.env => secret.secret_id } : null
}
