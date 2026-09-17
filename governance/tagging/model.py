"""Tag authority domain model (issue #1175).

The model keeps three ideas apart, because collapsing them is how a tagging
system becomes a label list nobody reads:

* **the taxonomy** — what a tag may BE. A closed vocabulary per dimension, and
  for every dimension whose vocabulary already has an authority in this repo, a
  *borrow* of that authority rather than a second copy of it.
* **the rules** — what a tag IMPLIES. A tag set derives the gates that tag set
  requires, along the channel they run in (pr / ci / cd / ops). Gate names are
  resolvable, not prose, so a plan that describes a gate which no longer exists
  is refused by name.
* **the findings** — every judgement carries a stable code and a severity, so a
  reader can tell an enforcement failure from a reported deviation without
  reading prose. The codes are the `refusals` this taxonomy declares; a refusal
  declared but never raised is itself a defect, which the gate provokes.

Offline by construction: this module reads files in the tree and never the
network, so it is a gate and not a report.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

SCHEMA_ID = "ao.tagging/report-v1"
TAXONOMY_SCHEMA = "ao.tagging/taxonomy-v1"
RULES_SCHEMA = "ao.tagging/rules-v1"

SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"

# -- finding codes. These ARE the refusal ids declared in taxonomy.yaml, and the
#    gate asserts the two sets are equal: a refusal nobody raises is a formality
#    (GR-12), and a code the taxonomy does not declare is undocumented behaviour.
CODE_UNKNOWN_DIMENSION = "unknown-dimension"
CODE_UNKNOWN_VALUE = "unknown-value"
CODE_VALUE_PATTERN = "value-pattern"
CODE_VOCABULARY_DRIFT = "vocabulary-drift"
CODE_NAME_AUTHORITY_DRIFT = "name-authority-drift"
CODE_POSTURE_CONTRADICTION = "posture-contradiction"
CODE_REQUIRED_MISSING = "required-missing"
CODE_UNKNOWN_TARGET = "unknown-target"
CODE_UNKNOWN_GATE = "unknown-gate"
CODE_RULE_UNKNOWN_DIMENSION = "rule-unknown-dimension"
CODE_FINOPS_FLOOR_UNMET = "finops-floor-unmet"
# The mandate (issue #1183): the tag authority is constitutional only while the
# contract documents declare it, so the declaration is itself gated.
CODE_MANDATE_MISSING_DOC = "mandate-missing-doc"
CODE_MANDATE_MISSING_MARKER = "mandate-missing-marker"

# Additional codes this module raises about the taxonomy's own shape. They are
# not tag refusals — they mean the authority file is malformed, which is a
# CANNOT-ASSESS for every tag set built on it.
CODE_TAXONOMY_INVALID = "taxonomy-invalid"
CODE_RULES_INVALID = "rules-invalid"
CODE_MATRIX_STALE = "matrix-stale"

# Every code this module can raise as an ENFORCEMENT failure. The gate asserts
# this tuple equals the `refusals` ids declared in taxonomy.yaml, so a refusal
# declared but never raised (a formality) fails, and so does a code raised but
# never documented.
REFUSAL_CODES = (
    CODE_UNKNOWN_DIMENSION,
    CODE_UNKNOWN_VALUE,
    CODE_VALUE_PATTERN,
    CODE_VOCABULARY_DRIFT,
    CODE_NAME_AUTHORITY_DRIFT,
    CODE_POSTURE_CONTRADICTION,
    CODE_REQUIRED_MISSING,
    CODE_UNKNOWN_TARGET,
    CODE_UNKNOWN_GATE,
    CODE_RULE_UNKNOWN_DIMENSION,
    CODE_FINOPS_FLOOR_UNMET,
    CODE_MANDATE_MISSING_DOC,
    CODE_MANDATE_MISSING_MARKER,
)

KIND_BORROWED = "borrowed"
KIND_CLOSED = "closed"
KIND_PATTERN = "pattern"
KINDS = (KIND_BORROWED, KIND_CLOSED, KIND_PATTERN)

CHANNELS = ("pr", "ci", "cd", "ops")


class TaggingUnavailable(Exception):
    """The authority could not be read. Callers map this to CANNOT-ASSESS."""


# ---------------------------------------------------------------------------
# findings
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Finding:
    """One judgement about a tag set, a rule, or the taxonomy itself."""

    code: str
    message: str
    severity: str = SEVERITY_ERROR
    subject: str = ""
    remediation: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "subject": self.subject,
            "message": self.message,
            "remediation": self.remediation,
        }


def errors(findings: Iterable[Finding]) -> Tuple[Finding, ...]:
    return tuple(f for f in findings if f.severity == SEVERITY_ERROR)


def warnings(findings: Iterable[Finding]) -> Tuple[Finding, ...]:
    return tuple(f for f in findings if f.severity == SEVERITY_WARNING)


# ---------------------------------------------------------------------------
# document loading
# ---------------------------------------------------------------------------
def _load_doc(path: Path) -> Any:
    """Load a YAML or JSON document. Raises TaggingUnavailable when unreadable."""
    if not path.is_file():
        raise TaggingUnavailable("no such file: %s" % path)
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".json":
        try:
            return json.loads(text)
        except ValueError as exc:
            raise TaggingUnavailable("%s is not valid JSON: %s" % (path, exc))
    try:
        import yaml  # type: ignore
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise TaggingUnavailable("PyYAML not installed (%s)" % exc)
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise TaggingUnavailable("%s is not valid YAML: %s" % (path, exc))


def resolve_pointer(doc: Any, pointer: str) -> Any:
    """Resolve a dotted pointer such as ``vocabulary.tiers`` or ``ladder``.

    Absent keys raise ``KeyError`` so a drifted authority path is a loud
    CANNOT-ASSESS rather than a silently empty vocabulary — an empty borrowed set
    would make every value look legal.
    """
    node = doc
    if not pointer:
        return node
    for part in pointer.split("."):
        if isinstance(node, Mapping):
            if part not in node:
                raise KeyError(pointer)
            node = node[part]
        elif isinstance(node, (list, tuple)):
            node = node[int(part)]
        else:
            raise KeyError(pointer)
    return node


def _as_str_tuple(value: Any) -> Tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)):
        return tuple(str(v) for v in value)
    raise ValueError("expected a string or a list of strings, got %r" % (value,))


# ---------------------------------------------------------------------------
# the taxonomy
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Anchor:
    """A pointer into another authority file in this repo."""

    path: str
    pointer: str
    drift: str = "exact-set"


@dataclass(frozen=True)
class Dimension:
    name: str
    kind: str
    multi: bool = False
    applies_to: Tuple[str, ...] = ()
    values: Tuple[str, ...] = ()
    pattern: str = ""
    ordered: bool = False
    description: str = ""
    borrowed_from: Optional[Anchor] = None
    name_authority: Optional[Anchor] = None
    mutually_exclusive: Tuple[Tuple[str, ...], ...] = ()

    def rank(self, value: str) -> int:
        try:
            return self.values.index(value)
        except ValueError:
            return -1

    @property
    def label_prefix(self) -> str:
        return self.name + ":"


@dataclass(frozen=True)
class Taxonomy:
    path: str
    targets: Tuple[str, ...]
    required: Mapping[str, Tuple[str, ...]]
    recommended: Mapping[str, Tuple[str, ...]]
    dimensions: Mapping[str, Dimension]
    refusal_ids: Tuple[str, ...]

    def required_for(self, target: str) -> Tuple[str, ...]:
        return tuple(self.required.get(target, ()))

    def recommended_for(self, target: str) -> Tuple[str, ...]:
        return tuple(self.recommended.get(target, ()))

    def class_ladder(self) -> Tuple[str, ...]:
        dim = self.dimensions.get("class")
        return dim.values if dim else ()


def load_taxonomy(path: Path) -> Taxonomy:
    raw = _load_doc(path)
    if not isinstance(raw, Mapping):
        raise TaggingUnavailable("%s does not contain a mapping" % path)
    if raw.get("schema") != TAXONOMY_SCHEMA:
        raise TaggingUnavailable(
            "%s declares schema %r, expected %r"
            % (path, raw.get("schema"), TAXONOMY_SCHEMA)
        )

    targets = _as_str_tuple(raw.get("targets"))
    dimensions: Dict[str, Dimension] = {}
    for name, body in (raw.get("dimensions") or {}).items():
        if not isinstance(body, Mapping):
            raise TaggingUnavailable("dimension %r is not a mapping" % name)
        anchor_raw = body.get("borrowed_from")
        anchor = None
        if isinstance(anchor_raw, Mapping):
            anchor = Anchor(
                path=str(anchor_raw.get("path", "")),
                pointer=str(anchor_raw.get("pointer", "")),
                drift=str(anchor_raw.get("drift", "exact-set")),
            )
        name_anchor_raw = body.get("name_authority")
        name_anchor = None
        if isinstance(name_anchor_raw, Mapping):
            name_anchor = Anchor(
                path=str(name_anchor_raw.get("path", "")),
                pointer=str(name_anchor_raw.get("pointer", "")),
                drift=str(name_anchor_raw.get("drift", "present")),
            )
        exclusives = tuple(
            _as_str_tuple(group) for group in (body.get("mutually_exclusive") or ())
        )
        dimensions[str(name)] = Dimension(
            name=str(name),
            kind=str(body.get("kind", "")),
            multi=bool(body.get("multi", False)),
            applies_to=_as_str_tuple(body.get("applies_to")),
            values=_as_str_tuple(body.get("values")),
            pattern=str(body.get("pattern", "")),
            ordered=bool(body.get("ordered", False)),
            description=str(body.get("description", "")),
            borrowed_from=anchor,
            name_authority=name_anchor,
            mutually_exclusive=exclusives,
        )

    refusals = tuple(
        str(entry.get("id", "")) for entry in (raw.get("refusals") or ())
    )

    def _map(key: str) -> Dict[str, Tuple[str, ...]]:
        body = raw.get(key) or {}
        if not isinstance(body, Mapping):
            raise TaggingUnavailable("%s.%s is not a mapping" % (path, key))
        return {str(k): _as_str_tuple(v) for k, v in body.items()}

    return Taxonomy(
        path=str(path),
        targets=targets,
        required=_map("required"),
        recommended=_map("recommended"),
        dimensions=dimensions,
        refusal_ids=refusals,
    )


def lint_taxonomy(taxonomy: Taxonomy) -> List[Finding]:
    """Self-consistency of the authority: is it a document that can mean anything?"""
    findings: List[Finding] = []
    for name, dim in sorted(taxonomy.dimensions.items()):
        subject = "dimension:%s" % name
        if dim.kind not in KINDS:
            findings.append(
                Finding(
                    CODE_TAXONOMY_INVALID,
                    "dimension %r declares kind %r, not one of %s"
                    % (name, dim.kind, ", ".join(KINDS)),
                    subject=subject,
                    remediation="use kind: %s" % " | ".join(KINDS),
                )
            )
        if dim.kind == KIND_BORROWED and dim.borrowed_from is None:
            findings.append(
                Finding(
                    CODE_TAXONOMY_INVALID,
                    "borrowed dimension %r declares no borrowed_from anchor" % name,
                    subject=subject,
                    remediation="name the authority path and pointer",
                )
            )
        if dim.kind in (KIND_CLOSED, KIND_BORROWED) and not dim.values:
            findings.append(
                Finding(
                    CODE_TAXONOMY_INVALID,
                    "dimension %r declares no values" % name,
                    subject=subject,
                    remediation=(
                        "declare values, or use kind: pattern / kind: borrowed"
                    ),
                )
            )
        if dim.kind == KIND_PATTERN and not dim.pattern:
            findings.append(
                Finding(
                    CODE_TAXONOMY_INVALID,
                    "pattern dimension %r declares no pattern" % name,
                    subject=subject,
                )
            )
        if dim.kind == KIND_PATTERN and dim.pattern:
            try:
                re.compile(dim.pattern)
            except re.error as exc:
                findings.append(
                    Finding(
                        CODE_TAXONOMY_INVALID,
                        "pattern dimension %r has an uncompilable pattern: %s"
                        % (name, exc),
                        subject=subject,
                    )
                )
        for target in dim.applies_to:
            if target not in taxonomy.targets:
                findings.append(
                    Finding(
                        CODE_TAXONOMY_INVALID,
                        "dimension %r applies_to %r, which is not a declared target"
                        % (name, target),
                        subject=subject,
                        remediation="declare the target in targets: or drop it",
                    )
                )
        for group in dim.mutually_exclusive:
            for value in group:
                if value not in dim.values:
                    findings.append(
                        Finding(
                            CODE_TAXONOMY_INVALID,
                            "dimension %r excludes %r, which is not a declared value"
                            % (name, value),
                            subject=subject,
                        )
                    )
        if len(set(dim.values)) != len(dim.values):
            findings.append(
                Finding(
                    CODE_TAXONOMY_INVALID,
                    "dimension %r declares a duplicate value" % name,
                    subject=subject,
                )
            )

    for key, table in (("required", taxonomy.required), ("recommended", taxonomy.recommended)):
        for target, names in sorted(table.items()):
            if target not in taxonomy.targets:
                findings.append(
                    Finding(
                        CODE_TAXONOMY_INVALID,
                        "%s.%s names an undeclared target" % (key, target),
                        subject="target:%s" % target,
                    )
                )
            for name in names:
                if name not in taxonomy.dimensions:
                    findings.append(
                        Finding(
                            CODE_TAXONOMY_INVALID,
                            "%s.%s names undeclared dimension %r"
                            % (key, target, name),
                            subject="target:%s" % target,
                        )
                    )
    return findings


def drift(taxonomy: Taxonomy, root: Path) -> List[Finding]:
    """Prove every borrowed vocabulary still EQUALS its authority's.

    This is the half that makes the taxonomy an authority rather than a second
    opinion: a lane cannot mint a rung, a tier or a role by editing one side only.
    """
    findings: List[Finding] = []
    for name, dim in sorted(taxonomy.dimensions.items()):
        subject = "dimension:%s" % name
        if dim.borrowed_from is not None:
            anchor = dim.borrowed_from
            authority = root / anchor.path
            try:
                source = resolve_pointer(_load_doc(authority), anchor.pointer)
                source_set = set(_as_str_tuple(source))
            except (TaggingUnavailable, KeyError, ValueError, IndexError) as exc:
                findings.append(
                    Finding(
                        CODE_VOCABULARY_DRIFT,
                        "dimension %r borrows %s -> %s, which is unreadable (%s)"
                        % (name, anchor.path, anchor.pointer, exc),
                        subject=subject,
                        remediation="fix the anchor, or declare the values here",
                    )
                )
                continue
            declared = set(dim.values)
            if declared != source_set:
                extra = sorted(declared - source_set)
                missing = sorted(source_set - declared)
                parts = []
                if extra:
                    parts.append("not in the authority: %s" % ", ".join(extra))
                if missing:
                    parts.append("missing from the taxonomy: %s" % ", ".join(missing))
                findings.append(
                    Finding(
                        CODE_VOCABULARY_DRIFT,
                        "dimension %r has drifted from %s (%s)"
                        % (name, anchor.path, "; ".join(parts)),
                        subject=subject,
                        remediation=(
                            "make the two sets equal — the taxonomy must not mint a "
                            "value the authority does not carry"
                        ),
                    )
                )
        if dim.name_authority is not None:
            anchor = dim.name_authority
            authority = root / anchor.path
            try:
                names = set(_as_str_tuple(resolve_pointer(_load_doc(authority), anchor.pointer)))
            except (TaggingUnavailable, KeyError, ValueError, IndexError) as exc:
                findings.append(
                    Finding(
                        CODE_NAME_AUTHORITY_DRIFT,
                        "dimension %r anchors its name to %s -> %s, unreadable (%s)"
                        % (name, anchor.path, anchor.pointer, exc),
                        subject=subject,
                    )
                )
                continue
            if dim.name not in names:
                findings.append(
                    Finding(
                        CODE_NAME_AUTHORITY_DRIFT,
                        "dimension %r is no longer declared by %s (%s)"
                        % (name, anchor.path, anchor.pointer),
                        subject=subject,
                        remediation="the label name was retired — update the anchor",
                    )
                )
    return findings


# ---------------------------------------------------------------------------
# the rules
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class GateRef:
    gate: str
    channel: str = "ci"


@dataclass(frozen=True)
class Rule:
    id: str
    description: str
    when: Mapping[str, Any]
    gates: Tuple[GateRef, ...]
    finops_floor: str = ""
    require_declaration: str = ""
    forbid_markers: Tuple[str, ...] = ()


@dataclass(frozen=True)
class Rules:
    path: str
    baseline: Tuple[GateRef, ...]
    rules: Tuple[Rule, ...]

    def by_id(self, rule_id: str) -> Optional[Rule]:
        for rule in self.rules:
            if rule.id == rule_id:
                return rule
        return None


def _gate_refs(raw: Any) -> Tuple[GateRef, ...]:
    refs: List[GateRef] = []
    for entry in raw or ():
        if isinstance(entry, Mapping):
            refs.append(
                GateRef(
                    gate=str(entry.get("gate", "")),
                    channel=str(entry.get("channel", "ci")),
                )
            )
        else:
            refs.append(GateRef(gate=str(entry)))
    return tuple(refs)


def load_rules(path: Path) -> Rules:
    raw = _load_doc(path)
    if not isinstance(raw, Mapping):
        raise TaggingUnavailable("%s does not contain a mapping" % path)
    if raw.get("schema") != RULES_SCHEMA:
        raise TaggingUnavailable(
            "%s declares schema %r, expected %r" % (path, raw.get("schema"), RULES_SCHEMA)
        )
    rules: List[Rule] = []
    for entry in raw.get("rules") or ():
        if not isinstance(entry, Mapping):
            raise TaggingUnavailable("a rule entry is not a mapping")
        rules.append(
            Rule(
                id=str(entry.get("id", "")),
                description=str(entry.get("description", "")),
                when=dict(entry.get("when") or {}),
                gates=_gate_refs(entry.get("gates")),
                finops_floor=str(entry.get("finops_floor", "")),
                require_declaration=str(entry.get("require_declaration", "")),
                forbid_markers=_as_str_tuple(entry.get("forbid_markers")),
            )
        )
    return Rules(path=str(path), baseline=_gate_refs(raw.get("baseline")), rules=tuple(rules))


def lint_rules(rules: Rules, taxonomy: Taxonomy) -> List[Finding]:
    """Does every rule name a declared dimension with declared values?"""
    findings: List[Finding] = []
    seen: Dict[str, int] = {}
    for rule in rules.rules:
        subject = "rule:%s" % rule.id
        seen[rule.id] = seen.get(rule.id, 0) + 1
        if not rule.id:
            findings.append(
                Finding(CODE_RULES_INVALID, "a rule declares no id", subject=subject)
            )
            continue
        when = rule.when
        if "dimension" in when:
            name = str(when["dimension"])
            dim = taxonomy.dimensions.get(name)
            if dim is None:
                findings.append(
                    Finding(
                        CODE_RULE_UNKNOWN_DIMENSION,
                        "rule %r keys on undeclared dimension %r" % (rule.id, name),
                        subject=subject,
                        remediation="declare the dimension in taxonomy.yaml",
                    )
                )
                continue
            for value in _as_str_tuple(when.get("includes")):
                legal = value in dim.values
                if not legal and dim.kind == KIND_PATTERN:
                    legal = bool(re.match(dim.pattern, value))
                if not legal:
                    findings.append(
                        Finding(
                            CODE_RULE_UNKNOWN_DIMENSION,
                            "rule %r keys on %s=%r, which %s does not declare"
                            % (rule.id, name, value, name),
                            subject=subject,
                        )
                    )
        elif "class_at_least" in when:
            rung = str(when["class_at_least"])
            if rung not in taxonomy.class_ladder():
                findings.append(
                    Finding(
                        CODE_RULE_UNKNOWN_DIMENSION,
                        "rule %r keys on class_at_least=%r, not a rung of the ladder"
                        % (rule.id, rung),
                        subject=subject,
                    )
                )
        elif when:
            findings.append(
                Finding(
                    CODE_RULES_INVALID,
                    "rule %r has an unrecognised when clause: %s"
                    % (rule.id, ", ".join(sorted(when))),
                    subject=subject,
                )
            )
        for ref in rule.gates:
            if ref.channel not in CHANNELS:
                findings.append(
                    Finding(
                        CODE_RULES_INVALID,
                        "rule %r names channel %r, not one of %s"
                        % (rule.id, ref.channel, ", ".join(CHANNELS)),
                        subject=subject,
                    )
                )
    for rule_id, count in sorted(seen.items()):
        if rule_id and count > 1:
            findings.append(
                Finding(
                    CODE_RULES_INVALID,
                    "rule id %r is declared %d times" % (rule_id, count),
                    subject="rule:%s" % rule_id,
                )
            )
    return findings


def make_targets(makefile: Path) -> Tuple[str, ...]:
    """Every target the Makefile declares, read from the file itself."""
    if not makefile.is_file():
        raise TaggingUnavailable("no Makefile at %s" % makefile)
    names = []
    for line in makefile.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^([A-Za-z0-9][A-Za-z0-9_-]*)\s*:(?!=)", line)
        if match:
            names.append(match.group(1))
    return tuple(sorted(set(names)))


def check_scripts(scripts_dir: Path) -> Tuple[str, ...]:
    """The check names `scripts/verify.sh` discovers (check-X.sh -> X, #698)."""
    if not scripts_dir.is_dir():
        raise TaggingUnavailable("no scripts directory at %s" % scripts_dir)
    names = []
    for path in sorted(scripts_dir.glob("check-*.sh")):
        names.append(path.name[len("check-") : -len(".sh")])

    # The explicit registry is the OTHER half of the check vocabulary: the
    # renamed checks, the `*.py` checks and the pytest suites are declared as
    # `'name|command'` entries in scripts/verify.sh (#698 keeps the array as the
    # source of truth for those). Reading only the discovered file names would
    # make half the gate vocabulary unresolvable — and a rule that cannot resolve
    # its own gate must be refused, never guessed at.
    verify = scripts_dir / "verify.sh"
    if verify.is_file():
        for line in verify.read_text(encoding="utf-8").splitlines():
            match = re.match(r"^\s*'([A-Za-z0-9][A-Za-z0-9_-]*)\|", line)
            if match:
                names.append(match.group(1))
    return tuple(sorted(set(names)))


def lint_gates(rules: Rules, root: Path) -> List[Finding]:
    """Resolve every gate a rule names. A plan naming a dead gate is refused."""
    findings: List[Finding] = []
    targets = set(make_targets(root / "Makefile"))
    checks = set(check_scripts(root / "scripts"))
    refs = list(rules.baseline)
    for rule in rules.rules:
        refs.extend(rule.gates)
    for ref in refs:
        if ":" not in ref.gate:
            findings.append(
                Finding(
                    CODE_UNKNOWN_GATE,
                    "gate %r is not resolvable — use make:<target> or check:<name>"
                    % ref.gate,
                    subject="gate:%s" % ref.gate,
                )
            )
            continue
        kind, _, name = ref.gate.partition(":")
        if kind == "make":
            if name not in targets:
                findings.append(
                    Finding(
                        CODE_UNKNOWN_GATE,
                        "gate %r names a Makefile target that does not exist" % ref.gate,
                        subject="gate:%s" % ref.gate,
                        remediation="rename the rule, or restore the target",
                    )
                )
        elif kind == "check":
            if name not in checks:
                findings.append(
                    Finding(
                        CODE_UNKNOWN_GATE,
                        "gate %r names scripts/check-%s.sh, which does not exist"
                        % (ref.gate, name),
                        subject="gate:%s" % ref.gate,
                        remediation="rename the rule, or restore the check",
                    )
                )
        else:
            findings.append(
                Finding(
                    CODE_UNKNOWN_GATE,
                    "gate %r uses unknown prefix %r (expected make: or check:)"
                    % (ref.gate, kind),
                    subject="gate:%s" % ref.gate,
                )
            )
    return findings


# ---------------------------------------------------------------------------
# tag sets
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class TagSet:
    """A parsed tag set: dimension -> values, plus the raw labels it came from."""

    labels: Tuple[str, ...] = ()
    values: Mapping[str, Tuple[str, ...]] = field(default_factory=dict)

    def get(self, dimension: str) -> Tuple[str, ...]:
        return tuple(self.values.get(dimension, ()))

    def has(self, dimension: str, value: str) -> bool:
        return value in self.get(dimension)

    def with_label(self, label: str) -> "TagSet":
        values = {k: list(v) for k, v in self.values.items()}
        name, _, value = label.partition(":")
        values.setdefault(name, []).append(value)
        return TagSet(labels=self.labels + (label,), values={k: tuple(v) for k, v in values.items()})


def parse_labels(labels: Iterable[str]) -> TagSet:
    """Parse ``name:value`` labels into a tag set.

    A label with no colon is not a tag and is ignored rather than guessed at —
    the board carries free-form labels too, and inventing a dimension for them
    would be the opposite of a closed vocabulary.
    """
    values: Dict[str, List[str]] = {}
    kept: List[str] = []
    for label in labels:
        name, sep, value = str(label).partition(":")
        if not sep or not name or not value:
            continue
        values.setdefault(name, []).append(value)
        kept.append(str(label))
    return TagSet(labels=tuple(kept), values={k: tuple(v) for k, v in values.items()})


def validate_tags(
    tag_set: TagSet,
    taxonomy: Taxonomy,
    target: str = "issue",
    strict: bool = False,
) -> List[Finding]:
    """Judge a tag set against the taxonomy."""
    findings: List[Finding] = []
    for name, values in sorted(tag_set.values.items()):
        dim = taxonomy.dimensions.get(name)
        if dim is None:
            findings.append(
                Finding(
                    CODE_UNKNOWN_DIMENSION,
                    "tag %r uses undeclared dimension %r" % (values[0], name),
                    subject="tag:%s" % name,
                    remediation="declare the dimension in governance/tagging/taxonomy.yaml",
                )
            )
            continue
        if target not in dim.applies_to:
            findings.append(
                Finding(
                    CODE_UNKNOWN_TARGET,
                    "dimension %r does not apply to a %s" % (name, target),
                    subject="tag:%s" % name,
                )
            )
        if not dim.multi and len(values) > 1:
            findings.append(
                Finding(
                    CODE_UNKNOWN_VALUE,
                    "dimension %r is single-valued but carries %d values (%s)"
                    % (name, len(values), ", ".join(values)),
                    subject="tag:%s" % name,
                )
            )
        for value in values:
            if dim.kind == KIND_PATTERN:
                if not re.match(dim.pattern, value):
                    findings.append(
                        Finding(
                            CODE_VALUE_PATTERN,
                            "%s=%r does not match %s" % (name, value, dim.pattern),
                            subject="tag:%s" % name,
                        )
                    )
            elif value not in dim.values:
                findings.append(
                    Finding(
                        CODE_UNKNOWN_VALUE,
                        "%s=%r is not a declared value (legal: %s)"
                        % (name, value, ", ".join(dim.values)),
                        subject="tag:%s" % name,
                        remediation="use a declared value, or declare the new one here",
                    )
                )
        for group in dim.mutually_exclusive:
            present = [value for value in group if value in values]
            if len(present) > 1:
                findings.append(
                    Finding(
                        CODE_POSTURE_CONTRADICTION,
                        "%s carries mutually exclusive values %s"
                        % (name, " and ".join(present)),
                        subject="tag:%s" % name,
                        remediation="pick one — a contradiction is not a preference",
                    )
                )

    for name in taxonomy.required_for(target):
        if not tag_set.get(name):
            findings.append(
                Finding(
                    CODE_REQUIRED_MISSING,
                    "target %r must declare %r" % (target, name),
                    subject="target:%s" % target,
                    remediation="add the %s:<value> label" % name,
                )
            )
    for name in taxonomy.recommended_for(target):
        if not tag_set.get(name):
            findings.append(
                Finding(
                    CODE_REQUIRED_MISSING,
                    "target %r should declare %r" % (target, name),
                    severity=SEVERITY_ERROR if strict else SEVERITY_WARNING,
                    subject="target:%s" % target,
                    remediation="add the %s:<value> label" % name,
                )
            )
    return findings


# ---------------------------------------------------------------------------
# the plan
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Plan:
    target: str
    tags: TagSet
    gates: Mapping[str, Tuple[str, ...]]
    rules_fired: Tuple[str, ...]
    finops_floor: str
    declarations: Tuple[str, ...]
    forbids: Tuple[str, ...]
    summary: str

    def as_dict(self) -> Dict[str, Any]:
        return {
            "target": self.target,
            "tags": {k: list(v) for k, v in sorted(self.tags.values.items())},
            "rules_fired": list(self.rules_fired),
            "finops_floor": self.finops_floor,
            "required_declarations": list(self.declarations),
            "forbidden_markers": list(self.forbids),
            "gates_by_channel": {k: list(v) for k, v in sorted(self.gates.items())},
        }


def derive(
    tag_set: TagSet,
    taxonomy: Taxonomy,
    rules: Rules,
    target: str = "issue",
    strict: bool = False,
) -> Tuple[Plan, List[Finding]]:
    """Derive the required gates for a tag set. Returns (plan, findings)."""
    findings = validate_tags(tag_set, taxonomy, target=target, strict=strict)

    gate_names: Dict[str, List[str]] = {channel: [] for channel in CHANNELS}
    fired: List[str] = []
    floors: List[Tuple[int, str]] = []
    declarations: List[str] = []
    forbids: List[str] = []

    def _add(refs: Iterable[GateRef]) -> None:
        for ref in refs:
            bucket = gate_names.setdefault(ref.channel, [])
            if ref.gate not in bucket:
                bucket.append(ref.gate)

    _add(rules.baseline)

    ladder = taxonomy.class_ladder()

    def _rank(rung: str) -> int:
        try:
            return ladder.index(rung)
        except ValueError:
            return -1

    declared_class = tag_set.get("class")
    for rule in rules.rules:
        when = rule.when
        matched = False
        if "dimension" in when:
            name = str(when["dimension"])
            includes = _as_str_tuple(when.get("includes"))
            tagged = tag_set.get(name)
            matched = any(value in tagged for value in includes)
        elif "class_at_least" in when:
            rung = str(when["class_at_least"])
            matched = bool(declared_class) and _rank(declared_class[0]) >= _rank(rung)
        if not matched:
            continue
        fired.append(rule.id)
        _add(rule.gates)
        if rule.finops_floor:
            floors.append((_finops_rank(rule.finops_floor), rule.finops_floor))
        if rule.require_declaration:
            declarations.append(rule.require_declaration)
        forbids.extend(rule.forbid_markers)

    declared_tier = tag_set.get("finops")
    if declared_tier and floors:
        tier_rank = _finops_rank(declared_tier[0])
        below = [name for rank, name in floors if rank > tier_rank]
        if below:
            findings.append(
                Finding(
                    CODE_FINOPS_FLOOR_UNMET,
                    "finops:%s is below the floor this tag set requires (%s)"
                    % (declared_tier[0], ", ".join(sorted(set(below)))),
                    subject="tag:finops",
                    remediation="raise the tier, or drop the tag that sets the floor",
                )
            )

    floor = ""
    if floors:
        floor = max(floors, key=lambda item: item[0])[1]

    ordered_gates = {k: tuple(v) for k, v in gate_names.items() if v}
    summary = (
        "target=%s rules=%d gates=%d floor=%s"
        % (
            target,
            len(fired),
            sum(len(v) for v in ordered_gates.values()),
            floor or "(none)",
        )
    )
    plan = Plan(
        target=target,
        tags=tag_set,
        gates=ordered_gates,
        rules_fired=tuple(fired),
        finops_floor=floor,
        declarations=tuple(sorted(set(declarations))),
        forbids=tuple(sorted(set(forbids))),
        summary=summary,
    )
    return plan, findings


def _finops_rank(tier: str) -> int:
    """Rank a FinOps tier on the declared ladder; unknown ranks sort lowest."""
    try:
        return FINOPS_RANK[tier]
    except KeyError:
        return 0


# Ranks mirror the ladder in governance/tagging/rules.yaml's `finops` block; the
# gate asserts the two agree, so this constant cannot drift from the declaration.
FINOPS_RANK: Dict[str, int] = {"flash": 1, "pro": 2, "auditor": 3}


def finops_rank_map(rules_path: Path) -> Tuple[Dict[str, int], List[Finding]]:
    """Read the FinOps rank ladder from rules.yaml and check it against the map.

    The ranks are declared in rules.yaml and mirrored in ``FINOPS_RANK`` above,
    for the same reason ``fleet/channel.py`` mirrors the fleet glossary: two
    copies that a gate keeps honest beats one copy a caller can reinterpret.
    """
    findings: List[Finding] = []
    declared: Dict[str, int] = {}
    try:
        raw = _load_doc(rules_path)
    except TaggingUnavailable as exc:
        return {}, [Finding(CODE_RULES_INVALID, str(exc))]
    block = (raw or {}).get("finops") or {}
    for name, rank in (block.get("floors_by_rank") or {}).items():
        declared[str(name)] = int(rank)
    if declared != FINOPS_RANK:
        findings.append(
            Finding(
                CODE_RULES_INVALID,
                "the FinOps rank ladder drift: rules.yaml declares %s, model.py mirrors %s"
                % (sorted(declared.items()), sorted(FINOPS_RANK.items())),
                subject="finops",
                remediation="make the two agree — a floor compared on two ladders is a guess",
            )
        )
    return declared, findings


# ---------------------------------------------------------------------------
# the matrix
# ---------------------------------------------------------------------------
MATRIX_BEGIN = "<!-- BEGIN GENERATED TAG -> GATE MATRIX (governance/tagging/cli.py matrix) -->"
MATRIX_END = "<!-- END GENERATED TAG -> GATE MATRIX -->"


def render_matrix(taxonomy: Taxonomy, rules: Rules) -> str:
    """Render the tag -> gate matrix as deterministic markdown."""
    lines: List[str] = [MATRIX_BEGIN, ""]
    lines.append(
        "| Rule | Fires when | Gates required | Channel | FinOps floor | Refuses |"
    )
    lines.append("|---|---|---|---|---|---|")
    for rule in rules.rules:
        when = rule.when
        if "dimension" in when:
            condition = "%s includes %s" % (
                when["dimension"],
                " or ".join("`%s`" % v for v in _as_str_tuple(when.get("includes"))),
            )
        elif "class_at_least" in when:
            condition = "class is at least `%s`" % when["class_at_least"]
        else:
            condition = "(always)"
        gates = ", ".join("`%s`" % ref.gate for ref in rule.gates) or "— (none)"
        channels = ", ".join(sorted({ref.channel for ref in rule.gates})) or "—"
        refuses = ", ".join("`%s`" % m for m in rule.forbid_markers) or "—"
        lines.append(
            "| `%s` | %s | %s | %s | %s | %s |"
            % (rule.id, condition, gates, channels, rule.finops_floor or "—", refuses)
        )
    lines.append("")
    lines.append(
        "Baseline gates applied to every tag set: %s."
        % ", ".join("`%s`" % ref.gate for ref in rules.baseline)
    )
    lines.append("")
    lines.append("### Dimensions")
    lines.append("")
    lines.append("| Dimension | Kind | Values | Multi | Applies to | Authority |")
    lines.append("|---|---|---|---|---|---|")
    for name, dim in sorted(taxonomy.dimensions.items()):
        if dim.kind == KIND_PATTERN:
            values = "`%s`" % dim.pattern
        else:
            values = ", ".join("`%s`" % v for v in dim.values) or "—"
        authority = "declared here"
        if dim.borrowed_from is not None:
            authority = "borrowed: `%s` → `%s`" % (
                dim.borrowed_from.path,
                dim.borrowed_from.pointer,
            )
        elif dim.name_authority is not None:
            authority = "name anchored: `%s` → `%s`" % (
                dim.name_authority.path,
                dim.name_authority.pointer,
            )
        lines.append(
            "| `%s` | %s | %s | %s | %s | %s |"
            % (
                name,
                dim.kind,
                values,
                "yes" if dim.multi else "no",
                ", ".join(dim.applies_to) or "—",
                authority,
            )
        )
    lines.append("")
    lines.append(MATRIX_END)
    return "\n".join(lines)


def extract_matrix(text: str) -> Optional[str]:
    """Pull the generated block out of a document, or None when absent."""
    start = text.find(MATRIX_BEGIN)
    end = text.find(MATRIX_END)
    if start < 0 or end < 0 or end < start:
        return None
    return text[start : end + len(MATRIX_END)]
