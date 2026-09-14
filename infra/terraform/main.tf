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

  # The web build (issue #606) publishes to Artifact Registry as
  # $_AR_REPO/$_IMAGE:$_TAG — the real reference for the web-surface module:
  # us-central1-docker.pkg.dev/<project_id>/ao-images/portal:<web_image_tag>.
  # The tag is explicit and immutable (never `latest`); the all-zero default
  # marks "no build promoted yet" and go-live pins the real COMMIT_SHA.
  web_image = "us-central1-docker.pkg.dev/${var.project_id}/ao-images/portal:${var.web_image_tag}"
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

module "web_surface" {
  source = "./modules/web-surface"

  enabled    = var.enable_web
  name       = "${var.env}-web"
  image      = local.web_image
  project_id = var.project_id
  region     = var.region
  domain     = var.web_domain
}

# The self-hosted upstream paperclip runtime (issue #411, ADR-0013): a process
# boundary beside the control plane, not a control-plane service. Count-gated on
# enable_paperclip, so with the flag OFF (the committed default) it is inert.
module "paperclip_runtime" {
  source = "../paperclip/terraform"

  enabled    = var.enable_paperclip
  name       = "${var.env}-paperclip"
  image      = var.paperclip_image
  project_id = var.project_id
  region     = var.region
}
