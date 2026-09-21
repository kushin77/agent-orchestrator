#!/usr/bin/env bash
# check-fleet-cron-image.sh — the fleet-cron image gate (issue #709, EPIC #706 D1).
#
# THE CONTRACT THIS HOLDS
#   "Package our scheduled automation into one image so it can run off this box"
#   is only a porting step if the list of things the image needs is written down
#   and CHECKED. `infra/fleet/inventory.yaml` is that list, and this gate holds
#   it and `infra/fleet/Dockerfile` in lock-step — so a dependency the image grew
#   that nobody recorded, and a listing the image does not honour, are refusals
#   rather than comments.
#
# WHAT IS MEASURED (against the real tree and the real image, not a description)
#   A. the declared surface: the files exist, the base is the declared
#      `python:3.12-slim`, the checkout is `COPY`d to `/repo`, and the entrypoint
#      is tini + `entrypoint.sh`;
#   B. THE SCHEDULE HAS ONE OWNER: `entrypoint.sh` installs it by calling
#      `fleet/cron.py install`, and no file under `infra/fleet/` carries a second
#      copy of a crontab line. The three markers and their count are re-READ from
#      `fleet/cron.MARKERS`, so the inventory cannot drift from the module that
#      owns them;
#   C. THE RUNGS are re-read from `fleet/watchdog.py`, and the state roots from
#      `fleet/runtime.py` — `AO_FLEET_DIR` really moves the fleet directory, and
#      `.board/` is really NOT namespaced;
#   D. THE INTERPRETER the schedule names is derived from `fleet/cron.py`'s own
#      line builder — never hardcoded here — and the image must provide it;
#   E. NO SECRET is baked: a Dockerfile that names a token/value pair, or copies
#      a credential file, is refused;
#   F. THE IMAGE ITSELF: it builds, the issue's own `docker run … fleet/cron.py
#      status` exits 0 and names the schedule it installed, the same command with
#      the entrypoint's install suppressed must NOT, every declared binary
#      resolves inside the image, and no build-host state (`.board/focus.json`)
#      is baked into it.
#
# PROVOCATION (GR-12 — a gate that cannot fail is a formality)
#   The static audit is a pure function over three paths, so it is run against a
#   scratch copy carrying ONE mutation. Each mutation must produce its OWN named
#   finding, and each is asserted to have applied, so a mutation that never
#   landed cannot be reported as caught:
#     package-undeclared     a package installed but not in the inventory
#     package-uninstalled    a package in the inventory but not installed
#     schedule-second-copy   a crontab line written outside `fleet/cron.py`
#     schedule-not-owned     an entrypoint that no longer asks the owner
#     secret-baked           a literal token in the image
#     marker-drift           the inventory's schedule markers vs `fleet/cron.py`
#     rung-drift             the inventory's rungs vs `fleet/watchdog.py`
#     interpreter-missing    the Dockerfile no longer provides the named path
#     state-root-missing     the Dockerfile no longer creates a state root
#     context-exclusion-lost `.dockerignore` no longer excludes a state root
#   The live half provokes twice more, against one-line derived images: removing
#   `gh` must be reported as a missing binary, and removing `cron` must break the
#   issue's own `status` command — which is how those two inventory entries are
#   proved load-bearing rather than argued for.
#
# THE BUILD NETWORK, MEASURED RATHER THAN ASSUMED
#   The build downloads packages, and on a host whose containers cannot resolve
#   DNS that download hangs rather than fails. So the gate PROBES resolution
#   first: when a bridge container resolves, the build runs exactly as the issue
#   writes it; when it does not, the gate says so, builds with `--network=host`
#   (the same name resolution, borrowed from the host) and keeps the environment
#   defect VISIBLE instead of hiding it. Measured on this box 2026-09-15: a
#   bridge container's `/etc/resolv.conf` names the LAN resolver, which refuses
#   the bridge subnet, so resolution fails `[Errno -3] Temporary failure in name
#   resolution` and `apt-get update` stalls; `--network=host` resolves. That is
#   this host's defect, not the image's, and the environment fix is the host's.
#
# No writes outside the scratch directory. Exit-code contract:
# 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# Usage: bash scripts/check-fleet-cron-image.sh
#
# ---knowledge---
# module_id: scripts.check-fleet-cron-image
# system: governance
# app: gates
# solution_class: enterprise
# patterns: [tri-state-exit, provoked-negative-control, no-false-green, named-refusal, schema-validation]
# derives_from: null
# owner_sme: platform-sme
# tier: L1
# interfaces: [exit 0 OK, exit 1 NOT-OK, exit 2 CANNOT-ASSESS]
# invariants: ""
# gotchas: ""
# related: ["#706", "#709"]
# do_not_duplicate: null
# ---knowledge---
set -uo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"
cd "$root" || exit 2

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-fleet-cron-image: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

for required in infra/fleet/Dockerfile infra/fleet/entrypoint.sh \
                infra/fleet/inventory.yaml infra/fleet/README.md \
                fleet/cron.py fleet/watchdog.py fleet/runtime.py .dockerignore; do
  if [ ! -f "$required" ]; then
    echo "check-fleet-cron-image: FAIL — $required is missing" >&2
    exit 1
  fi
done

# The scratch tree name is the sanctioned fleet idiom, NOT a template with a run
# of one letter: that literal trips this repo's own unfinished-marker scan, so
# the gate would fail docs-lint for a reason that looks like nothing. `mkdir`
# without `-p` refuses loudly instead of silently reusing another run's tree.
work="/tmp/ao-fleet-cron-image.$(date +%s%N).$"
mkdir "$work" 2>/dev/null || {
  echo "check-fleet-cron-image: CANNOT-ASSESS — no scratch directory available" >&2
  exit 2
}

tag="agent-fleet-cron:gate-$$-$(date +%s)"
images_to_remove=()

cleanup() {
  local image
  for image in "${images_to_remove[@]:-}"; do
    [ -n "$image" ] && docker image rm -f "$image" >/dev/null 2>&1
  done
  rm -rf "$work"
}
trap cleanup EXIT

fail=0
cannot=0
ok()   { printf '  OK    %s\n' "$1"; }
bad()  { printf '  FAIL  %s\n' "$1" >&2; fail=$((fail + 1)); }
note() { printf '  NOTE  %s\n' "$1"; }
unmeasured() { printf '  SKIP  %s\n' "$1"; cannot=$((cannot + 1)); }

# ---------------------------------------------------------------------------
# The static audit — a pure function over (image dir, .dockerignore, repo root).
# It prints `OK`/`NOTE` lines for what it measured, `<code>: <detail>` for each
# finding, and `unmeasured: <detail>` for a control it could not measure at all.
# ---------------------------------------------------------------------------
cat > "$work/audit.py" <<'PY'
#!/usr/bin/env python3
"""Audit the fleet-cron image's declared surface against its inventory (#709)."""
from __future__ import annotations

import os
import re
import sys

try:
    import yaml
except ImportError:  # pragma: no cover
    print("unmeasured: PyYAML is not installed, so the inventory cannot be read")
    raise SystemExit(0)

IMAGE_DIR, DOCKERIGNORE, REPO = sys.argv[1], sys.argv[2], sys.argv[3]

DOCKERFILE = os.path.join(IMAGE_DIR, "Dockerfile")
ENTRYPOINT = os.path.join(IMAGE_DIR, "entrypoint.sh")
INVENTORY = os.path.join(IMAGE_DIR, "inventory.yaml")

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


def logical_lines(text: str) -> list[str]:
    """Lines with their backslash continuations joined.

    Without this a multi-line `apt-get install` reads as a package list of ZERO —
    only the flags sit on the first physical line — and every entry would be
    reported uninstalled.
    """
    out, buffer = [], ""
    for raw in text.splitlines():
        line = raw.rstrip()
        if line.endswith("\\"):
            buffer += line[:-1] + " "
            continue
        buffer += line
        out.append(buffer)
        buffer = ""
    if buffer:
        out.append(buffer)
    return out


def apt_packages(text: str) -> set[str]:
    """Every package an `apt-get install` in this text actually installs."""
    packages: set[str] = set()
    for line in logical_lines(text):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        match = re.search(r"apt-get\s+install\b", stripped)
        if not match:
            continue
        # Up to the command separator: tokens after it belong to the next
        # command, and `rm` reads as a package otherwise.
        segment = stripped[match.end():].split(";")[0]
        for token in segment.split():
            token = token.strip("\\")
            if not token or token.startswith("-") or token.endswith(".deb"):
                continue
            packages.add(token)
    return packages


try:
    inv = yaml.safe_load(read(INVENTORY))
except Exception as exc:  # noqa: BLE001 — an unreadable contract is a finding
    bad("inventory-unreadable", f"{INVENTORY} does not parse ({exc})")
    inv = None

if inv is None:
    for code, detail in findings:
        print(f"{code}: {detail}")
    raise SystemExit(0)
if not isinstance(inv, dict):
    print("inventory-unreadable: the top level is not a mapping")
    raise SystemExit(0)
if inv.get("schema") != "fleet-cron-inventory-v1":
    bad("inventory-schema", f"schema is {inv.get('schema')!r}, not fleet-cron-inventory-v1")

dockerfile = read(DOCKERFILE)
entrypoint = read(ENTRYPOINT)
dockerignore_text = read(DOCKERIGNORE)

# The CODE of each file, with its comments stripped. Both files discuss the
# very things the checks look for — the entrypoint explains that it calls
# `fleet/cron.py install`, the Dockerfile explains that it provides
# `/usr/bin/python3` — so a check that cannot tell code from commentary would
# still pass on an image that had stopped DOING either one.
def code_of(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


entrypoint_code = code_of(entrypoint)
dockerfile_code = code_of(dockerfile)

# --- A. the declared surface ------------------------------------------------
image = inv.get("image") or {}
base = str(image.get("base", ""))
from_lines = [ln.strip() for ln in dockerfile.splitlines() if ln.strip().upper().startswith("FROM ")]
if from_lines and from_lines[0].split()[1] == base:
    ok(f"the base image is the declared one ({base})")
else:
    bad("base-image", f"the inventory declares base {base!r}; the Dockerfile starts from {from_lines[:1]!r}")

workdir = str(image.get("workdir", ""))
if re.search(rf"(?m)^\s*COPY\s+\.\s+{re.escape(workdir)}/?\s*$", dockerfile) and \
        re.search(rf"(?m)^\s*WORKDIR\s+{re.escape(workdir)}\s*$", dockerfile):
    ok(f"the checkout is COPY'd to {workdir}")
else:
    bad("copy-layout", f"the Dockerfile does not COPY . {workdir} + WORKDIR {workdir}")

entry = str(image.get("entrypoint", ""))
if re.search(r"(?m)^\s*ENTRYPOINT\b", dockerfile) and "tini" in dockerfile and entry in dockerfile:
    ok(f"tini is PID 1 and the entrypoint is {entry}")
else:
    bad("entrypoint-wiring", f"the Dockerfile does not run tini + {entry}")

if not os.path.isfile(os.path.join(IMAGE_DIR, os.path.basename(entry))):
    bad("entrypoint-missing", f"{entry} is declared as the entrypoint but does not exist")

# --- B. the schedule has exactly one owner ----------------------------------
schedule = inv.get("schedule") or {}
markers = [str(m) for m in schedule.get("markers") or []]
declared_lines = schedule.get("lines")

sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "fleet"))
measured_markers: list[str] = []
built_lines = ""
try:
    import cron as fleet_cron  # noqa: PLC0415 — measured here, not at gate start

    measured_markers = [str(m) for m in fleet_cron.MARKERS]
    built_lines = "\n".join(
        [fleet_cron.line(2), fleet_cron.prune_line(), fleet_cron.reconcile_line(2)]
    )
except Exception as exc:  # noqa: BLE001
    print(f"unmeasured: fleet/cron.py could not be measured ({exc}); the schedule controls were skipped")

if measured_markers:
    if markers == measured_markers and declared_lines == len(measured_markers):
        ok(f"the schedule is {declared_lines} crontab lines, matching fleet/cron.MARKERS")
    else:
        bad("marker-drift",
            f"the inventory declares {declared_lines} line(s) {markers}; fleet/cron.py owns "
            f"{len(measured_markers)} {measured_markers}")

    if "fleet/cron.py" in entrypoint_code and "install" in entrypoint_code:
        ok("entrypoint.sh installs the schedule by calling fleet/cron.py install")
    else:
        bad("schedule-not-owned", "entrypoint.sh does not call `fleet/cron.py install`")

    # A crontab line anywhere under infra/fleet/ is a second copy of the
    # schedule, whoever writes it — fed to `crontab -` by hand, COPY'd in as a
    # file, or quoted in a comment as the schedule to be maintained.
    CRON_FIVE_FIELDS = re.compile(
        r"(?:[0-9]{1,2}|\*)(?:[/,-][0-9]+)*\s+"
        r"(?:[0-9]{1,2}|\*)(?:[/,-][0-9]+)*\s+"
        r"(?:[0-9]{1,2}|\*)(?:[/,-][0-9]+)*\s+"
        r"(?:[0-9]{1,2}|\*)(?:[/,-][0-9]+)*\s+"
        r"(?:[0-9]{1,2}|\*)(?:[/,-][0-9]+)*"
    )
    for dirpath, _dirnames, filenames in os.walk(IMAGE_DIR):
        for name in sorted(filenames):
            path = os.path.join(dirpath, name)
            inside = os.path.commonpath([REPO, os.path.abspath(path)]) == REPO
            rel = os.path.relpath(path, REPO) if inside else path
            try:
                body = read(path)
            except (OSError, UnicodeDecodeError):
                continue
            match = CRON_FIVE_FIELDS.search(body)
            if match:
                bad("schedule-second-copy",
                    f"{rel} carries a crontab line ({match.group(0).strip()!r}); "
                    "the schedule belongs to fleet/cron.py alone")

    for pattern, detail in (
        (r"(?m)^\s*(?:COPY|ADD)\s+.*crontab", "a crontab file is COPY'd into the image"),
        (r"\|\s*crontab\s+-", "a crontab is written by hand instead of by fleet/cron.py"),
        (r"(?m)^\s*RUN\s+crontab\b", "a crontab is written by hand instead of by fleet/cron.py"),
    ):
        if re.search(pattern, dockerfile) or re.search(pattern, entrypoint_code):
            bad("schedule-second-copy", f"the Dockerfile/entrypoint {detail}")

    if not any(code == "schedule-second-copy" for code, _ in findings):
        ok("no second copy of the schedule exists under infra/fleet/")

# --- C. the rungs and the state roots, re-read from their owners ------------
rung_scripts = [str(s) for s in (inv.get("rungs") or {}).get("scripts") or []]
measured_rungs: list[str] = []
try:
    import watchdog as fleet_watchdog  # noqa: PLC0415

    measured_rungs = [str(item[1]) for item in fleet_watchdog.RUNGS] + [str(fleet_watchdog.MONITOR_PATTERN)]
except Exception as exc:  # noqa: BLE001
    print(f"unmeasured: fleet/watchdog.py could not be measured ({exc}); the rung control was skipped")

if measured_rungs:
    if sorted(rung_scripts) == sorted(measured_rungs):
        ok(f"the {len(measured_rungs)} rungs the watchdog owns are the inventory's")
    else:
        bad("rung-drift",
            f"the inventory declares {sorted(rung_scripts)}; fleet/watchdog.py owns {sorted(measured_rungs)}")

roots = inv.get("state_roots") or []
declared_root_paths = {str(r.get("path")) for r in roots if isinstance(r, dict)}
for path in sorted(declared_root_paths):
    if path in dockerfile_code:
        ok(f"the Dockerfile creates the state root {path}")
    else:
        bad("state-root-missing", f"the inventory declares the state root {path}; the Dockerfile never creates it")

namespaced = {str(r.get("path")): bool(r.get("namespaced")) for r in roots if isinstance(r, dict)}
if namespaced.get(str(workdir) + "/.fleet") and not namespaced.get(str(workdir) + "/.board"):
    ok("the state roots are declared as they are: .fleet namespaced, .board shared and un-namespaced")
else:
    bad("state-roots-undeclared",
        ".fleet must be declared namespaced (AO_FLEET_DIR) and .board must be declared NOT namespaced")

# The exclusion is a SUBSET requirement: another image may exclude more, never
# less, because an image that bakes one host's claim ledger hands every
# container the same stale board.
exclusions = {str(e).strip() for e in inv.get("context_exclusions") or []}
ignore_entries = {ln.strip() for ln in dockerignore_text.splitlines() if ln.strip() and not ln.strip().startswith("#")}
missing_exclusions = sorted(e for e in exclusions if e not in ignore_entries)
if missing_exclusions:
    bad("context-exclusion-lost",
        f".dockerignore no longer excludes {missing_exclusions} — the image would bake build-host state")
else:
    ok(f".dockerignore still excludes the state roots {sorted(exclusions)}")

# --- D. the interpreter the schedule names ----------------------------------
binaries = [str(b) for b in inv.get("binaries") or []]
if built_lines:
    found = re.findall(r"/usr/[A-Za-z0-9._/-]*python[0-9.]*", built_lines)
    interpreter = found[0] if found else ""
    if interpreter:
        ok(f"the interpreter the schedule names, measured from fleet/cron.py: {interpreter}")
        if interpreter in binaries:
            ok(f"{interpreter} is declared in the inventory's binaries")
        else:
            bad("interpreter-missing", f"the schedule names {interpreter}; the inventory's binaries do not declare it")
        if interpreter in dockerfile_code:
            ok(f"the Dockerfile provides {interpreter}")
        else:
            bad("interpreter-missing", f"the Dockerfile never provides {interpreter}, the path fleet/cron.py writes")
    else:
        note("fleet/cron.py's lines name no absolute interpreter; that control is not applicable")

# --- E. no secret is baked --------------------------------------------------
SECRET_NAME = re.compile(r"(?:TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|APIKEY|API_KEY|PRIVATE_KEY)", re.IGNORECASE)
secret_hits: list[str] = []
for line in logical_lines(dockerfile):
    stripped = line.strip()
    if stripped.startswith("#"):
        continue
    for match in re.finditer(r"(?:ENV|ARG)\s+([A-Za-z_][A-Za-z0-9_]*)(?:=|\s+)(\S*)", stripped):
        name, value = match.group(1), match.group(2)
        if SECRET_NAME.search(name) and value and not value.startswith("$"):
            secret_hits.append(f"{name}={value}")
    for match in re.finditer(r"^\s*(?:COPY|ADD)\s+(.*)$", stripped):
        for token in match.group(1).split():
            tail = os.path.basename(token.rstrip("/"))
            if tail in {".env", "credentials.json", "id_rsa", "id_ed25519"} or tail.endswith((".pem", ".key", ".pfx", ".p12")):
                secret_hits.append(token)
if secret_hits:
    bad("secret-baked", "the image bakes a credential: " + ", ".join(sorted(set(secret_hits))))
else:
    ok("no literal secret, key file or credential file is baked into the image")

# --- F. the packages, BOTH ways ---------------------------------------------
declared_apt = sorted(str(p.get("install")) for p in inv.get("packages") or []
                      if isinstance(p, dict) and p.get("via") == "apt")
installed = sorted(apt_packages(dockerfile))

undeclared = sorted(set(installed) - set(declared_apt))
uninstalled = sorted(set(declared_apt) - set(installed))
if undeclared:
    bad("package-undeclared", f"the Dockerfile installs {undeclared}, which the inventory does not declare")
if uninstalled:
    bad("package-uninstalled", f"the inventory declares {uninstalled}, which the Dockerfile never installs")
if not undeclared and not uninstalled:
    ok(f"the {len(declared_apt)} apt packages are the inventory's, in both directions")

for package in inv.get("packages") or []:
    if not isinstance(package, dict) or package.get("via") != "release":
        continue
    version = str(package.get("version", ""))
    url = str(package.get("url", ""))
    name = str(package.get("install", "?"))
    if version and version in dockerfile and url and url in dockerfile:
        ok(f"{name} is installed from the pinned release {version}")
    else:
        bad("package-uninstalled",
            f"the inventory pins {name} to {version} from {url}; the Dockerfile does not install that release")

for code, detail in findings:
    print(f"{code}: {detail}")
PY

run_audit() { python3 "$work/audit.py" "$1" "$2" "$root"; }
show_codes() { grep -E "^[a-z][a-z0-9-]*: " || true; }

# ---------------------------------------------------------------------------
# A. the real tree: the audit must find nothing.
# ---------------------------------------------------------------------------
printf '== fleet-cron-image: the declared surface, against its own inventory ==\n'
real_output="$(run_audit "$root/infra/fleet" "$root/.dockerignore")"
printf '%s\n' "$real_output" | grep -E '^\s+(OK|NOTE|SKIP)\s' || true

real_codes="$(printf '%s\n' "$real_output" | show_codes)"
real_unmeasured="$(printf '%s\n' "$real_output" | grep -E '^unmeasured: ' || true)"

if [ -z "$real_codes" ]; then
  ok "the image's declared surface matches infra/fleet/inventory.yaml"
else
  while IFS= read -r line; do
    [ -n "$line" ] && bad "$line"
  done <<< "$real_codes"
fi
if [ -n "$real_unmeasured" ]; then
  while IFS= read -r line; do
    [ -n "$line" ] && unmeasured "${line#unmeasured: }"
  done <<< "$real_unmeasured"
fi

# ---------------------------------------------------------------------------
# Provocation — one mutation per control, each in its own scratch copy.
# ---------------------------------------------------------------------------
cat > "$work/mutate.py" <<'PY'
#!/usr/bin/env python3
"""Apply exactly one mutation to a scratch copy of the fleet-cron image files.

Every case asserts its anchor matched exactly once AND that the text actually
changed, so a mutation that silently did nothing can never be reported as
"the gate caught it".
"""
from __future__ import annotations

import pathlib
import sys


def sub(path: pathlib.Path, old: str, new: str) -> None:
    text = path.read_text()
    count = text.count(old)
    assert count == 1, f"anchor matched {count} times in {path}: {old!r}"
    path.write_text(text.replace(old, new))
    assert path.read_text() != text, f"the replacement was a no-op in {path}"


def append(path: pathlib.Path, extra: str) -> None:
    text = path.read_text()
    path.write_text(text + extra)
    assert path.read_text() != text, f"the append was a no-op in {path}"


def case_package_undeclared(image: pathlib.Path, _ignore: pathlib.Path) -> None:
    sub(image / "Dockerfile", "        ca-certificates \\\n", "        ca-certificates \\\n        vim \\\n")


def case_package_uninstalled(image: pathlib.Path, _ignore: pathlib.Path) -> None:
    sub(image / "Dockerfile", "        cron \\\n", "")


def case_schedule_second_copy(image: pathlib.Path, _ignore: pathlib.Path) -> None:
    append(image / "entrypoint.sh",
           "\necho '17 4 * * * cd /repo && /usr/bin/python3 fleet/prune.py run --apply' | crontab -\n")


def case_schedule_not_owned(image: pathlib.Path, _ignore: pathlib.Path) -> None:
    sub(image / "entrypoint.sh",
        'python3 "${REPO}/fleet/cron.py" install --interval "${INTERVAL}"',
        "true")


def case_secret_baked(image: pathlib.Path, _ignore: pathlib.Path) -> None:
    append(image / "Dockerfile", "\nENV GITHUB_TOKEN=not-a-real-token\n")


def case_marker_drift(image: pathlib.Path, _ignore: pathlib.Path) -> None:
    sub(image / "inventory.yaml", "    - ao-fleet-prune\n", "")


def case_rung_drift(image: pathlib.Path, _ignore: pathlib.Path) -> None:
    sub(image / "inventory.yaml", "    - fleet/monitor.py\n", "")


def case_interpreter_missing(image: pathlib.Path, _ignore: pathlib.Path) -> None:
    # The whole RUN step goes: the path appears in the Dockerfile's commentary
    # too, so a global text replacement would not prove that the IMAGE stopped
    # providing it.
    sub(image / "Dockerfile",
        "RUN set -eux; \\\n"
        "    if [ ! -x /usr/bin/python3 ]; then ln -sf /usr/local/bin/python3 /usr/bin/python3; fi; \\\n"
        "    /usr/bin/python3 -V\n",
        "")


def case_state_root_missing(image: pathlib.Path, _ignore: pathlib.Path) -> None:
    sub(image / "Dockerfile", "mkdir -p /repo/.fleet /repo/.board /repo/.verify", "mkdir -p /repo/.fleet /repo/.verify")


def case_context_exclusion_lost(image: pathlib.Path, ignore: pathlib.Path) -> None:
    sub(ignore, "\n.fleet\n", "\n")


CASES = {
    "package-undeclared": case_package_undeclared,
    "package-uninstalled": case_package_uninstalled,
    "schedule-second-copy": case_schedule_second_copy,
    "schedule-not-owned": case_schedule_not_owned,
    "secret-baked": case_secret_baked,
    "marker-drift": case_marker_drift,
    "rung-drift": case_rung_drift,
    "interpreter-missing": case_interpreter_missing,
    "state-root-missing": case_state_root_missing,
    "context-exclusion-lost": case_context_exclusion_lost,
}

name, image_dir, ignore_path = sys.argv[1], pathlib.Path(sys.argv[2]), pathlib.Path(sys.argv[3])
CASES[name](image_dir, ignore_path)
PY

provoke() {  # <label> <expected finding code> <case name>
  local label="$1" expected="$2" name="$3"
  local dir="$work/mut-$name" ignore="$work/mut-$name.dockerignore"
  rm -rf "$dir"
  mkdir -p "$dir"
  cp "$root/infra/fleet/Dockerfile" "$root/infra/fleet/entrypoint.sh" "$root/infra/fleet/inventory.yaml" "$dir/"
  cp "$root/.dockerignore" "$ignore"
  if ! python3 "$work/mutate.py" "$name" "$dir" "$ignore" >"$work/$name.apply" 2>&1; then
    unmeasured "the '$label' control could not be staged: $(tail -1 "$work/$name.apply")"
    return
  fi
  local output
  output="$(run_audit "$dir" "$ignore")"
  # The finding is looked for in a FILE, never through `… | grep -q`: under
  # `set -o pipefail` a grep that exits on its first match can SIGPIPE the
  # producer and promote that 141 to the pipeline's status, so a finding that IS
  # present reads as absent. (Measured in this repo before — the same trap.)
  printf '%s\n' "$output" > "$work/$name.out"
  if grep -qE "^${expected}: " "$work/$name.out"; then
    ok "$label → refused by name ($expected)"
    grep -E "^${expected}: " "$work/$name.out" | sed 's/^/        /'
  else
    bad "$label → the gate did NOT report $expected (that control is not load-bearing)"
    grep -E '^[a-z][a-z0-9-]*: ' "$work/$name.out" | sed 's/^/        /' || true
  fi
}

printf '\n== fleet-cron-image: provocation — every control must be able to fail ==\n'
provoke "a package installed but not declared"      package-undeclared       package-undeclared
provoke "a declared package nobody installs"        package-uninstalled      package-uninstalled
provoke "the schedule written a second time"        schedule-second-copy     schedule-second-copy
provoke "an entrypoint that stops asking the owner" schedule-not-owned       schedule-not-owned
provoke "a literal token in the image"              secret-baked             secret-baked
provoke "the inventory's markers out of step"       marker-drift             marker-drift
provoke "the inventory's rungs out of step"         rung-drift               rung-drift
provoke "an image without the named interpreter"    interpreter-missing      interpreter-missing
provoke "an image without a declared state root"    state-root-missing       state-root-missing
provoke "a .dockerignore that stops excluding it"   context-exclusion-lost   context-exclusion-lost

# ---------------------------------------------------------------------------
# G. the image itself. No daemon is CANNOT-ASSESS, never a silent pass.
# ---------------------------------------------------------------------------
printf '\n== fleet-cron-image: the image builds and its entrypoint installs the schedule ==\n'

if ! command -v docker >/dev/null 2>&1; then
  printf 'check-fleet-cron-image: CANNOT-ASSESS — docker not found; the live controls were not run\n' >&2
  exit 2
fi
if ! timeout 30 docker info >/dev/null 2>&1; then
  printf 'check-fleet-cron-image: CANNOT-ASSESS — the docker daemon is unreachable; the live controls were not run\n' >&2
  exit 2
fi

build_network=()
if timeout 25 docker run --rm python:3.12-slim python3 -c \
     "import socket; socket.gethostbyname('deb.debian.org')" >/dev/null 2>&1; then
  note "a bridge container resolves DNS here, so the build runs exactly as the issue writes it"
else
  build_network=(--network=host)
  note "a bridge container CANNOT resolve DNS on this host (the LAN resolver refuses the bridge"
  note "subnet), so the build borrows the host's name resolution with --network=host. That is"
  note "this host's defect, not the image's: with working container DNS the plain command builds"
  note "the same image."
fi

build_log="$work/build.log"
if docker build ${build_network[@]+"${build_network[@]}"} -f infra/fleet/Dockerfile -t "$tag" . >"$build_log" 2>&1; then
  images_to_remove+=("$tag")
  ok "docker build -f infra/fleet/Dockerfile succeeds (dev-first: the image builds on this box)"
else
  bad "docker build -f infra/fleet/Dockerfile failed:"
  tail -20 "$build_log" | sed 's/^/        /' >&2
fi

if [ "${#images_to_remove[@]}" -gt 0 ]; then
  status_out="$work/status.out"
  status_rc=0
  docker run --rm "$tag" python3 fleet/cron.py status >"$status_out" 2>&1 || status_rc=$?
  # `^cron: installed (` is the STATUS command's own line. The entrypoint's
  # install banner also says `cron: installed — …`, so matching the bare prefix
  # would accept the banner as if it were the answer to the question asked.
  if [ "$status_rc" -eq 0 ] && grep -q '^cron: installed (' "$status_out"; then
    ok "docker run --rm <image> python3 fleet/cron.py status → rc 0, and it reports the schedule it installed:"
    sed -n '/^cron: installed (/,$p' "$status_out" | head -5 | sed 's/^/        /'
  else
    bad "the issue's own status command did not report an installed schedule (rc=$status_rc)"
    sed 's/^/        /' "$status_out" | head -10 >&2
  fi

  # The same command with the entrypoint's install suppressed must NOT agree:
  # that disagreement is what proves the ENTRYPOINT is what installed it.
  no_install_rc=0
  docker run --rm -e AO_FLEET_CRON_NO_INSTALL=1 "$tag" python3 fleet/cron.py status >"$work/noinstall.out" 2>&1 || no_install_rc=$?
  if [ "$no_install_rc" -ne 0 ] && grep -q 'cron: NOT installed' "$work/noinstall.out"; then
    ok "AO_FLEET_CRON_NO_INSTALL=1 → the same command reports NOT installed (rc=$no_install_rc), so the entrypoint is what installs it"
  else
    bad "with AO_FLEET_CRON_NO_INSTALL=1 the status command still reported an installed schedule (rc=$no_install_rc)"
    sed 's/^/        /' "$work/noinstall.out" | head -10 >&2
  fi

  # Every declared binary resolves INSIDE the image, measured there.
  probe="$work/probe.sh"
  {
    printf 'missing=""\nfor b in'
    python3 -c "
import yaml
for name in yaml.safe_load(open('infra/fleet/inventory.yaml')).get('binaries') or []:
    print(name)
" | while IFS= read -r name; do printf ' %s' "$name"; done
    printf '; do\n'
    printf '  case \"$b\" in\n'
    printf '    /*) [ -x \"$b\" ] || missing=\"$missing $b\" ;;\n'
    printf '    *) command -v \"$b\" >/dev/null 2>&1 || missing=\"$missing $b\" ;;\n'
    printf '  esac\n'
    printf 'done\n'
    printf 'if [ -n \"$missing\" ]; then echo \"MISSING:$missing\"; exit 1; fi\n'
    printf 'echo \"ALL-PRESENT\"\n'
  } > "$probe"
  if docker run --rm -v "$probe:/tmp/probe.sh:ro" "$tag" sh /tmp/probe.sh >"$work/binaries.out" 2>&1 \
     && grep -q 'ALL-PRESENT' "$work/binaries.out"; then
    ok "every binary the inventory declares resolves inside the image"
  else
    bad "a declared binary does not resolve inside the image: $(tr -d '\n' < "$work/binaries.out" | head -c 200)"
  fi

  # The state roots are mounts, not payload: nothing from the build host's board
  # may be baked in, and both roots must exist as empty directories.
  if docker run --rm "$tag" sh -c 'test ! -e /repo/.board/focus.json && test ! -e /repo/.board/claims.jsonl && test -d /repo/.fleet && test -d /repo/.board' >/dev/null 2>&1; then
    ok "no build-host board is baked in, and both state roots exist as empty mount points"
  else
    bad "the image bakes build-host state, or a declared state root is absent"
  fi

  if docker run --rm "$tag" python3 -c "import sys; sys.path.insert(0, '/repo/fleet'); import runtime; raise SystemExit(0 if str(runtime.FLEET_DIR) == '/repo/.fleet' else 1)"; then
    ok "in the image, fleet/runtime.py resolves AO_FLEET_DIR to /repo/.fleet"
  else
    bad "the image's fleet directory is not /repo/.fleet"
  fi

  # --- provocation, live: two inventory entries proved load-bearing ---------
  #
  # Each mutant is a one-line derived image built from a REAL context directory.
  # A Dockerfile on stdin is not an option: this daemon runs the legacy builder,
  # which refuses `-f -` ("can't use stdin for both build context and
  # dockerfile") and would otherwise read the text as a context tarball — so a
  # mutant written that way is not built at all and proves nothing.
  printf '\n== fleet-cron-image: provocation — the declared dependencies are load-bearing ==\n'
  derive() {  # <name> <RUN line>
    local name="$1" run_line="$2" ctx="$work/ctx-$1"
    rm -rf "$ctx"
    mkdir -p "$ctx"
    printf 'FROM %s\n%s\n' "$tag" "$run_line" > "$ctx/Dockerfile"
    docker build -t "${tag}-${name}" "$ctx" >"$work/derive-$name.log" 2>&1
  }

  if derive nogh 'RUN rm -f "$(command -v gh)"'; then
    images_to_remove+=("${tag}-nogh")
    # Same reason as the static provocation: the probe's output is looked for in a
    # file rather than through a `… | grep -q` pipeline that pipefail can invert.
    docker run --rm -v "$probe:/tmp/probe.sh:ro" "${tag}-nogh" sh /tmp/probe.sh >"$work/nogh.out" 2>&1 || true
    if grep -q 'MISSING:.*gh' "$work/nogh.out"; then
      ok "an image with gh removed is reported as missing it, by name — the gh entry is load-bearing"
      grep -E 'MISSING:' "$work/nogh.out" | sed 's/^/        /' || true
    else
      bad "removing gh from the image was NOT detected — the binary probe cannot fail"
      sed 's/^/        /' "$work/nogh.out" | tail -5 >&2
    fi
  else
    unmeasured "the gh mutant image could not be derived: $(tail -1 "$work/derive-nogh.log")"
  fi

  if derive nocron 'RUN rm -f "$(command -v crontab)" "$(command -v cron)"'; then
    images_to_remove+=("${tag}-nocron")
    if docker run --rm "${tag}-nocron" python3 fleet/cron.py status >"$work/nocron.out" 2>&1; then
      bad "removing cron from the image did NOT break fleet/cron.py status — the cron entry is not load-bearing"
    else
      ok "an image without cron cannot run the issue's own status command — the cron entry is load-bearing:"
      grep -m1 -E "Error|error" "$work/nocron.out" | sed 's/^/        /' || true
    fi
  else
    unmeasured "the cron mutant image could not be derived: $(tail -1 "$work/derive-nocron.log")"
  fi
else
  note "the image did not build, so the live controls above it were not run"
fi

# ---------------------------------------------------------------------------
printf '\n'
if [ "$fail" -gt 0 ]; then
  printf 'check-fleet-cron-image: FAIL — %d finding(s)\n' "$fail" >&2
  exit 1
fi
if [ "$cannot" -gt 0 ]; then
  printf 'check-fleet-cron-image: CANNOT-ASSESS — %d control(s) could not be measured\n' "$cannot" >&2
  exit 2
fi
printf 'check-fleet-cron-image: OK — the inventory is the image contract, every control is provoked, and the image builds and installs the schedule\n'
exit 0
