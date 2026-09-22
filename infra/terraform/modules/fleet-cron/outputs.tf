# outputs.tf
#
# ---knowledge---
# module_id: infra.terraform.modules.fleet-cron.outputs
# system: infra
# app: terraform
# solution_class: class
# patterns: [pre-standard-snapshot]
# derives_from: null
# owner_sme: iac-sme
# tier: L1
# interfaces: [container_names, node_hosts, enabled]
# invariants: ""
# gotchas: ""
# related: ["#1911"]
# do_not_duplicate: null
# ---knowledge---
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
