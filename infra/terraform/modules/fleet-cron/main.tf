# fleet-cron container PAIR (issue #900, EPIC #706, lane L5 #884).
#
# Declares the two nodes of the shared-services on-prem HA cluster
# (192.168.168.31 / .42) that will run the agent-orchestrator fleet-cron
# container active-active, once promoted. Pattern harvested from the peer
# repo's real Terraform for the sibling cronrunner container (GR-10
# provenance): shared-services/infra/modules/host-platform/main.tf, resource
# `null_resource.cronrunner` (~L6229) — `null_resource` + `file`/`remote-exec`
# provisioners over an ssh `connection` block, NOT the kreuzwerker/docker
# provider (shared-services does not use it for these on-prem hosts).
#
# Deliberate divergences from the peer pattern, both GR-6/GR-5-driven:
#   * no `docker build` on the node — the image is a promoted reference
#     (var.image), built and pushed by the image pipeline (#709/#710), not
#     compiled on the deploy target.
#   * the KeyDB password is never interpolated into the `docker run` command
#     line. The peer's `-e KEYDB_PASSWORD='${var.keydb_password}'` bakes the
#     secret into both Terraform state and `docker inspect`'s recorded
#     command — exactly what EPIC #706 rules out ("never `env_file` ... bakes
#     tokens into `docker inspect`" generalizes to any secret-bearing command
#     line). Instead the container is started with `--env-file
#     <secret_ref>`, where the ref is a path resolved and populated node-side,
#     out of band.
#
# GR-17 (local-code-first): the container's own health probe and port
# variable follow OUR image's contract (infra/fleet/env_contract.py,
# infra/fleet/docker-compose.agent-cron.yml, issue #710), not the peer
# cronrunner's — they are two different images. `AO_FLEET_PORT` is our port
# variable (not the Go cronrunner's `HTTP_ADDR`), the probe path defaults to
# `/healthz` (our `infra/fleet/healthz.py`, and #706's own acceptance
# criteria: `curl -fsS http://localhost:<port>/healthz`) rather than the
# peer's `/health`, and the probe command is a `python3 -c
# urllib.request...` one-liner (matching the compose file's own probe)
# rather than `wget`: the compose file records that this image "ships no
# curl it may rely on" and the same applies to wget — python3 is what the
# image is guaranteed to have.
#
# Every resource is for_each-gated on var.enabled (via local.nodes), so with
# the flag closed (the committed default) this module is inert — a plan with
# `enable_fleet_cron = false` shows zero resources, matching
# control-plane-service's `local.create` convention.

locals {
  nodes = var.enabled ? var.nodes : {}
}

resource "null_resource" "fleet_cron" {
  for_each = local.nodes

  triggers = {
    host                = each.value.host
    ssh_user            = each.value.ssh_user
    image               = var.image
    container_name      = "${var.container_name}-${each.key}"
    port                = var.port
    keydb_host          = var.keydb_host
    keydb_port          = var.keydb_port
    keydb_password_ref  = var.keydb_password_secret_ref
    restart_policy      = var.restart_policy
    cpus                = var.cpus
    memory              = var.memory
    health_start_period = var.health_start_period
    health_path         = var.health_path
    ssh_key_path        = var.ssh_private_key_path
  }

  provisioner "remote-exec" {
    inline = [
      "docker pull ${var.image}",
      "docker stop ${var.container_name}-${each.key} 2>/dev/null || true",
      "docker rm ${var.container_name}-${each.key} 2>/dev/null || true",
      join(" ", [
        "docker run -d",
        "--name ${var.container_name}-${each.key}",
        "--restart=${var.restart_policy}",
        "--cpus ${var.cpus}",
        "--memory ${var.memory}",
        "-p 0.0.0.0:${var.port}:${var.port}",
        "--env-file ${var.keydb_password_secret_ref}",
        "-e AO_FLEET_PORT=${var.port}",
        "-e KEYDB_HOST=${var.keydb_host}",
        "-e KEYDB_PORT=${var.keydb_port}",
        "--health-cmd=python3 -c \"import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:${var.port}${var.health_path}',timeout=3).status==200 else 1)\"",
        "--health-interval=30s",
        "--health-timeout=10s",
        "--health-retries=3",
        "--health-start-period=${var.health_start_period}",
        "${var.image}",
      ]),
    ]

    connection {
      type        = "ssh"
      host        = each.value.host
      user        = each.value.ssh_user
      private_key = file(var.ssh_private_key_path)
    }
  }

  provisioner "remote-exec" {
    when       = destroy
    on_failure = continue
    inline = [
      "docker stop ${self.triggers.container_name} 2>/dev/null || true",
      "docker rm ${self.triggers.container_name} 2>/dev/null || true",
    ]

    connection {
      type        = "ssh"
      host        = self.triggers.host
      user        = self.triggers.ssh_user
      private_key = file(self.triggers.ssh_key_path)
    }
  }
}
