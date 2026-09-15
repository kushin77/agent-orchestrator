"""Permanent negative control for the composite-gate coverage detector (#725, epic #708 G3).

Issue #526 delivered `scripts/check-gate-coverage.sh` — it fails BY NAME when a
delivered `scripts/check-*.sh` is invoked by no gate file (with the #603
provenance rules for the exceptions it accepts). Issue #737 (for #698) proved
that refusal exactly ONCE, by hand: it created a throwaway
`scripts/check-zzz-probe.sh`, observed the failure, and deleted the probe inside
the commit. Nothing re-derived it afterwards, so the refusal path was provable
only by a human repeating the experiment — and a control that cannot fail is a
formality (GR-12 / AO-GR-19).

This module is that experiment, made permanent. It provokes the refusal on a
CONTROLLED universe — a scratch git work tree carrying the SHIPPED detector
byte-for-byte plus a minimal gate surface — so the provocation cannot be
absorbed by this repository's own baseline and never touches the real tree.
Three directions are pinned:

  * an unwired delivered check                 -> the detector exits non-zero and
                                                  NAMES the path;
  * the same check invoked by a gate file      -> the detector exits 0;
  * a baseline row for a NEWLY delivered check -> refused as newly delivered
                                                  (#526/#603: never grandfathered);
  * a planted row newer than its own declared
    provenance commit                        -> refused too (#725: the anchor is
                                                  the row's own sha, not the
                                                  baseline's moving last commit).

`bash scripts/check-gate-coverage-control.sh` runs these assertions inside
`make verify` (the `scripts` pytest suite is itself baselined `swept-only`, so
the corpus alone is not provoked by the gate of record). Run this module
directly — `python3 scripts/tests/test_gate_coverage.py`, also reachable as
`bash scripts/check-gate-coverage-control.sh --prove` — to print the provoked
refusal verbatim.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DETECTOR = REPO / "scripts" / "check-gate-coverage.sh"

# The unwired artifact the control delivers. The `zzz-` prefix keeps it last in a
# sorted listing, mirroring the hand-run #737 probe this control replaces.
PROBE = "scripts/check-zzz-unwired.sh"
PROBE_BODY = "#!/usr/bin/env bash\nexit 0\n"
SUITE = "scratch-suite"

# The detector's gate-invocation universe, made to exist in the scratch tree. The
# list is duplicated deliberately: a change to the detector's universe without the
# control noticing turns these tests red (CANNOT-ASSESS), which is the signal.
GATE_FILES = (
    "scripts/verify.sh",
    "Makefile",
    "scripts/gate.sh",
    "scripts/merge-gate.sh",
    "scripts/qa-loop.sh",
)

# The tracker the never-grandfathered row defers to; must be OPEN in the scratch
# board snapshot, or the detector refuses the deferral for a different reason.
ROW_TRACKER = 725


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _git_env(root: Path) -> dict[str, str]:
    """An isolated environment: no host git identity, no global/system git config.

    The scratch tree must not inherit this machine's `~/.gitconfig` (an ignore
    rule or an `init.defaultBranch` there would change the detector's `git
    check-ignore` answer), and commits need an identity that is not the host's.
    """
    home = root.parent / "home"
    home.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.update(
        HOME=str(home),
        GIT_CONFIG_NOSYSTEM="1",
        GIT_AUTHOR_NAME="gate-coverage-control",
        GIT_AUTHOR_EMAIL="control@example.invalid",
        GIT_COMMITTER_NAME="gate-coverage-control",
        GIT_COMMITTER_EMAIL="control@example.invalid",
        PYTHONDONTWRITEBYTECODE="1",
    )
    return env


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, env=_git_env(root)
    )


def _gate_surface(wire_probe: bool) -> dict[str, str]:
    """A minimal gate surface: names the suite and the detector, never a probe.

    It deliberately carries no `scripts/discover-checks.sh` marker, so the #698
    self-wiring layer stays out of the way and the probe's coverage is decided by
    a literal invocation only — exactly the refusal the detector must make.
    """
    verify = (
        "#!/usr/bin/env bash\n"
        "python3 -m pytest -q %s/tests\n"
        "bash scripts/check-gate-coverage.sh\n" % SUITE
    )
    if wire_probe:
        verify += "bash %s\n" % PROBE
    return {
        "scripts/verify.sh": verify,
        "Makefile": "verify:\n\tbash scripts/verify.sh\n",
        "scripts/gate.sh": "#!/usr/bin/env bash\nexit 0\n",
        "scripts/merge-gate.sh": "#!/usr/bin/env bash\nexit 0\n",
        "scripts/qa-loop.sh": "#!/usr/bin/env bash\nexit 0\n",
    }


def build_universe(
    tmp_path: Path, *, wire_probe: bool, probe: bool = True
) -> Path:
    """Create a scratch git work tree carrying the SHIPPED detector.

    The detector is copied byte-for-byte, so these tests can never pass against a
    re-implementation: they exercise the file this repository gates on.
    """
    root = Path(tmp_path) / "universe"
    (root / "scripts").mkdir(parents=True, exist_ok=True)
    shutil.copy2(DETECTOR, root / "scripts" / "check-gate-coverage.sh")
    for rel, text in _gate_surface(wire_probe).items():
        _write(root, rel, text)
    _write(root, "scripts/pytest-suites.txt", "%s\n" % SUITE)
    if probe:
        _write(root, PROBE, PROBE_BODY)
    _git(root, "init", "-q")
    return root


def run_detector(root: Path) -> subprocess.CompletedProcess:
    """Run the detector as the gate does — from the tree root, out-of-process."""
    return subprocess.run(
        ["bash", "scripts/check-gate-coverage.sh"],
        cwd=root,
        capture_output=True,
        text=True,
        env=_git_env(root),
    )


def test_unwired_check_is_refused_by_name(tmp_path: Path) -> None:
    root = build_universe(tmp_path, wire_probe=False)
    result = run_detector(root)
    assert result.returncode == 1, (result.stdout, result.stderr)
    assert "script %s" % PROBE in result.stderr, result.stderr
    assert "UNWIRED=1" in result.stdout, result.stdout


def test_wired_check_is_accepted(tmp_path: Path) -> None:
    root = build_universe(tmp_path, wire_probe=True)
    result = run_detector(root)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "UNWIRED=0" in result.stdout, result.stdout


def test_newly_delivered_artifact_is_never_grandfathered(tmp_path: Path) -> None:
    root = build_universe(tmp_path, wire_probe=False, probe=False)
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "scratch: gate surface")
    first = _git(root, "rev-parse", "HEAD").stdout.strip()
    assert len(first) == 40, first

    _write(
        root,
        ".board/snapshot.json",
        json.dumps({"issues": [{"number": ROW_TRACKER, "state": "open"}]}),
    )
    _write(
        root,
        "scripts/gate-coverage-baseline.txt",
        "script\t%s\tuninvoked\t#%d\t%s\n" % (PROBE, ROW_TRACKER, first),
    )
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "scratch: baseline row")

    # NOW deliver the artifact the row would have grandfathered: it is absent from
    # the baseline's own last-touched commit, so the row is a planted exception.
    _write(root, PROBE, PROBE_BODY)
    result = run_detector(root)
    assert result.returncode == 1, (result.stdout, result.stderr)
    assert "newly delivered" in result.stderr, result.stderr
    assert PROBE in result.stderr, result.stderr


def test_planted_row_newer_than_its_own_provenance_is_refused(tmp_path: Path) -> None:
    """The row's OWN declared commit is the anchor, not a moving one (#725).

    Before #725 this planted row was ACCEPTED (detector RC=0): the artifact and
    its grandfathering row landed in ONE commit, and the detector compared the
    artifact against the baseline's last-touched commit — the very commit that
    added both, where the artifact of course exists. Anchoring on the row's own
    declared commit refuses it, because the artifact did not exist there.
    """
    root = build_universe(tmp_path, wire_probe=False, probe=False)
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "scratch: gate surface")
    first = _git(root, "rev-parse", "HEAD").stdout.strip()
    assert len(first) == 40, first

    _write(
        root,
        ".board/snapshot.json",
        json.dumps({"issues": [{"number": ROW_TRACKER, "state": "open"}]}),
    )
    # A row whose provenance commit predates the artifact it exempts.
    _write(
        root,
        "scripts/gate-coverage-baseline.txt",
        "script\t%s\tuninvoked\t#%d\t%s\n" % (PROBE, ROW_TRACKER, first),
    )
    # Deliver the artifact and plant its row in the SAME commit.
    _write(root, PROBE, PROBE_BODY)
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "scratch: deliver the artifact and grandfather it together")

    result = run_detector(root)
    assert result.returncode == 1, (result.stdout, result.stderr)
    assert "newly delivered" in result.stderr, result.stderr
    assert PROBE in result.stderr, result.stderr


def _demonstrate() -> int:
    """Print the provoked refusal verbatim — the re-derivation this control automates."""
    with tempfile.TemporaryDirectory(prefix="gate-coverage-control.") as scratch:
        universe = build_universe(Path(scratch), wire_probe=False)
        result = run_detector(universe)
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    problems = []
    if result.returncode == 0:
        problems.append("the detector ACCEPTED an unwired check (exit 0)")
    if "script %s" % PROBE not in result.stderr:
        problems.append("the refusal did not name %s" % PROBE)
    print("gate-coverage control: detector exit code %d (expected non-zero)" % result.returncode)
    for problem in problems:
        print("gate-coverage control: FAIL — %s" % problem, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(_demonstrate())
