# Web-surface outputs. Every output is null while the flag is OFF, so
# downstream automation cannot read a value for a surface that does not exist.

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
  description = "Cloud Run domain mapping resource name, or null while the flag is OFF."
  value       = var.enabled ? google_cloud_run_domain_mapping.web[0].name : null
}
