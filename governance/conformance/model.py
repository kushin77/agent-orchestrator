"""Conformance domain model: the class ladder, findings and reports (issue #140).

The model keeps three ideas separate, because conflating them is how a quality
gate turns into a formality:

* **required** — metadata every in-scope item must declare. Missing it is an
  error: an unclassified item cannot be held to any standard at all.
* **expectations** — what a *declared* rung additionally implies. Missing it is a
  deviation: real, reported, and assignable, but not fatal to the whole gate.
* **mandates** — cross-cutting rules that apply at every rung (the IaC mandate).

Findings carry a stable code and a severity so a reader can tell an enforcement
failure from a reported deviation without reading prose.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, List, Mapping, Optional, Sequence, Tuple

SCHEMA_ID = "cmr.conformance/report-v1"

SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"

# -- finding codes (each is driven deliberately by the tests) -----------------
CODE_CLASS_MISSING = "class-missing"
CODE_CLASS_UNKNOWN = "class-unknown"
CODE_CLASS_AMBIGUOUS = "class-ambiguous"
CODE_CLASSIFICATION_INCOMPLETE = "classification-incomplete"
CODE_CLASS_EXPECTATION_UNMET = "class-expectation-unmet"
CODE_SCOPE_MISMATCH = "scope-mismatch"
CODE_IAC_MANDATE_UNMET = "iac-mandate-unmet"
CODE_POLICY_INVALID = "policy-invalid"
CODE_DEPENDENCY_MISSING = "dependency-missing"

MANDATE_IAC = "iac"


@dataclass(frozen=True)
class Finding:
    """One conformance finding about an item, a change set or the policy itself."""

    code: str
    message: str
    severity: str = SEVERITY_ERROR
    subject: str = ""  # e.g. "issue-140" or a file path
    remediation: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "subject": self.subject,
            "message": self.message,
            "remediation": self.remediation,
        }


def errors(findings: Sequence[Finding]) -> Tuple[Finding, ...]:
    return tuple(f for f in findings if f.severity == SEVERITY_ERROR)


def warnings(findings: Sequence[Finding]) -> Tuple[Finding, ...]:
    return tuple(f for f in findings if f.severity == SEVERITY_WARNING)


@dataclass(frozen=True)
class Policy:
    """The declared conformance policy (loaded from ``policy.yaml``)."""

    ladder: Tuple[str, ...]
    required: Tuple[str, ...]
    expectations: Mapping[str, Tuple[str, ...]]
    prefixed: Tuple[str, ...]
    infra_paths: Tuple[str, ...]
    strictable: bool = True

    @property
    def allowed_classes(self) -> FrozenSet[str]:
        return frozenset(self.ladder)

    def rank(self, name: str) -> int:
        """Position on the ladder; ``-1`` when the name is not a rung."""
        try:
            return self.ladder.index(name)
        except ValueError:
            return -1

    def at_least(self, name: str, rung: str) -> bool:
        return self.rank(name) >= self.rank(rung)

    def expectations_for(self, name: str) -> Tuple[str, ...]:
        return tuple(self.expectations.get(name, ()))

    def as_dict(self) -> Dict[str, Any]:
        return {
            "ladder": list(self.ladder),
            "required": list(self.required),
            "expectations": {k: list(v) for k, v in self.expectations.items()},
            "prefixed": list(self.prefixed),
            "infra_paths": list(self.infra_paths),
        }


@dataclass(frozen=True)
class Classified:
    """An issue's declared classification, parsed from its labels."""

    issue: str
    title: str
    milestone: str
    classes: Tuple[str, ...]
    labels: Tuple[str, ...]

    @property
    def declared(self) -> str:
        return self.classes[0] if self.classes else ""

    def has(self, name: str) -> bool:
        return any(label.startswith(name + ":") for label in self.labels)

    def value_of(self, name: str) -> str:
        for label in self.labels:
            if label.startswith(name + ":"):
                return label.split(":", 1)[1]
        return ""


@dataclass
class ConformanceReport:
    """The result of one conformance run."""

    generated_at: str
    scope: str
    scanned: int = 0
    findings: List[Finding] = field(default_factory=list)
    class_counts: Mapping[str, int] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "schema": SCHEMA_ID,
            "generated_at": self.generated_at,
            "scope": self.scope,
            "scanned": self.scanned,
            "class_counts": dict(self.class_counts),
            "error_count": len(errors(self.findings)),
            "warning_count": len(warnings(self.findings)),
            "findings": [f.as_dict() for f in self.findings],
        }

    @property
    def conformant(self) -> bool:
        return not errors(self.findings)
