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
# own report.
#
# Exit-code contract: 0 OK / 1 NOT-OK (cap exceeded and/or reaper unscheduled,
# named `worktree-cap-exceeded:<n>/<cap>` / `reaper-unscheduled`) /
# 2 CANNOT-ASSESS (`.fleet/lanes` exists but could not be read, or the
# precondition otherwise cannot be measured — printed, never treated as a
# passing or failing count).
#
# Usage:
#   bash scripts/check-worktree-cap.sh              # check the real tree
#   bash scripts/check-worktree-cap.sh --self-test   # provoke both refusals on scratch repos
set -uo pipefail

self_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

self_test=0
for arg in "$@"; do
  case "$arg" in
    --self-test) self_test=1 ;;
    *) echo "check-worktree-cap: unknown argument $arg" >&2; exit 2 ;;
  esac
done

# --- the check itself, parameterized so --self-test can run it on a scratch
#     repo without touching the real tree ------------------------------------
declared_slack() { # declared_slack <repo> — the slack value in worktree-cap.yaml, default 10
  local repo="$1" file="$repo/governance/isolation/worktree-cap.yaml" value
  if [ ! -f "$file" ]; then
    printf '10'
    return 0
  fi
  value="$(grep -E '^[[:space:]]*slack:[[:space:]]*[0-9]+[[:space:]]*$' "$file" | head -n1 | grep -oE '[0-9]+')"
  if [ -z "$value" ]; then
    printf '10'
  else
    printf '%s' "$value"
  fi
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

  slack="$(declared_slack "$repo")"
  cap=$((lanes + slack))

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

  local rc=0
  if [ "$wt_count" -gt "$cap" ]; then
    excess=$((wt_count - cap))
    echo "check-worktree-cap: NOT-OK — worktree-cap-exceeded:$wt_count/$cap" >&2
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
    rc=1
  fi
  rm -f "$claimed_file"

  local schedule_out schedule_rc reaper_script="${REAPER_SCRIPT:-$self_root/scripts/prune-worktrees.sh}"
  schedule_out="$(cd "$repo" && bash "$reaper_script" --schedule 2>&1)"
  schedule_rc=$?
  if [ "$schedule_rc" -eq 1 ]; then
    echo "check-worktree-cap: NOT-OK — reaper-unscheduled: $schedule_out" >&2
    rc=1
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
  out="$(check_cap "$over" 2>&1)"; rc=$?
  if [ "$rc" -eq 1 ] && [[ "$out" == *"worktree-cap-exceeded:"* ]]; then
    ok "worktree-cap-exceeded fires by name when worktrees exceed lanes+slack"
  else
    bad "worktree-cap-exceeded did not fire (rc=$rc): $out"
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
  if [[ "$out3" == *"reaper-unscheduled"* ]]; then
    ok "reaper-unscheduled fires by name when nothing installs prune-worktrees.sh"
  else
    bad "reaper-unscheduled did not fire: $out3"
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
