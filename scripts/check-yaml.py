#!/usr/bin/env python3
"""YAML validity gate for `make verify` (GR-12).

Parses every *.yml / *.yaml under the repo (including .github/workflows,
excluding vendor/, .research/, .git, and local caches) with PyYAML
safe_load. Any file that does not parse is a hard failure — the gate never
passes vacuously.
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

# No known-broken files remain: the legacy ci-failure-scanner.yml (issue #5)
# was repaired by issue #6 (the `run: |` body was re-indented into its literal
# block) and is now parsed and must be valid like every other YAML file. If a
# file is ever intentionally preserved as a non-YAML artifact it must be
# declared here with a visible NOTICE — never silently.
KNOWN_BROKEN: set[str] = set()


def iter_yaml_files():
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            if name.endswith((".yml", ".yaml")):
                yield os.path.join(dirpath, name)


def main() -> int:
    failed = 0
    count = 0
    for path in sorted(iter_yaml_files()):
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
