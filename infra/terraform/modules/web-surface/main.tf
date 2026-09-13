# Declarative scaffold for the public web surface (ai.purebliss.app).
#
# Everything is count-gated on var.enabled so that with the flag closed (the
# committed default) Terraform creates nothing. When promoted, this module
# declares:
#   - a public Cloud Run v2 service (portal + shared-frontend bundle),
#   - public unauthenticated invocation (roles/run.invoker for allUsers),
#   - a DNS managed zone plus a CNAME record for the custom hostname,
#   - a Cloud Run domain mapping that provisions Google-managed TLS.
#
# There is no apply path here: this module is inert until a reviewed go-live
# flips the flag and the flag-gated apply pipeline (infra/cloudbuild/apply.yaml)
# deploys it as the deployer service account.

locals {
  create = var.enabled ? 1 : 0
  labels = merge(
    {
      service    = var.name
      managed_by = "terraform"
      product    = "agent-orchestrator"
      surface    = "web"
    },
    var.labels,
  )
}

# APIs required by the web surface, enabled only when it is being deployed.
resource "google_project_service" "run" {
  count              = local.create
  project            = var.project_id
  service            = "run.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "dns" {
  count              = local.create
  project            = var.project_id
  service            = "dns.googleapis.com"
  disable_on_destroy = false
}

# The public web service. Public ingress is deliberate: this is the one
# tenant-facing surface, exposed only after a reviewed go-live flips `enabled`.
resource "google_cloud_run_v2_service" "this" {
  count    = local.create
  name     = var.name
  location = var.region
  project  = var.project_id
  ingress  = "INGRESS_TRAFFIC_ALL"
  labels   = local.labels

  template {
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

  depends_on = [google_project_service.run]
}

# Public access: a web UI must be invocable without per-user credentials.
resource "google_cloud_run_v2_service_iam_member" "public" {
  count    = local.create
  project  = google_cloud_run_v2_service.this[0].project
  location = google_cloud_run_v2_service.this[0].location
  name     = google_cloud_run_v2_service.this[0].name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# DNS managed zone for the domain (self-contained by default; set
# create_dns_zone = false to attach records to a pre-existing zone instead).
resource "google_dns_managed_zone" "zone" {
  count       = var.enabled && var.create_dns_zone ? 1 : 0
  name        = var.zone_name
  dns_name    = var.dns_name
  description = "Public DNS zone for the agent-orchestrator web surface (${var.domain})."
  project     = var.project_id
  depends_on  = [google_project_service.dns]
}

# CNAME record mapping the custom hostname to Google's hosted load balancer —
# the verification target for the Cloud Run domain mapping (managed TLS).
resource "google_dns_record_set" "web" {
  count        = local.create
  name         = "${var.domain}."
  type         = "CNAME"
  ttl          = 300
  managed_zone = var.zone_name
  rrdatas      = ["ghs.googlehosted.com."]
  depends_on   = [google_dns_managed_zone.zone]
}

# Custom domain + Google-managed TLS for the web service.
resource "google_cloud_run_domain_mapping" "web" {
  count    = local.create
  name     = var.domain
  location = var.region
  project  = var.project_id

  metadata {
    namespace = var.project_id
  }

  spec {
    route_name = google_cloud_run_v2_service.this[0].name
  }

  depends_on = [google_cloud_run_v2_service.this]
}
