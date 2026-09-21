"""Conformance domain model: the class ladder, findings and reports (issue #140).

---knowledge---
module_id: governance.conformance.model
system: governance
app: conformance
solution_class: pattern
patterns: []
derives_from: null
owner_sme: qa-sme
tier: L1
interfaces: [Finding, errors, warnings, Policy, Classified, ConformanceReport]
invariants: ""
gotchas: ""
related: ["#140", "#174", "#320", "#517", "#1182"]
do_not_duplicate: null
---knowledge---

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

# -- filing-path codes (issue #320) -------------------------------------------
# A filing that cannot derive its declaring labels is REFUSED before `gh` runs,
# so these codes describe a filing *seam* defect, not a board defect: they are
# raised before an issue can exist, which is the point (prevention, not repair —
# repairing the legacy unclassified issues is #174's job).
CODE_FILING_REFUSED = "filing-refused"
CODE_FILING_SEAM_BYPASSED = "filing-seam-bypassed"

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
    # The `filing` block: what a NEW issue derives when the filing path does not
    # declare it. Empty means nothing is derivable, and a filing that declares no
    # class is then refused rather than filed unclassified (issue #320).
    filing_default_class: str = ""
    filing_defaults: Mapping[str, str] = field(default_factory=dict)
    # The tag dimensions a filing must also derive, in declaration order. Their
    # VALUES are the tag authority's (issue #1182); this field is only the LIST
    # of dimensions, so the filing seam derives them without re-declaring the
    # vocabulary.
    filing_tags: Tuple[str, ...] = ()
    strictable: bool = True

    @property
    def allowed_classes(self) -> FrozenSet[str]:
        return frozenset(self.ladder)

    @property
    def declaring_fields(self) -> Tuple[str, ...]:
        """Every name this policy recognises as a ``name:value`` declaring label.

        The union of ``class``, the ``required`` companions, every name any rung's
        ``expectations`` adds, and the ``prefixed`` vocabulary — in policy order, so
        a caller can depend on the order. It is the criterion the filing path uses
        to decide whether a declared label is recognised: a name the policy does not
        list here is not a declaring label, and `--declare` REFUSES it by name
        rather than dropping it (issue #517 — an ignored declaration is silent
        board-metadata loss).
        """
        names = ["class"]
        for name in self.required:
            if name not in names:
                names.append(name)
        for rung in self.ladder:
            for name in self.expectations_for(rung):
                if name not in names:
                    names.append(name)
        for name in self.prefixed:
            if name not in names:
                names.append(name)
        return tuple(names)

    def declares(self, name: str) -> bool:
        """Whether ``name`` is a declaring label this policy recognises."""
        return name in self.declaring_fields

    def filing_label_names(self, declared_class: str) -> Tuple[str, ...]:
        """Every label a filing must carry, in declaration order.

        `class` first, then the policy's `required` companions (`type`,
        `priority`, `area`), then the expectations the declared class adds
        (`gdc` at enterprise, `gdc`+`pillar` at elite). Deriving the set here —
        rather than listing labels in the caller — is what makes "no path can file
        an unclassified issue" a property of the policy instead of a convention.
        """
        names = ["class"]
        for name in self.required:
            if name not in names:
                names.append(name)
        for name in self.expectations_for(declared_class):
            if name not in names:
                names.append(name)
        for name in self.filing_tags:
            if name not in names:
                names.append(name)
        return tuple(names)

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
            "declaring_fields": list(self.declaring_fields),
            "infra_paths": list(self.infra_paths),
            "filing": {
                "default_class": self.filing_default_class,
                "defaults": dict(self.filing_defaults),
                "tags": list(self.filing_tags),
            },
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
