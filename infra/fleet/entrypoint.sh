#!/usr/bin/env bash
# fleet-cron entrypoint — the image's PID 1 child (tini execs it).
#
# Two jobs, in this order:
#
#   1. ASSERT THE SCHEDULE, from its single owner. `fleet/cron.py install` is the
#      only writer of the three crontab lines (watchdog, prune, reconcile); this
#      script holds no copy of them, no interval literal and no interpreter
#      path. That is the whole point of the image: `fleet/cron.py` stays the one
#      place the schedule is defined, and the container is a place it is
#      installed.
#
#      The install runs BEFORE any command the operator passes, because the
#      container's contract is "the schedule is installed here": the issue's own
#      verification is `docker run --rm <image> python3 fleet/cron.py status`,
#      and a `status` that reads the crontab before anything installed it would
#      report NOT installed and exit 1 for a container that is working exactly
#      as specified. `install_lines()` refreshes only our own marked lines and
#      keeps every foreign one, so running it on every start is idempotent
#      rather than destructive.
#
#      `AO_FLEET_CRON_NO_INSTALL=1` skips step 1, which is what makes the
#      assertion above measurable rather than asserted: the gate runs the same
#      `status` with and without it and requires the two to disagree.
#
#   2. RUN WHAT WAS ASKED FOR. An operator command replaces the default; with no
#      command the entrypoint runs cron in the FOREGROUND, because a container
#      whose main process daemonises exits immediately and takes the schedule
#      with it.
set -eu

REPO="${AO_FLEET_REPO:-/repo}"
INTERVAL="${AO_FLEET_CRON_INTERVAL:-2}"

if [ "${AO_FLEET_CRON_NO_INSTALL:-0}" != "1" ]; then
  python3 "${REPO}/fleet/cron.py" install --interval "${INTERVAL}"
fi

if [ "$#" -gt 0 ]; then
  exec "$@"
fi

exec cron -f
