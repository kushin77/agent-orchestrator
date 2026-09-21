#!/usr/bin/env bash
# check-worktree-cap.sh — live worktrees must not exceed lanes in flight (#1265).
#
# "The reaper handles worktrees" was the doctrine; measured 2026-09-18: 155
# worktrees on the box (105 under ~/ao-worktrees, 19 subagent) against ~20
# lanes actually in flight, and a nightly reaper that never ran (#830's
# `--schedule` answer was NOT-SCHEDULED the whole time). A reaper that runs is
# necessary but not sufficient — this gate is the number itself: the cap is
# `open lane records + declared slack` (governance/isolation/worktree-cap.yaml,
# default slack 10), and it also asks the reaper's own `--schedule` question so
# an unscheduled reaper is a finding here too, not just inside prune-worktrees'
# own report. worktree-cap.yaml may also declare a dated `ratchet` (measured on
# a specific host, honoured only until it expires) that raises the cap while
# the pile is still shrinking towards the declared `slack`; an active ratchet
# is printed as a NOTE (`worktree-cap-ratchet:<n>`), never silently.
#
# Exit-code contract: 0 OK / 1 NOT-OK (cap exceeded and/or reaper unscheduled,
# named `worktree-cap-exceeded:<n>/<cap>` / `reaper-unscheduled`) /
# 2 CANNOT-ASSESS (`.fleet/lanes` exists but could not be read, or the
# precondition otherwise cannot be measured — printed, never treated as a
# passing or failing count). Both box-wide findings (worktree-cap-exceeded,
# and since #1673 reaper-unscheduled too) are advisory NOTEs, not NOT-OK, in
# the default lane venue; AO_GATE_VENUE=attestation still reds on them by
# name (#1620).
#
# Usage:
#   bash scripts/check-worktree-cap.sh              # check the real tree
#   bash scripts/check-worktree-cap.sh --self-test   # provoke both refusals on scratch repos
#
# ---knowledge---
# module_id: scripts.check-worktree-cap
# system: governance
# app: gates
# solution_class: enterprise
# patterns: [tri-state-exit, provoked-negative-control, self-proving-gate, named-refusal, lane-isolation, deterministic]
# derives_from: null
# owner_sme: platform-sme
# tier: L1
# interfaces: [exit 0 OK, exit 1 NOT-OK, exit 2 CANNOT-ASSESS]
# invariants: ""
# gotchas: ""
# related: ["#830", "#1265", "#1620", "#1673"]
# do_not_duplicate: null
# ---knowledge---
set -uo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
self_root="$(find_repo_root)"

# #1620: AO_GATE_VENUE distinguishes a per-lane `make verify` (default —
# box-wide, concurrency-sensitive counts are advisory) from the serial
# post-merge master-attestation run (AO_GATE_VENUE=attestation — the same
# counts are enforced for real). Read per-call (inside check_cap), not once
# here, so a self-test can exercise both venues in the same process. See
# governance/reconcile/real_tree_baseline.py and check-reconcile-orphans.sh
# for the same distinction.

self_test=0
for arg in "$@"; do
  case "$arg" in
    --self-test) self_test=1 ;;
    *) echo "check-worktree-cap: unknown argument $arg" >&2; exit 2 ;;
  esac
done

# --- the check itself, parameterized so --self-test can run it on a scratch
#     repo without touching the real tree ------------------------------------
cap_config() { # cap_config <repo> — prints "SLACK|RATCHET_MEASURED|RATCHET_EXPIRES"
                # (the last two blank when no ratchet is declared, or malformed)
  local repo="$1" file="$repo/governance/isolation/worktree-cap.yaml"
  if [ ! -f "$file" ]; then
    printf '10||'
    return 0
  fi
  python3 - "$file" <<'PY'
import sys
try:
    import yaml
except ImportError:
    print("10||")
    raise SystemExit(0)

try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
except (OSError, yaml.YAMLError):
    data = {}

try:
    slack = int(data.get("slack", 10))
except (TypeError, ValueError):
    slack = 10

ratchet = data.get("ratchet") or {}
measured = ratchet.get("measured", "")
expires = ratchet.get("expires", "")
try:
    measured = int(measured) if measured != "" else ""
except (TypeError, ValueError):
    measured = ""
if not isinstance(expires, str):
    expires = str(expires) if expires not in (None, "") else ""

print(f"{slack}|{measured}|{expires}")
PY
}

fleet_dir_for() { # fleet_dir_for <repo> — .fleet/ lives beside the MAIN checkout's git dir,
                   # never inside a linked worktree (mirrors prune-worktrees.sh's own lookup)
  local repo="$1" common
  common="$(git -C "$repo" rev-parse --git-common-dir 2>/dev/null)" || return 1
  case "$common" in
    /*) : ;;
    *) common="$repo/$common" ;;
  esac
  printf '%s/.fleet' "$(cd "$common/.." && pwd)"
}

open_lane_count() { # open_lane_count <fleet_dir> — number of .fleet/lanes/*.json records
  local fleet="$1" dir="$fleet/lanes" count
  if [ ! -d "$dir" ]; then
    printf '0'
    return 0
  fi
  if ! count="$(find "$dir" -maxdepth 1 -name '*.json' 2>/dev/null | wc -l | tr -d ' ')"; then
    return 1
  fi
  # A directory that exists but cannot actually be listed (permissions) is the
  # CANNOT-ASSESS case, not zero — `find` above would already have failed for
  # that, but a belt-and-braces readability probe makes the refusal explicit.
  if ! ls "$dir" >/dev/null 2>&1; then
    return 1
  fi
  printf '%s' "$count"
}

lane_worktree_paths() { # lane_worktree_paths <fleet_dir> — the "worktree" field of every open lane record
  local fleet="$1" dir="$fleet/lanes"
  [ -d "$dir" ] || return 0
  python3 - "$dir" <<'PY'
import json
import sys
from pathlib import Path

for record in sorted(Path(sys.argv[1]).glob("*.json")):
    try:
        data = json.loads(record.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        continue
    worktree = data.get("worktree")
    if worktree:
        print(worktree)
PY
}

check_cap() { # check_cap <repo> — prints findings, returns 0/1/2 per the contract above
  local repo="$1" fleet lanes cap wt_list wt_count excess claimed_file oldest
  fleet="$(fleet_dir_for "$repo")" || { echo "check-worktree-cap: CANNOT-ASSESS — could not resolve $repo's .fleet/ directory" >&2; return 2; }

  lanes="$(open_lane_count "$fleet")"
  if [ $? -ne 0 ] || [ -z "$lanes" ]; then
    echo "check-worktree-cap: CANNOT-ASSESS — $fleet/lanes exists but could not be read" >&2
    return 2
  fi

  local cap_line slack ratchet_measured ratchet_expires
  cap_line="$(cap_config "$repo")"
  IFS='|' read -r slack ratchet_measured ratchet_expires <<< "$cap_line"
  [ -z "$slack" ] && slack=10
  cap=$((lanes + slack))

  # --- temporary ratchet (issue #1265 comment 3) -------------------------------
  #
  # `slack` is the TARGET, but the box was not there yet the day this gate
  # shipped. `ratchet.measured` is what was left on the declaring host after
  # ITS OWN reaper `--apply` (only provably-dead worktrees) — never renewed
  # here, never re-measured by this script, and only honoured until
  # `ratchet.expires`. After that date the declared `slack` is the only cap
  # again, on purpose: an expired ratchet must not quietly re-excuse the same
  # pile forever.
  local ratchet_active=0 today
  today="$(date -u +%Y-%m-%d)"
  if [ -n "$ratchet_measured" ] && [ -n "$ratchet_expires" ] && [[ ! "$today" > "$ratchet_expires" ]]; then
    local extra ratchet_cap
    extra=$((ratchet_measured - lanes))
    [ "$extra" -lt "$slack" ] && extra="$slack"
    ratchet_cap=$((lanes + extra))
    if [ "$ratchet_cap" -gt "$cap" ]; then
      cap="$ratchet_cap"
      ratchet_active=1
    fi
  fi

  wt_list="$(git -C "$repo" worktree list --porcelain | awk '/^worktree /{print $2}')"
  # The main checkout itself is not a lane worktree. `--show-toplevel` answers
  # for whichever worktree $repo HAPPENS to be (this could be a linked lane
  # worktree itself), so it is the wrong path here: the main checkout is the
  # parent of the shared `.git` directory every worktree points back to
  # (mirrors prune-worktrees.sh's own `fleet_dir_for`-style lookup).
  main_common="$(git -C "$repo" rev-parse --git-common-dir 2>/dev/null)"
  case "$main_common" in
    /*) : ;;
    *) main_common="$repo/$main_common" ;;
  esac
  main_wt="$(cd "$main_common/.." && pwd)"
  wt_count=0
  claimed_file="$(mktemp)"
  lane_worktree_paths "$fleet" > "$claimed_file" 2>/dev/null || true
  while IFS= read -r wt; do
    [ -z "$wt" ] && continue
    [ "$wt" = "$main_wt" ] && continue
    wt_count=$((wt_count + 1))
  done <<< "$wt_list"

  echo "check-worktree-cap: $wt_count worktree(s), $lanes open lane record(s), slack $slack, cap $cap"
  if [ "$ratchet_active" -eq 1 ]; then
    echo "check-worktree-cap: NOTE worktree-cap-ratchet:$ratchet_measured (measured on $ratchet_expires-bounded ratchet; target slack is $slack)"
  fi

  local rc=0
  local venue="${AO_GATE_VENUE:-lane}"
  if [ "$wt_count" -gt "$cap" ]; then
    excess=$((wt_count - cap))
    # #1620: the count is box-wide state — every concurrent session's
    # worktrees, not just this one's — so it is non-deterministic under
    # concurrent load. Blocking on it in a LANE venue (a PR's own `make
    # verify`) means an unrelated diff can fail purely on fleet timing;
    # the MASTER-ATTESTATION venue (AO_GATE_VENUE=attestation, run serially
    # post-merge) still enforces it for real.
    if [ "$venue" = "attestation" ]; then
      echo "check-worktree-cap: NOT-OK — worktree-cap-exceeded:$wt_count/$cap" >&2
      rc=1
    else
      echo "check-worktree-cap: NOTE — worktree-cap-exceeded:$wt_count/$cap (advisory in lane venue, #1620; blocking in AO_GATE_VENUE=attestation)" >&2
    fi
    echo "check-worktree-cap: oldest unexplained worktree(s) (excess $excess):" >&2
    # Oldest-first by mtime of the worktree's own .git file/dir, naming ones
    # that are NOT claimed by an open lane record — those are "explained".
    while IFS= read -r wt; do
      [ -z "$wt" ] && continue
      [ -e "$wt/.git" ] && stat -c '%Y %n' "$wt/.git" 2>/dev/null
    done <<< "$wt_list" | sort -n | while read -r _ wtpath; do
      wtpath="${wtpath%/.git}"
      [ "$wtpath" = "$main_wt" ] && continue
      grep -qxF -- "$wtpath" "$claimed_file" 2>/dev/null && continue
      printf '  %s\n' "$wtpath" >&2
    done
  fi
  rm -f "$claimed_file"

  local schedule_out schedule_rc reaper_script="${REAPER_SCRIPT:-$self_root/scripts/prune-worktrees.sh}"
  schedule_out="$(cd "$repo" && bash "$reaper_script" --schedule 2>&1)"
  schedule_rc=$?
  if [ "$schedule_rc" -eq 1 ]; then
    # #1673: box-wide scheduler state (crontab presence), same class as
    # worktree-cap-exceeded above — a lane's own `make verify` cannot fix a
    # missing crontab line, so it is advisory (NOTE) in the default lane
    # venue and still enforced for real in AO_GATE_VENUE=attestation.
    if [ "$venue" = "attestation" ]; then
      echo "check-worktree-cap: NOT-OK — reaper-unscheduled: $schedule_out" >&2
      rc=1
    else
      echo "check-worktree-cap: NOTE — reaper-unscheduled: $schedule_out (advisory in lane venue, #1673; blocking in AO_GATE_VENUE=attestation)" >&2
    fi
  elif [ "$schedule_rc" -eq 2 ]; then
    echo "check-worktree-cap: CANNOT-ASSESS — reaper schedule could not be determined: $schedule_out" >&2
    [ "$rc" -eq 0 ] && rc=2
  fi
  return "$rc"
}

# --- --self-test: provoke both refusals by name, on scratch repos -----------
self_test() {
  local work fails=0
  work="$(mktemp -d /tmp/ao1265-cap.XXXXXX)"

  ok() { printf '  OK    %s\n' "$1"; }
  bad() { printf '  FAIL  %s\n' "$1"; fails=$((fails + 1)); }

  # --- worktree-cap-exceeded --------------------------------------------------
  local over="$work/over"
  git init -q "$over"
  git -C "$over" config user.email t@example.com
  git -C "$over" config user.name t
  git -C "$over" commit -q --allow-empty -m seed
  mkdir -p "$over/.fleet/lanes"
  printf '{"worktree": "%s"}\n' "$over/.wt-a" > "$over/.fleet/lanes/a.json"
  mkdir -p "$over/governance/isolation"
  printf 'slack: 1\n' > "$over/governance/isolation/worktree-cap.yaml"
  for i in 1 2 3 4; do
    git -C "$over" worktree add -q --detach "$over/.wt-$i" master >/dev/null 2>&1 || true
  done
  # Text, not rc, is what proves the downgrade here: `check_cap` below runs
  # in the default lane venue, so both worktree-cap-exceeded AND
  # reaper-unscheduled (#1673) must appear as NOTE, not NOT-OK — that must
  # not be conflated with whether either finding fires at all.
  out="$(check_cap "$over" 2>&1)"
  if [[ "$out" == *"NOTE — worktree-cap-exceeded:"* ]] && [[ "$out" != *"NOT-OK — worktree-cap-exceeded:"* ]]; then
    ok "worktree-cap-exceeded is advisory (NOTE, not NOT-OK) in the default lane venue (#1620)"
  else
    bad "worktree-cap-exceeded was not advisory in the lane venue: $out"
  fi
  outa="$(AO_GATE_VENUE=attestation check_cap "$over" 2>&1)"; rca=$?
  if [ "$rca" -eq 1 ] && [[ "$outa" == *"NOT-OK — worktree-cap-exceeded:"* ]]; then
    ok "worktree-cap-exceeded still fires by name (rc=1) in AO_GATE_VENUE=attestation (#1620)"
  else
    bad "worktree-cap-exceeded did not fire in the attestation venue (rc=$rca): $outa"
  fi

  # --- CANNOT-ASSESS: .fleet/lanes unreadable --------------------------------
  local unreadable="$work/unreadable"
  git init -q "$unreadable"
  git -C "$unreadable" config user.email t@example.com
  git -C "$unreadable" config user.name t
  git -C "$unreadable" commit -q --allow-empty -m seed
  mkdir -p "$unreadable/.fleet/lanes"
  chmod 000 "$unreadable/.fleet/lanes"
  out2="$(check_cap "$unreadable" 2>&1)"; rc2=$?
  chmod 755 "$unreadable/.fleet/lanes"
  if [ "$rc2" -eq 2 ]; then
    ok "an unreadable .fleet/lanes is CANNOT-ASSESS (rc=2), never a passing count"
  else
    bad "an unreadable .fleet/lanes did not report CANNOT-ASSESS (rc=$rc2): $out2"
  fi

  # --- reaper-unscheduled ------------------------------------------------------
  local unsched="$work/unsched"
  git init -q "$unsched"
  git -C "$unsched" config user.email t@example.com
  git -C "$unsched" config user.name t
  git -C "$unsched" commit -q --allow-empty -m seed
  # A copy under a basename nothing schedules — the real crontab on this box
  # legitimately schedules prune-worktrees.sh itself, so testing THAT path
  # would only ever prove SCHEDULED regardless of the repo under test. This
  # exercises the identical --schedule logic under a name no crontab line can
  # match, which is exactly what "nothing installs this tool" looks like.
  cp "$self_root/scripts/prune-worktrees.sh" "$work/prune-worktrees-unscheduled-double.sh"
  out3="$(REAPER_SCRIPT="$work/prune-worktrees-unscheduled-double.sh" check_cap "$unsched" 2>&1)"; rc3=$?
  if [[ "$out3" == *"NOTE — reaper-unscheduled:"* ]] && [[ "$out3" != *"NOT-OK — reaper-unscheduled:"* ]]; then
    ok "reaper-unscheduled is advisory (NOTE, not NOT-OK) in the default lane venue (#1673)"
  else
    bad "reaper-unscheduled was not advisory in the lane venue: $out3"
  fi
  out3a="$(AO_GATE_VENUE=attestation REAPER_SCRIPT="$work/prune-worktrees-unscheduled-double.sh" check_cap "$unsched" 2>&1)"; rc3a=$?
  if [ "$rc3a" -eq 1 ] && [[ "$out3a" == *"NOT-OK — reaper-unscheduled:"* ]]; then
    ok "reaper-unscheduled still fires by name (rc=1) in AO_GATE_VENUE=attestation (#1673)"
  else
    bad "reaper-unscheduled did not fire in the attestation venue (rc=$rc3a): $out3a"
  fi

  # --- ratchet: live, honoured, NOTE ------------------------------------------
  local live_ratchet="$work/live-ratchet"
  git init -q "$live_ratchet"
  git -C "$live_ratchet" config user.email t@example.com
  git -C "$live_ratchet" config user.name t
  git -C "$live_ratchet" commit -q --allow-empty -m seed
  mkdir -p "$live_ratchet/.fleet/lanes" "$live_ratchet/governance/isolation"
  printf '{"worktree": "%s"}\n' "$live_ratchet/.wt-a" > "$live_ratchet/.fleet/lanes/a.json"
  cat > "$live_ratchet/governance/isolation/worktree-cap.yaml" <<'YAML'
slack: 1
ratchet:
  measured: 4
  host: self-test
  expires: "2999-01-01"
YAML
  for i in 1 2 3 4; do
    git -C "$live_ratchet" worktree add -q --detach "$live_ratchet/.wt-$i" master >/dev/null 2>&1 || true
  done
  out4="$(check_cap "$live_ratchet" 2>&1)"; rc4=$?
  if [ "$rc4" -eq 0 ] && [[ "$out4" == *"worktree-cap-ratchet:4"* ]]; then
    ok "a live (unexpired) ratchet raises the cap and prints worktree-cap-ratchet:<n> as a NOTE"
  else
    bad "a live ratchet did not hold, or did not print the NOTE by name (rc=$rc4): $out4"
  fi

  # --- ratchet: expired, real cap bites, reds ---------------------------------
  local expired_ratchet="$work/expired-ratchet"
  git init -q "$expired_ratchet"
  git -C "$expired_ratchet" config user.email t@example.com
  git -C "$expired_ratchet" config user.name t
  git -C "$expired_ratchet" commit -q --allow-empty -m seed
  mkdir -p "$expired_ratchet/.fleet/lanes" "$expired_ratchet/governance/isolation"
  printf '{"worktree": "%s"}\n' "$expired_ratchet/.wt-a" > "$expired_ratchet/.fleet/lanes/a.json"
  cat > "$expired_ratchet/governance/isolation/worktree-cap.yaml" <<'YAML'
slack: 1
ratchet:
  measured: 4
  host: self-test
  expires: "2000-01-01"
YAML
  for i in 1 2 3 4; do
    git -C "$expired_ratchet" worktree add -q --detach "$expired_ratchet/.wt-$i" master >/dev/null 2>&1 || true
  done
  out5="$(AO_GATE_VENUE=attestation check_cap "$expired_ratchet" 2>&1)"; rc5=$?
  if [ "$rc5" -eq 1 ] && [[ "$out5" == *"worktree-cap-exceeded:"* ]] && [[ "$out5" != *"worktree-cap-ratchet:"* ]]; then
    ok "an expired ratchet is ignored — the declared slack is the only cap, and it reds (attestation venue)"
  else
    bad "an expired ratchet still excused the pile (rc=$rc5): $out5"
  fi

  local result=0
  if [ "$fails" -eq 0 ]; then
    echo "check-worktree-cap: --self-test OK — both refusals fire by name on scratch repos"
  else
    echo "check-worktree-cap: --self-test NOT-OK — $fails control(s) failed" >&2
    result=1
  fi
  rm -rf "$work"
  return "$result"
}

if [ "$self_test" -eq 1 ]; then
  self_test
  exit $?
fi

root="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [ -z "$root" ]; then
  echo "check-worktree-cap: CANNOT-ASSESS — not inside a git repository" >&2
  exit 2
fi
check_cap "$root"
exit $?
