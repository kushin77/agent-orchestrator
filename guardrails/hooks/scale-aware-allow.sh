#!/usr/bin/env bash
# ============================================================================
# guardrails/hooks/scale-aware-allow.sh — scale-aware PreToolUse permission
# hook (#649). Makes permission friction a FUNCTION OF ops/scale-tripwire.sh
# rather than a static file.
#
# OPERATOR GOAL (verbatim): "I don't want to be asked for permissions all the
# time to allow basic tasks — permissions at limited or removed until the
# same 10 collaborator trigger."
#
# CONTRACT THIS BUILDS ON (verified against code.claude.com/docs hooks-guide
# and /permissions, not assumed):
#   1. hookSpecificOutput.permissionDecision ∈ {allow, deny, ask}. `allow`
#      DOES suppress the interactive prompt.
#   2. permissions.deny in settings is a HARD FLOOR a hook cannot override —
#      "Claude Code evaluates deny and ask rules regardless of what a
#      PreToolUse hook returns." So our `allow` can never weaken the deny
#      list. Good: the settings deny list stays authoritative.
#   3. ASYMMETRY: a hook returning `deny` overrides allow rules AND
#      bypassPermissions mode. The hook is therefore the STRONGEST
#      enforcement point in the system — and under bypassPermissions it is
#      the ONLY one left. That is why step 1 below runs first and depends on
#      nothing.
#   4. Settings evaluation order is deny -> ask -> allow, first match wins.
#
# DECISION ORDER (this is the whole design):
#   1. HARD-DENY SET, at ANY scale, before the tripwire is even consulted.
#      terraform apply/destroy/import/state-rm, force-push, push-to-main,
#      history rewriting, rm -rf /, secret exfiltration, curl|sh. Returns
#      `deny`. Per contract (3) this binds even under bypassPermissions.
#      Duplicating settings' deny list here is DELIBERATE defence in depth,
#      not redundancy: settings-deny protects if this hook is broken; this
#      hook-deny protects if settings are wrong, edited, or bypass mode is
#      on. The set is embedded LITERALLY in this script — unlike
#      shell-aware-deny.sh it does NOT read .claude/settings.json, because
#      the one thing that must bind under bypass mode cannot depend on a
#      config file being present and parseable.
#   2. SELF-MODIFICATION PATHS -> `ask`, even at solo scale. Edit/Write
#      against .claude/settings*.json, guardrails/hooks/**, ops/scale-
#      tripwire.sh and .github/workflows/** get one prompt. A blanket
#      solo-scale `allow` would otherwise let any agent silently neuter the
#      very mechanism that is supposed to reinstate governance
#      automatically. Costs the operator one prompt on the rarest action;
#      keeps the mechanism tamper-EVIDENT. Not a `deny`, because the
#      operator's own legitimate edits must remain possible.
#   3. TRIPWIRE FIRED -> `deny` for anything mutating; `ask` for the small
#      read-only set. NOT `ask`-for-everything, and emphatically NOT
#      fall-through. See "WHY FIRED MEANS DENY" below.
#   4. TRIPWIRE SOLO -> `allow` for anything not caught above. This is the
#      friction removal the operator asked for.
#   5. TRIPWIRE UNREACHABLE / ERRORS / TIMES OUT -> treated exactly like
#      FIRED. Fail TOWARD governance — the same posture ops/scale-
#      tripwire.sh itself takes (#639). Note scale-tripwire.sh already fails
#      toward "fired" when the GitHub collaborators API is unreachable; we do
#      NOT set SCALE_TRIPWIRE_GH_OPTIONAL on its behalf. Weakening another
#      component's fail-closed decision from inside this hook would be
#      exactly the wrong direction.
#
# WHY FIRED MEANS DENY, NOT ASK (design correction, supersedes the original
# brief for #649). The operator has moved to `bypassPermissions` and intends
# to stay there. Under that mode:
#   - a hook returning `deny` is DOCUMENTED to override allow rules and
#     bypassPermissions — it is the only decision confirmed to bind;
#   - whether a hook-returned `ask` binds under bypass is NOT documented
#     (the docs only say a settings-level ask RULE still prompts);
#   - falling through is definitely wrong — no prompt exists to fall through
#     to, so the call simply proceeds.
# So "tripwire fired -> ask/fall-through" would produce a control that LOOKS
# like governance reinstating while actually permitting everything. This
# hook therefore returns `deny` on the fired path for everything outside a
# deliberately tiny read-only set, with a reason naming the tripwire so the
# operator understands why something that worked yesterday is blocked.
#
# NOT EMPIRICALLY VERIFIED, STATED AS AN ASSUMPTION: this hook's behaviour
# under an actually-active `bypassPermissions` session has not been observed
# from inside a session. What IS verified here is the hook's own output
# (see scripts/check-scale-aware-allow-self-test.sh): the exact
# permissionDecision emitted for every input class. That the harness honours
# a hook `deny` under bypass mode rests on the documented contract, not on
# an observation. If the harness exposes the active mode on the hook payload
# it is read from `permission_mode` and echoed into the reason (that field
# is undocumented; the hook works with or without it).
#
# #647 COMPOUND-COMMAND BYPASS — THE THING THIS MUST NOT INHERIT.
# guardrails/hooks/shell-aware-deny.sh shlex-normalizes correctly and then
# fnmatch-prefix-matches the WHOLE joined string, so every compound form
# walks straight past it: `terraform apply` denied but `true; terraform
# apply` allowed; likewise `(terraform apply)`, `bash -c 'terraform apply'`,
# `cd /x && git push --force origin main`. This hook therefore DECOMPOSES
# the command into constituent simple commands and checks EACH ONE:
#   - splits on ; && || | & and newlines
#   - unwraps subshells ( ) and groups { }
#   - recurses into bash -c / sh -c / eval / xargs payloads
#   - strips transparent prefixes (VAR=val, env, sudo, command, nohup,
#     time, timeout N, nice, ionice, stdbuf) before matching
#   - matches on (program, subcommand, flags) — NOT string prefix — so
#     `git -C /x push --force` is caught even though the settings glob
#     `Bash(git push* --force*)` does not match it
#   - detects curl|bash pipelines structurally (source cmd + sink shell),
#     not by substring
#
# FAIL-CLOSED, NARROWLY. Blanket-denying anything containing `$(` would
# break ordinary work and the hook would get ripped out — a hook nobody
# runs protects nothing. The narrow rule: if decomposition leaves a simple
# command whose PROGRAM cannot be resolved (variable/substitution) AND the
# raw command text mentions any hard-deny keyword (terraform, push,
# filter-branch/filter-repo, reset --hard, rm -rf, gh secret...), DENY.
# Otherwise allow that unresolvable command to proceed to normal handling.
# If shlex cannot tokenize the command at all -> DENY.
# If python3 is missing, or the analyzer crashes, or emits nothing, or stdin
# is empty -> DENY, loudly, with recovery instructions in the reason. Under
# bypassPermissions "fall through" means ALLOW, so it is no longer a safe
# failure mode; a broken analyzer must never look identical to "checked and
# clean". A missing python3 therefore bricks tool calls until it is restored
# — that is deliberate: a loud, obvious, recoverable failure beats a silent
# inert guardrail. The deny reason names both remedies (install python3, or
# unregister the hook in .claude/settings.json).
#
# CACHE. This runs on EVERY tool call and scale-tripwire.sh makes a GitHub
# API call, so the SOLO verdict is cached under ops/.state/ with a short
# TTL (default 300s / 5 min, CMR_SCALE_ALLOW_TTL). Only the `solo` verdict
# is cached: a stale `fired` entry would keep governance ON, which is the
# safe direction and needs no cache; a stale `solo` entry is the only
# dangerous one, so its lifetime is exactly the TTL. WORST-CASE
# REINSTATEMENT LAG = TTL = 5 minutes: the window in which the tripwire has
# fired but this hook still answers `allow`. Cache key includes threshold +
# repo so a --threshold change cannot reuse a verdict. Writes are atomic
# (temp + mv) because many agents share ops/.state/ concurrently. A
# partial/garbage cache read is treated as a miss, never as `solo`.
#
# Env (test-harness hooks):
#   CMR_SCALE_ALLOW_STATE_DIR   cache dir (default <root>/ops/.state)
#   CMR_SCALE_ALLOW_TRIPWIRE    tripwire script (default ops/scale-tripwire.sh)
#   CMR_SCALE_ALLOW_TTL         cache TTL seconds (default 300)
#   CMR_SCALE_ALLOW_FORCE       "solo" | "fired" | "error" — fixture-inject
#                               the scale verdict WITHOUT running the
#                               tripwire (self-test only; never affects the
#                               hard-deny step, which runs first regardless)
# ============================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
STATE_DIR="${CMR_SCALE_ALLOW_STATE_DIR:-$ROOT/ops/.state}"
TRIPWIRE="${CMR_SCALE_ALLOW_TRIPWIRE:-$ROOT/ops/scale-tripwire.sh}"
TTL="${CMR_SCALE_ALLOW_TTL:-300}"
TW_TIMEOUT="${CMR_SCALE_ALLOW_TIMEOUT:-15}"
# TTL for NEGATIVE verdicts (fired/error). Short, and for COST not safety: a
# stale negative only delays un-firing (the safe direction), but without any
# caching every tool call in the fired state re-runs the tripwire's `gh api`
# + full-history `git log`. See scale_verdict().
NEG_TTL="${CMR_SCALE_ALLOW_NEG_TTL:-30}"

warn() { echo "scale-aware-allow: $1" >&2; }

# Fall through: emit NO decision. Normal permission handling applies. Under
# bypassPermissions this is equivalent to ALLOW, so it is reserved for cases
# where the call was already positively classified as harmless — never used
# as a failure mode. Failures go to hard_fail() below.
fall_through() { exit 0; }

# Loud fail-closed used when the hook cannot evaluate at all. Emits a deny
# without needing python3 (which may be the very thing that is missing):
# stderr + exit 2 is a documented deny path.
hard_fail() {
  echo "scale-aware-allow: BLOCKING — $1" >&2
  echo "scale-aware-allow: this hook cannot evaluate the call, and under bypassPermissions 'fall through' would mean ALLOW. Denying instead. To recover: restore python3 on PATH, or unregister guardrails/hooks/scale-aware-allow.sh from .claude/settings.json's PreToolUse hooks." >&2
  exit 2
}

# emit_authored: build the hook payload for the scale-verdict decisions. The
# reason text here is AUTHORED IN THIS FILE and contains no double quotes or
# backslashes, and $MODE has already been reduced to [A-Za-z0-9_-] by the
# analyzer — so no JSON escaper (and no extra python3 spawn) is needed.
# Anything carrying untrusted text is JSON-built by the analyzer instead.
emit_authored() {
  # $1 = allow|deny|ask, $2 = reason (authored, quote-free)
  printf '{ "hookSpecificOutput": { "hookEventName": "PreToolUse", "permissionDecision": "%s", "permissionDecisionReason": "%s" } }\n' "$1" "$2"
  if [ "$1" = "deny" ]; then exit 2; fi
  exit 0
}

# emit_payload: print a complete payload the analyzer already serialised.
emit_payload() {
  # $1 = decision (to pick the exit code), $2 = full JSON line
  printf '%s\n' "$2"
  if [ "$1" = "deny" ]; then exit 2; fi
  exit 0
}

if ! command -v python3 >/dev/null 2>&1; then
  hard_fail "python3 is unavailable, so the shell command cannot be decomposed and the hard-deny set cannot be evaluated."
fi

STDIN_JSON="$(cat 2>/dev/null)"
if [ -z "$STDIN_JSON" ]; then
  hard_fail "received empty stdin (no hook payload)."
fi

# ---------------------------------------------------------------------------
# Analyzer. Lives in guardrails/hooks/lib/scale_aware_allow_analyzer.py as a
# real, importable module rather than an inline heredoc.
#
# WHY A MODULE AND NOT `python3 -c "$PY_CODE"`:
#   - PERFORMANCE. This hook runs on EVERY tool call. Compiling ~500 lines of
#     analyzer source per invocation measured ~437ms on the dev box. Imported
#     as a module the compile happens once and __pycache__ serves every
#     subsequent call. A script run as __main__ is never pycached, which is
#     exactly why the wrapper below does `import ...; main(...)` rather than
#     running the file directly.
#   - ROBUSTNESS. An inline heredoc can be silently neutered by a shell
#     quoting mistake (a quoted delimiter stops the body expanding; the
#     interpreter then receives a literal variable name and every command
#     reads as "not denied"). A file cannot fail that way, and it is
#     independently lintable and testable.
#
# It writes exactly three lines: decision, readonly flag, and either the
# final hook JSON payload (deny/ask) or the sanitized permission_mode
# (probe). Exit 3 means the payload itself was unparseable.
#
# -S -E: skip site-packages initialisation and ignore PYTHON* env vars.
# `python3 -c pass` costs ~211ms with site processing versus ~31ms without on
# this box. The analyzer imports only stdlib (json/os/re/shlex), so it needs
# nothing from site-packages; -E additionally stops a stray
# PYTHONPATH/PYTHONSTARTUP from influencing a security check.
# ---------------------------------------------------------------------------
ANALYZER_DIR="$(dirname "${BASH_SOURCE[0]}")/lib"
if [ ! -f "$ANALYZER_DIR/scale_aware_allow_analyzer.py" ]; then
  hard_fail "the analyzer module is missing from $ANALYZER_DIR."
fi

RESULT="$(ANALYZER_DIR="$ANALYZER_DIR" python3 -S -E -c '
import os, sys
sys.path.insert(0, os.environ["ANALYZER_DIR"])
import scale_aware_allow_analyzer as a
a.main(sys.argv[1])
' "$STDIN_JSON" 2>/dev/null)"
PY_RC=$?

if [ "$PY_RC" != "0" ] || [ -z "$RESULT" ]; then
  hard_fail "the analyzer failed (rc=$PY_RC) or produced no output, so no decision could be computed."
fi

# The analyzer emits exactly three lines (see out() above): decision,
# readonly flag, and either the final JSON payload or the sanitized
# permission_mode. Parsed with pure bash — no second interpreter spawn.
{ IFS= read -r DECISION; IFS= read -r READONLY; IFS= read -r THIRD; } <<EOF
$RESULT
EOF

if [ "$DECISION" = "deny" ] || [ "$DECISION" = "ask" ]; then
  [ -z "$THIRD" ] && hard_fail "the analyzer decided '$DECISION' but emitted no payload."
fi

case "$DECISION" in
  deny)
    warn "hard-deny fired; see the permissionDecisionReason in the payload."
    emit_payload deny "$THIRD"
    ;;
  ask)
    emit_payload ask "$THIRD"
    ;;
  passthrough)
    # Agent/SendMessage: decline to decide so the dispatch guards registered
    # after this hook always run. See the analyzer's comment.
    fall_through
    ;;
  probe)
    MODE="$THIRD"   # already reduced to [A-Za-z0-9_-] by the analyzer
    ;;
  *)
    hard_fail "the analyzer returned an unrecognised decision ('$DECISION')."
    ;;
esac

# ---------------------------------------------------------------------------
# STEP 3/4/5: consult the scale verdict (cached).
# ---------------------------------------------------------------------------
scale_verdict() {
  # Fixture injection for the self-test. Never reached before hard-deny.
  if [ -n "${CMR_SCALE_ALLOW_FORCE:-}" ]; then
    printf '%s\tfixture-injected\n' "$CMR_SCALE_ALLOW_FORCE"
    return 0
  fi

  local threshold="${SCALE_TRIPWIRE_THRESHOLD:-10}"
  local repo="${SCALE_TRIPWIRE_REPO:-kushin77/CMR}"
  local key
  key="$(printf '%s|%s|%s' "$threshold" "$repo" "$TRIPWIRE" | cksum | tr -d ' \n')"
  local cache="$STATE_DIR/scale-aware-allow.$key.cache"

  # --- cache read. Entries are "<ts>\t<verdict>\t<cause>". Two different
  # TTLs apply, because the two directions carry completely different risk:
  #
  #   solo  -> TTL (300s). A stale `solo` is a GOVERNANCE HOLE: the tripwire
  #            has fired but this hook still answers `allow`. That window is
  #            the worst-case reinstatement lag and is bounded here.
  #   fired/error -> NEG_TTL (30s). A stale negative only delays UN-firing,
  #            which is the safe direction. It is cached purely for COST:
  #            without it, every single tool call in the fired state re-runs
  #            ops/scale-tripwire.sh, which does a 10s-timeout `gh api` plus
  #            a full-history `git log`. Across ~60 concurrent worktrees
  #            (each with its own ops/.state/) that is a multi-second stall
  #            on every call and unbounded API pressure — and a rate-limit
  #            403 reads as `unreachable`, which fires, which causes more
  #            calls. It self-amplifies. Caching negatives bounds the call
  #            rate by ~30x and keeps the tool usable in exactly the state
  #            this whole feature exists to reach.
  #
  # A partial/garbage/future-dated read is a MISS, never a verdict. ---
  if [ -f "$cache" ]; then
    local line ts verdict cause now age max_age
    line="$(cat "$cache" 2>/dev/null)"
    ts="${line%%	*}"
    cause="${line##*	}"
    verdict="$(printf '%s' "$line" | cut -f2)"
    if [ "$ts" -eq "$ts" ] 2>/dev/null; then
      now="$(date +%s)"
      age=$((now - ts))
      case "$verdict" in
        solo)         max_age="$TTL" ;;
        fired|error)  max_age="$NEG_TTL" ;;
        *)            max_age=-1 ;;
      esac
      if [ "$max_age" -ge 0 ] && [ "$age" -lt "$max_age" ] && [ "$age" -ge 0 ]; then
        printf '%s\t%s\n' "$verdict" "$cause"
        return 0
      fi
    fi
  fi

  if [ ! -x "$TRIPWIRE" ] && [ ! -f "$TRIPWIRE" ]; then
    printf 'error\tmissing-tripwire\n'
    return 0
  fi

  local rc out verdict cause
  out="$(timeout "${TW_TIMEOUT}s" bash "$TRIPWIRE" 2>&1)"
  rc=$?
  if [ "$rc" -eq 0 ]; then
    verdict="solo"; cause="below-threshold"
  elif [ "$rc" -eq 1 ]; then
    verdict="fired"
    # The tripwire distinguishes a REAL threshold breach from its fail-safe
    # API escalation in its own output, and those demand completely
    # different operator responses ("a 10th collaborator exists" vs "GitHub
    # is unreachable and we are assuming the worst"). Preserve that instead
    # of collapsing both into identical text.
    case "$out" in
      *"fail-safe escalation"*) cause="gh-unreachable" ;;
      *)                        cause="threshold-breach" ;;
    esac
  elif [ "$rc" -ge 124 ]; then
    verdict="error"; cause="timeout"
  else
    verdict="error"; cause="tripwire-usage-error"
  fi

  mkdir -p "$STATE_DIR" 2>/dev/null
  local tmp
  if tmp="$(mktemp "$STATE_DIR/.scale-aware-allow.XXXXXX" 2>/dev/null)"; then
    printf '%s\t%s\t%s\n' "$(date +%s)" "$verdict" "$cause" > "$tmp" 2>/dev/null
    mv -f "$tmp" "$cache" 2>/dev/null || rm -f "$tmp" 2>/dev/null
  fi
  printf '%s\t%s\n' "$verdict" "$cause"
}

VERDICT_LINE="$(scale_verdict)"
VERDICT="${VERDICT_LINE%%	*}"
VERDICT_CAUSE="${VERDICT_LINE##*	}"
[ "$VERDICT_CAUSE" = "$VERDICT" ] && VERDICT_CAUSE="unknown"
MODE_NOTE=""
[ -n "${MODE:-}" ] && MODE_NOTE=" (harness permission_mode: ${MODE})"

case "$VERDICT" in
  solo)
    emit_authored allow "scale-aware-allow: ops/scale-tripwire.sh reports SOLO scale (all signals below the 10-collaborator threshold), and this call is not in the hard-deny set. Auto-allowed — permission friction is a function of scale, not a static list. Governance reinstates automatically within ${TTL}s of the tripwire firing.${MODE_NOTE}"
    ;;
  fired|error)
    # Name the actual cause. 'a 10th collaborator now exists' and 'GitHub is
    # unreachable so we are assuming the worst' demand completely different
    # operator responses; collapsing both into one message would waste the
    # distinction ops/scale-tripwire.sh already draws.
    case "$VERDICT_CAUSE" in
      threshold-breach)
        WHY="ops/scale-tripwire.sh has FIRED on a REAL THRESHOLD BREACH — a scale signal (git-committer identities, onboarded non-hub spokes, or GitHub repo collaborators) reached 10. This is the trigger the operator specified." ;;
      gh-unreachable)
        WHY="ops/scale-tripwire.sh has FIRED as a FAIL-SAFE ESCALATION, not a proven breach — the GitHub collaborators signal is unreachable (no gh, no auth, network down, or an under-scoped token), so solo scale cannot be proven and #627 assumes collaborative. Fix gh auth, or set SCALE_TRIPWIRE_GH_OPTIONAL=1 if you have independently confirmed solo scale." ;;
      timeout)
        WHY="ops/scale-tripwire.sh TIMED OUT after ${TW_TIMEOUT}s, so solo scale cannot be proven. Failing toward governance." ;;
      missing-tripwire)
        WHY="ops/scale-tripwire.sh is MISSING at ${TRIPWIRE}, so solo scale cannot be proven. Failing toward governance." ;;
      *)
        WHY="ops/scale-tripwire.sh could not be consulted (cause: ${VERDICT_CAUSE}), so solo scale cannot be proven. Failing toward governance." ;;
    esac
    if [ "$READONLY" = "1" ]; then
      # Read-only calls stay usable so a fired tripwire does not brick the
      # operator's ability to inspect the repo and understand what changed.
      emit_authored ask "scale-aware-allow: ${WHY} This call is read-only, so it asks rather than blocks — confirm it.${MODE_NOTE}"
    fi
    emit_authored deny "scale-aware-allow: ${WHY} Collaborative-scale governance is now in force automatically, with no action from the operator: mutating calls are BLOCKED here rather than merely prompted, because under bypassPermissions a hook-returned 'ask' is not documented to bind and falling through would silently permit the call. Read-only calls still work. To proceed, leave bypassPermissions (so normal review applies) or re-review the merge/permission posture per ADR-0020/ADR-0030 and the GR-4 carve-out.${MODE_NOTE}"
    ;;
  *)
    hard_fail "the scale verdict was unavailable ('$VERDICT')."
    ;;
esac
