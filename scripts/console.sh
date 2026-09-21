#!/usr/bin/env bash
# console.sh — serve the operator browser console from ANY working directory
# (`make console`, issue #763).
#
# WHY this is a script and not the raw module invocation: `python3 -m
# portal.server.main` resolves the `portal` package against the CURRENT
# directory, so it only works when the shell is already at the repo root. Run it
# from `$HOME` and Python reports `No module named 'portal'` — a working-directory
# trap, not a broken console, and exactly the failure an operator hit. The
# wrapper resolves the repo root from its own path and execs the module there, so
# the documented command is the same from anywhere:
#
#     bash <repo>/scripts/console.sh            # loopback:8787
#     make -C <repo> console                    # same thing, one word
#
# The defaults stay the safe ones: loopback, and the auth gate FAILS CLOSED with
# no JWKS mirror configured (portal/server/main.py). Binding a reachable
# interface is a deliberate act and is announced as one; see
# docs/OPERATOR-ACCESS.md §4.
#
# Usage: bash scripts/console.sh [--host H] [--port P]
#
# ---knowledge---
# module_id: scripts.console
# system: scripts
# app: scripts
# solution_class: pattern
# patterns: []
# derives_from: null
# owner_sme: platform-sme
# tier: L0
# interfaces: [exec python3 -m portal.server.main]
# invariants: ""
# gotchas: ""
# related: ["#763"]
# do_not_duplicate: null
# ---knowledge---
set -uo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"
cd "$root" || exit 2

host="127.0.0.1"
port="8787"
while [ "$#" -gt 0 ]; do
  case "$1" in
    --host)
      host="${2:-}"
      shift 2
      ;;
    --port)
      port="${2:-}"
      shift 2
      ;;
    -h | --help)
      printf 'usage: bash scripts/console.sh [--host H] [--port P]\n'
      exit 0
      ;;
    *)
      printf 'console: unknown argument %s\n' "$1" >&2
      exit 2
      ;;
  esac
done

if [ -z "$host" ] || [ -z "$port" ]; then
  printf 'console: --host and --port both need a value\n' >&2
  exit 2
fi

# Binding anything but loopback is the one decision this script must not make
# quietly: the console has no login of its own and refuses every session until an
# auth-gate JWKS mirror is configured.
case "$host" in
  127.0.0.1 | localhost | ::1) ;;
  *)
    printf 'console: WARNING — binding %s, which is reachable beyond this host.\n' "$host" >&2
    printf 'console: the console has NO login of its own and FAILS CLOSED until\n' >&2
    printf 'console: PORTAL_AUTH_GATE_JWKS_FILE (and ROOT_ADMIN_EMAILS) are set.\n' >&2
    printf 'console: read docs/OPERATOR-ACCESS.md §4 before exposing it.\n' >&2
    ;;
esac

printf 'console: repo root %s\n' "$root"
printf 'console: serving on http://%s:%s (loopback by default; FAILS CLOSED with no JWKS mirror)\n' \
  "$host" "$port"
exec python3 -m portal.server.main --host "$host" --port "$port"
