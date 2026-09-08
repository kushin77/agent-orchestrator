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
