output "service_account_email" {
  description = "Deployer service account email, or null while deployer_enabled is OFF."
  value       = var.enabled ? google_service_account.deployer[0].email : null
}

output "service_account_id" {
  description = "Deployer service account id, or null while deployer_enabled is OFF."
  value       = var.enabled ? google_service_account.deployer[0].id : null
}
