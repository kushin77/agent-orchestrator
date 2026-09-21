# One control-plane service on Cloud Run (internal by default).
#
# Everything is gated by `enabled`, which defaults to false (IaC mandate).
# With the flag closed this module is inert — it creates no service, no API
# enablement, nothing. Promotion = a reviewed go-live flips `enabled` and
# supplies a real `image`.

variable "enabled" {
  description = "Master flag-gate for this service. When false (default), nothing is created."
  type        = bool
  default     = false
}

variable "name" {
  description = "Cloud Run service name (e.g. dev-registry)."
  type        = string
}

variable "project_id" {
  description = "GCP project id. Required at apply."
  type        = string
  default     = null
}

variable "region" {
  description = "GCP region for the service."
  type        = string
  default     = "us-central1"
}

variable "image" {
  description = "Container image to deploy. Placeholder until the owning phase promotes a real build."
  type        = string
}

variable "ingress" {
  description = "Cloud Run ingress setting. Internal by default — no public endpoint until promoted."
  type        = string
  default     = "INGRESS_TRAFFIC_INTERNAL_ONLY"
}

variable "service_account_email" {
  description = "Runtime service account email for the revision. Null = Cloud Run default compute SA."
  type        = string
  default     = null
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

variable "labels" {
  description = "Extra labels merged onto the service."
  type        = map(string)
  default     = {}
}

variable "secret_env" {
  description = <<-EOT
    Secret Manager-backed env vars, injected via secret_key_ref, keyed by the
    env name the container reads. Empty by default -- no secret is projected
    unless the caller explicitly supplies one (issue #1748). The caller (this
    module's root) is responsible for deciding WHETHER to pass an entry (the
    flag gate) and for granting the runtime identity secretAccessor on it; this
    module only wires the entry it is given.
  EOT
  type = map(object({
    secret_id = string
    version   = string
  }))
  default = {}
}
