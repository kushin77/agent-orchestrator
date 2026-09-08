# Terraform version + provider pins for the agent-orchestrator control plane.
#
# Providers are pinned to versions known to the local offline provider cache
# (see scripts/check-terraform.sh) so `make verify` can run `terraform
# validate` with no network. New infrastructure ships flag-gated OFF by
# default (IaC mandate) — every resource is inert until a reviewed go-live
# flips its `enable_*` flag.
terraform {
  required_version = ">= 1.6"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }
}
