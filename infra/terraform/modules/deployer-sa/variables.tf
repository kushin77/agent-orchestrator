# Deployer service account for the control-plane apply path.
#
# The deployer is the ONLY identity that runs `terraform apply` — via the
# flag-gated Cloud Build apply pipeline (infra/cloudbuild/apply.yaml). There is
# no console path. `enabled` defaults to false; the account is created only by
# a reviewed go-live that also grants it exactly the roles it needs.

variable "enabled" {
  description = "Master flag-gate. When false (default), no service account is created."
  type        = bool
  default     = false
}

variable "account_id" {
  description = "Service account id (must be 6-30 chars, lowercase alphanumeric + dashes)."
  type        = string
}

variable "display_name" {
  description = "Human-readable display name."
  type        = string
  default     = null
}

variable "project_id" {
  description = "GCP project id. Required at apply."
  type        = string
  default     = null
}

variable "roles" {
  description = "IAM roles granted to the deployer at promotion. Empty by default."
  type        = list(string)
  default     = []
}
