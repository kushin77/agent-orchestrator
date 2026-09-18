#!/usr/bin/env bash
# check-portal-offline-spog.sh — the fleet SPoG, reproducible from the doc, headless (#732).
#
# WHAT THIS PROVES
#   `docs/PORTAL-OFFLINE-DEV.md` promises a reviewer the fleet Single-Pane-of-Glass
#   from the doc ALONE, with zero external infra. This gate is that promise,
#   executed: it starts the REAL console (`python3 -m portal.server.main`) on a
#   free LOOPBACK port with the promoted surface registry and a session minted by
#   the doc's own helper (`scripts/portal-dev-session.py`), drives the whole
#   status matrix over the loopback socket, and then asserts nothing survived.
#
# THE MATRIX (every row asserted; the codes are why a 404/401 is diagnosable)
#   flag OFF  (/views/fleet.html)          -> 200  the static view is NOT gated
#   flag OFF  (/api/fleet/snapshot)        -> 404 feature_disabled
#   flag OFF  (/api/fleet/events?limit=8)  -> 404 feature_disabled
#   flag ON   (/api/fleet/snapshot, no cookie)  -> 401 unauthorized
#   flag ON   (/api/fleet/snapshot, foreign-key cookie) -> 401: a REAL token
#                                              whose signing key the mirror does
#                                              not publish, so the 401 cannot be
#                                              read as "a cookie was missing"
#   flag ON   (/api/fleet/snapshot, cookie)     -> 200 AND the console's own
#                                                  projection (named rungs, the
#                                                  snapshot's frozen keys) — a
#                                                  200 over an empty body is not
#                                                  "the SPoG renders"
#   flag ON   (/api/fleet/events, cookie)       -> 200, a JSON list
#   flag ON   (/api/fleet/stream, cookie)       -> an SSE `snapshot` frame
#
# WHY IT CANNOT PASS VACUOUSLY
#   * the OFF half and the ON half are opposite directions of the SAME switch: a
#     gate that always saw 404 would fail the ON half, and one that always saw
#     200 would fail the OFF half. Both halves are asserted in one run.
#   * the `401` half proves the surface is access-controlled, not merely gated:
#     the 200 half depends on the cookie being a real RS256 session the running
#     server verifies against the minted mirror.
#   * a THIRD phase re-engages the withdrawal seam (`AO_SURFACE_STATE`) on a
#     surface the registry still declares `on`: the promoted surface must go
#     dark (`404 feature_disabled`) without any commit. That is the trap
#     `portal/server/surface_state.py` documents — an overlay left behind in a
#     checkout silently reverting a promotion — provoked rather than described.
#
# OFFLINE, AND WHAT IT DOES NOT NEED
#   Loopback only (`--host 127.0.0.1`); no network, no container, no published
#   port. `127.0.0.1:<ephemeral>` is picked from the kernel, so a busy port is
#   never a failure — a fresh port is picked instead. This matters on this box:
#   a PUBLISHED container port is not reachable from the host here (measured, see
#   `docs/PORTAL-OFFLINE-DEV.md`), which is why the doc runs the server directly.
#
# EXIT-CONTRACT (the repo's honesty tri-state)
#   0 OK / 1 NOT-OK / 2 CANNOT-ASSESS (python3 or a runtime dep missing, no
#   checkout, the helper refused, the server never came up) — never a silent 0.
#
# Usage: bash scripts/check-portal-offline-spog.sh
set -uo pipefail

self="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/$(basename "${BASH_SOURCE[0]}")"
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)" || exit 2

#: Per-request budget. Generous because the snapshot composes the console's own
#: projection (which one-shot reads the issue board); normally well under a second.
PROBE_TIMEOUT=45
#: The SSE stream never ends by design, so its budget IS the assertion window.
STREAM_TIMEOUT=12

work=""
server_pid=""
port=""
status=""
body_file=""
pass=0
fail=0
started_pids=()
started_ports=()

cleanup() {
  local pid
  if [ "${#started_pids[@]}" -gt 0 ]; then
    for pid in "${started_pids[@]}"; do
      if is_alive "$pid"; then
        kill "$pid" 2>/dev/null || true
      fi
    done
    for pid in "${started_pids[@]}"; do
      wait "$pid" 2>/dev/null || true
    done
  fi
  [ -n "$work" ] && rm -rf "$work" || true
}
trap cleanup EXIT

ok() { printf '  OK    %s\n' "$1"; pass=$((pass + 1)); }
bad() { printf '  FAIL  %s\n' "$1" >&2; fail=$((fail + 1)); }
note() { printf '  ..    %s\n' "$1"; }

cannot_assess() {
  printf 'check-portal-offline-spog: CANNOT-ASSESS — %s\n' "$1" >&2
  exit 2
}

# A zombie is NOT a survivor: `kill -0` succeeds until the shell reaps it, and
# bash only reaps on `wait`, so the state is read from /proc rather than inferred
# from a signal probe that cannot tell the two apart.
is_alive() {
  local pid="$1" state=""
  [ -n "$pid" ] || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  state="$(awk '{print $3}' "/proc/$pid/stat" 2>/dev/null)"
  case "$state" in
    Z*) return 1 ;;
  esac
  return 0
}

free_port() {
  python3 - <<'PY'
import socket

sock = socket.socket()
sock.bind(("127.0.0.1", 0))
print(sock.getsockname()[1])
sock.close()
PY
}

# probe <path> [cookie] -> $status, and the body in $body_file
probe() {
  local path="$1" cookie="${2:-}"
  status="000"
  body_file="$work/probe.body"
  if [ -n "$cookie" ]; then
    status="$(curl -sS --max-time "$PROBE_TIMEOUT" -o "$body_file" -w '%{http_code}' \
      -H "Cookie: $cookie" "http://127.0.0.1:$port$path" 2>"$work/probe.err")" || status="000"
  else
    status="$(curl -sS --max-time "$PROBE_TIMEOUT" -o "$body_file" -w '%{http_code}' \
      "http://127.0.0.1:$port$path" 2>"$work/probe.err")" || status="000"
  fi
}

expect_status() { # expect_status <label> <want> <path> [cookie]
  local label="$1" want="$2" path="$3" cookie="${4:-}"
  probe "$path" "$cookie"
  if [ "$status" = "$want" ]; then
    ok "$label -> $status"
  else
    bad "$label -> $status (expected $want)"
  fi
}

expect_body_has() { # expect_body_has <label> <needle>
  local label="$1" needle="$2"
  case "$(cat "$body_file" 2>/dev/null)" in
    *"$needle"*) ok "$label (body carries $needle)" ;;
    *) bad "$label (body does NOT carry $needle)" ;;
  esac
}

port_closed() { # port_closed <port> — refuses nothing; true when nothing answers
  curl -sS --max-time 3 -o /dev/null "http://127.0.0.1:$1/api/healthz" >/dev/null 2>&1 && return 1
  return 0
}

# start_server <tag> <registry> <state> -> $server_pid / $port
start_server() {
  local tag="$1" registry="$2" state="$3"
  local log="$work/server-$tag.log" candidate="" attempt=0
  while [ "$attempt" -lt 3 ]; do
    attempt=$((attempt + 1))
    candidate="$(free_port)"
    port="$candidate"
    ( cd "$root" && exec env -u PORTAL_AUTH_GATE_JWKS \
        PORTAL_AUTH_GATE_JWKS_FILE="$jwks_file" \
        ROOT_ADMIN_EMAILS="$email" \
        AO_SURFACE_REGISTRY="$registry" \
        AO_SURFACE_STATE="$state" \
        python3 -m portal.server.main --host 127.0.0.1 --port "$candidate" ) >"$log" 2>&1 &
    server_pid=$!
    started_pids+=("$server_pid")
    started_ports+=("$candidate")
    if wait_ready "$log"; then
      note "server up on 127.0.0.1:$port (log $log)"
      return 0
    fi
    if ! is_alive "$server_pid"; then
      note "server exited on port $candidate; retrying on a fresh port"
      continue
    fi
    bad "the server on 127.0.0.1:$port never answered /api/healthz"
    printf '        last log lines: %s\n' "$(tail -3 "$log" 2>/dev/null | tr '\n' ' ')" >&2
    return 1
  done
  bad "the server could not be started on any of 3 free loopback ports"
  return 1
}

wait_ready() { # wait_ready <log> — the public health rail is the readiness signal
  local log="$1" i=0
  while [ "$i" -lt 80 ]; do
    if curl -fsS --max-time 2 -o /dev/null "http://127.0.0.1:$port/api/healthz" >/dev/null 2>&1; then
      return 0
    fi
    if ! is_alive "$server_pid"; then
      note "server died during startup (log $log)"
      return 1
    fi
    sleep 0.25
    i=$((i + 1))
  done
  return 1
}

stop_server() { # stop_server <pid> — kill, reap, and report whether it survived
  local pid="$1" i=0
  [ -n "$pid" ] || return 0
  if is_alive "$pid"; then
    kill "$pid" 2>/dev/null || true
    while [ "$i" -lt 40 ] && is_alive "$pid"; do
      sleep 0.25
      i=$((i + 1))
    done
  fi
  if is_alive "$pid"; then
    kill -9 "$pid" 2>/dev/null || true
    sleep 0.5
  fi
  wait "$pid" 2>/dev/null || true
  is_alive "$pid" && return 1
  return 0
}

# --- preconditions: CANNOT-ASSESS, never a silent pass -----------------------
command -v python3 >/dev/null 2>&1 || cannot_assess "python3 is not on PATH"
command -v curl >/dev/null 2>&1 || cannot_assess "curl is not on PATH"
[ -f "$root/portal/server/main.py" ] || cannot_assess "no portal/server/main.py under $root"
python3 -c 'import yaml' >/dev/null 2>&1 || cannot_assess "PyYAML is not installed (a console runtime dep)"
python3 -c 'import cryptography' >/dev/null 2>&1 || cannot_assess "cryptography is not installed (a console runtime dep)"
( cd "$root" && python3 -c 'import portal.server.app' >/dev/null 2>&1 ) \
  || cannot_assess "the portal module does not import from $root"

work="$(mktemp -d /tmp/ao-portal-offline-spog.XXXXXX)" || cannot_assess "cannot create a scratch dir"
note "scratch dir $work"

echo "== the doc's own helper: scripts/portal-dev-session.py =="
if ! env -u PORTAL_AUTH_GATE_JWKS -u PORTAL_AUTH_GATE_JWKS_FILE -u ROOT_ADMIN_EMAILS \
    python3 "$root/scripts/portal-dev-session.py" --json --runtime-dir "$work/dev" \
    --port 8787 >"$work/session.json" 2>"$work/session.err"; then
  cannot_assess "the mint helper refused: $(tail -2 "$work/session.err" 2>/dev/null | tr '\n' ' ')"
fi
mapfile -t session_fields < <(python3 -c 'import json, sys; doc = json.load(open(sys.argv[1])); [print(doc[key]) for key in ("runtime_dir", "jwks_file", "registry_file", "state_file", "cookie_name", "email", "validator", "surface")]' "$work/session.json")
if [ "${#session_fields[@]}" -ne 8 ]; then
  cannot_assess "the mint helper's JSON did not carry the 8 expected fields"
fi
runtime_dir="${session_fields[0]}"
jwks_file="${session_fields[1]}"
registry_file="${session_fields[2]}"
state_file="${session_fields[3]}"
cookie_name="${session_fields[4]}"
email="${session_fields[5]}"
validator_summary="${session_fields[6]}"
surface="${session_fields[7]}"
ok "minted a session for $email (cookie $cookie_name) with the mirror self-checked by the repo's validator"
note "validator: $validator_summary"
note "runtime dir $runtime_dir; promoted registry $registry_file"

cookie="$(python3 -c 'import json, sys; doc = json.load(open(sys.argv[1])); print("%s=%s" % (doc["cookie_name"], doc["cookie_value"]))' "$work/session.json")"

#: The overlay path for the OFF/ON phases: a path that does NOT exist, so an
#: overlay left behind in the checkout cannot silently revert the promotion.
clean_state="$work/no-overlay-engaged.json"

# --- phase A: NEGATIVE CONTROL — a fixture registry with the surface forced
# back to the pre-go-live, UNPROMOTED posture (#1043) --------------------------
# NOTE (#1043): issue #1027 (commit 32e8c24) deliberately promoted
# surfaces.fleet_projection/remote_control/operator_terminal for the #607
# go-live, so the COMMITTED registry now ships this surface ON, not off. That
# shipped posture is asserted directly in phase A2 below. This phase keeps the
# OFF/404 half of the matrix alive as a genuine negative control by building a
# scratch, unpromoted COPY of the registry (never mutating the committed file)
# — a fixture that must still read dark.
echo "== phase A: NEGATIVE CONTROL — fixture registry, surfaces.$surface forced unpromoted =="
unpromoted_registry="$work/registry-unpromoted.yaml"
python3 - "$root/infra/feature-flags/registry.yaml" "$unpromoted_registry" "$surface" <<'PY'
import sys
import yaml

source, target, surface = sys.argv[1:4]
doc = yaml.safe_load(open(source, encoding="utf-8").read())
doc["surfaces"] = {name: (dict(entry) if isinstance(entry, dict) else entry) for name, entry in doc["surfaces"].items()}
doc["surfaces"][surface] = dict(doc["surfaces"][surface])
doc["surfaces"][surface]["default"] = "off"
doc["surfaces"][surface]["promoted"] = False
with open(target, "w", encoding="utf-8") as fh:
    yaml.safe_dump(doc, fh, sort_keys=False)
PY
start_server a "$unpromoted_registry" "$clean_state" \
  || cannot_assess "the console did not start with the unpromoted fixture registry"
expect_status "GET /views/fleet.html (flag OFF)" 200 /views/fleet.html
expect_body_has "GET /views/fleet.html (flag OFF)" "Fleet"
expect_status "GET /api/fleet/snapshot (flag OFF)" 404 /api/fleet/snapshot
expect_body_has "GET /api/fleet/snapshot (flag OFF)" "feature_disabled"
expect_status "GET /api/fleet/events?limit=8 (flag OFF)" 404 "/api/fleet/events?limit=8"
expect_body_has "GET /api/fleet/events?limit=8 (flag OFF)" "feature_disabled"
expect_status "GET /api/healthz (public rail)" 200 /api/healthz
if stop_server "$server_pid"; then
  ok "phase A server stopped ($server_pid), no survivor"
else
  bad "phase A server $server_pid survived the kill"
fi
server_pid=""

# --- phase A2: the registry AS COMMITTED — the surface ships promoted ON,
# read-controlled from the first request (#1043: the shipped go-live posture,
# asserted directly against infra/feature-flags/registry.yaml, no fixture) ---
echo "== phase A2: the committed registry (surfaces.$surface promoted on, #1027/#607) =="
start_server a2 "$root/infra/feature-flags/registry.yaml" "$clean_state" \
  || cannot_assess "the console did not start with the committed registry"
expect_status "GET /api/fleet/snapshot (committed, no cookie)" 401 /api/fleet/snapshot
expect_body_has "GET /api/fleet/snapshot (committed, no cookie)" "unauthorized"
expect_status "GET /api/healthz (committed, promoted)" 200 /api/healthz
if stop_server "$server_pid"; then
  ok "phase A2 server stopped ($server_pid), no survivor"
else
  bad "phase A2 server $server_pid survived the kill"
fi
server_pid=""

# --- phase B: the promoted registry + the minted session ---------------------
echo "== phase B: the promoted registry + the minted session =="
start_server b "$registry_file" "$clean_state" \
  || cannot_assess "the console did not start with the promoted registry"
expect_status "GET /api/fleet/snapshot (flag ON, no cookie)" 401 /api/fleet/snapshot
expect_body_has "GET /api/fleet/snapshot (flag ON, no cookie)" "unauthorized"
expect_status "GET /api/fleet/events?limit=8 (flag ON, no cookie)" 401 "/api/fleet/events?limit=8"
# A token minted from a keypair the mirror does NOT carry: the 401 above could
# otherwise be read as "a cookie is missing", when what must be true is that the
# mirror is load-bearing (the kid is the RFC 7638 thumbprint of the signing key).
foreign_cookie="$(python3 - "$root" <<'PY'
import sys
import time

sys.path.insert(0, sys.argv[1])
from identity.sso.jose import generate_rsa_keypair
from identity.sso.tokens import console_kid_for, issue_console_session_token

private_key, public_key = generate_rsa_keypair()
token, _claims = issue_console_session_token(
    private_key,
    kid=console_kid_for(public_key),
    tenant_id="platform",
    subject_id="root@platform.example.com",
    email="root@platform.example.com",
    name="root",
    role="root_admin",
    now=int(time.time()),
    ttl=600,
)
print("os-session-token=" + token)
PY
)"
expect_status "GET /api/fleet/snapshot (flag ON, foreign-key cookie)" 401 /api/fleet/snapshot "$foreign_cookie"
expect_body_has "GET /api/fleet/snapshot (flag ON, foreign-key cookie)" "unauthorized"
expect_status "GET /api/healthz (flag ON)" 200 /api/healthz
expect_status "GET /views/fleet.html (flag ON, cookie)" 200 /views/fleet.html "$cookie"
expect_status "GET /api/fleet/snapshot (flag ON, cookie)" 200 /api/fleet/snapshot "$cookie"
if [ "$status" = "200" ]; then
  python3 - "$body_file" >"$work/snapshot.txt" 2>&1 <<'PY'
import json
import sys

doc = json.load(open(sys.argv[1]))
data = doc.get("data") if isinstance(doc, dict) else None
problems = []
if not isinstance(doc, dict) or doc.get("ok") is not True:
    problems.append("the envelope carries no ok:true")
if not isinstance(data, dict):
    problems.append("the envelope carries no data mapping")
    raise SystemExit("snapshot-shape: " + "; ".join(problems))
rungs = data.get("rungs")
if not isinstance(rungs, dict) or not rungs:
    problems.append("data.rungs is not a non-empty mapping (the projection served nothing)")
else:
    for name, info in sorted(rungs.items()):
        if not isinstance(info, dict) or "state" not in info:
            problems.append("rung %s is not the console's own shape" % name)
if not str(data.get("repo") or "").strip():
    problems.append("data.repo is empty")
if not isinstance(data.get("events"), list):
    problems.append("data.events is not a list")
if problems:
    raise SystemExit("snapshot-shape: " + "; ".join(problems))
print("rungs=%d" % len(rungs))
for name, info in sorted(rungs.items()):
    print("  %s state=%s pid=%s" % (name, info.get("state"), info.get("pid")))
print("repo=%s head=%s" % (data.get("repo"), data.get("head")))
print("claims=%d events=%d" % (len(data.get("claims") or []), len(data.get("events") or [])))
PY
  if [ "$?" -eq 0 ]; then
    ok "the served snapshot IS the console's own projection (not an empty 200)"
    while IFS= read -r line; do note "snapshot $line"; done <"$work/snapshot.txt"
  else
    bad "the served snapshot is not the console's projection: $(tr '\n' ' ' <"$work/snapshot.txt")"
  fi
else
  bad "the snapshot was not served, so its projection cannot be assessed"
fi
expect_status "GET /api/fleet/events?limit=8 (flag ON, cookie)" 200 "/api/fleet/events?limit=8" "$cookie"
if [ "$status" = "200" ]; then
  if python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); raise SystemExit(0 if isinstance(d.get("data"), list) else 1)' "$body_file" >/dev/null 2>&1; then
    ok "the events read carries a JSON list under the console envelope"
  else
    bad "the events read did not carry a JSON list"
  fi
fi

# The push channel the page opens (SSE). It has no end by design, so the client
# MUST time out: curl's 28 is the expected outcome, not a failure — what is
# asserted is that a `snapshot` frame was flushed before the budget ran out.
stream_rc=0
curl -sS -N --max-time "$STREAM_TIMEOUT" -o "$work/stream.body" \
  -H "Cookie: $cookie" "http://127.0.0.1:$port/api/fleet/stream" >/dev/null 2>&1 || stream_rc=$?
if [ -s "$work/stream.body" ]; then
  case "$(cat "$work/stream.body")" in
    *'"rungs"'*) ok "GET /api/fleet/stream flushed a snapshot frame (curl rc=$stream_rc, SSE stays open)" ;;
    *) bad "GET /api/fleet/stream answered with bytes but no snapshot frame" ;;
  esac
else
  bad "GET /api/fleet/stream flushed nothing within ${STREAM_TIMEOUT}s (curl rc=$stream_rc)"
fi
if stop_server "$server_pid"; then
  ok "phase B server stopped ($server_pid), no survivor"
else
  bad "phase B server $server_pid survived the kill"
fi
server_pid=""

# --- phase C: the withdrawal seam, provoked ----------------------------------
echo "== phase C: a runtime rollback takes the promoted surface dark (no commit) =="
cat >"$work/rollback-engaged.json" <<JSON
{
  "schema_version": 1,
  "rollbacks": {
    "$surface": {
      "stage": "off",
      "reason": "provoked by scripts/check-portal-offline-spog.sh",
      "actor": "check-portal-offline-spog",
      "at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    }
  }
}
JSON
start_server c "$registry_file" "$work/rollback-engaged.json" \
  || cannot_assess "the console did not start with the rollback overlay engaged"
expect_status "GET /api/fleet/snapshot (rolled back, cookie)" 404 /api/fleet/snapshot "$cookie"
expect_body_has "GET /api/fleet/snapshot (rolled back, cookie)" "feature_disabled"
expect_status "GET /api/fleet/snapshot (rolled back, no cookie)" 404 /api/fleet/snapshot
expect_status "GET /api/healthz (rolled back)" 200 /api/healthz
if stop_server "$server_pid"; then
  ok "phase C server stopped ($server_pid), no survivor"
else
  bad "phase C server $server_pid survived the kill"
fi
server_pid=""

# --- survivors: no process and no port left behind ---------------------------
echo "== no process, no port left behind =="
survivors=0
for pid in "${started_pids[@]}"; do
  if is_alive "$pid"; then
    bad "process $pid is still alive"
    survivors=$((survivors + 1))
  fi
done
if [ "$survivors" -eq 0 ]; then
  ok "${#started_pids[@]}/${#started_pids[@]} server processes are gone (no signal-by-name: only these pids)"
fi
open_ports=0
for used in "${started_ports[@]}"; do
  if ! port_closed "$used"; then
    bad "port 127.0.0.1:$used still answers after the kill"
    open_ports=$((open_ports + 1))
  fi
done
if [ "$open_ports" -eq 0 ]; then
  ok "${#started_ports[@]}/${#started_ports[@]} loopback ports are closed"
fi

# --- verdict -----------------------------------------------------------------
if [ "$fail" -ne 0 ]; then
  printf 'portal-offline-spog: FAIL — %s check(s) failed, %s passed\n' "$fail" "$pass" >&2
  exit 1
fi
printf 'portal-offline-spog: PASS — %s check(s): the documented SPoG run reproduces offline (flag OFF 404, no-cookie 401, cookie 200 with real data, rollback 404)\n' "$pass"
exit 0
