"""The offline chat-quality eval harness (issue #509).

    python3 -m registry.chat.eval.harness --cases registry/chat/eval/cases.yaml

For every case the harness resolves the module version the fixture declares,
drives the deterministic stand-ins (:mod:`registry.chat.eval.standins`), and
checks the response against the **declared** expectation: the outcome, the
module that answered, the module's own contract (does its schema require the
citations envelope?), the response against that schema, the citation
expectation, and the reason code. A check that does not hold fails the case *by
name* — the whole point of a regression gate is that it names what regressed.

Exit-code contract (honesty tri-state, AO-GR-19):

* ``0`` — every case was run and met its declared expectation;
* ``1`` — at least one case was run and failed its declared expectation;
* ``2`` — no case failed, but at least one could not be run (``CANNOT_ASSESS``).

A case the harness could not run is never reported as a pass: an unresolvable
module version, an unreadable case, a malformed fragment or an outcome outside
the declared vocabulary is ``CANNOT_ASSESS`` and says why.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import jsonschema
import yaml

from .. import envelope, labels
from ..prompt_modules import ChatPromptError, ChatPromptRegistry
from . import standins

#: The fixture-set schema marker.
CASES_SCHEMA = "chat-eval-cases/v1"

#: Where the committed fixture set lives (the CLI's default).
DEFAULT_CASES = Path(__file__).resolve().parent / "cases.yaml"

PASS = "PASS"
FAIL = "FAIL"
CANNOT_ASSESS = "CANNOT_ASSESS"

#: The checks every runnable case is judged by, in report order.
CHECK_NAMES = ("outcome", "module", "contract", "schema", "citations", "reason_code")


class EvalFixtureError(Exception):
    """Raised when the fixture set itself cannot be read or parsed."""


@dataclass(frozen=True)
class CaseCheck:
    """One judged property of one case."""

    name: str
    ok: bool
    detail: str = ""


@dataclass
class CaseResult:
    """The verdict for one case."""

    case_id: str
    status: str
    checks: List[CaseCheck] = field(default_factory=list)
    detail: str = ""
    prompt_id: str = ""

    @property
    def failed_checks(self) -> Tuple[str, ...]:
        return tuple(check.name for check in self.checks if not check.ok)

    def summary(self) -> str:
        if self.status == CANNOT_ASSESS:
            return f"CANNOT-ASSESS {self.case_id} — {self.detail}"
        judged = ", ".join(
            check.name if check.ok else f"{check.name}({check.detail})"
            for check in self.checks
        )
        return f"{self.status} {self.case_id} ({self.prompt_id}) checks: {judged}"


@dataclass
class EvalReport:
    """The verdict for a whole fixture set."""

    cases_path: Path
    package_root: Path
    fixture_digest: str
    results: List[CaseResult] = field(default_factory=list)

    @property
    def passed(self) -> Tuple[str, ...]:
        return tuple(r.case_id for r in self.results if r.status == PASS)

    @property
    def failed(self) -> Tuple[str, ...]:
        return tuple(r.case_id for r in self.results if r.status == FAIL)

    @property
    def unassessable(self) -> Tuple[str, ...]:
        return tuple(r.case_id for r in self.results if r.status == CANNOT_ASSESS)

    @property
    def exit_code(self) -> int:
        if self.failed:
            return 1
        if self.unassessable:
            return 2
        return 0

    def render(self) -> str:
        lines = [
            f"chat-eval: fixture {self.cases_path} "
            f"(sha256:{self.fixture_digest}) root {self.package_root}",
        ]
        for result in self.results:
            lines.append(f"  {result.summary()}")
        total = len(self.results)
        if self.exit_code == 0:
            lines.append(
                f"chat-eval: OK — {len(self.passed)}/{total} case(s) met their declared "
                "expectation, 0 cannot assess"
            )
        elif self.exit_code == 1:
            lines.append(
                f"chat-eval: NOT-OK — {len(self.failed)}/{total} case(s) failed their "
                f"declared expectation: {', '.join(self.failed)}"
            )
            if self.unassessable:
                lines.append(
                    f"chat-eval: also CANNOT-ASSESS for {', '.join(self.unassessable)} "
                    "(reported, never counted as a pass)"
                )
        else:
            lines.append(
                f"chat-eval: CANNOT-ASSESS — {len(self.unassessable)} case(s) could not "
                f"be run and no case failed: {', '.join(self.unassessable)}"
            )
        return "\n".join(lines)

    def projection(self) -> Dict[str, Any]:
        """The machine-readable verdict (what the promotion gate consumes)."""
        return {
            "schema": "chat-eval-report/v1",
            "cases": str(self.cases_path),
            "fixture_digest": self.fixture_digest,
            "exit_code": self.exit_code,
            "passed": list(self.passed),
            "failed": list(self.failed),
            "cannot_assess": list(self.unassessable),
            "results": [
                {
                    "case": result.case_id,
                    "status": result.status,
                    "prompt_id": result.prompt_id,
                    "detail": result.detail,
                    "checks": [
                        {"name": check.name, "ok": check.ok, "detail": check.detail}
                        for check in result.checks
                    ],
                }
                for result in self.results
            ],
        }


def load_cases(path: Path) -> Dict[str, Any]:
    """Read and shape-check the fixture set (the set, not the individual cases)."""
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise EvalFixtureError(f"cannot read {path}: {exc}") from exc
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise EvalFixtureError(f"{path} is not valid YAML: {exc}") from exc
    if not isinstance(data, Mapping) or data.get("schema") != CASES_SCHEMA:
        raise EvalFixtureError(f"{path} is not a {CASES_SCHEMA} document")
    cases = data.get("cases")
    if not isinstance(cases, Sequence) or isinstance(cases, (str, bytes)) or not cases:
        raise EvalFixtureError(f"{path} declares no cases")
    return dict(data)


def _unrunnable(case: Mapping[str, Any], registry: ChatPromptRegistry) -> Optional[str]:
    """Why this case cannot be run at all — ``None`` when it can."""
    if not isinstance(case, Mapping):
        return "the case is not a mapping"
    case_id = case.get("id")
    if not isinstance(case_id, str) or not case_id.strip():
        return "the case has no id"
    for key in ("question", "tenant"):
        value = case.get(key)
        if not isinstance(value, str) or not value.strip():
            return f"the case has no {key}"
    context = case.get("context", [])
    if not isinstance(context, Sequence) or isinstance(context, (str, bytes)):
        return "the case's context is not a list"
    for index, fragment in enumerate(context):
        if not isinstance(fragment, Mapping):
            return f"fragment {index} is not a mapping"
        for key in ("fragment_id", "source_id", "text"):
            value = fragment.get(key)
            if not isinstance(value, str) or not value.strip():
                return f"fragment {index} has no {key}"
        if not envelope.SOURCE_ID_PATTERN.match(str(fragment["source_id"])):
            return f"fragment {index} has a malformed source_id {fragment['source_id']!r}"
    expect = case.get("expect")
    if not isinstance(expect, Mapping):
        return "the case declares no expectation"
    task_type = expect.get("module")
    version = expect.get("version")
    if not isinstance(task_type, str) or not isinstance(version, str):
        return "the case does not name the module (module + version) that must answer"
    outcome = expect.get("outcome")
    if outcome not in labels.OUTCOMES:
        return f"declared outcome {outcome!r} is not in the declared vocabulary"
    try:
        registry.resolve(task_type, version)
    except ChatPromptError as exc:
        return f"module {task_type}@{version} is not resolvable: {exc}"
    return None


def _check_outcome(response: standins.Response, expect: Mapping[str, Any]) -> CaseCheck:
    declared = str(expect.get("outcome"))
    ok = response.outcome == declared
    return CaseCheck(
        "outcome",
        ok,
        "" if ok else f"declared {declared}, got {response.outcome}",
    )


def _check_module(
    response: standins.Response,
    expect: Mapping[str, Any],
    versions: Mapping[str, str],
) -> CaseCheck:
    declared_task = str(expect.get("module"))
    declared_version = str(expect.get("version"))
    if response.module.task_type != declared_task:
        return CaseCheck(
            "module",
            False,
            f"declared {declared_task}, answered by {response.module.task_type}",
        )
    if declared_task in versions:
        return CaseCheck(
            "module",
            True,
            f"version {declared_version} substituted by {response.module.version}",
        )
    ok = response.module.version == declared_version
    return CaseCheck(
        "module",
        ok,
        "" if ok else f"declared {declared_version}, answered by {response.module.version}",
    )


def _check_contract(
    response: standins.Response, registry: ChatPromptRegistry
) -> CaseCheck:
    violations = registry.check_module_contract(response.module)
    if violations:
        return CaseCheck("contract", False, "; ".join(violations))
    return CaseCheck("contract", True)


def _check_schema(response: standins.Response) -> CaseCheck:
    schema = response.module.output_schema()
    validator = jsonschema.Draft7Validator(schema)
    errors = sorted(validator.iter_errors(dict(response.document)), key=lambda e: e.path)
    if not errors:
        return CaseCheck("schema", True)
    first = errors[0]
    return CaseCheck("schema", False, f"{first.message} at {list(first.path)}")


def _check_citations(
    case: Mapping[str, Any], response: standins.Response, expect: Mapping[str, Any]
) -> CaseCheck:
    declared = str(expect.get("citations", "required"))
    supplied = standins.supplied_source_ids(case)
    if declared == "required":
        if not response.citations:
            return CaseCheck(
                "citations", False, "the fixture requires citations, the answer carried none"
            )
        invented = envelope.fabricated(response.citations, supplied)
        if invented:
            return CaseCheck(
                "citations",
                False,
                f"fabricated source_id(s) never supplied to the turn: {invented}",
            )
        return CaseCheck("citations", True)
    if declared == "empty":
        if response.citations:
            return CaseCheck(
                "citations",
                False,
                f"the fixture requires an empty envelope, got {list(response.cited_source_ids)}",
            )
        return CaseCheck("citations", True)
    return CaseCheck("citations", False, f"unknown citations expectation {declared!r}")


def _check_reason_code(response: standins.Response, expect: Mapping[str, Any]) -> CaseCheck:
    if "reason_code" not in expect:
        return CaseCheck("reason_code", True)
    declared = expect.get("reason_code")
    ok = declared == response.reason_code
    return CaseCheck(
        "reason_code",
        ok,
        "" if ok else f"declared {declared!r}, got {response.reason_code!r}",
    )


def run_case(
    case: Mapping[str, Any],
    registry: ChatPromptRegistry,
    versions: Optional[Mapping[str, str]] = None,
) -> CaseResult:
    """Judge one case against its declared expectation."""
    case_id = str(case.get("id", "<unnamed>"))
    reason = _unrunnable(case, registry)
    if reason is not None:
        return CaseResult(case_id=case_id, status=CANNOT_ASSESS, detail=reason)

    overrides = dict(versions or {})
    expect = case["expect"]
    try:
        response = standins.respond(case, registry, overrides)
    except ChatPromptError as exc:
        return CaseResult(
            case_id=case_id,
            status=CANNOT_ASSESS,
            detail=f"the turn could not be run with the module under evaluation: {exc}",
        )
    checks = [
        _check_outcome(response, expect),
        _check_module(response, expect, overrides),
        _check_contract(response, registry),
        _check_schema(response),
        _check_citations(case, response, expect),
        _check_reason_code(response, expect),
    ]
    status = PASS if all(check.ok for check in checks) else FAIL
    return CaseResult(
        case_id=case_id,
        status=status,
        checks=checks,
        prompt_id=response.module.prompt_id,
    )


def evaluate(
    cases_path: Optional[Path] = None,
    root: Optional[Path] = None,
    versions: Optional[Mapping[str, str]] = None,
) -> EvalReport:
    """Run the whole fixture set, in fixture order, deterministically."""
    path = Path(cases_path) if cases_path is not None else DEFAULT_CASES
    data = load_cases(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    registry = ChatPromptRegistry(root)
    results = [
        run_case(case, registry, versions) for case in data["cases"]
    ]
    return EvalReport(
        cases_path=path,
        package_root=registry.root,
        fixture_digest=digest,
        results=results,
    )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="chat-eval",
        description="Offline, deterministic chat-quality evaluation (issue #509)",
    )
    parser.add_argument("--cases", default=str(DEFAULT_CASES), help="fixture set path")
    parser.add_argument("--root", default=None, help="chat package root override")
    parser.add_argument("--json", action="store_true", help="emit the machine-readable verdict")
    args = parser.parse_args(argv)
    try:
        report = evaluate(Path(args.cases), args.root)
    except EvalFixtureError as exc:
        print(f"chat-eval: CANNOT-ASSESS — {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report.projection(), indent=2, sort_keys=True))
    else:
        print(report.render())
    return report.exit_code


if __name__ == "__main__":
    sys.exit(main())
