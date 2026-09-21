#!/usr/bin/env bash
# check-telemetry.sh — the telemetry surface's dedicated gate (issue #886,
# lane L7 of EPIC #878; parent of #971).
#
# Three real checks, then a negative control:
#
#   1. Every telemetry/metering/rate_cards/*.yaml file parses as valid YAML
#      and matches the card shape ratecards.py expects (schemaVersion,
#      provider, models) — a malformed card must never load silently.
#
#   2. Every ANTHROPIC (provider: anthropic) model id referenced from
#      gateway/finops/tiers.yaml and gateway/health/health.yaml resolves,
#      through the shared telemetry.metering.model_aliases normalization, to
#      an entry in rate_cards/anthropic.yaml. This is the actual #971 gap
#      (retired Claude ids silently unpriced) and is a hard FAIL
#      (TELEMETRY-RATECARD-MISSING) — anthropic.yaml is a file this lane
#      owns. Non-anthropic provider/model pairs referenced from the same
#      two files are reported too, but only as a WARNING
#      (TELEMETRY-RATECARD-UNOWNED-GAP): those rate cards
#      (deepseek.yaml, a `google`/`gemini` provider-name mismatch, ...) are
#      owned by other lanes and out of this lane's file scope (see
#      docs/EXECUTION-PLAN.md). This gate reports them honestly (name +
#      site) rather than silently ignoring them, but does not fail on them.
#      This is not merely a scope courtesy: scripts/discover-checks.sh
#      auto-wires every scripts/check-*.sh into `make verify` (issue #698),
#      so a hard FAIL here would red the gate of record for every sibling
#      lane over a gap this lane has no file permission to fix. Residual
#      gaps as of this writing: deepseek-v4-flash / deepseek-v4-pro /
#      deepseek-v4-pro-thinking absent from rate_cards/deepseek.yaml, and
#      `provider: google` in tiers.yaml L0 vs the card's `provider: gemini`
#      (gateway/finops + gateway/health own the fix).
#
#   3. telemetry/metering's own pytest suite (the rate-card + alias-
#      resolution behavior itself) is green.
#
# Exit-code contract (repo convention, GR-12): 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# Usage: bash scripts/check-telemetry.sh
set -u

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"
cd "$root" || exit 2

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-telemetry: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

# Only the self-invoked negative-control child (TELEMETRY_NEGATIVE_CONTROL=1,
# below) may redirect which rate-card directory is measured — without this
# guard, anyone could point the gate at a clean directory elsewhere while the
# repo's real rate_cards/ goes unmeasured (a gate that can be pointed away
# from what it claims to check is a gate that cannot fail, GR-12).
if [ -n "${TELEMETRY_NEGATIVE_CONTROL:-}" ]; then
  rate_dir="${TELEMETRY_RATE_CARD_DIR:-telemetry/metering/rate_cards}"
else
  rate_dir="telemetry/metering/rate_cards"
fi

check_out="$(python3 - "$rate_dir" <<'PYEOF'
import sys
sys.path.insert(0, ".")

from pathlib import Path

import yaml

from telemetry.metering.ratecards import RateCardError, RateCardStore
from telemetry.metering.model_aliases import normalize_model

rate_dir = Path(sys.argv[1])

# --- 1. every rate card parses + matches the shape ratecards.py expects ----
try:
    store = RateCardStore.load_dir(rate_dir)
except RateCardError as exc:
    print(f"TELEMETRY-RATECARD-PARSE: {exc}")
    sys.exit(1)
except Exception as exc:  # pragma: no cover - defensive, still a real FAIL
    print(f"TELEMETRY-RATECARD-PARSE: {rate_dir} failed to load: {exc}")
    sys.exit(1)

print(f"rate cards parsed OK: {', '.join(store.providers())}")

# --- 2. every referenced model id resolves in its provider's card ---------
refs = []  # (provider, model, where)

tiers = yaml.safe_load(Path("gateway/finops/tiers.yaml").read_text(encoding="utf-8"))
for tier_id, tier in (tiers.get("ladder") or {}).items():
    for m in tier.get("models") or []:
        refs.append((m["provider"], m["id"], f"gateway/finops/tiers.yaml ladder.{tier_id}"))

health = yaml.safe_load(Path("gateway/health/health.yaml").read_text(encoding="utf-8"))
chains = health.get("chains") or {}
shared = chains.get("sharedServices") or {}
if shared.get("provider") and shared.get("model"):
    refs.append((shared["provider"], shared["model"], "gateway/health/health.yaml chains.sharedServices"))
for chain in chains.get("explicit") or []:
    for rung in chain.get("rungs") or []:
        refs.append((rung["provider"], rung["model"], f"gateway/health/health.yaml chains.explicit[{chain.get('key')}]"))
last_resort = chains.get("localLastResort")
if last_resort and "/" in last_resort:
    p, m = last_resort.split("/", 1)
    refs.append((p, m, "gateway/health/health.yaml chains.localLastResort"))

# Providers this lane does not own a rate card for (no card at all, e.g. the
# `shared-services` routing rung is deliberately unpriced -- it is a fallback
# endpoint, not a billable model).
NO_CARD_EXPECTED = {"shared-services"}

missing_anthropic = []
unowned_gaps = []
for provider, model, where in refs:
    if provider in NO_CARD_EXPECTED:
        continue
    card = store.card(provider)
    if card is None:
        unowned_gaps.append(f"{where}: provider {provider!r} has no rate card at all (model {model!r})")
        continue
    resolved = normalize_model(model) if provider == "anthropic" else model
    if card.lookup(resolved) is None:
        line = f"{where}: {provider}/{model} (normalized: {resolved}) not in {rate_dir}/{provider}.yaml"
        if provider == "anthropic":
            missing_anthropic.append(line)
        else:
            unowned_gaps.append(line)

for line in unowned_gaps:
    print(f"TELEMETRY-RATECARD-GAP: {line}")

if missing_anthropic:
    for line in missing_anthropic:
        print(f"TELEMETRY-RATECARD-MISSING: {line}")
    sys.exit(1)

print(f"anthropic model ids referenced from tiers.yaml/health.yaml all resolve in {rate_dir}/anthropic.yaml")
sys.exit(0)
PYEOF
)"
check_rc=$?
echo "$check_out"

if [ "$check_rc" -ne 0 ]; then
  echo "check-telemetry: NOT-OK — rate-card / model-id check failed" >&2
  exit 1
fi

# --- 3. the metering suite itself is green ----------------------------------
if [ -z "${TELEMETRY_NEGATIVE_CONTROL:-}" ]; then
  if ! suite_out="$(python3 -m pytest telemetry/metering/tests -q 2>&1)"; then
    echo "check-telemetry: NOT-OK — telemetry/metering/tests failed:" >&2
    echo "$suite_out" >&2
    exit 1
  fi
  echo "check-telemetry: telemetry/metering/tests OK ($(echo "$suite_out" | tail -1))"
fi

# --- 4. negative control: provoke TELEMETRY-RATECARD-MISSING on a mutant ---
#    card directory with the claude-sonnet-5 entry deleted, by DRIVING the
#    real check above (self-invoking this gate), never by re-deriving the
#    assertion inline (that would be a tautology, not a proof).
if [ -z "${TELEMETRY_NEGATIVE_CONTROL:-}" ]; then
  scratch="$(mktemp -d "/tmp/ao877-telemetry.$(printf 'X%.0s' 1 2 3 4 5 6)")"
  trap 'rm -rf "$scratch"' EXIT
  cp -r "$rate_dir"/*.yaml "$scratch/"

  python3 - "$scratch/anthropic.yaml" <<'PYEOF'
import sys
import yaml

path = sys.argv[1]
with open(path, encoding="utf-8") as fh:
    data = yaml.safe_load(fh)
assert "claude-sonnet-5" in data["models"], "fixture drifted: claude-sonnet-5 missing before mutation"
del data["models"]["claude-sonnet-5"]
with open(path, "w", encoding="utf-8") as fh:
    yaml.safe_dump(data, fh)
PYEOF

  nc_out="$(TELEMETRY_NEGATIVE_CONTROL=1 TELEMETRY_RATE_CARD_DIR="$scratch" bash "$0" 2>&1)"
  nc_rc=$?

  if [ "$nc_rc" -eq 0 ]; then
    echo "check-telemetry: NOT-OK — negative control: a rate-card mutant with claude-sonnet-5 deleted ($scratch) was ACCEPTED (rc 0); the missing-id check does not fire:" >&2
    echo "$nc_out" >&2
    exit 1
  fi

  if [[ "$nc_out" != *"TELEMETRY-RATECARD-MISSING"* ]]; then
    echo "check-telemetry: NOT-OK — negative control: mutant was refused (rc $nc_rc) but not by name (TELEMETRY-RATECARD-MISSING missing):" >&2
    echo "$nc_out" >&2
    exit 1
  fi

  echo "check-telemetry: negative control confirmed — a rate-card mutant with claude-sonnet-5 deleted was refused (rc $nc_rc) and named TELEMETRY-RATECARD-MISSING"
fi

echo "check-telemetry: OK"
exit 0
