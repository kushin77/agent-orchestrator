"""Self-heal a stale committed board snapshot before failing (issue #1692).

Shared by every ``.board/snapshot.json`` freshness consumer —
``governance/dispatch/queue_freshness.py`` (#1189) and
``governance/ticket/freshness.py`` (#1077): a bare staleness refusal is a
chore with no owner, so before a gate fails on it, this tries the ONE
declared refresh verb (``python3 governance/dispatch/cli.py snapshot
--from-github``) and re-assesses. It only fails if the refresh itself fails
(no ``gh`` auth, offline, ...) — and then the failure names why.

This module lives at ``governance/`` top level, outside both consumer
packages, on purpose: ``governance/dispatch`` and ``governance/ticket`` are
deliberately isolated script directories that share bare module basenames
(``model``, ``cli``, ``snapshot`` — see each package's ``tests/conftest.py``,
issues #699/#702/#1042), so importing one package's modules from inside the
other collides the flat namespace. This module has no such dependency: it
shells out to the refresh verb as a subprocess — the same command every
refusal already names — so either consumer can call it without importing the
other's internals.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parent.parent
DISPATCH_CLI = REPO_ROOT / "governance" / "dispatch" / "cli.py"

#: The refusal names this so the remedy travels with the finding (RCA-0014).
REFRESH_COMMAND = "python3 governance/dispatch/cli.py snapshot --from-github"


def refresh(
    repo: str | None = None,
    *,
    runner: Callable[..., subprocess.CompletedProcess] | None = None,
    timeout: float | None = None,
) -> tuple[bool, str]:
    """Run the ONE refresh verb as a subprocess; return ``(ok, detail)``.

    A refused network, a failing ``gh`` and a call that outlives ``timeout``
    are all reported as ``(False, reason)`` — a first-class outcome, never a
    crash.
    """
    run = runner or subprocess.run
    cmd = [sys.executable, str(DISPATCH_CLI), "snapshot", "--from-github"]
    if repo:
        cmd += ["--repo", repo]
    try:
        result = run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        return False, f"refresh exceeded {timeout}s: {exc}"
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip() or f"exit {result.returncode}"
        return False, detail
    lines = (result.stdout or "").strip().splitlines()
    return True, (lines[-1] if lines else "refreshed")


def self_heal(
    assess: Callable[..., Any],
    subject: Any,
    *,
    max_age_hours: float,
    now: Any = None,
    repo: str | None = None,
    stale_code: str = "board-snapshot-stale",
    runner: Callable[..., subprocess.CompletedProcess] | None = None,
    timeout: float | None = None,
) -> tuple[Any, bool, str]:
    """Refresh-then-reassess a stale committed snapshot.

    ``assess`` is the caller's own assess() (``queue_freshness.assess`` or
    ``ticket.freshness.assess``) — same shape (``.ok``, a tuple of findings/
    violations each with ``.code``), different module; ``subject`` is
    whatever that assess() takes (a snapshot file, or a project root).

    Returns ``(verdict, healed, detail)``: ``verdict`` is the final assess()
    result (post-refresh when one was attempted), ``healed`` is True only
    when a refresh was attempted AND cleared the staleness, and ``detail`` is
    the refresh's own outcome string — empty when no refresh was attempted
    (already fresh, or stale for a reason a refresh can't fix, e.g. no
    generated_at at all).
    """
    verdict = assess(subject, max_age_hours=max_age_hours, now=now)
    if verdict.ok:
        return verdict, False, ""
    findings = getattr(verdict, "findings", None)
    if findings is None:
        findings = getattr(verdict, "violations", ())
    if not any(finding.code == stale_code for finding in findings):
        return verdict, False, ""
    ok, detail = refresh(repo, runner=runner, timeout=timeout)
    if not ok:
        return verdict, False, detail
    healed_verdict = assess(subject, max_age_hours=max_age_hours, now=now)
    return healed_verdict, healed_verdict.ok, detail
