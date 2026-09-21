#!/usr/bin/env bash
# check-erp-portal.sh — the ERP module's portal surface (ERP-07, issue #652).
#
# THE DEFECT THIS EXISTS FOR
#   The ERP module is indexer-fed: ERP-01 declares it, ERP-02 owns the document
#   model, ERP-06 serves it as a REST contract. A portal half is where that
#   property is easiest to lose — a dashboard that keeps its own family list, a
#   form that hard-codes its fields, or a "module" that renders while its flag is
#   off and merely hides its data behind a login. All three read green in a test
#   that never asks the flag or the contract.
#
# WHAT IS MEASURED (each load-bearing property, each provoked)
#   (a) THE FLAG IS DECLARED, OFF, and in lock-step across the files that have to
#       agree: the portal's own `surfaces.erp_module` (the runtime switch), the
#       central promotion rows `services.erp_module` + `surfaces.erp_module`, and
#       `enable_erp_module` in infra/terraform/variables.tf — plus the
#       rollout-state rows that make the surface promotable *and withdrawable*.
#       The module's own manifest says the central row lands with this surface,
#       so its absence is a finding rather than an omission.
#   (b) THE READER FAILS CLOSED, and the scan is a mutation: flipping `default`
#       to "on" in a scratch copy must flip the reader, so a reader that always
#       answered "off" fails here. Also measured: the malformed-YAML case, which
#       this issue found escaping the reader (`yaml.YAMLError` is not a
#       `ValueError`) and turning a dark surface into a crash.
#   (c) FLAG OFF -> the whole surface is ABSENT, checked before AuthN: every API
#       route and every one of the module's own documents answers
#       404 feature_disabled with no cookie AND with a valid session — while an
#       unrelated console document is still served, so the gate withholds the
#       module and not the console. The control is one flag away: the same routes
#       promoted answer 401 unauthenticated and 200 authenticated.
#   (d) THE MODULE READS ITS KNOWLEDGE. The dashboard's families are the served
#       contract's own `kinds` in its own order, every figure is ERP-06's own
#       collection answer for this principal, a declared move succeeds while the
#       same move twice is refused by name, and a refused write reaches the
#       caller with the model's own code. Mutating the *manifest* in a scratch
#       tree must change the served declaration — a projection carrying its own
#       copy would not notice.
#   (e) THE MUTANT, on a copied tree. The copy is proved GREEN under the same
#       driver first, because a red mutant on an unproven copy proves nothing;
#       then the module's flag gate is neutered in the copy and the driver must
#       go red on that probe — and on no unrelated one.
#
# VACUITY: every refusal is paired with the answer one flag (or one family) away,
# so a probe that always refuses fails as loudly as a surface that never refuses
# (GR-12 / AO-GR-4).
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# Usage: bash scripts/check-erp-portal.sh
set -u

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"
cd "$root" || exit 2

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-erp-portal: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

fail=0
cannot=0
note() { printf '  OK    %s\n' "$1"; }
problem() { printf '  FAIL  %s\n' "$1" >&2; fail=$((fail + 1)); }

# ── the surface's files ────────────────────────────────────────────────────
echo "== the surface's files =="
for required in \
  portal/server/erp.py \
  portal/server/config_flags.py \
  portal/config/feature-flags.yaml \
  portal/static/erp/module.html \
  portal/static/erp/module.js \
  portal/static/erp/module.css \
  portal/tests/test_erp_module_surface.py
do
  if [ -f "$required" ]; then
    note "$required present"
  else
    problem "$required is missing"
  fi
done
if [ "$fail" -ne 0 ]; then
  echo "erp-portal: FAIL — the surface is incomplete" >&2
  exit 1
fi

# ── the driver ─────────────────────────────────────────────────────────────
# One driver, run twice: once over the real tree (the evidence) and once over a
# copied tree before and after the mutation. It takes the repository root as its
# only argument, so "which tree am I measuring" is never implicit.
driver="$(mktemp /tmp/ao652-driver.XXXXXX.py)"
trap 'rm -f "$driver"' EXIT
cat > "$driver" <<'PY_DRIVER'
"""Drive the console surface offline and print one OK/FAIL line per probe.

``sys.argv[1]`` is the repository root under test. Bytecode writing is disabled
and stale ``__pycache__`` is purged first: a cached module from an earlier run
can make a mutation invisible, and a gate that reads a stale copy proves nothing.
"""
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.dont_write_bytecode = True

ROOT = Path(sys.argv[1]).resolve()
for tree in ("portal", "integrations"):
    for cache in (ROOT / tree).rglob("__pycache__"):
        shutil.rmtree(cache, ignore_errors=True)
sys.path.insert(0, str(ROOT))

import yaml

from identity.sso.jose import generate_rsa_keypair, jwks_for_keys
from identity.sso.tokens import console_kid_for, issue_console_session_token
from portal.server.app import build_app
from portal.server.config_flags import ERP_MODULE_SURFACE, surface_enabled
from portal.server.erp import ErpModuleSurface, build_erp_api
from portal.server.sso import SESSION_COOKIE, ConsoleSso

problems = []


def ok(label):
    print(f"  OK    {label}")


def fail(label):
    print(f"  FAIL  {label}")
    problems.append(label)


# The isolation proof: every module this run measures must come from the tree
# named on the command line. Without it a mutant could be measured against the
# real tree's code and read green.
import portal.server.app as app_module  # noqa: E402

if not str(Path(app_module.__file__).resolve()).startswith(str(ROOT)):
    fail(f"the driver is reading {app_module.__file__}, not the tree under test ({ROOT})")
    print("erp-portal-driver: FAIL")
    raise SystemExit(1)

# -- an offline auth gate (mints a genuine os-session-token) ---------------
private_key, public_key = generate_rsa_keypair()
kid = console_kid_for(public_key)
jwks = jwks_for_keys([(kid, public_key)])
ROOT_ADMIN = ("root@platform.example.com",)


def sso():
    return ConsoleSso(jwks=jwks, root_admin_emails=ROOT_ADMIN)


def mint(email, tenant_id):
    token, _ = issue_console_session_token(
        private_key, kid=kid, tenant_id=tenant_id, subject_id=email,
        email=email, name=email.split("@", 1)[0], role="user",
        now=int(time.time()), ttl=3600,
    )
    return token


def surface(enabled):
    return ErpModuleSurface(repo_root=ROOT, enabled=enabled, api=build_erp_api(ROOT))


def request(app, method, path, *, cookie=None, body=None):
    cookies = {SESSION_COOKIE: cookie} if cookie else {}
    response = app.handle(method, path, body=body or {}, cookies=cookies)
    payload = response.payload
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8", "replace")
    return response.status, payload


def code_of(payload):
    return ((payload or {}).get("error") or {}).get("code") if isinstance(payload, dict) else None


# -- (a) the declarations that have to agree -------------------------------
portal_config = ROOT / "portal" / "config" / "feature-flags.yaml"
portal_entry = (yaml.safe_load(portal_config.read_text(encoding="utf-8")).get("surfaces") or {}).get(
    ERP_MODULE_SURFACE
)
if not isinstance(portal_entry, dict):
    fail(f"portal/config/feature-flags.yaml declares no surfaces.{ERP_MODULE_SURFACE}")
    portal_entry = {}
elif portal_entry.get("default") not in (False, "off"):
    fail(f"surfaces.{ERP_MODULE_SURFACE}.default is {portal_entry.get('default')!r}, expected off")
else:
    ok(f"the portal's own switch surfaces.{ERP_MODULE_SURFACE} is declared OFF")

registry = yaml.safe_load((ROOT / "infra" / "feature-flags" / "registry.yaml").read_text(encoding="utf-8"))
services = (registry or {}).get("services") or {}
surfaces = (registry or {}).get("surfaces") or {}
declared_rows = 0
for label, row in (("services", services.get(ERP_MODULE_SURFACE)), ("surfaces", surfaces.get(ERP_MODULE_SURFACE))):
    if not isinstance(row, dict):
        fail(
            f"infra/feature-flags/registry.yaml declares no {label}.{ERP_MODULE_SURFACE} — the module's "
            "manifest says this promotion row lands with the surface that becomes reachable (ERP-07, #652)"
        )
        continue
    declared_rows += 1
    if row.get("default") not in (False, "off"):
        fail(f"{label}.{ERP_MODULE_SURFACE}.default is {row.get('default')!r}, expected off")
    if row.get("promoted"):
        fail(f"{label}.{ERP_MODULE_SURFACE}.promoted must be false while it ships off")
    if row.get("tf_flag") != "enable_erp_module":
        fail(f"{label}.{ERP_MODULE_SURFACE}.tf_flag is {row.get('tf_flag')!r}, expected enable_erp_module")
if declared_rows == 2:
    ok("the central promotion rows services.erp_module + surfaces.erp_module are declared OFF")

variables = (ROOT / "infra" / "terraform" / "variables.tf").read_text(encoding="utf-8")
_, _, after = variables.partition('variable "enable_erp_module"')
head = after.split("}", 1)[0] if after else ""
if not after:
    fail("infra/terraform/variables.tf declares no enable_erp_module variable")
elif "default" not in head:
    fail("variable enable_erp_module has no explicit default (must be false)")
elif "true" in head.split("default", 1)[1]:
    fail("variable enable_erp_module does not default to false")
else:
    ok("infra/terraform/variables.tf declares enable_erp_module, defaulting false")

rollout = yaml.safe_load((ROOT / "infra" / "rollout" / "rollout-state.yaml").read_text(encoding="utf-8"))
flags = (rollout or {}).get("flags") or {}
for key in (f"services.{ERP_MODULE_SURFACE}", f"surfaces.{ERP_MODULE_SURFACE}"):
    if key not in flags:
        fail(f"infra/rollout/rollout-state.yaml has no {key} row — the flag is declared but unpromotable")
    elif flags[key].get("stage") != "off":
        fail(f"rollout state {key}.stage is {flags[key].get('stage')!r}, expected off")
if f"services.{ERP_MODULE_SURFACE}" in flags:
    ok("the rollout state carries the surface, so a promotion (and a rollback) can drive it")

# -- (b) the reader fails closed, and the scan is a mutation ---------------
reader_off = surface_enabled(ROOT, surface=ERP_MODULE_SURFACE)
if reader_off is False:
    ok("the fail-closed reader resolves the committed declaration to OFF")
else:
    fail(f"surface_enabled resolved erp_module to {reader_off!r}, expected False")

scratch = Path("/tmp") / f"ao652-reg-on.{os.getpid()}.yaml"
mutated_doc = yaml.safe_load(portal_config.read_text(encoding="utf-8"))
mutated_doc.setdefault("surfaces", {})[ERP_MODULE_SURFACE] = dict(portal_entry or {}, default="on")
scratch.write_text(yaml.safe_dump(mutated_doc), encoding="utf-8")
flipped = surface_enabled(ROOT, config_path=scratch, surface=ERP_MODULE_SURFACE)
scratch.unlink(missing_ok=True)
if flipped is True:
    ok("MUTATION: flipping the declared default to 'on' flips the reader, so the 404 below is the flag")
else:
    fail(f"MUTATION: flipping the flag did not change the reader ({flipped!r}) — the gate is a formality")

broken = Path("/tmp") / f"ao652-reg-broken.{os.getpid()}.yaml"
broken.write_text('surfaces: {erp_module: {default: "on"}\n', encoding="utf-8")
try:
    resolved = surface_enabled(ROOT, config_path=broken, surface=ERP_MODULE_SURFACE)
except Exception as escape:  # noqa: BLE001 - the probe is that nothing escapes
    resolved = f"raised {type(escape).__name__}"
broken.unlink(missing_ok=True)
if resolved is False:
    ok("a malformed declaration reads as OFF rather than escaping the reader (the contract: a value, never an exception)")
else:
    fail(f"a malformed declaration resolved to {resolved!r} — the reader does not fail closed")

# -- (c) flag OFF -> absent; the promoted answer one flag away -------------
MODULE_ROUTES = (
    "/api/erp/module",
    "/api/erp/dashboard",
    "/api/erp/reports",
    "/api/erp/reports/inventory",
    "/api/erp/documents/sales-order",
    "/api/erp/documents/sales-order/SALES-ORDER-0001",
    "/api/erp/health",
    "/api/erp/openapi.json",
)
MODULE_DOCUMENTS = ("/erp/module.html", "/erp/module.js", "/erp/module.css")

off_app = build_app(sso=sso(), erp_module_surface=surface(False))
on_app = build_app(sso=sso(), erp_module_surface=surface(True))
token = mint("root@platform.example.com", "acme")

withheld = []
for path in MODULE_ROUTES + MODULE_DOCUMENTS:
    for label, cookie in (("anonymous", None), ("authenticated", token)):
        status, payload = request(off_app, "GET", path, cookie=cookie)
        if status != 404 or code_of(payload) != "feature_disabled":
            withheld.append(f"{path} ({label}) -> {status} {code_of(payload)}")
if withheld:
    fail(
        "the flag gate: flag OFF must answer 404 feature_disabled before AuthN, but "
        + "; ".join(withheld[:4])
    )
else:
    ok(
        "flag OFF: all %d API routes and %d documents answer 404 feature_disabled, with and "
        "without a session — absent, not merely unauthorised" % (len(MODULE_ROUTES), len(MODULE_DOCUMENTS))
    )

status, _ = request(off_app, "GET", "/views/overview.html")
if status == 200:
    ok("the control: an unrelated console document is still served while the module is off")
else:
    fail(f"the control failed: /views/overview.html -> {status} — the gate is refusing the console, not the module")

promoted = [
    f"{path} -> {request(on_app, 'GET', path, cookie=token)[0]}"
    for path in MODULE_ROUTES
    if request(on_app, "GET", path, cookie=token)[0] != 200
]
if promoted:
    fail("a promoted module did not answer 200: " + "; ".join(promoted[:4]))
else:
    ok(f"the control one flag away: all {len(MODULE_ROUTES)} routes answer 200 when promoted")

anonymous = [path for path in MODULE_ROUTES if request(on_app, "GET", path)[0] != 401]
if anonymous:
    fail("a promoted API route answered an anonymous caller: " + "; ".join(anonymous[:4]))
else:
    ok("a promoted module still needs a session (401 anonymous), so the 404 above is the flag, not the session")

# -- (d) the projection reads its knowledge; it does not carry it ----------
live = surface(True)
contract = live.contract()
dashboard = live.dashboard()
kinds = (contract.get("x-erp-model") or {}).get("kinds") or []
if kinds and [family["kind"] for family in dashboard["families"]] == kinds:
    ok(f"the dashboard's families are the served contract's own kinds, in its own order ({len(kinds)})")
else:
    fail("the dashboard's families are not the contract's kinds")

mismatched = []
for family in dashboard["families"]:
    data = live.call("GET", f"/documents/{family['kind']}").get("data") or {}
    if family["documents"] != data.get("count"):
        mismatched.append(f"{family['kind']}: {family['documents']} rows vs the API's {data.get('count')}")
    if family["redactedFields"] != list(data.get("redacted") or []):
        mismatched.append(f"{family['kind']}: redaction differs from the API's own answer")
if mismatched:
    fail("a dashboard figure is not the API's answer: " + "; ".join(mismatched[:4]))
else:
    ok("every dashboard figure is ERP-06's own answer for this principal (counts and omissions alike)")

first = request(on_app, "POST", "/api/erp/documents/quotation/QUOTATION-0001/transitions/submit", cookie=token)
second = request(on_app, "POST", "/api/erp/documents/quotation/QUOTATION-0001/transitions/submit", cookie=token)
if first[0] == 200 and second[0] == 409 and code_of(second[1]) == "unknown_action":
    ok("a declared move succeeds and the same move twice is refused by name (the model's own 409)")
else:
    fail(f"the transition pair did not behave: first={first[0]}, second={second[0]} {code_of(second[1])}")

status, payload = request(on_app, "POST", "/api/erp/documents/party", cookie=token, body={"doctype": "party"})
if status == 400 and code_of(payload) == "schema_violation":
    ok("the model's refusal reaches the caller unchanged (400 schema_violation — ERP-06's own code)")
else:
    fail(f"a refused create answered {status} {code_of(payload)}, expected 400 schema_violation")

scratch_root = Path(tempfile.mkdtemp(prefix="ao652-manifest-"))
try:
    (scratch_root / "integrations" / "erp").mkdir(parents=True)
    manifest = (ROOT / "integrations" / "erp" / "module.yaml").read_text(encoding="utf-8")
    (scratch_root / "integrations" / "erp" / "module.yaml").write_text(
        manifest.replace("name: ERP\n", "name: ERP (renamed)\n", 1), encoding="utf-8"
    )
    renamed = ErpModuleSurface(repo_root=scratch_root, enabled=True, api=build_erp_api(ROOT)).module()
    if renamed["module"]["name"] == "ERP (renamed)" and live.module()["module"]["name"] == "ERP":
        ok("MUTATION: renaming the manifest in a scratch tree changes the served declaration")
    else:
        fail(f"the served declaration did not follow the manifest ({renamed['module']['name']!r})")
finally:
    shutil.rmtree(scratch_root, ignore_errors=True)

print("erp-portal-driver: " + ("FAIL" if problems else "OK"))
raise SystemExit(1 if problems else 0)
PY_DRIVER
chmod +x "$driver"

echo "== the declarations, the flag gate and the projection =="
if python3 -B "$driver" "$root"; then
  note "the driver is green over the real tree"
else
  rc=$?
  if [ "$rc" -eq 1 ]; then
    printf '  FAIL  the driver is red over the real tree (see the FAIL lines above)\n' >&2
    fail=$((fail + 1))
  else
    echo "  CANNOT-ASSESS  the driver could not run over the real tree (rc=$rc)" >&2
    cannot=1
  fi
fi

# ── (e) the mutant: the flag gate neutered, on a proved-clean copy ─────────
if [ "$fail" -eq 0 ]; then
  echo "== the mutant (a copied tree, proved clean, then the flag gate neutered) =="
  scratch="$(mktemp -d /tmp/ao652-mutant.XXXXXX)"
  tree="$scratch/repo"
  mkdir -p "$tree"
  for name in portal identity integrations infra telemetry engine registry fleet guardrails; do
    if [ -d "$root/$name" ]; then
      mkdir -p "$tree/$name"
      cp -r "$root/$name/." "$tree/$name/"
    fi
  done
  find "$tree" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null

  if python3 -B "$driver" "$tree" > "$scratch/clean.log" 2>&1; then
    note "the copied tree is green under the very same driver (the mutant starts from proven ground)"
    app_file="$tree/portal/server/app.py"
    if python3 - "$app_file" <<'PY_MUTATE'
"""Neuter the module's flag gate in the copy — the one line that makes an
unpromoted surface absent — and prove the write landed."""
import sys
from pathlib import Path

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
gate = '        if parts[0] == "erp" and not self.erp.enabled:'
if gate not in text:
    raise SystemExit(f"the flag gate is not in {path}")
path.write_text(
    text.replace(gate, '        if False and parts[0] == "erp" and not self.erp.enabled:', 1),
    encoding="utf-8",
)
print("mutated")
PY_MUTATE
    then
      python3 -B "$driver" "$tree" > "$scratch/mutant.log" 2>&1
      mutant_rc=$?
      named="$(grep -E '^  FAIL' "$scratch/mutant.log" || true)"
      if [ "$mutant_rc" -eq 0 ]; then
        problem "neutering the flag gate left the driver green — the flag gate is not what withholds the surface"
      elif [[ "$named" != *feature_disabled* ]]; then
        problem "the mutant went red but named no probe about the flag gate: $(printf '%s' "$named" | head -n 1)"
      elif [ -n "$(printf '%s\n' "$named" | grep -v 'feature_disabled')" ]; then
        problem "the mutant reddened an unrelated probe too, so it does not name the gate it removed"
      else
        note "the mutant goes red on that probe and on no unrelated one:"
        printf '%s\n' "$named" | sed 's/^/        /'
      fi
    else
      problem "the mutation could not be applied to the copied portal/server/app.py"
    fi
  else
    problem "the copied tree is not clean under the driver, so a mutation on it proves nothing"
    tail -n 6 "$scratch/clean.log" | sed 's/^/        /' >&2
  fi
  rm -rf "$scratch"
else
  echo "  SKIP  the mutant (the driver is already red; a mutant on an unproven tree proves nothing)"
fi

# ── the module's own gate, still green over this lane ─────────────────────
echo "== the ERP-01 declaration this lane reads =="
if python3 integrations/erp/catalog/cli.py verify >/dev/null 2>&1; then
  note "integrations/erp/catalog/cli.py verify is still green (the manifest this lane reads)"
else
  problem "integrations/erp/catalog/cli.py verify is not green — the manifest this lane reads is broken"
fi

if [ "$fail" -ne 0 ]; then
  echo "erp-portal: FAIL" >&2
  exit 1
fi
if [ "$cannot" -ne 0 ]; then
  echo "erp-portal: CANNOT-ASSESS" >&2
  exit 2
fi
echo "erp-portal: OK"
exit 0
