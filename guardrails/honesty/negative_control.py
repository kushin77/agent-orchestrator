"""Negative-control + blockproof runner (issue #28, acceptance criterion 2).

A guard that only tests the happy path proves nothing; a guard with a control
that correctly FAILS proves the guard discriminates.  This module runs the
controls every honest guard ships:

* a NEGATIVE control -- a planted mutation the guard MUST fail on (expected
  verdict NOT-OK).  If the guard returns OK on the mutation it is a
  no-false-green violation and the control FAILS.
* a POSITIVE control -- clean input the guard MUST pass (expected OK).
* a CANNOT-ASSESS control -- input the guard cannot assess (expected
  CANNOT-ASSESS), proving the UNKNOWN path never reads as a pass.

Each run produces a BLOCKPROOF: the actual exit code, the tri-state verdict,
the expected verdict, and the output -- evidence the guard really fired.

Controls are declared in a YAML manifest:

    controls:
      - id: blocklist_rejects_forbidden
        guard: fixtures/honest/check_blocklist.sh
        args: [fixtures/inputs/violation.txt]
        expect: NOT-OK
        description: a planted violation must fail the guard

Guard paths are resolved against a base directory (default: the caller's
current directory).  Guards are executed with ``bash`` so the executable bit
is never a hidden precondition.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence

import yaml

from .tristate import TriState, from_exit_code, parse, to_exit_code


@dataclass
class NegativeControl:
    """One declared control: run ``guard`` with ``args`` and require it to
    report ``expect``."""

    id: str
    guard: str  # resolved absolute path once loaded
    args: List[str] = field(default_factory=list)
    expect: TriState = TriState.NOT_OK
    description: str = ""


@dataclass
class ControlOutcome:
    """The observed result of running one control."""

    control_id: str
    guard: str
    args: List[str]
    exit_code: int
    verdict: TriState
    expected: TriState
    output: str

    @property
    def passed(self) -> bool:
        """A control passes only when the guard reported exactly ``expect``.

        If a NEGATIVE control (expect NOT-OK) is run and the guard reports OK,
        the guard could not fail on a violation it must catch -- that is a
        no-false-green failure and the control did NOT pass.
        """
        return self.verdict is self.expected

    def blockproof(self) -> Dict[str, Any]:
        """The evidence block recorded for this control run."""
        return {
            "control": self.control_id,
            "guard": self.guard,
            "planted": list(self.args),
            "exit_code": self.exit_code,
            "verdict": self.verdict.value,
            "expected": self.expected.value,
            "proof": self.passed,
            "output": self.output,
        }


def load_manifest(manifest_path: str, base: Optional[str] = None) -> List[NegativeControl]:
    """Load controls from a YAML manifest.

    Guard paths in the manifest are relative to ``base`` (default: the
    directory of the manifest itself, so the manifest is self-contained).
    """
    if base is None:
        base = os.path.dirname(os.path.abspath(manifest_path))
    with open(manifest_path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    controls: List[NegativeControl] = []

    def _resolve(rel: str) -> str:
        return rel if os.path.isabs(rel) else os.path.normpath(os.path.join(base, rel))

    for item in data.get("controls", []):
        # Guard paths resolve against the manifest base.  Args are NOT
        # absolutized: they are interpreted from the guard's working directory
        # (the manifest base), so a command name such as "bash" passes
        # through untouched while relative file paths resolve naturally.
        controls.append(
            NegativeControl(
                id=str(item["id"]),
                guard=_resolve(str(item["guard"])),
                args=[str(a) for a in item.get("args", [])],
                expect=parse(str(item.get("expect", "NOT-OK"))),
                description=str(item.get("description", "")),
            )
        )
    return controls


def run_control(control: NegativeControl, timeout: int = 30, cwd: Optional[str] = None) -> ControlOutcome:
    """Execute one control and observe the guard's honest verdict.

    The guard is run with ``bash <guard> <args>`` so execution does not depend
    on the executable bit.  ``cwd`` is the working directory the guard runs
    in -- pass the manifest base so relative args resolve there.  A timeout is
    surfaced as exit 124, which the tri-state contract maps to CANNOT-ASSESS
    (never a pass).
    """
    argv = ["bash", control.guard] + list(control.args)
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            cwd=cwd,
        )
        exit_code = proc.returncode
        output = (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired:
        exit_code = 124
        output = f"control {control.id} timed out after {timeout}s"
    except OSError as exc:  # guard could not even be invoked -> cannot assess
        exit_code = 2
        output = f"could not run guard {control.guard}: {exc}"
    return ControlOutcome(
        control_id=control.id,
        guard=control.guard,
        args=list(control.args),
        exit_code=exit_code,
        verdict=from_exit_code(exit_code),
        expected=control.expect,
        output=output.strip(),
    )


def run_controls(
    controls: Sequence[NegativeControl], timeout: int = 30, cwd: Optional[str] = None
) -> List[ControlOutcome]:
    return [run_control(c, timeout=timeout, cwd=cwd) for c in controls]


def all_passed(outcomes: Iterable[ControlOutcome]) -> bool:
    return all(o.passed for o in outcomes)


def write_report(outcomes: Sequence[ControlOutcome], report_path: str) -> None:
    """Write the blockproofs of every control run to a JSON report."""
    payload = {
        "result": "PASS" if all_passed(outcomes) else "FAIL",
        "controls": [o.blockproof() for o in outcomes],
    }
    with open(report_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
        fh.write("\n")


def controls_as_manifest(controls: Sequence[NegativeControl]) -> Dict[str, Any]:
    """Round-trip helper: serialize controls back to a manifest dict."""
    return {
        "controls": [
            {
                "id": c.id,
                "guard": c.guard,
                "args": list(c.args),
                "expect": c.expect.value,
                "description": c.description,
            }
            for c in controls
        ]
    }


def exit_code_for_outcomes(outcomes: Sequence[ControlOutcome]) -> int:
    """Gate exit code for a control run: 0 when every control passed, else 1."""
    return 0 if all_passed(outcomes) else 1
