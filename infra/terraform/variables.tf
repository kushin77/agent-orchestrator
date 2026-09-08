# Root variables for the agent-orchestrator control-plane environment.
#
# Every `enable_*` flag defaults to `false` (IaC mandate / flag-gated OFF).
# scripts/check-feature-flags.py enforces this mechanically and keeps the
# flags in lock-step with infra/feature-flags/registry.yaml, so a new surface
# cannot ship on by accident.

variable "project_id" {
  description = "GCP project id for the control plane. Required at apply; supplied via tfvars or environment."
  type        = string
  default     = null
}

variable "region" {
  description = "GCP region for control-plane services."
  type        = string
  default     = "us-central1"
}

variable "env" {
  description = "Environment label prepended to resource names (dev/staging/prod)."
  type        = string
  default     = "dev"
}

# --- Per-service master flags (all OFF by default; promoted per go-live) ----

variable "enable_registry" {
  description = "Deploy the Agent Registry & Profiling service (phase 1). OFF until promoted."
  type        = bool
  default     = false
}

variable "enable_gateway" {
  description = "Deploy the Model Gateways service (phase 2). OFF until promoted."
  type        = bool
  default     = false
}

variable "enable_engine" {
  description = "Deploy the State-machine execution engine (phase 3). OFF until promoted."
  type        = bool
  default     = false
}

variable "enable_guardrails" {
  description = "Deploy the Security & guardrails service (phase 4). OFF until promoted."
  type        = bool
  default     = false
}

variable "enable_telemetry" {
  description = "Deploy the Observability / telemetry service (phase 5). OFF until promoted."
  type        = bool
  default     = false
}

variable "enable_identity" {
  description = "Deploy the Tenant identity / RBAC service (phase 6). OFF until promoted."
  type        = bool
  default     = false
}

variable "enable_portal" {
  description = "Deploy the admin control-plane portal (phase 7). OFF until promoted."
  type        = bool
  default     = false
}

# --- Deployer service account (the ONLY apply route) ------------------------

variable "deployer_enabled" {
  description = "Create the flag-gated deployer service account. OFF until the apply path is reviewed."
  type        = bool
  default     = false
}

variable "deployer_roles" {
  description = "IAM roles granted to the deployer service account at promotion. Empty by default — populated by a reviewed go-live."
  type        = list(string)
  default     = []
}

# --- Container images (placeholders until each phase ships a real build) ----
#
# Every image is OFF behind its service flag; no resource is created. Values
# are overridden at promotion. The word "placeholder" marks these as scaffold
# defaults, never deployable artifacts.

variable "registry_image" {
  description = "Container image for the registry service. Placeholder until phase 1 promotes a build."
  type        = string
  default     = "us-docker.pkg.dev/example-control-plane/registry:placeholder"
}

variable "gateway_image" {
  description = "Container image for the gateway service. Placeholder until phase 2 promotes a build."
  type        = string
  default     = "us-docker.pkg.dev/example-control-plane/gateway:placeholder"
}

variable "engine_image" {
  description = "Container image for the engine service. Placeholder until phase 3 promotes a build."
  type        = string
  default     = "us-docker.pkg.dev/example-control-plane/engine:placeholder"
}

variable "guardrails_image" {
  description = "Container image for the guardrails service. Placeholder until phase 4 promotes a build."
  type        = string
  default     = "us-docker.pkg.dev/example-control-plane/guardrails:placeholder"
}

variable "telemetry_image" {
  description = "Container image for the telemetry service. Placeholder until phase 5 promotes a build."
  type        = string
  default     = "us-docker.pkg.dev/example-control-plane/telemetry:placeholder"
}

variable "identity_image" {
  description = "Container image for the identity service. Placeholder until phase 6 promotes a build."
  type        = string
  default     = "us-docker.pkg.dev/example-control-plane/identity:placeholder"
}

variable "portal_image" {
  description = "Container image for the portal service. Placeholder until phase 7 promotes a build."
  type        = string
  default     = "us-docker.pkg.dev/example-control-plane/portal:placeholder"
}
