#!/usr/bin/env bash
# check-ao-ssh-access.sh — the remote operator SSH route (issues #771, #785).
#
# THE DEFECT THIS EXISTS FOR
#   Publishing a hostname through a remotely-managed Cloudflare Tunnel means
#   rewriting the tunnel's whole `config.ingress` array: the API has no "add one
#   rule" call. A blind replacement therefore deletes every other hostname the
#   tunnel serves -- a deploy that is an outage, and one that reports success.
#   That merge is the property worth a gate, and it is the property a "helpful"
#   refactor breaks silently. Issue #785 widened the route to own the provision
#   and connector halves too, so the gate now also provokes the two properties
#   those halves add: find-or-create must be idempotent and fail-closed, and the
#   connector deploy must converge and never embed a token.
#
# WHAT IS MEASURED (each provoked, and each named)
#   (a) a DRY RUN performs NO mutating request. The route is driven against
#       `infra/cloudflare/stub_cf_api.py`, which records every request and
#       refuses a mutating method, so "no PUT and no POST" is observed rather
#       than promised -- and the CONTROL is that the stub really did serve the
#       reads the dry run needs, so the probe cannot pass by talking to nobody.
#       The same run's own output is then read: the unrelated rule the tunnel
#       already serves is printed, and the new rule is printed BEFORE the
#       catch-all.
#   (b) the merge itself, MUTATION-PROVED. A stdlib probe drives the pure
#       `merge_ssh_rule` and names every property (unrelated rule survives, the
#       same hostname is updated in place, the catch-all stays last, the input
#       is never mutated). The module is then copied to a scratch tree with the
#       merge NEUTERED to a blind replacement, and the SAME probe must fail BY
#       NAME -- so a probe that stopped catching the defect fails the gate of
#       record instead of passing vacuously. The real module is proven
#       byte-identical afterwards, and the probe proves it imported the tree
#       under test.
#   (f) the provision + connector path, MUTATION-PROVED the same way: a probe
#       names every property (find-or-create reuses by id, an empty list
#       authorises a create, an unreadable list is refused, a new tunnel is
#       seeded with one catch-all, the connector deploy is idempotent,
#       token-free and `--restart unless-stopped`). TWO neutered copies must
#       then fail BY NAME: a fail-open read (unreadable treated as empty) and a
#       non-idempotent deploy (no `docker rm -f`). The real module is proven
#       byte-identical afterwards.
#   (g) the `--provision` and `--connector` DRY RUNS, driven against the stub:
#       each sends no mutating request, the provision run really reads the
#       tunnel list and plans a REUSE (never a create), and the connector run
#       prints the idempotent deploy per host with no token literal.
#   (c) no Cloudflare account id, zone id, tunnel id, operator email or token is
#       hardcoded: the route's files are scanned (no 32-hex account id, no UUID
#       tunnel id, no unmarked email, no literal bearer token, and no
#       `${CF_*:-default}` fallback for an identifier), the environment-driven
#       names are REQUIRED to be present so the scan cannot pass by finding
#       nothing, and a MISSING identifier is driven: it is refused BY NAME,
#       before any request at all (the stub's log must stay empty).
#   (d) `--apply` refuses while the route's feature flag is OFF (GR-5), and the
#       refusal is the FLAG and not something else: driven with every identifier
#       present, with the stub running, with the flag explicitly OFF and then
#       explicitly ON -- OFF must refuse by name and send nothing, ON must get
#       past the flag and actually attempt the mutation. Both states come from a
#       fixture registry built FROM the committed one, so the gate binds the
#       surface the repo declares without pinning the delivery-time value: a
#       reviewed promotion of the surface cannot turn this gate red.
#   (e) the declaration is real: `infra/feature-flags/registry.yaml` declares the
#       surface (with a description and a service), the registry's own parity
#       gate (`scripts/check-feature-flags.py`, consumed rather than
#       re-implemented) passes with it, `infra/cloudflare` is a declared pytest
#       suite, and the Makefile both runs this check and lists it under `lint`.
#
# The route is never driven against a real estate: the stub binds 127.0.0.1 and
# everything here is offline.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# Usage: bash scripts/check-ao-ssh-access.sh
set -uo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

surface="remote_ssh_access"
script_rel="infra/cloudflare/ao-ssh-access.sh"
ingress_rel="infra/cloudflare/ingress.py"
provision_rel="infra/cloudflare/provision.py"
stub_rel="infra/cloudflare/stub_cf_api.py"
registry_rel="infra/feature-flags/registry.yaml"
suite="infra/cloudflare"

for required in \
  "$script_rel" "$ingress_rel" "$provision_rel" "$stub_rel" "$registry_rel" \
  "$suite/tests/conftest.py" "$suite/tests/test_ingress.py" \
  "$suite/tests/test_provision.py"
do
  if [ ! -f "$required" ]; then
    echo "check-ao-ssh-access: FAIL — $required is missing" >&2
    exit 1
  fi
done

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-ao-ssh-access: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

work="/tmp/check-ao-ssh-access.$$.$(date +%s%N)"
if ! mkdir -p "$work" 2>/dev/null; then
  echo "check-ao-ssh-access: CANNOT-ASSESS — cannot create a scratch directory at ${work}" >&2
  exit 2
fi

stub_pid=""
cleanup() {
  if [ -n "$stub_pid" ]; then kill "$stub_pid" 2>/dev/null; fi
  rm -rf "$work"
}
trap cleanup EXIT

fail=0
note() { printf '  OK    %s\n' "$1"; }
problem() { printf '  FAIL  %s\n' "$1" >&2; fail=$((fail + 1)); }

# ── the read-only stub the route is driven against ──────────────────────────
stub_log="$work/requests.log"
stub_port_file="$work/stub.port"
: > "$stub_log"

python3 "$stub_rel" --port 0 --port-file "$stub_port_file" --log "$stub_log" \
  > "$work/stub.out" 2>&1 &
stub_pid=$!

waited=0
while [ ! -s "$stub_port_file" ]; do
  if [ "$waited" -ge 100 ]; then
    echo "check-ao-ssh-access: CANNOT-ASSESS — the stub did not start (see $work/stub.out)" >&2
    cat "$work/stub.out" >&2 2>/dev/null
    exit 2
  fi
  sleep 0.1
  waited=$((waited + 1))
done
base="http://127.0.0.1:$(cat "$stub_port_file" | tr -d '[:space:]')"
note "the read-only stub is up at ${base} (records every request, refuses a mutating method)"

# Every probe below runs with the full environment a real run needs, except for
# the one variable the probe is about. The values are placeholders: no estate
# identifier exists in this tree.
set_probe_env() {
  export AO_CF_API_BASE="$base"
  export CF_API_TOKEN="stub-token-for-the-offline-stub"
  export CF_ACCOUNT_ID="stub-account"
  export CF_ZONE_ID="stub-zone"
  export CF_TUNNEL_ID="stub-tunnel"
  export AO_SSH_HOSTNAME="ssh.example.test"
  export AO_SSH_ORIGIN_HOST="192.0.2.10"
  export AO_SSH_ORIGIN_PORT="22"
  export AO_SSH_ACCESS_EMAILS="operator@example.invalid"
}
set_probe_env

# ── (a) a dry run mutates nothing, and prints the merged plan ───────────────
echo "== a dry run performs no mutating request =="
: > "$stub_log"
bash "$script_rel" --dry-run > "$work/a.out" 2>&1
a_rc=$?

if [ "$a_rc" -ne 0 ]; then
  problem "the dry run exited ${a_rc}, expected 0: $(tail -n 3 "$work/a.out" | tr '\n' ' ')"
else
  note "the dry run exits 0 against the stub"
fi

if grep -q '^MUTATING ' "$stub_log"; then
  problem "the dry run sent a mutating request: $(grep -m1 '^MUTATING ' "$stub_log")"
else
  note "the dry run sent NO mutating request ($(grep -c '^GET ' "$stub_log") read(s) recorded)"
fi

if grep -q '^GET ' "$stub_log"; then
  note "control: the stub really served the dry run's reads, so 'no mutating request' is not vacuous"
else
  problem "the stub recorded no read at all: the dry run never reached it, so the no-mutation probe is vacuous"
fi

cat > "$work/plan-probe.py" <<'PLAN'
"""Read the route's OWN printed plan and name what it has to show (issue #771).

The claims are about the merged ingress BLOCK the dry run prints -- not about
the transcript around it, where the service string also appears in the header.
Reading the whole file would let a header line satisfy a claim about the plan.
"""

import sys

lines = open(sys.argv[1], encoding="utf-8", errors="replace").read().splitlines()
start = next((i for i, line in enumerate(lines) if "ingress that would be sent" in line), None)
if start is None:
    print("FAIL  plan-block-printed the dry run printed no merged-ingress block")
    raise SystemExit(1)

block = []
for line in lines[start + 1:]:
    if not line.startswith("        ") or not line.strip():
        break
    block.append(line.strip())

HOST = "ssh.example.test"
SERVICE = "ssh://192.0.2.10:22"
UNRELATED = "stub-estate.example.test -> http://stub-estate:80"
CATCH_ALL = "<catch-all> -> http_status:404"

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    if ok:
        print("OK    %s" % name)
    else:
        failures.append(name)
        print("FAIL  %s %s" % (name, detail))


check(
    "unrelated-live-rule-printed",
    UNRELATED in block,
    "=> the block does not show the rule the tunnel already serves: %r" % (block,),
)
check(
    "ssh-rule-printed",
    "%s -> %s" % (HOST, SERVICE) in block,
    "=> the block does not show the route's own rule: %r" % (block,),
)
ssh_index = next((i for i, line in enumerate(block) if line.startswith(HOST)), None)
catch_index = next((i for i, line in enumerate(block) if line.startswith("<catch-all>")), None)
check(
    "ssh-rule-before-the-catch-all",
    ssh_index is not None and catch_index is not None and ssh_index < catch_index,
    "=> ssh at %r, catch-all at %r, block=%r" % (ssh_index, catch_index, block),
)
check(
    "catch-all-printed-last",
    catch_index is not None and catch_index == len(block) - 1,
    "=> the catch-all is not the last printed rule: %r" % (block,),
)

if failures:
    raise SystemExit(1)
raise SystemExit(0)
PLAN

if python3 "$work/plan-probe.py" "$work/a.out" > "$work/a-plan.out" 2>&1; then
  while IFS= read -r line; do note "${line#OK    }"; done < <(grep '^OK ' "$work/a-plan.out")
else
  problem "the dry run's printed plan is wrong: $(grep -m3 '^FAIL ' "$work/a-plan.out" | tr '\n' ' ')"
fi

# ── (b) the merge, mutation-proved ──────────────────────────────────────────
echo "== the merge never drops a live rule (mutation-proved) =="

cat > "$work/merge-probe.py" <<'PROBE'
"""Name every property the ingress merge owes the route (issue #771).

The tree under test arrives as argv[1]. Bytecode writing is disabled and every
`__pycache__` under it is purged first: a module cached by an earlier run can
make a mutation invisible, and a gate that reads a stale copy proves nothing.
The import is then PROVEN to have resolved inside that tree, so the probe can
never report green while driving a different module.
"""

import shutil
import sys
from pathlib import Path

sys.dont_write_bytecode = True
root = Path(sys.argv[1]).resolve()
for cache in root.rglob("__pycache__"):
    shutil.rmtree(cache, ignore_errors=True)
sys.path.insert(0, str(root))

from infra.cloudflare import ingress  # noqa: E402

UNRELATED = {"hostname": "stub-estate.example.test", "service": "http://stub-estate:80"}
OTHER = {"hostname": "stub-auth.example.test", "service": "https://stub-auth:443"}
CATCH_ALL = {"service": "http_status:404"}
HOST = "ssh.example.test"
SERVICE = "ssh://192.0.2.10:22"

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    if ok:
        print("OK    %s" % name)
    else:
        failures.append(name)
        print("FAIL  %s %s" % (name, detail))


if not Path(ingress.__file__).resolve().is_relative_to(root):
    print("FAIL  probe-import-root imported %s, not the tree under test %s"
          % (ingress.__file__, root))
    raise SystemExit(1)

original = [dict(UNRELATED), dict(OTHER), dict(CATCH_ALL)]
before = [dict(rule) for rule in original]
merged = ingress.merge_ssh_rule(original, HOST, SERVICE)

check(
    "unrelated-rule-survived",
    len(merged) == 4 and merged[0] == UNRELATED and merged[1] == OTHER,
    "=> the merge returned %r" % (merged,),
)
check(
    "new-rule-before-catch-all",
    merged[-2] == {"hostname": HOST, "service": SERVICE} and merged[-1] == CATCH_ALL,
    "=> the rule did not land before the catch-all: %r" % (merged,),
)
check("input-not-mutated", original == before, "=> the merge mutated its argument")

stale = {"hostname": HOST, "service": "http://stale:80"}
in_place = ingress.merge_ssh_rule([dict(UNRELATED), stale, dict(CATCH_ALL)], HOST, SERVICE)
check(
    "same-hostname-updated-in-place",
    len(in_place) == 3 and in_place[1] == {"hostname": HOST, "service": SERVICE},
    "=> the same hostname was not updated in place: %r" % (in_place,),
)
check(
    "no-catch-all-appends",
    ingress.merge_ssh_rule([dict(UNRELATED)], HOST, SERVICE)[-1]
    == {"hostname": HOST, "service": SERVICE},
    "=> an ingress without a catch-all did not receive the rule",
)
check(
    "empty-ingress-yields-the-rule",
    ingress.merge_ssh_rule([], HOST, SERVICE) == [{"hostname": HOST, "service": SERVICE}],
    "=> an empty ingress was not handled",
)

if failures:
    print("the merge lost %d propert(y/ies): %s" % (len(failures), ", ".join(failures)))
    raise SystemExit(1)
raise SystemExit(0)
PROBE

real_sha_before="$(sha256sum "$ingress_rel" | awk '{print $1}')"

if python3 "$work/merge-probe.py" "$root" > "$work/b-real.out" 2>&1; then
  note "the merge keeps an unrelated rule, updates the same hostname in place and lands before the catch-all ($(grep -c '^OK ' "$work/b-real.out") propert(y/ies) proven)"
else
  problem "the merge probe FAILED on the real module: $(grep -m3 '^FAIL ' "$work/b-real.out" | tr '\n' ' ')"
fi

mutant="$work/mutant"
mkdir -p "$mutant/infra/cloudflare"
cp "$ingress_rel" "$mutant/infra/cloudflare/ingress.py"
cat >> "$mutant/infra/cloudflare/ingress.py" <<'MUTANT'


def merge_ssh_rule(ingress, hostname, service):  # MUTANT: a blind replacement
    return [{"hostname": hostname, "service": service}]
MUTANT

mutant_out="$(python3 "$work/merge-probe.py" "$mutant" 2>&1)"
mutant_rc=$?

if [ "$mutant_rc" -eq 0 ]; then
  problem "the mutation (a blind replacement) was NOT detected: the probe passes on the mutant, so this gate is vacuous"
elif grep -qF 'FAIL  unrelated-rule-survived' <<<"$mutant_out"; then
  note "mutation-proved: neutering the merge to a blind replacement fails the probe BY NAME (unrelated-rule-survived)"
else
  problem "the mutation was detected but not by name (expected 'FAIL  unrelated-rule-survived'): $(grep -m2 '^FAIL ' <<<"$mutant_out" | tr '\n' ' ')"
fi

real_sha_after="$(sha256sum "$ingress_rel" | awk '{print $1}')"
if [ "$real_sha_before" = "$real_sha_after" ]; then
  note "the real ${ingress_rel} is byte-identical after the mutation proof (sha256 ${real_sha_before:0:12})"
else
  problem "the mutation proof changed the real ${ingress_rel} (${real_sha_before:0:12} -> ${real_sha_after:0:12})"
fi

# ── (f) the provision + connector path, mutation-proved ────────────────────
echo "== provision + connector: idempotent, fail-closed, token-free (mutation-proved) =="

cat > "$work/provision-probe.py" <<'PROBE'
"""Name every property the provision + connector path owes the route (#785).

The tree under test arrives as argv[1]. Bytecode writing is disabled and every
`__pycache__` under it is purged first, then the import is PROVEN to have
resolved inside that tree — the same discipline as the merge probe, so a cached
or mis-resolved module cannot make a mutation invisible.
"""

import shutil
import sys
from pathlib import Path

sys.dont_write_bytecode = True
root = Path(sys.argv[1]).resolve()
for cache in root.rglob("__pycache__"):
    shutil.rmtree(cache, ignore_errors=True)
sys.path.insert(0, str(root))

from infra.cloudflare import provision  # noqa: E402

TID = "stub-tunnel-id-1234"
failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    if ok:
        print("OK    %s" % name)
    else:
        failures.append(name)
        print("FAIL  %s %s" % (name, detail))


if not Path(provision.__file__).resolve().is_relative_to(root):
    print("FAIL  probe-import-root imported %s, not the tree under test %s"
          % (provision.__file__, root))
    raise SystemExit(1)

# find-or-create: an existing tunnel is reused, an empty list authorises a create.
check("reuse-existing",
      provision.tunnel_id_from_list({"success": True, "result": [{"id": TID}]}) == TID,
      "=> an existing tunnel was not reused by id")
check("empty-authorizes-create",
      provision.tunnel_id_from_list({"success": True, "result": []}) == "",
      "=> an empty list did not authorise a create")

# fail-closed read: anything unreadable is refused, never "no tunnel exists".
fail_closed_ok = True
for bad in (
    {"success": False, "errors": [{"message": "denied"}]},
    {"success": True, "result": None},
    {"success": True, "result": [{"name": "no-id"}]},
    "not-an-object",
):
    try:
        provision.tunnel_id_from_list(bad)
        fail_closed_ok = False
    except ValueError:
        pass
check("fail-closed-read", fail_closed_ok,
      "=> an unreadable tunnel list was not refused")

# a new tunnel is seeded with exactly one catch-all, so the merge has a rule to
# land before and the tunnel never sits on an unreadably-empty config.
check("seed-catch-all",
      provision.initial_tunnel_config()
      == {"config": {"ingress": [{"service": "http_status:404"}]}},
      "=> the seed config is not exactly one catch-all")

# the connector deploy converges (rm -f before run) and never embeds a token.
lines = provision.connector_deploy_lines("ao-tunnel", "cloudflare/cloudflared:2026.7.2")
run = next((line for line in lines if line.startswith("docker run")), "")
check("connector-idempotent",
      any("docker rm -f" in line and "|| true" in line for line in lines),
      "=> the deploy carries no docker rm -f, so a re-run would collide")
check("connector-token-free",
      "-e TUNNEL_TOKEN" in run and "TUNNEL_TOKEN=" not in run,
      "=> the deploy embeds a token literal: %r" % (run,))
check("connector-restart", "--restart unless-stopped" in run, "=> %r" % (run,))
check("connector-name-normalised",
      provision.connector_container_name("ao-tunnel", ["ssh.example.test"])
      == "ao-tunnel-ssh-example-test",
      "=> the container name is not normalised")

if failures:
    print("the provision path lost %d propert(y/ies): %s"
          % (len(failures), ", ".join(failures)))
    raise SystemExit(1)
raise SystemExit(0)
PROBE

real_sha_before_p="$(sha256sum "$provision_rel" | awk '{print $1}')"

if python3 "$work/provision-probe.py" "$root" > "$work/f-real.out" 2>&1; then
  note "the provision + connector logic is idempotent, fail-closed and token-free ($(grep -c '^OK ' "$work/f-real.out") propert(y/ies) proven)"
else
  problem "the provision probe FAILED on the real module: $(grep -m3 '^FAIL ' "$work/f-real.out" | tr '\n' ' ')"
fi

# mutant 1: neuter the read to treat "unreadable" as "no tunnel" (fail-open).
mutant_p="$work/mutant-p"
mkdir -p "$mutant_p/infra/cloudflare"
cp "$provision_rel" "$mutant_p/infra/cloudflare/provision.py"
cat >> "$mutant_p/infra/cloudflare/provision.py" <<'MUTANT'


def tunnel_id_from_list(document):  # MUTANT: unreadable == no tunnel (fail-open)
    return ""
MUTANT

mutant_out_p="$(python3 "$work/provision-probe.py" "$mutant_p" 2>&1)"
mutant_rc_p=$?
if [ "$mutant_rc_p" -eq 0 ]; then
  problem "the provision mutation (fail-open read) was NOT detected: the probe passes on the mutant, so this gate is vacuous"
elif grep -qF 'FAIL  fail-closed-read' <<<"$mutant_out_p"; then
  note "mutation-proved: neutering the read to treat unreadable as empty fails the probe BY NAME (fail-closed-read)"
else
  problem "the provision mutation was detected but not by name (expected 'FAIL  fail-closed-read'): $(grep -m2 '^FAIL ' <<<"$mutant_out_p" | tr '\n' ' ')"
fi

# mutant 2: drop the `docker rm -f` from the deploy (breaks idempotency).
mutant_c="$work/mutant-c"
mkdir -p "$mutant_c/infra/cloudflare"
cp "$provision_rel" "$mutant_c/infra/cloudflare/provision.py"
cat >> "$mutant_c/infra/cloudflare/provision.py" <<'MUTANT'


def connector_deploy_lines(container, image, network="bridge"):  # MUTANT: no rm -f
    return [
        "docker pull %s" % image,
        "docker run -d --name %s --network %s --restart unless-stopped "
        "-e TUNNEL_TOKEN %s tunnel --no-autoupdate run" % (container, network, image),
    ]
MUTANT

mutant_out_c="$(python3 "$work/provision-probe.py" "$mutant_c" 2>&1)"
mutant_rc_c=$?
if [ "$mutant_rc_c" -eq 0 ]; then
  problem "the connector mutation (non-idempotent deploy) was NOT detected: the probe passes on the mutant, so this gate is vacuous"
elif grep -qF 'FAIL  connector-idempotent' <<<"$mutant_out_c"; then
  note "mutation-proved: dropping the docker rm -f fails the probe BY NAME (connector-idempotent)"
else
  problem "the connector mutation was detected but not by name (expected 'FAIL  connector-idempotent'): $(grep -m2 '^FAIL ' <<<"$mutant_out_c" | tr '\n' ' ')"
fi

real_sha_after_p="$(sha256sum "$provision_rel" | awk '{print $1}')"
if [ "$real_sha_before_p" = "$real_sha_after_p" ]; then
  note "the real ${provision_rel} is byte-identical after the mutation proofs (sha256 ${real_sha_before_p:0:12})"
else
  problem "the provision mutation proofs changed the real ${provision_rel} (${real_sha_before_p:0:12} -> ${real_sha_after_p:0:12})"
fi

# ── (g) the provision + connector dry runs, driven against the stub ────────
echo "== provision + connector dry runs mutates nothing and names the plan =="

: > "$stub_log"
CF_TUNNEL_NAME=stub-tunnel bash "$script_rel" --dry-run --provision > "$work/g.out" 2>&1
g_rc=$?
if [ "$g_rc" -ne 0 ]; then
  problem "the --provision dry run exited ${g_rc}, expected 0: $(tail -n 3 "$work/g.out" | tr '\n' ' ')"
else
  note "the --provision dry run exits 0 against the stub"
fi
if grep -q '^MUTATING ' "$stub_log"; then
  problem "the --provision dry run sent a mutating request: $(grep -m1 '^MUTATING ' "$stub_log")"
else
  note "the --provision dry run sent NO mutating request ($(grep -c '^GET ' "$stub_log") read(s) recorded)"
fi
if grep -q 'cfd_tunnel?name=' "$stub_log"; then
  note "control: the provision dry run really read the tunnel list, so 'find-or-create' is not vacuous"
else
  problem "the provision dry run never read the tunnel list: the find-or-create probe is vacuous"
fi
if grep -qF 'reusing the existing tunnel stub-tunnel' "$work/g.out"; then
  note "the provision dry run planned a REUSE of the existing tunnel (idempotent, no create)"
else
  problem "the provision dry run did not plan a reuse of the existing tunnel: $(grep -m2 'reus\|creat' "$work/g.out" | tr '\n' ' ')"
fi

: > "$stub_log"
AO_SSH_CONNECTOR_HOSTS="192.0.2.31" AO_SSH_CONNECTOR_USER="op" \
  bash "$script_rel" --dry-run --connector > "$work/h.out" 2>&1
h_rc=$?
if [ "$h_rc" -ne 0 ]; then
  problem "the --connector dry run exited ${h_rc}, expected 0: $(tail -n 3 "$work/h.out" | tr '\n' ' ')"
else
  note "the --connector dry run exits 0 against the stub"
fi
if grep -q '^MUTATING ' "$stub_log"; then
  problem "the --connector dry run sent a mutating request: $(grep -m1 '^MUTATING ' "$stub_log")"
else
  note "the --connector dry run sent NO mutating request"
fi
if grep -qF 'docker pull cloudflare/cloudflared:2026.7.2' "$work/h.out" \
  && grep -q 'docker rm -f' "$work/h.out" \
  && grep -qF 'docker run -d --name ao-tunnel-ssh-example-test' "$work/h.out" \
  && grep -qF -- '--restart unless-stopped' "$work/h.out"; then
  note "the connector dry run prints the idempotent deploy (pull, rm -f, run --restart unless-stopped)"
else
  problem "the connector dry run did not print the idempotent deploy plan"
fi
if grep -qF 'TUNNEL_TOKEN=' "$work/h.out"; then
  problem "the connector plan embeds a token literal"
else
  note "the connector plan embeds no token (the token arrives from the environment at apply time only)"
fi
if grep -qF 'host 192.0.2.31: would run' "$work/h.out"; then
  note "the connector dry run names the host and plans (never executes) the deploy"
else
  problem "the connector dry run did not plan per-host: $(grep -m2 'host ' "$work/h.out" | tr '\n' ' ')"
fi

# ── (c) no identifier, email or token is hardcoded ──────────────────────────
echo "== no estate identifier, email or token is hardcoded =="

scanned=("$script_rel" "$ingress_rel" "$provision_rel" "$stub_rel" \
  "$suite/tests/conftest.py" "$suite/tests/test_ingress.py" \
  "$suite/tests/test_provision.py" \
  "scripts/check-ao-ssh-access.sh")

hex_hits="$(grep -nE '[0-9a-f]{32}' "${scanned[@]}" 2>/dev/null || true)"
if [ -z "$hex_hits" ]; then
  note "no 32-hex identifier (a Cloudflare account or zone id) in the route's files"
else
  problem "a 32-hex identifier is hardcoded: $(printf '%s' "$hex_hits" | head -2 | tr '\n' ' ')"
fi

uuid_hits="$(grep -nE '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}' "${scanned[@]}" 2>/dev/null || true)"
if [ -z "$uuid_hits" ]; then
  note "no UUID (a Cloudflare tunnel id) in the route's files"
else
  problem "a UUID is hardcoded: $(printf '%s' "$uuid_hits" | head -2 | tr '\n' ' ')"
fi

# A documented placeholder (RFC 2606 / .invalid) is not somebody's address; the
# marker convention is the one the cto-overlay secret scan already uses.
email_hits="$(grep -nE '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}' "${scanned[@]}" 2>/dev/null \
  | grep -vE 'example|invalid|sample|placeholder' || true)"
if [ -z "$email_hits" ]; then
  note "no operator email is hardcoded (placeholders on a marked line are not addresses)"
else
  problem "an email address is hardcoded: $(printf '%s' "$email_hits" | head -2 | tr '\n' ' ')"
fi

script_email="$(grep -nE '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}' "$script_rel" 2>/dev/null || true)"
if [ -z "$script_email" ]; then
  note "the route script carries no email literal at all, not even a placeholder"
else
  problem "the route script carries an email literal: ${script_email}"
fi

default_hits="$(grep -nE '\$\{(CF_ACCOUNT_ID|CF_ZONE_ID|CF_TUNNEL_ID)(:-|=)' "$script_rel" 2>/dev/null || true)"
if [ -z "$default_hits" ]; then
  note "no identifier has a \${VAR:-default} fallback: an absent id cannot silently become an estate"
else
  problem "an identifier is defaulted instead of refused: $(printf '%s' "$default_hits" | tr '\n' ' ')"
fi

for name in CF_ACCOUNT_ID CF_ZONE_ID CF_TUNNEL_ID CF_API_TOKEN \
  CF_TUNNEL_NAME \
  AO_SSH_HOSTNAME AO_SSH_ORIGIN_HOST AO_SSH_ACCESS_EMAILS \
  AO_SSH_CONNECTOR_HOSTS AO_SSH_CONNECTOR_USER AO_SSH_CONNECTOR_TOKEN
do
  if grep -qF "$name" "$script_rel"; then
    :
  else
    problem "the route script never names ${name}: that identifier is not read from the environment"
  fi
done
note "the eleven environment names the route needs are all read from the environment"

bearer_hits="$(grep -nE 'Authorization: Bearer' "$script_rel" 2>/dev/null | grep -vF 'Bearer ${CF_TOKEN}' || true)"
if [ -z "$bearer_hits" ]; then
  note "every Authorization header is built from the resolved token (no literal bearer token)"
else
  problem "a literal bearer token is hardcoded: $(printf '%s' "$bearer_hits" | head -2 | tr '\n' ' ')"
fi

# ── (c) a missing identifier is refused by name, before any request ─────────
: > "$stub_log"
env -u CF_ACCOUNT_ID bash "$script_rel" --dry-run > "$work/c.out" 2>&1
c_rc=$?
if [ "$c_rc" -eq 1 ] && grep -qF "CF_ACCOUNT_ID is not set" "$work/c.out"; then
  note "an unset CF_ACCOUNT_ID is refused BY NAME and the dry run exits 1 (no default, no silent estate)"
else
  problem "an unset CF_ACCOUNT_ID was not refused by name (rc=${c_rc}): $(tail -n 2 "$work/c.out" | tr '\n' ' ')"
fi
if [ -s "$stub_log" ]; then
  problem "the missing-identifier refusal made $(grep -c . "$stub_log") request(s) before refusing"
else
  note "the missing-identifier refusal sent NOTHING (the stub's log is empty)"
fi

# ── (d) the feature flag gates --apply (both states, driven) ────────────────
echo "== --apply refuses while the surface is OFF (GR-5) =="

scratch="$work/scratch-root"
mkdir -p "$scratch/infra/cloudflare" "$scratch/infra/feature-flags" \
  "$scratch/portal/server"
# Exactly what the route script imports: itself, the pure module, and the
# fail-closed surface reader it consumes from the portal package. `portal/`
# itself is a PEP-420 namespace package (no __init__.py), so one is copied only
# when a checkout actually has one -- a hard requirement here would break the
# scratch tree on the layout the repo really uses.
for rel in "$script_rel" "$ingress_rel" portal/server/__init__.py portal/server/fleet.py; do
  mkdir -p "$scratch/$(dirname "$rel")"
  if ! cp "$rel" "$scratch/$rel"; then
    problem "the scratch tree could not copy ${rel}: the flag probe cannot drive the route"
  fi
done
if [ -f portal/__init__.py ]; then
  cp portal/__init__.py "$scratch/portal/__init__.py"
fi

if [ "$(sha256sum "$script_rel" | awk '{print $1}')" = \
  "$(sha256sum "$scratch/infra/cloudflare/ao-ssh-access.sh" | awk '{print $1}')" ]; then
  note "the scratch tree the flag is driven through carries a byte-identical copy of the route script"
else
  problem "the scratch copy of the route script differs from the delivered one: the flag probe would drive a different artifact"
fi

cat > "$work/set-flag.py" <<'SETFLAG'
"""Copy the committed registry and set one surface's promotion state."""

import sys
from pathlib import Path

import yaml

source, destination, surface, value = sys.argv[1:5]
document = yaml.safe_load(Path(source).read_text(encoding="utf-8"))
entry = (document.get("surfaces") or {}).get(surface)
if not isinstance(entry, dict):
    print("set-flag: %s declares no surfaces.%s" % (source, surface), file=sys.stderr)
    raise SystemExit(1)
entry["default"] = value
entry["promoted"] = value == "on"
Path(destination).write_text(
    yaml.safe_dump(document, sort_keys=False, allow_unicode=True), encoding="utf-8"
)
SETFLAG

fixture="$scratch/infra/feature-flags/registry.yaml"
if ! python3 "$work/set-flag.py" "$registry_rel" "$fixture" "$surface" off 2>"$work/d-flag.out"; then
  problem "the fixture registry could not be built from the committed one: $(tail -n 2 "$work/d-flag.out" | tr '\n' ' ')"
else
  : > "$stub_log"
  ( cd "$scratch" && bash infra/cloudflare/ao-ssh-access.sh --apply ) > "$work/d-off.out" 2>&1
  d_off_rc=$?
  if [ "$d_off_rc" -eq 1 ] && grep -qF "surfaces.${surface} is 'off'" "$work/d-off.out" \
    && grep -qF "infra/feature-flags/registry.yaml" "$work/d-off.out"; then
    note "--apply with the surface OFF is refused by name (rc=1, naming surfaces.${surface} and the registry)"
  else
    problem "--apply with the surface OFF was not refused by name (rc=${d_off_rc}): $(tail -n 2 "$work/d-off.out" | tr '\n' ' ')"
  fi
  if [ -s "$stub_log" ]; then
    problem "the flag refusal made $(grep -c . "$stub_log") request(s) before refusing"
  else
    note "the flag refusal sent NOTHING (the stub's log is empty)"
  fi

  # The control: the same run with the flag ON must get PAST the flag. Without
  # this, "refuses while off" would be satisfied by a route that always refuses.
  if ! python3 "$work/set-flag.py" "$registry_rel" "$fixture" "$surface" on 2>"$work/d-flag.out"; then
    problem "the ON fixture registry could not be built: $(tail -n 2 "$work/d-flag.out" | tr '\n' ' ')"
  else
    : > "$stub_log"
    ( cd "$scratch" && bash infra/cloudflare/ao-ssh-access.sh --apply ) > "$work/d-on.out" 2>&1
    d_on_rc=$?
    if grep -qF "surfaces.${surface} is 'off'" "$work/d-on.out"; then
      problem "with the surface ON the run still refused on the flag: the OFF refusal above proves nothing"
    elif ! grep -q '^GET ' "$stub_log"; then
      problem "with the surface ON the run reached the API for nothing: $(tail -n 2 "$work/d-on.out" | tr '\n' ' ')"
    elif ! grep -q '^MUTATING ' "$stub_log"; then
      problem "with the surface ON the run never attempted the mutation, so the flag is not what gates it: rc=${d_on_rc}"
    else
      note "control: with the surface ON the same command gets past the flag and DOES attempt the mutation (the stub refused it, as designed)"
    fi
  fi
fi

# ── (e) the declaration is real ─────────────────────────────────────────────
echo "== the declaration =="

if python3 - "$registry_rel" "$surface" <<'DECL'
import sys

import yaml
from pathlib import Path

registry, surface = sys.argv[1], sys.argv[2]
document = yaml.safe_load(Path(registry).read_text(encoding="utf-8"))
surfaces = document.get("surfaces") or {}
entry = surfaces.get(surface)
problems = []
if not isinstance(entry, dict):
    problems.append("%s declares no surfaces.%s" % (registry, surface))
else:
    # The DELIVERY-TIME value is deliberately not pinned here: a reviewed
    # promotion of the surface must not turn this gate red.
    if entry.get("default") not in (False, True, "off", "on"):
        problems.append("surfaces.%s.default is %r, not a promotion state"
                        % (surface, entry.get("default")))
    if entry.get("default") in (False, "off") and entry.get("promoted"):
        problems.append("surfaces.%s is off but declares promoted" % surface)
    if not str(entry.get("description") or "").strip():
        problems.append("surfaces.%s carries no description" % surface)
    if not str(entry.get("service") or "").strip():
        problems.append("surfaces.%s names no service" % surface)
if problems:
    for problem in problems:
        print("  FAIL  %s" % problem, file=sys.stderr)
    raise SystemExit(1)
print("  OK    %s declares surfaces.%s (promoted=%s, service=%s)"
      % (registry, surface, entry.get("promoted", False), entry.get("service")))
DECL
then
  :
else
  fail=$((fail + 1))
fi

if python3 scripts/check-feature-flags.py > "$work/e-ff.out" 2>&1; then
  note "scripts/check-feature-flags.py (the registry's own parity gate, consumed) passes with the surface"
else
  problem "scripts/check-feature-flags.py fails with the new surface: $(tail -n 2 "$work/e-ff.out" | tr '\n' ' ')"
fi

if grep -qxF "$suite" scripts/pytest-suites.txt; then
  note "${suite} is declared in scripts/pytest-suites.txt"
else
  problem "${suite} is not declared in scripts/pytest-suites.txt (its suite would be undeclared drift)"
fi

target_recipe() { # the recipe lines of one Makefile target
  awk -v target="$1" '
    $0 ~ "^" target ":" { inside = 1; next }
    inside && /^[^\t]/ { exit }
    inside { print }
  ' Makefile
}
if grep -qF 'scripts/check-ao-ssh-access.sh' <<<"$(target_recipe ao-ssh-access)"; then
  note "the Makefile's ao-ssh-access target runs this check"
else
  problem "the Makefile declares no ao-ssh-access target running this check"
fi
if grep -qE '^lint:.*( |^)ao-ssh-access( |$)' Makefile; then
  note "the Makefile lint list runs ao-ssh-access"
else
  problem "the Makefile lint list does not run ao-ssh-access"
fi

# ── verdict ─────────────────────────────────────────────────────────────────
if [ "$fail" -gt 0 ]; then
  echo "check-ao-ssh-access: FAIL (${fail} violation(s))" >&2
  exit 1
fi
echo "check-ao-ssh-access: OK — a dry run mutates nothing, the merge never drops a live rule (mutation-proved), the provision + connector path is idempotent, fail-closed and token-free (mutation-proved), no estate identifier or token is in the tree, and --apply refuses while the surface ships OFF"
exit 0
