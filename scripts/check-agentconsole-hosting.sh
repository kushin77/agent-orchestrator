#!/usr/bin/env bash
# check-agentconsole-hosting.sh — the AgentConsole hosting contract cannot
# regress silently (issue #1029, parent #607).
#
# THE DEFECT THIS EXISTS FOR
#   The go-live was DECLARED for months and never EXERCISED. The declarations
#   said "nothing here is live", and the image recipe had never been run — its
#   only builder was the RETIRED Cloud Run route — so a defect INSIDE the
#   artifact was invisible: `portal/Dockerfile` installed PyYAML but not
#   `cryptography`, so the container exited 1 at boot instead of serving its own
#   CMD. A declare-only control cannot fail, so nobody noticed it had. This gate
#   is the mechanical half: it asserts the hosting contract in the tree, offline.
#
# WHAT IS MEASURED (text properties; offline; deterministic; always assessed)
#   P1  image recipe — `portal/Dockerfile` installs BOTH PyYAML and
#       `cryptography` (the boot path imports it) and its CMD runs the module.
#   P2  the overlay's declared contract — `contrib/shared-services/
#       agentconsole.compose.yml` names the container, publishes the console
#       port, joins the external shared-services net, restarts unless-stopped,
#       probes `/api/healthz`, and mounts the JWKS mirror + the fleet/ledger/
#       board state.
#   P3  the three composed surfaces are promoted TOGETHER — `fleet_projection`,
#       `remote_control` and `operator_terminal` agree (all on, or all off). A
#       PARTIAL promotion is a console that 404s half its panels, or a
#       terminal whose read/write halves are dark.
#   P4  the hosting declaration names the LIVE host (the shared-services
#       cluster) and the retired GCP route as RETIRED — and no longer claims
#       the surface is not deployed.
#   P5  the env contract — the JWKS-file and allowlist names are declared with
#       their fail-closed consequence (healthy ≠ usable).
#   P6  the cutover recipe declares the **two-rule** fronting and, in
#       particular, the `^/auth/` path rule — the console's unauthenticated
#       redirect is RELATIVE (`AUTH_GATE_LOGIN_PATH = "/auth/login"`), so the
#       fronting must serve `/auth/*`. Without it `/auth/login` answers 404 and
#       login is impossible (measured 2026-09-17: the first cutover shipped
#       exactly that defect).
#
# HOW IT PROVES ITSELF (a control that cannot fail is a formality — GR-12)
#   `--self-test` runs on EVERY invocation, over a scratch copy of the shipped
#   files, in both directions:
#     1. the UNMUTATED copy produces NO finding — a rule that matches everything
#        cannot pass this half;
#     2. one mutation per property must move the verdict, and must do so BY NAME
#        — the property under test is the property that fired. A property with
#        two distinct arms (P3: a surface missing vs. a partial promotion)
#        carries one control per arm, each asserting the arm it must name;
#     3. a mutation that changes no bytes is reported NOOP and fails the gate.
#
# DEGRADE CONTRACT: every property is a text property, so the gate always
# assesses and its exit code is always driven by real assertions. A genuine
# inability to assess (no python3, no scratch dir) is rc 2, CANNOT-ASSESS.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# Usage:
#   bash scripts/check-agentconsole-hosting.sh              # gate (self-test + tree)
#   bash scripts/check-agentconsole-hosting.sh --self-test  # the provocation alone
#   bash scripts/check-agentconsole-hosting.sh --root DIR   # analyse another tree
set -uo pipefail

# The script's OWN tree: the provocation stages from here, so `--root DIR` can
# analyse a DIFFERENT tree while the rules still prove themselves on the shipped
# files. Staging from `--root` would make the provocation fail on every tree it
# is asked to analyse, which is the opposite of its purpose.
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
script_root="$(find_repo_root)"
root="$script_root"
mode="full"
while [ $# -gt 0 ]; do
  case "$1" in
    --self-test) mode="self-test"; shift ;;
    --root)      root="${2:-}"; shift 2 ;;
    --root=*)    root="${1#*=}"; shift ;;
    -h|--help)   sed -n '2,45p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "check-agentconsole-hosting: unknown argument '$1'" >&2; exit 2 ;;
  esac
done
if ! command -v python3 >/dev/null 2>&1; then
  echo "check-agentconsole-hosting: CANNOT-ASSESS — python3 is required" >&2
  exit 2
fi

# --- the analyser: one function, used by both halves of the gate ------------
analyze() { # $1 = tree root; prints one finding per line ("PROP: message")
  python3 - "$1" <<'PY'
import json, re, sys, pathlib
root = pathlib.Path(sys.argv[1])
f = []

def read(rel):
    p = root / rel
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return None

# P1 — image recipe: both runtime deps + the module CMD.
df = read("portal/Dockerfile")
if df is None:
    f.append("P1: portal/Dockerfile is missing")
else:
    for dep in ("PyYAML", "cryptography"):
        if not re.search(r"pip install[^\n]*\b%s\b" % re.escape(dep), df):
            f.append("P1: portal/Dockerfile does not install %s (the boot path needs it)" % dep)
    if 'CMD ["python3", "-m", "portal.server.main"' not in df:
        f.append("P1: portal/Dockerfile CMD is not the portal module invocation")

# P2 — the declared overlay contract.
ov = read("contrib/shared-services/agentconsole.compose.yml")
if ov is None:
    f.append("P2: contrib/shared-services/agentconsole.compose.yml is missing")
else:
    for needle, why in (
        ("container_name: shared-services-agentconsole", "container name"),
        ("${AGENTCONSOLE_PORT:-18286}:8080", "console port publish"),
        ("shared-services-net", "shared-services network"),
        ("restart: unless-stopped", "restart policy"),
        ("/api/healthz", "health probe"),
        ("auth-gate-jwks.json", "JWKS mount"),
        ("AO_FLEET_DIR", "fleet state dir"),
        ("AO_LEDGER_DIR", "control ledger dir"),
    ):
        if needle not in ov:
            f.append("P2: overlay does not declare the %s (%s)" % (why, needle))

# P3 — the three composed surfaces are promoted together (no partial state).
reg = read("infra/feature-flags/registry.yaml")
if reg is None:
    f.append("P3: infra/feature-flags/registry.yaml is missing")
else:
    def default_on(name):
        m = re.search(r"^  %s:\s*$(.*?)(?=^  \S|\Z)" % re.escape(name), reg, re.S | re.M)
        if not m:
            return None
        # The value is a YAML scalar, NOT "the rest of the line". The registry's
        # `default:` lines carry a trailing inline comment — commit 03c16d3d
        # (#1789, 2026-09-21) appended one to all 20 surfaces — and an inline
        # comment is valid YAML, which is how the file's own code-enforced reader
        # reads it (`portal/server/fleet.py`: an explicit `default: on` or boolean
        # True, parsed through a YAML loader). A rule that demands the scalar be
        # the whole rest of the line reads a COMMENTED default as "unregistered";
        # that is how this gate reported the three composed surfaces as missing
        # from the very registry that declares them (issue #1823).
        d = re.search(r"^    default:\s*([^#\s]+)\s*(?:#.*)?$", m.group(1), re.M)
        return None if d is None else d.group(1).strip().strip('"').lower() in ("on", "true")
    composed = {n: default_on(n) for n in ("fleet_projection", "remote_control", "operator_terminal")}
    missing = [n for n, v in composed.items() if v is None]
    if missing:
        f.append("P3: surfaces missing from the registry: %s" % ", ".join(missing))
    elif len(set(composed.values())) != 1:
        on = sorted(n for n, v in composed.items() if v)
        off = sorted(n for n, v in composed.items() if not v)
        f.append("P3: partial promotion — on: %s ; off: %s (the console composes all three)"
                 % (",".join(on) or "none", ",".join(off) or "none"))

# P4 — the hosting declaration names the live host and the retirement.
doc = read("docs/AGENTCONSOLE-HOSTING.md")
if doc is None:
    f.append("P4: docs/AGENTCONSOLE-HOSTING.md is missing")
else:
    low = doc.lower()
    if "shared-services" not in low:
        f.append("P4: hosting doc does not name the shared-services live host")
    if "retired" not in low:
        f.append("P4: hosting doc does not declare the Cloud Run route RETIRED")
    if re.search(r"Status:\s*(?:\*{1,2}\s*)?declaration, not a deployment", doc):
        f.append("P4: hosting doc still claims it is a declaration, not a deployment "
                 "(the surface is live — the declaration must match reality)")

# P5 — the env contract and its fail-closed consequence.
if doc is None:
    pass
else:
    for name in ("PORTAL_AUTH_GATE_JWKS_FILE", "ROOT_ADMIN_EMAILS"):
        if name not in doc:
            f.append("P5: hosting doc does not declare %s" % name)
    if "healthy" not in doc.lower() or "refus" not in doc.lower():
        f.append("P5: hosting doc does not state the fail-closed consequence (healthy ≠ usable)")

# P6 — the cutover recipe declares the ^/auth/ path rule.
rb = read("docs/AGENTCONSOLE-GOLIVE.md")
if rb is None:
    f.append("P6: docs/AGENTCONSOLE-GOLIVE.md (the go-live recipe) is missing")
elif "^/auth/" not in rb:
    f.append("P6: the go-live recipe does not declare the ^/auth/ path rule — the console's "
             "redirect is RELATIVE, so without it /auth/login 404s and login is impossible")

for line in f:
    print(line)
PY
}

selftest() {
  scratch="$(mktemp -d /tmp/ao1029-selftest.XXXXXX)" || { echo "CANNOT-ASSESS: no scratch dir" >&2; return 2; }
  trap 'rm -rf "$scratch"' EXIT
  # stage the files the analyser reads, at their real relative paths
  for rel in portal/Dockerfile contrib/shared-services/agentconsole.compose.yml \
             infra/feature-flags/registry.yaml docs/AGENTCONSOLE-HOSTING.md \
             docs/AGENTCONSOLE-GOLIVE.md; do
    mkdir -p "$scratch/$(dirname "$rel")"
    cp "$script_root/$rel" "$scratch/$rel" 2>/dev/null || true
  done
  fail=0
  base="$(analyze "$scratch" | wc -l | tr -d ' ')"
  if [ "$base" != "0" ]; then
    echo "SELFTEST: the unmutated copy produced $base finding(s) — the rule matches everything:"
    analyze "$scratch"
    return 1
  fi
  echo "SELFTEST: unmutated copy clean"
  # one mutation per property; each must move the verdict BY NAME. A property
  # with two distinct failure arms (P3) carries one control per arm, and passes
  # the reason it must name as $4 — by-name alone cannot tell "missing" from
  # "partial promotion", and a rule that answered one arm for the other would
  # otherwise satisfy both controls.
  mutate() { # $1 = property, $2 = rel file, $3 = python expression on the text
             # $4 = optional substring the by-name line must also carry
    local prop="$1" rel="$2" expr="$3" expect="${4:-}"
    local dir="$scratch.mut"; rm -rf "$dir"; cp -a "$scratch" "$dir"
    python3 - "$dir/$rel" "$expr" <<'PY'
import pathlib, sys
p = pathlib.Path(sys.argv[1]); t = p.read_text()
ns = {"t": t, "re": __import__("re")}
exec("new = " + sys.argv[2], ns)
new = ns.get("new")
if new is None:
    print("mutation expression did not bind 'new'"); sys.exit(4)
if new == t:
    print("NOOP"); sys.exit(3)
p.write_text(new)
PY
    case "$?" in
      3) echo "SELFTEST FAIL: $prop mutation was a NOOP (changed no bytes)"; rm -rf "$dir"; return 1 ;;
      0) : ;;
      *) echo "SELFTEST FAIL: $prop mutation could not be applied"; rm -rf "$dir"; return 1 ;;
    esac
    local out; out="$(analyze "$dir")"; rm -rf "$dir"
    # Line-anchored containment, bash-native: a piped quiet test (`grep -q`) is
    # killed by SIGPIPE on its first match, and with `set -o pipefail` that
    # promotes to the whole pipeline's exit status -- so a report larger than
    # the pipe buffer can report a verdict that IS present as absent (issue
    # #1006). A plain substring test would also accept "x$prop:" or match
    # inside another line, so this keeps the start-of-line anchor.
    local moved=0 line
    while IFS= read -r line; do
      case "$line" in
        # `"$expect"` empty matches every line, so the 3-argument form is
        # unchanged: by-name only.
        "$prop":*) case "$line" in *"$expect"*) moved=1; break ;; esac ;;
      esac
    done <<< "$out"
    if [ "$moved" -eq 0 ]; then
      echo "SELFTEST FAIL: the $prop mutation did not move the verdict${expect:+ naming '$expect'} (got: ${out:-<none>})"
      return 1
    fi
    echo "SELFTEST OK: $prop refused by name"
    return 0
  }
  mutate P1 portal/Dockerfile \
    're.sub(r"pip install([^\n]*)cryptography", "pip install\\1", t)' || fail=1
  mutate P2 contrib/shared-services/agentconsole.compose.yml \
    't.replace("container_name: shared-services-agentconsole", "container_name: wrong")' || fail=1
  # P3 has TWO arms and needs one control each.
  #   1. a PARTIAL promotion: state-aware — it flips ONE of the three to the
  #      opposite of the others, which is a partial promotion whether the
  #      committed state is all on or all off, so the control cannot go stale as
  #      the promotion state moves.
  #   2. a surface GENUINELY ABSENT (renamed): the arm that must NOT be reachable
  #      from a complete registry. It had no control at all, which is precisely
  #      how issue #1823's build lived: the "missing" arm was firing on the
  #      unmutated copy, and nothing in the self-test could tell that from a
  #      correct rule.
  mutate P3 infra/feature-flags/registry.yaml \
    're.sub(r"(  operator_terminal:\n    default: )(on|off)", lambda m: m.group(1) + ("off" if m.group(2) == "on" else "on"), t, count=1)' \
    "partial promotion" || fail=1
  mutate P3 infra/feature-flags/registry.yaml \
    't.replace("  fleet_projection:\n", "  fleet_projection_RENAMED:\n", 1)' \
    "surfaces missing from the registry" || fail=1
  # P4's mutation APPENDS the stale claim rather than substituting a fixed line,
  # so it cannot go NOOP when the status line is reworded.
  mutate P4 docs/AGENTCONSOLE-HOSTING.md \
    't + "\n\n> Status: **declaration, not a deployment**\n"' || fail=1
  mutate P5 docs/AGENTCONSOLE-HOSTING.md \
    't.replace("PORTAL_AUTH_GATE_JWKS_FILE", "SOME_OTHER_VAR")' || fail=1
  # P6: drop the ^/auth/ path rule from the recipe — the exact omission that
  # broke login on the first cutover.
  mutate P6 docs/AGENTCONSOLE-GOLIVE.md \
    't.replace("^/auth/", "/NOTAUTH/")' || fail=1
  [ "$fail" -eq 0 ] && echo "check-agentconsole-hosting: SELFTEST OK" || echo "check-agentconsole-hosting: SELFTEST FAIL"
  return "$fail"
}

if [ "$mode" = "self-test" ]; then
  selftest
  exit $?
fi

# FULL: the provocation runs on EVERY invocation — a gate whose provocation is
# opt-in is a gate that stops proving itself (GR-12).
if ! selftest; then
  echo "check-agentconsole-hosting: FAIL — the self-test did not pass, so this gate proves nothing"
  exit 1
fi

findings="$(analyze "$root")"
if [ -n "$findings" ]; then
  printf '%s\n' "$findings" | sed 's/^/  FAIL  /'
  n="$(printf '%s\n' "$findings" | wc -l | tr -d ' ')"
  echo "check-agentconsole-hosting: FAIL ($n finding(s))"
  exit 1
fi
echo "check-agentconsole-hosting: OK — the hosting contract holds (image recipe, overlay, promoted surfaces, live-host declaration, env contract)"
exit 0
