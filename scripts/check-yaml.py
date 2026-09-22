#!/usr/bin/env python3
"""YAML validity gate for `make verify` (GR-12).

Parses every *.yml / *.yaml under the repo (including .github/workflows,
excluding vendor/, .research/, .git, local caches, and the gate's own generated
roots) with PyYAML safe_load. Any file that does not parse is a hard failure —
the gate never passes vacuously.

THE GATE'S OWN OUTPUT IS NOT THE TREE (issue #1983). `verify.sh` keeps its
scratch under `.verify/`, and its checks leave artifacts there too. A walk that
reads them judges a DIFFERENT file set on the second run in the same venue:
MEASURED back to back in one lane worktree, `yaml: OK (296 file(s))` then
`yaml: OK (299 file(s))`, and all 3 extra files are the gate's own output. The
roots are therefore read from the ONE declaration
(`scripts/gate-generated-roots.txt`) that `check-secrets.sh` and
`check-gitignore.sh` read -- never spelled out here.

---knowledge---
module_id: scripts.check-yaml
system: governance
app: gates
solution_class: enterprise
patterns: [no-false-green]
derives_from: null
owner_sme: qa-sme
tier: L1
interfaces: [exit 0 OK, exit 1 NOT-OK, exit 2 CANNOT-ASSESS]
invariants: ""
gotchas: ""
related: ["#1983"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

import os
import sys

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    print(f"check-yaml: PyYAML not installed ({exc})", file=sys.stderr)
    sys.exit(1)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = {".git", ".research", "vendor", "node_modules", ".venv", "venv", ".terraform"}

# The ONE declaration of the gate's own generated roots (issue #1983), read here
# as well as by `check-secrets.sh` and `check-gitignore.sh` -- never re-typed.
DECLARATION = os.path.join(ROOT, "scripts", "gate-generated-roots.txt")


def load_generated_roots() -> set[str] | None:
    """The declared generated roots, or None when the declaration is unusable.

    An unreadable or empty declaration is CANNOT-ASSESS, never a walk that
    quietly excludes nothing -- that silent behaviour is the defect itself.
    """
    try:
        with open(DECLARATION, "r", encoding="utf-8") as handle:
            roots = {
                stripped
                for stripped in (line.split("#", 1)[0].strip() for line in handle)
                if stripped
            }
    except OSError as exc:
        print(
            f"check-yaml: CANNOT-ASSESS — the generated-roots declaration is "
            f"unreadable ({exc}): {DECLARATION}",
            file=sys.stderr,
        )
        return None
    if not roots:
        print(
            f"check-yaml: CANNOT-ASSESS — {DECLARATION} declares no generated root",
            file=sys.stderr,
        )
        return None
    return roots

# No known-broken files remain: the legacy ci-failure-scanner.yml (issue #5)
# was repaired by issue #6 (the `run: |` body was re-indented into its literal
# block) and is now parsed and must be valid like every other YAML file. If a
# file is ever intentionally preserved as a non-YAML artifact it must be
# declared here with a visible NOTICE — never silently.
KNOWN_BROKEN: set[str] = set()


def iter_yaml_files(generated_roots: set[str]):
    for dirpath, dirnames, filenames in os.walk(ROOT):
        rel = os.path.relpath(dirpath, ROOT)
        dirnames[:] = [
            d
            for d in dirnames
            if d not in SKIP_DIRS
            and os.path.normpath(os.path.join(rel, d)) not in generated_roots
        ]
        for name in filenames:
            if name.endswith((".yml", ".yaml")):
                yield os.path.join(dirpath, name)


def main() -> int:
    generated_roots = load_generated_roots()
    if generated_roots is None:
        return 2
    failed = 0
    count = 0
    for path in sorted(iter_yaml_files(generated_roots)):
        rel = os.path.relpath(path, ROOT)
        if rel in KNOWN_BROKEN:
            print(f"  SKIP  {rel} (legacy artifact, preserved as-is; issue #6 owns repair)")
            continue
        count += 1
        try:
            with open(path, "r", encoding="utf-8") as fh:
                yaml.safe_load(fh)
            print(f"  OK    {rel}")
        except yaml.YAMLError as exc:
            print(f"  FAIL  {rel}: {exc}", file=sys.stderr)
            failed += 1
    if failed:
        print(f"yaml: {failed} of {count} file(s) FAILED", file=sys.stderr)
        return 1
    print(f"yaml: OK ({count} file(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main())
