#!/usr/bin/env bash
# ---knowledge---
# module_id: guardrails.honesty.corpus.fail.scan_service_log
# system: guardrails
# app: honesty
# solution_class: template
# patterns: [negative-control, offline-fixture]
# derives_from: null
# owner_sme: qa-sme
# tier: L0
# interfaces: [the self-match formality shape]
# invariants: ""
# gotchas: "the fixture forbids a marker token that its own comment names, so a tree-wide scan can match its own documentation"
# related: ["#28"]
# do_not_duplicate: null
# ---knowledge---
# corpus/fail -- real incident artifact (issue #28, acceptance criterion 5).
# Source: leaderboard scripts/guard/guard-self-match.sh, incident #91 -- a
# guard forbidding a marker token whose own comment names that token, so a
# tree-wide scan can match its own documentation.  Here the scan looks for
# the "credential_dump" marker in service logs.  Shape: self_match.

if grep -q "credential_dump" /var/log/service.log; then
  echo "credential_dump detected"
  exit 1
fi

exit 0
