output "container_names" {
  description = "Container names per node, or an empty map while the flag is OFF."
  value       = { for k, v in null_resource.fleet_cron : k => v.triggers.container_name }
}

output "node_hosts" {
  description = "Node hostnames the pair deploys to, or an empty map while the flag is OFF."
  value       = { for k, v in local.nodes : k => v.host }
}

output "enabled" {
  description = "Whether the fleet-cron pair is promoted."
  value       = var.enabled
}
