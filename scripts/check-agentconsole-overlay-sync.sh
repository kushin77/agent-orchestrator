#!/usr/bin/env bash
# check-agentconsole-overlay-sync.sh — the overlay-lift sync tool must not be
# inert (issue #1513, parent #1510). This is the offline gate half: it proves
# the detector on fixtures AND validates the manifest schema, so a sync tool
# whose comparison silently stops detecting (or a manifest that rots) fails
# `make verify` by name.
#
# It is deliberately OFFLINE (no gh, no network) — `make verify` must not need
# the network. The live half (`scripts/sync-agentconsole-overlay.sh --check`)
# runs under the push-triggered Cloud Build job (infra/cloudbuild/overlay-sync.*),
# which is where the network-dependent diff belongs.
#
# WHAT IS PROVEN
#   * --self-test: an identical fixture pair is accepted (no false drift) and a
#     mutated pair is reported BY NAME (no false green — GR-12).
#   * the manifest (scripts/overlay-sync-manifest.json) parses and every entry
#     carries name + source_repo + source_path + target_path, and a skip (when
#     present) is a non-empty string.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
set -uo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-agentconsole-overlay-sync: CANNOT-ASSESS — python3 is required" >&2
  exit 2
fi

# 1. the detector proves itself on fixtures (offline).
bash scripts/sync-agentconsole-overlay.sh --self-test
rc=$?
if [ "$rc" -ne 0 ]; then
  echo "check-agentconsole-overlay-sync: FAIL — --self-test returned $rc" >&2
  exit "$rc"
fi

# 2. the manifest parses and every entry is well-formed.
python3 - scripts/overlay-sync-manifest.json <<'PY'
import json, sys
m = json.load(open(sys.argv[1], encoding="utf-8"))
errs = []
if m.get("schema") != "overlay-sync-v1":
    errs.append("schema is not overlay-sync-v1")
entries = m.get("entries")
if not isinstance(entries, list) or not entries:
    errs.append("entries is not a non-empty list")
else:
    seen = set()
    for e in entries:
        for req in ("name", "source_repo", "source_path", "target_path"):
            if not e.get(req):
                errs.append(f"entry {e.get('name','?')!r} missing '{req}'")
        n = e.get("name")
        if n in seen:
            errs.append(f"duplicate entry name {n!r}")
        seen.add(n)
        if "skip" in e and not isinstance(e.get("skip"), str):
            errs.append(f"entry {n!r} skip is not a string")
if errs:
    for e in errs:
        print(f"  manifest: {e}", file=sys.stderr)
    sys.exit(1)
print(f"  manifest: OK ({len(entries)} entries)")
sys.exit(0)
PY
mrc=$?
if [ "$mrc" -ne 0 ]; then
  echo "check-agentconsole-overlay-sync: FAIL — manifest invalid" >&2
  exit 1
fi

echo "check-agentconsole-overlay-sync: OK"
exit 0
