#!/usr/bin/env bash
# check-module-admission.sh — parent-side sub-module admission gate (issue #423).
#
# Validates the parent-side declaration in the root `module.json` against the
# published contract in `docs/MODULE-ADMISSION.md`, so the sub-module register is
# a gated fact rather than a trust statement. It edits no peer repository and
# files nothing on a peer board (NG4) — the child-side declaration is the
# child's own issue.
#
# Modes:
#   (default)          offline + deterministic. Validates schema, shape, the
#                      three-state admission rule, reference integrity, the
#                      dependency pins and the recorded peer facts, then proves
#                      its own negative controls (a gate that cannot fail cannot
#                      pass, AO-GR-4).
#   --verify-peers     live: `gh api` each peer repo and its module.json. With
#                      --peer-facts FILE the same code path runs offline from a
#                      recorded facts file, so the refusal is reproducible.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS. CANNOT-ASSESS never
# exits 0, and a missing or unavailable peer source is never a PASS.
#
# Usage:
#   bash scripts/check-module-admission.sh
#   bash scripts/check-module-admission.sh --verify-peers
#   bash scripts/check-module-admission.sh --verify-peers --peer-facts FACTS.json
set -u

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"
cd "$root" || exit 2

mode="declared"
manifest="module.json"
doc="docs/MODULE-ADMISSION.md"
facts=""
controls=1

while [ "$#" -gt 0 ]; do
  case "$1" in
    --verify-peers) mode="peers"; shift ;;
    --peer-facts) facts="${2:-}"; shift 2 ;;
    --manifest) manifest="${2:-}"; shift 2 ;;
    --doc) doc="${2:-}"; shift 2 ;;
    --no-controls) controls=0; shift ;;
    -h|--help) sed -n '2,23p' "$0"; exit 0 ;;
    *) printf 'check-module-admission: unknown argument: %s\n' "$1" >&2; exit 2 ;;
  esac
done

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-module-admission: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

run_check() {
  # $1 manifest  $2 doc  $3 mode  $4 facts path ("" in declared mode)
  python3 - "$1" "$2" "$3" "$4" <<'PY'
import base64
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

FLEET_REPO = re.compile(r"^kushin77/[A-Za-z0-9][A-Za-z0-9._-]*$")
ADMISSIONS = ("declared", "requested", "independent")


def cannot(msg):
    sys.stderr.write("module-admission: CANNOT-ASSESS — %s\n" % msg)
    sys.exit(2)


def load_json(path, label):
    p = Path(path)
    if not p.is_file():
        cannot("%s not found: %s" % (label, path))
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        cannot("%s unreadable: %s: %s" % (label, path, exc))


def gh_api(args):
    return subprocess.run(["gh", "api"] + args, capture_output=True, text=True)


def is_404(proc):
    return "404" in (proc.stderr or "") or "Not Found" in (proc.stderr or "")


def gather_live_facts(repos, unavailable):
    if shutil.which("gh") is None:
        cannot("gh is not installed; the live peer check needs gh")
    facts = {}
    for repo in repos:
        fact = {}
        head = gh_api(["repos/%s" % repo, "--jq", ".full_name"])
        if head.returncode == 0 and head.stdout.strip():
            fact["repo_exists"] = True
        elif is_404(head):
            facts[repo] = {"repo_exists": False, "module_json": False, "parent": None}
            continue
        else:
            unavailable.append("%s: gh api repos/%s failed: %s"
                               % (repo, repo, (head.stderr or "").strip()[:200]))
            continue
        tree = gh_api(["repos/%s/contents/module.json" % repo, "--jq", ".content"])
        if tree.returncode == 0 and tree.stdout.strip():
            try:
                blob = json.loads(base64.b64decode(tree.stdout.strip()).decode("utf-8"))
            except Exception as exc:  # noqa: BLE001 - any decode error is unknowable
                unavailable.append("%s: peer module.json undecodable: %s" % (repo, exc))
                continue
            fact["module_json"] = True
            fact["parent"] = blob.get("parent")
        elif is_404(tree):
            fact["module_json"] = False
            fact["parent"] = None
        else:
            unavailable.append("%s: gh api module.json failed: %s"
                               % (repo, (tree.stderr or "").strip()[:200]))
            continue
        facts[repo] = fact
    return facts


def main():
    manifest_path, doc_path, mode, facts_path = sys.argv[1:5]
    manifest = load_json(manifest_path, "manifest")

    doc_file = Path(doc_path)
    if not doc_file.is_file():
        cannot("contract doc not found: %s" % doc_path)
    try:
        doc = doc_file.read_text(encoding="utf-8")
    except OSError as exc:
        cannot("contract doc unreadable: %s: %s" % (doc_path, exc))

    self_repo = (manifest.get("source") or {}).get("repo")
    findings = []
    unknown = []
    pending = []
    counts = {}

    parent = manifest.get("parent")
    if not isinstance(parent, dict):
        findings.append("parent: the parent-side declaration block is missing")
    else:
        if parent.get("schema") != "cmr.module.admission/v1":
            findings.append("parent: schema must be cmr.module.admission/v1 (got %r)"
                            % parent.get("schema"))
        for key in ("module_id", "repo", "uplink", "contract"):
            if not parent.get(key):
                findings.append("parent: missing the required field %r" % key)
        if parent.get("contract") and parent.get("contract") != doc_path:
            findings.append("parent: contract %r does not name %r"
                            % (parent.get("contract"), doc_path))
        if parent.get("uplink") and not FLEET_REPO.match(str(parent.get("uplink"))):
            findings.append("parent: uplink %r is not a fleet repo reference"
                            % parent.get("uplink"))

    register = {}
    subs = manifest.get("submodules")
    if not isinstance(subs, list):
        findings.append("submodules: the sub-module register is missing")
    else:
        for entry in subs:
            if not isinstance(entry, dict):
                findings.append("submodules: a register entry is not an object")
                continue
            mid = entry.get("id")
            label = mid if isinstance(mid, str) and mid else "(unnamed entry)"
            repo = entry.get("repo")
            if not isinstance(mid, str) or not mid:
                findings.append("submodules: a register entry has no id")
            elif mid in register:
                findings.append("%s: duplicate sub-module id in the register" % label)
            if not isinstance(repo, str) or not FLEET_REPO.match(repo):
                findings.append("%s: repo %r is not a fleet repo reference" % (label, repo))
            elif isinstance(mid, str) and mid and not repo.endswith("/" + mid):
                findings.append("%s: id does not match repo %r" % (label, repo))
            admission = entry.get("admission")
            if admission == "undeclared" or admission is None:
                findings.append("%s: undeclared — a register entry with no declaration "
                                "(admission=%r)" % (label, admission))
            elif admission not in ADMISSIONS:
                findings.append("%s: unknown admission state %r" % (label, admission))
            request = entry.get("request")
            if not isinstance(request, str):
                findings.append("%s: the declaration request reference is missing" % label)
            elif isinstance(repo, str) and not re.fullmatch(re.escape(repo) + r"#[0-9]+", request):
                findings.append("%s: declaration request %r is not filed on the peer's "
                                "own board" % (label, request))
            if admission in ADMISSIONS:
                for fact in ("repo_exists", "peer_manifest", "peer_parent"):
                    if fact not in entry:
                        unknown.append("%s: peer fact %r is absent" % (label, fact))
                if not entry.get("evidence"):
                    findings.append("%s: no measured evidence recorded" % label)
                counts[admission] = counts.get(admission, 0) + 1
            if entry.get("repo_exists") is False:
                findings.append("%s: names a repo that does not exist (%s)" % (label, repo))
            if admission == "declared":
                if entry.get("peer_manifest") is not True:
                    findings.append("%s: declared sub-module has no module.json on the "
                                    "peer side (peer_manifest=%r)"
                                    % (label, entry.get("peer_manifest")))
                if entry.get("peer_parent") != self_repo:
                    findings.append("%s: declared sub-module's peer manifest does not "
                                    "declare parent %r" % (label, self_repo))
                if not entry.get("module_version"):
                    findings.append("%s: declared sub-module records no module_version "
                                    "to pin" % label)
            if admission == "requested":
                pending.append(label)
                if entry.get("peer_parent") == self_repo:
                    findings.append("%s: peer manifest now declares this parent but the "
                                    "register still reads requested (promote it)" % label)
            if admission == "independent":
                if not entry.get("rationale"):
                    findings.append("%s: independent answer records no rationale "
                                    "reference" % label)
                if entry.get("peer_parent") == self_repo:
                    findings.append("%s: peer manifest declares this parent yet the "
                                    "register answers independent" % label)
            if isinstance(repo, str) and repo not in doc:
                findings.append("%s: repo %s is not published in %s" % (label, repo, doc_path))
            if isinstance(mid, str) and mid and mid not in register:
                register[mid] = entry

    admitted = [mid for mid, entry in register.items() if entry.get("admission") == "declared"]
    deps = manifest.get("dependencies")
    pinned = {}
    if not isinstance(deps, list):
        findings.append("dependencies: the dependency list is missing")
    else:
        for dep in deps:
            if not isinstance(dep, dict):
                findings.append("dependencies: a dependency entry is not an object")
                continue
            did = dep.get("module")
            label = did if isinstance(did, str) and did else "(unnamed dependency)"
            target = register.get(did) if isinstance(did, str) else None
            if target is None:
                findings.append("%s: dependency names a module outside the sub-module "
                                "register" % label)
                continue
            if target.get("admission") != "declared":
                findings.append("%s: dependency names a module that is not an admitted "
                                "sub-module" % label)
            pin = dep.get("pin")
            want = target.get("module_version")
            if not isinstance(pin, str) or not pin or pin != want:
                findings.append("%s: dependency pin %r does not resolve to the declared "
                                "version %r" % (label, pin, want))
            pinned[did] = pin
    for mid in admitted:
        if mid not in pinned:
            findings.append("%s: admitted sub-module carries no dependency pin" % mid)

    if mode == "peers":
        if facts_path:
            facts = load_json(facts_path, "peer facts")
        else:
            repos = [e.get("repo") for e in register.values() if isinstance(e.get("repo"), str)]
            facts = gather_live_facts(repos, unknown)
        for mid, entry in register.items():
            repo = entry.get("repo")
            fact = facts.get(repo) if isinstance(facts, dict) else None
            if not isinstance(fact, dict):
                unknown.append("%s: no peer facts recorded for %s" % (mid, repo))
                continue
            if fact.get("repo_exists") is False:
                findings.append("%s: repo %s does not exist on the peer side" % (mid, repo))
                continue
            has_manifest = fact.get("module_json") is True
            peer_parent = fact.get("parent")
            admission = entry.get("admission")
            if admission == "declared" and not has_manifest:
                findings.append("%s: declared sub-module has no module.json on the peer "
                                "side (%s)" % (mid, repo))
            elif admission == "declared" and peer_parent != self_repo:
                findings.append("%s: peer manifest does not declare parent %r"
                                % (mid, self_repo))
            elif admission == "requested" and has_manifest and peer_parent == self_repo:
                findings.append("%s: peer manifest now declares this parent — the "
                                "register still reads requested" % mid)
            elif admission == "independent" and peer_parent == self_repo:
                findings.append("%s: peer manifest declares this parent — the register "
                                "answers independent" % mid)

    if unknown:
        for item in unknown:
            sys.stderr.write("  CANNOT-ASSESS %s\n" % item)
        sys.stderr.write("module-admission: CANNOT-ASSESS — %d unavailable fact(s)\n"
                         % len(unknown))
        return 2
    if findings:
        for item in findings:
            sys.stderr.write("  FAIL  %s\n" % item)
        sys.stderr.write("module-admission: NOT-OK — %d finding(s)\n" % len(findings))
        return 1
    sys.stdout.write("  OK    %d sub-module(s): %d declared, %d requested, %d independent, "
                     "0 undeclared\n"
                     % (len(register), counts.get("declared", 0), counts.get("requested", 0),
                        counts.get("independent", 0)))
    if pending:
        sys.stdout.write("  NOTE  awaiting the peer-side module.json: %s\n"
                         % ", ".join(sorted(pending)))
    sys.stdout.write("module-admission: OK\n")
    return 0


sys.exit(main())
PY
}

# --- 1. the real check --------------------------------------------------------
run_check "$manifest" "$doc" "$mode" "$facts"
rc=$?
if [ "$rc" -ne 0 ]; then
  exit "$rc"
fi

# --- 2. provoked negative controls (default invocation only) -----------------
if [ "$controls" -eq 0 ] || [ "$mode" != "declared" ] || [ "$manifest" != "module.json" ]; then
  exit 0
fi

scratch="/tmp/module-admission.$(date +%s%N).$$"
if ! mkdir -p "$scratch" 2>/dev/null; then
  echo "check-module-admission: CANNOT-ASSESS — cannot create a scratch directory" >&2
  exit 2
fi
trap 'rm -rf "$scratch"' EXIT

if ! python3 - "$manifest" "$scratch" <<'PY'
import copy
import json
import pathlib
import sys

manifest_path, scratch = sys.argv[1:3]
base = json.loads(pathlib.Path(manifest_path).read_text(encoding="utf-8"))
scratch = pathlib.Path(scratch)
self_repo = (base.get("source") or {}).get("repo")


def dump(name, obj):
    (scratch / name).write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")


def entry(obj, mid):
    for item in obj["submodules"]:
        if item.get("id") == mid:
            return item
    raise SystemExit("control setup: %s is not in the register" % mid)


# (a) an undeclared register entry.
a = copy.deepcopy(base)
entry(a, "hermes-agents")["admission"] = "undeclared"
dump("mut-undeclared.json", a)

# (b) a declared sub-module whose recorded peer facts say there is no module.json.
b = copy.deepcopy(base)
e = entry(b, "hermes-agents")
e["admission"] = "declared"
e["peer_parent"] = self_repo
e["module_version"] = "v0.1.0"
e["peer_manifest"] = False
b["dependencies"] = [{"module": "hermes-agents", "kind": "contract", "pin": "v0.1.0"}]
dump("mut-no-peer-manifest.json", b)

# (b2) the same declaration, recorded consistently, caught by the peer-side check.
b2 = copy.deepcopy(base)
e = entry(b2, "hermes-agents")
e["admission"] = "declared"
e["peer_parent"] = self_repo
e["module_version"] = "v0.1.0"
e["peer_manifest"] = True
b2["dependencies"] = [{"module": "hermes-agents", "kind": "contract", "pin": "v0.1.0"}]
dump("mut-declared.json", b2)

# (c) a dependency pin that does not resolve.
c = copy.deepcopy(base)
e = entry(c, "deepseek")
e["admission"] = "declared"
e["peer_parent"] = self_repo
e["peer_manifest"] = True
e["module_version"] = "v0.1.0"
c["dependencies"] = [{"module": "deepseek", "kind": "contract", "pin": "v9.9.9"}]
dump("mut-bad-pin.json", c)

# (d) a register entry naming a repo that does not exist.
d = copy.deepcopy(base)
entry(d, "ollama")["repo_exists"] = False
dump("mut-no-repo.json", d)

# (e) a peer fact removed -> CANNOT-ASSESS, never a pass.
f = copy.deepcopy(base)
entry(f, "deepseek").pop("peer_manifest", None)
dump("mut-missing-fact.json", f)

# (f) peer-side facts for the declared child: the peer publishes no module.json.
facts = {}
for item in base["submodules"]:
    facts[item["repo"]] = {"repo_exists": True, "module_json": False, "parent": None}
facts["kushin77/deepseek"] = {"repo_exists": True, "module_json": True, "parent": None}
(scratch / "peer-facts.json").write_text(json.dumps(facts, indent=2) + "\n", encoding="utf-8")
PY
then
  echo "check-module-admission: CANNOT-ASSESS — could not stage the negative controls" >&2
  exit 2
fi

expect_rc() {
  # $1 want  $2 label  $3 module name it must name  $4.. = run_check args
  want="$1"; label="$2"; needle="$3"; shift 3
  out="$(run_check "$@" 2>&1)"
  got=$?
  if [ "$got" -ne "$want" ]; then
    printf 'check-module-admission: FAIL — negative control %s returned rc %s (want %s)\n' \
      "$label" "$got" "$want" >&2
    exit 1
  fi
  if [ -n "$needle" ] && ! printf '%s\n' "$out" | grep -q "$needle"; then
    printf 'check-module-admission: FAIL — negative control %s did not name %s\n' \
      "$label" "$needle" >&2
    exit 1
  fi
  printf '  OK    negative control: %s (rc %s, names %s)\n' "$label" "$got" "$needle"
}

expect_rc 1 "undeclared register entry is refused" "hermes-agents" \
  "$scratch/mut-undeclared.json" "$doc" declared ""
expect_rc 1 "declared sub-module with no peer module.json is refused" "hermes-agents" \
  "$scratch/mut-no-peer-manifest.json" "$doc" declared ""
expect_rc 1 "the peer-side check refuses a declared peer with no manifest" "hermes-agents" \
  "$scratch/mut-declared.json" "$doc" peers "$scratch/peer-facts.json"
expect_rc 1 "unresolvable dependency pin is refused" "deepseek" \
  "$scratch/mut-bad-pin.json" "$doc" declared ""
expect_rc 1 "an entry naming a repo that does not exist is refused" "ollama" \
  "$scratch/mut-no-repo.json" "$doc" declared ""
expect_rc 2 "a missing peer fact is CANNOT-ASSESS, never a pass" "deepseek" \
  "$scratch/mut-missing-fact.json" "$doc" declared ""

echo "  OK    negative controls passed (the gate can genuinely fail)"
exit 0
