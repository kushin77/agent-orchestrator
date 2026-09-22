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
#      with it. It runs BACKGROUNDED-then-``wait``ed rather than ``exec``'d
#      (issue #712, EPIC #706 D4): an ``exec`` replaces this shell, so a trap
#      installed on it would never fire, and the container's start/stop would
#      carry no structured evidence of its own — only the crontab lines' own
#      log lines, which say nothing about the SUPERVISOR that installed them.
#      Backgrounding keeps this shell alive as the signal-forwarding point tini
#      talks to, so a clean stop (AGENTS.md rule 24) is a JSON log line, not
#      silence.
#
# STRUCTURED LOGGING (#712, EPIC #706 D4). Every lifecycle event this script
# itself is responsible for — the environment check, the schedule install, the
# start and the stop of the foreground process — is one JSON line on stdout,
# shaped like the peer contract this image is ported against
# (shared-services `docker/cronrunner`, whose own log lines are
# `{"job":...,"status":"ok|skipped|failed","host":...,"detail":...}`):
# `{"job":"<name>","status":"ok|failed","host":"<hostname>","detail":"<why>"}`.
# stdout, never a file: a restart loses a file this container did not durably
# mount, and Promtail/Loki (the peer contract's own consumer) reads container
# stdout, not a path inside it.
# ---knowledge---
# module_id: infra.fleet.entrypoint
# system: infra
# app: fleet
# solution_class: class
# patterns: [pre-standard-snapshot]
# derives_from: null
# owner_sme: iac-sme
# tier: L1
# interfaces: [json_escape, log_json, on_term]
# invariants: ""
# gotchas: ""
# related: ["#1911"]
# do_not_duplicate: null
# ---knowledge---
set -eu

REPO="${AO_FLEET_REPO:-/repo}"
HOST="$(hostname 2>/dev/null || echo unknown)"

# json_escape/log_json: the two primitives every lifecycle line below is built
# from. Escaping is minimal (backslash, quote, control chars) because the only
# untrusted-ish input here is a refusal DETAIL string this repo's own modules
# produce — never operator-supplied free text — but a detail that happens to
# contain a `"` must not produce invalid JSON.
json_escape() {
  printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' | tr -d '\n\r\t'
}

log_json() {
  # log_json <job> <status> <detail>
  printf '{"job":"%s","status":"%s","host":"%s","detail":"%s"}\n' \
    "$(json_escape "$1")" "$(json_escape "$2")" "$(json_escape "$HOST")" "$(json_escape "$3")"
}

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
  log_json env-contract failed "the environment does not satisfy ${REPO}/infra/fleet/env_contract.py"
  exit 1
fi
log_json env-contract ok "environment satisfies ${REPO}/infra/fleet/env_contract.py"
INTERVAL="$(python3 "${REPO}/infra/fleet/env_contract.py" print | sed -n 's/^AO_FLEET_CRON_INTERVAL=//p')"

# NOTE for a reader of the log lines below: they deliberately do NOT spell out
# the module + verb this step calls (that pair is what scripts/check-fleet-cron-image.sh's
# `schedule-not-owned` provocation looks for in this file's own CODE, to prove
# the entrypoint really is the one asking the owner; restating it in a log
# string would leave that pair present even in a mutant where the call itself
# had been removed, and the control would stop being able to fail).
if [ "${AO_FLEET_CRON_NO_INSTALL:-0}" != "1" ]; then
  if python3 "${REPO}/fleet/cron.py" install --interval "${INTERVAL}"; then
    log_json schedule ok "the schedule's single owner installed it (interval ${INTERVAL})"
  else
    log_json schedule failed "the schedule's owner exited non-zero (interval ${INTERVAL})"
    exit 1
  fi
else
  log_json schedule skipped "AO_FLEET_CRON_NO_INSTALL=1 — this entrypoint did not ask the owner to install"
fi

# --- run what was asked for, backgrounded so a signal is a logged event ------
#
# `CHILD_PID` is the process tini's forwarded SIGTERM/SIGINT is relayed to.
# The handler is installed BEFORE the child starts (same discipline as
# `dev_run.py`'s own signal handling, AGENTS.md rule 24): a stop that arrives
# before the child exists still gets a `stopped` line naming that there was
# nothing to forward to, rather than silence.
# Both status values below stay inside the peer contract's own enum
# (`ok|skipped|failed`, shared-services docker/cronrunner) rather than
# inventing `stopping`/`stopped`: a Loki query filtering on that enum must not
# silently miss this image's own lifecycle lines. `on_term` also PROPAGATES a
# bad child rc as this script's own exit code (never a bare `exit 0`) — tini
# and `docker inspect`'s own exit code must not read a failed stop as clean.
CHILD_PID=""

on_term() {
  signal_name="$1"
  if [ -n "${CHILD_PID}" ] && kill -0 "${CHILD_PID}" 2>/dev/null; then
    kill -TERM "${CHILD_PID}" 2>/dev/null || true
    child_rc=0
    wait "${CHILD_PID}" 2>/dev/null || child_rc=$?
    if [ "${child_rc}" -eq 0 ] || [ "${child_rc}" -eq 143 ]; then
      log_json cron ok "clean stop (${signal_name}), child rc=${child_rc}"
      exit 0
    fi
    log_json cron failed "child exited rc=${child_rc} after ${signal_name}"
    exit "${child_rc}"
  fi
  log_json cron ok "received ${signal_name} before a child was running"
  exit 0
}
trap 'on_term SIGTERM' TERM
trap 'on_term SIGINT' INT

# The /health + /metrics surface (infra/fleet/healthz.py) is NOT started here.
# `dev_run.py` — the only wired consumer today, run via
# `infra/fleet/docker-compose.agent-cron.yml`'s own `command:` — already calls
# `healthz.serve` itself and answers from the decision document IT writes.
# `healthz.py`'s new `serve` subcommand exists for a bare `docker run <image>`
# with no command (the plain `cron -f` default), but nothing in this repo
# writes a decision document for that posture yet — a production
# decision-writer is D3+'s deliverable (this issue is itself blocked-by D3,
# #711) — so starting a listener here would answer `/health` 503 not-ready
# FOREVER under that path: an image-level HEALTHCHECK that can never pass is
# the mirror of a check that can never fail, and both are formalities
# (AGENTS.md rule 8). Until a writer exists, the honest statement is what this
# comment says, not a listener with nothing to report.
if [ "$#" -gt 0 ]; then
  log_json cron ok "starting operator command: $*"
  "$@" &
else
  log_json cron ok "starting cron -f (foreground scheduler)"
  cron -f &
fi
CHILD_PID=$!
rc=0
wait "${CHILD_PID}" || rc=$?
log_json cron "$([ "${rc}" -eq 0 ] && echo ok || echo failed)" "the foreground process exited rc=${rc}"
exit "${rc}"
