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

variable "enable_web" {
  description = "Deploy the public web surface (portal + shared-frontend) at the custom domain. OFF until promoted."
  type        = bool
  default     = false
}

variable "enable_paperclip" {
  description = "Deploy the self-hosted upstream paperclip runtime beside the control plane (issue #411, ADR-0013). OFF until promoted."
  type        = bool
  default     = false
}

variable "enable_hermes" {
  description = "Independent rollout/kill-switch for the local hermes agent-service provider (issue #255, module-catalog issue #349, feature-flag registry entry `hermes`). Not a separate deployable process — no OCI image, no terraform resource — so this variable declares the switch only; the in-process gateway provider is gated at the rollout layer. OFF until promoted."
  type        = bool
  default     = false
}

variable "enable_chat" {
  description = "Serve the conversational surface (issue #503, ADR-0023: POST /v1/chat/completions, POST /api/chat, GET /v1/models). Its own flag, so chat can be promoted or killed without promoting the gateway or the portal. OFF until promoted."
  type        = bool
  default     = false
}

# --- Workbook-surface flags (issue #644, workbook-13) ------------------------
#
# The five surfaces workbook-11/12/5 added own an in-module switch; each also
# has a row in infra/feature-flags/registry.yaml (services.<name>, kept in
# lock-step with the variables below by scripts/check-feature-flags.py) and a
# flag in infra/rollout/rollout-state.yaml (so the rollout pipeline can promote
# it) that mirrors it.
#
# These variables create NO resource — the code ships inside the portal, the
# gateway or the guardrails service — so they are switches, not deploy targets.
# They are not inert: infra/cloudbuild/apply.yaml passes each one explicitly to
# `terraform plan` from its `_ENABLE_*` substitution (all "false" until a
# reviewed promotion), and the `workbook_surface_flags` output records the
# rendered posture in the apply log, so "flag promoted, nothing deployed"
# cannot pass unnoticed.

variable "enable_org_chart" {
  description = "Serve the org-chart portal view (issue #644, workbook-13). In-module switch: surfaces.org_chart in portal/config/feature-flags.yaml. OFF until promoted."
  type        = bool
  default     = false
}

variable "enable_skill_studio" {
  description = "Serve the skill-studio portal view (issue #644, workbook-13). In-module switch: surfaces.skill_studio in portal/config/feature-flags.yaml. OFF until promoted."
  type        = bool
  default     = false
}

variable "enable_task_board" {
  description = "Serve the tenant task board (issue #644, workbook-13). In-module switch: surfaces.task_board in portal/config/feature-flags.yaml. OFF until promoted."
  type        = bool
  default     = false
}

variable "enable_mcp_outbound" {
  description = "Allow outbound MCP server calls (issue #644, workbook-13). In-module switch: AO_MCP_OUTBOUND_ENABLED in gateway/mcp/outbound.py. OFF until promoted."
  type        = bool
  default     = false
}

variable "enable_sandbox_runtime" {
  description = "Allow a real sandbox runtime to run (issue #644, workbook-13). In-module switch: SandboxEnablement in guardrails/sandbox/enablement.py. OFF until promoted."
  type        = bool
  default     = false
}

# The ERP module's portal surface (ERP-07, issue #652). Its own switch rather
# than `enable_portal`: the module is independently promotable and independently
# killable, and the module's manifest records that this row lands with the
# surface that becomes reachable. It creates no resource — the code ships inside
# the portal deployable — so it is a switch, not a deploy target, and
# `erp_module_enabled` records the posture it rendered.
variable "enable_erp_module" {
  description = "Serve the ERP module's portal surface (ERP-07, issue #652). In-module switch: surfaces.erp_module in portal/config/feature-flags.yaml. OFF until promoted."
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

# The web-surface build already ships (issue #606, infra/cloudbuild/web-image.yaml),
# so its image is the real Artifact-Registry reference assembled in main.tf from
# project_id + this tag. The build's tag gate refuses an empty tag or `latest`;
# the all-zero default marks "no build promoted yet" and the go-live apply pins
# the real build's commit sha.
variable "web_image_tag" {
  description = "Image tag for the public web surface; the AR path is assembled in main.tf."
  type        = string
  default     = "0000000000000000000000000000000000000000"
}

variable "web_domain" {
  description = "Public hostname served by the web surface (custom domain + Google-managed TLS)."
  type        = string
  default     = "ai.purebliss.app"
}

# --- Pinned upstream runtime image (self-hosted paperclip, issue #411) -------
#
# The runtime is consumed as a pinned OCI image — never a floating `latest`.
# The pin and its provenance are recorded in infra/paperclip/release.yaml; an
# upgrade bumps both this default and that record in one reviewed PR.

variable "paperclip_image" {
  description = "Pinned upstream image for the self-hosted paperclip runtime (see infra/paperclip/release.yaml)."
  type        = string
  default     = "ghcr.io/paperclipai/paperclip:v2026.831.1"
}

# --- fleet-cron container pair (issue #900, EPIC #706, lane L5 #884) --------
#
# The fleet-cron container pair on the shared-services on-prem HA cluster
# (nodes .31/.42), active-active. OFF by default; no resource is created
# until this flag is promoted (GR-5).

variable "enable_fleet_cron" {
  description = "Deploy the fleet-cron container pair to the shared-services HA cluster (issue #900, EPIC #706). OFF until promoted."
  type        = bool
  default     = false
}

variable "fleet_cron_image" {
  description = "Container image reference for the fleet-cron image (infra/fleet/Dockerfile, issue #709/#710). No default — required at promotion."
  type        = string
  default     = null
}

variable "fleet_cron_ssh_private_key_path" {
  description = "Path to the SSH private key used to reach the shared-services nodes. A path, never key material (GR-6). No default — required at promotion."
  type        = string
  default     = null
}

variable "fleet_cron_keydb_password_secret_ref" {
  description = "Reference (name/path) to the KeyDB password used for the active-active distributed lock. Never the secret value (GR-6). No default — required at promotion."
  type        = string
  default     = null
}
