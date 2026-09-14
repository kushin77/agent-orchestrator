# Declarative scaffold for the self-hosted upstream paperclip runtime.
#
# ADR-0013 adopts the upstream Paperclip CLI as an external operator surface
# across a process boundary. This module stands that process up **beside** the
# control plane: one Cloud Run v2 service running the pinned upstream image,
# count-gated on var.enabled so the committed state (flag OFF) creates nothing.
#
# Health is a real check, not a hope: the template declares both a startup and a
# liveness probe against upstream's GET /api/health, so an unhealthy runtime is
# never routed to. The deploy pipeline (../cloudbuild/deploy.yaml) probes the
# same path after a deploy.
#
# There is no apply path here: this module is inert until a reviewed go-live
# flips `enable_paperclip` and the flag-gated deploy pipeline runs.

locals {
  create = var.enabled ? 1 : 0
  labels = merge(
    {
      service    = var.name
      managed_by = "terraform"
      product    = "agent-orchestrator"
      surface    = "paperclip-runtime"
    },
    var.labels,
  )
}

# The Cloud Run API is enabled only when the runtime is actually being deployed.
resource "google_project_service" "cloudrun" {
  count              = local.create
  project            = var.project_id
  service            = "run.googleapis.com"
  disable_on_destroy = false
}

# The self-hosted runtime process. Internal ingress: it is reached across the
# process boundary from the control plane, never from the public internet.
resource "google_cloud_run_v2_service" "this" {
  count    = local.create
  name     = var.name
  location = var.region
  project  = var.project_id
  ingress  = var.ingress
  labels   = local.labels

  template {
    containers {
      image = var.image

      ports {
        container_port = var.port
      }

      env {
        name  = "SERVICE_NAME"
        value = var.name
      }
      env {
        name  = "FEATURE_FLAG_DEFAULT"
        value = "off"
      }
      env {
        name  = "PAPERCLIP_HEALTH_PATH"
        value = var.health_path
      }

      # A runtime that does not answer GET /api/health never starts and is never
      # routed to.
      startup_probe {
        http_get {
          path = var.health_path
          port = var.port
        }
        initial_delay_seconds = 5
        timeout_seconds       = 3
        period_seconds        = 10
        failure_threshold     = 6
      }

      # A runtime that stops answering GET /api/health is restarted.
      liveness_probe {
        http_get {
          path = var.health_path
          port = var.port
        }
        timeout_seconds   = 3
        period_seconds    = 30
        failure_threshold = 3
      }

      resources {
        limits = {
          cpu    = var.cpu
          memory = var.memory
        }
      }
    }
  }

  depends_on = [google_project_service.cloudrun]
}
