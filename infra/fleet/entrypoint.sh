#!/usr/bin/env bash
# fleet-cron entrypoint — the image's PID 1 child (tini execs it).
#
# Three jobs, in this order:
#
#   1. VALIDATE THE ENVIRONMENT, from its single owner (D2, #710).
#      `infra/fleet/env_contract.py` declares every `AO_FLEET_*` variable this
#      image reads, its default and the rule its value must satisfy; this script
#      asks it before anything else runs, and refuses by name when the answer is
#      no. The dev-first run (`dev_run.py`) asks the same contract, so the two
#      cannot disagree about what a valid environment is — and the interval this
#      script installs is read back from the contract rather than defaulted here
#      a second time.
#
#   2. ASSERT THE SCHEDULE, from its single owner. `fleet/cron.py install` is the
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
#      `AO_FLEET_CRON_NO_INSTALL=1` skips step 2, which is what makes the
#      assertion above measurable rather than asserted: the gate runs the same
#      `status` with and without it and requires the two to disagree.
#
#   3. RUN WHAT WAS ASKED FOR. An operator command replaces the default; with no
#      command the entrypoint runs cron in the FOREGROUND, because a container
#      whose main process daemonises exits immediately and takes the schedule
#      with it.
set -eu

REPO="${AO_FLEET_REPO:-/repo}"

# --- the environment, validated from its single owner (D2, #710) --------------
#
# A container started with a value no code path can honour would come up looking
# healthy and do nothing: `AO_FLEET_CRON_INTERVAL=banana` installs a schedule
# whose ticks never fire, and the container's own logs would not say so. So the
# environment is checked BEFORE anything is installed, against
# `infra/fleet/env_contract.py` — the one declaration of what this image's
# environment may be — and a refusal is terminal and named:
#
#   entrypoint: REFUSED — dry-run-required: AO_FLEET_DRY_RUN='0' is not one of '1'
#
# The resolved INTERVAL is read back from that same contract rather than
# defaulted a second time here, so the schedule's interval has one default in
# the image and not two.
if ! python3 "${REPO}/infra/fleet/env_contract.py" check; then
  echo "entrypoint: REFUSED — the environment does not satisfy ${REPO}/infra/fleet/env_contract.py" >&2
  exit 1
fi
INTERVAL="$(python3 "${REPO}/infra/fleet/env_contract.py" print | sed -n 's/^AO_FLEET_CRON_INTERVAL=//p')"

if [ "${AO_FLEET_CRON_NO_INSTALL:-0}" != "1" ]; then
  python3 "${REPO}/fleet/cron.py" install --interval "${INTERVAL}"
fi

if [ "$#" -gt 0 ]; then
  exec "$@"
fi

exec cron -f
