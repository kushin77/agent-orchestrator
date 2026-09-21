#!/usr/bin/env bash
# branch-protection.sh — make branch protection a DECLARED, REPRODUCIBLE control.
#
#   apply        PUT the declared policy; idempotent (a second run changes nothing)
#   verify       READ BACK the live protection and diff it against the declaration
#   show         print the declared policy and the live protection side by side
#   break-glass  record ONE use of the break-glass merge authority: append the use
#                to the journal and write its audit record (issue #1276). It
#                refuses to write a record that does not name the page to the
#                owner, because "break-glass pages the owner" without a channel
#                and an acknowledgement is an intention, not evidence.
#
# Exit codes follow the repo tri-state: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# CANNOT-ASSESS IS THE IMPORTANT ONE. `verify` needs the GitHub API. When it
# cannot reach it, the correct answer is *"I could not check"* — never a pass.
# A protection check that fails open is worse than no check at all: it reports
# safety it did not observe. This is the same defect class as #739, where an
# unreadable HEAD read back as `healthy` and silently disabled drift detection.
#
# Why this exists (#803 P0-3): `master` was measured UNPROTECTED — a 404 — while
# `AGENTS.md` claimed it was "protected by convention". The protection was then
# applied by an operator API call, which is a GR-5 breach (infrastructure is
# declared, never clicked) and is not reproducible. This script is the repair:
# the declared policy in governance/platform/branch-protection.yaml is the source
# of truth, `apply` makes the live state match it, and `verify` proves it did.
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

POLICY="governance/platform/branch-protection.yaml"

die() { printf 'branch-protection: %s\n' "$1" >&2; exit "${2:-1}"; }

[ -f "$POLICY" ] || die "CANNOT-ASSESS — declared policy is missing: $POLICY" 2
command -v python3 >/dev/null 2>&1 || die "CANNOT-ASSESS — python3 not found" 2
# `break-glass` records a LOCAL audit artifact (issue #1276): it needs neither
# `gh` nor the network, so the gh precondition does not apply to it. The #1313
# rule cuts both ways — a precondition the invoked verb does not have must not
# turn that verb into CANNOT-ASSESS for a reason no operator can fix.
command -v gh      >/dev/null 2>&1 || [ "${1:-}" = "break-glass" ] || die "CANNOT-ASSESS — gh not found" 2

# Read repo/branch out of the declaration so the policy is the only place they
# are written: two sources for one fact drift.
read -r REPO BRANCH <<EOF
$(python3 - "$POLICY" <<'PY'
import sys, re
text = open(sys.argv[1]).read()
def field(name):
    m = re.search(rf"^{name}:\s*(\S+)\s*$", text, re.M)
    return m.group(1).strip('"\'') if m else ""
print(field("repo"), field("branch"))
PY
)
EOF
[ -n "${REPO:-}" ] && [ -n "${BRANCH:-}" ] || die "CANNOT-ASSESS — policy names no repo/branch" 2
[ "$REPO" = "." ] && REPO="$(gh repo view --json nameWithOwner --jq .nameWithOwner 2>/dev/null)" || true

python3 - "$POLICY" >/tmp/bp-declared.json <<'PY'
import sys, json
try:
    import yaml
except ImportError:
    sys.exit(2)
policy = yaml.safe_load(open(sys.argv[1]))
want, has = policy.get("protection", {}), policy.get("has", {})
print(json.dumps({"want": want, "has": has}))
PY
[ -s /tmp/bp-declared.json ] || die "CANNOT-ASSESS — could not parse the declared policy (PyYAML?)" 2

# Compare the declared policy against a live-state document.
#
# $1 = declared json (want/has), $2 = live json. Exits 1 on any drift.
#
# The comparator lives in its OWN file so that scripts/check-branch-protection.sh
# can PROVOKE the same code path this verify uses. If the gate proved a copy of
# the logic instead, the proof would say nothing about the path that actually
# runs -- measured lesson: a harness that asserted its mutation was caught while
# grepping a string a *passing* run also printed certified nothing at all.
compare() {
  python3 scripts/branch-protection-compare.py "$1" "$2"
}

live_state() {
  gh api "repos/$REPO/branches/$BRANCH/protection" 2>/tmp/bp-err.txt
  local rc=$?
  if [ $rc -ne 0 ]; then
    if grep -q "Branch not protected" /tmp/bp-err.txt 2>/dev/null; then
      printf '{"__unprotected__": true}\n' > /tmp/bp-live.json
      return 0
    fi
    return 2
  fi
}

# The declaration's merge-identity facts, printed ONE `key=value` per line and
# read back BY NAME. Positional reading was the first shape here and it was
# wrong: an empty FIRST field (an undeclared queue identity -- exactly the state
# this control must judge) is leading IFS whitespace, which `read` strips, so
# every later field shifted one place left and a caller read the records
# directory as the journal.
merge_identity_facts() {
  python3 - "$POLICY" <<'PY'
import sys

try:
    import yaml
except ImportError:
    sys.exit(2)

with open(sys.argv[1], encoding="utf-8") as handle:
    data = yaml.safe_load(handle)
mi = (data or {}).get("merge_identity") or {}
bg = mi.get("break_glass") or {}
audit = bg.get("audit_record") or {}
for key, value in (
    ("principal", mi.get("principal") or ""),
    ("actor", bg.get("actor") or ""),
    ("max_window", bg.get("max_window_minutes") or ""),
    ("schema", audit.get("schema") or ""),
    ("validator", audit.get("validator") or ""),
    ("journal", audit.get("journal") or ""),
    ("records_dir", audit.get("records_dir") or ""),
    ("branch", (data or {}).get("branch") or "master"),
):
    print(f"{key}={value}")
PY
}

# record_break_glass -- write the journal entry and (unless --journal-only) the
# audit record, in ONE program, so a use can never end up half recorded. The
# facts that belong to the DECLARATION (the queue identity, the branch) are read
# from the policy here rather than passed in, so the caller cannot state them
# differently.
record_break_glass() {
  python3 - "$POLICY" "$@" <<'PY'
import argparse
import json
import os
import sys

import yaml

policy_path = sys.argv[1]
parser = argparse.ArgumentParser(add_help=False, prog="branch-protection.sh break-glass")
parser.add_argument("--journal")
parser.add_argument("--records-dir")
parser.add_argument("--id", dest="ident")
parser.add_argument("--at")
parser.add_argument("--expires-at")
parser.add_argument("--actor")
parser.add_argument("--actor-kind", default="team")
parser.add_argument("--action", default="merge")
parser.add_argument("--pr", default="")
parser.add_argument("--reason")
parser.add_argument("--window")
parser.add_argument("--channel")
parser.add_argument("--ack-by")
parser.add_argument("--evidence", default="")
parser.add_argument("--declared-by", default="")
parser.add_argument("--journal-only", default="0")
args = parser.parse_args(sys.argv[2:])

with open(policy_path, encoding="utf-8") as handle:
    data = yaml.safe_load(handle)
mi = (data or {}).get("merge_identity") or {}
principal = str(mi.get("principal") or "")
branch = str((data or {}).get("branch") or "master")

record_rel = os.path.join(args.records_dir, args.ident + ".json")
record = {
    "schema": "ao.break-glass-record/v1",
    "id": args.ident,
    "at": args.at,
    "actor": args.actor,
    "actor_kind": args.actor_kind,
    "principal_expected": principal,
    "action": args.action,
    "branch": branch,
    "pr": int(args.pr) if args.pr else None,
    "reason": args.reason,
    "window_minutes": int(args.window),
    "expires_at": args.expires_at,
    "paged": {"channel": args.channel, "at": args.at, "ack_by": args.ack_by},
    "evidence": args.evidence,
    "declared_by": args.declared_by,
}
use = {
    "id": args.ident,
    "at": args.at,
    "actor": args.actor,
    "action": args.action,
    "reason": args.reason,
    "record": record_rel,
}

with open(args.journal, encoding="utf-8") as handle:
    journal = json.load(handle)
journal.setdefault("uses", []).append(use)
with open(args.journal, "w", encoding="utf-8") as handle:
    json.dump(journal, handle, indent=2)
    handle.write("\n")

if args.journal_only != "1":
    os.makedirs(args.records_dir, exist_ok=True)
    with open(record_rel, "w", encoding="utf-8") as handle:
        json.dump(record, handle, indent=2)
        handle.write("\n")

print(record_rel)
PY
}

case "${1:-verify}" in
  show)
    echo "declared ($POLICY):"; python3 -m json.tool /tmp/bp-declared.json 2>/dev/null | head -30
    echo; echo "live ($REPO@$BRANCH):"
    live_state; gh api "repos/$REPO/branches/$BRANCH/protection" 2>/dev/null | python3 -m json.tool 2>/dev/null | head -30 || cat /tmp/bp-err.txt
    ;;

  apply)
    # The PUT body is derived from the declaration, so `apply` cannot drift from
    # what `verify` checks -- one source, two consumers.
    python3 - /tmp/bp-declared.json >/tmp/bp-put.json <<'PY'
import json, sys
want = json.load(open(sys.argv[1]))["want"]
body = {}
for k, v in want.items():
    body[k] = v
print(json.dumps(body))
PY
    gh api -X PUT "repos/$REPO/branches/$BRANCH/protection" \
       -H "Accept: application/vnd.github+json" --input /tmp/bp-put.json >/dev/null 2>/tmp/bp-apply-err.txt \
      || die "apply FAILED — $(head -c 200 /tmp/bp-apply-err.txt)"
    echo "branch-protection: applied the declared policy to $REPO@$BRANCH"
    ;;

  verify)
    live_state
    rc=$?
    if [ $rc -eq 2 ]; then
      # Fail-closed: never report a protection we did not observe.
      echo "branch-protection: CANNOT-ASSESS — the GitHub API was unreachable; the live protection was NOT observed" >&2
      head -c 200 /tmp/bp-err.txt >&2; echo >&2
      exit 2
    fi
    if grep -q "__unprotected__" /tmp/bp-live.json 2>/dev/null; then
      echo "branch-protection: NOT-OK — $REPO@$BRANCH is NOT PROTECTED (the platform enforces nothing)" >&2
      echo "                  remedy: bash scripts/branch-protection.sh apply" >&2
      exit 1
    fi
    gh api "repos/$REPO/branches/$BRANCH/protection" > /tmp/bp-live.json 2>/dev/null || { echo "branch-protection: CANNOT-ASSESS" >&2; exit 2; }
    compare /tmp/bp-declared.json /tmp/bp-live.json
    ;;

  break-glass)
    # Take the break-glass merge authority ON THE RECORD (issue #1276). The
    # right to merge to master is declared to live with one queue identity; this
    # verb is how the ONE other principal exercises it, and the artifact it
    # writes is what makes the exception reviewable afterwards instead of
    # indistinguishable from an agent token merging on its own authority.
    shift
    bg_actor=""; bg_reason=""; bg_pr=""; bg_evidence=""; bg_channel=""; bg_ack=""
    bg_window=""; bg_action="merge"; bg_actor_kind="team"; bg_journal_only=0
    while [ $# -gt 0 ]; do
      case "$1" in
        --actor | --reason | --pr | --evidence | --paged-channel | --paged-ack-by | --window-minutes | --action | --actor-kind)
          if [ $# -lt 2 ] || [ -z "${2:-}" ]; then
            die "CANNOT-ASSESS — $1 needs a value" 2
          fi
          ;;
        --journal-only) ;;  # flag, handled below
        *) die "CANNOT-ASSESS — unknown argument: $1 (see: branch-protection.sh break-glass --help)" 2 ;;
      esac
      case "$1" in
        --actor) bg_actor="$2"; shift 2 ;;
        --reason) bg_reason="$2"; shift 2 ;;
        --pr) bg_pr="$2"; shift 2 ;;
        --evidence) bg_evidence="$2"; shift 2 ;;
        --paged-channel) bg_channel="$2"; shift 2 ;;
        --paged-ack-by) bg_ack="$2"; shift 2 ;;
        --window-minutes) bg_window="$2"; shift 2 ;;
        --action) bg_action="$2"; shift 2 ;;
        --actor-kind) bg_actor_kind="$2"; shift 2 ;;
        --journal-only) bg_journal_only=1; shift ;;
      esac
    done

    bg_facts="$(merge_identity_facts)" || die "CANNOT-ASSESS — pyyaml-missing: the declaration could not be read" 2
    [ -n "$bg_facts" ] || die "CANNOT-ASSESS — the declaration could not be read: $POLICY" 2
    principal=""; decl_actor=""; max_window=""; bg_journal=""; bg_records=""; bg_schema=""
    while IFS= read -r bg_fact; do
      case "$bg_fact" in
        principal=*) principal="${bg_fact#principal=}" ;;
        actor=*) decl_actor="${bg_fact#actor=}" ;;
        max_window=*) max_window="${bg_fact#max_window=}" ;;
        schema=*) bg_schema="${bg_fact#schema=}" ;;
        journal=*) bg_journal="${bg_fact#journal=}" ;;
        records_dir=*) bg_records="${bg_fact#records_dir=}" ;;
      esac
    done <<EOF
$bg_facts
EOF

    [ -n "$bg_actor" ] || die "CANNOT-ASSESS — --actor is required (the declaration names '${decl_actor:-<none>}')" 2
    [ -n "$bg_reason" ] || die "CANNOT-ASSESS — --reason is required: an unexplained bypass cannot be reviewed" 2
    [ -n "$decl_actor" ] || die "break-glass-actor-undeclared — $POLICY names no break_glass.actor" 1
    if [ "$bg_actor" != "$decl_actor" ]; then
      die "break-glass-actor-not-declared:$bg_actor — the declaration names only '$decl_actor' as the break-glass actor" 1
    fi
    [ "${#bg_reason}" -ge 20 ] || die "break-glass-reason-too-short:${#bg_reason} — give at least 20 characters of why the queue identity could not be used" 1
    [ -n "$bg_journal" ] || die "break-glass-journal-missing — $POLICY names no audit_record.journal" 1
    [ -f "$bg_journal" ] || die "break-glass-journal-missing:$bg_journal" 1
    if [ -z "$bg_channel" ] || [ -z "$bg_ack" ]; then
      printf 'branch-protection: CANNOT-ASSESS — page-not-recorded: the record must name the channel the owner was paged on and the owner who acknowledged it; without them this is not a break-glass with evidence, it is a token merge\n' >&2
      printf '  operator step: send the page (the notices runtime, the mailbox directive, or the channel your escalation uses), then re-run with:\n' >&2
      printf '    bash scripts/branch-protection.sh break-glass --actor %s --reason "<why>" --pr <n> --paged-channel "<where>" --paged-ack-by "<who acked>"\n' "$bg_actor" >&2
      exit 2
    fi
    [ -f "$bg_schema" ] || die "break-glass-schema-missing:$bg_schema" 1

    if [ -z "$bg_window" ]; then
      bg_window="${max_window:-120}"
    fi
    case "$bg_window" in
      '' | *[!0-9]*) die "CANNOT-ASSESS — --window-minutes must be a positive integer" 2 ;;
    esac
    [ "$bg_window" -ge 1 ] || die "CANNOT-ASSESS — --window-minutes must be a positive integer" 2
    if [ -n "$max_window" ] && [ "$bg_window" -gt "$max_window" ]; then
      die "break-glass-window-exceeds-ceiling:$max_window — the declaration time-boxes break-glass to $max_window minutes" 1
    fi
    case "${bg_pr:-}" in
      '' | *[!0-9]*) [ -z "$bg_pr" ] || die "CANNOT-ASSESS — --pr must be a pull request number" 2 ;;
    esac

    bg_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    bg_ident="bg-$(date -u +%Y%m%dT%H%M%SZ)-$(python3 -c 'import secrets; print(secrets.token_hex(3))')"
    bg_expires="$(python3 -c '
import datetime, sys
start = datetime.datetime.strptime(sys.argv[1], "%Y-%m-%dT%H:%M:%SZ")
print((start + datetime.timedelta(minutes=int(sys.argv[2]))).strftime("%Y-%m-%dT%H:%M:%SZ"))
' "$bg_at" "$bg_window")"

    bg_record="$(record_break_glass \
      --journal "$bg_journal" --records-dir "$bg_records" --id "$bg_ident" \
      --at "$bg_at" --expires-at "$bg_expires" --actor "$bg_actor" \
      --actor-kind "$bg_actor_kind" --action "$bg_action" --pr "$bg_pr" \
      --reason "$bg_reason" --window "$bg_window" --channel "$bg_channel" \
      --ack-by "$bg_ack" --evidence "$bg_evidence" \
      --declared-by "bash scripts/branch-protection.sh break-glass" \
      --journal-only "$bg_journal_only")" || die "break-glass-record-unwritable — the journal or the record could not be written" 1

    echo "branch-protection: break-glass recorded — $bg_ident"
    echo "  used by         : $bg_actor (the declared break-glass actor; the merge right lives with $principal)"
    echo "  time-box        : $bg_window minutes, expiring $bg_expires"
    echo "  paged           : $bg_channel, acked by $bg_ack at $bg_at"
    if [ "$bg_journal_only" -eq 1 ]; then
      echo "  audit record    : NOT WRITTEN (--journal-only). scripts/check-merge-identity.sh refuses this use"
      echo "                    by name until the record lands — that refusal IS the control."
    else
      echo "  audit record    : $bg_record"
    fi
    echo "  finish it       : git add $bg_journal $bg_records && (commit on a branch, PR, land)"
    echo "                    then: bash scripts/check-merge-identity.sh   # the audit arm re-reads this use"
    exit 0
    ;;

  *) die "usage: $0 {apply|verify|show|break-glass}" 1 ;;
esac
