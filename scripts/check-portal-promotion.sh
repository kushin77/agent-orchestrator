#!/usr/bin/env bash
# check-portal-promotion.sh — the portal-promotion rung's contract cannot
# regress silently (issue #1329, parent #1295).
#
# WHAT IS MEASURED
#   P1  the rung is DECLARED — fleet/cron.py names a promote marker/schedule
#       and a `promote` subcommand exists.
#   P2  the compose overlay carries NO unconditional `build:` fallback on the
#       production `agentconsole` service (contrib/shared-services/
#       agentconsole.compose.yml) — the exact defect that let a host-built
#       image serve for a day of merged commits.
#   P3  fixture dry-run: `infra/fleet/promote_portal.py`'s pure planner
#       (`select_newest_master_tag` / `run_cycle`) picks the newest
#       master-reachable tag, refuses `tag-not-on-master` by name, refuses
#       CANNOT-ASSESS `ar-auth-missing` when no credential is configured, and
#       rolls back + records `rolled_back` on a failed healthz.
#
# HOW IT PROVES ITSELF (GR-12): `--self-test` runs on every invocation and
# mutates the live tree/module to prove each property CAN fail:
#   - drop the rollback branch -> the rolled-back scenario must be refused
#     by name;
#   - drop the ancestry check (always treat every tag as an ancestor) -> the
#     tag-not-on-master scenario must stop being refused.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# Usage:
#   bash scripts/check-portal-promotion.sh              # gate (self-test + tree)
#   bash scripts/check-portal-promotion.sh --self-test   # the provocation alone
#   bash scripts/check-portal-promotion.sh --root DIR    # analyse another tree
set -uo pipefail

script_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
root="$script_root"
mode="full"
while [ $# -gt 0 ]; do
  case "$1" in
    --self-test) mode="self-test"; shift ;;
    --root)      root="${2:-}"; shift 2 ;;
    --root=*)    root="${1#*=}"; shift ;;
    -h|--help)   sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "check-portal-promotion: unknown argument '$1'" >&2; exit 2 ;;
  esac
done
if ! command -v python3 >/dev/null 2>&1; then
  echo "check-portal-promotion: CANNOT-ASSESS — python3 is required" >&2
  exit 2
fi

# --- P1/P2: static text properties over the given tree ----------------------
analyze_static() { # $1 = tree root; prints one finding per line ("P1: ...")
  python3 - "$1" <<'PY'
import re, sys, pathlib
root = pathlib.Path(sys.argv[1])
f = []

def read(rel):
    try:
        return (root / rel).read_text(encoding="utf-8")
    except OSError:
        return None

cron = read("fleet/cron.py")
if cron is None:
    f.append("P1: fleet/cron.py is missing")
else:
    if "PROMOTE_MARKER" not in cron or "ao-fleet-promote-portal" not in cron:
        f.append("P1: fleet/cron.py does not declare a promote-portal marker")
    if "PROMOTE_SCHEDULE" not in cron:
        f.append("P1: fleet/cron.py does not declare a promote-portal schedule")
    if not re.search(r'sub\.add_parser\(\s*"promote"', cron):
        f.append("P1: fleet/cron.py does not expose a `promote` subcommand")

ov = read("contrib/shared-services/agentconsole.compose.yml")
if ov is None:
    f.append("P2: contrib/shared-services/agentconsole.compose.yml is missing")
else:
    m = re.search(r"^  agentconsole:\n(?:.*\n)*?(?=^  \S|\Z)", ov, re.M)
    block = m.group(0) if m else ov
    if re.search(r"^\s*build:\s*$", block, re.M) and "AGENTCONSOLE_IMAGE:?" not in block:
        f.append("P2: the agentconsole service still carries an unconditional build: fallback")

pp = read("infra/fleet/promote_portal.py")
if pp is None:
    f.append("P3: infra/fleet/promote_portal.py is missing")

for line in f:
    print(line)
PY
}

# --- P3: fixture dry-run over the promote_portal module ----------------------
analyze_dynamic() { # $1 = tree root; prints one finding per line ("P3: ...")
  PYTHONPATH="$1/infra/fleet" python3 - "$1" <<'PY'
import sys, importlib
root = sys.argv[1]
sys.path.insert(0, root + "/infra/fleet")
findings = []
try:
    if "promote_portal" in sys.modules:
        del sys.modules["promote_portal"]
    pp = importlib.import_module("promote_portal")
except Exception as exc:  # noqa: BLE001 — any import failure is this property's own finding
    print(f"P3: promote_portal.py failed to import: {exc}")
    sys.exit(0)


def tag(t, s):
    return pp.TagRef(tag=t, sha=s)


class Rec:
    def __init__(self):
        self.records, self.statuses, self.escalations, self.deploys = [], [], [], []
        self.park = None

    def record(self, r):
        self.records.append(r)

    def post_status(self, sha, rc):
        self.statuses.append((sha, rc))

    def escalate(self, code, detail):
        self.escalations.append((code, detail))

    def deploy(self, ref):
        self.deploys.append(ref)

    def read_park(self):
        return self.park

    def write_park(self, t):
        self.park = t


def base(rec, tags, running, healthy, is_ancestor=None, auth_ok=True):
    return dict(
        auth_ok=auth_ok,
        list_tags=lambda: tags,
        is_ancestor=(is_ancestor if is_ancestor is not None else (lambda sha: True)),
        commit_index=lambda sha: {"a": 1, "b": 2}.get(sha, 0),
        running_ref=lambda: running,
        deploy=rec.deploy,
        healthz=lambda: healthy,
        escalate=rec.escalate,
        record=rec.record,
        post_status=rec.post_status,
        read_park=rec.read_park,
        write_park=rec.write_park,
        now=lambda: "2026-01-01T00:00:00Z",
    )

# scenario 1: ar-auth-missing
rec = Rec()
result = pp.run_cycle(**base(rec, [tag("t1", "a")], None, True, auth_ok=False))
if result.get("code") != "ar-auth-missing" or pp.rc_for(result) != pp.CANNOT_ASSESS:
    findings.append(f"P3: ar-auth-missing scenario did not refuse by name (got {result})")

# scenario 2: tag-not-on-master
rec = Rec()
result = pp.run_cycle(**base(rec, [tag("t1", "a")], None, True, is_ancestor=lambda sha: False))
if result.get("code") != "tag-not-on-master":
    findings.append(f"P3: tag-not-on-master scenario did not refuse by name (got {result})")

# scenario 3: rollback on failed healthz
rec = Rec()
result = pp.run_cycle(**base(rec, [tag("t1", "a")], "old-ref", False))
if result.get("action") != "rolled_back" or "old-ref" not in rec.deploys:
    findings.append(f"P3: failed-healthz scenario did not roll back (got {result}, deploys={rec.deploys})")
if len(rec.escalations) != 1:
    findings.append(f"P3: failed-healthz scenario did not escalate exactly once (got {rec.escalations})")

for finding in findings:
    print(finding)
PY
}

selftest() {
  scratch="$(mktemp -d /tmp/ao1329-selftest.XXXXXX)" || { echo "CANNOT-ASSESS: no scratch dir" >&2; return 2; }
  trap 'rm -rf "$scratch"' EXIT
  for rel in fleet/cron.py contrib/shared-services/agentconsole.compose.yml infra/fleet/promote_portal.py; do
    mkdir -p "$scratch/$(dirname "$rel")"
    cp "$script_root/$rel" "$scratch/$rel" 2>/dev/null || true
  done
  fail=0
  base_findings="$( { analyze_static "$scratch"; analyze_dynamic "$scratch"; } )"
  base_count="$(printf '%s' "$base_findings" | grep -c . || true)"
  if [ "$base_count" != "0" ]; then
    echo "SELFTEST: the unmutated copy produced $base_count finding(s):"
    printf '%s\n' "$base_findings"
    return 1
  fi
  echo "SELFTEST: unmutated copy clean"

  # Mutant A: drop the rollback branch from promote_portal.py -> the rolled-back
  # scenario must stop matching and be refused BY NAME.
  dir="$scratch.mutA"; rm -rf "$dir"; cp -a "$scratch" "$dir"
  python3 - "$dir/infra/fleet/promote_portal.py" <<'PY'
import pathlib, sys
p = pathlib.Path(sys.argv[1])
t = p.read_text()
needle = "    if previous:\n        deploy(previous)\n"
if needle not in t:
    print("mutation site not found"); sys.exit(4)
p.write_text(t.replace(needle, "    # mutated: rollback removed\n"))
PY
  case "$?" in
    4) echo "SELFTEST FAIL: mutant-A site not found (rollback branch text changed)"; fail=1 ;;
    0)
      out="$(analyze_dynamic "$dir")"
      if [[ "$out" == *"P3: failed-healthz scenario did not roll back"* ]]; then
        echo "SELFTEST OK: dropping rollback is refused by name"
      else
        echo "SELFTEST FAIL: dropping rollback was NOT caught (got: ${out:-<none>})"
        fail=1
      fi
      ;;
    *) echo "SELFTEST FAIL: mutant-A could not be applied"; fail=1 ;;
  esac
  rm -rf "$dir"

  # Mutant B: drop the ancestry check (treat everything as an ancestor) -> the
  # tag-not-on-master scenario must stop being refused.
  dir="$scratch.mutB"; rm -rf "$dir"; cp -a "$scratch" "$dir"
  python3 - "$dir/infra/fleet/promote_portal.py" <<'PY'
import pathlib, sys
p = pathlib.Path(sys.argv[1])
t = p.read_text()
needle = "    ancestors = [t for t in candidates if is_ancestor(t.sha)]"
if needle not in t:
    print("mutation site not found"); sys.exit(4)
p.write_text(t.replace(needle, "    ancestors = list(candidates)  # mutated: ancestry check removed"))
PY
  case "$?" in
    4) echo "SELFTEST FAIL: mutant-B site not found (ancestry-check text changed)"; fail=1 ;;
    0)
      out="$(analyze_dynamic "$dir")"
      if [[ "$out" == *"P3: tag-not-on-master scenario did not refuse by name"* ]]; then
        echo "SELFTEST OK: dropping the ancestry check is refused by name"
      else
        echo "SELFTEST FAIL: dropping the ancestry check was NOT caught (got: ${out:-<none>})"
        fail=1
      fi
      ;;
    *) echo "SELFTEST FAIL: mutant-B could not be applied"; fail=1 ;;
  esac
  rm -rf "$dir"

  [ "$fail" -eq 0 ] && echo "check-portal-promotion: SELFTEST OK" || echo "check-portal-promotion: SELFTEST FAIL"
  return "$fail"
}

if [ "$mode" = "self-test" ]; then
  selftest
  exit $?
fi

if ! selftest; then
  echo "check-portal-promotion: FAIL — the self-test did not pass, so this gate proves nothing"
  exit 1
fi

findings="$( { analyze_static "$root"; analyze_dynamic "$root"; } )"
if [ -n "$findings" ]; then
  printf '%s\n' "$findings" | sed 's/^/  FAIL  /'
  n="$(printf '%s\n' "$findings" | grep -c .)"
  echo "check-portal-promotion: FAIL ($n finding(s))"
  exit 1
fi
echo "check-portal-promotion: OK — the promotion rung is declared, the compose overlay has no unconditional build fallback, and the fixture scenarios (auth-missing, tag-not-on-master, rollback) behave"
exit 0
