#!/usr/bin/env bash
# check-portal-view-syntax.sh — every inline <script> in portal/static/**/*.html
# must parse. The console ships these views as-is (no bundler, no build step), so a
# stray paren is a page that renders nothing in production and nothing in CI noticed:
# approvals.html and policies.html were unparseable from f3fdd7c1 (2026-09-13) until
# this gate existed.
#
# Parser: `node --check` when node is on PATH (dev boxes), otherwise the esprima
# Python parser (the Cloud Build runner is `python:3.14` with no node — pinned in
# infra/cloudbuild/requirements-verify.txt). Neither available = FAIL with the
# reason, never a silent pass.
#
# ---knowledge---
# module_id: scripts.check-portal-view-syntax
# system: governance
# app: gates
# solution_class: enterprise
# patterns: []
# derives_from: null
# owner_sme: qa-sme
# tier: L1
# interfaces: [exit 0 OK, exit 1 NOT-OK]
# invariants: ""
# gotchas: ""
# related: []
# do_not_duplicate: null
# ---knowledge---
set -euo pipefail
cd "$(dirname "$0")/.." || exit 2

if command -v node >/dev/null 2>&1; then
  PARSER=node
elif python3 -c 'import esprima' 2>/dev/null; then
  PARSER=esprima
else
  echo "✗ portal-view-syntax: neither node nor python esprima available (pip install -r infra/cloudbuild/requirements-verify.txt)"
  exit 1
fi

python3 - "$PARSER" <<'PY'
import pathlib, re, subprocess, sys, tempfile

parser = sys.argv[1]
files = sorted(pathlib.Path("portal/static").rglob("*.html"))
blocks = []  # (label, is_module, source)
for f in files:
    s = f.read_text()
    for i, m in enumerate(re.finditer(r"<script(?![^>]*\bsrc=)([^>]*)>(.*?)</script>", s, re.S)):
        blocks.append((f"{f}#{i}", "module" in m.group(1), m.group(2)))

fail = 0
if parser == "node":
    with tempfile.TemporaryDirectory() as d:
        for label, is_module, src in blocks:
            p = pathlib.Path(d) / (re.sub(r"[^A-Za-z0-9]+", "_", label) + (".mjs" if is_module else ".js"))
            p.write_text(src)
            r = subprocess.run(["node", "--check", str(p)], capture_output=True, text=True)
            if r.returncode:
                err = next((l for l in r.stderr.splitlines() if "Error" in l), r.stderr.strip())
                print(f"✗ {label}: {err}")
                fail += 1
else:
    import esprima
    for label, is_module, src in blocks:
        try:
            (esprima.parseModule if is_module else esprima.parseScript)(src)
        except Exception as e:  # esprima.Error carries the line
            print(f"✗ {label}: {e}")
            fail += 1

if fail:
    sys.exit(1)
print(f"✅ portal view scripts parse ({len(blocks)} blocks, {parser})")
PY
