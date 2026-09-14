# Public web-surface module inputs (ai.purebliss.app).
#
# Every resource is gated by `enabled`, which defaults to false (IaC mandate
# / flag-gated OFF). With the flag closed this module is inert — it creates
# no service, no DNS records, no domain mapping, no certificate, nothing.
# Promotion = a reviewed go-live flips `enabled`; the root module passes the
# real Artifact Registry image assembled from project_id + web_image_tag.

variable "enabled" {
  description = "Master flag-gate for the web surface. When false (default), nothing is created."
  type        = bool
  default     = false
}

variable "name" {
  description = "Cloud Run service name for the public web surface."
  type        = string
  default     = "web"
}

variable "project_id" {
  description = "GCP project id. Required at apply."
  type        = string
  default     = null
}

variable "region" {
  description = "GCP region for the web service."
  type        = string
  default     = "us-central1"
}

variable "image" {
  description = "Container image for the web surface: the real Artifact Registry reference the web build (#606) promotes — us-central1-docker.pkg.dev/<project>/ao-images/portal:<immutable-tag> — assembled by the root module."
  type        = string
}

variable "domain" {
  description = "Public hostname the web surface serves (custom domain + Google-managed TLS)."
  type        = string
  default     = "ai.purebliss.app"
}

variable "zone_name" {
  description = "DNS managed zone name that holds the web surface records."
  type        = string
  default     = "purebliss-app"
}

variable "dns_name" {
  description = "DNS zone apex (with trailing dot). The zone is created only when create_dns_zone is true."
  type        = string
  default     = "purebliss.app."
}

variable "create_dns_zone" {
  description = "Create the DNS managed zone (true) or attach records to a pre-existing zone (false)."
  type        = bool
  default     = true
}

variable "labels" {
  description = "Extra labels merged onto the service."
  type        = map(string)
  default     = {}
}

variable "cpu" {
  description = "CPU limit for the container."
  type        = string
  default     = "1"
}

variable "memory" {
  description = "Memory limit for the container."
  type        = string
  default     = "512Mi"
}
