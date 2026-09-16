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

# tini is PID 1: it reaps the rungs' orphans and forwards SIGTERM/SIGINT to the
# entrypoint, which is what makes `docker stop` a clean stop rather than an
# abrupt kill of a loop that holds a claim (AGENTS.md rule 24).
ENTRYPOINT ["/usr/bin/tini", "--", "/repo/infra/fleet/entrypoint.sh"]
CMD []
