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

  # The Nous provider credential (issue #1748): declared ONCE in
  # provider-credentials.json beside this file and PROJECTED from here --
  # never restated. No value rides in the declaration (GR-6), only the secret
  # id; the entry is only ever passed to a service's module instance when its
  # `gate` flag is on. `enable_hermes` now defaults to ON
  # (policy-gr5-enabled-by-default, variables.tf), so this wiring is live by
  # default; the secret container itself still carries no value (GR-6).
  provider_credentials = jsondecode(file("${path.module}/provider-credentials.json"))
  provider_credentials_gate = {
    enable_hermes = var.enable_hermes
  }
  gateway_secret_env = {
    for s in local.provider_credentials.secrets : s.env => {
      secret_id = s.secret_id
      version   = s.version
    }
    if s.service == "gateway" && lookup(local.provider_credentials_gate, s.gate, false)
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

  docker_config {
    immutable_tags = true
  }
}

module "control_plane_service" {
  source = "./modules/control-plane-service"

  for_each = local.services

  enabled               = each.value.enabled
  name                  = "${var.env}-${each.key}"
  image                 = each.value.image
  project_id            = var.project_id
  region                = var.region
  service_account_email = each.key == "gateway" ? one(google_service_account.gateway_runtime[*].email) : null
  secret_env            = each.key == "gateway" ? local.gateway_secret_env : {}
}

# Secret Manager must be enabled for the gateway to read the Nous credential
# (issue #1748) -- created only when there is something to read, never
# unconditionally.
resource "google_project_service" "secretmanager_gateway" {
  count              = length(local.gateway_secret_env) > 0 ? 1 : 0
  project            = var.project_id
  service            = "secretmanager.googleapis.com"
  disable_on_destroy = false
}

# A dedicated runtime identity for the gateway, created only once there is a
# secret for it to read (never the project default compute SA -- least
# privilege). Its sole grant is below: read the declared provider secrets.
resource "google_service_account" "gateway_runtime" {
  count        = length(local.gateway_secret_env) > 0 ? 1 : 0
  project      = var.project_id
  account_id   = "${var.env}-gateway-runtime"
  display_name = "Gateway runtime (issue #1748)"
  description  = "Runtime identity for the gateway service: reads the provider credentials declared in provider-credentials.json from Secret Manager, and nothing else."
}

# The secret CONTAINER only -- name, no value, no version (GR-6). Terraform
# declares the id the operator will later push a value into (issue #1748
# step 5); it never creates a google_secret_manager_secret_version, so this
# resource alone can never hold a literal.
resource "google_secret_manager_secret" "gateway_provider_credentials" {
  for_each = local.gateway_secret_env

  project   = var.project_id
  secret_id = each.value.secret_id

  replication {
    auto {}
  }
}

# Read access, one grant per declared+gated secret -- part of the wiring
# (without it the revision fails PERMISSION_DENIED), not an extra.
resource "google_secret_manager_secret_iam_member" "gateway_provider_credentials" {
  for_each = local.gateway_secret_env

  project   = var.project_id
  secret_id = each.value.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.gateway_runtime[0].email}"

  depends_on = [
    google_project_service.secretmanager_gateway,
    google_secret_manager_secret.gateway_provider_credentials,
  ]
}

# Named IAM role bundles for the deployer SA (issue #411/#1136 go-live), keyed
# by var.deployer_role_class — a single tag/substitution selects a bundle
# instead of a raw role list ever passing through Cloud Build substitutions.
# "none" is the fail-closed default: the SA exists but can do nothing.
locals {
  deployer_role_bundles = {
    none = []

    # Enough to manage the 7 Cloud Run services + Artifact Registry + the
    # project APIs this stack enables (run.googleapis.com etc.) — no project
    # editor/owner grant. roles/logging.logWriter is required to run a Cloud
    # Build step AS this SA with options.logging = CLOUD_LOGGING_ONLY
    # (apply.yaml) — without it the deployer SA cannot run the pipeline that
    # is itself the only thing that can grant it roles.
    minimal = [
      "roles/run.admin",
      "roles/artifactregistry.writer",
      "roles/serviceusage.serviceUsageAdmin",
      "roles/iam.serviceAccountUser",
      "roles/logging.logWriter",
    ]

    # minimal + state bucket object admin, for when the deployer SA (rather
    # than a human's own credentials) owns writing infra/terraform state.
    standard = [
      "roles/run.admin",
      "roles/artifactregistry.writer",
      "roles/serviceusage.serviceUsageAdmin",
      "roles/iam.serviceAccountUser",
      "roles/storage.objectAdmin",
    ]
  }

  deployer_roles = local.deployer_role_bundles[var.deployer_role_class]
}

module "deployer" {
  source = "./modules/deployer-sa"

  enabled      = var.deployer_enabled
  account_id   = "control-plane-deployer"
  display_name = "agent-orchestrator control-plane deployer (flag-gated apply)"
  project_id   = var.project_id
  roles        = local.deployer_roles
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
# enable_paperclip, held at OFF (the committed default) per the explicit NO-GO
# decision (issue #1515, docs/PAPERCLIP-PROMOTION-DECISION.md, 2026-09-20).
# UNRESOLVED as of the policy-gr5-enabled-by-default PR (2026-09-21, one day
# later, same owner): that PR's own preamble names "Paperclip live sync" as a
# capability that must ship enabled by default, which reads as a possible
# supersession of the NO-GO. This PR does NOT resolve that on its own
# authority — flagged for an explicit owner call, at the same prominence as
# the CloudBuild trigger exception.
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

# Bucket-scoped state access for the deployer SA — NOT a project-wide storage
# role. Once the deployer SA runs apply.yaml's terraform steps itself
# (_DEPLOYER_SA cutover), it needs to read/write infra/terraform's own
# backend state, and nothing else in Cloud Storage.
resource "google_storage_bucket_iam_member" "deployer_tfstate" {
  count  = var.deployer_enabled ? 1 : 0
  bucket = "agent-orchestrator-tfstate"
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${module.deployer.service_account_email}"
}
