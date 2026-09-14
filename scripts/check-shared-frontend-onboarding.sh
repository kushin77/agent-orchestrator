#!/usr/bin/env bash
# check-shared-frontend-onboarding.sh — the shared-frontend mandatory onboarding
# gate (issue #703; GR-18 / CMR:ONBOARD-0003).
#
# `shared-frontend` is the hub's THIRD mandatory module: it ships to every repo
# CMR governs, and its declared consumer assets are `tokens.json` (the shared
# `--os-` design-token twin) and a `gdc-manifest.yaml` `shared-frontend.tokens`
# module pin. An asset that nothing checks is a formality, so this gate refuses
# BY NAME when:
#
#   * `tokens.json` is missing at the repo root;
#   * `tokens.json` is not byte-identical to the pinned seed — its sha256 must
#     equal the pin recorded in the lane (`render.py` PINNED / the template's
#     `provenance`), and when the vendored seed IS initialised it must be
#     byte-identical to that too;
#   * `tokens.json` drifted from the pinned rev (`ace748f4` / `v0.2.0`);
#   * `gdc-manifest.yaml` is missing at the repo root;
#   * any of the three mandatory pins (`code-indexing.mcp`, `diagrams.blueprint`,
#     `shared-frontend.tokens`) is missing, or its version/update policy moved;
#   * the `x-onboarding` tenant/org/domain binding disagrees with the instance;
#   * either root asset is NOT the render of the committed
#     `governance/onboarding/shared-frontend/instance.yaml` (drift, i.e. a hand
#     edit that bypassed the template).
#
# The onboarding is a TEMPLATE, not a hand-copied pair of files: the two assets
# are rendered from `governance/onboarding/shared-frontend/template.yaml` for the
# (org, tenant, domain, repo) tuple the instance names, and the render is a pure,
# byte-stable function (no network, no clock, no environment).
#
# Self-proving (AO-GR-19, GR-12). A check that cannot fail is rejected, so every
# invocation on the real tree also stages scratch copies of the tree and REQUIRES
# each refusal to fire: a deleted `tokens.json`, a mutated one, a dropped pin, an
# unknown tenant, and an unparseable lane input (which must land in
# CANNOT-ASSESS, not a pass). The 0/1/2 contract is exercised in all three
# states.
#
# Exit-code contract (guardrails/honesty tri-state, issue #28):
#   0  OK               the root assets are the render of the instance
#   1  NOT-OK           a real defect, named on stderr
#   2  CANNOT-ASSESS    the contract could not be assessed — never a pass
#
# Offline, deterministic, stdlib + PyYAML only.
#
# Usage:
#   bash scripts/check-shared-frontend-onboarding.sh             # this repo
#   bash scripts/check-shared-frontend-onboarding.sh --root DIR   # a scratch tree
#     --root DIR checks DIR instead of the repo root and suppresses the
#     self-proof, so the gate's own failure paths are testable without recursion.
set -u

self_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
self_script="$self_dir/$(basename "${BASH_SOURCE[0]}")"
self_root="$(cd "$self_dir/.." && pwd)"
root="$self_root"
lane_rel="governance/onboarding/shared-frontend"

while [ "$#" -gt 0 ]; do
  case "$1" in
    --root)
      [ "$#" -ge 2 ] || { echo "check-shared-frontend-onboarding: CANNOT-ASSESS — --root needs a directory" >&2; exit 2; }
      root="$2"; shift 2 ;;
    -h|--help)
      sed -n '2,48p' "$0"; exit 0 ;;
    *)
      printf 'check-shared-frontend-onboarding: unknown argument: %s\n' "$1" >&2; exit 2 ;;
  esac
done

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-shared-frontend-onboarding: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi
if [ ! -d "$root" ]; then
  printf 'check-shared-frontend-onboarding: CANNOT-ASSESS — root is not a directory: %s\n' "$root" >&2
  exit 2
fi

renderer="$root/$lane_rel/render.py"
suite="$root/$lane_rel/tests"
if [ ! -f "$renderer" ]; then
  printf 'check-shared-frontend-onboarding: CANNOT-ASSESS — %s is missing (nothing to assess)\n' \
    "$lane_rel/render.py" >&2
  exit 2
fi

echo "== shared-frontend mandatory onboarding =="
if python3 "$renderer" --repo-root "$root" check; then
  :
else
  rc=$?
  if [ "$rc" -eq 2 ]; then
    echo "check-shared-frontend-onboarding: CANNOT-ASSESS — the onboarding contract could not be assessed" >&2
  else
    echo "check-shared-frontend-onboarding: NOT-OK — the root assets are not the render of the instance" >&2
  fi
  exit "$rc"
fi

# The renderer's own behavioural suite is exercised here so it cannot rot into an
# artifact no gate runs. Only on the real tree (a scratch tree has no business
# running the gate's suite), and never when this gate is itself running under
# pytest — that would recurse.
if [ "$root" = "$self_root" ] && [ -d "$suite" ]; then
  if [ -n "${PYTEST_CURRENT_TEST:-}" ]; then
    echo "  NOTE  renderer suite skipped: this gate is running under pytest (recursion guard)"
  elif ! python3 -c 'import pytest' >/dev/null 2>&1; then
    echo "check-shared-frontend-onboarding: CANNOT-ASSESS — pytest is unavailable; the renderer suite could not run" >&2
    exit 2
  else
    echo "== renderer suite =="
    if env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q "$suite"; then
      echo "  OK    the renderer suite is green"
    else
      src=$?
      printf 'check-shared-frontend-onboarding: NOT-OK — the renderer suite is red (rc %s)\n' "$src" >&2
      exit 1
    fi
  fi
fi

if [ "$root" != "$self_root" ]; then
  echo "check-shared-frontend-onboarding: OK (checked $root; self-proof suppressed under --root)"
  exit 0
fi

echo "== self-proof (every refusal can actually fire) =="

# Scratch paths are built from the pid and the nanosecond clock rather than a run
# of placeholder characters, which the repo's unfinished-marker scan would flag.
scratch_root="/tmp/ao703-shared-frontend.$$.$(date +%s%N)"
if ! mkdir -p "$scratch_root"; then
  echo "check-shared-frontend-onboarding: CANNOT-ASSESS — cannot create a scratch directory" >&2
  exit 2
fi
trap 'rm -rf "$scratch_root"' EXIT

stage() { # $1 = case name; echoes a fresh scratch repo root
  local case_dir="$scratch_root/$1"
  mkdir -p "$case_dir/$(dirname "$lane_rel")" || return 1
  cp -r "$self_root/$lane_rel" "$case_dir/$lane_rel" || return 1
  cp "$self_root/tokens.json" "$case_dir/tokens.json" || return 1
  cp "$self_root/gdc-manifest.yaml" "$case_dir/gdc-manifest.yaml" || return 1
  printf '%s' "$case_dir"
}

CASE=""

expect_rc() { # $1 want  $2 label  $3 needle ("" = no output requirement)
  local want="$1" label="$2" needle="$3" out got
  out="$(bash "$self_script" --root "$CASE" 2>&1)"
  got=$?
  if [ "$got" -ne "$want" ]; then
    printf 'check-shared-frontend-onboarding: FAIL — self-proof %s returned rc %s (want %s)\n' \
      "$label" "$got" "$want" >&2
    printf '%s\n' "$out" >&2
    exit 1
  fi
  if [ -n "$needle" ] && ! printf '%s\n' "$out" | grep -qF -- "$needle"; then
    printf 'check-shared-frontend-onboarding: FAIL — self-proof %s did not name %s\n' \
      "$label" "$needle" >&2
    printf '%s\n' "$out" >&2
    exit 1
  fi
  printf '  OK    self-proof: %s (rc %s, names %s)\n' "$label" "$got" "${needle:-nothing}"
}

# (control) the untouched scratch tree is green, so a later red is the mutation.
CASE="$(stage control)" || { echo "check-shared-frontend-onboarding: CANNOT-ASSESS — staging failed" >&2; exit 2; }
expect_rc 0 "untouched scratch tree is green" ""

# (a) tokens.json deleted -> REFUSED, naming the missing asset.
CASE="$(stage missing-tokens)" || { echo "check-shared-frontend-onboarding: CANNOT-ASSESS — staging failed" >&2; exit 2; }
rm -f "$CASE/tokens.json" || exit 2
expect_rc 1 "deleting tokens.json is refused" "tokens.json: MISSING"

# (b) tokens.json mutated -> REFUSED, naming the pinned-rev drift.
CASE="$(stage drifted-tokens)" || { echo "check-shared-frontend-onboarding: CANNOT-ASSESS — staging failed" >&2; exit 2; }
printf ' ' >>"$CASE/tokens.json" || exit 2
expect_rc 1 "a token set that drifted from the pinned rev is refused" "drifted from the pinned rev"

# (c) a mandatory pin dropped -> REFUSED, naming the pin.
CASE="$(stage dropped-pin)" || { echo "check-shared-frontend-onboarding: CANNOT-ASSESS — staging failed" >&2; exit 2; }
if ! python3 - "$CASE/gdc-manifest.yaml" <<'PY'
import sys

import yaml

path = sys.argv[1]
with open(path, encoding="utf-8") as handle:
    doc = yaml.safe_load(handle)
before = len(doc.get("modules") or [])
doc["modules"] = [
    pin for pin in (doc.get("modules") or [])
    if not (isinstance(pin, dict) and pin.get("module") == "shared-frontend.tokens")
]
if len(doc["modules"]) != before - 1:
    sys.stderr.write("could not remove exactly one shared-frontend.tokens pin\n")
    sys.exit(1)
with open(path, "w", encoding="utf-8") as handle:
    handle.write(yaml.safe_dump(doc, sort_keys=False))
PY
then
  echo "check-shared-frontend-onboarding: CANNOT-ASSESS — could not stage the dropped-pin control" >&2
  exit 2
fi
expect_rc 1 "dropping the shared-frontend.tokens pin is refused" \
  "mandatory pin 'shared-frontend.tokens' is MISSING"

# (d) an unknown tenant -> REFUSED by name (the vocabulary is closed).
CASE="$(stage unknown-tenant)" || { echo "check-shared-frontend-onboarding: CANNOT-ASSESS — staging failed" >&2; exit 2; }
if ! python3 - "$CASE/$lane_rel/instance.yaml" <<'PY'
import sys

path = sys.argv[1]
with open(path, encoding="utf-8") as handle:
    lines = handle.read().splitlines()
out = ["tenant: kushin77-typo" if line.startswith("tenant:") else line for line in lines]
with open(path, "w", encoding="utf-8") as handle:
    handle.write("\n".join(out) + "\n")
PY
then
  echo "check-shared-frontend-onboarding: CANNOT-ASSESS — could not stage the unknown-tenant control" >&2
  exit 2
fi
expect_rc 1 "an unknown tenant is refused by name" "unknown tenant 'kushin77-typo'"

# (e) an unparseable lane input -> CANNOT-ASSESS (never a pass).
CASE="$(stage broken-lane)" || { echo "check-shared-frontend-onboarding: CANNOT-ASSESS — staging failed" >&2; exit 2; }
printf 'org: [unclosed\n' >"$CASE/$lane_rel/instance.yaml" || exit 2
expect_rc 2 "an unparseable lane input is CANNOT-ASSESS" "CANNOT-ASSESS"

echo "  OK    self-proof passed (the 0 / 1 / 2 contract is exercised in all three states)"
echo "check-shared-frontend-onboarding: OK"
exit 0
