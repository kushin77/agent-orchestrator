#!/usr/bin/env bash
# check-adr-status.sh — ADR promotion-state gate (issue #1619).
#
# The ADR front-matter `status:` vocabulary used to be undecided ("accepted"
# meant both "we decided" and "the decision is live", and 9/27 ADRs carried no
# status at all). This closes the vocabulary to
#   reserved | proposed | accepted | live | superseded | deprecated
# ("reserved" = an index-only placeholder number, not yet a decision — see
# docs/decision-records/README.md)
# and refuses, BY NAME:
#   - any docs/decision-records/ADR-*.md with no `status:` key
#   - any `status:` value outside the closed set
#   - `status: superseded` with no (or empty) `superseded_by:` key, or one
#     naming an ADR id that does not exist on disk
#   - `status: live` with no (or empty) `live_resource:` key
#
# Usage:
#   bash scripts/check-adr-status.sh              the gate, over docs/decision-records/
#   bash scripts/check-adr-status.sh --self-test   prove both directions
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 1

ADR_DIR="docs/decision-records"
VALID_STATES="reserved proposed accepted live superseded deprecated"

# check_one FILE DIR -> prints failures to stdout, returns nonzero on failure.
# DIR is the directory being scanned (successor lookups resolve against it,
# not a hardcoded path, so the self-test can exercise BAD-SUCCESSOR).
check_one() {
  local f="$1" dir="$2" fail=0
  local status superseded_by live_resource
  status="$(grep -m1 '^status:' "$f" | sed -E 's/^status:[[:space:]]*//')"
  if [ -z "$status" ]; then
    echo "MISSING-STATUS: $f has no 'status:' front-matter key"
    return 1
  fi
  local ok=0
  for v in $VALID_STATES; do
    [ "$status" = "$v" ] && ok=1
  done
  if [ "$ok" -ne 1 ]; then
    echo "UNKNOWN-STATE: $f status '$status' is outside {$VALID_STATES}"
    fail=1
  fi
  if [ "$status" = "superseded" ]; then
    superseded_by="$(grep -m1 '^superseded_by:' "$f" | sed -E 's/^superseded_by:[[:space:]]*//')"
    if [ -z "$superseded_by" ]; then
      echo "NO-SUCCESSOR: $f is superseded but has no populated 'superseded_by:'"
      fail=1
    elif ! compgen -G "$dir/${superseded_by}-*.md" >/dev/null && [ ! -f "$dir/${superseded_by}.md" ]; then
      echo "BAD-SUCCESSOR: $f superseded_by '$superseded_by' names no ADR on disk"
      fail=1
    fi
  fi
  if [ "$status" = "live" ]; then
    live_resource="$(grep -m1 '^live_resource:' "$f" | sed -E 's/^live_resource:[[:space:]]*//')"
    if [ -z "$live_resource" ]; then
      echo "NO-RESOURCE: $f is live but has no populated 'live_resource:'"
      fail=1
    fi
  fi
  return "$fail"
}

run_gate() {
  local dir="$1" bad=0
  shopt -s nullglob
  for f in "$dir"/ADR-*.md; do
    out="$(check_one "$f" "$dir")"
    if [ -n "$out" ]; then
      echo "$out"
      bad=1
    fi
  done
  shopt -u nullglob
  return "$bad"
}

# provoke NAME FILENAME CONTENT TOKEN -> a fresh, isolated dir holding only
# this one ADR; asserts the gate fails AND names the specific defect (not
# just "some file in the dir failed" — issue #1619's provoke-both-directions
# requirement, one case per defect).
provoke() {
  local name="$1" filename="$2" content="$3" token="$4" work out
  work="$(mktemp -d "${TMPDIR:-/tmp}/adrstatus.XXXXXX")" || exit 2
  printf '%s' "$content" > "$work/$filename"
  out="$(run_gate "$work" 2>&1)"
  local rc=$?
  rm -rf "$work"
  if [ "$rc" -eq 0 ]; then
    echo "SELF-TEST FAIL ($name): gate passed a file it must refuse" >&2
    return 1
  fi
  if ! grep -q "$token" <<<"$out"; then
    echo "SELF-TEST FAIL ($name): gate failed but did not name '$token'; got: $out" >&2
    return 1
  fi
  echo "self-test: $name correctly refused ($token)"
  return 0
}

self_test() {
  local bad=0 work out rc

  provoke "missing status" "ADR-9001-no-status.md" \
    '# ADR-9001: no status key
' "MISSING-STATUS" || bad=1

  provoke "out-of-vocabulary state" "ADR-9002-bad-state.md" \
    '---
id: ADR-9002
status: kinda-done
---
# ADR-9002: bad state
' "UNKNOWN-STATE" || bad=1

  provoke "orphan successor" "ADR-9003-orphan.md" \
    '---
id: ADR-9003
status: superseded
superseded_by: ADR-9999
---
# ADR-9003: orphan successor
' "BAD-SUCCESSOR" || bad=1

  provoke "superseded with no successor" "ADR-9004-no-successor.md" \
    '---
id: ADR-9004
status: superseded
---
# ADR-9004: no superseded_by at all
' "NO-SUCCESSOR" || bad=1

  provoke "live with no resource" "ADR-9005-no-resource.md" \
    '---
id: ADR-9005
status: live
---
# ADR-9005: no live_resource at all
' "NO-RESOURCE" || bad=1

  if [ "$bad" -ne 0 ]; then
    echo "self-test: FAIL — at least one negative control did not fire correctly" >&2
    return 1
  fi

  # Positive control: a clean, fully-specified set, including a superseded_by
  # that must resolve WITHIN the scanned dir -> must pass.
  work="$(mktemp -d "${TMPDIR:-/tmp}/adrstatus.XXXXXX")" || exit 2
  cat > "$work/ADR-9001-proposed.md" <<'EOF'
---
id: ADR-9001
status: proposed
---
# ADR-9001: proposed
EOF
  cat > "$work/ADR-9002-accepted.md" <<'EOF'
---
id: ADR-9002
status: accepted
---
# ADR-9002: accepted
EOF
  cat > "$work/ADR-9003-live.md" <<'EOF'
---
id: ADR-9003
status: live
live_resource: scripts/check-adr-status.sh
---
# ADR-9003: live
EOF
  cat > "$work/ADR-9004-superseded.md" <<'EOF'
---
id: ADR-9004
status: superseded
superseded_by: ADR-9003
---
# ADR-9004: superseded, valid successor
EOF
  out="$(run_gate "$work" 2>&1)"
  rc=$?
  rm -rf "$work"
  if [ "$rc" -ne 0 ]; then
    echo "SELF-TEST FAIL: a fully-specified clean set was refused: $out" >&2
    return 1
  fi
  echo "self-test: clean, fully-specified set correctly passed"

  echo "self-test: PASS (every rule provoked in isolation and verified by name)"
  return 0
}

if [ "${1:-}" = "--self-test" ]; then
  self_test
  exit $?
fi

if ! run_gate "$ADR_DIR"; then
  echo "check-adr-status: FAIL — see failures above (issue #1619)" >&2
  exit 1
fi

echo "check-adr-status: OK — every ADR carries a status from {$VALID_STATES}, superseded/live cross-refs resolve"
