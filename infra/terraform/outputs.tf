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

output "web_surface_uri" {
  description = "URI of the public web surface; null until enable_web."
  value       = module.web_surface.service_uri
}

output "web_surface_domain" {
  description = "Public hostname served by the web surface; null until enable_web."
  value       = module.web_surface.domain
}

output "paperclip_runtime_uri" {
  description = "URI of the self-hosted paperclip runtime; null until enable_paperclip"
  value       = module.paperclip_runtime.service_uri
}

# Workbook surfaces (issue #644, workbook-13). The five flags gate code inside
# the portal, the gateway and the guardrails services, so they create no
# resource and no module output could report them. This output is what keeps
# them from being inert declarations: every plan and apply records the exact
# posture of each workbook switch, so a promotion that reached the pipeline but
# was never rendered is visible as `false` in the deploy log instead of passing
# unnoticed. All five are false until a reviewed promotion.
output "workbook_surface_flags" {
  description = "Rendered posture of the five workbook-surface switches (issue #644); false until each one is promoted."
  value = {
    org_chart       = var.enable_org_chart
    skill_studio    = var.enable_skill_studio
    task_board      = var.enable_task_board
    mcp_outbound    = var.enable_mcp_outbound
    sandbox_runtime = var.enable_sandbox_runtime
  }
}
