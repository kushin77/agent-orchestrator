"""The declared tag controls, and the reader that holds the authority to them.

---knowledge---
module_id: governance.tagging.policy
system: governance
app: tagging
solution_class: pattern
patterns: [no-false-green]
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [ControlsUnavailable, load, control, check]
invariants: ""
gotchas: ""
related: []
do_not_duplicate: null
---knowledge---

`controls.yaml` states how the tag authority is governed; this module is what
makes those statements binding. Every control is re-derived from the authority It
governs and compared to the declaration, so:

* a lane that **relaxes** the taxonomy's `required` set without touching the
  controls is refused by name (`control-required-mismatch`);
* a lane that adds a **seventh rung** to the ladder cannot do it by editing
  `controls.yaml`'s FinOps ranks, because the ranks are compared against both
  `rules.yaml` and :data:`model.FINOPS_RANK`;
* a control declared with the wrong severity, a posture pair the taxonomy does
  not declare, or a limit the authority has crossed, are all refusals rather
  than surprises.

Reading is not enforcement if nothing calls it — `scripts/check-tagging.sh`
runs :func:`check`, and the suite drives each refusal with its clean twin.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

import model as M  # noqa: E402

DEFAULT_CONTROLS = Path(__file__).resolve().parent / "controls.yaml"

CONTROLS_SCHEMA = "ao.tagging/controls-v1"

CODE_CONTROLS_INVALID = "control-declaration-invalid"
CODE_REQUIRED_MISMATCH = "control-required-mismatch"
CODE_RECOMMENDED_MISMATCH = "control-recommended-mismatch"
CODE_SEVERITY_MISMATCH = "control-severity-mismatch"
CODE_FLOOR_MISMATCH = "control-floor-mismatch"
CODE_CONTRADICTION_MISMATCH = "control-contradiction-mismatch"
CODE_LIMIT_EXCEEDED = "control-limit-exceeded"


class ControlsUnavailable(M.TaggingUnavailable):
    """The controls could not be read — a CANNOT-ASSESS for the whole gate."""


def load(path: Optional[Path] = None) -> Dict[str, Any]:
    """Load and shape-check ``controls.yaml``."""
    target = Path(path) if path else DEFAULT_CONTROLS
    try:
        raw = M._load_doc(target)
    except M.TaggingUnavailable as exc:
        raise ControlsUnavailable(str(exc))
    if not isinstance(raw, Mapping):
        raise ControlsUnavailable("%s does not contain a mapping" % target)
    if raw.get("schema") != CONTROLS_SCHEMA:
        raise ControlsUnavailable(
            "%s declares schema %r, expected %r"
            % (target, raw.get("schema"), CONTROLS_SCHEMA)
        )
    if not isinstance(raw.get("controls"), list):
        raise ControlsUnavailable("%s declares no controls list" % target)
    return dict(raw)


def control(document: Mapping[str, Any], control_id: str) -> Optional[Mapping[str, Any]]:
    for entry in document.get("controls") or ():
        if isinstance(entry, Mapping) and str(entry.get("id", "")) == control_id:
            return entry
    return None


def _finding(code: str, message: str, remediation: str = "") -> M.Finding:
    return M.Finding(code, message, subject="controls", remediation=remediation)


def check(
    taxonomy: M.Taxonomy,
    rules: M.Rules,
    document: Mapping[str, Any],
    rules_path: Optional[Path] = None,
) -> Tuple[M.Finding, ...]:
    """Every way the controls and the authority can disagree, as findings."""
    findings: List[M.Finding] = []

    required = control(document, "required-by-target")
    if required is None:
        findings.append(
            _finding(CODE_CONTROLS_INVALID, "the required-by-target control is absent")
        )
    else:
        declared = {
            str(k): tuple(str(v) for v in (vals or ()))
            for k, vals in (required.get("required") or {}).items()
        }
        for target, names in sorted(taxonomy.required.items()):
            if declared.get(target) != tuple(names):
                findings.append(
                    _finding(
                        CODE_REQUIRED_MISMATCH,
                        "target %r: controls declare %s, the taxonomy requires %s"
                        % (target, list(declared.get(target, ())), list(names)),
                        "make the two agree — a required set nobody reads is not a control",
                    )
                )
        for extra in sorted(set(declared) - set(taxonomy.required)):
            findings.append(
                _finding(
                    CODE_REQUIRED_MISMATCH,
                    "target %r is controlled but not declared by the taxonomy" % extra,
                )
            )

    recommended = control(document, "recommended-by-target")
    if recommended is None:
        findings.append(
            _finding(CODE_CONTROLS_INVALID, "the recommended-by-target control is absent")
        )
    else:
        declared = {
            str(k): tuple(str(v) for v in (vals or ()))
            for k, vals in (recommended.get("recommended") or {}).items()
        }
        for target, names in sorted(taxonomy.recommended.items()):
            if declared.get(target) != tuple(names):
                findings.append(
                    _finding(
                        CODE_RECOMMENDED_MISMATCH,
                        "target %r: controls declare %s as recommended, the taxonomy declares %s"
                        % (target, list(declared.get(target, ())), list(names)),
                    )
                )

    severity = control(document, "refusal-severity")
    if severity is None:
        findings.append(_finding(CODE_CONTROLS_INVALID, "the refusal-severity control is absent"))
    elif str(severity.get("severity", "")) != M.SEVERITY_ERROR:
        findings.append(
            _finding(
                CODE_SEVERITY_MISMATCH,
                "refusal-severity declares %r; every refusal must be an error"
                % severity.get("severity"),
                "a refusal that only warns is a preference, not a gate (GR-12)",
            )
        )

    floors = control(document, "finops-floors")
    if floors is None:
        findings.append(_finding(CODE_CONTROLS_INVALID, "the finops-floors control is absent"))
    else:
        declared = {str(k): int(v) for k, v in (floors.get("floors_by_rank") or {}).items()}
        if declared != M.FINOPS_RANK:
            findings.append(
                _finding(
                    CODE_FLOOR_MISMATCH,
                    "controls declare the FinOps ranks %s; model.py mirrors %s"
                    % (sorted(declared.items()), sorted(M.FINOPS_RANK.items())),
                )
            )
        _, rank_findings = M.finops_rank_map(
            rules_path if rules_path else Path(rules.path)
        )
        for finding in rank_findings:
            findings.append(_finding(CODE_FLOOR_MISMATCH, finding.message))

    contradiction = control(document, "contradiction-refused")
    if contradiction is None:
        findings.append(
            _finding(CODE_CONTROLS_INVALID, "the contradiction-refused control is absent")
        )
    else:
        name = str(contradiction.get("dimension", ""))
        dim = taxonomy.dimensions.get(name)
        if dim is None:
            findings.append(
                _finding(
                    CODE_CONTRADICTION_MISMATCH,
                    "the contradiction control names undeclared dimension %r" % name,
                )
            )
        else:
            declared = tuple(
                tuple(str(v) for v in pair) for pair in (contradiction.get("pairs") or ())
            )
            if declared != tuple(dim.mutually_exclusive):
                findings.append(
                    _finding(
                        CODE_CONTRADICTION_MISMATCH,
                        "controls exclude %s; dimension %r declares %s"
                        % (list(declared), name, list(dim.mutually_exclusive)),
                    )
                )

    limits = document.get("limits") or {}
    measured = {
        "max_dimensions": (len(taxonomy.dimensions), "dimension(s)"),
        "max_rules": (len(rules.rules), "rule(s)"),
        "max_refusals": (len(taxonomy.refusal_ids), "refusal(s)"),
    }
    for key, (actual, unit) in sorted(measured.items()):
        limit = limits.get(key)
        if limit is None:
            findings.append(_finding(CODE_CONTROLS_INVALID, "limit %r is not declared" % key))
            continue
        if actual > int(limit):
            findings.append(
                _finding(
                    CODE_LIMIT_EXCEEDED,
                    "%d %s exceeds the declared limit %s=%s"
                    % (actual, unit, key, limit),
                    "a vocabulary that grows without bound is not a closed vocabulary",
                )
            )

    return tuple(findings)
