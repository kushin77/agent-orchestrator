# Declarative scaffold for the public web surface (ai.purebliss.app).
#
# Everything is count-gated on var.enabled so that with the flag closed (the
# committed default) Terraform creates nothing. When promoted, this module
# declares:
#   - a public Cloud Run v2 service (portal + shared-frontend bundle),
#   - public unauthenticated invocation (roles/run.invoker for allUsers),
#   - a DNS managed zone plus a CNAME record for the custom hostname,
#   - a Cloud Run domain mapping that provisions Google-managed TLS,
#   - and the console's auth-gate environment (issue #730): the JWKS mirror
#     mounted from Secret Manager and the root-admin allowlist injected from a
#     secret version, read by a dedicated runtime identity.
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

  # The console's auth-gate environment (issue #730), declared ONCE in
  # `auth-env.json` beside this module and PROJECTED from here. The env names,
  # the secret ids, the mount path and the volume name exist in that file and
  # nowhere else, so a rename cannot leave the deploy and the console disagreeing
  # — `scripts/check-portal-auth-env.sh` refuses a name restated here as
  # `env-name-restated`. The declaration carries no value (GR-6): a mounted file
  # for the JWKS mirror, an injected secret VERSION for the allowlist.
  auth_env         = jsondecode(file("${path.module}/auth-env.json"))
  auth_env_secrets = { for secret in local.auth_env.secrets : secret.env => secret }
  auth_env_file    = [for secret in local.auth_env.secrets : secret if secret.delivery == "file"]
  auth_env_value   = [for secret in local.auth_env.secrets : secret if secret.delivery == "value"]
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

# Secret Manager must be enabled in the project for a secret to be readable (and
# therefore mountable) at all; without it the revision fails to start.
resource "google_project_service" "secretmanager" {
  count              = local.create
  project            = var.project_id
  service            = "secretmanager.googleapis.com"
  disable_on_destroy = false
}

# The runtime identity, dedicated (never the project's default compute service
# account). The console needs exactly one privilege — read the two auth-gate
# secrets — so the grants below are the whole of its access, and it holds no
# other role anywhere.
resource "google_service_account" "portal" {
  count        = local.create
  project      = var.project_id
  account_id   = "${var.name}-runtime"
  display_name = "Portal web surface runtime (${var.name})"
  description  = "Runtime identity for the public web surface (issue #730): reads the auth-gate JWKS mirror and the root-admin allowlist from Secret Manager, and nothing else."
}

# Read access, one grant per declared secret. This is part of the wiring rather
# than an extra: a secret volume or a secret_key_ref without it makes the
# revision fail with PERMISSION_DENIED, and the console then serves nothing.
resource "google_secret_manager_secret_iam_member" "auth_gate" {
  for_each = var.enabled ? local.auth_env_secrets : {}

  project   = var.project_id
  secret_id = each.value.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.portal[0].email}"
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
    # The dedicated identity above, not the project default.
    service_account = google_service_account.portal[0].email

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

      # The declared auth-gate environment, projected — never restated. The
      # mirror is a FILE the console opens, so its variable carries the path the
      # volume below puts it at; the allowlist is a value, so it is injected from
      # a secret version. Neither payload exists in this repository (GR-6): only
      # the secret id does.
      dynamic "env" {
        for_each = local.auth_env_file
        iterator = secret
        content {
          name  = secret.value.env
          value = "${secret.value.mount_dir}/${secret.value.filename}"
        }
      }

      dynamic "env" {
        for_each = local.auth_env_value
        iterator = secret
        content {
          name = secret.value.env
          value_source {
            secret_key_ref {
              secret  = secret.value.secret_id
              version = secret.value.version
            }
          }
        }
      }

      dynamic "volume_mounts" {
        for_each = local.auth_env_file
        iterator = secret
        content {
          name       = secret.value.volume
          mount_path = secret.value.mount_dir
        }
      }

      resources {
        limits = {
          cpu    = var.cpu
          memory = var.memory
        }
      }
    }

    # Each declared file-delivered secret becomes one volume whose single item is
    # the filename the env above names, at `latest` — so a key rollover is a new
    # secret version picked up by the next revision, not a redeploy.
    dynamic "volumes" {
      for_each = local.auth_env_file
      iterator = secret
      content {
        name = secret.value.volume
        secret {
          secret = secret.value.secret_id
          items {
            path    = secret.value.filename
            version = secret.value.version
          }
        }
      }
    }
  }

  depends_on = [google_project_service.run, google_project_service.secretmanager]
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
