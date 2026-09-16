# Public web-surface module inputs (ai.purebliss.app).
#
# Every resource is gated by `enabled`, which defaults to false (IaC mandate
# / flag-gated OFF). With the flag closed this module is inert — it creates
# no service, no DNS records, no domain mapping, no certificate, nothing.
# Promotion = a reviewed go-live flips `enabled`; the root module passes the
# real Artifact Registry image assembled from project_id + web_image_tag.
#
# TWO gates, not one (issue #731). `enabled` decides whether the SURFACE exists;
# `create_gcp_edge_route` (default false) decides whether the GCP EDGE ROUTE for
# its hostname exists — the Cloud DNS record plus the Cloud Run domain mapping.
# That route is RETIRED: the live fronting for the domain is a Cloudflare tunnel
# to the shared-services run half (docs/EDGE-CUTOVER.md), so promoting the
# surface must NOT silently re-create a competing GCP record. Declaring the GCP
# route again is a deliberate, separate act.

variable "enabled" {
  description = "Master flag-gate for the web surface. When false (default), nothing is created."
  type        = bool
  default     = false
}

variable "name" {
  description = "Cloud Run service name for the public web surface."
  type        = string
  default     = "web"

  # The runtime service account is `<name>-runtime` (see google_service_account
  # in main.tf), and GCP caps an account id at 30 characters, so the name is
  # bounded here rather than failing at apply with a length error.
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,22}$", var.name))
    error_message = "name must be 2-23 lowercase letters, digits or hyphens starting with a letter, so the runtime service account id (<name>-runtime) fits GCP's 30-character limit."
  }
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
  description = "Public hostname the web surface serves. Also the name the RETIRED GCP edge route would claim, so it is only tied to a Google-managed certificate when create_gcp_edge_route = true."
  type        = string
  default     = "ai.purebliss.app"
}

variable "zone_name" {
  description = "DNS managed zone name that holds the web surface records."
  type        = string
  default     = "purebliss-app"
}

variable "dns_name" {
  description = "DNS zone apex (with trailing dot). The zone is created only when create_gcp_edge_route and create_dns_zone are both true."
  type        = string
  default     = "purebliss.app."
}

variable "create_gcp_edge_route" {
  description = "Declare the RETIRED GCP edge route for var.domain: the Cloud DNS CNAME record to ghs.googlehosted.com and the Cloud Run domain mapping that provisions Google-managed TLS. Default false — the live fronting is a Cloudflare tunnel to the shared-services run half (docs/EDGE-CUTOVER.md), so a default deploy creates neither. Neither this flag nor the edge route affects the Cloud Run service or its public invoker binding."
  type        = bool
  default     = false
}

variable "create_dns_zone" {
  description = "Create the DNS managed zone (true) or resolve a pre-existing one by name (false). Only meaningful with create_gcp_edge_route = true, because the zone exists to hold that route's record; it defaults to false so a default deploy creates no zone."
  type        = bool
  default     = false

  # The incoherent combination is refused BY NAME rather than applied (issue
  # #731): a zone whose only purpose was to hold the retired route's record is a
  # zone that holds nothing. Before this, `create_dns_zone` defaulted true while
  # the route was retired, so the module's own default declaration asked for an
  # orphan zone.
  validation {
    condition     = !var.create_dns_zone || var.create_gcp_edge_route
    error_message = "create-dns-zone-orphan: create_dns_zone = true declares a DNS managed zone to hold the GCP edge route's record, but create_gcp_edge_route = false means that route is RETIRED and no record will be created in it (docs/EDGE-CUTOVER.md). Declare the route with create_gcp_edge_route = true, or leave create_dns_zone = false."
  }
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
