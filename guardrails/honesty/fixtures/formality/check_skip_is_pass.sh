#!/usr/bin/env bash
# FORMALITY FIXTURE (issue #28): SKIP counted as PASS.
#
# Real incident shape: a health check reports the provider is fine by skipping
# it whenever it was never configured.  A SKIP that returns 0 on a surface
# where every provider must be checked is a pass that never ran.

check_provider_health() {
  if [ -z "${PROVIDER_STATUS:-}" ]; then
    echo "SKIP: provider not configured"
    return 0
  fi
  if grep -q "UP" "${PROVIDER_STATUS}"; then
    return 0
  fi
  return 0
}

check_provider_health
exit 0
