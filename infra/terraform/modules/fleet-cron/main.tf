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
        "-e KEYDB_HOST=${var.keydb_host}",
        "-e KEYDB_PORT=${var.keydb_port}",
        "-e HTTP_ADDR=:${var.port}",
        "--health-cmd='wget -qO- http://localhost:${var.port}/health || exit 1'",
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
