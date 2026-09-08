# Declarative scaffold for one control-plane service (Cloud Run v2).
#
# Every resource is count-gated on var.enabled so that with the flag closed
# (the default) Terraform creates nothing. This is the shape a real service
# takes once its phase promotes a build; the count-gating guarantees a plan
# with all flags OFF stays inert.

locals {
  create = var.enabled ? 1 : 0
  labels = merge(
    {
      service    = var.name
      managed_by = "terraform"
      product    = "agent-orchestrator"
    },
    var.labels,
  )
}

# Enable the Cloud Run API only when a service is actually being deployed.
resource "google_project_service" "cloudrun" {
  count              = local.create
  project            = var.project_id
  service            = "run.googleapis.com"
  disable_on_destroy = false
}

# The service itself. Ingress is internal by default (var.ingress), so nothing
# is publicly reachable until a go-live explicitly opens it.
resource "google_cloud_run_v2_service" "this" {
  count    = local.create
  name     = var.name
  location = var.region
  project  = var.project_id
  ingress  = var.ingress
  labels   = local.labels

  template {
    service_account = var.service_account_email

    containers {
      image = var.image

      env {
        name  = "SERVICE_NAME"
        value = var.name
      }
      env {
        name  = "FEATURE_FLAG_DEFAULT"
        value = "off"
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
