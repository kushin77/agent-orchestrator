# Google provider for the control-plane environment.
#
# No credentials are hard-coded (GR-6): the provider uses Application Default
# Credentials. The only intended apply identity is the flag-gated deployer
# service account (see modules/deployer-sa) driven by the Cloud Build apply
# pipeline — never a console click and never a personal token.
provider "google" {
  project = var.project_id
  region  = var.region
}
