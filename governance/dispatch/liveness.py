"""The board's liveness contract and the state of its producers (issue #1179).

WHY THIS MODULE EXISTS
    ``governance/dispatch/snapshot.py`` refuses an answer derived from a board
    snapshot older than ``SNAPSHOT_STALENESS_MINUTES`` (15; declared once in
    ``governance/policy/lease.py``). That is a **liveness** tolerance: it is only
    honest for a consumer that keeps the board fresh. The fleet loop does — it
    runs the #727 in-band refresh (``refresh_or_park``) — but for a long time
    nothing said so, and nothing checked that *any* producer of a fresh board
    existed. So the entry point was ``CANNOT-ASSESS`` by construction: the
    requirement had no producer it could name, and the one cron rung that could
    have been that producer is declared ``"enabled": false`` in
    ``config/fleet-jobs.json`` (ship-gated OFF) — which
    ``scripts/check-fleet-jobs.sh`` *affirmatively requires*, because it proves
    the reconciler, not the installation.

WHAT IT DECIDES
    Given the declaration (``config/fleet-jobs.json``) and the LIVE crontab, this
    module answers one question in the repo's tri-state vocabulary: **is there a
    producer of a fresh board, and is it the one that was declared?** Four
    outcomes, each named:

    ``installed``
        the declared board-refresh rung is enabled *and* installed;
    ``self-refresh``
        no rung is installed, but the entry point refreshes in-band, so the
        liveness contract is still met (this is the state the loop relies on);
    ``declared-but-not-installed``
        the rung is enabled in the manifest and absent from the live crontab —
        **a finding, never a pass** (this is the gap the module exists for);
    ``installed-but-declared-off``
        the rung is installed while the manifest declares it OFF — the mirror
        image, and also a finding: the installed crontab carries a line the
        declaration says should not be there.

    An unreadable manifest or crontab is **CANNOT-ASSESS**, never a pass: the
    produced state cannot be compared with the declared one, and "I could not
    look" must never read as "it is fine" (GR-12).

    Reading the LIVE crontab is the point. ``scripts/check-fleet-jobs.sh``
    proves the reconciler heals drift *in a scratch crontab* and never touches
    the real one; that is exactly the blind spot this module closes, so the real
    crontab is the only source it accepts (a ``--crontab-file`` fixture seam
    exists for the gate's provocations and names itself as such).
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

#: The manifest key that declares which job, if installed, refreshes the board.
#: A role rather than a marker name: renaming the rung must not silently detach
#: the liveness contract from its producer.
REFRESH_KEY = "refreshes"
BOARD_REFRESH_ROLE = "board-snapshot"

MANIFEST_PATH = ROOT / "config" / "fleet-jobs.json"

# --- verdicts ---------------------------------------------------------------
VERDICT_INSTALLED = "installed"
VERDICT_SELF_REFRESH = "self-refresh"
VERDICT_DECLARED_NOT_INSTALLED = "declared-but-not-installed"
VERDICT_INSTALLED_BUT_OFF = "installed-but-declared-off"
VERDICT_NO_PRODUCER = "no-producer"
VERDICT_CANNOT_ASSESS = "cannot-assess"

#: The verdicts that are findings: a defect the reader must act on. A pass is
#: never in this set, and a finding is never silently a pass.
FINDING_VERDICTS = (
    VERDICT_DECLARED_NOT_INSTALLED,
    VERDICT_INSTALLED_BUT_OFF,
    VERDICT_NO_PRODUCER,
)


@dataclass(frozen=True)
class Declaration:
    """The declared board-refresh rung: what the manifest says should be running."""

    declared: bool
    name: str = ""
    marker: str = ""
    enabled: bool = False
    detail: str = ""

    @property
    def assessable(self) -> bool:
        return not self.detail


@dataclass(frozen=True)
class Crontab:
    """The reader's answer about the LIVE crontab. Unreadable is not empty."""

    assessable: bool
    lines: tuple[str, ...] = ()
    detail: str = ""

    def has(self, marker: str) -> bool:
        """Whether a crontab line carries ``marker`` as its own trailing comment tag.

        Matched as the line's trailing ``# <marker>`` tag, never as a bare
        substring of the whole file: a marker *mentioned* inside another job's
        command is not that job's line, and a comment-only line is not a job at
        all. The rendered convention (``fleet/cron.py``) is ``... # <marker>``.
        """
        if not marker:
            return False
        for line in self.lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if "#" not in stripped:
                continue
            tag = stripped.rsplit("#", 1)[-1].strip()
            if tag == marker:
                return True
        return False


@dataclass(frozen=True)
class Liveness:
    """The verdict: is there a producer of a fresh board, and is it the declared one?"""

    verdict: str
    declaration: Declaration
    crontab: Crontab
    self_refresh: bool = True
    detail: str = ""

    @property
    def assessable(self) -> bool:
        return self.verdict != VERDICT_CANNOT_ASSESS

    @property
    def ok(self) -> bool:
        return self.verdict in (VERDICT_INSTALLED, VERDICT_SELF_REFRESH)

    @property
    def finding(self) -> str:
        """The named finding, empty when there is nothing to report.

        Named, not numbered: the string is what a refusal quotes back and what a
        gate asserts, so it must survive a refactor of the surrounding prose.
        """
        marker = self.declaration.marker or "<undeclared>"
        if self.verdict == VERDICT_DECLARED_NOT_INSTALLED:
            return (
                f"declared-but-not-installed: the board-refresh rung {marker} is enabled in "
                f"{MANIFEST_PATH.name} but no line carrying it is in the live crontab"
            )
        if self.verdict == VERDICT_INSTALLED_BUT_OFF:
            return (
                f"installed-but-declared-off: the live crontab carries {marker}, which "
                f"{MANIFEST_PATH.name} declares enabled:false — the crontab and the "
                "declaration disagree about what should be running"
            )
        if self.verdict == VERDICT_NO_PRODUCER:
            return (
                "no-board-refresher: the live crontab carries no board-refresh rung, the "
                f"manifest declares {marker} shipped OFF, and the entry point declares no "
                "in-band refresh — nothing can produce a board fresh enough for the "
                "staleness contract, so every board read is CANNOT-ASSESS by construction"
            )
        return ""

    def to_json(self) -> dict:
        return {
            "verdict": self.verdict,
            "check": "liveness",
            "assessable": self.assessable,
            "ok": self.ok,
            "finding": self.finding,
            "detail": self.detail,
            "self_refresh": self.self_refresh,
            "declaration": {
                "declared": self.declaration.declared,
                "name": self.declaration.name,
                "marker": self.declaration.marker,
                "enabled": self.declaration.enabled,
                "source": str(MANIFEST_PATH),
            },
            "crontab": {
                "assessable": self.crontab.assessable,
                "lines": len(self.crontab.lines),
                "source": "live crontab",
            },
        }


def _default_runner(cmd, **kwargs):
    return subprocess.run(cmd, **kwargs)


def read_manifest(path: Path | str | None = None) -> dict:
    """Read the fleet-jobs manifest. Raises ``OSError``/``ValueError`` if unusable."""
    target = Path(path) if path is not None else MANIFEST_PATH
    try:
        with target.open(encoding="utf-8") as handle:
            return json.load(handle)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{target} is not valid JSON ({exc})") from exc


def declaration(path: Path | str | None = None) -> Declaration:
    """Find the job that declares itself the board refresher.

    A manifest that cannot be read, or that declares no board-refresh role, is
    reported as such — never treated as "no declaration, so nothing is missing".
    """
    try:
        manifest = read_manifest(path)
    except FileNotFoundError:
        return Declaration(False, detail="config/fleet-jobs.json is missing")
    except ValueError as exc:
        return Declaration(False, detail=str(exc))
    except OSError as exc:
        return Declaration(False, detail=f"config/fleet-jobs.json is unreadable ({exc})")

    jobs = manifest.get("jobs")
    if not isinstance(jobs, list):
        return Declaration(False, detail="config/fleet-jobs.json has no 'jobs' list")
    for job in jobs:
        if not isinstance(job, dict):
            continue
        if str(job.get(REFRESH_KEY) or "") == BOARD_REFRESH_ROLE:
            return Declaration(
                True,
                name=str(job.get("name") or ""),
                marker=str(job.get("marker") or ""),
                enabled=job.get("enabled") is True,
            )
    return Declaration(
        False,
        detail=(
            f"no job in config/fleet-jobs.json declares {REFRESH_KEY}: {BOARD_REFRESH_ROLE}, "
            "so the board's liveness contract has no declared producer"
        ),
    )


def read_crontab(
    *,
    command: str = "crontab",
    runner=None,
    crontab_file: Path | str | None = None,
) -> Crontab:
    """Read the LIVE crontab.

    Three outcomes, and only one of them is an empty crontab:

    * the crontab is readable (rc 0, or "no crontab for ..." — a readable, empty
      answer) -> ``assessable=True``;
    * ``crontab_file`` was given (the gate's fixture seam) -> that file, or
      CANNOT-ASSESS if it is unreadable;
    * anything else (no ``crontab`` binary, a non-zero exit that is not "no
      crontab") -> ``assessable=False``, because "I could not look" is not "there
      is nothing there".
    """
    if crontab_file is not None:
        target = Path(crontab_file)
        try:
            text = target.read_text(encoding="utf-8")
        except OSError as exc:
            return Crontab(False, detail=f"crontab fixture {target} is unreadable ({exc})")
        return Crontab(True, lines=tuple(text.splitlines()))

    run = runner or _default_runner
    try:
        proc = run(
            [command, "-l"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except FileNotFoundError:
        return Crontab(False, detail=f"{command} was not found on this host")
    except subprocess.TimeoutExpired:
        return Crontab(False, detail=f"{command} -l did not answer within 30s")
    except OSError as exc:
        return Crontab(False, detail=f"{command} -l could not be run ({exc})")

    stdout = getattr(proc, "stdout", "") or ""
    stderr = getattr(proc, "stderr", "") or ""
    rc = getattr(proc, "returncode", 1)
    if rc == 0:
        return Crontab(True, lines=tuple(stdout.splitlines()))
    if "no crontab for" in stderr:
        return Crontab(True, lines=())
    return Crontab(False, detail=f"{command} -l failed (rc {rc}): {stderr.strip() or 'no stderr'}")


def assess(
    *,
    manifest_path: Path | str | None = None,
    crontab_file: Path | str | None = None,
    command: str = "crontab",
    runner=None,
    self_refresh: bool = True,
) -> Liveness:
    """The verdict: is the board's liveness contract met, by the declared producer?

    ``self_refresh`` is the entry point's own declaration that it can refresh
    in-band (``cli.py`` passes ``True``; a caller that cannot is honest about it
    and gets ``no-producer``).
    """
    declared = declaration(manifest_path)
    crontab = read_crontab(command=command, runner=runner, crontab_file=crontab_file)

    if not declared.assessable:
        return Liveness(VERDICT_CANNOT_ASSESS, declared, crontab, self_refresh, declared.detail)
    if not crontab.assessable:
        return Liveness(VERDICT_CANNOT_ASSESS, declared, crontab, self_refresh, crontab.detail)

    installed = crontab.has(declared.marker) if declared.marker else False

    if declared.enabled and installed:
        verdict = VERDICT_INSTALLED
    elif declared.enabled and not installed:
        verdict = VERDICT_DECLARED_NOT_INSTALLED
    elif installed:
        verdict = VERDICT_INSTALLED_BUT_OFF
    elif self_refresh:
        verdict = VERDICT_SELF_REFRESH
    else:
        verdict = VERDICT_NO_PRODUCER

    detail = ""
    if verdict == VERDICT_INSTALLED:
        detail = f"{declared.marker} is enabled and installed"
    elif verdict == VERDICT_SELF_REFRESH:
        detail = (
            f"{declared.marker} is not installed (declared enabled:{str(declared.enabled).lower()}); "
            "the entry point refreshes the board in-band, so the contract is met by self-refresh"
        )
    return Liveness(verdict, declared, crontab, self_refresh, detail)
