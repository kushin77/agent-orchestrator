# Root outputs. Every output is gated on its service flag: when a service is
# OFF (the default) the output is null, so downstream automation cannot read a
# value for a service that does not exist.

output "service_uris" {
  description = "URI per promoted control-plane service; null until the service flag is ON."
  value = {
    for svc, mod in module.control_plane_service :
    svc => mod.service_uri
  }
}

output "deployer_service_account" {
  description = "Email of the flag-gated deployer service account; null until deployer_enabled."
  value       = module.deployer.service_account_email
}
