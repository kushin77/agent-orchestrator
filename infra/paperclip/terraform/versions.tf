# Terraform + provider pins for the self-hosted paperclip runtime module.
#
# Pinned to the same versions as the root control-plane environment so the
# offline provider cache that scripts/check-terraform.sh uses resolves this
# module too (the root environment references it — see infra/terraform/main.tf).
terraform {
  required_version = ">= 1.6"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }
}
