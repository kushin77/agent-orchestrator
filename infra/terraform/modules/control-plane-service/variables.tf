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
