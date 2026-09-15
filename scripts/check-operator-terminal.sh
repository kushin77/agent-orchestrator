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
#   (h) the rollout + rollback bar (issue #802), on the surface's OWN readiness
#       signal: the committed surface reads 'off' and is NOT named on the
#       readiness rail; a promoted fixture (built from the committed registry)
#       reads 'ready', is named, and serves; a missing served artifact makes it
#       'not-ready' and the anchor WITHDRAWS it — flag rolled to OFF through the
#       rollout engine (audited, chain verified) and the runtime overlay engaged
#       — so a freshly resolved console answers 404 while the registry still
#       declares the surface on. Controls: a healthy reading must NOT withdraw
#       it, an unreadable declaration must withdraw nothing and answer 503 (never
#       'ready'), and clearing the rollback must re-promote. Two mutations prove
#       the probes can fail: neutering the overlay writer leaves the surface
#       serving after a 'rollback' (302), and neutering the steer path's
#       _require_capability lets a capability-less caller through (200).
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
from infra.rollout import surface_guard
from infra.rollout.checks.check_rollout import check_all as check_rollout_all
from portal.server import control_api
from portal.server import surface_state
from portal.server.app import build_app
from portal.server.control_api import LeverResult, RemoteControl, Vocabulary, install
from portal.server.fleet import FleetProjection, load_fleet_console, read_surface_default
from portal.server.sso import AUTH_GATE_LOGIN_PATH, SESSION_COOKIE, ConsoleSso
from portal.server.surface_health import (
    SURFACE_CANNOT_ASSESS,
    SURFACE_NOT_READY,
    SURFACE_OFF,
    SURFACE_READY,
    readiness,
)

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

# -- (h) rollout + rollback: the surface's OWN readiness decides (issue #802) --
#
# The enterprise bar asks for a rollout AND a rollback, and the console surface
# had neither: it was declared, promoted by hand, and withdrawable only by hand.
# Here the surface's readiness signal is the health check, the anchor rolls it
# back on a failed reading, and the rollback is proven at BOTH layers.
#
# Vacuity: every probe is paired with a control one reading (or one input) away.
# The promoted fixture is built FROM the committed declaration, the rollback is
# driven through the REAL app, and the runtime-half writer is then neutered to
# prove the acceptance probe can fail.
starting_env = {name: os.environ.get(name) for name in ("AO_SURFACE_REGISTRY", "AO_SURFACE_STATE")}
work = Path("/tmp") / f"ao802-rollback.{os.getpid()}.{int(time.time() * 1000)}"
shutil.rmtree(work, ignore_errors=True)
work.mkdir(parents=True, exist_ok=True)


def restore_env():
    for name, value in starting_env.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value
    shutil.rmtree(work, ignore_errors=True)


# (h0) the declaration the anchor drives is real. Without the rollout-state row
# the engine refuses the flag BY NAME, so the anchor would have nothing to drive
# — the inert-control failure this repo keeps finding.
rollout_errors = check_rollout_all()
rollout_state_doc = yaml.safe_load(
    (ROOT / "infra" / "rollout" / "rollout-state.yaml").read_text(encoding="utf-8")
)
row = (rollout_state_doc.get("flags") or {}).get("surfaces.operator_terminal")
if row and row.get("stage") == "off" and not rollout_errors:
    ok("the rollout state carries surfaces.operator_terminal (off) and check_rollout validates the declarations")
else:
    fail(f"the rollout declaration is not sound: row={row!r} errors={rollout_errors[:2]}")

# (h1) the committed tree: unpromoted, and the readiness rail does NOT name it —
# an unpromoted surface is absent, not merely unauthorised.
committed = readiness(ROOT, "operator_terminal")
if committed.state == SURFACE_OFF and not committed.promoted:
    ok("readiness: the committed surface reads 'off' (unpromoted — nothing to serve)")
else:
    fail(f"readiness on the committed tree -> {committed.state}")

status, payload, _ = request(on_app, "GET", "/api/healthz/ready", {})
data = payload.get("data") if isinstance(payload, dict) else None
if status == 200 and isinstance(data, dict) and data.get("surfaces") == {}:
    ok("GET /api/healthz/ready: the unpromoted surface is NOT named (absent, not merely unauthorised)")
else:
    fail(f"/api/healthz/ready (unpromoted) -> {status} {payload!r}")

# (h2) a PROMOTED fixture built from the committed declaration: ready, named, and
# really served — the control every 404 below depends on.
registry_doc = yaml.safe_load(
    (ROOT / "infra" / "feature-flags" / "registry.yaml").read_text(encoding="utf-8")
)
registry_doc["surfaces"] = dict(registry_doc["surfaces"])
registry_doc["surfaces"]["operator_terminal"] = dict(registry_doc["surfaces"]["operator_terminal"])
registry_doc["surfaces"]["operator_terminal"]["default"] = "on"
promoted_registry = work / "registry-promoted.yaml"
promoted_registry.write_text(yaml.safe_dump(registry_doc, sort_keys=False), encoding="utf-8")

overlay = work / "surface-state.json"
broken_static = work / "static"
shutil.copytree(ROOT / "portal" / "static", broken_static)
(broken_static / "views" / "console.html").unlink()

os.environ["AO_SURFACE_REGISTRY"] = str(promoted_registry)
os.environ["AO_SURFACE_STATE"] = str(overlay)

status, payload, location = request(build_app(sso=sso()), "GET", "/console", cookie)
if status == 302 and location == "/views/console.html":
    ok("the promoted fixture really serves /console (302 to the view)")
else:
    fail(f"the promoted fixture -> {status} {location}, expected 302 /views/console.html")

promoted = readiness(ROOT, "operator_terminal")
if promoted.state == SURFACE_READY:
    ok(f"readiness: promoted + intact dependencies -> ready (composes {dict(promoted.dependencies)})")
else:
    fail(f"readiness (promoted) -> {promoted.state} ({promoted.detail})")

status, payload, _ = request(build_app(sso=sso()), "GET", "/api/healthz/ready", {})
named = ((payload.get("data") or {}).get("surfaces") or {}) if isinstance(payload, dict) else {}
if status == 200 and named.get("operator_terminal", {}).get("state") == SURFACE_READY:
    ok("GET /api/healthz/ready names the promoted surface and its state (the existing health rail, no second dashboard)")
else:
    fail(f"/api/healthz/ready (promoted) -> {status} {payload!r}")

# (h3) PROVOKE: the view the surface redirects to is gone. The readiness signal
# must say so (never 'ready'), and the anchor must withdraw the surface.
provoked = readiness(ROOT, "operator_terminal", static_dir=broken_static)
if provoked.state == SURFACE_NOT_READY and any("console.html" in m for m in provoked.missing):
    ok(f"readiness: a missing served artifact -> {provoked.state}, naming {provoked.missing[0]}")
else:
    fail(f"readiness (broken artifact) -> {provoked.state} missing={provoked.missing}")

outcome = surface_guard.reconcile(
    ROOT, "operator_terminal", static_dir=broken_static, audit_path=work / "audit.jsonl"
)
rollbacks = [record for record in outcome.audit_records if record.get("action") == "rollback"]
if outcome.action == "rolled-back" and outcome.exit_code == 1 and rollbacks and outcome.audit_verified:
    ok(f"the anchor acted on the failed reading and AUDITED it: #{rollbacks[0]['seq']} rollback "
       f"{rollbacks[0]['from_stage']}->{rollbacks[0]['to_stage']}, chain verified, actor={rollbacks[0]['actor']}")
else:
    fail(f"the anchor -> {outcome.action} rc={outcome.exit_code} audit={outcome.audit_records!r}")

if outcome.flag_action == "rolled-to-off" and outcome.exposure_stage == "canary":
    ok("the engine withdrew the flag it was exposed at (the committed rollout row cannot carry an "
       "exposure, so the declared exposure is applied in memory before the observation)")
else:
    fail(f"the flag was not withdrawn through the engine: {outcome.flag_action}/{outcome.exposure_stage}")

if surface_state.is_rolled_back(ROOT, "operator_terminal"):
    ok("the runtime rollback overlay is engaged")
else:
    fail("the runtime overlay was not engaged")

# THE acceptance: the surface RETURNS TO OFF. The declaration still says on, and
# a freshly resolved console refuses it.
status, payload, _ = request(build_app(sso=sso()), "GET", "/console", cookie)
if status == 404 and payload.get("error", {}).get("code") == "feature_disabled":
    ok("the surface RETURNED TO OFF: /console -> 404 feature_disabled while the registry still declares it on")
else:
    fail(f"after the rollback /console -> {status} {payload!r}, expected 404 feature_disabled")

if read_surface_default(ROOT, surface="operator_terminal") == "off":
    ok("read_surface_default resolves 'off': the rollback overlay wins over the declaration")
else:
    fail("read_surface_default still resolves the rolled-back surface as on")

status, payload, _ = request(build_app(sso=sso()), "GET", "/api/healthz/ready", {})
state = (((payload.get("data") or {}).get("surfaces") or {}).get("operator_terminal")
         if isinstance(payload, dict) else None) or {}
if status == 200 and state.get("state") == SURFACE_OFF and (state.get("rolledBack") or {}).get("reason"):
    ok(f"the readiness rail reports the withdrawal and its reason: "
       f"{state['rolledBack']['reason'][:64]}...")
else:
    fail(f"/api/healthz/ready (rolled back) -> {status} {state!r}")

# (h4) CONTROL: a healthy reading must NOT withdraw the surface — a guard that
# always rolls back fails here.
os.environ["AO_SURFACE_STATE"] = str(work / "surface-state-control.json")
healthy = surface_guard.reconcile(
    ROOT, "operator_terminal", audit_path=work / "audit-control.jsonl"
)
status, payload, location = request(build_app(sso=sso()), "GET", "/console", cookie)
if (healthy.action == "healthy"
        and not surface_state.is_rolled_back(ROOT, "operator_terminal")
        and status == 302):
    ok("CONTROL: a healthy reading leaves the surface promoted and serving (no vacuous rollback)")
else:
    fail(f"CONTROL -> {healthy.action} engaged="
         f"{surface_state.is_rolled_back(ROOT, 'operator_terminal')} /console={status}")

# (h5) CONTROL: an unreadable reading rolls NOTHING back and is never reported
# ready. A malformed declaration is exactly the "cannot tell" case.
os.environ["AO_SURFACE_REGISTRY"] = str(work / "registry-broken.yaml")
(work / "registry-broken.yaml").write_text("surfaces: [not a mapping\n", encoding="utf-8")
os.environ["AO_SURFACE_STATE"] = str(work / "surface-state-ca.json")
cannot = surface_guard.reconcile(
    ROOT, "operator_terminal", audit_path=work / "audit-ca.jsonl"
)
unreadable = readiness(ROOT, "operator_terminal")
status, payload, _ = request(build_app(sso=sso()), "GET", "/api/healthz/ready", {})
if (cannot.action == "cannot-assess" and cannot.exit_code == 2
        and unreadable.state == SURFACE_CANNOT_ASSESS and status == 503
        and not (work / "surface-state-ca.json").exists()):
    ok("an unreadable declaration rolls nothing back (cannot-assess, rc 2, no overlay) and the rail "
       "answers 503 — never 'ready'")
else:
    fail(f"cannot-assess -> {cannot.action} rc={cannot.exit_code} state={unreadable.state} http={status}")

# (h6) reversible: the rollback is a rollback, not a one-way kill.
os.environ["AO_SURFACE_REGISTRY"] = str(promoted_registry)
os.environ["AO_SURFACE_STATE"] = str(overlay)
if surface_guard.clear(ROOT, "operator_terminal"):
    status, payload, location = request(build_app(sso=sso()), "GET", "/console", cookie)
    if status == 302 and location == "/views/console.html":
        ok("CONTROL: clearing the rollback re-promotes the surface (the round trip is complete)")
    else:
        fail(f"after --clear /console -> {status} {location}, expected 302")
else:
    fail("the engaged rollback could not be cleared")

# (h7) MUTATION: neuter the runtime-half writer and the SAME probe must stop
# working. Without it the acceptance probe could pass on a rollback that never
# happened, which is the formality GR-12 forbids.
os.environ["AO_SURFACE_STATE"] = str(work / "surface-state-mutant.json")
original_record = surface_state.record_rollback
surface_state.record_rollback = lambda *args, **kwargs: Path("/dev/null")
try:
    mutant = surface_guard.reconcile(
        ROOT, "operator_terminal", static_dir=broken_static,
        audit_path=work / "audit-mutant.jsonl",
    )
    mutant_status, _, _ = request(build_app(sso=sso()), "GET", "/console", cookie)
finally:
    surface_state.record_rollback = original_record
os.environ["AO_SURFACE_STATE"] = str(work / "surface-state-restored.json")
surface_guard.reconcile(
    ROOT, "operator_terminal", static_dir=broken_static,
    audit_path=work / "audit-restored.jsonl",
)
restored_status, _, _ = request(build_app(sso=sso()), "GET", "/console", cookie)
if mutant.action == "rolled-back" and mutant_status == 302 and restored_status == 404:
    ok("MUTATION: with the overlay writer neutered the surface KEEPS SERVING after a 'rollback' "
       "(302), and restoring it takes it dark again (404)")
else:
    fail(f"MUTATION: mutant={mutant.action}/{mutant_status} restored={restored_status}")

# (h8) the capability check on the steer path, MUTATION-PROVED: the guard that
# refuses a capability-less caller is neutered IN PROCESS and the SAME probe must
# stop refusing — so the 403 above is the guard's doing and not a route that
# 403s everything — then it is restored and the refusal must return.
original_require = RemoteControl.__dict__["_require_capability"]
try:
    RemoteControl._require_capability = lambda self, principal, row: None
    mutant_steer, _, _ = request(steer_app, "POST", "/api/control/fleet/pause", scoped_cookie)
finally:
    RemoteControl._require_capability = original_require
restored_steer, _, _ = request(steer_app, "POST", "/api/control/fleet/pause", scoped_cookie)
if mutant_steer == 200 and restored_steer == 403:
    ok("MUTATION: neutering _require_capability lets the capability-less caller through (200), "
       "and restoring it refuses again (403) — the 403 is the capability check, not the route")
else:
    fail(f"MUTATION: capability mutant={mutant_steer} restored={restored_steer}, expected 200 then 403")

restore_env()

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
