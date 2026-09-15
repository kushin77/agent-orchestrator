"""The ticket-trailer predicate — one implementation, shared with the PR gate.

Golden rule 1 (`AGENTS.md`) requires every commit a session authors to carry
``Refs kushin77/agent-orchestrator#<n>``. Where that reference sits is the
substance of the rule: it must be a line of the message's **trailing trailer
block**, not a mention somewhere in the message. A reference in the subject line
or buried in prose is a mention, and a commit whose message has no trailer
paragraph at all is exactly what the rule exists to refuse (issue #287, measured
on `6d89618` and `576edce`).

That predicate already exists — once — in ``scripts/check-pr-contract.sh``
(issue #288, live since `a7e7312`), whose landed audit runs it over every
non-merge commit. This module is deliberately **not** a second implementation of
it. It is the adapter the lane audit uses to ask that predicate about one
commit, so the isolation audit and the PR-contract gate cannot drift apart: one
rule, one parser, one place to fix a bug in either. Two implementations of one
rule is the worst outcome — they disagree silently, and the disagreement stays
invisible until a real commit slips through the weaker one.

The adapter runs the gate exactly as its own landed audit does, one commit at a
time::

    bash scripts/check-pr-contract.sh --repo <r> --landed \\
        --range <sha>^..<sha> --enforcement-gate <sha>^

The enforcement boundary is the commit's own parent, so the range is exactly one
commit. Findings about the boundary itself (``enforcement-gate-*``) are the
script's statement about the *grandfathered region*, not about the commit under
test, and are filtered out here; the landed mode in ``cli.py`` reports them
verbatim instead, because a whole-range re-check is the shared gate's verdict
rather than this adapter's.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

#: The one implementation of the trailing-trailer predicate (issue #288).
PREDICATE_SCRIPT = REPO_ROOT / "scripts" / "check-pr-contract.sh"

#: ``  FAIL  <finding>:<commit>`` — the shape ``report()`` writes to stderr. The
#: commit field is whatever the predicate printed, and that is deliberately not
#: required to be 12 hex: for the enforcement boundary the predicate truncates the
#: ``--enforcement-gate`` argument it was given, so a caller that passes a rev
#: expression (``8b97ab6^``, as the boundary controls in this suite do) gets that
#: expression back. Requiring hex here dropped the boundary finding silently, and
#: the over-strict parse is exactly how a "no parseable finding" guard ends up
#: mistaking a real verdict for an unreadable one (measured: it made the
#: real-commit control on `8b97ab6` fail after the guard was added, issue #287).
_FINDING_RE = re.compile(r"^\s*FAIL\s+(?P<code>[a-z][a-z0-9-]*):(?P<sha>[^\s:]+)\s*$")

#: Findings about the grandfathered region rather than about the commit itself.
_BOUNDARY_PREFIX = "enforcement-gate-"

#: A hung predicate must not hang a gate. Measured: the script is sub-second.
_TIMEOUT_SECONDS = 120


class PredicateUnavailable(RuntimeError):
    """The shared predicate could not be run — never a silent pass (GR-12)."""


@dataclass(frozen=True)
class LandedResult:
    """A whole-range landed re-check: the shared gate's exit code and output."""

    returncode: int
    output: str


def _run(args: list[str], cwd: Path | str) -> subprocess.CompletedProcess:
    """Run the shared predicate under this repo's own git hygiene."""
    if not PREDICATE_SCRIPT.is_file():
        raise PredicateUnavailable(f"the shared predicate {PREDICATE_SCRIPT} is missing")
    try:
        return subprocess.run(
            ["bash", str(PREDICATE_SCRIPT), *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
            env={
                **os.environ,
                # The module never reads the developer's global git config, and
                # neither does the predicate it delegates to.
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_TERMINAL_PROMPT": "0",
            },
        )
    except FileNotFoundError as exc:  # no bash on PATH
        raise PredicateUnavailable(f"cannot run the shared predicate: {exc}") from exc
    except subprocess.SubprocessError as exc:
        raise PredicateUnavailable(f"the shared predicate did not complete: {exc}") from exc


def findings_in(output: str) -> list[tuple[str, str]]:
    """Every ``(code, sha12)`` finding the predicate printed in ``output``.

    The predicate reports findings as ``  FAIL  <code>:<sha12>`` on stderr, and
    that shape is the whole contract this adapter reads; nothing here re-derives
    the trailer rule, it only parses the verdict the one implementation printed.
    A caller that needs the per-commit verdict of a whole range (see
    :mod:`governance.isolation.landed`) needs the commit each finding belongs to,
    which ``named_findings`` discards.
    """
    found: list[tuple[str, str]] = []
    for line in output.splitlines():
        match = _FINDING_RE.match(line)
        if match is not None:
            found.append((match.group("code"), match.group("sha")))
    return found


def named_findings(output: str, sha: str = "") -> list[str]:
    """The predicate's findings, as ``<code>`` (filtered to ``sha`` when given).

    Findings about the enforcement boundary are excluded when a specific commit
    is asked about: they describe the grandfathered region, not that commit.
    """
    short = sha[:12]
    found: list[str] = []
    for code, finding_sha in findings_in(output):
        if sha and code.startswith(_BOUNDARY_PREFIX):
            continue
        if sha and not finding_sha.startswith(short):
            continue
        found.append(code)
    return found


def classify_commit(repo: Path | str, sha: str) -> str | None:
    """The shared predicate's finding for one commit, or None when it passes.

    Raises :class:`PredicateUnavailable` when the predicate cannot be run or
    answers CANNOT-ASSESS: an unassessable commit is not a clean one.
    """
    result = _run(
        ["--repo", str(repo), "--landed", "--range", f"{sha}^..{sha}", "--enforcement-gate", f"{sha}^"],
        cwd=repo,
    )
    named = named_findings(result.stderr, sha)
    if named:
        return named[0]
    if result.returncode == 2:
        detail = (result.stderr.strip() or result.stdout.strip()).splitlines()
        raise PredicateUnavailable(
            f"CANNOT-ASSESS for {sha[:12]}: {detail[-1] if detail else 'no output from the shared predicate'}"
        )
    if result.returncode != 0 and not findings_in(result.stderr):
        # A non-zero predicate that printed no finding this adapter can parse at
        # all is a verdict it cannot attribute — a changed output shape, a crash.
        # Returning None here would report the commit as clean, which is the
        # fail-open direction: unproven is not clean (GR-12). Measured by mutation
        # while proving ``scripts/check-isolation-landed.sh``: making the finding
        # parser drop everything turned a non-compliant lane audit green until
        # this branch existed.
        #
        # Findings that exist but belong to the enforcement BOUNDARY are the
        # different, legitimate case handled below by falling through: the
        # predicate is describing the grandfathered region, and the commit under
        # test is clean.
        detail = (result.stderr.strip() or result.stdout.strip()).splitlines()
        raise PredicateUnavailable(
            f"the shared predicate exited {result.returncode} for {sha[:12]} without naming a finding: "
            f"{detail[-1] if detail else 'no output'}"
        )
    return None


def run_landed(repo: Path | str, range_: str = "HEAD", gate: str = "") -> LandedResult:
    """Re-check a whole landed range through the shared gate, verbatim.

    The verdict is the shared gate's, not an interpretation of it: a caller in
    this position is asking "does the repository's landed history satisfy the
    rule", which is exactly what ``--landed`` answers.
    """
    args = ["--repo", str(repo), "--landed", "--range", range_]
    if gate:
        args += ["--enforcement-gate", gate]
    result = _run(args, cwd=repo)
    return LandedResult(returncode=result.returncode, output=f"{result.stdout}{result.stderr}")
