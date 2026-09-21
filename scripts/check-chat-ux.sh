#!/usr/bin/env bash
# check-chat-ux.sh — the conversational surface's UX contract (issue #508).
#
# The chat surface (portal/server/chat.py + the view) is the "elite SaaS" half
# of EPIC #500, and its two load-bearing properties are things a test can drift
# away from silently:
#
#   * the vocabulary it *declares* must be the authority's vocabulary — the
#     tier ladder pinned from `gateway/providers/contract.py` and the budget
#     actions pinned from `gateway/finops/budget.py`. A rename on either side
#     fails here rather than shipping a picker that offers a tier nobody
#     resolves;
#   * an unpromoted surface must be **absent**, not merely refused. This gate
#     provokes that: with `surfaces.chat: default: off` the API family AND the
#     view's own assets must 404 **before AuthN**, and the check FAILS if
#     either is still served. Its negative control is its positive control one
#     flag flip away: with `default: on` the same probes must stop being 404
#     (401 for the API, a served document for the view) — so a probe that
#     always 404s fails too.
#
# Plus the honest-degradation contract, which is what the surface is *for*: a
# client that cannot choose a provider model, and refusal codes that keep the
# hard stop, an unreadable budget and a cancelled turn distinguishable.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# Usage: bash scripts/check-chat-ux.sh
#
# ---knowledge---
# module_id: scripts.check-chat-ux
# system: governance
# app: gates
# solution_class: enterprise
# patterns: [tri-state-exit, provoked-negative-control, no-false-green, declared-authority, schema-validation]
# derives_from: null
# owner_sme: qa-sme
# tier: L1
# interfaces: [exit 0 OK, exit 1 NOT-OK, exit 2 CANNOT-ASSESS]
# invariants: ""
# gotchas: ""
# related: ["#500", "#508"]
# do_not_duplicate: null
# ---knowledge---
set -u

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"
cd "$root" || exit 2

# ── the pinned vocabulary ──────────────────────────────────────────────────
# Source of truth: the gateway's own declarations, read as text (the portal
# consumes them over HTTP at runtime and never imports them).
EXPECT_TIERS="LOW, MED, HIGH, MAX"
EXPECT_BUDGET_ACTIONS="allow, warn, fallback, stop"

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-chat-ux: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

fail=0

for required in \
  portal/server/chat.py \
  portal/static/views/chat.html \
  portal/static/js/chat.js \
  gateway/providers/contract.py \
  gateway/finops/budget.py
do
  if [ -f "$required" ]; then
    echo "  OK    $required present"
  else
    echo "  FAIL  $required is missing" >&2
    fail=$((fail + 1))
  fi
done
if [ "$fail" -gt 0 ]; then
  echo "check-chat-ux: FAIL ($fail missing file(s))" >&2
  exit 1
fi

echo "== the pin agrees with the authority =="
if python3 - "$EXPECT_TIERS" "$EXPECT_BUDGET_ACTIONS" <<'PY'
import re
import sys
from pathlib import Path

expect_tiers = [part.strip() for part in sys.argv[1].split(",")]
expect_actions = [part.strip() for part in sys.argv[2].split(",")]
problems = []

# 1. the authority: the DECLARED tier vocabulary and the budget action enum.
#    The ladder's authority is `registry/profiles/catalog.yaml` `tiers:`, read
#    through its one reader (#1494) — the portal used to pin a second copy of the
#    ladder "from the gateway's provider contract", and that copy is gone, so this
#    reads the one authority every surface now reads. The pin above stays an
#    independent literal, which is what makes this comparison able to fail.
sys.path.insert(0, ".")
from registry.profiles import tiers as tier_authority  # noqa: E402

authority_tiers = list(tier_authority.authority())
if authority_tiers != expect_tiers:
    problems.append(
        f"registry/profiles/catalog.yaml tiers = {authority_tiers} "
        f"(pinned {expect_tiers})"
    )

budget = Path("gateway/finops/budget.py").read_text(encoding="utf-8")
authority_actions = [
    value for value in re.findall(r'=\s*"(allow|warn|fallback|stop)"', budget)
]
if sorted(set(authority_actions)) != sorted(expect_actions):
    problems.append(
        f"gateway/finops/budget.py BudgetAction = {sorted(set(authority_actions))} "
        f"(pinned {sorted(expect_actions)})"
    )

# 2. the surface: the portal's pinned copy of the same vocabulary.
sys.path.insert(0, ".")
from portal.server.chat import (  # noqa: E402
    BUDGET_ACTIONS,
    DEFAULT_TIER,
    GROUNDING_NO_DATA,
    GROUNDING_OK,
    SEVERITIES,
    TIERS,
)

if list(TIERS) != expect_tiers:
    problems.append(f"portal/server/chat.py TIERS = {list(TIERS)}")
if list(BUDGET_ACTIONS) != expect_actions:
    problems.append(f"portal/server/chat.py BUDGET_ACTIONS = {list(BUDGET_ACTIONS)}")
if DEFAULT_TIER not in TIERS:
    problems.append(f"portal/server/chat.py DEFAULT_TIER = {DEFAULT_TIER!r}")
if GROUNDING_OK != "OK" or GROUNDING_NO_DATA != "NO_DATA":
    problems.append(
        f"grounding states are {GROUNDING_OK!r}/{GROUNDING_NO_DATA!r}, expected OK/NO_DATA"
    )
if list(SEVERITIES) != ["ok", "warning", "hard_stop", "no_data"]:
    problems.append(
        f"portal/server/chat.py SEVERITIES = {list(SEVERITIES)}: a soft warning, a "
        "hard stop and an unreadable budget must stay distinguishable"
    )

if problems:
    print("  FAIL  the chat vocabulary disagrees with the authority:", file=sys.stderr)
    for problem in problems:
        print(f"        {problem}", file=sys.stderr)
    raise SystemExit(1)
print("  OK    tiers and budget actions agree with the gateway declarations")
PY
then
  :
else
  fail=$((fail + 1))
fi

echo "== the client selects a tier, never a provider model =="
if python3 - <<'PY'
import sys

sys.path.insert(0, ".")
from portal.server.chat import ChatError, TIERS, parse_turn_request

problems = []

# A client-supplied provider model must be refused, and refused for free: the
# refusal has to happen here, before anything is sent to the authority.
for forbidden in ("model", "modelId", "provider"):
    try:
        parse_turn_request({"text": "hello", forbidden: "gpt-4o"})
        problems.append(f"a turn carrying {forbidden!r} was ACCEPTED")
    except ChatError as exc:
        if exc.code != "chat_tier_only":
            problems.append(f"{forbidden!r} was refused with {exc.code!r}")

# An unknown tier is refused too, so no arbitrary model id can ride the field.
try:
    parse_turn_request({"text": "hello", "tier": "ULTRA"})
    problems.append("an unknown tier was ACCEPTED")
except ChatError as exc:
    if exc.code != "chat_unknown_tier":
        problems.append(f"an unknown tier was refused with {exc.code!r}")

# ...and the normal paths still work, or the refusals above prove nothing.
for tier in TIERS:
    text, resolved = parse_turn_request({"text": "hello", "tier": tier})
    if resolved != tier or text != "hello":
        problems.append(f"a valid turn at {tier} parsed as {(text, resolved)!r}")

if problems:
    print("  FAIL  the tier-only contract is not enforced:", file=sys.stderr)
    for problem in problems:
        print(f"        {problem}", file=sys.stderr)
    raise SystemExit(1)
print("  OK    a turn refuses 'model'/'provider', refuses an unknown tier, accepts every tier")
PY
then
  :
else
  fail=$((fail + 1))
fi

echo "== the flag gate, with its negative control =="
work="/tmp/ao508-chat-ux.$$.$(date +%s)"
mkdir "$work" || exit 2
trap 'rm -rf "$work"' EXIT

write_registry() { # write_registry <file> <on|off>
  cat > "$1" <<EOF
schema_version: 1
default_policy: off
surfaces:
  chat:
    default: $2
    promoted: false
    service: portal
    description: conversational surface (issue #508)
EOF
}
write_registry "$work/off.yaml" off
write_registry "$work/on.yaml" on

probe() { # probe <registry> <json-field>  -> prints one JSON line
  python3 - "$root" "$1" "$2" "$3" <<'PY'
import json
import sys

root, registry, field, store = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
sys.path.insert(0, root)
from portal.server.app import build_app  # noqa: E402
from portal.server.chat import ChatSurface  # noqa: E402

app = build_app(repo_root=root, chat_surface=ChatSurface(
    repo_root=root, registry_path=registry, store_dir=store,
))

# No cookies at all: the flag gate runs BEFORE AuthN, so this is the probe an
# anonymous visitor makes.
api = app.handle("GET", "/api/chat/tiers", query={}, body={}, cookies={})
view = app.handle("GET", "/views/chat.html", query={}, body={}, cookies={})
client = app.handle("GET", "/js/chat.js", query={}, body={}, cookies={})

code = ""
if api.payload and isinstance(api.payload, dict):
    code = ((api.payload.get("error") or {}).get("code")) or ""
print(json.dumps({
    "registry": field,
    "apiStatus": api.status,
    "apiCode": code,
    "viewStatus": view.status,
    "viewBytes": len(view.as_bytes()),
    "clientStatus": client.status,
}))
PY
}

off_json="$(probe "$work/off.yaml" off "$work/store" 2>&1)"
if off_json="$off_json" python3 - <<'PY'
import json
import os
import sys

data = json.loads(os.environ["off_json"])

# The negative control: provoke the failure (flag off) and require the absence.
# A route or a view that is still served here is a red gate, not a warning.
problems = []
if data["apiStatus"] != 404 or data["apiCode"] != "feature_disabled":
    problems.append(
        f"the API family is still reachable with the flag off "
        f"(status={data['apiStatus']}, code={data['apiCode']!r})"
    )
for name in ("viewStatus", "clientStatus"):
    if data[name] != 404:
        problems.append(f"{name} is still served with the flag off ({data[name]})")
if problems:
    print("  FAIL  the unpromoted surface is not absent:", file=sys.stderr)
    for problem in problems:
        print(f"        {problem}", file=sys.stderr)
    raise SystemExit(1)
print("  OK    flag off: /api/chat/*, /views/chat.html and /js/chat.js are all absent (404, before AuthN)")
PY
then
  :
else
  fail=$((fail + 1))
fi

on_json="$(probe "$work/on.yaml" on "$work/store" 2>&1)"
if on_json="$on_json" python3 - <<'PY'
import json
import os
import sys

data = json.loads(os.environ["on_json"])

# The control has to be able to fail in the other direction too: if the probes
# 404 with the flag on, the "absence" above is a formality (GR-12).
problems = []
if data["apiStatus"] == 404:
    problems.append("the API family 404s even with the flag promoted")
if data["apiStatus"] != 401:
    problems.append(
        f"an anonymous API probe with the flag promoted gave {data['apiStatus']}, "
        "expected 401 (flag gate first, then AuthN)"
    )
if data["viewStatus"] != 200 or data["viewBytes"] == 0:
    problems.append(
        f"the view is not served with the flag promoted "
        f"(status={data['viewStatus']}, bytes={data['viewBytes']})"
    )
if data["clientStatus"] != 200:
    problems.append(f"the client asset is not served with the flag promoted ({data['clientStatus']})")
if problems:
    print("  FAIL  the promoted surface is not reachable:", file=sys.stderr)
    for problem in problems:
        print(f"        {problem}", file=sys.stderr)
    raise SystemExit(1)
print("  OK    flag on: the API family answers 401 (not 404) and the view is served")
PY
then
  :
else
  fail=$((fail + 1))
fi

echo "== honest states are implemented, not merely declared =="
missing=0
check_literal() { # check_literal <file> <literal> <why>
  if grep -qF -- "$2" "$1"; then
    echo "  OK    $1 carries $3"
  else
    echo "  FAIL  $1 does not carry $3 (literal: $2)" >&2
    missing=$((missing + 1))
  fi
}
check_literal portal/static/js/chat.js '"data-supported": supported ? "true" : "false"' \
  'the unsupported-claim marker'
check_literal portal/static/js/chat.js '"data-degraded": "true"' \
  'the degraded-turn label'
check_literal portal/static/js/chat.js '"data-state": "failed"' \
  'a failed-turn state (the upstream-fault render)'
check_literal portal/static/js/chat.js 'hard_stop' 'the hard-stop severity'
check_literal portal/static/js/chat.js 'no_data' 'the unreadable-budget severity'
check_literal portal/static/views/chat.html 'aria-live="polite"' \
  'a live region for streamed content'
check_literal portal/static/views/chat.html 'role="log"' 'the transcript role'
check_literal portal/static/views/chat.html 'data-selects="tier"' \
  'the tier-only picker note'
check_literal portal/server/chat.py 'unsupported — no source in the citations envelope' \
  'the unsupported-claim label'
if [ "$missing" -gt 0 ]; then
  fail=$((fail + missing))
fi

if [ "$fail" -gt 0 ]; then
  echo "check-chat-ux: FAIL ($fail violation(s))" >&2
  exit 1
fi
echo "check-chat-ux: OK — vocabulary pinned to the gateway, flag-absent before AuthN, tier-only client, honest states present"
exit 0
