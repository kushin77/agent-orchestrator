#!/usr/bin/env bash
# check-fleet-cron-dev-run.sh — the fleet-cron DEV-RUN gate (issue #710, EPIC #706 D2).
#
# THE CONTRACT THIS HOLDS
#   "Prove the image boots and dispatches roles without touching live state" is
#   only a porting step if the dry-ness is MEASURED. An image that starts and a
#   compose file that parses are not evidence that no job applied; this gate
#   holds the dev run to the three claims in the issue's acceptance, each with a
#   control that FAILS when the claim is false:
#
#     * "entrypoint validates env then dispatches roles" — a refused value must
#       stop the container BEFORE the schedule is installed, by name;
#     * "no job with --apply runs" — the argv check is static, the state guard is
#       dynamic, and BOTH are provoked by a mutant in which only the state guard
#       can still catch the write;
#     * "live state untouched, asserted by checksum before/after" — measured
#       against a SNAPSHOT of this box's live state (so the container is the only
#       writer and a change is unambiguously its own), writable, and required to
#       be byte-identical afterwards.
#
# WHAT IS MEASURED
#   A. the declared surface, as a PURE FUNCTION over a copy of the tree: the
#      compose file the issue names, the harness it runs, its port, its read-only
#      state mounts, its refusal of an env file, and the inventory's `dev_run`
#      block against the modules it describes;
#   B. THE DISPATCH DISCIPLINE, from the code that enforces it: the role table
#      covers `fleet/cron.py`'s own markers exactly, no argv carries `--apply`,
#      the entrypoint calls the environment contract BEFORE it installs the
#      schedule, and the decision document is not written into a state root;
#   C. THE ISSUE'S OWN COMMANDS, live: `docker compose … up -d`, `curl` on
#      `/healthz`, `docker compose … logs | grep -c 'dry-run'`, and a stop whose
#      exit code is recorded;
#   D. THE MUTANTS. Eighteen static mutations (one per rule — three added by
#      issue #711, D3: state-rw gating and the secrets injection contract) must
#      each produce
#      their OWN named finding — and the unmutated audit must produce none, so a
#      gate that reds a clean tree cannot hide behind its provocations. The live
#      half provokes what no static check can: an environment refusal, a mutant
#      image whose prune role carries `--apply` with the argv check disabled, a
#      missing state root, and the wrong-path 404.
#
# THE PORT, AND WHY THE LIVE RUN DOES NOT USE THE DEFAULT ONE
#   The default port is asserted from `docker compose config` — no container
#   needed — and the live runs use a port derived from this gate's own pid.
#   This box runs many lanes: two gates publishing one port would fail each
#   other, and a control that fails because a sibling is running is not a
#   control. The numbers differ; the mechanism is the one the file ships.
#
#   THE CONTAINER NAME AND THE COMPOSE PROJECT ARE DERIVED THE SAME WAY, and
#   the project name is the one that mattered (issue #939): compose groups by
#   PROJECT, not by container name, so two lanes sharing the file's constant
#   `name: agent-fleet-cron` had the second lane's `up -d` RECREATE — i.e.
#   destroy — the first lane's container. Measured 2026-09-16; see the block
#   above COMPOSE_PROJECT_NAME below.
#
# THE THREE THINGS THIS BOX DOES TO A CONTAINER, ALL MEASURED
#   1. a bridge container cannot resolve DNS, so the BUILD borrows the host's
#      resolution (`--network=host`) — D1 measured this, and this gate probes it
#      on every run rather than assuming it;
#   2. **a published container port cannot be reached from the host at all**
#      (measured 2026-09-16: a trivial `python:3.12-slim` HTTP server answers
#      200 from INSIDE the container and `Connection reset by peer` from the
#      host, on a published `127.0.0.1:<port>`). The issue's Verify therefore
#      cannot be run from the host ON THIS BOX, and the gate says so instead of
#      failing the dev run for the host's defect: it probes the capability with a
#      trivial container FIRST, then makes the issue's own request from
#      whichever side can make it, and reports which side that was.
#   3. **two lanes running this gate used to share one compose project**, so one
#      lane's `up -d`/`down` removed the other's container mid-run and made five
#      assertions fail together (issue #939). The project is now derived from
#      this gate's pid, and asserted to be (see the `compose project is its own`
#      control) — so a regression of that isolation fails BY NAME rather than
#      turning into a phantom dev-run failure.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# Usage: bash scripts/check-fleet-cron-dev-run.sh
set -uo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

fail=0
cannot=0
ok() { printf '  OK    %s\n' "$1"; }
bad() { printf '  FAIL  %s\n' "$1" >&2; fail=$((fail + 1)); }
note() { printf '  NOTE  %s\n' "$1"; }
unmeasured() { printf '  SKIP  %s\n' "$1"; cannot=$((cannot + 1)); }

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-fleet-cron-dev-run: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

IMAGE_DIR="infra/fleet"
COMPOSE="infra/fleet/docker-compose.agent-cron.yml"
INVENTORY="infra/fleet/inventory.yaml"
for required in "$COMPOSE" "$INVENTORY" "$IMAGE_DIR/dev_run.py" \
                "$IMAGE_DIR/env_contract.py" "$IMAGE_DIR/healthz.py" \
                "$IMAGE_DIR/entrypoint.sh" "$IMAGE_DIR/Dockerfile" \
                fleet/cron.py fleet/prune.py; do
  if [ ! -f "$required" ]; then
    echo "check-fleet-cron-dev-run: FAIL — $required is missing" >&2
    exit 1
  fi
done

# --- 0. the declared pytest suite (issue #711, D3) --------------------------
# `infra/fleet` is declared in scripts/pytest-suites.txt; naming it here (a
# wired check, auto-discovered per #698) is what makes it COVERED rather than
# merely declared (scripts/check-gate-coverage.sh, issue #526).
printf '\n== fleet-cron-dev-run: the declared pytest suite (infra/fleet/tests) ==\n'
if python3 -c 'import pytest' >/dev/null 2>&1; then
  pytest_log="$(mktemp /tmp/ao-fleet-cron-pytest.XXXXXX)"
  if env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q infra/fleet/tests >"$pytest_log" 2>&1; then
    ok "infra/fleet/tests: $(tail -1 "$pytest_log")"
  else
    bad "infra/fleet/tests failed: $(tail -5 "$pytest_log" | tr '\n' ' ')"
  fi
  rm -f "$pytest_log"
else
  unmeasured "pytest is not importable, so infra/fleet/tests was not run"
fi

work="$(mktemp -d /tmp/ao-fleet-cron-dev-run.XXXXXX)"
gate_image="agent-fleet-cron:dev-run-gate-$$"
gate_container="ao-710-dev-run-gate-$$"
live_port=$((20000 + $$ % 20000))
containers_to_remove=()
images_to_remove=()
snapshot=""

# --- the compose PROJECT is per-run too (issue #939) ------------------------
# The container NAME and the published PORT were already derived from this
# gate's own pid — but compose groups containers by PROJECT, not by container
# name, and the compose file declares a CONSTANT project (`name: agent-fleet-cron`).
# Two lanes running this gate at once therefore shared one project, and the
# second lane's `up -d` printed `Container <lane A> Recreate / Recreated`:
# it DESTROYED the first lane's container, and `down` then removed the shared
# network. Measured 2026-09-16 (two lanes, one box, same file).
#
# The first lane's remaining assertions then failed TOGETHER — `docker exec` on
# a container that no longer existed read `answered 0, not 404`, its `dev-run:`
# log lines were gone (so no `dry-run` line and no `UNTOUCHED` line), and the
# healthcheck and the exit code both read `unknown` — while every assertion that
# had already run (`up -d`, the verdict, `/healthz` 200) passed. That is issue
# #939 exactly, and it is why #939 was red only when a sibling lane was running
# `make verify` at the same time.
#
# The gate's own principle — stated above the port, and the reason the port is
# derived rather than fixed — is that "a control that fails because a sibling is
# running is not a control". The project name was the one identifier compose
# groups by that did not honour it. COMPOSE_PROJECT_NAME overrides the file's
# `name:` (measured: the file resolves to `agent-fleet-cron`, and to this value
# once the variable is set), so every compose call below — `config`, `up`,
# `stop`, `down`, `logs` — is now scoped to *this* run.
export COMPOSE_PROJECT_NAME="ao-710-dev-run-gate-$$"

cleanup() {
  local container
  for container in "${containers_to_remove[@]:-}"; do
    [ -n "$container" ] && docker rm -f "$container" >/dev/null 2>&1
  done
  if command -v docker >/dev/null 2>&1; then
    docker compose -f "$COMPOSE" down >/dev/null 2>&1
  fi
  for image in "${images_to_remove[@]:-}"; do
    [ -n "$image" ] && docker rmi -f "$image" >/dev/null 2>&1
  done
  # A container's root is not this user, so a directory a container created in
  # the scratch tree is not removable here. Clean it from a container as well,
  # so a gate that passed never leaves its own tree behind in /tmp.
  if [ -d "$work" ] && ! rm -rf "$work" 2>/dev/null; then
    for image in "${images_to_remove[@]:-}"; do
      if [ -n "$image" ] && docker image inspect "$image" >/dev/null 2>&1; then
        docker run --rm -v "$work:/gate-scratch" "$image" rm -rf /gate-scratch >/dev/null 2>&1
        break
      fi
    done
    rm -rf "$work" 2>/dev/null
  fi
}
trap cleanup EXIT

# ---------------------------------------------------------------------------
# A/B. THE STATIC AUDIT — a pure function over (image dir, repo root), so it can
# be run against a scratch copy carrying ONE mutation. It prints `OK`/`NOTE`
# lines for what it measured and `<code>: <detail>` for each finding.
# ---------------------------------------------------------------------------
cat > "$work/audit.py" <<'PY'
#!/usr/bin/env python3
"""Audit the fleet-cron dev run's declared surface against the code that enforces it (#710)."""
from __future__ import annotations

import os
import pathlib
import sys

try:
    import yaml
except ImportError:  # pragma: no cover
    print("unmeasured: PyYAML is not installed, so the compose file cannot be read")
    raise SystemExit(0)

IMAGE_DIR, REPO = sys.argv[1], sys.argv[2]
COMPOSE = os.path.join(IMAGE_DIR, "docker-compose.agent-cron.yml")
INVENTORY = os.path.join(IMAGE_DIR, "inventory.yaml")
ENTRYPOINT = os.path.join(IMAGE_DIR, "entrypoint.sh")

findings: list[tuple[str, str]] = []


def bad(code: str, detail: str) -> None:
    findings.append((code, detail))


def ok(message: str) -> None:
    print(f"  OK    {message}")


def note(message: str) -> None:
    print(f"  NOTE  {message}")


def read(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def code_of(text: str) -> str:
    """The file's CODE, comments stripped — both files discuss what the checks look for."""
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


# --- the modules, imported from the copy, so a mutation is what is measured ---
sys.path.insert(0, os.path.join(REPO, "fleet"))
sys.path.insert(0, IMAGE_DIR)
try:
    import cron as fleet_cron
    import dev_run
    import env_contract
    import healthz
    import secrets_contract
except Exception as exc:  # noqa: BLE001 — an unimportable harness is a finding
    print(f"harness-unimportable: {os.path.basename(IMAGE_DIR)} modules do not import ({exc})")
    raise SystemExit(0)

try:
    inventory = yaml.safe_load(read(INVENTORY)) or {}
except Exception as exc:  # noqa: BLE001
    bad("inventory-unreadable", f"{INVENTORY} does not parse ({exc})")
    inventory = {}

markers = [str(marker) for marker in fleet_cron.MARKERS]
declared = {role.marker: role for role in dev_run.ROLES}
dev_run_decl = (inventory.get("dev_run") or {}) if isinstance(inventory, dict) else {}

# --- 1. the compose file the issue names ------------------------------------
if not os.path.isfile(COMPOSE):
    bad("compose-missing", f"{COMPOSE} does not exist")
    compose = None
else:
    try:
        compose = yaml.safe_load(read(COMPOSE))
    except Exception as exc:  # noqa: BLE001
        bad("compose-unparsable", f"{COMPOSE} does not parse ({exc})")
        compose = None

if compose is not None:
    text = read(COMPOSE)
    services = (compose or {}).get("services") or {}
    if not services:
        bad("compose-no-service", "the compose file declares no service")
    for name, service in services.items():
        strings = []

        def collect(value: object) -> None:
            if isinstance(value, str):
                strings.append(value)
            elif isinstance(value, list):
                for item in value:
                    collect(item)
            elif isinstance(value, dict):
                for item in value.values():
                    collect(item)

        collect(service)
        joined = " ".join(strings)
        if "dev_run.py" not in joined:
            bad("service-command", f"service {name!r} does not run the harness (infra/fleet/dev_run.py)")
        tokens = joined.split()
        if dev_run.FORBIDDEN_TOKEN in tokens:
            bad("apply-in-compose", f"service {name!r} names {dev_run.FORBIDDEN_TOKEN}")
        if "env_file" in (service or {}):
            bad("env-file-present", f"service {name!r} mounts an env file")

        # every mount that lands on a state root must be read-only, and must not
        # create a missing host path (a root-owned empty board is a fiction) —
        # UNLESS the service is behind a compose `profiles:` gate (issue #711,
        # D3: `docker compose up` with no `--profile` never starts it, so a
        # profiled service's writable mount is the flag-gated-OFF posture, not
        # a violation of it). A service with NO profiles is the default surface
        # `docker compose up` starts, and that one may never write state.
        profiles = [str(item) for item in (service.get("profiles") or [])]
        for volume in service.get("volumes") or []:
            if not isinstance(volume, dict):
                continue
            target = str(volume.get("target") or "")
            if target not in ("/repo/.fleet", "/repo/.board"):
                continue
            if not profiles and volume.get("read_only") is not True:
                bad("state-mount-writable", f"{target} is mounted without read_only: true")
            if profiles and volume.get("read_only") is not True:
                ok(f"service {name!r} (profile {profiles}) mounts {target} read-write behind a gate that defaults OFF")
            if (volume.get("bind") or {}).get("create_host_path") is not False:
                bad(
                    "host-path-create",
                    f"{target} would let compose create a missing host path as root, instead of refusing",
                )

        # the port: one variable for the container's own value and the publish,
        # defaulting to the port the inventory declares.
        port = str(dev_run_decl.get("port", ""))
        ports = [str(item) for item in (service.get("ports") or [])]
        env = {str(key): str(value) for key, value in (service.get("environment") or {}).items()}
        if not ports:
            bad("port-missing", f"service {name!r} publishes no port")
        declared_port = None
        if isinstance(service.get("environment"), dict):
            raw = env.get("AO_FLEET_PORT", "")
            declared_port = raw.split(":-")[-1].rstrip("}") if raw else None
            if f"${{AO_FLEET_CRON_PORT:-{port}}}" not in raw:
                bad(
                    "port-drift",
                    f"AO_FLEET_PORT is {raw!r}, which does not default to the inventory's port {port!r}",
                )
        for item in ports:
            if f"${{AO_FLEET_CRON_PORT:-{port}}}" not in item:
                bad("port-drift", f"published port {item!r} does not default to the inventory's port {port!r}")
        if declared_port is None:
            bad("port-drift", "the service declares no AO_FLEET_PORT")

    ok(f"the compose file {os.path.basename(COMPOSE)} declares {len(services)} service(s), no env file, no {dev_run.FORBIDDEN_TOKEN}")

    # --- 1b. no default (non-profiled) service is state-rw (issue #711, D3) ---
    # `AO_FLEET_STATE_RW` documents the writable-mount posture in the image's own
    # environment contract; a non-profiled service claiming it is a service the
    # flag cannot actually gate off, since `docker compose up` starts it anyway.
    for name, service in services.items():
        profiles = [str(item) for item in (service.get("profiles") or [])]
        env = {str(key): str(value) for key, value in (service.get("environment") or {}).items()}
        if not profiles and env.get("AO_FLEET_STATE_RW") == "1":
            bad("state-rw-not-gated", f"service {name!r} sets AO_FLEET_STATE_RW=1 with no profiles: gate")

    # --- 1c. secrets injection contract (issue #711, D3): mounted from outside
    # the repo, read-only, never `env_file` (checked above) and never a secret
    # VALUE spelled out anywhere in this compose file's own text.
    secret_findings = secrets_contract.validate(repo_root=pathlib.Path(REPO))
    if secret_findings:
        for finding in secret_findings:
            bad(finding.code, finding.detail)
    else:
        ok(f"secrets_contract declares {len(secrets_contract.SECRET_MOUNTS)} mount(s), all sourced outside the checkout")
    leaked = secrets_contract.scan_for_secret_values(text)
    if leaked:
        bad("secret-value-in-compose", f"names that look like a credential value: {leaked}")

# --- 2. the dispatch discipline, from the code that enforces it -------------
drift = dev_run.check_role_table(markers)
if drift:
    bad("role-table-drift", "; ".join(drift))
else:
    ok(f"the role table covers fleet/cron.py's {len(markers)} marker(s) exactly")

applying = dev_run.check_no_apply()
if applying:
    bad("apply-in-dispatch", "; ".join(applying))
else:
    ok(f"no dispatched role carries {dev_run.FORBIDDEN_TOKEN}")

# --- 3. the environment contract -------------------------------------------
port_defaults = {var.name: var.default for var in env_contract.VARS}
if port_defaults.get("AO_FLEET_PORT") != str(dev_run_decl.get("port", "")):
    bad(
        "env-port-drift",
        f"env_contract declares AO_FLEET_PORT={port_defaults.get('AO_FLEET_PORT')!r}, "
        f"the inventory declares {dev_run_decl.get('port')!r}",
    )
else:
    ok(f"the port is declared once: env_contract AO_FLEET_PORT={port_defaults.get('AO_FLEET_PORT')}")

if "1" not in [choice for var in env_contract.VARS if var.name == "AO_FLEET_DRY_RUN" for choice in var.choices]:
    bad("env-dry-run-required", "the contract does not refuse a value that would make the run apply")
else:
    ok("the contract refuses any AO_FLEET_DRY_RUN other than 1 (dry-run-required)")

# --- 4. the entrypoint validates BEFORE it installs the schedule ------------
# Measured on the CALL, not on the import: reading the contract's defaults from
# it is not validating against it, so a mutation that turns the call into `true`
# while leaving the read in place must still be caught.
entrypoint_code = code_of(read(ENTRYPOINT))
contract_at = entrypoint_code.find('env_contract.py" check')
install_at = entrypoint_code.find("fleet/cron.py")
if contract_at < 0:
    bad("env-contract-not-called", "entrypoint.sh never VALIDATES the environment (no `env_contract.py check`)")
elif install_at < 0:
    bad("schedule-not-owned", "entrypoint.sh does not call fleet/cron.py install")
elif contract_at > install_at:
    bad(
        "env-contract-not-called",
        "entrypoint.sh calls the environment contract AFTER it installs the schedule",
    )
else:
    ok("entrypoint.sh validates the environment before it installs the schedule")

# --- 5. the evidence does not live in what it is proving -------------------
decision = str(dev_run.DECISION_RELATIVE)
roots = [str(item) for item in dev_run_decl.get("state_roots", [])] or [".fleet", ".board"]
if any(decision.startswith(name) for name in (".fleet", ".board")):
    bad("decision-in-a-state-root", f"the decision document lives at {decision!r}, inside a state root")
else:
    ok(f"the decision document lives outside the state roots ({decision})")

if healthz.HEALTH_PATH != "/healthz":
    bad("health-path-drift", f"the surface answers {healthz.HEALTH_PATH!r}, not '/healthz'")
else:
    ok("the health surface answers /healthz")

# --- 6. the inventory's own role block ------------------------------------
inventory_roles = {str(item.get("marker")): item for item in (dev_run_decl.get("roles") or [])}
if not inventory_roles:
    bad("inventory-role-drift", "the inventory declares no dev_run.roles")
else:
    if sorted(inventory_roles) != sorted(declared):
        bad(
            "inventory-role-drift",
            f"the inventory declares {sorted(inventory_roles)}; the harness declares {sorted(declared)}",
        )
    for marker, role in declared.items():
        entry = inventory_roles.get(marker) or {}
        command = " ".join(role.argv)
        if str(entry.get("command", "")) != command:
            bad(
                "inventory-role-drift",
                f"{marker}: the inventory declares command {entry.get('command')!r}, the harness runs {command!r}",
            )
        if str(entry.get("discipline", "")) != role.disposition:
            bad(
                "inventory-role-drift",
                f"{marker}: the inventory declares discipline {entry.get('discipline')!r}, "
                f"the harness declares {role.disposition!r}",
            )
    if not [code for code, _ in findings if code == "inventory-role-drift"]:
        ok(f"the inventory's {len(inventory_roles)} role(s) match the harness, command for command")

if not findings:
    ok("the declared surface, the dispatch discipline and the evidence path all hold")

for code, detail in findings:
    print(f"{code}: {detail}")
PY

run_audit() { python3 "$work/audit.py" "$1" "$root"; }
show_codes() { grep -E "^[a-z][a-z0-9-]*: " || true; }

printf '\n== fleet-cron-dev-run: the declared surface ==\n'
real_output="$(run_audit "$IMAGE_DIR")"
printf '%s\n' "$real_output" | grep -E '^\s+(OK|NOTE|SKIP)\s' || true
# Containment is tested by `case` over a CAPTURED variable, never by piping into
# `grep -q`: the quiet form exits on its first match, the producer dies of
# SIGPIPE, and `pipefail` promotes that 141 to the status of the whole pipeline —
# so the test reports ABSENT for text that is PRESENT (the #852 idiom).
unmeasured_detail=""
while IFS= read -r audit_line; do
  case "$audit_line" in
    "unmeasured: "*)
      unmeasured_detail="${audit_line#unmeasured: }"
      break
      ;;
  esac
done <<<"$real_output"
if [ -n "$unmeasured_detail" ]; then
  unmeasured "$unmeasured_detail"
fi
audit_codes="$(show_codes <<<"$real_output")"
if [ -n "$audit_codes" ]; then
  while IFS= read -r audit_finding; do
    bad "the clean tree already fails the audit — $audit_finding"
  done <<<"$audit_codes"
else
  ok "the audit reports no finding on the tree as committed"
fi

# ---------------------------------------------------------------------------
# D. THE MUTANTS — one mutation per rule, each in its own scratch copy, each
# asserted to have LANDED before its finding is believed. A mutation that never
# applied must never be reported as caught.
# ---------------------------------------------------------------------------
printf '\n== fleet-cron-dev-run: provocation — every rule is load-bearing ==\n'
provoke() {  # <label> <expected code> <case name>
  local label="$1" expected="$2" case_name="$3" dir finding_code out
  dir="$work/provoke-$case_name"
  rm -rf "$dir"
  mkdir -p "$dir"
  cp -r "$IMAGE_DIR" "$dir/image"
  python3 - "$root" "$dir/image" "$case_name" <<'PY'
import pathlib, sys

root, image, case = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]), sys.argv[3]
compose = image / "docker-compose.agent-cron.yml"
dev_run = image / "dev_run.py"
entrypoint = image / "entrypoint.sh"
inventory = image / "inventory.yaml"


def patch(path: pathlib.Path, old: str, new: str) -> None:
    text = path.read_text()
    assert old in text, f"the mutation anchor for {case!r} is not in {path.name}"
    path.write_text(text.replace(old, new, 1))


if case == "compose-missing":
    compose.unlink()
elif case == "compose-unparsable":
    compose.write_text(compose.read_text() + "\n  broken: [unclosed\n")
elif case == "service-command":
    patch(compose, 'command: ["python3", "/repo/infra/fleet/dev_run.py"]', 'command: ["cron", "-f"]')
elif case == "port-drift":
    patch(compose, '"127.0.0.1:${AO_FLEET_CRON_PORT:-8790}:${AO_FLEET_CRON_PORT:-8790}"', '"127.0.0.1:8791:8791"')
elif case == "state-mount-writable":
    patch(compose, "        target: /repo/.fleet\n        read_only: true", "        target: /repo/.fleet\n        read_only: false")
elif case == "host-path-create":
    patch(compose, "          create_host_path: false", "          create_host_path: true")
elif case == "env-file-present":
    patch(compose, "    command:", "    env_file:\n      - .env\n    command:")
elif case == "apply-in-compose":
    patch(compose, 'command: ["python3", "/repo/infra/fleet/dev_run.py"]', 'command: ["python3", "/repo/infra/fleet/dev_run.py", "--apply"]')
elif case == "apply-in-dispatch":
    patch(dev_run, 'argv=("fleet/prune.py", "run"),', 'argv=("fleet/prune.py", "run", "--apply"),')
elif case == "role-table-drift":
    patch(dev_run, '        marker="ao-fleet-prune",', '        marker="ao-fleet-invented",')
elif case == "env-contract-not-called":
    patch(entrypoint, 'python3 "${REPO}/infra/fleet/env_contract.py" check', "true")
elif case == "decision-in-a-state-root":
    patch(dev_run, 'DECISION_RELATIVE = Path(".verify/dev-run/decision.json")', 'DECISION_RELATIVE = Path(".fleet/dev-run/decision.json")')
elif case == "health-path-drift":
    patch(image / "healthz.py", 'HEALTH_PATH = "/healthz"', 'HEALTH_PATH = "/health"')
elif case == "env-port-drift":
    # ONLY the contract's default moves: the inventory keeps the port it declares,
    # so the finding checked here is the drift BETWEEN the two declarations and
    # not the compose file's own (which has a mutation of its own).
    patch(image / "env_contract.py", '        default="8790",\n        kind="port",', '        default="8799",\n        kind="port",')
elif case == "inventory-role-drift":
    patch(inventory, "      discipline: dry-run\n      command: \"fleet/prune.py run\"", "      discipline: apply\n      command: \"fleet/prune.py run\"")
elif case == "state-rw-not-gated":
    patch(compose, "    profiles:\n      - state-rw\n    command:", "    command:")
elif case == "secret-source-inside-repo":
    secrets_module = image / "secrets_contract.py"
    patch(
        secrets_module,
        'default_host_path="${HOME}/.config/gh"',
        f'default_host_path={str(root / "infra" / "fleet")!r}',
    )
elif case == "secret-value-in-compose":
    example_name = "AO_FLEET_SAMPLE_" + "TOKEN"  # built, not spelled, so this file stays clean of the literal
    patch(compose, '      AO_FLEET_STATE_RW: "1"', f'      AO_FLEET_STATE_RW: "1"\n      {example_name}: "x"')
else:
    raise SystemExit(f"unknown case {case!r}")
PY
  if [ "$?" -ne 0 ]; then
    unmeasured "the mutation for $case_name did not land"
    return
  fi
  out="$(run_audit "$dir/image")"
  finding_code="$(show_codes <<<"$out" | cut -d: -f1 | tr '\n' ' ')"
  # The expected finding's own line, matched with `case` on a captured variable.
  expected_line=""
  while IFS= read -r audit_line; do
    case "$audit_line" in
      "${expected}: "*)
        expected_line="$audit_line"
        break
        ;;
    esac
  done <<<"$out"
  if [ -n "$expected_line" ]; then
    ok "$label — reported as $expected"
    printf '        %s\n' "$expected_line"
  else
    bad "$label was NOT reported as $expected (findings seen: ${finding_code:-none})"
  fi
}

provoke "a compose file that is not there"            compose-missing        compose-missing
provoke "a compose file that does not parse"          compose-unparsable     compose-unparsable
provoke "a service that does not run the harness"     service-command        service-command
provoke "a published port that drifted from the contract" port-drift         port-drift
provoke "a state root mounted writable"               state-mount-writable   state-mount-writable
provoke "a bind mount that would create a root-owned path" host-path-create  host-path-create
provoke "an env file mounted into the container"      env-file-present       env-file-present
provoke "a compose command carrying --apply"          apply-in-compose       apply-in-compose
provoke "a dispatched role carrying --apply"          apply-in-dispatch      apply-in-dispatch
provoke "a role the schedule does not install"        role-table-drift       role-table-drift
provoke "an entrypoint that stops validating the environment" env-contract-not-called env-contract-not-called
provoke "the decision document moved into a state root" decision-in-a-state-root decision-in-a-state-root
provoke "a health surface on the wrong path"          health-path-drift      health-path-drift
provoke "the port declared in two places"             env-port-drift         env-port-drift
provoke "an inventory role that stopped matching the harness" inventory-role-drift inventory-role-drift
provoke "a state-rw service with no profile to gate it"       state-rw-not-gated    state-rw-not-gated
provoke "a secret source resolving inside the checkout"       secret-source-inside-repo secret-source-inside-repo
provoke "a credential-shaped value spelled out in compose"    secret-value-in-compose secret-value-in-compose

# ---------------------------------------------------------------------------
# E. THE LIVE HALF
# ---------------------------------------------------------------------------
printf '\n== fleet-cron-dev-run: the image, the compose file and a snapshot of live state ==\n'
if ! command -v docker >/dev/null 2>&1; then
  unmeasured "docker is not installed, so the live controls were not run"
  printf '\n'
  if [ "$fail" -gt 0 ]; then
    printf 'check-fleet-cron-dev-run: FAIL — %d finding(s)\n' "$fail" >&2
    exit 1
  fi
  printf 'check-fleet-cron-dev-run: CANNOT-ASSESS — %d control(s) could not be measured\n' "$cannot" >&2
  exit 2
fi
if ! timeout 30 docker info >/dev/null 2>&1; then
  unmeasured "the docker daemon is not answering, so the live controls were not run"
  printf '\n'
  if [ "$fail" -gt 0 ]; then
    printf 'check-fleet-cron-dev-run: FAIL — %d finding(s)\n' "$fail" >&2
    exit 1
  fi
  printf 'check-fleet-cron-dev-run: CANNOT-ASSESS — %d control(s) could not be measured\n' "$cannot" >&2
  exit 2
fi
if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
  unmeasured "docker compose is not available, so the live controls were not run"
fi

# The build needs to reach the network, and on this host a bridge container does
# not resolve (D1's own measurement). Probe, then say which was used.
build_network=()
if docker run --rm python:3.12-slim python3 -c "import socket; socket.gethostbyname('deb.debian.org')" >/dev/null 2>&1; then
  note "container DNS resolves; building exactly as the inventory writes it"
else
  build_network=(--network=host)
  note "a bridge container cannot resolve on this host (measured), so the build borrows the host's resolution"
fi

if docker build "${build_network[@]}" -f "$IMAGE_DIR/Dockerfile" -t "$gate_image" . >"$work/build.log" 2>&1; then
  images_to_remove+=("$gate_image")
  ok "the image builds from the repository root ($(docker image inspect --format '{{.Size}}' "$gate_image" 2>/dev/null) bytes)"
else
  bad "the image does not build: $(tail -3 "$work/build.log" | tr '\n' ' ')"
fi

# --- this run's compose project is its OWN (issue #939) --------------------
# A control, not a formality: if the export above is removed the project
# resolves back to the file's shared `agent-fleet-cron`, two lanes share it
# again, and this assertion names that. `--format json` is compose v2's own
# view of the project, so what is asserted is what compose will group by.
#
# `${COMPOSE_PROJECT_NAME:-<unset>}` rather than the bare variable: this script
# runs under `set -u`, so the first version of this control died with
# `COMPOSE_PROJECT_NAME: unbound variable` instead of reporting the finding in
# its own words (measured by provoking exactly that). A control that crashes
# still exits non-zero, but a crash does not NAME the regression, and naming it
# is the whole point of the assertion.
expected_project="${COMPOSE_PROJECT_NAME:-<unset>}"
resolved_project="$(docker compose -f "$COMPOSE" config --format json 2>/dev/null \
  | python3 -c 'import json, sys
try:
    print((json.load(sys.stdin) or {}).get("name", ""))
except Exception:
    print("")' 2>/dev/null)"
if [ "$resolved_project" = "$expected_project" ]; then
  ok "this gate's compose project is its own ($resolved_project), not the file's shared 'agent-fleet-cron'"
else
  bad "the gate's compose project resolved to '${resolved_project:-none}', not '$expected_project' — a concurrent lane would share that project, and its own \`up -d\` would recreate — i.e. destroy — this gate's container mid-run"
fi

# --- the default port, without starting anything ---------------------------
if docker compose -f "$COMPOSE" config -q >/dev/null 2>&1; then
  resolved="$(docker compose -f "$COMPOSE" config 2>/dev/null | grep -A 3 'published:' | grep 'published:' | head -1 | awk '{print $2}' | tr -d '"')"
  declared_port="$(python3 -c "
import yaml
print((yaml.safe_load(open('$INVENTORY')) or {}).get('dev_run', {}).get('port'))
")"
  if [ "$resolved" = "$declared_port" ]; then
    ok "docker compose config -q passes and publishes the declared port $declared_port by default"
  else
    bad "the default published port is $resolved, not the declared $declared_port"
  fi
else
  bad "docker compose config -q does not pass"
fi

# --- a snapshot of THIS BOX's live state ----------------------------------
# The main worktree is the checkout the live fleet runs from, and it is derived
# from git rather than named: a gate that hardcoded this machine's path would be
# a gate for one machine.
main_worktree="$(git worktree list --porcelain 2>/dev/null | awk '/^worktree /{print $2; exit}')"
live_source="${AO_FLEET_LIVE_REPO:-$main_worktree}"
if [ -d "$live_source/.fleet" ] && [ -d "$live_source/.board" ]; then
  snapshot="$work/live-snapshot"
  mkdir -p "$snapshot"
  cp -a "$live_source/.fleet" "$snapshot/.fleet"
  cp -a "$live_source/.board" "$snapshot/.board"
  live_files="$(find "$snapshot" -type f 2>/dev/null | wc -l)"
  ok "snapshotted live state from $live_source ($live_files file(s)) so the container is its only writer"
else
  snapshot="$work/live-snapshot"
  mkdir -p "$snapshot/.fleet/outbox" "$snapshot/.fleet/sent" "$snapshot/.board"
  echo '{"from":"gate","to":"nobody"}' > "$snapshot/.fleet/outbox/gate-entry.json"
  {
    printf '# claims ledger\n'
    echo '{"event":"claim","issue":999,"agent":"gate","at":"2026-01-01T00:00:00Z"}'
  } > "$snapshot/.board/claims.jsonl"
  note "no live state at $live_source, so a representative snapshot was synthesised for the live controls"
fi

# --- the issue's own three commands, against the snapshot -----------------
printf '\n== fleet-cron-dev-run: the issue'"'"'s own commands ==\n'
export AO_FLEET_CRON_IMAGE="$gate_image"
export AO_FLEET_CRON_CONTAINER="$gate_container"
export AO_FLEET_HOST_REPO="$snapshot"
export AO_FLEET_CRON_PORT="$live_port"
containers_to_remove+=("$gate_container")

# --- can this HOST reach a published container port at all? -----------------
# Measured BEFORE the run is judged, because the answer decides where the
# issue's own `curl` can be made from — and an environment defect must stay
# visible rather than being reported as the dev run's failure (D1's posture for
# the build network, the same reasoning). A trivial container answers 200 from
# inside and `Connection reset by peer` from this host (measured 2026-09-16),
# so on this box the request is made from inside the container and the defect is
# stated in the output either way.
port_probe="ao-710-port-probe-$$"
port_publishing="unknown"
if docker run --rm -d --name "$port_probe" -p "127.0.0.1:$live_port:$live_port" "$gate_image" \
     python3 -m http.server "$live_port" >/dev/null 2>&1; then
  containers_to_remove+=("$port_probe")
  for _ in $(seq 1 20); do
    probe_status="$(docker ps --filter "name=$port_probe" --format '{{.Status}}' 2>/dev/null)"
    case "$probe_status" in *Up*) break ;; esac
    sleep 1
  done
  if curl -fsS --max-time 6 "http://127.0.0.1:$live_port/" >/dev/null 2>&1; then
    port_publishing=yes
  else
    port_publishing=no
  fi
  docker rm -f "$port_probe" >/dev/null 2>&1
fi
if [ "$port_publishing" = yes ]; then
  note "this host reaches a published container port, so the issue's curl is made from the host"
elif [ "$port_publishing" = no ]; then
  note "MEASURED: this host cannot reach a PUBLISHED container port (a trivial probe answers 200 inside the container and 'Connection reset by peer' from the host), so the issue's request is made from inside the container — the defect is stated, not worked around"
else
  note "the port-publishing probe did not run, so the health request is made from inside the container"
fi

# The same request the issue makes, from wherever this box can make it. The
# script is written once and copied in, so both branches ask the same question.
cat > "$work/probe.py" <<'PY'
import json, sys, urllib.error, urllib.request

port, path = sys.argv[1], sys.argv[2]
try:
    response = urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=15)
    payload = {"status": response.status, "body": response.read().decode()}
except urllib.error.HTTPError as exc:
    payload = {"status": exc.code, "body": exc.read().decode()}
except Exception as exc:  # noqa: BLE001 — a failed request is a measurement, not a crash
    payload = {"status": 0, "body": f"{exc.__class__.__name__}: {exc}"}
print(json.dumps(payload))
PY
health_request() { # <path> → $work/health.json
  if [ "$port_publishing" = yes ]; then
    python3 "$work/probe.py" "$live_port" "$1" >"$work/health.json" 2>/dev/null
  else
    docker cp "$work/probe.py" "$gate_container:/tmp/probe.py" >/dev/null 2>&1
    docker exec "$gate_container" python3 /tmp/probe.py "$live_port" "$1" >"$work/health.json" 2>/dev/null
  fi
}

docker compose -f "$COMPOSE" up -d >"$work/up.log" 2>&1
up_rc=$?
for _ in $(seq 1 40); do
  state="$(docker inspect --format '{{.State.Status}}' "$gate_container" 2>/dev/null)"
  [ "$state" = "running" ] && break
  sleep 0.5
done
if [ "$up_rc" -eq 0 ] && [ "$state" = "running" ]; then
  ok "docker compose up -d started the container (entrypoint validated the environment, installed the schedule, dispatched)"
else
  bad "docker compose up -d did not leave the container running (rc=$up_rc state=${state:-absent}): $(tail -2 "$work/up.log" | tr '\n' ' ')"
fi

# --- wait for the run to reach its verdict ---------------------------------
# The run checksums both state roots before it serves, and a live root holds
# thousands of files: probing at t+1s would judge a run that has not finished
# (measured — the first version of this gate did exactly that). So the wait is
# for the run's OWN verdict, bounded and reported.
ready_seconds=""
verdict=""
for second in $(seq 1 180); do
  verdict="$(docker exec "$gate_container" python3 -c "import json; print(json.load(open('/repo/.verify/dev-run/decision.json'))['verdict'])" 2>/dev/null)"
  if [ -n "$verdict" ]; then
    ready_seconds="$second"
    break
  fi
  sleep 1
done
if [ -n "$ready_seconds" ]; then
  ok "the run reached its verdict ($verdict) ${ready_seconds}s after up -d"
else
  bad "the run wrote no verdict within 180s"
  docker compose -f "$COMPOSE" logs --no-color 2>&1 | tail -5 | sed 's/^/        /'
fi

health_request "/healthz"
python3 - "$work/health.json" >"$work/healthz.txt" 2>&1 <<'PY' || true
import json, sys

from importlib import import_module

sys.path.insert(0, "fleet")
fleet_cron = import_module("cron")

payload = json.load(open(sys.argv[1]))
status = payload.get("status")
if status != 200:
    print(f"FINDING /healthz answered {status}: {payload.get('body', '')[:200]}")
    raise SystemExit(0)
body = json.loads(payload["body"])
markers = [str(item) for item in fleet_cron.MARKERS]
print(f"        status={body.get('status')} jobs={body.get('jobs')} markers={body.get('job_markers')}")
jobs = body.get("job_markers") or []
if sorted(jobs) != sorted(markers):
    print(f"FINDING the health body names {jobs}, not the schedule's {markers}")
if body.get("state", {}).get("attributable_changes"):
    print(f"FINDING the health body reports written state: {body['state']['attributable_changes']}")
PY
if grep -q '^FINDING' "$work/healthz.txt"; then
  bad "/healthz did not answer 200 with the schedule's own jobs: $(grep '^FINDING' "$work/healthz.txt" | head -1)"
else
  marker_count="$(python3 -c "import sys; sys.path.insert(0, 'fleet'); import cron; print(len(cron.MARKERS))")"
  ok "/healthz answers 200 and names all $marker_count scheduled job(s)"
  sed 's/^/      /' "$work/healthz.txt"
fi

health_request "/nothealthz"
wrong_path="$(python3 -c "import json, sys; print(json.load(open('$work/health.json'))['status'])")"
if [ "$wrong_path" = "404" ]; then
  ok "any path other than /healthz is a 404, so the published surface serves one fact"
else
  bad "an unserved path answered $wrong_path, not 404"
fi

docker compose -f "$COMPOSE" logs --no-color >"$work/logs.txt" 2>&1
dry_runs="$(grep -c 'dry-run' "$work/logs.txt" || true)"
if [ "${dry_runs:-0}" -ge 1 ]; then
  ok "docker compose logs | grep -c 'dry-run' → $dry_runs (the decision is in the container's own log)"
  grep -E 'dev-run: ' "$work/logs.txt" | sed 's/^/        /' | head -12
else
  bad "the container's log carries no 'dry-run' line"
fi

if grep -q 'dev-run: state: attributable changes 0; UNTOUCHED' "$work/logs.txt"; then
  ok "the live-shaped snapshot is untouched by checksum before/after (attributable changes: 0)"
else
  bad "the run did not report an untouched snapshot"
fi

# the container that is up must be healthy, not merely running
health="unknown"
for _ in $(seq 1 60); do
  health="$(docker inspect --format '{{.State.Health.Status}}' "$gate_container" 2>/dev/null)"
  [ "$health" = healthy ] && break
  sleep 1
done
if [ "$health" = "healthy" ]; then
  ok "the compose healthcheck reaches the surface and reports healthy"
else
  bad "the compose healthcheck reports ${health:-unknown}, not healthy"
fi

# The stop is measured on a container that still exists: `down` removes it, so
# the exit code is read after `stop` and before `down`.
if docker compose -f "$COMPOSE" stop >"$work/stop.log" 2>&1; then
  stop_seconds=0
  for _ in $(seq 1 30); do
    state="$(docker inspect --format '{{.State.Status}}' "$gate_container" 2>/dev/null)"
    [ "$state" = "exited" ] && break
    sleep 1
    stop_seconds=$((stop_seconds + 1))
  done
  exit_code="$(docker inspect --format '{{.State.ExitCode}}' "$gate_container" 2>/dev/null)"
  if [ "$exit_code" = "0" ]; then
    ok "docker compose stop stopped it cleanly — the container's own exit code is 0 after ${stop_seconds}s"
    grep -E 'dev-run: clean stop' "$work/logs.txt" | sed 's/^/        /' | head -1
  else
    bad "the container exited with ${exit_code:-unknown}, so the stop was not the handled one"
  fi
else
  bad "docker compose stop did not succeed: $(tail -2 "$work/stop.log" | tr '\n' ' ')"
fi
if docker compose -f "$COMPOSE" down >"$work/down.log" 2>&1; then
  ok "docker compose down removed the container and its network"
else
  bad "docker compose down did not succeed: $(tail -2 "$work/down.log" | tr '\n' ' ')"
fi

# --- the state guard, provoked where a static check cannot reach -----------
printf '\n== fleet-cron-dev-run: provocation, live — the state guard and the env refusal ==\n'
# The SAME snapshot, now mounted WRITABLE: the compose run above could not have
# written it (its mounts are read-only), so reusing it keeps the container the
# only writer — which is what makes a change unambiguous.
writable="$snapshot"

# The decision document is read OUT of the container rather than through a bind
# mount: a container writes it as its own root, and a root-owned directory in the
# scratch tree is one the gate's own cleanup cannot remove (measured — the first
# version of this gate left one behind on every pass).
run_writable() { # <container> <image>
  docker rm -f "$1" >/dev/null 2>&1
  containers_to_remove+=("$1")
  docker run --name "$1" \
    -e AO_FLEET_REPO=/repo \
    -e AO_FLEET_DIR=/repo/.fleet \
    -e AO_FLEET_BOARD_DIR=/repo/.board \
    -e AO_FLEET_ROLE_TIMEOUT=60 \
    -v "$writable/.fleet:/repo/.fleet" \
    -v "$writable/.board:/repo/.board" \
    "$2" python3 /repo/infra/fleet/dev_run.py --once
}

pull_decision() { # <container> <destination>
  rm -f "$2"
  docker cp "$1:/repo/.verify/dev-run/decision.json" "$2" >/dev/null 2>&1
}

if run_writable "${gate_container}-writable" "$gate_image" >"$work/writable.log" 2>&1; then
  ok "the SAME live-shaped state, mounted WRITABLE, is untouched by checksum (the strongest form of 'untouched')"
  grep -E 'state: |verdict: ' "$work/writable.log" | sed 's/^/        /' | head -3
else
  bad "the unmutated writable run failed: $(tail -3 "$work/writable.log" | tr '\n' ' ')"
fi

# a mutant image: the prune role carries --apply, and the argv check is disabled
# so that ONLY the state guard can catch it. A one-line derived image from a real
# context, because this daemon's builder refuses a Dockerfile on stdin.
mutant_ctx="$work/mutant-context"
mkdir -p "$mutant_ctx"
cp "$IMAGE_DIR/dev_run.py" "$mutant_ctx/dev_run.py"
python3 - "$mutant_ctx/dev_run.py" <<'PY'
import pathlib, sys

path = pathlib.Path(sys.argv[1])
text = path.read_text()
text = text.replace('argv=("fleet/prune.py", "run"),', 'argv=("fleet/prune.py", "run", "--apply"),')
text = text.replace("if FORBIDDEN_TOKEN in role.argv:", "if False:")
path.write_text(text)
if 'run", "--apply"' not in text or "if False:" not in text:
    raise SystemExit("the mutation did not land")
PY
printf 'FROM %s\nCOPY dev_run.py /repo/infra/fleet/dev_run.py\n' "$gate_image" > "$mutant_ctx/Dockerfile"
if docker build "${build_network[@]}" -t "${gate_image}-apply" "$mutant_ctx" >"$work/mutant-build.log" 2>&1; then
  images_to_remove+=("${gate_image}-apply")
  # reset the mailbox so the mutant has something the retention planner would take
  printf '{"from":"gate","to":"nobody","old":true}\n' > "$writable/.fleet/outbox/mutant-entry.json"
  touch -d '30 days ago' "$writable/.fleet/outbox/mutant-entry.json"
  if run_writable "${gate_container}-mutant" "${gate_image}-apply" >"$work/mutant.log" 2>&1; then
    bad "a role that APPLIED was not caught: the run exited 0"
  else
    if grep -q 'state-written' "$work/mutant.log" && grep -q 'mutant-entry.json' "$work/mutant.log"; then
      ok "a role that applied is caught by the state guard, naming the file it wrote:"
      grep -E 'FINDING state-written|state: |verdict: ' "$work/mutant.log" | sed 's/^/        /' | head -3
    else
      bad "the mutant failed without naming the write: $(tail -3 "$work/mutant.log" | tr '\n' ' ')"
    fi
    pull_decision "${gate_container}-mutant" "$work/mutant-decision.json"
    if python3 - "$work/mutant-decision.json" >"$work/mutant-health.txt" 2>&1 <<'PY'
import json, sys

doc = json.load(open(sys.argv[1]))
from importlib.util import module_from_spec, spec_from_file_location

spec = spec_from_file_location("gate_healthz", "infra/fleet/healthz.py")
module = module_from_spec(spec)
spec.loader.exec_module(module)
status, body = module.decision_status(doc)
print(status, body.get("status"), body.get("reason"))
raise SystemExit(0 if status == 503 else 1)
PY
    then
      ok "/healthz refuses the failed run with 503: $(cat "$work/mutant-health.txt")"
    else
      bad "/healthz did not refuse the failed run: $(cat "$work/mutant-health.txt" 2>&1 | tail -1)"
    fi
  fi
else
  unmeasured "the mutant image could not be built: $(tail -2 "$work/mutant-build.log" | tr '\n' ' ')"
fi

# the environment is validated by the ENTRYPOINT, before anything is installed
for bad_value in "AO_FLEET_DRY_RUN=0" "AO_FLEET_CRON_INTERVAL=banana"; do
  if docker run --rm -e "$bad_value" "$gate_image" >"$work/env-refusal.log" 2>&1; then
    bad "the entrypoint accepted $bad_value — a container that cannot do its job would come up healthy"
  elif grep -q 'REFUSED' "$work/env-refusal.log"; then
    ok "the entrypoint refuses $bad_value, by name:"
    grep -E 'REFUSED' "$work/env-refusal.log" | head -1 | sed 's/^/        /'
  else
    bad "the entrypoint failed on $bad_value without saying why: $(tail -2 "$work/env-refusal.log" | tr '\n' ' ')"
  fi
done

# a missing state root is an error, never a silently created root-owned directory
missing="$work/absent-checkout"
mkdir -p "$missing"
if AO_FLEET_HOST_REPO="$missing" AO_FLEET_CRON_CONTAINER="${gate_container}-missing" \
   docker compose -f "$COMPOSE" up -d >"$work/missing.log" 2>&1; then
  bad "compose started with a missing state root, so it created one — the run would have been against an empty board"
  docker rm -f "${gate_container}-missing" >/dev/null 2>&1
else
  if grep -q 'bind source path does not exist' "$work/missing.log"; then
    ok "a missing state root makes compose refuse, naming the path (it is not created as root):"
    grep -m1 'bind source path does not exist' "$work/missing.log" | sed 's/^/        /'
  else
    bad "a missing state root failed for another reason: $(tail -2 "$work/missing.log" | tr '\n' ' ')"
  fi
  docker rm -f "${gate_container}-missing" >/dev/null 2>&1
  docker compose -f "$COMPOSE" down >/dev/null 2>&1
fi

# ---------------------------------------------------------------------------
printf '\n'
if [ "$fail" -gt 0 ]; then
  printf 'check-fleet-cron-dev-run: FAIL — %d finding(s)\n' "$fail" >&2
  exit 1
fi
if [ "$cannot" -gt 0 ]; then
  printf 'check-fleet-cron-dev-run: CANNOT-ASSESS — %d control(s) could not be measured\n' "$cannot" >&2
  exit 2
fi
printf 'check-fleet-cron-dev-run: OK — the harness refuses to apply, the state guard catches a role that does, and the live-shaped snapshot is provably untouched\n'
exit 0
