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

# The `ao-images` Artifact Registry repository the web build (issue #606,
# infra/cloudbuild/web-image.yaml, web-image-trigger.yaml) pushes
# `portal:<commit-sha>` into, and that `local.web_image` above and
# `modules/web-surface` resolve by reference. Declared here because nothing
# in this tree provisioned it (confirmed via `gcloud artifacts repositories
# list` — no `ao-images` entry existed) even though three files already
# assumed it.
#
# Deliberately UNCONDITIONAL, not gated behind an `enable_*` flag: this repo
# is a build precondition, not a promoted surface (it holds no traffic, costs
# nothing idle, and grants no IAM). Every `enable_*` var in this file names a
# CANONICAL_SERVICES-shaped surface with a required infra/feature-flags/registry.yaml
# row (scripts/check-feature-flags.py, `make verify`) — the web build has to
# push a real image and get a real commit-sha tag BEFORE `enable_web` can ever
# be promoted with a non-placeholder `web_image_tag`, so gating the repo behind
# `enable_web` (or a new flag needing its own registry row) would deadlock that
# build -> tag -> promote chain.
resource "google_artifact_registry_repository" "ao_images" {
  project       = var.project_id
  location      = var.region
  repository_id = "ao-images"
  format        = "DOCKER"
  description   = "Web build images (issue #606) — portal:<commit-sha> pushed by web-image.yaml, consumed by modules/web-surface."

  labels = {
    managed_by = "terraform"
    product    = "agent-orchestrator"
    surface    = "web"
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

# The fleet-cron container pair on the shared-services on-prem HA cluster
# (issue #900, EPIC #706, lane L5 #884): 2 replicas, active-active, on nodes
# 192.168.168.31 / .42. Count-gated on enable_fleet_cron, so with the flag OFF
# (the committed default) this module is inert.
module "fleet_cron" {
  source = "./modules/fleet-cron"

  count = var.enable_fleet_cron ? 1 : 0

  enabled                   = var.enable_fleet_cron
  image                     = var.fleet_cron_image
  ssh_private_key_path      = var.fleet_cron_ssh_private_key_path
  keydb_password_secret_ref = var.fleet_cron_keydb_password_secret_ref
}
