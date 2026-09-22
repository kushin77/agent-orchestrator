# Inputs for the self-hosted paperclip runtime module (issue #411, ADR-0013).
#
# Every resource is gated by `enabled`, which the root environment wires to the
# `enable_paperclip` flag defaulting to false (IaC mandate / flag-gated OFF).
# With the flag closed this module is inert: it creates no service and probes
# nothing. Promotion = a reviewed go-live flips the flag and the flag-gated
# deploy pipeline rolls the runtime out.

# ---knowledge---
# module_id: infra.paperclip.terraform.variables
# system: infra
# app: paperclip
# solution_class: class
# patterns: [pre-standard-snapshot]
# derives_from: null
# owner_sme: iac-sme
# tier: L1
# interfaces: [enabled, name, project_id, region, image, port, health_path, ingress, (+3 more)]
# invariants: ""
# gotchas: ""
# related: ["#1911"]
# do_not_duplicate: null
# ---knowledge---
variable "enabled" {
  description = "Master flag-gate for the paperclip runtime. When false (default) nothing is created."
  type        = bool
  default     = false
}

variable "name" {
  description = "Cloud Run service name for the self-hosted paperclip runtime."
  type        = string
  default     = "paperclip"
}

variable "project_id" {
  description = "GCP project id. Required at apply."
  type        = string
  default     = null
}

variable "region" {
  description = "GCP region for the runtime."
  type        = string
  default     = "us-central1"
}

variable "image" {
  description = "Pinned upstream OCI reference. Never a floating tag (see infra/paperclip/release.yaml)."
  type        = string
  default     = "ghcr.io/paperclipai/paperclip:v2026.831.1"
}

variable "port" {
  description = "Container port the upstream runtime listens on."
  type        = number
  default     = 3100
}

variable "health_path" {
  description = "HTTP health path probed by the startup and liveness probes (upstream GET /api/health)."
  type        = string
  default     = "/api/health"
}

variable "ingress" {
  description = "Cloud Run ingress. Internal by default: the runtime sits beside the control plane, not on the public internet."
  type        = string
  default     = "INGRESS_TRAFFIC_INTERNAL_ONLY"
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
