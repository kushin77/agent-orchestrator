# Control-plane environment composition.
#
# Each service is an instance of modules/control-plane-service, driven by an
# `enable_*` flag that defaults to OFF (IaC mandate). With every flag closed —
# the committed state — this configuration declares but creates nothing; a
# plan shows zero resources. Promotion flips one flag at a time behind a
# reviewed go-live (infra/feature-flags/registry.yaml records the state).

locals {
  services = {
    registry   = { enabled = var.enable_registry, image = var.registry_image }
    gateway    = { enabled = var.enable_gateway, image = var.gateway_image }
    engine     = { enabled = var.enable_engine, image = var.engine_image }
    guardrails = { enabled = var.enable_guardrails, image = var.guardrails_image }
    telemetry  = { enabled = var.enable_telemetry, image = var.telemetry_image }
    identity   = { enabled = var.enable_identity, image = var.identity_image }
    portal     = { enabled = var.enable_portal, image = var.portal_image }
  }
}

module "control_plane_service" {
  source = "./modules/control-plane-service"

  for_each = local.services

  enabled    = each.value.enabled
  name       = "${var.env}-${each.key}"
  image      = each.value.image
  project_id = var.project_id
  region     = var.region
}

module "deployer" {
  source = "./modules/deployer-sa"

  enabled      = var.deployer_enabled
  account_id   = "control-plane-deployer"
  display_name = "agent-orchestrator control-plane deployer (flag-gated apply)"
  project_id   = var.project_id
  roles        = var.deployer_roles
}
