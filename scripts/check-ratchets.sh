#!/usr/bin/env bash
# check-ratchets.sh — every dated ratchet in the tree is watched: expiry is a
# gate, not a date nobody reads (#1414, parent #1268).
#
# THE DEFECT THIS EXISTS FOR
#   `governance/isolation/worktree-cap.yaml` (ratchet measured 90, expires
#   2026-10-02, #1335) and `governance/reconcile/orphan-budget.yaml` (the
#   2026-09-18 walk's counts, same expiry, #1385) are both TEMPORARY: each
#   excuses a pile that was on the box the day it landed, and each is read for
#   its `expires` by its own consumer — `check-worktree-cap.sh` stops honouring
#   the ratchet the day after, `orphans.load_budget` drops every kind to 0 after
#   it. So on 2026-10-02 both stop excusing anything, and NOTHING SAYS SO. The
#   defect is not the expiry; it is the silence. Whichever gate reds first that
#   day reds with a message about a COUNT, never about a deadline nobody read.
#   This gate is that deadline, said out loud — for every dated ratchet in the
#   tree, not just the two that happened to be on the box when it landed.
#
# THE CLASS, AND HOW IT IS FOUND (never a hand-kept list)
#   A "dated ratchet" is any `expires` KEY in a machine-readable declaration:
#   `*.yaml`, `*.yml`, `*.json` under the root, excluding `.git/`, `vendor/`,
#   `.research/`, `node_modules/` and test-fixture directories (`tests/`,
#   `fixtures/` — REPORTED when skipped, never a silent hole). The scan is a
#   real YAML parse (JSON is a YAML subset), so a COMMENTED-OUT `expires` — the
#   shape `governance/board/exceptions.yaml` carries as its example — is not a
#   declaration, and a JSON `"expires":` key IS one. Every mapping that carries
#   the key is a finding, wherever it is nested, reported by MAPPING PATH
#   (`ratchet.expires`, `exceptions[0].expires`, ...), which is exact where a
#   line number would be a guess.
#   A cheap prefilter (does the file text contain the literal token `expires`?)
#   bounds the parse; a key must appear literally, so the prefilter cannot miss
#   a declaration, and the number of files it rejected is printed by name-count
#   below so the scope is visible rather than assumed.
#
# WHICH FILES ARE READ — the list comes from GIT, never from a bare walk
#   Where the root is a git work tree the candidates are
#   `git ls-files --cached --others --exclude-standard -- '*.yaml' '*.yml'
#   '*.json'`: the idiom the sibling checks use (`check-verdict-contains.sh`,
#   `check-shell-patterns.sh`, `check-docs.sh`), i.e. tracked files PLUS
#   untracked-not-ignored ones, so a lane's just-written declaration is seen
#   while gitignored runtime state is not. A filesystem walk is used ONLY for a
#   root that is not a work tree (the `--root` fixture seam), and the report
#   names which of the two ran, because a scope nobody states is a scope nobody
#   can check. This is load-bearing rather than stylistic, and it was measured:
#   the default root is the checkout `make verify` runs in, and on this box that
#   is the MAIN checkout, where a walk descends into `.claude/worktrees/agent-*`
#   — 20,630 data files against the 477 git records — so the gate took its two
#   findings from OTHER LANES' worktrees (peer copies still saying `measured=90`,
#   `renewed_at=none`) and never read the checked-out repo's own ratchets. Git
#   collapses a nested worktree to a single directory entry, so the git list is
#   this repo's own two declarations and nothing else.
#
# THE RULE (from #1414's Do)
#   "every dated ratchet/budget in the tree (grep `expires:`) must be >= 3 days
#    from expiry or carry a `renewed_at`; expired -> red naming the file; 7 days
#    before expiry -> NOTE"
#   Four zones, and every message names the file it came from:
#     * `expired`        (expiry in the past, clock > expires)      -> RED
#     * `expiry-reached` (expiry is TODAY, clock == expires)        -> RED
#       The consumer gates honour a declaration FOR its last day, so this arm is
#       the one day of notice left: re-measure, bump `expires`, or the allowance
#       reverts at the next tick.
#     * `renewal-window` (1..<floor> days left, no `renewed_at`)    -> RED
#       This is the ">= 3 days from expiry OR carry a `renewed_at`" half of the
#       rule. Without it the `or` clause is unenforced and a renewal one day
#       before the cliff — the last-minute renewal the clause exists to forbid —
#       would pass silently.
#     * `approaching`    (floor..<= 7 days left, or <= 7 with a renewal) -> NOTE
#       A NOTE never reds: rc stays 0. 2026-09-29 is exactly 3 days from the
#       two ratchets' expiry, and this gate's acceptance is that it NOTEs both
#       that day (tested in --self-test below).
#   A RENEWAL IS A PAIR: `renewed_at` records that the number was re-measured
#   and `expires` MOVES. `renewed_at` alone cannot resurrect a date that has
#   arrived — a "renewal" that leaves `expires` in the past is red for the same
#   reason an unreadable date is: it is a claim the declaration does not carry.
#   FAIL CLOSED ON AN UNREADABLE DATE: a malformed `expires` is RED, not a skip.
#   The consumers compare `expires` as a STRING (`[[ ! "$today" > "$expires" ]]`),
#   so a garbage date can keep honouring a ratchet while looking declared — the
#   one shape that must never pass quietly.
#
# EXIT-CODE CONTRACT (matches scripts/verify.sh's 0/1/2 tri-state)
#   0 OK / NOTE      — nothing is at or inside the floor
#   1 NOT-OK         — at least one dated ratchet is expired, at its expiry, or
#                      inside the floor unrenewed; the finding names the FILE
#   2 CANNOT-ASSESS  — no python3/PyYAML, an unreadable root, a malformed
#                      --clock, or a data file that carries the token but cannot
#                      be read as a declaration. Never recorded as a pass.
#
# SEAMS (so the clock and the tree can be pinned for an offline provocation):
#   --root <dir>     | RATCHET_ROOT   the tree to scan (default: git toplevel)
#   --now <ISO-8601> | RATCHET_NOW    the instant to measure from (default:
#   --clock <date>   | RATCHET_CLOCK  today, UTC). `--now` is the flag #1414
#                                     names; `--clock` is the same seam under
#                                     the name this check first shipped with,
#                                     kept as an alias so no caller breaks.
#   --self-test                       provoke every arm on scratch fixtures
#
# Usage:
#   bash scripts/check-ratchets.sh
#   bash scripts/check-ratchets.sh --now 2026-10-02
#   bash scripts/check-ratchets.sh --root <dir> --clock 2026-10-02
#   bash scripts/check-ratchets.sh --self-test
#
# ---knowledge---
# module_id: scripts.check-ratchets
# system: governance
# app: gates
# solution_class: enterprise
# patterns: [tri-state-exit, provoked-negative-control, self-proving-gate, offline-hermetic, lane-isolation]
# derives_from: null
# owner_sme: qa-sme
# tier: L1
# interfaces: [exit 0 OK, exit 1 NOT-OK, exit 2 CANNOT-ASSESS]
# invariants: ""
# gotchas: ""
# related: ["#1268", "#1335", "#1385", "#1414"]
# do_not_duplicate: null
# ---knowledge---
set -uo pipefail

#: The two boundaries of the rule, declared once and printed in every summary,
#: so a reader can see which side of which line a finding fell on.
window_days=7 # <= this many days to expiry        -> NOTE (rc 0)
floor_days=3  # <  this many days, unrenewed        -> RED  (rc 1)

self_test=0
root="${RATCHET_ROOT:-}"
clock="${RATCHET_CLOCK:-${RATCHET_NOW:-}}"

bad_arg() {
  printf 'check-ratchets: unknown or incomplete argument %s\n' "$1" >&2
  printf 'usage: check-ratchets.sh [--root DIR] [--now YYYY-MM-DD] [--clock YYYY-MM-DD] [--self-test]\n' >&2
  exit 2
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --root)
      [ "$#" -ge 2 ] || bad_arg "--root"
      root="$2"
      shift 2
      ;;
    --now | --clock)
      # `--now` is the seam #1414 names; `--clock` is the same one under the name
      # this check first shipped with, kept so a caller is never broken by it.
      [ "$#" -ge 2 ] || bad_arg "$1"
      clock="$2"
      shift 2
      ;;
    --self-test)
      self_test=1
      shift
      ;;
    *) bad_arg "$1" ;;
  esac
done

# --- the candidate list: derived from GIT, not from a filesystem walk ---------
# The idiom is the siblings' (`check-verdict-contains.sh`, `check-shell-patterns.sh`,
# `check-docs.sh`): `--cached` so a tracked declaration is seen, `--others
# --exclude-standard` so a lane's not-yet-committed declaration is seen too while
# gitignored runtime state is not. A filesystem walk is reserved for a root that
# is not a work tree (the `--root` fixture seam), and the caller prints which of
# the two ran. Measured, and why this is not a style preference: the default root
# is the checkout `make verify` runs in, and a walk there descends into
# `.claude/worktrees/agent-*` — 20,630 data files against the 477 git records —
# so the verdict was decided by OTHER LANES' worktrees (peer copies still saying
# `measured=90`, `renewed_at=none`) and the checked-out repo's own ratchets were
# never read. Git collapses a nested worktree to one directory entry.
ratchet_file_list() { # ratchet_file_list <root> <list-out> -> the scope, on stdout
  local root="$1" out="$2" rc
  if [ -e "$root/.git" ]; then
    git -C "$root" ls-files --cached --others --exclude-standard \
      -- '*.yaml' '*.yml' '*.json' > "$out" 2>/dev/null
    rc=$?
    if [ "$rc" -ne 0 ]; then
      printf 'the candidate list is unknown — git ls-files failed for %s (rc=%d), so the class cannot be covered' \
        "$root" "$rc"
      return 2
    fi
    printf '%s' 'git ls-files --cached --others --exclude-standard'
    return 0
  fi
  : > "$out"
  printf '%s' 'filesystem walk (this root is not a git work tree)'
  return 0
}

# --- the check itself, parameterized so --self-test can run it on a fixture ---
core() { # core <root> <clock> — prints findings, returns 0 / 1 / 2
  local scan_root="$1" moment="$2" out rc scope list
  list="$(mktemp "${TMPDIR:-/tmp}/check-ratchets-list.XXXXXX")" || {
    printf 'check-ratchets: CANNOT-ASSESS — cannot create a scratch candidate list\n'
    return 2
  }
  scope="$(ratchet_file_list "$scan_root" "$list")"
  rc=$?
  if [ "$rc" -ne 0 ]; then
    printf 'check-ratchets: CANNOT-ASSESS — %s\n' "$scope"
    rm -f "$list"
    return 2
  fi
  out="$(python3 - "$scan_root" "$moment" "$window_days" "$floor_days" "$list" "$scope" <<'PY' 2>&1
"""Every dated ratchet under <root>, held to the floor and the window.

argv: root, clock (ISO date), window days, floor days, candidate-list file
      (a `git ls-files` listing when the root is a work tree; empty otherwise),
      and the scope label to report.
Exit: 0 (live/approaching only), 1 (expired / expiry-reached / renewal-window /
unreadable date), 2 (could not assess at all -- never a pass).
"""

import datetime
import os
import sys
from pathlib import Path

SUFFIXES = (".yaml", ".yml", ".json")
SKIP_DIRS = {".git", ".research", "vendor", "node_modules", "__pycache__", ".venv"}
FIXTURE_DIRS = {"tests", "fixtures"}
TOKEN = "expires"
RENEWAL_KEY = "renewed_at"
MEASUREMENT_KEYS = ("measured", "measured_count", "measured_at")

PREFIX = "check-ratchets: "


def say(line):
    print(PREFIX + line)


def data_files_under(directory):
    """Every candidate declaration file, in a stable order."""
    for dirpath, dirnames, filenames in os.walk(directory):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            if name.endswith(SUFFIXES):
                yield Path(dirpath) / name


def declarations(node, path):
    """(mapping path, mapping) for every mapping in the document carrying `expires`."""
    if isinstance(node, dict):
        for key, value in node.items():
            key = str(key)
            if key == TOKEN:
                yield path + [key], node
            else:
                yield from declarations(value, path + [key])
    elif isinstance(node, list):
        for index, item in enumerate(node):
            label = "%s[%d]" % (path[-1], index) if path else "[%d]" % index
            yield from declarations(item, path[:-1] + [label] if path else [label])


def as_date(value):
    """An ISO date from a str/date/datetime, or None when it is not one."""
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    if isinstance(value, str):
        try:
            return datetime.date.fromisoformat(value.strip())
        except ValueError:
            return None
    return None


def measurement_of(mapping):
    """A one-field rendering of the measurement that justifies the number."""
    for key in MEASUREMENT_KEYS:
        if key in mapping:
            value = mapping[key]
            if isinstance(value, dict):
                recorded = value.get("date") or value.get("renewed_at") or "recorded"
                return "%s(date=%s)" % (key, recorded)
            return "%s=%s" % (key, value)
    if "budget" in mapping and isinstance(mapping["budget"], dict):
        return "budget=%d kind(s)" % len(mapping["budget"])
    return "MISSING"


def main():
    root = Path(sys.argv[1])
    window = int(sys.argv[3])
    floor = int(sys.argv[4])
    listing = sys.argv[5]
    scope = sys.argv[6]

    try:
        clock = datetime.date.fromisoformat(sys.argv[2])
    except ValueError:
        say(
            "CANNOT-ASSESS — the clock %r is not an ISO date (YYYY-MM-DD); nothing was "
            "measured and that is never a pass" % sys.argv[2]
        )
        return 2

    if not root.is_dir():
        say("CANNOT-ASSESS — %s is not a directory; nothing was scanned and that is never a pass" % root)
        return 2

    try:
        import yaml
    except ImportError:
        say("CANNOT-ASSESS — PyYAML is not importable, so no declaration can be read (never a pass)")
        return 2

    # The candidates arrive from the shell, which derived them from GIT whenever
    # the root is a work tree and says so in `scope` (printed below, so the
    # derivation is visible rather than assumed). Only a fixture root that is not
    # a repository is walked, which is the one case where a walk is the truth.
    if scope.startswith("git ls-files"):
        try:
            candidates = [
                root / line.strip()
                for line in Path(listing).read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        except OSError as exc:
            say(
                "CANNOT-ASSESS — the `git ls-files` candidate list could not be read (%s); "
                "nothing was scanned and that is never a pass" % exc
            )
            return 2
    else:
        candidates = list(data_files_under(root))

    scanned = 0
    with_token = 0
    fixtures = []
    unreadable = []
    found = []
    for path in candidates:
        try:
            relative = path.relative_to(root)
        except ValueError:
            continue
        if not path.is_file():
            continue
        if set(relative.parts[:-1]) & FIXTURE_DIRS:
            fixtures.append(str(relative))
            continue
        scanned += 1
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            unreadable.append("%s (%s)" % (relative, exc))
            continue
        if TOKEN not in text:
            continue
        with_token += 1
        try:
            document = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            unreadable.append("%s (%s)" % (relative, str(exc).splitlines()[0]))
            continue
        for mapping_path, mapping in declarations(document, []):
            found.append((str(relative), ".".join(mapping_path), mapping))

    say(
        "clock=%s root=%s scope=%s scanned=%d data file(s) (%d carrying the `%s` token), "
        "dated declaration(s)=%d, window=%dd floor=%dd"
        % (clock.isoformat(), root, scope, scanned, with_token, TOKEN, len(found), window, floor)
    )
    if fixtures:
        shown = ", ".join(fixtures[:5]) + (" (+%d more)" % (len(fixtures) - 5) if len(fixtures) > 5 else "")
        say(
            "NOTE  skipped %d test-fixture path(s) under a %s/ directory (declared out of scope, "
            "never a silent hole): %s"
            % (len(fixtures), " or ".join(sorted(FIXTURE_DIRS)), shown)
        )

    if unreadable:
        for item in unreadable:
            say("CANNOT-ASSESS — %s carries the `%s` token but could not be read as a declaration" % (item, TOKEN))
        say("CANNOT-ASSESS — the class could not be covered; that is never a pass")
        return 2

    if not found:
        say(
            "NOTE  no dated ratchet found under %s — nothing in the tree is date-bounded today "
            "(scanned %d data file(s), %d carrying the token)" % (root, scanned, with_token)
        )
        return 0

    reds = []
    notes = 0
    for name, label, mapping in sorted(found, key=lambda item: (item[0], item[1])):
        raw = mapping.get(TOKEN)
        renewal = mapping.get(RENEWAL_KEY)
        where = "%s %s" % (name, label)
        measured = measurement_of(mapping)

        expires = as_date(raw)
        if expires is None:
            reds.append(where)
            say(
                "NOT-OK ratchet-unreadable-date: %s %s=%r — an unparseable `expires` fails CLOSED: "
                "the consumers compare it as a string, so a garbage date can keep honouring a "
                "ratchet while looking declared" % (name, label, raw)
            )
            continue
        renewed = None
        if renewal is not None:
            renewed = as_date(renewal)
            if renewed is None:
                reds.append(where)
                say(
                    "NOT-OK ratchet-unreadable-date: %s=%r — a `%s` that cannot be parsed is a "
                    "renewal claim the declaration does not carry" % (label, renewal, RENEWAL_KEY)
                )
                continue
        if renewed is not None and renewed > expires:
            say(
                "NOT-OK ratchet-renewal-after-expiry: %s %s=%s %s=%s — the renewal is dated after "
                "the date it renews" % (name, label, expires.isoformat(), RENEWAL_KEY, renewed.isoformat())
            )
            reds.append(where)
            continue

        days = (expires - clock).days
        renewal_note = RENEWAL_KEY + "=" + renewed.isoformat() if renewed is not None else RENEWAL_KEY + "=none"
        shape = "%s %s=%s (%d day(s) to expiry, %s, measurement=%s)" % (
            name, label, expires.isoformat(), days, renewal_note, measured
        )

        if days <= 0:
            state = "expired" if days < 0 else "expiry-reached"
            reds.append(where)
            if days < 0:
                advice = (
                    "it lapsed %d day(s) ago and excuses nothing today — re-measure the number, "
                    "tighten it where the pile shrank, and bump `%s` (or retire the ratchet)"
                    % (-days, TOKEN)
                )
            else:
                advice = (
                    "expiry is TODAY, and the consumer gates honour a declaration for its last day "
                    "only — re-measure the number and bump `%s`, or the allowance reverts at the "
                    "next tick" % TOKEN
                )
            say("NOT-OK ratchet-%s: %s — %s" % (state, shape, advice))
        elif days < floor and renewed is None:
            reds.append(where)
            say(
                "NOT-OK ratchet-renewal-window: %s — the rule is >= %d day(s) from expiry OR a "
                "recorded `%s` (#1414); %d day(s) left with no renewal is the last-minute renewal "
                "the clause forbids" % (shape, floor, RENEWAL_KEY, days)
            )
        elif days <= window:
            notes += 1
            say(
                "NOTE  ratchet-approaching: %s — inside the %d-day window; re-measure and bump "
                "`%s`, or record `%s`, before it lapses" % (shape, window, TOKEN, RENEWAL_KEY)
            )
        else:
            say("OK    ratchet-live: " + shape)
        if measured == "MISSING":
            say(
                "NOTE  ratchet-no-measurement-record: %s %s — the number is declared with no "
                "measurement beside it, so nothing in the tree justifies it" % (name, label)
            )

    if reds:
        say(
            "NOT-OK — %d dated ratchet(s) expired, at expiry, or inside the %d-day floor: %s"
            % (len(reds), floor, ", ".join(sorted(set(reds))))
        )
        return 1

    if notes:
        say(
            "OK — %d dated ratchet(s) live, %d inside the %d-day window (NOTEs above, never a red), "
            "none inside the %d-day floor" % (len(found), notes, window, floor)
        )
        return 0

    say("OK — %d dated ratchet(s), all more than %d day(s) from expiry" % (len(found), window))
    return 0


sys.exit(main())
PY
)"
  rc=$?
  rm -f "$list"
  printf '%s\n' "$out"
  return "$rc"
}

# --- --self-test: provoke every arm on scratch fixtures ----------------------
# Every arm below is the acceptance shape from #1414 read literally: the two
# ratchets' own dates (2026-10-02) measured from 2026-09-29 (NOTE, rc 0), from
# 2026-10-02 (RED by file name), and a renewal carrying a LOWER number (passes).
write_ratchet() { # write_ratchet <file> <body>
  local file="$1" body="$2"
  mkdir -p "$(dirname "$file")"
  printf '%s\n' "$body" > "$file"
}

self_test() {
  local work fails=0
  work="$(mktemp -d /tmp/check-ratchets.XXXXXX)"

  TMPD="$work"
  cleanup() { [ -n "$TMPD" ] && rm -rf "$TMPD" || true; }
  trap cleanup EXIT

  ok() { printf '  OK    %s\n' "$1"; }
  bad() {
    printf '  FAIL  %s\n' "$1" >&2
    fails=$((fails + 1))
  }

  expect() { # expect <want-rc> <want-needle-or-'-> <label> <root> <clock>
    local want_rc="$1" needle="$2" label="$3" fixture="$4" moment="$5" out rc
    out="$(core "$fixture" "$moment")"
    rc=$?
    if [ "$rc" -ne "$want_rc" ]; then
      bad "$label (rc=$rc want=$want_rc): $out"
      return 0
    fi
    if [ "$needle" != "-" ] && [[ "$out" != *"$needle"* ]]; then
      bad "$label (rc=$rc ok, but the report never said '$needle'): $out"
      return 0
    fi
    ok "$label"
  }

  local d="$work"

  # 1. the issue's own acceptance: 2026-09-29 NOTEs a 2026-10-02 ratchet.
  write_ratchet "$d/three-days/governance/isolation/worktree-cap.yaml" \
    'slack: 10
ratchet:
  measured: 60
  host: ElevatedIQ-AK
  expires: "2026-10-02"'
  expect 0 "ratchet-approaching:" "the issue's acceptance — 2026-09-29 NOTEs the 2026-10-02 ratchet, rc 0" \
    "$d/three-days" "2026-09-29"
  expect 0 "governance/isolation/worktree-cap.yaml" "...and the NOTE names the file" "$d/three-days" "2026-09-29"

  # 2. the issue's other acceptance: on the expiry day, with no renewal, RED by file name.
  expect 1 "ratchet-expiry-reached: governance/isolation/worktree-cap.yaml" \
    "the issue's acceptance — 2026-10-02 with no renewal reds, naming the file" "$d/three-days" "2026-10-02"
  expect 1 "governance/isolation/worktree-cap.yaml" "...and the summary names the file too" "$d/three-days" "2026-10-02"

  # 3. past the expiry: RED, and the message says how long ago.
  write_ratchet "$d/expired/governance/reconcile/orphan-budget.yaml" \
    'budget:
  orphan-branch: 95
measured:
  host: ElevatedIQ-AK
  date: "2026-09-18"
expires: "2026-10-02"'
  expect 1 "ratchet-expired: governance/reconcile/orphan-budget.yaml" \
    "a ratchet past its expiry reds by file name" "$d/expired" "2026-10-04"
  expect 1 "ratchet-renewal-window:" "...and one day before expiry is the floor, not the past" \
    "$d/expired" "2026-10-01"
  expect 1 "ratchet-expired:" "the day after expiry is named by its own state" "$d/expired" "2026-10-03"

  # 4. the floor: <3 days with no renewal is RED; the same date WITH a renewal is not.
  expect 1 "ratchet-renewal-window:" "1 day out with no renewal reds (the '>= 3 days OR renewed_at' clause)" \
    "$d/three-days" "2026-10-01"
  write_ratchet "$d/one-day-renewed/governance/x.yaml" \
    'ratchet:
  measured: 60
  renewed_at: "2026-09-19"
  expires: "2026-10-02"'
  expect 0 "ratchet-approaching:" "1 day out WITH renewed_at is a NOTE, rc 0 — the clause is satisfied" \
    "$d/one-day-renewed" "2026-10-01"

  # 5. the 7-day window boundary: 7 days NOTEs, 8 days does not.
  expect 0 "ratchet-approaching:" "exactly 7 days out is still a NOTE" "$d/three-days" "2026-09-25"
  expect 0 "-" "8 days out is not even a NOTE (rc 0)" "$d/three-days" "2026-09-24"
  local far_out
  far_out="$(core "$d/three-days" "2026-09-24")"
  if [[ "$far_out" == *"ratchet-live:"* ]]; then
    ok "8 days out reports the ratchet live, not approaching"
  else
    bad "8 days out did not report ratchet-live: $far_out"
  fi

  # 6. a renewal carrying a LOWER number passes (the renewal shape #1414 names).
  write_ratchet "$d/renewal/governance/reconcile/orphan-budget.yaml" \
    'budget:
  orphan-worktree: 36
measured:
  host: ElevatedIQ-AK
  date: "2026-09-19"
renewed_at: "2026-09-19"
expires: "2026-11-01"'
  expect 0 "ratchet-live:" "a renewal with a LOWER number (36 < 55) and a bumped date passes" \
    "$d/renewal" "2026-09-19"
  expect 0 "renewed_at=2026-09-19" "...and the report prints the renewal it honoured" "$d/renewal" "2026-09-19"

  # 7. a renewal that does not MOVE the date is not a renewal.
  write_ratchet "$d/stale-renewal/governance/x.yaml" \
    'ratchet:
  renewed_at: "2026-09-19"
  expires: "2026-10-02"'
  expect 1 "ratchet-expiry-reached:" "renewed_at cannot resurrect a date that has arrived" \
    "$d/stale-renewal" "2026-10-02"
  write_ratchet "$d/renewal-after/governance/x.yaml" \
    'ratchet:
  renewed_at: "2026-11-01"
  expires: "2026-10-02"'
  expect 1 "ratchet-renewal-after-expiry:" "a renewal dated after the date it renews is red" \
    "$d/renewal-after" "2026-09-19"

  # 8. an unreadable date fails CLOSED (never skipped, never a pass).
  write_ratchet "$d/garbage/governance/x.yaml" 'ratchet:
  expires: "soon"'
  expect 1 "ratchet-unreadable-date: governance/x.yaml" "an unparseable expires is RED, naming the file" \
    "$d/garbage" "2026-09-19"

  # 9. a commented-out expires is not a declaration, and a class-wide scan that
  #    finds nothing SAYS SO rather than passing mutely.
  write_ratchet "$d/commented/governance/board/exceptions.yaml" \
    '# Example (commented out — no exceptions are active today):
#
# exceptions:
#   - check: conformance
#     expires: "2026-10-01"

exceptions: []
'
  expect 0 "no dated ratchet found" "a commented-out expires is not a declaration — and finding none says so" \
    "$d/commented" "2026-10-02"
  expect 0 "-" "the empty case is rc 0, not an error" "$d/commented" "2026-10-02"

  # 10. the class is covered wherever the key hides: nested, in a list, and in JSON.
  write_ratchet "$d/class/gov/deep/deep.yaml" \
    'outer:
  inner:
    ratchet:
      expires: "2027-01-01"'
  write_ratchet "$d/class/gov/board/exceptions.yaml" \
    'exceptions:
  - check: conformance
    expires: "2027-02-01"'
  write_ratchet "$d/class/gov/data.json" '{"budget": {"x": 1}, "expires": "2027-03-01"}'
  local class_out
  class_out="$(core "$d/class" "2026-09-19")"
  if [[ "$class_out" == *"dated declaration(s)=3"* ]]; then
    ok "nested, list-nested and JSON expires keys are all found (3 declarations)"
  else
    bad "the class scan did not find all three declarations: $class_out"
  fi
  if [[ "$class_out" == *"outer.inner.ratchet.expires"* && "$class_out" == *"exceptions[0].expires"* ]]; then
    ok "...and each is reported by its mapping path, not by a guessed line"
  else
    bad "mapping paths were not reported: $class_out"
  fi

  # 11. a fixture under tests/ is declared out of scope BY NAME, never silently.
  write_ratchet "$d/fixture/tests/old.yaml" 'ratchet:
  expires: "2000-01-01"'
  local fixture_out
  fixture_out="$(core "$d/fixture" "2026-09-19")"
  if [[ "$fixture_out" == *"skipped 1 test-fixture path(s)"* && "$fixture_out" == *"no dated ratchet found"* ]]; then
    ok "a fixture under tests/ is skipped BY NAME (and the empty result still says so)"
  else
    bad "the fixture skip was not reported by name: $fixture_out"
  fi

  # 12. no-measurement-record is reported, and it does NOT red.
  write_ratchet "$d/no-measurement/gov/x.yaml" 'ratchet:
  expires: "2027-01-01"'
  expect 0 "ratchet-no-measurement-record:" "a declared number with no measurement is reported, not red" \
    "$d/no-measurement" "2026-09-19"

  # 13. the tri-state: an unreadable file and a malformed clock are CANNOT-ASSESS.
  printf 'ratchet:\n  expires: "2027-01-01"\n  broken: [unclosed\n' > "$d/unparsable.yaml"
  expect 2 "CANNOT-ASSESS" "a file that carries the token but cannot be parsed is CANNOT-ASSESS, never a pass" \
    "$d" "2026-09-19"
  expect 2 "not an ISO date" "a malformed clock is CANNOT-ASSESS" "$d/commented" "yesterday"
  expect 2 "is not a directory" "a missing root is CANNOT-ASSESS" "$d/does-not-exist" "2026-09-19"

  # 14. the candidate list is derived from git, so state git does not record cannot
  #     decide this gate's verdict: a gitignored runtime file, and a NESTED peer
  #     worktree, are both invisible — and the same two shapes ARE read by the walk
  #     fallback at a root that is not a work tree, which is why the arm is a pair.
  mkdir -p "$d/gitscope/.runtime" "$d/gitscope/.claude/worktrees/agent-x"
  write_ratchet "$d/gitscope/governance/live.yaml" 'ratchet:
  expires: "2027-01-01"'
  printf '.runtime/\n' > "$d/gitscope/.gitignore"
  write_ratchet "$d/gitscope/.runtime/stale.json" '{"expires": "2000-01-01"}'
  write_ratchet "$d/gitscope/.claude/worktrees/agent-x/governance/peer.yaml" 'ratchet:
  expires: "2000-01-01"'
  # The fixture is only hermetic if it strips what the code under test reads: an
  # exported GIT_DIR/GIT_WORK_TREE/identity would repoint or re-sign these repos.
  env -u GIT_DIR -u GIT_WORK_TREE -u GIT_INDEX_FILE -u GIT_AUTHOR_NAME \
      -u GIT_AUTHOR_EMAIL -u GIT_COMMITTER_NAME -u GIT_COMMITTER_EMAIL \
      git init -q "$d/gitscope" 2>/dev/null
  env -u GIT_DIR -u GIT_WORK_TREE -u GIT_INDEX_FILE \
      git init -q "$d/gitscope/.claude/worktrees/agent-x" 2>/dev/null
  local gitscope_out gitscope_rc
  gitscope_out="$(core "$d/gitscope" "2026-09-19")"
  gitscope_rc=$?
  if [ "$gitscope_rc" -eq 0 ] && [[ "$gitscope_out" == *"scope=git ls-files"* ]] &&
    [[ "$gitscope_out" == *"dated declaration(s)=1"* ]]; then
    ok "git-derived scope: a gitignored runtime file and a nested peer worktree are NOT read (rc 0)"
  else
    bad "the git-derived scope did not exclude unrecorded state (rc=$gitscope_rc): $gitscope_out"
  fi
  write_ratchet "$d/walkscope/.runtime/stale.json" '{"expires": "2000-01-01"}'
  local walkscope_out walkscope_rc
  walkscope_out="$(core "$d/walkscope" "2026-09-19")"
  walkscope_rc=$?
  if [ "$walkscope_rc" -eq 1 ] && [[ "$walkscope_out" == *"ratchet-expired: .runtime/stale.json"* ]] &&
    [[ "$walkscope_out" == *"scope=filesystem walk"* ]]; then
    ok "...and the same shape at a non-git root IS read by the walk fallback (rc 1, by name)"
  else
    bad "the walk fallback did not read the runtime file (rc=$walkscope_rc): $walkscope_out"
  fi

  # 15. the shape this check does NOT consider, asserted rather than assumed: the
  #     class is the DECLARATION (`*.yaml`/`*.yml`/`*.json`), not the text. Both
  #     halves exist in this tree — `governance/reconcile/tests/test_orphans.py`
  #     writes `expires: "2026-10-02"` into a fixture as a Python string (a
  #     generator, not a declaration), and `governance/board/exceptions.yaml`
  #     carries its `expires` only inside a commented-out example, which the YAML
  #     parse already refuses (arm 9). The suffix boundary is asserted here.
  mkdir -p "$d/suffix"
  printf 'BODY = "budget:\\n  x: 1\\nexpires: \\"2000-01-01\\"\\n"\n' > "$d/suffix/declaration.py"
  expect 0 "no dated ratchet found" "a .py quoting the shape is not a declaration (rc 0, and it says it found none)" \
    "$d/suffix" "2026-09-19"
  write_ratchet "$d/suffix/same-text.yaml" 'budget:
  x: 1
expires: "2000-01-01"'
  expect 1 "same-text.yaml" "...and the SAME text in a .yaml IS one (rc 1, naming the file)" \
    "$d/suffix" "2026-09-19"

  # 16. the seam #1414 names, end to end through the real entry point (argv, not
  #     the shell functions the other arms call), so the flag itself is provoked.
  local now_out now_rc
  now_out="$(bash "${BASH_SOURCE[0]}" --root "$d/three-days" --now 2026-10-02 2>&1)"
  now_rc=$?
  if [ "$now_rc" -eq 1 ] &&
    [[ "$now_out" == *"ratchet-expiry-reached: governance/isolation/worktree-cap.yaml"* ]]; then
    ok "--now <ISO-8601> is the injected clock: 2026-10-02 reds by file name (rc 1)"
  else
    bad "--now did not inject the clock (rc=$now_rc): $now_out"
  fi
  local alias_out alias_rc
  alias_out="$(bash "${BASH_SOURCE[0]}" --root "$d/three-days" --clock 2026-10-02 2>&1)"
  alias_rc=$?
  if [ "$alias_rc" -eq 1 ] && [[ "$alias_out" == *"ratchet-expiry-reached:"* ]]; then
    ok "...and its retained alias --clock reaches the same verdict (rc 1)"
  else
    bad "--clock stopped being the same seam (rc=$alias_rc): $alias_out"
  fi

  local result=0
  if [ "$fails" -eq 0 ]; then
    echo "check-ratchets: --self-test OK — every arm fires by name on scratch fixtures"
  else
    echo "check-ratchets: --self-test NOT-OK — $fails control(s) failed" >&2
    result=1
  fi
  return "$result"
}

if [ "$self_test" -eq 1 ]; then
  self_test
  exit $?
fi

if [ -z "$root" ]; then
  root="$(git rev-parse --show-toplevel 2>/dev/null)" || root=""
fi
if [ -z "$root" ]; then
  echo "check-ratchets: CANNOT-ASSESS — not inside a git repository and no --root was given" >&2
  exit 2
fi

if [ -z "$clock" ]; then
  clock="$(date -u +%Y-%m-%d)"
fi

core "$root" "$clock"
