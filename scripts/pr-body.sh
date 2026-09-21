#!/usr/bin/env bash
# pr-body.sh — render a PR body from the issue, so the trailer paragraph
# `scripts/check-squash-message.sh` demands is never hand-typed (issue #1674).
#
# The trailer paragraph is generated LAST and is the ONLY thing in the
# trailing paragraph, so it always survives as the trailer block: a `Refs`
# line (repo convention, `AGENTS.md` rule 1) plus a bare `Closes #<n>` /
# `Refs #<n>` line as the final line, exactly what the squash guard's
# `closes_finding`/shared predicate look for.
#
# Refuses `epic-target:<n>` (stdout note + exit 1) when the issue carries
# label `type:epic` — an epic is never closed by a single PR.
#
# Usage:
#   scripts/pr-body.sh <issue-number> [--refs] [--repo <owner/repo>]
#
# ---knowledge---
# module_id: scripts.pr-body
# system: scripts
# app: scripts
# solution_class: enterprise
# patterns: [trailer-enforced, generated-never-hand-typed]
# derives_from: scripts/check-squash-message.sh
# owner_sme: qa-sme
# tier: L1
# interfaces: [render a PR body from the issue]
# invariants: "the trailer paragraph is generated LAST and is the ONLY thing in the trailing paragraph"
# gotchas: ""
# related: ["#1674"]
# do_not_duplicate: null
# ---knowledge---
set -euo pipefail

repo="kushin77/agent-orchestrator"
issue=""
mode="closes"

while [ $# -gt 0 ]; do
  case "$1" in
    --refs) mode="refs"; shift ;;
    --repo) repo="${2:-}"; shift 2 ;;
    -h|--help) echo "usage: $0 <issue-number> [--refs] [--repo <owner/repo>]" >&2; exit 0 ;;
    *) issue="$1"; shift ;;
  esac
done

if [ -z "$issue" ]; then
  echo "pr-body: usage: $0 <issue-number> [--refs] [--repo <owner/repo>]" >&2
  exit 2
fi

title="$(gh issue view "$issue" --repo "$repo" --json title -q .title 2>/dev/null || true)"
labels="$(gh issue view "$issue" --repo "$repo" --json labels -q '.labels[].name' 2>/dev/null || true)"

if printf '%s\n' "$labels" | grep -qx 'type:epic'; then
  echo "pr-body: epic-target:${issue} — an epic is not closed by a single PR" >&2
  exit 1
fi

trailer_line="Closes #${issue}"
if [ "$mode" = "refs" ]; then
  trailer_line="Refs #${issue}"
fi

cat <<EOF
## Summary
- ${title:-issue #${issue}}

🤖 Generated with [Claude Code](https://claude.com/claude-code)

Refs ${repo}#${issue}
${trailer_line}
EOF
