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
# Renders normally even when the issue carries label `type:epic`: a
# docs-only closeout PR closing an epic directly is real precedent
# (#1962/#1963/#1969), and refusing it forced a hand-authored body that
# silently dropped the AI-assistance/Gate-changing fields check-pr-contract.sh
# requires (issue #1987). Nothing about the epic label changes what this
# script renders — the trailer paragraph shape is identical either way.
#
# Usage:
#   scripts/pr-body.sh <issue-number> [--refs] [--repo <owner/repo>]
#
# ---knowledge---
# module_id: scripts.pr-body
# system: scripts
# app: scripts
# solution_class: class
# patterns: [trailer-generated-last]
# derives_from: scripts/check-squash-message.sh
# owner_sme: platform-sme
# tier: L1
# interfaces: [stdout: PR body markdown]
# invariants: "the trailer line is always the final line, exactly what the squash guard looks for"
# gotchas: "an issue labeled type:epic renders the same as any other issue (#1987)"
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
