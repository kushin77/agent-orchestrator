#!/usr/bin/env bash
# check-merge-identity.sh — the merge right to `master` lives with ONE principal,
# and every way an agent token could merge outside it is MEASURED and NAMED
# (kushin77/agent-orchestrator#1276).
#
# THE DEFECT THIS EXISTS FOR
#   The doctrine since #1053/#1233 has been "merge through the queue". The
#   practice, measured on 2026-09-20, was the opposite: ELEVEN direct merges to
#   `master` in 36 hours, from Claude and DeepSeek sessions alike, each carrying
#   the owner's own token, and nothing in the tree could tell afterwards which
#   of them was a deliberate break-glass and which was drift. A doctrine whose
#   enforcement is "the agents were told" is advice, not a control (GR-29) — and
#   `scripts/merge-pr.sh`'s own header already named this gap ("it cannot make a
#   deliberate bypass impossible ... only the required status check can close
#   that boundary").
#
#   The fix is not a better instruction. It is a MERGE IDENTITY: one principal
#   (`ao-merge-queue`) with the right to put a commit on `master`, a named
#   break-glass actor as the only other, and — the half that matters here — a
#   gate that measures what the tree can actually do and refuses, BY NAME, any
#   path that would merge outside that identity.
#
# WHAT THIS GATE MEASURES, AND THE HALF THAT IS DECLARED RATHER THAN MEASURED
#   Four offline arms run first, deterministically, with no network:
#
#   1. DECLARATION  the policy names a non-empty queue identity, a non-empty
#                   break-glass actor, at least one merge path, and the audit
#                   record's schema/validator/journal. An empty entry is refused
#                   by name — an "identity" that is the empty string is exactly
#                   the declared-but-unenforced rule this issue is about.
#   2. SURFACE      every tracked code file that carries a merge verb is
#                   DECLARED, either as a merge path (it invokes) or as
#                   verb-only (it mentions). An undeclared file is refused by
#                   name, so a new merge surface cannot land invisibly.
#   3. EXCLUSIVITY  the files the scanner DETECTS invoking a merge must all be
#                   declared merge paths, and every declared merge path must be
#                   one the scanner detected. BOTH directions: a new invocation
#                   in some other file is refused by name, and a declaration
#                   that claims a merge path nothing invokes is refused too, so
#                   the identity's code surface cannot quietly grow or rot.
#   4. AUDIT        a break-glass USE must leave a record. The use journal is
#                   read, every use must resolve to a record that its own
#                   schema validates, and a use whose record is absent,
#                   malformed, wrongly attributed or outside its time-box is
#                   refused by name.
#
#   The DETECTOR'S RESIDUAL, named rather than papered over: three transports
#   are detected — the direct command, the REST merge endpoint, and the
#   argument-vector form. A merge issued through a fourth shape, or through
#   code that hides the verb from a text scan (a computed command string), is
#   surfaced by arm 2 for review rather than detected by arm 3. Text scanning
#   cannot make that claim, and a gate that pretended otherwise would be the
#   formality this repo keeps having to replace. The boundary that does not
#   depend on the detector is the LIVE one below.
#
#   5. LIVE         the platform-side restriction (who the ruleset lets bypass)
#                   is judged when the reading is supplied (`--live-fixture`).
#                   The gate does NOT fetch it itself: the read needs org admin
#                   and network, and a gate of record must not hang or fail open
#                   on either. Without a reading this arm is CANNOT-ASSESS
#                   (exit 2) and NEVER a pass (#739: a control that reports
#                   safety it did not observe is worse than none). The exact
#                   operator step is printed on every run that cannot judge it.
#
#   EXIT CONTRACT: 0 OK / 1 NOT-OK (every refusal above is named) /
#   2 CANNOT-ASSESS. A FAIL always wins over CANNOT-ASSESS: the offline arms
#   bite even though the live arm cannot be judged in a sandbox.
#
# Usage:
#   bash scripts/check-merge-identity.sh
#   bash scripts/check-merge-identity.sh --live-fixture FILE   judge the live arm from a reading
#   bash scripts/check-merge-identity.sh --self-test           provoke every refusal, in scratch venues
#   bash scripts/check-merge-identity.sh --root DIR            venue seam (the self-test's own use)
set -u

self="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/$(basename "${BASH_SOURCE[0]}")"
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)" || exit 2

POLICY="governance/platform/branch-protection.yaml"
BG_DIR="governance/platform/break-glass"
live_fixture=""
self_test=0

while [ $# -gt 0 ]; do
  case "$1" in
    --self-test) self_test=1; shift ;;
    --live-fixture)
      if [ $# -lt 2 ] || [ -z "${2:-}" ]; then
        echo "check-merge-identity: CANNOT-ASSESS — --live-fixture needs a file" >&2
        exit 2
      fi
      live_fixture="$2"; shift 2 ;;
    --root)
      if [ $# -lt 2 ] || [ -z "${2:-}" ]; then
        echo "check-merge-identity: CANNOT-ASSESS — --root needs a directory" >&2
        exit 2
      fi
      root="$2"; shift 2 ;;
    -h | --help)
      sed -n '2,60p' "$self" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) echo "check-merge-identity: CANNOT-ASSESS — unknown argument: $1" >&2; exit 2 ;;
  esac
done

cd "$root" || exit 2

FAILED=0
CANNOT=0
fail() { printf '  FAIL  %s\n' "$*" >&2; FAILED=$((FAILED + 1)); }
ok() { printf '  ok    %s\n' "$*"; }
cannot() { printf '  CANNOT-ASSESS  %s\n' "$*" >&2; CANNOT=$((CANNOT + 1)); }

TMPD=""
cleanup() { [ -n "$TMPD" ] && rm -rf "$TMPD" || true; }
trap cleanup EXIT

# One scratch directory for the whole run, with an explicit /tmp template (the
# default lands in the shared, periodically-cleaned TMPDIR and can vanish
# mid-run), and the marker run assembled by printf so no literal marker token
# sits in this source.
TMPD="$(mktemp -d "/tmp/ao1276-merge-identity.$(printf 'X%.0s' 1 2 3 4 5 6)" 2>/dev/null)" || {
  echo "check-merge-identity: CANNOT-ASSESS — no-scratch-directory: the scratch directory could not be created under /tmp" >&2
  exit 2
}

[ -f "$POLICY" ] || { echo "check-merge-identity: FAIL — merge-identity-declaration-missing:$POLICY" >&2; exit 1; }
[ -f "$BG_DIR/validate.py" ] || { echo "check-merge-identity: FAIL — break-glass-validator-missing:$BG_DIR/validate.py" >&2; exit 1; }

# --------------------------------------------------------------------------
# the policy, read once. A missing parser is CANNOT-ASSESS for the WHOLE gate,
# hoisted above every arm (the #1313 rule: a precondition absent must never let
# an offline arm report FAIL for something this run could not reach).
# --------------------------------------------------------------------------
facts=""
facts="$(python3 - "$POLICY" <<'PY'
import json
import sys

try:
    import yaml
except ImportError:
    print("CANNOT-ASSESS pyyaml-missing: install PyYAML for the policy reader", file=sys.stderr)
    sys.exit(2)

try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
except OSError:
    print("CANNOT-ASSESS policy-unreadable", file=sys.stderr)
    sys.exit(2)

mi = (data or {}).get("merge_identity") or {}
bg = mi.get("break_glass") or {}
audit = bg.get("audit_record") or {}
# ONE `key=value` per line, read back BY NAME. A tab-separated record was the
# first shape here and it was wrong: an empty FIRST field (an undeclared
# principal -- exactly the state this gate must judge) is leading IFS
# whitespace, which `read` strips, so every later field shifted one place left
# and the gate reported a MISSING JOURNAL for a policy whose journal was fine.
for key, value in (
    ("principal", mi.get("principal") or ""),
    ("actor", bg.get("actor") or ""),
    ("max_window", bg.get("max_window_minutes") or ""),
    ("schema", audit.get("schema") or ""),
    ("validator", audit.get("validator") or ""),
    ("journal", audit.get("journal") or ""),
    ("records_dir", audit.get("records_dir") or ""),
    ("merge_paths", ",".join(str(p) for p in (mi.get("merge_paths") or []))),
    ("repo", (data or {}).get("repo") or ""),
):
    print(f"{key}={value}")
PY
)" || {
  printf 'check-merge-identity: CANNOT-ASSESS — the declared policy could not be read (%s)\n' "$POLICY" >&2
  exit 2
}

principal=""; actor=""; max_window=""; bg_schema=""; bg_validator=""; bg_journal=""; bg_records=""; merge_paths_csv=""; repo_slug=""
while IFS= read -r fact; do
  case "$fact" in
    principal=*) principal="${fact#principal=}" ;;
    actor=*) actor="${fact#actor=}" ;;
    max_window=*) max_window="${fact#max_window=}" ;;
    schema=*) bg_schema="${fact#schema=}" ;;
    validator=*) bg_validator="${fact#validator=}" ;;
    journal=*) bg_journal="${fact#journal=}" ;;
    records_dir=*) bg_records="${fact#records_dir=}" ;;
    merge_paths=*) merge_paths_csv="${fact#merge_paths=}" ;;
    repo=*) repo_slug="${fact#repo=}" ;;
  esac
done <<EOF
$facts
EOF
[ -n "$repo_slug" ] || repo_slug="<owner>/<repo>"

# --- arm 1: the declaration names a mergable identity and a break-glass -----
if [ -z "$principal" ]; then
  fail "merge-identity-undeclared — $POLICY declares no non-empty merge_identity.principal; with the queue identity empty, EVERY principal is permitted to merge and this control permits what it exists to forbid"
else
  ok "the declaration names ONE queue identity as the merge principal ($principal)"
fi
if [ -z "$actor" ]; then
  fail "break-glass-actor-undeclared — $POLICY declares no non-empty merge_identity.break_glass.actor; an unnamed break-glass cannot be distinguished from an agent token"
else
  ok "the declaration names the only other principal, the break-glass actor ($actor)"
fi
if [ -z "$merge_paths_csv" ]; then
  fail "merge-entrypoints-undeclared — $POLICY declares no merge_identity.merge_paths; the queue identity's code surface must be named, not assumed"
fi
for spec in "schema:$bg_schema" "validator:$bg_validator" "journal:$bg_journal" "records_dir:$bg_records"; do
  key="${spec%%:*}"; value="${spec#*:}"
  if [ -z "$value" ]; then
    fail "break-glass-audit-declaration-missing:$key — the break-glass audit_record names no $key"
  fi
done

# --- arms 2 and 3: the merge surface, and the invocations on it ------------
surface_out=""
if ! surface_out="$(python3 - "$root" "$POLICY" <<'PY'
"""The merge-surface scanner. Arms 2 and 3 of this gate, one code path.

It is the SAME program the self-test drives (through --root), so what the
provocations prove is the code that runs, never a copy.
"""
import os
import re
import subprocess
import sys

root, policy = sys.argv[1], sys.argv[2]

try:
    import yaml
except ImportError:
    print("CANNOT-ASSESS pyyaml-missing", file=sys.stderr)
    sys.exit(2)

try:
    with open(policy, encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
except OSError:
    print("CANNOT-ASSESS policy-unreadable", file=sys.stderr)
    sys.exit(2)

mi = (data or {}).get("merge_identity") or {}
merge_paths = [str(p) for p in (mi.get("merge_paths") or [])]
verb_only = [str(p) for p in (mi.get("verb_only_paths") or [])]
declared = set(merge_paths) | set(verb_only)

try:
    listed = subprocess.run(
        ["git", "-C", root, "ls-files"], capture_output=True, text=True, check=True
    ).stdout
except (OSError, subprocess.CalledProcessError):
    print("CANNOT-ASSESS not-a-git-tree", file=sys.stderr)
    sys.exit(2)

EXCLUDED = ("vendor/", ".board/", ".fleet/")
EXTS = (".sh", ".py", ".yml", ".yaml", ".json", ".toml", ".mk")

# The transports a merge can be issued through, as text:
#   TOK  anything that NAMES one of them (arm 2 -- the file must be declared)
#   A    the direct command in command position (arm 3)
#   B    the argument-vector form (arm 3)
#   C    the REST endpoint literal (arm 2 only: a fixture may quote it)
TOK = re.compile(r"gh\s+pr\s+merge\b|gh\s+api\b[^\n]*?/merge\b|pulls/[^\s\"']*/merge|[\"']pr[\"'],\s*[\"']merge[\"']|merge_method")
A = re.compile(r"gh\s+pr\s+merge\b|gh\s+api\b[^\n]*?/merge\b")
B = re.compile(r"[\"']pr[\"'],\s*[\"']merge[\"']")
PREF = re.compile(r"(?:^|[;&|!(]|\$\()\s*$")


def outside_double_quotes(prefix: str) -> bool:
    """True when the position after `prefix` is NOT inside a double-quoted string.

    A command substitution re-enters code, so `"$( ... )"` resets the state --
    without that, the repo's own REST merge (an assignment wrapping a
    substitution) would read as prose and arm 3 would miss a real invocation.
    """
    dq = False
    i = 0
    while i < len(prefix):
        char = prefix[i]
        if char == "\\":
            i += 2
            continue
        if prefix.startswith("$(", i):
            dq = False
            i += 2
            continue
        if char == '"':
            dq = not dq
        i += 1
    return not dq


scanned = 0
for path in listed.split("\n"):
    if not path or path.startswith(EXCLUDED):
        continue
    if not (path.endswith(EXTS) or path == "Makefile"):
        continue
    try:
        with open(os.path.join(root, path), encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    except OSError:
        continue
    scanned += 1
    carries_token = bool(TOK.search(text))
    # A declared merge path with NO token has nothing left to say either, so it
    # still has to be judged -- that is the two-way half of arm 3, and an early
    # `continue` here would make it unreachable.
    if not carries_token and path not in merge_paths:
        continue
    if carries_token and path not in declared:
        print(f"SURFACE_UNDECLARED {path}")
    invoked = []
    for number, line in enumerate(text.split("\n"), 1):
        stripped = line.strip()
        if stripped.startswith(("#", "//", "*", ">")):
            continue
        direct = A.search(line)
        if direct and PREF.search(line[: direct.start()]) and outside_double_quotes(line[: direct.start()]):
            invoked.append((number, "command"))
            continue
        if B.search(line):
            invoked.append((number, "argument-vector"))
    if invoked and path not in merge_paths:
        for number, how in invoked:
            print(f"INVOCATION_OUTSIDE {path}:{number}:{how}")
    elif path in merge_paths and not invoked:
        print(f"PATH_DECLARED_WITHOUT_INVOCATION {path}")

print(f"SCOPE files={scanned}")
PY
)"; then
  cannot "merge-surface-unscannable — the scanner could not read the tree: $surface_out"
  surface_out=""
fi

undeclared_surface=0
outside=0
silent=0
while IFS= read -r line; do
  case "$line" in
    SURFACE_UNDECLARED\ *)
      fail "merge-surface-undeclared:${line#SURFACE_UNDECLARED } — this file carries a merge verb and is in neither merge_paths nor verb_only_paths; declare it (reviewing which it is) or remove the verb"
      undeclared_surface=$((undeclared_surface + 1)) ;;
    INVOCATION_OUTSIDE\ *)
      fail "merge-invocation-outside-queue-identity:${line#INVOCATION_OUTSIDE } — this is a merge path the declared queue identity does not cover"
      outside=$((outside + 1)) ;;
    PATH_DECLARED_WITHOUT_INVOCATION\ *)
      fail "merge-path-declared-without-invocation:${line#PATH_DECLARED_WITHOUT_INVOCATION } — the declaration claims a merge path the scanner cannot find an invocation in; the identity's surface must be minimal and true"
      silent=$((silent + 1)) ;;
    SCOPE\ *) ok "merge surface scanned (${line#SCOPE })" ;;
  esac
done <<EOF
$surface_out
EOF
if [ "$undeclared_surface" -eq 0 ] && [ "$outside" -eq 0 ] && [ "$silent" -eq 0 ] && [ -n "$surface_out" ]; then
  ok "no merge surface is undeclared, and the queue identity's declared paths are exactly the invocations the scanner found"
fi

# --- arm 4: a break-glass USE must leave an audit record -------------------
if [ -f "$bg_schema" ]; then
  ok "the break-glass audit record's schema is present ($bg_schema)"
else
  fail "break-glass-schema-missing:$bg_schema"
fi
if [ -f "$bg_validator" ]; then
  if python3 "$bg_validator" --self-test >"$TMPD/validate.out" 2>&1; then
    ok "the audit record validator proves its own refusals ($(grep -c '^  ok' "$TMPD/validate.out" 2>/dev/null) controls)"
  else
    fail "break-glass-validator-self-test-failed — $(tail -1 "$TMPD/validate.out" 2>/dev/null)"
  fi
else
  fail "break-glass-validator-missing:$bg_validator"
fi
if [ -f "$bg_journal" ]; then
  audit_args=(uses "$bg_journal" --records-dir "$bg_records" --root "$root")
  [ -n "$max_window" ] && audit_args+=(--max-window-minutes "$max_window")
  [ -n "$actor" ] && audit_args+=(--expect-actor "$actor")
  [ -n "$principal" ] && audit_args+=(--expect-principal "$principal")
  audit_out="$(python3 "$bg_validator" "${audit_args[@]}" 2>&1)"
  audit_rc=$?
  if [ "$audit_rc" -eq 0 ]; then
    uses_seen="$(python3 -c 'import json,sys;print(len(json.load(open(sys.argv[1])).get("uses") or []))' "$bg_journal" 2>/dev/null || echo "?")"
    ok "every break-glass use on record resolves to a valid audit record (uses measured: $uses_seen)"
  elif [ "$audit_rc" -eq 1 ]; then
    while IFS= read -r finding; do
      [ -n "$finding" ] && fail "break-glass-$finding"
    done <<EOF
$audit_out
EOF
  else
    cannot "break-glass-audit-unvalidated — the record validator is CANNOT-ASSESS: $(printf '%s' "$audit_out" | tail -1)"
  fi
else
  fail "break-glass-journal-missing:$bg_journal — a break-glass use has nowhere to be recorded, so it cannot be audited"
fi

# --- arm 5: the live platform restriction (judged from a reading, or not) --
if [ -n "$live_fixture" ]; then
  live_out="$(python3 - "$live_fixture" "$principal" "$actor" <<'PY'
import json
import sys

path, principal, actor = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    with open(path, encoding="utf-8") as handle:
        reading = json.load(handle)
except (OSError, ValueError):
    print("CANNOT-ASSESS reading-unreadable")
    raise SystemExit(2)

allowed = reading.get("allowed_principals")
if not isinstance(allowed, list) or not allowed:
    print("CANNOT-ASSESS reading-has-no-principals")
    raise SystemExit(2)

declared = [p for p in (principal, actor) if p]
for name in allowed:
    if name not in declared:
        print(f"LIVE_PRINCIPAL_NOT_DECLARED {name}")
for name in declared:
    if name not in allowed:
        print(f"LIVE_PRINCIPAL_MISSING {name}")
print(f"LIVE_READ OK declared={','.join(declared)} live={','.join(str(a) for a in allowed)}")
PY
)"
  live_rc=$?
  if [ "$live_rc" -eq 2 ]; then
    cannot "live-ruleset-unreadable — ${live_out#CANNOT-ASSESS }"
  else
    while IFS= read -r line; do
      case "$line" in
        LIVE_PRINCIPAL_NOT_DECLARED\ *)
          fail "live-principal-not-declared:${line#LIVE_PRINCIPAL_NOT_DECLARED } — the live ruleset lets a principal merge that the declaration does not name" ;;
        LIVE_PRINCIPAL_MISSING\ *)
          fail "live-principal-missing:${line#LIVE_PRINCIPAL_MISSING } — the live ruleset does not grant the declared principal, so the queue cannot land what it is declared to land" ;;
        LIVE_READ\ OK*) ok "the live merge restriction agrees with the declaration (${line#LIVE_READ OK })" ;;
      esac
    done <<EOF
$live_out
EOF
  fi
else
  cannot "live-ruleset-not-asserted — no reading of the live ruleset was supplied, so the platform-side half of the merge right is UNOBSERVED and this run does not claim it"
  printf '        operator step: read the rulesets that guard master and normalise the reading to\n' >&2
  printf '          {"branch":"master","allowed_principals":["<who may bypass>", ...]}\n' >&2
  printf '        e.g. gh api "/orgs/%s/rulesets" --paginate and gh api "repos/%s/rulesets", then each\n' "${repo_slug%%/*}" "$repo_slug" >&2
  printf '        ruleset detail for its bypass_actors; then re-run:\n' >&2
  printf '          bash scripts/check-merge-identity.sh --live-fixture <file>\n' >&2
fi

# --------------------------------------------------------------------------
# the self-test: every refusal above, provoked in a scratch venue, driving
# THIS script through --root (the same arms, never a copy of them).
# --------------------------------------------------------------------------
SELFTEST_FAILED=0

st_fail() { printf '  FAIL  %s\n' "$*" >&2; SELFTEST_FAILED=$((SELFTEST_FAILED + 1)); }
st_ok() { printf '  ok    %s\n' "$*"; }

# st_contains <text> <needle> — bash-native containment (a pipe into grep is the
# shape scripts/check-verdict-contains.sh refuses: grep -q exits on its first
# match, the producer is killed by SIGPIPE, and the branch that REPORTS the
# failure is the branch that gets skipped).
st_contains() {
  case "$1" in
    *"$2"*) return 0 ;;
    *) return 1 ;;
  esac
}

# venue <name> -> the path of a git-tracked scratch checkout seeded from the
# real declaration and the real break-glass module.
venue() {
  local dir="$TMPD/venue-$1"
  mkdir -p "$dir/governance/platform" "$dir/scripts" || return 1
  cp -r "$real_root/governance/platform/break-glass" "$dir/governance/platform/" || return 1
  cp "$real_root/$POLICY" "$dir/$POLICY" || return 1
  git -C "$dir" init -q >/dev/null 2>&1 || return 1
  printf '%s\n' "$dir"
}

venue_commit() {
  git -C "$1" -c user.name=selftest -c user.email=selftest@example.invalid add -A >/dev/null 2>&1
  git -C "$1" -c user.name=selftest -c user.email=selftest@example.invalid commit -q -m selftest >/dev/null 2>&1
}

# policy_set <dir> <dotted.key> <json-value> -- a venue's declaration is edited
# as DATA, so a provocation cannot silently miss its field.
policy_set() {
  python3 - "$1/$POLICY" "$2" "$3" <<'PY'
import json
import sys

import yaml

path, dotted, raw = sys.argv[1], sys.argv[2], sys.argv[3]
with open(path, encoding="utf-8") as handle:
    data = yaml.safe_load(handle)
node = data
parts = dotted.split(".")
for key in parts[:-1]:
    node = node.setdefault(key, {})
node[parts[-1]] = json.loads(raw)
with open(path, "w", encoding="utf-8") as handle:
    yaml.safe_dump(data, handle, sort_keys=False)
PY
}

# st_first_fail <text> — the first refusal line of a venue run, for a failure
# MESSAGE. Bash-native on purpose: a pipe into grep is the shape
# scripts/check-verdict-contains.sh refuses, because the producer is killed by
# SIGPIPE on grep's first match and the branch that reports the failure is the
# branch that gets skipped.
st_first_fail() {
  local line
  while IFS= read -r line; do
    case "$line" in
      "  FAIL"*) printf '%s' "$line"; return 0 ;;
    esac
  done <<EOF
$1
EOF
  printf '%s' "(no FAIL line in the venue's output)"
}

# run_venue <dir> [args...] -> rc, output in $st_out
expect_finding() { # <label> <name> <dir> [args...]
  local label="$1" name="$2" dir="$3"
  shift 3
  local out rc
  out="$(bash "$self" --root "$dir" "$@" 2>&1)"
  rc=$?
  if [ "$rc" -ne 1 ]; then
    st_fail "$label — expected rc 1 (refused), got rc=$rc; output: $(st_first_fail "$out")"
    return 0
  fi
  case "$out" in
    *"$name"*) st_ok "refused by name: $label ($name)" ;;
    *) st_fail "$label — refused, but NOT by the name $name; output: $(st_first_fail "$out")" ;;
  esac
}

real_root="$root"
if [ "$self_test" -eq 1 ]; then
  if [ "$FAILED" -gt 0 ]; then
    echo "check-merge-identity: NOT-OK — $FAILED finding(s) in the real tree; the self-test's provocations are only meaningful once those are fixed" >&2
    exit 1
  fi
  echo "== check-merge-identity self-test (scratch venues, no network) =="

  # N1 — a compliant venue with a compliant reading is NOT refused, and a file
  # that carries no merge verb produces NO finding (vacuity: the scanner must
  # not fire on everything, or every refusal below would be worthless).
  v="$(venue compliant)" || st_fail "could not build the compliant venue"
  printf '#!/usr/bin/env bash\necho hello\n' >"$v/scripts/clean.sh"
  printf '{"branch":"master","allowed_principals":["ao-merge-queue","cto-root"]}\n' >"$v/reading.json"
  venue_commit "$v"
  st_out="$(bash "$self" --root "$v" --live-fixture "$v/reading.json" 2>&1)"; st_rc=$?
  if [ "$st_rc" -eq 0 ]; then
    st_ok "negative control: a compliant declaration, surface AND reading exit 0"
  else
    st_fail "negative control: a compliant venue should exit 0, got rc=$st_rc: $(st_first_fail "$st_out")"
  fi
  case "$st_out" in
    *SURFACE_UNDECLARED* | *merge-surface-undeclared* | *merge-invocation-outside*)
      st_fail "vacuity: a clean file produced a merge-surface finding" ;;
    *)
      st_ok "vacuity: a file carrying no merge verb produces no finding" ;;
  esac

  # N2 — the same compliant venue WITHOUT a reading is CANNOT-ASSESS (2), never 0.
  st_out="$(bash "$self" --root "$v" 2>&1)"; st_rc=$?
  if [ "$st_rc" -eq 2 ] && st_contains "$st_out" live-ruleset-not-asserted; then
    st_ok "negative control: an unobserved live half is CANNOT-ASSESS (rc 2), never a pass"
  else
    st_fail "an unobserved live ruleset must be CANNOT-ASSESS, got rc=$st_rc: $(printf '%s' "$st_out" | tail -2)"
  fi

  # P1 — the queue identity is empty.
  v="$(venue empty-identity)" || st_fail "could not build the empty-identity venue"
  policy_set "$v" merge_identity.principal '""'
  venue_commit "$v"
  expect_finding "an empty queue identity" "merge-identity-undeclared" "$v"

  # P2 — the break-glass actor is empty.
  v="$(venue empty-actor)" || st_fail "could not build the empty-actor venue"
  policy_set "$v" merge_identity.break_glass.actor '""'
  venue_commit "$v"
  expect_finding "an empty break-glass actor" "break-glass-actor-undeclared" "$v"

  # P3 — a MERGE PATH that bypasses the queue identity: the actual defect.
  v="$(venue bypass)" || st_fail "could not build the bypass venue"
  printf '#!/usr/bin/env bash\ngh pr merge "$1" --squash\n' >"$v/scripts/planted-sneak.sh"
  venue_commit "$v"
  expect_finding "a merge call outside the queue identity" "merge-invocation-outside-queue-identity:scripts/planted-sneak.sh" "$v"

  # P4 — a declared merge path nothing invokes (the control must be two-way).
  v="$(venue silent-path)" || st_fail "could not build the silent-path venue"
  printf '#!/usr/bin/env bash\n# nothing merges here\n' >"$v/scripts/declared-but-silent.sh"
  policy_set "$v" merge_identity.merge_paths '["scripts/declared-but-silent.sh"]'
  venue_commit "$v"
  expect_finding "a declared merge path with no invocation" "merge-path-declared-without-invocation:scripts/declared-but-silent.sh" "$v"

  # P5 — a file carrying the endpoint literal but declared nowhere.
  v="$(venue undeclared-surface)" || st_fail "could not build the undeclared-surface venue"
  printf '#!/usr/bin/env bash\necho "repos/x/pulls/42/merge"\n' >"$v/scripts/mentions-endpoint.sh"
  venue_commit "$v"
  expect_finding "an undeclared merge surface" "merge-surface-undeclared:scripts/mentions-endpoint.sh" "$v"

  # P6 — break-glass used, no audit record: the case the issue names.
  v="$(venue use-no-record)" || st_fail "could not build the use-no-record venue"
  bg="$v/governance/platform/break-glass"
  printf '{"schema":"ao.break-glass-uses/v1","uses":[{"id":"bg-20260921T120001Z-abc123","at":"2026-09-21T12:00:01Z","actor":"cto-root","action":"merge","reason":"the queue identity was wedged","record":"%s/records/bg-20260921T120001Z-abc123.json"}]}\n' "$bg" >"$bg/uses.json"
  venue_commit "$v"
  expect_finding "a break-glass use with no audit record" "break-glass-use-without-audit-record:bg-20260921T120001Z-abc123" "$v"

  # P7 — the record exists but its shape is wrong (a refusal by FIELD name).
  mkdir -p "$bg/records"
  printf '{"schema":"ao.break-glass-record/v1","id":"bg-20260921T120001Z-abc123","at":"2026-09-21T12:00:01Z","actor":"cto-root","actor_kind":"team","principal_expected":"ao-merge-queue","action":"merge","branch":"master","reason":"the queue identity was wedged","window_minutes":5,"expires_at":"2026-09-21T12:05:01Z","evidence":"x","declared_by":"issue #1276"}\n' >"$bg/records/bg-20260921T120001Z-abc123.json"
  venue_commit "$v"
  expect_finding "an audit record with no page to the owner" "record-missing-field:paged" "$v"

  # P8 — the record exists and is well shaped, but names a principal that is
  # not the declared break-glass actor.
  v="$(venue wrong-actor)" || st_fail "could not build the wrong-actor venue"
  bg="$v/governance/platform/break-glass"
  mkdir -p "$bg/records"
  printf '{"schema":"ao.break-glass-uses/v1","uses":[{"id":"bg-20260921T120002Z-abc123","at":"2026-09-21T12:00:02Z","actor":"some-agent","action":"merge","reason":"escalated","record":"%s/records/bg-20260921T120002Z-abc123.json"}]}\n' "$bg" >"$bg/uses.json"
  printf '{"schema":"ao.break-glass-record/v1","id":"bg-20260921T120002Z-abc123","at":"2026-09-21T12:00:02Z","actor":"some-agent","actor_kind":"user","principal_expected":"ao-merge-queue","action":"merge","branch":"master","pr":4242,"reason":"an agent token merged without the queue","window_minutes":5,"expires_at":"2026-09-21T12:05:02Z","paged":{"channel":"#ao-escalation","at":"2026-09-21T12:00:03Z","ack_by":"kushin77"},"evidence":"rc=200","declared_by":"issue #1276"}\n' >"$bg/records/bg-20260921T120002Z-abc123.json"
  venue_commit "$v"
  expect_finding "a record attributed to an undeclared actor" "record-actor-not-declared:some-agent" "$v"

  # P9/N3 — the LIVE reading: an undeclared principal, and a missing one.
  v="$(venue live)" || st_fail "could not build the live venue"
  printf '{"branch":"master","allowed_principals":["ao-merge-queue","cto-root","claude-agent"]}\n' >"$v/reading-extra.json"
  printf '{"branch":"master","allowed_principals":["someone-else"]}\n' >"$v/reading-missing.json"
  venue_commit "$v"
  expect_finding "a live principal the declaration does not name" "live-principal-not-declared:claude-agent" "$v" --live-fixture "$v/reading-extra.json"
  expect_finding "a live ruleset that does not grant the queue identity" "live-principal-missing:ao-merge-queue" "$v" --live-fixture "$v/reading-missing.json"
  st_out="$(bash "$self" --root "$v" --live-fixture "$v/absent.json" 2>&1)"; st_rc=$?
  if [ "$st_rc" -eq 2 ] && st_contains "$st_out" live-ruleset-unreadable; then
    st_ok "an unreadable reading is CANNOT-ASSESS (rc 2), never a pass"
  else
    st_fail "an unreadable live reading must be CANNOT-ASSESS, got rc=$st_rc"
  fi

  if [ "$SELFTEST_FAILED" -gt 0 ]; then
    printf 'check-merge-identity: NOT-OK — self-test: %d control(s) failed\n' "$SELFTEST_FAILED" >&2
    exit 1
  fi
  echo "check-merge-identity: OK — self-test: every refusal was provoked by name, and the compliant venue passed"
  exit 0
fi

if [ "$FAILED" -gt 0 ]; then
  printf 'check-merge-identity: NOT-OK — %d finding(s); the merge right is not confined to the declared identity\n' "$FAILED" >&2
  exit 1
fi
if [ "$CANNOT" -gt 0 ]; then
  printf 'check-merge-identity: CANNOT-ASSESS — %d arm(s) could not be judged (never a pass: the live half is unobserved)\n' "$CANNOT" >&2
  exit 2
fi
echo "check-merge-identity: OK — the merge right is declared and confined to the queue identity"
exit 0
