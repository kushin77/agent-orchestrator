"""The self-wiring discovery layer must pick up a new check and honour the denylist.

Issue #698: scripts/verify.sh held an explicit `checks=()` array, so a new
`scripts/check-*.sh` needed a hand-edit to the sole-writer file (#559). The
discovery layer (scripts/discover-checks.sh) ends that serialization: it scans
`scripts/check-*.sh`, derives each check's name from its filename
(`check-X.sh` -> `X`), and emits a `name|bash scripts/check-<name>.sh` entry for
verify.sh to append. A check can be disabled BY NAME via
scripts/check-denylist.txt — never silently: a denylisted name is reported on
stderr.

These tests pin, out-of-process, the two behaviours and both directions:

  * a brand-new `scripts/check-zzz-probe.sh` IS discovered (its entry emitted);
  * once the probe is denylisted by name (either the derived name or the
    basename), it is NOT emitted AND is reported on stderr.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HELPER = REPO / "scripts" / "discover-checks.sh"
PROBE = REPO / "scripts" / "check-zzz-probe.sh"


def run_discovery(env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    """Source the helper and run discover_check_scripts out-of-process."""
    base = dict(os.environ)
    base.pop("CHECK_DENYLIST", None)  # no inherited seam
    if env:
        base.update(env)
    return subprocess.run(
        ["bash", "-c", 'source "%s"; discover_check_scripts' % HELPER],
        cwd=REPO,
        capture_output=True,
        text=True,
        env=base,
    )


def test_new_check_is_discovered() -> None:
    PROBE.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    try:
        result = run_discovery()
        assert result.returncode == 0, result.stderr
        assert "zzz-probe|bash scripts/check-zzz-probe.sh" in result.stdout
    finally:
        PROBE.unlink(missing_ok=True)


def test_denylisted_check_is_excluded_and_reported(tmp_path) -> None:
    PROBE.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    deny = tmp_path / "deny.txt"
    deny.write_text("zzz-probe\n", encoding="utf-8")
    try:
        result = run_discovery(env={"CHECK_DENYLIST": str(deny)})
        assert result.returncode == 0, result.stderr
        assert "zzz-probe" not in result.stdout  # excluded
        assert "zzz-probe" in result.stderr       # reported, never silent
    finally:
        PROBE.unlink(missing_ok=True)


def test_denylist_matches_basename_too(tmp_path) -> None:
    PROBE.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    deny = tmp_path / "deny.txt"
    deny.write_text("check-zzz-probe.sh\n", encoding="utf-8")
    try:
        result = run_discovery(env={"CHECK_DENYLIST": str(deny)})
        assert result.returncode == 0, result.stderr
        assert "zzz-probe" not in result.stdout
    finally:
        PROBE.unlink(missing_ok=True)
