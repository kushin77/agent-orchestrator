terraform {
  backend "gcs" {
    bucket = "agent-orchestrator-tfstate"
    prefix = "control-plane"
  }
}
