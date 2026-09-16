# fleet-cron: the agent-orchestrator fleet-cron container PAIR on the
# shared-services on-prem HA cluster (nodes .31 / .42), active-active.
#
# Everything here is gated by `enabled`, which defaults to false (IaC
# mandate / GR-5). With the flag closed this module creates nothing.

variable "enabled" {
  description = "Master flag-gate for the fleet-cron pair. When false (default), nothing is created."
  type        = bool
  default     = false
}

variable "image" {
  description = "Container image reference for the fleet-cron image (infra/fleet/Dockerfile build, issue #709/#710). No default — supplied at promotion; never a floating `latest`."
  type        = string
  default     = null
}

variable "container_name" {
  description = "Base container name; each node gets `<container_name>-<node key>`."
  type        = string
  default     = "agent-orchestrator-fleet-cron"
}

variable "port" {
  description = "HTTP port the container listens on and exposes for /health and /metrics (matches infra/fleet/inventory.yaml)."
  type        = number
  default     = 8790
}

variable "nodes" {
  description = <<-EOT
    The shared-services HA cluster nodes this pair deploys to, keyed by a
    short node id. Defaults to the pair named in issue #900: 192.168.168.31
    and 192.168.168.42 (the same nodes shared-services/docker/cronrunner runs
    active-active on). `ssh_user` is the deploy account already used by
    shared-services' host-platform module for these nodes.
  EOT
  type = map(object({
    host     = string
    ssh_user = string
  }))
  default = {
    "31" = {
      host     = "192.168.168.31"
      ssh_user = "deploy"
    }
    "42" = {
      host     = "192.168.168.42"
      ssh_user = "deploy"
    }
  }
}

variable "ssh_private_key_path" {
  description = "Path to the SSH private key used to reach the shared-services nodes. A path, never key material — the file is expected to already exist outside this repo (GR-6). No default: required at apply."
  type        = string
  default     = null
}

variable "keydb_host" {
  description = "KeyDB host used for the distributed `scheduler:lock:<job-id>` SETNX lock that arbitrates the active-active pair (matches shared-services docker/cronrunner's locking scheme). Defaults to the primary KeyDB node named in the peer README."
  type        = string
  default     = "192.168.168.31"
}

variable "keydb_port" {
  description = "KeyDB port for the distributed lock connection."
  type        = number
  default     = 7379
}

variable "keydb_password_secret_ref" {
  description = <<-EOT
    Reference to the KeyDB password — a secret NAME/path, never the secret
    value (GR-6). Resolved node-side via an env-file the container is started
    with (`--env-file <this path>`), so the password is never interpolated
    into a `docker run` command line, never lands in shell history or
    `docker inspect`'s recorded command, and never enters Terraform state.
    The referenced file is provisioned out-of-band (secret manager / vault
    agent on the node), not by this module. No default: required at apply.
  EOT
  type        = string
  default     = null
}

variable "restart_policy" {
  description = "Docker restart policy for the container."
  type        = string
  default     = "unless-stopped"
}

variable "cpus" {
  description = "CPU limit passed to `docker run --cpus`."
  type        = string
  default     = "0.5"
}

variable "memory" {
  description = "Memory limit passed to `docker run --memory`."
  type        = string
  default     = "256m"
}

variable "health_start_period" {
  description = "Docker healthcheck start-period."
  type        = string
  default     = "30s"
}
