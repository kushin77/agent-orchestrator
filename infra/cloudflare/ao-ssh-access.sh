#!/usr/bin/env bash
# ao-ssh-access.sh — the remote operator SSH route, end to end (issues #771,
# #785): provision the tunnel, publish the hostname through it, and deploy the
# connector, with Cloudflare Access in front.
#
# THE PROBLEM
#   Every operator surface this repo ships needs a shell on the host, and the one
#   remote-capable surface (the browser console) refuses every session until the
#   OS auth gate issues a token. This script is the transport half of the remote
#   way in: it publishes a hostname that reaches the host's sshd through the
#   Cloudflare Tunnel, so an operator with no shell on the box reaches one
#   without opening port 22 to the internet. Until #785 the route only did the
#   publish half and ASSUMED the tunnel existed and a connector ran; those two
#   jobs lived in another repo (`shared-services`). This script now owns the
#   whole flow, so the route is one coherent, vendorable module.
#
# THE STAGES (each idempotent)
#   0/5 PROVISION (--provision) find-or-create the tunnel by name; seed the
#       trailing catch-all when it is created. The read is FAIL-CLOSED: an
#       unreadable tunnel list is a refusal, never "no tunnel exists".
#   1/4 merge an `ssh://<origin>:<port>` rule for the hostname into the tunnel's
#       LIVE remote ingress config and PUT the merged array back. The merge is
#       the point of the whole script: the API has no "add one rule" call, so a
#       blind replacement would delete every other hostname the tunnel serves.
#       The merge itself is the pure function in `infra/cloudflare/ingress.py`.
#   2/4 upsert the proxied CNAME `<hostname>` -> `<tunnel id>.cfargotunnel.com`
#       (PUT the record that already carries the name, else POST).
#   3/4 ensure a Cloudflare Access self-hosted application for the hostname plus
#       an allow-policy listing the operator emails. WITHOUT THIS THE HOSTNAME IS
#       AN UNAUTHENTICATED PUBLIC DOOR TO sshd, so there is deliberately no flag
#       that skips it.
#   4/4 verify: the rule is present in the live configuration, and the name
#       resolves (A/AAAA).
#   5/5 CONNECTOR (--connector) deploy `cloudflared` on each connector host,
#       idempotently (pull, rm -f, run --restart unless-stopped).
#
# DRY RUN IS THE DEFAULT. `--apply` is the only way to mutate; it refuses while
# the route's feature flag is OFF (GR-5), and it is an OPERATOR act -- an agent
# never applies it, because it changes an estate this repo does not own. A dry
# run performs NO mutating request: it reads the live config (GET) and prints the
# exact merged ingress it would send, so its output is evidence rather than a
# promise.
#
# NO ESTATE IDENTIFIERS IN THIS TREE. The account id, zone id, tunnel id, origin
# host, connector hosts and operator emails all come from the environment, and a
# missing one is REFUSED BY NAME rather than defaulted -- a default here would
# point the run at somebody else's estate. The API token comes from `CF_API_TOKEN`
# or GCP Secret Manager (`AO_CF_TOKEN_SECRET` + `AO_GCP_SECRET_PROJECT`), and the
# connector's tunnel token from the environment (`AO_SSH_CONNECTOR_TOKEN`, which
# an operator can source from Vault or Secret Manager); never a file, never git
# (GR-6).
#
# Exit codes: 0 the run did what was asked / 1 refusal (flag, configuration,
# token) / 2 usage error / 3 an API or verification failure. Nothing is ever
# reported as applied before it is verified.
#
# Usage:
#   infra/cloudflare/ao-ssh-access.sh              # dry run (the default)
#   infra/cloudflare/ao-ssh-access.sh --apply      # mutate (flag must be ON)
#   infra/cloudflare/ao-ssh-access.sh --provision  # also find-or-create the tunnel
#   infra/cloudflare/ao-ssh-access.sh --connector  # also deploy the connector
# ---knowledge---
# module_id: infra.cloudflare.ao-ssh-access
# system: infra
# app: cloudflare
# solution_class: class
# patterns: [pre-standard-snapshot]
# derives_from: null
# owner_sme: iac-sme
# tier: L1
# interfaces: [usage, refuse, abort, read_flag, require_var, resolve_token, release_lease, cf, (+2 more)]
# invariants: ""
# gotchas: ""
# related: ["#1911"]
# do_not_duplicate: null
# ---knowledge---
set -euo pipefail

SURFACE="remote_ssh_access"
REGISTRY_RELATIVE="infra/feature-flags/registry.yaml"

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# A cd whose failure is not handled leaves the script operating on whatever the
# caller's cwd happened to be. Refuse the failure instead of ignoring it.
cd "$root" || exit 2

DRY_RUN=true
PROVISION=false
CONNECTOR=false
flag_state=""

usage() {
  cat <<'TEXT'
ao-ssh-access.sh — the remote operator SSH route, end to end (issues #771, #785).
Dry run by default; `--apply` mutates and refuses while the route's feature flag
is OFF (GR-5).

Usage:
  infra/cloudflare/ao-ssh-access.sh              # dry run (the default)
  infra/cloudflare/ao-ssh-access.sh --apply      # mutate (flag must be ON)
  infra/cloudflare/ao-ssh-access.sh --provision  # also find-or-create the tunnel
  infra/cloudflare/ao-ssh-access.sh --connector  # also deploy the connector

Environment (every identifier is REQUIRED; a missing one is refused by name):
  CF_ACCOUNT_ID            the Cloudflare account that owns the tunnel
  CF_ZONE_ID               the zone that holds the published hostname
  CF_TUNNEL_ID             the tunnel that serves the hostname (required unless
                           --provision, which finds or creates it instead)
  CF_TUNNEL_NAME           with --provision: the tunnel name to find-or-create
  AO_SSH_HOSTNAME          the public hostname to publish
  AO_SSH_ORIGIN_HOST       the host the tunnel reaches sshd on
  AO_SSH_ACCESS_EMAILS     comma-separated operator emails for the allow-policy
  AO_SSH_ORIGIN_PORT       the sshd port (default 22)
  AO_SSH_ACCESS_SESSION_DURATION  the Access session lifetime (default 24h)
  CF_API_TOKEN             the API token, or use the two GSM variables below
  AO_CF_TOKEN_SECRET       the GCP Secret Manager secret holding the token
  AO_GCP_SECRET_PROJECT    the project that secret lives in
  AO_CF_API_BASE           the API base URL (a seam for offline dry runs)
  AO_SSH_CONNECTOR_HOSTS   with --connector: comma-separated connector host(s)
  AO_SSH_CONNECTOR_USER    with --connector: the SSH user on those host(s)
  AO_SSH_CONNECTOR_TOKEN   with --connector --apply: the tunnel token (env/Vault/GSM)
  AO_SSH_CONNECTOR_KEY     with --connector --apply: path to the SSH private key
  AO_RESOURCE_CLAIM_HOLDER the identity this run holds the surface's lease as
                           (default operator:<user>) -- see the resource lease below
  AO_SSH_CONNECTOR_IMAGE   the cloudflared image (default cloudflare/cloudflared:2026.7.2)
  AO_SSH_CONNECTOR_NETWORK the docker network (default bridge)
  AO_SSH_CONNECTOR_CONTAINER_PREFIX  the connector container name prefix (default ao-tunnel)
TEXT
}
refuse() { printf 'ao-ssh-access: REFUSED: %s\n' "$1" >&2; exit 1; }
abort() { printf 'ao-ssh-access: FAILED: %s\n' "$1" >&2; exit 3; }

while [ "$#" -gt 0 ]; do
  case "$1" in
    --apply) DRY_RUN=false; shift ;;
    --dry-run) DRY_RUN=true; shift ;;
    --provision) PROVISION=true; shift ;;
    --connector) CONNECTOR=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'ao-ssh-access: unknown argument: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
done

# ── the feature flag (GR-5) ─────────────────────────────────────────────────
# Read through `portal.server.fleet.read_surface_default` -- the one fail-closed
# implementation of "is this surface promoted" -- rather than a second parser
# here. The flag is consulted for `--apply` only: previewing a route is harmless
# while it ships OFF, applying it is not.
read_flag() {
  python3 - "$root" "$SURFACE" <<'PY'
import sys

sys.dont_write_bytecode = True
root, surface = sys.argv[1], sys.argv[2]
sys.path.insert(0, root)
try:
    from portal.server.fleet import read_surface_default

    print(read_surface_default(root, surface=surface))
except Exception as exc:  # noqa: BLE001 - fail closed, and say so
    print("off")
    print(
        "ao-ssh-access: note: the feature-flag registry could not be read (%s); "
        "surfaces.%s is treated as off" % (exc, surface),
        file=sys.stderr,
    )
PY
}

if [ "$DRY_RUN" = false ]; then
  flag_state="$(read_flag)"
  if [ "$flag_state" != "on" ]; then
    refuse "surfaces.${SURFACE} is '${flag_state}' in ${REGISTRY_RELATIVE}, so --apply is refused: this route publishes a host to the internet, and new infrastructure ships OFF until a reviewed go-live promotes it (GR-5). Run without --apply for a dry run, or promote the surface first."
  fi
fi

# ── configuration: every identifier from the environment ────────────────────
require_var() { # require_var <NAME> <what it is for>
  local name="$1" why="$2"
  if [ -z "${!name:-}" ]; then
    refuse "${name} is not set (${why}). This route takes its identifiers from the environment only: a default here would point the run at somebody else's estate."
  fi
}

require_var CF_ACCOUNT_ID "the Cloudflare account that owns the tunnel"
require_var CF_ZONE_ID "the zone that holds the published hostname"
if [ "$PROVISION" = true ]; then
  require_var CF_TUNNEL_NAME "the tunnel to find-or-create when --provision is set"
else
  require_var CF_TUNNEL_ID "the tunnel that serves the hostname"
fi
require_var AO_SSH_HOSTNAME "the public hostname to publish"
require_var AO_SSH_ORIGIN_HOST "the host the tunnel reaches sshd on"
require_var AO_SSH_ACCESS_EMAILS "the operator emails the Access allow-policy lists"

case "$AO_SSH_HOSTNAME" in
  */*|*:*|*' '*) refuse "AO_SSH_HOSTNAME must be a bare hostname, not a URL or an address: ${AO_SSH_HOSTNAME}" ;;
esac

ORIGIN_PORT="${AO_SSH_ORIGIN_PORT:-22}"
case "$ORIGIN_PORT" in
  ''|*[!0-9]*) refuse "AO_SSH_ORIGIN_PORT is not a number: ${ORIGIN_PORT}" ;;
esac

SESSION_DURATION="${AO_SSH_ACCESS_SESSION_DURATION:-24h}"

if [ -z "$(printf '%s' "$AO_SSH_ACCESS_EMAILS" | tr -d ' ,')" ]; then
  refuse "AO_SSH_ACCESS_EMAILS lists no address, so the Access application would have no allow-policy: an Access app with an empty policy is not a door worth publishing."
fi

API_BASE="${AO_CF_API_BASE:-https://api.cloudflare.com/client/v4}"

resolve_token() {
  if [ -n "${CF_API_TOKEN:-}" ]; then
    printf 'CF_API_TOKEN (environment)'
    printf '%s' "$CF_API_TOKEN" > "$scratch/token"
    return 0
  fi
  # The Secret Manager name and project are passed as separate arguments on
  # purpose: a `--secret=<value>` form is indistinguishable, to a mechanical
  # secret scan, from a hardcoded credential, so it must not be written that way.
  if [ -n "${AO_CF_TOKEN_SECRET:-}" ] && [ -n "${AO_GCP_SECRET_PROJECT:-}" ] \
    && command -v gcloud >/dev/null 2>&1; then
    if gcloud secrets versions access latest \
      --secret "${AO_CF_TOKEN_SECRET}" --project "${AO_GCP_SECRET_PROJECT}" \
      > "$scratch/token" 2>/dev/null && [ -s "$scratch/token" ]; then
      printf 'GCP Secret Manager (%s in %s)' \
        "${AO_CF_TOKEN_SECRET}" "${AO_GCP_SECRET_PROJECT}"
      return 0
    fi
  fi
  refuse "no Cloudflare API token: set CF_API_TOKEN, or AO_CF_TOKEN_SECRET + AO_GCP_SECRET_PROJECT for GCP Secret Manager (GR-6: environment or Secret Manager only, never a file and never git)."
}

scratch="/tmp/ao-ssh-access.$$.$(date +%s)"
mkdir -p "$scratch" || refuse "cannot create a scratch directory at ${scratch}"

# ── the live-resource lease (issue #1545) ──────────────────────────────────
# `--apply` mutates ONE live resource -- this surface on this Cloudflare estate
# -- and two owners pushing a phase of one surface at the same time is a
# measured incident rather than a hypothetical: each side's push silently
# reverted the other's phase. The lease is keyed by the RESOURCE, so a second
# holder is refused BY NAME (holder + expiry quoted) instead of interleaving,
# and it is released on exit -- or by its declared TTL if this run is killed
# (`governance/policy/lease.py`). A DRY RUN takes no lease: it mutates nothing,
# so it cannot become the second writer the lease exists to stop.
RESOURCE_LEASE_MODULE="governance/dispatch/resource_lease.py"
LEASE_RESOURCE="cloudflare-phase:${SURFACE}"
LEASE_HOLDER="${AO_RESOURCE_CLAIM_HOLDER:-operator:${USER:-unknown}}"
LEASE_ACQUIRED=false

release_lease() {
  # Cleanup must never turn a good run into a failed one, and the TTL is the
  # backstop, so a release that cannot be written is not raised here.
  if [ "$LEASE_ACQUIRED" = true ]; then
    python3 "$RESOURCE_LEASE_MODULE" release \
      --resource "$LEASE_RESOURCE" --holder "$LEASE_HOLDER" >/dev/null 2>&1 || true
  fi
}

# This replaces the scratch-only trap: the lease is released FIRST, because the
# release reads the tree the scratch removal would delete.
trap 'release_lease; rm -rf "$scratch"' EXIT

token_source="$(resolve_token)"
CF_TOKEN="$(cat "$scratch/token")"
rm -f "$scratch/token"

if [ "$DRY_RUN" = false ]; then
  if ! lease_out="$(python3 "$RESOURCE_LEASE_MODULE" acquire \
        --resource "$LEASE_RESOURCE" --holder "$LEASE_HOLDER" --lane cloudflare-phase 2>&1)"; then
    refuse "the live-resource lease on ${LEASE_RESOURCE} was not granted (${lease_out}). Another holder is pushing a phase of this surface: wait for its release or TTL instead of interleaving (issue #1545)."
  fi
  LEASE_ACQUIRED=true
fi

# The one function that talks to the API. Its only mutating entry point is
# `cf_mutate`, which is unreachable in a dry run -- so "a dry run sends nothing"
# is a property of the code rather than of the care taken writing the branches.
# It is defined before the provision stage because both the provision and the
# publish stages call it.
cf() { # cf <METHOD> <PATH> [BODY]
  local method="$1" path="$2" body="${3:-}"
  local -a args=(-sS -X "$method" "${API_BASE}${path}"
    -H "Authorization: Bearer ${CF_TOKEN}" -H "Content-Type: application/json")
  if [ -n "$body" ]; then args+=(--data "$body"); fi
  curl "${args[@]}"
}

cf_mutate() { # cf_mutate <METHOD> <PATH> [BODY]
  if [ "$DRY_RUN" = true ]; then
    abort "internal: a mutating request (${1} ${2}) was attempted in a dry run"
  fi
  cf "$@"
}

ack() { # ack <description> <response json>
  python3 - "$1" "$2" <<'PY'
import json
import sys

what, raw = sys.argv[1], sys.argv[2]
try:
    document = json.loads(raw)
except ValueError:
    print("ao-ssh-access: FAILED: %s returned something that is not JSON: %.200s"
          % (what, raw), file=sys.stderr)
    raise SystemExit(3)
if not isinstance(document, dict) or document.get("success") is not True:
    errors = document.get("errors") if isinstance(document, dict) else raw
    print("ao-ssh-access: FAILED: %s was refused by the API: %s" % (what, errors),
          file=sys.stderr)
    raise SystemExit(3)
print("ao-ssh-access:   ok  %s" % what)
PY
}

# ── 0/5: provision — find-or-create the tunnel (issue #785) ────────────────
# The route used to assume the tunnel already existed. The provision step makes
# that true, idempotently: it looks the tunnel up by name, reuses it when it is
# there, and creates it (seeding the trailing catch-all) when it is not. The
# read is fail-closed — an unreadable tunnel list is a refusal, never "no tunnel
# exists" — so a create can only ever be authorised by an honest empty list.
if [ "$PROVISION" = true ]; then
  echo "[0/5] finding or creating the tunnel '${CF_TUNNEL_NAME}' ..."
  if ! tunnel_list_json="$(cf GET "/accounts/${CF_ACCOUNT_ID}/cfd_tunnel?name=${CF_TUNNEL_NAME}&is_deleted=false")"; then
    abort "[0/5] the tunnel list could not be read"
  fi
  if ! tunnel_id="$(python3 - "$tunnel_list_json" "$root" <<'PY'
import json
import sys

sys.dont_write_bytecode = True
raw, root = sys.argv[1], sys.argv[2]
sys.path.insert(0, root)
from infra.cloudflare.provision import tunnel_id_from_list

try:
    document = json.loads(raw)
except ValueError:
    print("ao-ssh-access: FAILED: [0/5] the tunnel list response is not JSON",
          file=sys.stderr)
    raise SystemExit(3)
try:
    print(tunnel_id_from_list(document))
except ValueError as exc:
    print("ao-ssh-access: FAILED: %s" % exc, file=sys.stderr)
    raise SystemExit(3)
PY
)"; then
    abort "[0/5] the tunnel list could not be parsed (fail-closed: an unreadable list is never treated as 'no tunnel')"
  fi

  if [ -n "$tunnel_id" ]; then
    echo "      reusing the existing tunnel ${tunnel_id}"
  else
    echo "      no tunnel named '${CF_TUNNEL_NAME}' exists — one must be created"
    if [ "$DRY_RUN" = true ]; then
      echo "      (dry run: not created)"
    else
      create_body="$(python3 - "$CF_TUNNEL_NAME" <<'PY'
import json
import sys

print(json.dumps({"name": sys.argv[1], "config_src": "cloudflare"}, sort_keys=True))
PY
)"
      response="$(cf_mutate POST "/accounts/${CF_ACCOUNT_ID}/cfd_tunnel" "$create_body")" \
        || abort "[0/5] the tunnel could not be created"
      ack "[0/5] the new tunnel" "$response"
      tunnel_id="$(python3 - "$response" <<'PY'
import json
import sys

try:
    document = json.loads(sys.argv[1])
except ValueError:
    print("ao-ssh-access: FAILED: [0/5] the create response is not JSON",
          file=sys.stderr)
    raise SystemExit(3)
print((document.get("result") or {}).get("id", ""))
PY
)" || abort "[0/5] the new tunnel's id could not be read"
      if [ -z "$tunnel_id" ]; then
        abort "[0/5] the new tunnel has no id, so refusing to continue"
      fi
      # Seed the trailing catch-all so the publish merge has a rule to land
      # before, and so the tunnel never sits with an unreadably-empty config.
      seed_body="$(python3 - "$root" <<'PY'
import json
import sys

sys.dont_write_bytecode = True
root = sys.argv[1]
sys.path.insert(0, root)
from infra.cloudflare.provision import initial_tunnel_config

print(json.dumps(initial_tunnel_config(), indent=2, sort_keys=True))
PY
)"
      response="$(cf_mutate PUT "/accounts/${CF_ACCOUNT_ID}/cfd_tunnel/${tunnel_id}/configurations" "$seed_body")" \
        || abort "[0/5] the new tunnel's catch-all could not be seeded"
      ack "[0/5] the new tunnel's catch-all seed" "$response"
    fi
  fi
  CF_TUNNEL_ID="$tunnel_id"
  echo "      tunnel id: ${CF_TUNNEL_ID}"
fi

# The service string and the CNAME content come from the pure module, so the
# shell holds no formatting rule of its own.
if ! service_cname="$(python3 - "$root" "$AO_SSH_ORIGIN_HOST" "$ORIGIN_PORT" "$CF_TUNNEL_ID" <<'PY'
import sys

sys.dont_write_bytecode = True
root, origin, port, tunnel_id = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4]
sys.path.insert(0, root)
from infra.cloudflare.ingress import ssh_service, tunnel_cname

print("%s %s" % (ssh_service(origin, port), tunnel_cname(tunnel_id)))
PY
)"; then
  refuse "the ssh service string could not be built from AO_SSH_ORIGIN_HOST='${AO_SSH_ORIGIN_HOST}' and AO_SSH_ORIGIN_PORT='${ORIGIN_PORT}'"
fi
read -r SERVICE CNAME <<<"$service_cname"
if [ -z "$SERVICE" ] || [ -z "$CNAME" ]; then
  refuse "AO_SSH_ORIGIN_HOST must be a bare host with no spaces: '${AO_SSH_ORIGIN_HOST}'"
fi

if [ "$DRY_RUN" = true ]; then
  mode_label="DRY RUN (the default; --apply mutates)"
  flag_label="not consulted (a dry run mutates nothing)"
else
  mode_label="APPLY"
  flag_label="${flag_state} (surfaces.${SURFACE})"
fi

echo "=== ao-ssh-access: ${mode_label} ==="
echo "hostname  : ${AO_SSH_HOSTNAME}"
echo "service   : ${SERVICE}"
echo "tunnel    : ${CF_TUNNEL_ID}"
echo "api base  : ${API_BASE}"
echo "token     : ${token_source}"
echo "flag      : ${flag_label}"
echo "access    : ${AO_SSH_ACCESS_EMAILS} (session ${SESSION_DURATION})"
echo

# ── 1/4: merge the ssh rule into the live ingress ───────────────────────────
echo "[1/4] reading the live tunnel configuration from ${API_BASE} ..."
if ! config_json="$(cf GET "/accounts/${CF_ACCOUNT_ID}/cfd_tunnel/${CF_TUNNEL_ID}/configurations")"; then
  abort "[1/4] the tunnel configuration could not be read: is ${API_BASE} reachable and the token valid?"
fi

backup="${scratch}/tunnel-config-before.json"
merged_body="${scratch}/merged-ingress.json"

python3 - "$config_json" "$backup" "$merged_body" "$root" "$AO_SSH_HOSTNAME" "$SERVICE" <<'PY'
import json
import pathlib
import sys

sys.dont_write_bytecode = True
raw, backup, merged_path, root, host, service = sys.argv[1:7]
sys.path.insert(0, root)
from infra.cloudflare.ingress import merge_ssh_rule, tunnel_config_ingress

try:
    document = json.loads(raw)
except ValueError:
    print("ao-ssh-access: FAILED: the tunnel configuration response is not JSON",
          file=sys.stderr)
    raise SystemExit(3)

try:
    ingress = tunnel_config_ingress(document)
except ValueError as exc:
    print("ao-ssh-access: FAILED: %s" % exc, file=sys.stderr)
    raise SystemExit(3)

pathlib.Path(backup).write_text(json.dumps(document, indent=2, sort_keys=True) + "\n",
                                encoding="utf-8")
merged = merge_ssh_rule(ingress, host, service)
pathlib.Path(merged_path).write_text(
    json.dumps({"config": {"ingress": merged}}, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)

print("      live ingress: %d rule(s), backed up to %s" % (len(ingress), backup))
print("      ingress that would be sent: %d rule(s), in order:" % len(merged))
for rule in merged:
    print("        %s -> %s" % (rule.get("hostname") or "<catch-all>", rule.get("service")))
PY

if [ "$DRY_RUN" = true ]; then
  echo "      (dry run: not sent)"
else
  response="$(cf_mutate PUT "/accounts/${CF_ACCOUNT_ID}/cfd_tunnel/${CF_TUNNEL_ID}/configurations" "$merged_body")" \
    || abort "[1/4] the merged ingress could not be sent"
  ack "[1/4] the merged ingress" "$response"
fi

# ── 2/4: the proxied CNAME ──────────────────────────────────────────────────
if ! records_json="$(cf GET "/zones/${CF_ZONE_ID}/dns_records?name=${AO_SSH_HOSTNAME}")"; then
  abort "[2/4] the zone's DNS records could not be read"
fi

if ! record_id="$(python3 - "$records_json" "$AO_SSH_HOSTNAME" <<'PY'
import json
import sys

try:
    document = json.loads(sys.argv[1])
except ValueError:
    print("ao-ssh-access: FAILED: [2/4] the DNS records response is not JSON",
          file=sys.stderr)
    raise SystemExit(3)
for record in document.get("result") or []:
    if record.get("name") == sys.argv[2]:
        print(record.get("id", ""))
        break
PY
)"; then
  abort "[2/4] the DNS record for the hostname could not be identified"
fi

dns_body="$(python3 - "$root" "$AO_SSH_HOSTNAME" "$CF_TUNNEL_ID" <<'PY'
import json
import sys

sys.dont_write_bytecode = True
root, host, tunnel_id = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path.insert(0, root)
from infra.cloudflare.ingress import tunnel_cname

print(json.dumps({"type": "CNAME", "name": host, "content": tunnel_cname(tunnel_id),
                  "proxied": True, "ttl": 1}, sort_keys=True))
PY
)" || abort "[2/4] the CNAME body could not be built"

if [ -n "$record_id" ]; then dns_action="update the record ${record_id} for"; else dns_action="create"; fi

if [ "$DRY_RUN" = true ]; then
  echo "[2/4] would ${dns_action} the proxied CNAME ${AO_SSH_HOSTNAME} -> ${CNAME}"
  echo "      body: ${dns_body}"
else
  if [ -n "$record_id" ]; then
    response="$(cf_mutate PUT "/zones/${CF_ZONE_ID}/dns_records/${record_id}" "$dns_body")" \
      || abort "[2/4] the existing CNAME could not be updated"
  else
    response="$(cf_mutate POST "/zones/${CF_ZONE_ID}/dns_records" "$dns_body")" \
      || abort "[2/4] the CNAME could not be created"
  fi
  ack "[2/4] the proxied CNAME ${AO_SSH_HOSTNAME}" "$response"
fi

# ── 3/4: the Access application and its allow-policy ────────────────────────
# Without this pair the hostname is an unauthenticated public door to sshd, so
# the route never skips it and never leaves an app without a policy.
if ! apps_json="$(cf GET "/accounts/${CF_ACCOUNT_ID}/access/apps?per_page=100")"; then
  abort "[3/4] the Access applications could not be read"
fi

if ! app_id="$(python3 - "$apps_json" "$AO_SSH_HOSTNAME" <<'PY'
import json
import sys

try:
    document = json.loads(sys.argv[1])
except ValueError:
    print("ao-ssh-access: FAILED: [3/4] the Access applications response is not JSON",
          file=sys.stderr)
    raise SystemExit(3)
for app in document.get("result") or []:
    if app.get("domain") == sys.argv[2]:
        print(app.get("id", ""))
        break
PY
)"; then
  abort "[3/4] the Access application for the hostname could not be identified"
fi

app_body="$(python3 - "$AO_SSH_HOSTNAME" "$SESSION_DURATION" <<'PY'
import json
import sys

host, session = sys.argv[1], sys.argv[2]
print(json.dumps({"name": "agent-orchestrator SSH (%s)" % host, "domain": host,
                  "type": "self_hosted", "session_duration": session,
                  "app_launcher_visible": False}, sort_keys=True))
PY
)" || abort "[3/4] the Access application body could not be built"

policy_body="$(python3 - "$AO_SSH_ACCESS_EMAILS" "$SESSION_DURATION" <<'PY'
import json
import sys

emails = [address.strip() for address in sys.argv[1].split(",") if address.strip()]
print(json.dumps({"name": "agent-orchestrator operators", "decision": "allow",
                  "include": [{"email": {"email": address}} for address in emails],
                  "session_duration": sys.argv[2]}, sort_keys=True))
PY
)" || abort "[3/4] the Access allow-policy body could not be built"

if [ "$DRY_RUN" = true ]; then
  if [ -n "$app_id" ]; then
    echo "[3/4] would reuse the Access application ${app_id} for ${AO_SSH_HOSTNAME} and ensure its allow-policy"
  else
    echo "[3/4] would create the Access self-hosted application for ${AO_SSH_HOSTNAME} and its allow-policy"
  fi
  echo "      app body: ${app_body}"
  echo "      policy body: ${policy_body}"
else
  if [ -z "$app_id" ]; then
    response="$(cf_mutate POST "/accounts/${CF_ACCOUNT_ID}/access/apps" "$app_body")" \
      || abort "[3/4] the Access application could not be created"
    ack "[3/4] the Access application ${AO_SSH_HOSTNAME}" "$response"
    app_id="$(python3 - "$response" <<'PY'
import json
import sys

try:
    document = json.loads(sys.argv[1])
except ValueError:
    print("ao-ssh-access: FAILED: [3/4] the Access application response is not JSON",
          file=sys.stderr)
    raise SystemExit(3)
print((document.get("result") or {}).get("id", ""))
PY
)" || abort "[3/4] the new Access application's id could not be read"
  else
    echo "[3/4] the Access application ${app_id} already covers ${AO_SSH_HOSTNAME}"
  fi

  if [ -z "$app_id" ]; then
    abort "[3/4] the Access application has no id, so refusing to leave ${AO_SSH_HOSTNAME} without a policy"
  fi

  if ! policies_json="$(cf GET "/accounts/${CF_ACCOUNT_ID}/access/apps/${app_id}/policies")"; then
    abort "[3/4] the Access policies could not be read"
  fi
  policy_count="$(python3 - "$policies_json" <<'PY'
import json
import sys

try:
    document = json.loads(sys.argv[1])
except ValueError:
    print("ao-ssh-access: FAILED: [3/4] the Access policies response is not JSON",
          file=sys.stderr)
    raise SystemExit(3)
print(len(document.get("result") or []))
PY
)" || abort "[3/4] the Access policies could not be counted"

  if [ "$policy_count" = "0" ]; then
    response="$(cf_mutate POST "/accounts/${CF_ACCOUNT_ID}/access/apps/${app_id}/policies" "$policy_body")" \
      || abort "[3/4] the Access allow-policy could not be created"
    ack "[3/4] the Access allow-policy for ${AO_SSH_HOSTNAME}" "$response"
  else
    echo "[3/4] ${policy_count} Access policy(ies) already cover the application"
  fi
fi

# ── 4/4: verify what was applied ────────────────────────────────────────────
if [ "$DRY_RUN" = true ]; then
  echo "[4/4] dry run: nothing was sent, so there is nothing to verify live"
else
  if ! verified="$(cf GET "/accounts/${CF_ACCOUNT_ID}/cfd_tunnel/${CF_TUNNEL_ID}/configurations")"; then
    abort "[4/4] the merged configuration could not be re-read"
  fi
  python3 - "$verified" "$AO_SSH_HOSTNAME" "$SERVICE" <<'PY'
import json
import sys

document = json.loads(sys.argv[1])
host, service = sys.argv[2], sys.argv[3]
config = (document.get("result") or {}).get("config") or {}
rules = config.get("ingress") or []
match = [rule for rule in rules if rule.get("hostname") == host]
if not match:
    print("ao-ssh-access: FAILED: [4/4] the live configuration has NO rule for %s" % host,
          file=sys.stderr)
    raise SystemExit(3)
if match[0].get("service") != service:
    print("ao-ssh-access: FAILED: [4/4] the live rule for %s serves %r, not %r"
          % (host, match[0].get("service"), service), file=sys.stderr)
    raise SystemExit(3)
print("[4/4] the live configuration serves %s -> %s (%d rule(s) in total)"
      % (host, service, len(rules)))
PY

  if command -v dig >/dev/null 2>&1; then
    printf '[4/4] DNS: A=%s AAAA=%s\n' \
      "$(dig +short A "$AO_SSH_HOSTNAME" @1.1.1.1 2>/dev/null | tr '\n' ' ' || true)" \
      "$(dig +short AAAA "$AO_SSH_HOSTNAME" @1.1.1.1 2>/dev/null | tr '\n' ' ' || true)"
  else
    echo "[4/4] DNS: not checked (dig is not installed on this client)"
  fi
fi

# ── 5/5: connector deploy (issue #785) ─────────────────────────────────────
# The publish steps above reach the host through the tunnel; the connector is
# what actually joins the tunnel to the origin, so without it the route is a
# configuration that serves nothing. This step deploys `cloudflared` on each
# connector host, idempotently (pull, rm -f, run --restart unless-stopped). It
# is an OPERATOR act like --apply: a dry run prints the exact commands per host
# and sends nothing, and the tunnel token arrives from the environment only —
# never embedded in a command, never a file, never git (GR-6).
if [ "$CONNECTOR" = true ]; then
  require_var AO_SSH_CONNECTOR_HOSTS "the host(s) to deploy the cloudflared connector on (comma-separated)"
  require_var AO_SSH_CONNECTOR_USER "the SSH user for the connector host(s)"

  CONNECTOR_IMAGE="${AO_SSH_CONNECTOR_IMAGE:-cloudflare/cloudflared:2026.7.2}"
  CONNECTOR_NETWORK="${AO_SSH_CONNECTOR_NETWORK:-bridge}"
  CONNECTOR_PREFIX="${AO_SSH_CONNECTOR_CONTAINER_PREFIX:-ao-tunnel}"
  CONNECTOR_TOKEN="${AO_SSH_CONNECTOR_TOKEN:-}"

  if [ "$DRY_RUN" = false ] && [ -z "$CONNECTOR_TOKEN" ]; then
    refuse "AO_SSH_CONNECTOR_TOKEN is not set (the connector needs a tunnel token; obtain it with 'cloudflared tunnel token' and hold it in the environment, Vault or GCP Secret Manager — GR-6: never a file, never git)."
  fi

  echo "[5/5] deploying the cloudflared connector on: ${AO_SSH_CONNECTOR_HOSTS}"
  echo "      image   : ${CONNECTOR_IMAGE}"
  echo "      network : ${CONNECTOR_NETWORK}"
  if [ -n "$CONNECTOR_TOKEN" ]; then
    echo "      token   : from the environment (never printed)"
  else
    echo "      token   : (dry run: not required to plan)"
  fi

  hosts="$(printf '%s' "$AO_SSH_CONNECTOR_HOSTS" | tr ',' '\n')"
  while IFS= read -r host; do
    host="$(printf '%s' "$host" | tr -d '[:space:]')"
    [ -n "$host" ] || continue

    container="$(python3 - "$root" "$CONNECTOR_PREFIX" "$AO_SSH_HOSTNAME" <<'PY'
import sys

sys.dont_write_bytecode = True
root, prefix, hostname = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path.insert(0, root)
from infra.cloudflare.provision import connector_container_name

print(connector_container_name(prefix, [hostname]))
PY
)" || abort "[5/5] the connector container name could not be built"

    deploy_lines_txt="$(python3 - "$root" "$container" "$CONNECTOR_IMAGE" "$CONNECTOR_NETWORK" <<'PY'
import sys

sys.dont_write_bytecode = True
root, container, image, network = sys.argv[1:5]
sys.path.insert(0, root)
from infra.cloudflare.provision import connector_deploy_lines

for line in connector_deploy_lines(container, image, network):
    print(line)
PY
)" || abort "[5/5] the connector deploy plan could not be built"
    mapfile -t deploy_lines <<< "$deploy_lines_txt"

    if [ "$DRY_RUN" = true ]; then
      echo "      host ${host}: would run (idempotent; re-running converges):"
      for line in "${deploy_lines[@]}"; do
        echo "        ${line}"
      done
    else
      ssh_args=()
      [ -n "${AO_SSH_CONNECTOR_KEY:-}" ] && ssh_args+=(-i "$AO_SSH_CONNECTOR_KEY")
      remote="$(python3 - "$CONNECTOR_TOKEN" "${deploy_lines[@]}" <<'PY'
import shlex
import sys

token, lines = sys.argv[1], sys.argv[2:]
print("export TUNNEL_TOKEN=%s; %s" % (shlex.quote(token), " && ".join(lines)))
PY
)"
      if ! ssh "${ssh_args[@]}" "${AO_SSH_CONNECTOR_USER}@${host}" "$remote"; then
        abort "[5/5] the connector deploy on ${host} failed"
      fi
      echo "      host ${host}: connector deployed (${container})"
    fi
  done <<< "$hosts"
fi

cat <<EOF

NEXT STEP — this script publishes the hostname; it does not make it headless.
The Access application gives you the browser one-time-PIN flow. A Cloudflare
Access SERVICE TOKEN (plus its client-side wrapper) is what makes

    ssh ${AO_SSH_HOSTNAME}

work with no browser and no cached login. And reaching this host is not
reaching the fleet console: that still needs the auth-gate JWKS mirror
(issues #763 / #730). See docs/OPERATOR-ACCESS.md section 6.
EOF
