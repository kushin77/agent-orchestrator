#!/usr/bin/env bash
# mirror-auth-gate-jwks.sh — publish the OS auth gate's JWKS into Secret Manager (#730).
#
# The console verifies its session tokens OFFLINE, against a mirror of the auth
# gate's published key set. `infra/portal/auth-env.json` declares that mirror as a
# Secret Manager secret mounted into the container; this is the other half — the
# job that PUTS the mirror there. Without it the declaration names a secret that
# holds nothing, and the container exits 1 at boot rather than refusing sessions
# politely (`ConsoleAuthError … cannot be read`).
#
# It is one fetch and one publish, with three properties that matter:
#
#   * it validates with the CONSOLE'S OWN predicate
#     (`portal.server.sso.trusted_keys_from_jwks`), so the mirror and the console
#     cannot disagree about what a usable key set is: an empty or unusable
#     `{"keys": []}` is refused here, by name, instead of being published and
#     then crashing the console;
#   * the payload reaches Secret Manager on STDIN and never on argv — a
#     `--data-file=<payload>` form is indistinguishable, to a mechanical secret
#     scan, from a hardcoded credential (the rule infra/cloudflare/ao-ssh-access.sh
#     already follows for its token);
#   * nothing prints the payload. The evidence is the key ids and a digest of the
#     published bytes, which is what an operator needs to confirm a rollover.
#
# Dry-run by default: it prints what it would publish and writes nothing until
# `--apply` is passed. The secret's CONTAINER is created by the go-live runbook,
# not by this script, unless `--create` is given (a script that silently creates
# a secret is a script that silently gets it wrong).
#
# Usage:
#   bash infra/portal/mirror-auth-gate-jwks.sh --gate-url https://ai.purebliss.app
#   bash infra/portal/mirror-auth-gate-jwks.sh --gate-url URL --project P --apply
#   bash infra/portal/mirror-auth-gate-jwks.sh --jwks-file payload.json --project P --apply --create
#
# Exit: 0 OK / 1 NOT-OK (refused) / 2 CANNOT-ASSESS (an input or tool is missing).
set -u

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$repo_root" || exit 2

declare -r auth_env_script="infra/portal/auth_env.py"

gate_url="${AUTH_GATE_URL:-}"
jwks_file=""
secret_id=""
project="${GOOGLE_CLOUD_PROJECT:-${CLOUDSDK_CORE_PROJECT:-}}"
apply=0
create=0

usage() {
  sed -n '2,40p' "$0" | sed 's/^# \{0,1\}//'
}

while [ $# -gt 0 ]; do
  case "$1" in
    --gate-url) gate_url="${2:-}"; shift 2 ;;
    --jwks-file) jwks_file="${2:-}"; shift 2 ;;
    --secret) secret_id="${2:-}"; shift 2 ;;
    --project) project="${2:-}"; shift 2 ;;
    --apply) apply=1; shift ;;
    --create) create=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'mirror-auth-gate-jwks: unknown argument %s\n' "$1" >&2; exit 2 ;;
  esac
done

command -v python3 >/dev/null 2>&1 || {
  echo "mirror-auth-gate-jwks: CANNOT-ASSESS — python3 not found" >&2
  exit 2
}

# The secret id comes from the declaration, so the job and the deploy cannot name
# two different secrets. An explicit --secret overrides it (a staged rollover).
if [ -z "$secret_id" ]; then
  secret_id="$(python3 - "$repo_root" <<'PY'
import sys
from pathlib import Path

sys.path.insert(0, str(Path(sys.argv[1])))
from infra.portal import auth_env  # noqa: E402

root = Path(sys.argv[1])
declaration = auth_env.load_declaration(root)
entry = auth_env.entry_for(declaration, "PORTAL_AUTH_GATE_JWKS_FILE")
print(entry["secret_id"])
PY
)" || { echo "mirror-auth-gate-jwks: CANNOT-ASSESS — the declaration cannot be read" >&2; exit 2; }
fi
[ -n "$secret_id" ] || { echo "mirror-auth-gate-jwks: CANNOT-ASSESS — no secret id declared or given" >&2; exit 2; }

# The payload: a file when given (offline, and how the gate drives this script),
# else the auth gate's published key set. `--fail` makes an HTML error page a
# refusal here rather than a published non-JSON mirror.
payload=""
if [ -n "$jwks_file" ]; then
  [ -f "$jwks_file" ] || { printf 'mirror-auth-gate-jwks: NOT-OK — --jwks-file %s does not exist\n' "$jwks_file" >&2; exit 1; }
  payload="$(cat -- "$jwks_file")"
else
  [ -n "$gate_url" ] || {
    echo "mirror-auth-gate-jwks: CANNOT-ASSESS — give --gate-url (or AUTH_GATE_URL), or --jwks-file" >&2
    exit 2
  }
  command -v curl >/dev/null 2>&1 || {
    echo "mirror-auth-gate-jwks: CANNOT-ASSESS — curl not found (no network fetch possible)" >&2
    exit 2
  }
  url="${gate_url%/}/auth/.well-known/jwks.json"
  if ! payload="$(curl -fsS --max-time 20 "$url")"; then
    printf 'mirror-auth-gate-jwks: NOT-OK — the gate did not publish a key set at %s\n' "$url" >&2
    exit 1
  fi
fi

# The console's own predicate decides what a usable mirror is. stdin, not argv:
# the payload never appears in this process's arguments.
if ! evidence="$(printf '%s' "$payload" | python3 "$auth_env_script" check-jwks --root "$repo_root")"; then
  echo "mirror-auth-gate-jwks: NOT-OK — the payload is not a mirror the console can verify against" >&2
  exit 1
fi

printf 'mirror-auth-gate-jwks: %s\n' "$evidence"
printf '  secret   %s%s\n' "$secret_id" "${project:+ (project $project)}"
printf '%s\n' "$wiring" | sed 's/^  /  reaches  /'

if [ "$apply" -ne 1 ]; then
  echo "  mode     DRY RUN — nothing published; pass --apply to add a new secret version"
  exit 0
fi

command -v gcloud >/dev/null 2>&1 || {
  echo "mirror-auth-gate-jwks: CANNOT-ASSESS — gcloud not found; cannot publish (pass --apply only where it is installed)" >&2
  exit 2
}
[ -n "$project" ] || {
  echo "mirror-auth-gate-jwks: CANNOT-ASSESS — pass --project (or set GOOGLE_CLOUD_PROJECT) to publish" >&2
  exit 2
}

# The secret's CONTAINER is the runbook's to create, so its absence is a refusal
# naming the exact command (unless --create was given): a script that silently
# creates a secret is a script that silently gets it wrong.
if ! describe_out="$(gcloud secrets describe "$secret_id" --project "$project" 2>&1)"; then
  if [ "$create" -ne 1 ]; then
    printf 'mirror-auth-gate-jwks: NOT-OK — no secret %s in project %s\n' "$secret_id" "$project" >&2
    printf '%s\n' "$describe_out" >&2
    printf '  create the container first (the go-live runbook owns it):\n' >&2
    printf '    gcloud secrets create %s --project %s --replication-policy=automatic\n' "$secret_id" "$project" >&2
    printf '  or re-run with --create.\n' >&2
    exit 1
  fi
  if ! create_out="$(gcloud secrets create "$secret_id" --project "$project" --replication-policy=automatic 2>&1)"; then
    printf 'mirror-auth-gate-jwks: NOT-OK — could not create secret %s in %s\n' "$secret_id" "$project" >&2
    printf '%s\n' "$create_out" >&2
    exit 1
  fi
  printf '  created  secret container %s\n' "$secret_id"
fi

# The payload goes in on stdin: never an argv value, never a file on disk (GR-6).
if ! version="$(printf '%s' "$payload" | gcloud secrets versions add "$secret_id" \
  --project "$project" --data-file=- --format='value(name)' 2>&1)"; then
  printf 'mirror-auth-gate-jwks: NOT-OK — publishing a version of %s failed\n' "$secret_id" >&2
  printf '%s\n' "$version" >&2
  exit 1
fi

printf '  published %s\n' "${version##*/}"
echo "mirror-auth-gate-jwks: OK — the mirror is published; the next revision mounts it (version latest)"
