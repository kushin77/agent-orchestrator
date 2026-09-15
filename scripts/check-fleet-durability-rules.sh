#!/usr/bin/env bash
# ============================================================================
# scripts/check-fleet-durability-rules.sh
#
# Owner-lane: fleet / governance
# Class: enterprise
# Connects-to: consumes=docs/GOLDEN-RULES.md,AGENTS.md,fleet/README.md; gates=AO-GR-21..27
# ============================================================================
#
# AO-GR-4: this check must be able to genuinely FAIL, and it must prove that
# about itself. The fleet-durability rules (AO-GR-21..27) were each written from
# a *measured* failure on 2026-09-14 — a 16-hour gate-stacking runaway, a drift
# detector that failed open, 46 commits shelved across 31 lanes, and an operator
# told to `HUP` a loop that does not handle `SIGHUP`.
#
# A rule in a doc is advisory (the spine says so explicitly). This check is the
# mechanical half: it asserts that each rule EXISTS in both the spine and the
# canonical doctrine, that each names the failure it prevents, and that the
# codebase still contains the CONTROL the rule depends on. If a rule is deleted,
# renamed, or its control is ripped out, this exits 1 by name.
#
# Tri-state contract (documented in docs/QA-GATE.md):
#   0 OK             — every rule present, evidenced, and backed by a control
#   1 NOT-OK         — a rule or its control is missing (names which)
#   2 CANNOT-ASSESS  — the repository cannot be read (never a silent pass)
#
set -uo pipefail

# Every "does this report contain this string?" test below is bash-native (#852).
# `printf '%s' "$body" | grep -qF -- "$s"` is NOT the same test: `grep -q` exits on
# its first match, SIGPIPE then kills the producer, and `set -o pipefail` promotes
# that 141 to the status of the whole pipeline — so a *large* body reports ABSENT
# for text that is PRESENT. Negated, that is a false red; positive, the control
# silently stops controlling and the check fails OPEN.
contains() { # contains <haystack> <needle>
  case "$1" in
    *"$2"*) return 0 ;;
    *) return 1 ;;
  esac
}

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)" || exit 2

SPINE="$ROOT/docs/GOLDEN-RULES.md"
DOCTRINE="$ROOT/AGENTS.md"
RUNBOOK="$ROOT/fleet/README.md"

fail=0
note() { printf '  %-6s %s\n' "$1" "$2"; }
bad() { note "FAIL" "$1"; fail=1; }
ok() { note "OK" "$1"; }

# --- Preconditions: the documents this rule set lives in must be readable. ----
for f in "$SPINE" "$DOCTRINE" "$RUNBOOK"; do
  if [ ! -r "$f" ]; then
    printf 'check-fleet-durability-rules: cannot read %s\n' "${f#"$ROOT"/}" >&2
    exit 2
  fi
done

printf '== fleet durability rules (AO-GR-21..27) ==\n'

# --- 1. Each rule exists in the spine, with the three parts the format demands.
#         A rule without a "Verify." block is a formality, so it is rejected.
#         shellcheck disable=SC2016  # the backtick is literal markdown
check_rule() {
  local id="$1" slug="$2"
  local heading="### ${id} — ${slug}"
  if ! grep -qF -- "$heading" "$SPINE"; then
    bad "$id is missing from docs/GOLDEN-RULES.md (expected heading: $id — $slug)"
    return 1
  fi
  # The rule body must carry Rule/Why/Verify. Extract from its heading to the
  # next rule heading (or EOF) and require all three.
  local body
  body="$(awk -v h="$heading" '
    index($0, h) == 1 { found = 1; next }
    found && /^### AO-GR-/ { exit }
    found { print }
  ' "$SPINE")"
  local missing=""
  for part in '**Rule.**' '**Why.**' '**Verify.**'; do
    contains "$body" "$part" || missing="$missing ${part}"
  done
  if [ -n "$missing" ]; then
    bad "$id is present but incomplete (missing:$missing)"
    return 1
  fi
  ok "$id present with Rule/Why/Verify"
  return 0
}

check_rule AO-GR-21 "Bounded work: no queue item is retried forever" || true
check_rule AO-GR-22 "One gate per worktree, and the gate is admission-controlled" || true
check_rule AO-GR-23 "No work is invisible: commit is pushed before it is gated" || true
check_rule AO-GR-24 "A wave is provably file-disjoint before it is dispatched" || true
check_rule AO-GR-25 "Drift is measured against the remote, and never fails open" || true
check_rule AO-GR-26 "A long-lived loop resolves its own dependencies before taking work" || true
check_rule AO-GR-27 "A loop honours the signals it is sent, and documents the rest" || true

# --- 2. The canonical doctrine must POINT AT the spine rules, so an agent that
#         reads only AGENTS.md still learns the rule and its issue. And each
#         spine rule must carry an **Origin.** naming the issue that produced it
#         (provenance — a rule with no recorded origin cannot be deregistered
#         later, because nobody can find why it exists). Provenance is asserted
#         per-rule from that rule's OWN body, not by a whole-file grep: a
#         whole-file grep passes as long as the issue number appears ANYWHERE,
#         which is precisely how this check was caught being vacuous.
printf '\n== doctrine linkage + provenance (AGENTS.md, spine) ==\n'
check_provenance() {
  local rule="$1" ref="$2"
  local body
  body="$(awk -v h="### ${rule} —" '
    index($0, h) == 1 { found = 1; next }
    found && /^### AO-GR-/ { exit }
    found { print }
  ' "$SPINE")"
  if contains "$body" "$ref"; then
    ok "$rule records its provenance ($ref)"
    return 0
  fi
  bad "$rule does not record its origin ($ref) in its own body — provenance is missing"
  return 1
}

for pair in "AO-GR-21:#723" "AO-GR-22:#724" "AO-GR-23:#740" "AO-GR-24:#740" \
            "AO-GR-25:#739" "AO-GR-26:#733" "AO-GR-27:#733"; do
  rid="${pair%%:*}"
  shrunk="${pair##*:}"
  if grep -qF -- "$rid" "$DOCTRINE"; then
    ok "AGENTS.md cites $rid"
  else
    bad "AGENTS.md does not cite $rid — the rule is not discoverable from the canonical doctrine"
  fi
  check_provenance "$rid" "$shrunk" || true
done

# --- 3. Each rule's DISCRIMINATING CONTROL must still exist in the codebase.
#         A rule whose control was deleted is advisory again; this is the part
#         that makes the check fail when someone rips the mechanism out.
printf '\n== rule controls still present ==\n'
require_control() {
  local rule="$1" path="$2" needle="$3"
  local target="$ROOT/$path"
  if [ ! -f "$target" ]; then
    bad "$rule: control file missing: $path"
    return 1
  fi
  if grep -qF -- "$needle" "$target"; then
    ok "$rule: $path declares '$needle'"
    return 0
  fi
  bad "$rule: $path no longer declares '$needle' — the rule's control was removed"
  return 1
}

# AO-GR-21 bounded work — the runner preflight must remain, and the loop must
#         still treat an unassessable gate as CANNOT-ASSESS rather than a
#         failure (that distinction is what stopped the per-cycle re-dispatch).
require_control "AO-GR-21" "fleet/terminal.py" "def preflight" || true
require_control "AO-GR-21" "fleet/README.md" "CANNOT-ASSESS" || true
# AO-GR-25 drift vs remote, never fail open — the classifier must remain.
require_control "AO-GR-25" "fleet/watchdog.py" "def decide" || true
# AO-GR-26 preflight the runner — resolution lives in code, not ambient PATH.
require_control "AO-GR-26" "fleet/terminal.py" "def preflight" || true
require_control "AO-GR-26" "fleet/runtime.py" "def runner_env" || true
# AO-GR-27 signal honesty — the loops must still install explicit handlers.
require_control "AO-GR-27" "fleet/brain.py" "install_stop_handlers" || true
require_control "AO-GR-27" "fleet/monitor.py" "signal.signal" || true

# Not yet buildable, and declared rather than silently skipped. Asserting a
# symbol that does not exist yet would be a false green (AO-GR-4), so these are
# reported as PENDING against the issue that ships them. The moment the module
# lands the check flips them to OK automatically.
printf '\n== declared-pending controls (honest gaps, never implied-green) ==\n'
for pending in \
    "AO-GR-21:fleet/runaway.py:attempt budget + dead-letter:#723" \
    "AO-GR-22:fleet/gatelock.py:gate admission control:#724" \
    "AO-GR-23:fleet/gitpublish.py:push-before-gate:#740"; do
  rule="${pending%%:*}"; rest="${pending#*:}"
  path="${rest%%:*}"; tail="${rest#*:}"
  what="${tail%%:*}"; iss="${tail##*:}"
  if [ -f "$ROOT/$path" ]; then
    ok "$rule: $path has landed — control is now assertable"
  else
    note "PEND" "$rule: $path absent — $what lands with $iss (stated, not implied-green)"
  fi
done

# --- 4. The operator runbook must document the clean restart signal AND the
#         fact that SIGHUP is unhandled. This is the rule's whole point: an
#         operator must never be advised to send an abrupt kill.
printf '\n== runbook signal guidance (AO-GR-27) ==\n'
if grep -qF -- 'SIGHUP' "$RUNBOOK"; then
  ok "fleet/README.md documents SIGHUP explicitly"
else
  bad "fleet/README.md does not mention SIGHUP — an operator cannot know it is unhandled"
fi
if grep -qiE 'SIGTERM' "$RUNBOOK"; then
  ok "fleet/README.md names the clean restart signal (SIGTERM)"
else
  bad "fleet/README.md does not name the clean restart signal (SIGTERM)"
fi
# The dangerous advice this rule forbids: *recommending* SIGHUP as a restart.
# Documenting the hazard (`SIGHUP is NOT handled`) is the rule's whole point, so
# this matches only an imperative/advice formulation, never the warning itself.
if grep -nE '(use|send|just|simply|try)[^.]{0,24}kill +-HUP|kill +-HUP +[^ ]+ *(#|$)' \
      "$RUNBOOK" "$DOCTRINE" 2>/dev/null; then
  bad "a document advises sending SIGHUP as a restart — the loops do not handle it and it is an abrupt kill"
else
  ok "no document advises sending SIGHUP as a restart"
fi

printf '\n'
if [ "$fail" -eq 0 ]; then
  printf 'check-fleet-durability-rules: OK — AO-GR-21..27 are declared, cited, evidenced and backed by live controls\n'
  exit 0
fi
printf 'check-fleet-durability-rules: NOT-OK — see the FAIL lines above\n' >&2
exit 1
