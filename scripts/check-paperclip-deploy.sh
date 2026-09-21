#!/usr/bin/env bash
# check-paperclip-deploy.sh — the self-hosted paperclip runtime deployment gate
# (issue #411, ADR-0013).
#
# ADR-0013 adopts the upstream Paperclip CLI as an external operator surface
# across a process boundary. Issue #411 stands that process up **beside** the
# control plane as a declaration-only, flag-gated-OFF deployment. A deployment
# that nothing validates is a hope, not a check (no-false-green doctrine,
# GR-12), so this gate fails, BY NAME, when any load-bearing part is missing:
#
#   * the flag   — infra/terraform/variables.tf declares `enable_paperclip`
#                  defaulting to false, and infra/feature-flags/registry.yaml
#                  records services.paperclip defaulting OFF (GR-5);
#   * the pin    — infra/paperclip/release.yaml pins an exact upstream release
#                  (never a floating `latest`) with its release and retrieval
#                  dates and the MIT license (GR-10 provenance);
#   * the probe  — the deploy declaration and the Terraform runtime both probe
#                  `GET /api/health`, and the probe module exists;
#   * no vendoring — nothing from paperclipai/paperclip is copied into this
#                  tree; only the pinned OCI reference and the provenance
#                  record name upstream (GR-10).
#
# The gate never touches the network. It proves the health probe is REAL by
# exercising it offline both ways — a live healthy server passes, an HTTP error
# fails, and an absent process fails — and it runs its own negative control:
# three scratch copies drop the flag, the pin and the health probe in turn, and
# each must be refused BY NAME. A check that cannot fail is a formality.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
# CANNOT-ASSESS must never be reported as a pass.
#
# Usage: bash scripts/check-paperclip-deploy.sh
set -u

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-paperclip-deploy: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi
if [ ! -d "$root/infra/paperclip" ]; then
  echo "check-paperclip-deploy: FAIL — infra/paperclip/ is missing (nothing is declared)" >&2
  exit 1
fi

# validate <root> — 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS. Parameterised by root so
# the negative control can validate a scratch copy.
validate() {
  python3 - "$1" <<'PY'
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    print(f"check-paperclip-deploy: CANNOT-ASSESS - PyYAML not installed ({exc})", file=sys.stderr)
    raise SystemExit(2)

root = Path(sys.argv[1]).resolve()
findings = []


def read(rel):
    path = root / rel
    if not path.is_file():
        findings.append(f"{rel}: missing")
        return None
    return path.read_text(encoding="utf-8")


# --- (1) the flag: enable_paperclip defaults OFF ----------------------------
tf_vars = read("infra/terraform/variables.tf")
if tf_vars is not None:
    block = re.search(r'variable\s+"enable_paperclip"\s*\{(.*?)\n\}', tf_vars, re.S)
    if not block:
        findings.append("flag: infra/terraform/variables.tf declares no `enable_paperclip` variable")
    else:
        default = re.search(r"default\s*=\s*(true|false)", block.group(1))
        if not default:
            findings.append("flag: enable_paperclip has no explicit default (must be false)")
        elif default.group(1) != "false":
            findings.append("flag: enable_paperclip must default to false (flag-gated OFF)")

# --- (1b) the registry row --------------------------------------------------
registry_text = read("infra/feature-flags/registry.yaml")
if registry_text is not None:
    try:
        registry = yaml.safe_load(registry_text)
    except yaml.YAMLError as exc:
        findings.append(f"registry: infra/feature-flags/registry.yaml does not parse ({exc})")
        registry = None
    if isinstance(registry, dict):
        entry = (registry.get("services") or {}).get("paperclip")
        if not isinstance(entry, dict):
            findings.append("registry: services.paperclip is not declared (the flag has no registry row)")
        else:
            if entry.get("default") not in (False, "off"):
                findings.append(f"registry: services.paperclip.default must be off (got {entry.get('default')!r})")
            if entry.get("promoted"):
                findings.append("registry: services.paperclip.promoted must be false while it ships OFF")
            if entry.get("tf_flag") != "enable_paperclip":
                findings.append(f"registry: services.paperclip.tf_flag must be enable_paperclip (got {entry.get('tf_flag')!r})")

# --- (2) the pin: exact tag, provenance, no vendoring declaration -----------
release_text = read("infra/paperclip/release.yaml")
if release_text is not None:
    try:
        release = yaml.safe_load(release_text)
    except yaml.YAMLError as exc:
        findings.append(f"pin: infra/paperclip/release.yaml does not parse ({exc})")
        release = None
    if isinstance(release, dict):
        source = release.get("source") or {}
        pin = release.get("pin") or {}
        vendoring = release.get("vendoring") or {}
        if source.get("repository") != "paperclipai/paperclip":
            findings.append("pin: release.yaml must name the upstream repository paperclipai/paperclip")
        if source.get("license") != "MIT":
            findings.append("pin: release.yaml must record the upstream license (MIT)")
        version = str(pin.get("version") or "")
        if not version:
            findings.append("pin: release.yaml pins no version")
        elif "latest" in version.lower():
            findings.append(f"pin: release.yaml must pin an exact release, never `latest` (got {version!r})")
        if not pin.get("released"):
            findings.append("pin: release.yaml must record the upstream release date")
        if not pin.get("retrieved"):
            findings.append("pin: release.yaml must record the retrieval date")
        if vendoring.get("upstream_source") != "none":
            findings.append("vendoring: release.yaml must declare vendoring.upstream_source: none (GR-10)")

# --- (3) the health probe: declared everywhere, real ------------------------
probe = root / "infra/paperclip/health/healthcheck.py"
if not probe.is_file():
    findings.append("health: infra/paperclip/health/healthcheck.py is missing (no probe to run)")
elif "/api/health" not in probe.read_text(encoding="utf-8"):
    findings.append("health: healthcheck.py does not probe /api/health")

deploy_text = read("infra/paperclip/cloudbuild/deploy.yaml")
if deploy_text is not None:
    if "/api/health" not in deploy_text:
        findings.append("health: deploy.yaml declares no GET /api/health probe step")
    if "healthcheck.py" not in deploy_text:
        findings.append("health: deploy.yaml does not run the health probe module")

trigger_text = read("infra/paperclip/cloudbuild/deploy-trigger.yaml")
if trigger_text is not None:
    try:
        trigger = yaml.safe_load(trigger_text)
    except yaml.YAMLError as exc:
        findings.append(f"enable: deploy-trigger.yaml does not parse ({exc})")
        trigger = None
    if isinstance(trigger, dict):
        if trigger.get("disabled") is not True:
            findings.append("enable: deploy-trigger.yaml must ship disabled: true (flag-gated OFF, GR-5)")
        substitutions = trigger.get("substitutions") or {}
        if substitutions.get("_ENABLE_PAPERCLIP") != "false":
            findings.append('enable: deploy-trigger.yaml substitution _ENABLE_PAPERCLIP must be "false"')

tf_main = read("infra/paperclip/terraform/main.tf")
if tf_main is not None:
    if "/api/health" not in tf_main:
        findings.append("health: the terraform runtime declares no /api/health probe")
    if "probe" not in tf_main:
        findings.append("health: the terraform runtime declares no startup/liveness probe")

# --- (4) no vendoring (GR-10) -----------------------------------------------
for forbidden in ("paperclipai", "vendor/paperclip", "paperclip/upstream",
                  "infra/paperclip/upstream", "infra/paperclip/vendor",
                  "third_party/paperclip"):
    if (root / forbidden).exists():
        findings.append(f"vendoring: {forbidden} must not exist — upstream source never lands in this tree (GR-10)")

paperclip_dir = root / "infra/paperclip"
if paperclip_dir.is_dir():
    code_suffixes = {".py", ".ts", ".js", ".tsx", ".go", ".rs", ".sh", ".rb", ".java"}
    for path in sorted(paperclip_dir.rglob("*")):
        if not path.is_file() or path.suffix not in code_suffixes:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for match in re.finditer(r"paperclipai/paperclip", text):
            if not text[max(0, match.start() - 8):match.start()].endswith("ghcr.io/"):
                findings.append(
                    f"vendoring: {path.relative_to(root)} references upstream source (GR-10)"
                )

if findings:
    for finding in findings:
        print(f"  FAIL  {finding}", file=sys.stderr)
    raise SystemExit(1)
print("  OK    flag (enable_paperclip=false), pin (exact tag), health probe and no-vendoring assertions all hold")
raise SystemExit(0)
PY
}

rc=0
validate "$root" || rc=$?
case "$rc" in
  0) : ;;
  1) echo "check-paperclip-deploy: FAIL — the deployment declaration is incomplete (see findings above)" >&2; exit 1 ;;
  *) echo "check-paperclip-deploy: CANNOT-ASSESS — validator returned $rc" >&2; exit 2 ;;
esac

# --- the health probe is REAL: exercise it offline, both ways ---------------
selftest() {
  python3 - "$root" <<'PY'
import http.server
import importlib.util
import sys
import threading
from pathlib import Path

root = Path(sys.argv[1]).resolve()
module_path = root / "infra/paperclip/health/healthcheck.py"
spec = importlib.util.spec_from_file_location("paperclip_healthcheck", module_path)
health = importlib.util.module_from_spec(spec)
spec.loader.exec_module(health)

failures = []


class Handler(http.server.BaseHTTPRequestHandler):
    status = 200
    body = b'{"status":"ok"}'

    def do_GET(self):  # noqa: N802 - stdlib interface
        self.send_response(self.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(self.body)))
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, *args):  # silence the test server
        return


def serve(status, body):
    Handler.status = status
    Handler.body = body
    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


# (a) a live, healthy runtime passes.
server, base = serve(200, b'{"status":"ok"}')
try:
    code, message = health.probe(base, "/api/health", 2.0)
finally:
    server.shutdown()
if code == 0:
    print(f"  OK    health probe passes against a live healthy runtime ({message})")
else:
    failures.append(f"a live healthy server was not reported healthy (rc={code}: {message})")

# (b) an unhealthy runtime (HTTP 500) fails, naming the reason.
server, base = serve(500, b'{"status":"down"}')
try:
    code, message = health.probe(base, "/api/health", 2.0)
finally:
    server.shutdown()
if code != 0 and "unhealthy" in message.lower():
    print(f"  OK    health probe fails on an unhealthy runtime ({message})")
else:
    failures.append(f"an unhealthy (HTTP 500) runtime was not refused by name (rc={code}: {message})")

# (c) an absent process fails, naming the reason.
code, message = health.probe("http://127.0.0.1:1", "/api/health", 1.0)
if code != 0 and "unreachable" in message.lower():
    print(f"  OK    health probe fails when the process is absent ({message})")
else:
    failures.append(f"an absent process was not refused by name (rc={code}: {message})")

if failures:
    for failure in failures:
        print(f"  FAIL  {failure}", file=sys.stderr)
    raise SystemExit(1)
raise SystemExit(0)
PY
}

if ! selftest; then
  echo "check-paperclip-deploy: FAIL — the health probe is not a real check (see findings above)" >&2
  exit 1
fi

# --- negative control: each provocation must be refused BY NAME -------------
if ! command -v sha256sum >/dev/null 2>&1; then
  echo "check-paperclip-deploy: CANNOT-ASSESS — sha256sum not found (cannot prove the mutation)" >&2
  exit 2
fi

scratch="/tmp/ao411-deploy.$(date +%s%N).$"
if ! mkdir "$scratch" 2>/dev/null; then
  echo "check-paperclip-deploy: CANNOT-ASSESS — cannot create a scratch directory" >&2
  exit 2
fi
trap 'rm -rf "$scratch"' EXIT

files=(
  infra/terraform/variables.tf
  infra/feature-flags/registry.yaml
  infra/paperclip/release.yaml
  infra/paperclip/cloudbuild/deploy.yaml
  infra/paperclip/cloudbuild/deploy-trigger.yaml
  infra/paperclip/terraform/main.tf
  infra/paperclip/health/healthcheck.py
)

copy_tree() {
  local src="$1" dst="$2" rel
  for rel in "${files[@]}"; do
    mkdir -p "$dst/$(dirname "$rel")"
    cp "$src/$rel" "$dst/$rel"
  done
}

baseline_shas="$(cd "$root" && sha256sum "${files[@]}")"

mutate() {
  # mutate <root> <flag|pin|health>
  python3 - "$1" "$2" <<'PY'
import re
import sys
from pathlib import Path

root = Path(sys.argv[1])
tag = sys.argv[2]

if tag == "flag":
    path = root / "infra/terraform/variables.tf"
    text = path.read_text(encoding="utf-8")
    mutated = re.sub(r'variable\s+"enable_paperclip"\s*\{.*?\n\}\n*', "", text, count=1, flags=re.S)
    if mutated == text:
        print("mutation 'flag' changed nothing", file=sys.stderr)
        raise SystemExit(2)
    path.write_text(mutated, encoding="utf-8")
elif tag == "pin":
    path = root / "infra/paperclip/release.yaml"
    text = path.read_text(encoding="utf-8")
    mutated = text.replace("version: v2026.831.1", "version: latest", 1)
    if mutated == text:
        print("mutation 'pin' changed nothing", file=sys.stderr)
        raise SystemExit(2)
    path.write_text(mutated, encoding="utf-8")
elif tag == "health":
    for rel in ("infra/paperclip/cloudbuild/deploy.yaml", "infra/paperclip/terraform/main.tf"):
        path = root / rel
        text = path.read_text(encoding="utf-8")
        mutated = text.replace("/api/health", "/api/ping")
        if mutated == text:
            print(f"mutation 'health' changed nothing in {rel}", file=sys.stderr)
            raise SystemExit(2)
        path.write_text(mutated, encoding="utf-8")
else:
    print(f"unknown mutation tag {tag!r}", file=sys.stderr)
    raise SystemExit(2)
raise SystemExit(0)
PY
}

control() {
  # control <tag> <keyword-that-must-appear-in-the-refusal>
  local tag="$1" keyword="$2"
  local dir="$scratch/$tag"
  mkdir -p "$dir"
  copy_tree "$root" "$dir"
  if ! mutate "$dir" "$tag"; then
    echo "check-paperclip-deploy: CANNOT-ASSESS — could not build the '$tag' mutant" >&2
    return 2
  fi
  local mut_sha orig_sha
  mut_sha="$(cd "$dir" && sha256sum "${files[@]}" | sha256sum | awk '{print $1}')"
  orig_sha="$(printf '%s\n' "$baseline_shas" | sha256sum | awk '{print $1}')"
  if [ "$mut_sha" = "$orig_sha" ]; then
    echo "check-paperclip-deploy: CANNOT-ASSESS — the '$tag' mutant is byte-identical to the baseline" >&2
    return 2
  fi

  local out rc
  out="$(validate "$dir" 2>&1)"
  rc=$?
  if [ "$rc" -eq 1 ] && printf '%s\n' "$out" | grep -qF -- "$keyword"; then
    echo "  OK    negative control ($tag): refused by name — '$(printf '%s\n' "$out" | grep -F -- "$keyword" | head -1 | sed 's/^ *//')'"
    return 0
  fi
  echo "check-paperclip-deploy: FAIL — negative control ($tag) passed; removing it was not caught (the gate cannot fail)" >&2
  printf '%s\n' "$out" >&2
  return 1
}

control_ok=0
control "flag" "enable_paperclip" || control_ok=1
control "pin" "latest" || control_ok=1
control "health" "api/health" || control_ok=1
if [ "$control_ok" -ne 0 ]; then
  exit 1
fi

# The control works on copies; the tree must be untouched — originals byte-identical.
after_shas="$(cd "$root" && sha256sum "${files[@]}")"
if [ "$baseline_shas" != "$after_shas" ]; then
  echo "check-paperclip-deploy: FAIL — the negative control mutated the working tree" >&2
  exit 1
fi
echo "  OK    working tree byte-identical after the control (sha256 ${after_shas%% *})"

echo "check-paperclip-deploy: OK — flag, pin, health probe and no-vendoring all hold;"
echo "  the probe is real (live-pass / HTTP-500-fail / absent-fail) and its own"
echo "  mutants (dropped flag, floating pin, removed health probe) are refused by name."
exit 0
