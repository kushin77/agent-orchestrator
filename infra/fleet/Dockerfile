# syntax=docker/dockerfile:1
#
# fleet-cron — the fleet's scheduled automation, packaged as one image.
#
#   docker build -f infra/fleet/Dockerfile -t agent-fleet-cron:dev .
#   docker run --rm agent-fleet-cron:dev python3 fleet/cron.py status
#
# The dependency contract this image may not exceed is
# `infra/fleet/inventory.yaml`; `scripts/check-fleet-cron-image.sh` holds the two
# in lock-step in both directions (a package here that is not listed there, and
# a listing there that is not installed here, are both refusals). The issue that
# ordered the image is #709, EPIC #706 (D1).
#
# TWO THINGS ABOUT THE ORDER OF THIS FILE.
#
# 1. Every layer that reaches the network sits ABOVE `COPY . /repo`. The
#    dependency layers are then cache-stable across a checkout edit, so the gate
#    that builds this image costs seconds on a box that has built it once, and
#    the full cold build is paid once rather than per lane.
# 2. The schedule is NOT copied in. `entrypoint.sh` installs it from the single
#    owner of those crontab lines, `fleet/cron.py install`. A schedule written
#    here a second time is exactly the drift this image exists to remove:
#    `fleet/cron.py` owns the markers, the interval and the interpreter path,
#    and the image only asks it to install.

FROM python:3.12-slim

# --- base packages (inventory: packages[], stage=runtime) --------------------
#
#   tini             PID 1 that reaps the rungs and forwards signals.
#   git              every rung's branch / worktree operation.
#   openssh-client   `git push` over ssh.
#   cron             `crontab` (fleet/cron.py's own read/write path) and the
#                    daemon the entrypoint runs in the foreground.
#   ca-certificates  TLS for gh and the pinned claude release.
#   curl             the pinned claude release download (build-time only).
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        tini \
        git \
        openssh-client \
        cron \
        ca-certificates \
        curl; \
    rm -rf /var/lib/apt/lists/*

# --- PyYAML for THIS image's interpreter (issue #1034) ----------------------
#
# The reconcile rung (`governance/reconcile/cli.py`) imports
# `governance.lifecycle`, which validates its closure vocabulary against
# `governance/lifecycle/controls.yaml` at import time (#885/#1065) and needs
# PyYAML to read it. That import-time dependency was never declared for this
# image, so every dispatch of the reconcile rung raised `ModuleNotFoundError`
# before running a single pass — `scripts/check-fleet-cron-dev-run.sh`'s
# baseline red, before any mutant is even tried.
#
# `pip`, not `apt-get install python3-yaml`: the apt package pulls Debian's
# own `python3` as a dependency, which would occupy `/usr/bin/python3` before
# the symlink step below runs and silently split the image onto two
# interpreters — the one `fleet/cron.py` hardcodes into every crontab line
# and the one this image's own commands (`sys.executable`) actually run
# under. `pip` installs into the one interpreter this image has (below),
# so both paths keep resolving to the same site-packages.
RUN set -eux; \
    python3 -m pip install --no-cache-dir --root-user-action=ignore "PyYAML==6.0.2"

# --- gh: the GitHub CLI, from GitHub's own apt repository -------------------
RUN set -eux; \
    arch="$(dpkg --print-architecture)"; \
    curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
        -o /usr/share/keyrings/githubcli-archive-keyring.gpg; \
    chmod 0644 /usr/share/keyrings/githubcli-archive-keyring.gpg; \
    printf 'deb [arch=%s signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main\n' "$arch" \
        > /etc/apt/sources.list.d/github-cli.list; \
    apt-get update; \
    apt-get install -y --no-install-recommends gh; \
    rm -rf /var/lib/apt/lists/*

# --- docker-ce-cli: the Docker CLI, from Docker's own apt repository --------
#
# The promote-portal rung's `--apply` path (issue #1329/#1341, the
# `agent-cron-rw` service) talks to the HOST's Docker daemon over
# `/var/run/docker.sock` to pull and recreate the `shared-services-agentconsole`
# container. `docker-ce-cli` — CLIENT ONLY, no `dockerd` — is the smallest
# package that gives that binary; it depends on no daemon package, unlike the
# distro's own `docker.io`, which pulls containerd/runc as if this image ran
# its own engine. The image already adds one third-party apt repo for the same
# reason (`gh`, above), so this follows the same reproducible pattern rather
# than inventing a second one.
RUN set -eux; \
    arch="$(dpkg --print-architecture)"; \
    install -m 0755 -d /etc/apt/keyrings; \
    curl -fsSL https://download.docker.com/linux/debian/gpg \
        -o /etc/apt/keyrings/docker.asc; \
    chmod a+r /etc/apt/keyrings/docker.asc; \
    codename="$(. /etc/os-release && echo "$VERSION_CODENAME")"; \
    printf 'deb [arch=%s signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian %s stable\n' "$arch" "$codename" \
        > /etc/apt/sources.list.d/docker.list; \
    apt-get update; \
    apt-get install -y --no-install-recommends docker-ce-cli; \
    rm -rf /var/lib/apt/lists/*

# --- claude: the pinned native release, checksum-verified -------------------
#
# The CLI is a self-contained linux-x64 binary published per version with a
# manifest carrying its checksum. Pinning the VERSION and verifying the
# CHECKSUM is what makes the image reproducible without vendoring 200 MB into
# the repository: a re-tag upstream cannot silently change what is built here.
ARG CLAUDE_VERSION=2.1.270
RUN set -eux; \
    base="https://downloads.claude.ai/claude-code-releases/${CLAUDE_VERSION}"; \
    curl -fsSL "${base}/manifest.json" -o /tmp/claude-manifest.json; \
    expected="$(python3 -c 'import json; print(json.load(open("/tmp/claude-manifest.json"))["platforms"]["linux-x64"]["checksum"])')"; \
    curl -fsSL "${base}/linux-x64/claude" -o /usr/local/bin/claude; \
    actual="$(sha256sum /usr/local/bin/claude | cut -d' ' -f1)"; \
    [ "$actual" = "$expected" ]; \
    chmod 0755 /usr/local/bin/claude; \
    rm -f /tmp/claude-manifest.json; \
    /usr/local/bin/claude --version

# --- the interpreter the crontab lines name ---------------------------------
#
# `fleet/cron.py` writes its lines with the literal `/usr/bin/python3`, which is
# the system interpreter on the box the fleet runs on today. In this image
# python lives at /usr/local/bin/python3, so the path the schedule names must
# exist or every tick dies with a 127 nobody reads: the container appears to run
# while no lane is ever dispatched. Rather than edit the schedule's owner, the
# image provides the path the owner names.
RUN set -eux; \
    if [ ! -x /usr/bin/python3 ]; then ln -sf /usr/local/bin/python3 /usr/bin/python3; fi; \
    /usr/bin/python3 -V

# --- the checkout, and the state roots --------------------------------------
#
# `COPY . /repo` is the porting contract: the image IS this repository.
#
# `.board/` and `.fleet/` are excluded from the build context by
# `.dockerignore`, deliberately, and the inventory records that the image
# DEPENDS on that exclusion — they are runtime STATE (a claim ledger, a mailbox,
# a heartbeat), and an image that baked one host's board would hand every
# container the same stale claims. The paths are created empty here and are the
# mount points a real run fills.
COPY . /repo
WORKDIR /repo
RUN set -eux; \
    mkdir -p /repo/.fleet /repo/.board /repo/.verify; \
    python3 -c "import sys; sys.path.insert(0, '/repo/fleet'); import runtime; print('fleet dir:', runtime.FLEET_DIR)"; \
    test -f /repo/fleet/cron.py; \
    test -f /repo/infra/fleet/entrypoint.sh; \
    chmod 0755 /repo/infra/fleet/entrypoint.sh

# --- container healthcheck (issue #712, EPIC #706 D4) -----------------------
#
# Docker's own liveness probe, against `infra/fleet/healthz.py`'s `/health`
# path (an alias of this repo's own `/healthz`, added so this image also
# answers the peer contract's name — shared-services `docker/cronrunner`).
# `AO_FLEET_PORT` mirrors what `infra/fleet/env_contract.py` resolves (default
# 8790); declaring it here too means a container started with no override
# still probes the port it actually serves.
#
# The probe is `python3 infra/fleet/healthz.py probe`, not `curl`:
# `inventory.yaml` (read-only to this lane) declares curl `stage: build`
# only, and a runtime dependency on a build-stage package would make the
# porting contract say something false that the gate's static audit does not
# check for (it checks package presence, not stage). `python3` is already a
# declared runtime binary, and `healthz.py`'s own `probe` subcommand exits
# 1 on anything but HTTP 200 — a stale decision document, a short job list,
# or a process up but no longer writing evidence all flip this unhealthy
# (#712's own acceptance: "not merely process exists").
#
# WHO IS LISTENING. The image's DEFAULT command (`cron -f`, no operator
# command) starts no /health listener — `entrypoint.sh` says why: nothing
# writes a decision document for that posture yet (D3+, blocked-by #711), so
# an image-level HEALTHCHECK that started one would only ever answer
# not-ready. `infra/fleet/docker-compose.agent-cron.yml` (D2's wired
# consumer) replaces the command with `dev_run.py`, which serves its own
# copy AND overrides this HEALTHCHECK with its own `healthcheck:` — so the
# probe declared here is this image's OWN default for a bare `docker run`,
# not yet exercised by anything in this repo until a later deliverable wires
# a listener into the default path too.
ENV AO_FLEET_PORT=8790
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python3 /repo/infra/fleet/healthz.py probe --port "${AO_FLEET_PORT}" || exit 1

# --- restart policy (documented here; the compose file is D3's lane, #711) --
#
# This image declares HEALTHCHECK; it does not and must not declare
# `restart:` — that is a compose-level field, and
# `infra/fleet/docker-compose.agent-cron.yml` belongs to issue #711, not this
# one. The snippet its consumer needs, in inventory-agnostic form (no node
# name, no literal beyond this image's own port default):
#
#   services:
#     agent-cron:
#       restart: unless-stopped   # AGENTS.md rule 24: a killed container comes
#                                  # back rather than leaving the schedule dark
#       init: true                 # a second PID-1 reaper at the compose layer;
#                                  # belt-and-suspenders with this image's own tini
#       stop_grace_period: 30s     # >= one reconcile pass (issue #304), so
#                                  # `docker compose down` does not SIGKILL a
#                                  # loop mid claim-release
#       deploy:
#         resources:
#           limits:
#             cpus: "1.0"          # a CPU cap; tune to the host's headroom —
#                                  # this image's own three jobs are bursty,
#                                  # not sustained-load
#
# tini is PID 1: it reaps the rungs' orphans and forwards SIGTERM/SIGINT to the
# entrypoint, which is what makes `docker stop` a clean stop rather than an
# abrupt kill of a loop that holds a claim (AGENTS.md rule 24).
ENTRYPOINT ["/usr/bin/tini", "--", "/repo/infra/fleet/entrypoint.sh"]
CMD []
