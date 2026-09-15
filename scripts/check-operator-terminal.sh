#!/usr/bin/env bash
# check-operator-terminal.sh — the operator terminal (issue #774).
#
# THE DEFECT THIS EXISTS FOR
#   The operator's end goal is one link — `https://ai.purebliss.app` → Google
#   OAuth → the live fleet terminal. The SSO half existed and was measured; the
#   terminal half did not. A browser view that re-invented the projection, the
#   control path, or (worst) a login of its own would have been a third surface
#   that disagreed with the two that already work.
#
# WHAT IS MEASURED (each load-bearing property, each provoked, not asserted)
#   (a) unauthenticated /console -> /auth/login          (the one link, no login
#       of its own; it reuses the session)
#   (b) authenticated /console -> the view                (302 to the view, then
#       the view is served)
#   (c) flag OFF -> 404 feature_disabled, BEFORE AuthN    (an unpromoted surface
#       is absent, not merely unauthorised) — MUTATION-PROVED: flipping the flag
#       to "on" must make /console reachable, so a gate that always 404s (or a
#       route that ignores the flag) fails by name.
#   (d) the view renders its projection/steer from fixtures with NO network:
#       the read half is the fleet projection served offline from a redirected
#       runtime, and the steer half is the closed control vocabulary served
#       offline — and the view + script reference exactly those endpoints.
#   (e) a steer action is refused without the required capability (no audit
#       written) and every allowed action lands on the audit rail; an
#       out-of-vocabulary action is refused 422, never executed.
#
# VACUITY: every probe is paired with a control one flag (or one principal) away,
# so a probe that always refuses fails just as loudly as a surface that never
# refuses (GR-12 / AO-GR-4).
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# Usage: bash scripts/check-operator-terminal.sh
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-operator-terminal: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

fail=0
note() { printf '  OK    %s\n' "$1"; }
problem() { printf '  FAIL  %s\n' "$1" >&2; fail=$((fail + 1)); }

# ── the surface's files ────────────────────────────────────────────────────
for required in \
  portal/static/views/console.html \
  portal/static/js/operator.js \
  scripts/check-operator-terminal.sh
do
  if [ -f "$required" ]; then
    note "$required present"
  else
    problem "$required is missing"
  fi
done
if [ "$fail" -gt 0 ]; then
  echo "check-operator-terminal: FAIL ($fail missing file(s))" >&2
  exit 1
fi

# ── the declaration + the provoked probes (one offline harness) ────────────
echo "== the flag declaration + the provoked refusals (and their controls) =="
AO_CHECK_ROOT="$root" python3 - <<'PY'
"""Drive the real console offline and print one OK/FAIL line per probe.

The harness imports the tree under test with bytecode writing disabled and any
stale ``__pycache__`` purged first: a cached module from an earlier run can make
a mutation invisible (and a gate that reads a stale copy proves nothing). The
repository root arrives in ``AO_CHECK_ROOT``.
"""
import json
import os
import shutil
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True

ROOT = Path(os.environ["AO_CHECK_ROOT"]).resolve()
for module in (ROOT / "portal").rglob("*.py"):
    shutil.rmtree(module.parent / "__pycache__", ignore_errors=True)
# NOTE: only the repo root goes on sys.path up front. `fleet/` is added later,
# right before `import channel`, because `fleet/telemetry.py` shadows the
# `telemetry/` namespace package when `fleet/` precedes the root on the path —
# which turns `telemetry.metering` into "not a package" (measured). The portal
# server imports must therefore run first, so the real `telemetry` is already
# cached in sys.modules before `fleet/` is ever importable.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import yaml

from identity.sso.jose import generate_rsa_keypair, jwks_for_keys
from identity.sso.tokens import console_kid_for, issue_console_session_token
from portal.server import control_api
from portal.server.app import build_app
from portal.server.control_api import LeverResult, RemoteControl, Vocabulary, install
from portal.server.fleet import FleetProjection, load_fleet_console, read_surface_default
from portal.server.sso import AUTH_GATE_LOGIN_PATH, SESSION_COOKIE, ConsoleSso

problems = []


def ok(label):
    print(f"  OK    {label}")


def fail(label):
    print(f"  FAIL  {label}")
    problems.append(label)


# -- an offline auth gate (mints genuine os-session-token) -------------------
private_key, public_key = generate_rsa_keypair()
kid = console_kid_for(public_key)
jwks = jwks_for_keys([(kid, public_key)])
ROOT_ADMIN = ("root@platform.example.com",)
SCOPED_EMAIL = "alice@acme.example.com"


def sso():
    return ConsoleSso(jwks=jwks, root_admin_emails=ROOT_ADMIN)


def mint(email, tenant_id):
    token, _ = issue_console_session_token(
        private_key, kid=kid, tenant_id=tenant_id, subject_id=email,
        email=email, name=email.split("@", 1)[0], role="user",
        now=int(time.time()), ttl=3600,
    )
    return token


def request(app, method, path, cookies=None):
    response = app.handle(method, path, cookies=cookies or {})
    headers = dict(response.headers)
    location = headers.get("Location", "")
    return response.status, response.payload, location


# -- the flag declaration ----------------------------------------------------
registry = yaml.safe_load(
    (ROOT / "infra" / "feature-flags" / "registry.yaml").read_text(encoding="utf-8")
)
entry = (registry.get("surfaces") or {}).get("operator_terminal")
if not isinstance(entry, dict):
    fail("infra/feature-flags/registry.yaml declares no surfaces.operator_terminal")
else:
    if entry.get("default") not in (False, "off"):
        fail(f"surfaces.operator_terminal.default is {entry.get('default')!r}, expected off")
    if entry.get("promoted"):
        fail("surfaces.operator_terminal.promoted must be false while it ships off")
    if entry.get("service") != "portal":
        fail("surfaces.operator_terminal.service must be portal")
    if not entry.get("tf_flag"):
        fail("surfaces.operator_terminal declares no tf_flag")

# fail-closed reader + the mutation that proves it is real
declared = read_surface_default(ROOT, surface="operator_terminal")
if declared == "off":
    ok("the fail-closed reader resolves surfaces.operator_terminal to 'off'")
else:
    fail(f"read_surface_default resolved operator_terminal to {declared!r}, expected off")

# mutation: flip the flag on in a scratch registry copy -> the reader must say on
scratch = Path("/tmp") / f"ao774-reg-on.{os.getpid()}.yaml"
mutated = registry
mutated["surfaces"] = dict(registry["surfaces"])
mutated["surfaces"]["operator_terminal"] = dict(entry)
mutated["surfaces"]["operator_terminal"]["default"] = "on"
scratch.write_text(yaml.safe_dump(mutated), encoding="utf-8")
flipped = read_surface_default(ROOT, registry_path=scratch, surface="operator_terminal")
scratch.unlink(missing_ok=True)
if flipped == "on":
    ok("MUTATION: flipping the flag to 'on' makes the reader say 'on' (the gate is the flag, not a hardcoded 404)")
else:
    fail(f"MUTATION: flipping the flag did not change the reader ({flipped!r}) — the 404 is a formality")

# -- (c) flag OFF -> 404 feature_disabled, before AuthN ---------------------
off_app = build_app(sso=sso(), operator_terminal_enabled=False)
status, payload, _ = request(off_app, "GET", "/console", {})
if status == 404 and payload.get("error", {}).get("code") == "feature_disabled":
    ok("flag OFF: /console -> 404 feature_disabled (no cookie, before AuthN)")
else:
    fail(f"flag OFF: /console -> {status} {payload!r}, expected 404 feature_disabled")

status, payload, _ = request(off_app, "GET", "/views/console.html", {})
if status == 404 and payload.get("error", {}).get("code") == "feature_disabled":
    ok("flag OFF: /views/console.html -> 404 feature_disabled")
else:
    fail(f"flag OFF: /views/console.html -> {status}, expected 404 feature_disabled")

status, payload, _ = request(off_app, "GET", "/js/operator.js", {})
if status == 404 and payload.get("error", {}).get("code") == "feature_disabled":
    ok("flag OFF: /js/operator.js -> 404 feature_disabled")
else:
    fail(f"flag OFF: /js/operator.js -> {status}, expected 404 feature_disabled")

# -- (a) unauthenticated /console -> /auth/login -----------------------------
on_app = build_app(sso=sso(), operator_terminal_enabled=True)
status, payload, location = request(on_app, "GET", "/console", {})
if status == 302 and location == AUTH_GATE_LOGIN_PATH:
    ok("unauthenticated /console -> 302 /auth/login (the one link reuses the session)")
else:
    fail(f"unauthenticated /console -> {status} {location}, expected 302 {AUTH_GATE_LOGIN_PATH}")

# -- (b) authenticated /console -> the view ---------------------------------
cookie = {SESSION_COOKIE: mint(ROOT_ADMIN[0], "acme")}
status, payload, location = request(on_app, "GET", "/console", cookie)
if status == 302 and location == "/views/console.html":
    ok("authenticated /console -> 302 /views/console.html")
else:
    fail(f"authenticated /console -> {status} {location}, expected 302 /views/console.html")

status, payload, _ = request(on_app, "GET", "/views/console.html", cookie)
if status == 200 and isinstance(payload, bytes) and b"<html" in payload:
    ok("authenticated /views/console.html -> 200 (the view is served)")
else:
    fail(f"authenticated /views/console.html -> {status}, expected 200 html")

# a forged/absent token must still reach the gate (fail closed)
status, payload, location = request(on_app, "GET", "/console", {SESSION_COOKIE: "forged"})
if status == 302 and location == AUTH_GATE_LOGIN_PATH:
    ok("a forged session token -> 302 /auth/login (fail closed)")
else:
    fail(f"a forged session token -> {status}, expected 302 /auth/login")

# -- (d) the view renders its projection from fixtures with NO network -------
# `fleet/` goes on the path only now, after `portal.server.app` (and therefore
# `telemetry.metering`) is already imported and cached — see the NOTE above.
if str(ROOT / "fleet") not in sys.path:
    sys.path.insert(0, str(ROOT / "fleet"))
import channel  # noqa: E402  (fleet/channel.py — the runtime path source)

tmp = Path("/tmp") / f"ao774-fleet.{os.getpid()}"
shutil.rmtree(tmp, ignore_errors=True)
tmp.mkdir(parents=True, exist_ok=True)
console = load_fleet_console(ROOT)
channel.SLOG = tmp / "slog.jsonl"
channel.HEARTBEAT = tmp / "sister.heartbeat.json"
channel.BRAIN_HEARTBEAT = tmp / "brain.heartbeat.json"
channel.BRAIN_INBOX = tmp / "brain" / "inbox"
channel.BRAIN_SENT = tmp / "brain" / "sent"
channel.BRAIN_OUTBOX = tmp / "brain" / "outbox"
console.FLEET_DIR = tmp
channel.head_commit = lambda: "testsha"
console.loop_pid = lambda pattern: None
console.claims_snapshot = lambda: []
console.closed_issues = lambda *args, **kwargs: set()

fleet_projection = FleetProjection(repo_root=ROOT, console=console, enabled=True)
read_app = build_app(sso=sso(), fleet_projection=fleet_projection, operator_terminal_enabled=True)
status, payload, _ = request(read_app, "GET", "/api/fleet/snapshot", cookie)
if status == 200 and isinstance(payload.get("data"), dict):
    keys = set(payload["data"])
    if {"repo", "rungs", "waves", "claims", "dispatches"} <= keys:
        ok("the projection serves the view's read half offline (snapshot from a redirected runtime, no network)")
    else:
        fail(f"snapshot missing keys: {sorted({'repo','rungs','waves','claims','dispatches'} - keys)}")
else:
    fail(f"/api/fleet/snapshot -> {status}, expected 200 projection")
shutil.rmtree(tmp, ignore_errors=True)

# -- (d)/(e) the steer half is the closed vocabulary, offline -----------------
class RecordingLever:
    def __init__(self):
        self.calls = []

    def run(self, row, args):
        self.calls.append((row.lever, tuple(args)))
        return LeverResult(argv=(row.source, row.local, *args), exit_code=0, stdout="", stderr="")


class SpyLedger:
    def __init__(self):
        self.begun, self.finished, self.ended = [], [], []

    def begin(self, command):
        self.begun.append(command.verb)
        return None

    def finish(self, command, record):
        self.finished.append(command.verb)

    def end(self, command):
        self.ended.append(command.verb)


lever = RecordingLever()
ledger = SpyLedger()
steer_app = build_app(sso=sso(), operator_terminal_enabled=True)
install(steer_app, RemoteControl(app=steer_app, enabled=True, lever=lever, commands=ledger))

# the vocabulary the steer panel lists (served offline, no lever process)
status, payload, _ = request(steer_app, "POST", "/api/control/fleet/verbs", cookie)
if status == 200 and isinstance(payload.get("data", {}).get("content"), dict):
    served = {row["id"] for row in payload["data"]["content"]["verbs"]}
    declared_set = set(Vocabulary.load(ROOT / "control-plane" / "control" / "verbs.yaml").verbs)
    if served == declared_set:
        ok("the steer panel lists the closed vocabulary (served offline, == the registry's declared set)")
    else:
        fail("the served vocabulary disagrees with the registry's declared set")
else:
    fail(f"POST /api/control/fleet/verbs -> {status}, expected the vocabulary")

# an out-of-vocabulary action is refused, never executed
status, payload, _ = request(steer_app, "POST", "/api/control/fleet/frobnicate", cookie)
if status == 422 and payload.get("error", {}).get("code") == "unknown_verb":
    ok("an out-of-vocabulary steer -> 422 unknown_verb (never executed)")
else:
    fail(f"out-of-vocabulary steer -> {status}, expected 422 unknown_verb")

# refused without the required capability, and nothing is audited
scoped_cookie = {SESSION_COOKIE: mint(SCOPED_EMAIL, "acme")}
before = list(ledger.finished)
status, payload, _ = request(steer_app, "POST", "/api/control/fleet/pause", scoped_cookie)
if status == 403 and payload.get("error", {}).get("code") in ("permission_denied", "scope_denied"):
    ok("a caller without the control capability is refused 403 (scope/permission)")
else:
    fail(f"capability-less steer -> {status}, expected 403")
if ledger.finished == before:
    ok("the refused steer wrote nothing on the audit rail")
else:
    fail("the refused steer wrote an audit record")

# allowed -> delivered through the lever AND audited
status, payload, _ = request(steer_app, "POST", "/api/control/fleet/pause", cookie)
if status == 200 and "fleet.pause" in ledger.finished and "fleet.pause" in ledger.begun and "fleet.pause" in ledger.ended:
    ok("an allowed steer is delivered through the lever and lands on the audit rail")
else:
    fail(f"allowed steer -> {status}, expected 200 + a full audit record")

# -- the view wires to exactly those endpoints -------------------------------
html = (ROOT / "portal" / "static" / "views" / "console.html").read_text(encoding="utf-8")
js = (ROOT / "portal" / "static" / "js" / "operator.js").read_text(encoding="utf-8")
if "/api/fleet/snapshot" in js and "/api/control/fleet/verbs" in js and 'src="/js/operator.js"' in html:
    ok("the view + script reference the real read/steer endpoints (no re-invention)")
else:
    fail("the view/script do not reference the real read/steer endpoints")

if "window.OT" in js:
    ok("the pure model is exposed (window.OT) so the view is testable offline")
else:
    fail("the pure model is not exposed for offline testing")

print()
if problems:
    print(f"check-operator-terminal: FAIL ({len(problems)} problem(s))")
    sys.exit(1)
print("check-operator-terminal: OK")
PY
rc=$?

if [ "$rc" -eq 0 ]; then
  echo "check-operator-terminal: OK"
  exit 0
else
  echo "check-operator-terminal: FAIL" >&2
  exit 1
fi
