#!/usr/bin/env bash
# SELF-MATCH FIXTURE (issue #28): a grep whose pattern matches its own docs.
#
# This scan looks for the "credential_dump" marker in service logs.

if grep -q "credential_dump" /var/log/service.log; then
  echo "credential_dump detected"
  exit 1
fi

exit 0
