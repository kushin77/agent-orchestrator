#!/usr/bin/env bash
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
