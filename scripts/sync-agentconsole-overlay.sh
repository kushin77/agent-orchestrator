#!/usr/bin/env bash
# sync-agentconsole-overlay.sh — automate the contrib/shared-services overlay
# lift (issue #1513, parent epic #1510, offshore pillar).
#
# WHY THIS EXISTS
#   contrib/shared-services/agentconsole.compose.yml waited 3 days (09-17 ->
#   09-20, container hand-started in the meantime) to be lifted into
#   shared-services infra/docker-compose.agentconsole.yml, because the lift was
#   a DOCUMENTED MANUAL step ("shared-services run half lifts it into its own
#   repo"). A manual step cannot drift-close a 3-day gap. This script turns the
#   hand-lift into a mechanical diff: it compares each declared overlay source
#   (this repo, or shared-frontend) against its shared-services run-half target
#   and reports drift, so a future overlay change cannot sit un-lifted for days.
#
# WHAT IS COMPARED (structure, not comment prose)
#   Each pair is parsed as YAML (PyYAML, which resolves `<<: *anchor` merge keys)
#   and the deployable structure is compared, so the two repos' different
#   comment preambles do not read as drift while a real service-body change
#   does. The repo-local compose `name:` key (shared-services adds its own
#   project name at lift time) is stripped before comparing.
#
# MODES
#   --check   (default) diff every non-skip manifest entry; exit 0 all-in-sync,
#             1 drift-or-missing, 2 CANNOT-ASSESS. This is what the push-triggered
#             CI runs (infra/cloudbuild/overlay-sync.yaml).
#   --sync    generate the run-half handoff for each drifted/missing entry and,
#             unless --dry-run, file it as a DIRECTION ISSUE on the shared-services
#             board (AGENTS.md: the run half is handed off by direction issue,
#             never by an edit to that repo). --dry-run prints the handoff only.
#   --self-test  offline provocation (no network, no gh): proves the comparison
#             accepts an identical fixture pair AND reports a mutated pair, by
#             name — a control that cannot fail is a formality (GR-12).
#
# MANIFEST: scripts/overlay-sync-manifest.json (schema overlay-sync-v1).
#   entry: { name, source_repo, source_path, target_path, skip? }.
#   source_repo == "agent-orchestrator" (or "self" / "local") reads the local
#   file from the repo root; any other value fetches via
#   `gh api repos/<repo>/contents/<path>`.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# Usage:
#   bash scripts/sync-agentconsole-overlay.sh --check [--manifest FILE]
#   bash scripts/sync-agentconsole-overlay.sh --sync [--dry-run]
#   bash scripts/sync-agentconsole-overlay.sh --self-test
set -uo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

manifest="$root/scripts/overlay-sync-manifest.json"
mode="check"
dry_run=0
src_local=""
tgt_local=""

while [ $# -gt 0 ]; do
  case "$1" in
    --check)        mode="check"; shift ;;
    --sync)         mode="sync"; shift ;;
    --dry-run)      dry_run=1; shift ;;
    --self-test)    mode="self-test"; shift ;;
    --manifest)     manifest="${2:-}"; shift 2 ;;
    --manifest=*)   manifest="${1#*=}"; shift ;;
    --source-local) src_local="${2:-}"; shift 2 ;;
    --target-local) tgt_local="${2:-}"; shift 2 ;;
    -h|--help)      sed -n '2,52p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "sync-agentconsole-overlay: unknown argument '$1'" >&2; exit 2 ;;
  esac
done

if ! command -v python3 >/dev/null 2>&1; then
  echo "sync-agentconsole-overlay: CANNOT-ASSESS — python3 is required" >&2
  exit 2
fi

python3 - "$root" "$manifest" "$mode" "$dry_run" "$src_local" "$tgt_local" <<'PY'
import json, os, subprocess, sys, tempfile

ROOT, MANIFEST, MODE = sys.argv[1], sys.argv[2], sys.argv[3]
DRY_RUN = sys.argv[4] == "1"
SRC_LOCAL = sys.argv[5] or ""
TGT_LOCAL = sys.argv[6] or ""
SELF = ("agent-orchestrator", "self", "local")

def gh_fetch(repo, path):
    """Return (bytes, status) where status is 'ok' | 'missing' | 'error'."""
    proc = subprocess.run(
        ["gh", "api", f"repos/{repo}/contents/{path}", "--jq", ".content"],
        capture_output=True, text=True)
    if proc.returncode == 0:
        import base64
        try:
            return base64.b64decode(proc.stdout.strip()), "ok"
        except Exception:
            return b"", "error"
    err = (proc.stderr or "").lower()
    if "not found" in err or "404" in err:
        return b"", "missing"
    return b"", "error"

def read_source(entry):
    if SRC_LOCAL:
        with open(SRC_LOCAL, "rb") as fh:
            return fh.read(), "ok"
    if entry.get("source_repo") in SELF:
        p = os.path.join(ROOT, entry["source_path"])
        if not os.path.exists(p):
            return b"", "missing"
        with open(p, "rb") as fh:
            return fh.read(), "ok"
    return gh_fetch(entry["source_repo"], entry["source_path"])

def read_target(entry):
    if TGT_LOCAL:
        with open(TGT_LOCAL, "rb") as fh:
            return fh.read(), "ok"
    return gh_fetch(entry.get("target_repo"), entry["target_path"])

def parse_yaml(raw, label):
    import yaml
    try:
        doc = yaml.safe_load(raw.decode("utf-8"))
    except Exception as exc:
        return None, f"parse: {exc}"
    if not isinstance(doc, dict):
        return None, "not a mapping"
    return doc, ""

def compare_parsed(src, tgt):
    """Return a list of human-readable difference strings (empty == in sync)."""
    diffs = []
    s = {k: v for k, v in src.items() if k != "name"}
    t = {k: v for k, v in tgt.items() if k != "name"}
    for key in sorted(set(s) | set(t)):
        if key not in s:
            diffs.append(f"key '{key}' present only in TARGET")
        elif key not in t:
            diffs.append(f"key '{key}' present only in SOURCE")
        elif s[key] != t[key]:
            if key == "services":
                ss, ts = s[key], t[key]
                for svc in sorted(set(ss) | set(ts)):
                    if svc not in ss:
                        diffs.append(f"service '{svc}' present only in TARGET")
                    elif svc not in ts:
                        diffs.append(f"service '{svc}' present only in SOURCE")
                    elif ss[svc] != ts[svc]:
                        for k in sorted(set(ss[svc]) | set(ts[svc])):
                            if ss[svc].get(k) != ts[svc].get(k):
                                diffs.append(f"service '{svc}' key '{k}' differs")
            else:
                diffs.append(f"key '{key}' differs")
    return diffs

def load_manifest():
    with open(MANIFEST, encoding="utf-8") as fh:
        m = json.load(fh)
    # target_repo is a top-level default; every entry inherits it unless it
    # overrides. (source_repo is always per-entry.)
    default_tgt = m.get("target_repo", "kushin77/shared-services")
    for e in m.get("entries", []):
        e.setdefault("target_repo", default_tgt)
    return m

def one_entry(entry):
    """Return (verdict, detail) where verdict is 'sync'|'drift'|'missing'|'assess'."""
    if entry.get("skip"):
        return "skip", entry["skip"]
    src_bytes, src_st = read_source(entry)
    if src_st == "missing":
        return "missing", "SOURCE file missing"
    if src_st == "error":
        return "assess", "SOURCE fetch error (gh/network)"
    tgt_bytes, tgt_st = read_target(entry)
    if tgt_st == "missing":
        return "missing", "TARGET file missing (not yet lifted)"
    if tgt_st == "error":
        return "assess", "TARGET fetch error (gh/network)"
    src_doc, src_err = parse_yaml(src_bytes, "source")
    if src_doc is None:
        return "assess", f"SOURCE {src_err}"
    tgt_doc, tgt_err = parse_yaml(tgt_bytes, "target")
    if tgt_doc is None:
        return "assess", f"TARGET {tgt_err}"
    diffs = compare_parsed(src_doc, tgt_doc)
    if diffs:
        return "drift", "; ".join(diffs)
    return "sync", ""

def check():
    m = load_manifest()
    entries = m.get("entries", [])
    drift = 0
    assess = 0
    skipped = 0
    for e in entries:
        verdict, detail = one_entry(e)
        name = e.get("name", "?")
        if verdict == "sync":
            print(f"  IN-SYNC  {name}")
        elif verdict == "skip":
            skipped += 1
            print(f"  SKIP     {name}  ({detail[:90]})")
        elif verdict == "missing":
            drift += 1
            print(f"  DRIFTED  {name}  ({detail})")
        elif verdict == "drift":
            drift += 1
            print(f"  DRIFTED  {name}  {detail}")
        else:
            assess += 1
            print(f"  CANNOT-ASSESS  {name}  ({detail})")
    print(f"sync-agentconsole-overlay: {len(entries)} entries "
          f"({drift} drifted, {assess} cannot-assess, {skipped} skipped)")
    if assess:
        return 2
    if drift:
        return 1
    return 0

def sync():
    m = load_manifest()
    entries = [e for e in m.get("entries", []) if not e.get("skip")]
    drifted = []
    for e in entries:
        verdict, detail = one_entry(e)
        if verdict in ("drift", "missing"):
            drifted.append((e, verdict, detail))
    if not drifted:
        print("sync-agentconsole-overlay: nothing to hand off (all in sync)")
        return 0
    target_repo = m.get("target_repo", "kushin77/shared-services")
    for e, verdict, detail in drifted:
        title = f"overlay drift: {e['name']} ({e['source_path']})"
        body = (
            f"Overlay lift drift detected by scripts/sync-agentconsole-overlay.sh "
            f"(agent-orchestrator #1513).\n\n"
            f"- source: `{e['source_repo']}` `{e['source_path']}`\n"
            f"- target: `{target_repo}` `{e['target_path']}`\n"
            f"- state : {verdict}\n- detail: {detail}\n"
        )
        print(f"--- direction issue (would file on {target_repo}) ---")
        print(f"title: {title}")
        print(body.rstrip())
        if not DRY_RUN:
            proc = subprocess.run(
                ["gh", "issue", "create", "--repo", target_repo,
                 "--title", title, "--body", body],
                capture_output=True, text=True)
            print(f"filed: {proc.stdout.strip() or proc.stderr.strip()}")
    return 0

def self_test():
    """Prove the comparison detects drift and accepts identical content."""
    import yaml
    tmp = tempfile.mkdtemp(prefix="ao-overlay-sync-")
    base = {
        "services": {
            "agentconsole": {
                "image": "${AGENTCONSOLE_IMAGE:-x}",
                "container_name": "shared-services-agentconsole",
            }
        }
    }
    ident_a = os.path.join(tmp, "a.yml")
    ident_b = os.path.join(tmp, "b.yml")
    with open(ident_a, "w") as fh:
        yaml.safe_dump(base, fh)
    with open(ident_b, "w") as fh:
        yaml.safe_dump(base, fh)
    # 1. identical pair -> no differences
    a, _ = parse_yaml(open(ident_a, "rb").read(), "a")
    b, _ = parse_yaml(open(ident_b, "rb").read(), "b")
    d = compare_parsed(a, b)
    print(f"self-test: identical pair -> {len(d)} difference(s)")
    if d:
        print("  FAIL: identical pair reported differences: %s" % d)
        return 1
    # 2. mutated pair -> differences, by name; assert the mutation took effect
    mutated = dict(base)
    mutated["services"]["agentconsole"]["image"] = "${AGENTCONSOLE_IMAGE:?CHANGED}"
    mut_file = os.path.join(tmp, "mut.yml")
    with open(mut_file, "w") as fh:
        yaml.safe_dump(mutated, fh)
    c, _ = parse_yaml(open(mut_file, "rb").read(), "mut")
    if c["services"]["agentconsole"]["image"] != "${AGENTCONSOLE_IMAGE:?CHANGED}":
        print("  FAIL: mutation did not take effect")
        return 1
    d = compare_parsed(b, c)
    print(f"self-test: mutated pair -> {len(d)} difference(s): {d}")
    if not d:
        print("  FAIL: mutated pair reported no differences")
        return 1
    # 3. a missing-target pair (only-source) must report drift too
    src_only = os.path.join(tmp, "src_only.yml")
    with open(src_only, "w") as fh:
        yaml.safe_dump(base, fh)
    s_doc, _ = parse_yaml(open(src_only, "rb").read(), "src_only")
    e_doc = {"services": {}}
    d = compare_parsed(s_doc, e_doc)
    print(f"self-test: only-source pair -> {len(d)} difference(s): {d}")
    if not d:
        print("  FAIL: only-source pair reported no differences")
        return 1
    print("self-test: OK (accepts identical, detects mutated + missing, by name)")
    return 0

def main():
    if MODE == "self-test":
        return self_test()
    if MODE == "sync":
        return sync()
    return check()

sys.exit(main())
PY
