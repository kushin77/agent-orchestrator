"""Conformance checking for issues and change sets (issue #140).

Two surfaces are checked, and they answer different questions:

* **The board** — is every item of in-scope work classified, and does the class it
  declares actually hold? Offline, from the committed board snapshot.
* **A change set** — does the work itself honour the mandates? Specifically the
  IaC mandate: infrastructure is declared behind a feature flag (default ON,
  policy-gr5-enabled-by-default, unless a recorded owner exception keeps it
  off), and no new GitHub Actions workflow is introduced (fleet GR-15).

Everything is deterministic and offline; the network path is confined to whatever
produced the snapshot.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import (
    Any,
    Callable,
    Dict,
    FrozenSet,
    Iterable,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
)

from model import (
    CODE_CLASS_AMBIGUOUS,
    CODE_CLASS_EXPECTATION_UNMET,
    CODE_CLASS_MISSING,
    CODE_CLASS_UNKNOWN,
    CODE_CLASSIFICATION_INCOMPLETE,
    CODE_DEPENDENCY_MISSING,
    CODE_IAC_MANDATE_UNMET,
    CODE_POLICY_INVALID,
    CODE_SCOPE_MISMATCH,
    SEVERITY_WARNING,
    Classified,
    ConformanceReport,
    Finding,
    Policy,
)

SNAPSHOT_RELPATH = Path(".board") / "snapshot.json"
POLICY_RELPATH = Path("governance") / "conformance" / "policy.yaml"
REPORT_RELPATH = Path(".verify") / "conformance-report.json"
SUITES_RELPATH = Path("scripts") / "pytest-suites.txt"

# -- the recorded label vocabulary (issue #1160) ------------------------------
# Deriving a declaring label is not the same as the repository HAVING it. `gh
# issue create` refuses a label that does not exist — so a `filing.defaults` entry
# naming one makes the *defaulted* filing path fail in production, which no
# derivation-only control can see: the seam hands `gh` a label set it cannot check
# and the refusal happens after the plan was built. The gate therefore resolves
# every label the policy's filing defaults derive against a committed inventory
# recorded from the live label set — the same committed-offline-artifact pattern
# `.board/snapshot.json` uses for the board, with one documented refresh verb.
LABELS_RELPATH = Path("governance") / "conformance" / "labels.json"

# The ONE refresh verb, named in every refusal: an inventory verified offline can
# never refresh itself, and a gate that fails closed without naming the way to
# open it is a dead end rather than an instruction.
LABELS_REFRESH_VERB = "python3 governance/conformance/cli.py labels --refresh"

# The finding code for a default that names a label the repository does not have.
# The other filing-path codes live in `model.py`; this one describes the
# resolution half of the same seam and is declared here, beside the inventory it
# is graded against.
CODE_FILING_LABEL_UNRESOLVED = "filing-label-unresolved"

# Paths that must never receive a new file without the IaC mandate satisfied.
INFRA_PREFIXES = ("infra/",)
WORKFLOW_PREFIXES = (".github/workflows/",)
FLAG_MARKERS = ("flag", "enable_", "_ENABLE", "disabled", "gated")

# Package roots whose new modules are expected to declare a test suite.
SUITE_TRACKED_PREFIXES = (
    "governance/",
    "registry/",
    "gateway/",
    "engine/",
    "guardrails/",
    "telemetry/",
    "identity/",
    "portal/",
    "control-plane/",
)


class PolicyUnavailable(Exception):
    """The policy could not be read (missing, malformed, or no YAML support)."""


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# -- policy ------------------------------------------------------------------


def load_policy(path: Path) -> Policy:
    try:
        import yaml  # noqa: PLC0415 - optional dependency, resolved on demand
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise PolicyUnavailable("PyYAML is not installed: %s" % exc) from exc

    try:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise PolicyUnavailable("cannot read policy %s: %s" % (path, exc)) from exc
    except Exception as exc:  # yaml.YAMLError and friends
        raise PolicyUnavailable("policy %s is not valid YAML: %s" % (path, exc)) from exc

    if not isinstance(raw, Mapping):
        raise PolicyUnavailable("policy %s must be a mapping" % path)

    ladder = tuple(str(rung) for rung in raw.get("ladder", ()) or ())
    if not ladder:
        raise PolicyUnavailable("policy declares an empty class ladder")

    expectations: Dict[str, Tuple[str, ...]] = {}
    for rung, names in (raw.get("expectations") or {}).items():
        expectations[str(rung)] = tuple(str(n) for n in (names or ()))
        if str(rung) not in ladder:
            raise PolicyUnavailable(
                "expectations name %r, which is not a rung of the ladder" % rung
            )

    mandates = raw.get("mandates") or {}
    iac = mandates.get("iac") if isinstance(mandates, Mapping) else None
    infra_paths = tuple(str(p) for p in ((iac or {}).get("infra_paths") or INFRA_PREFIXES))

    filing = raw.get("filing") or {}
    if not isinstance(filing, Mapping):
        raise PolicyUnavailable("policy %s declares `filing` as a non-mapping" % path)
    filing_default_class = str(filing.get("default_class") or "")
    if filing_default_class and filing_default_class not in ladder:
        # The filing path derives a *class* from this value; one that names no rung
        # would make every filing already-unconformant (issue #320).
        raise PolicyUnavailable(
            "filing.default_class %r is not a rung of the ladder" % filing_default_class
        )
    filing_defaults = {
        str(name): str(value)
        for name, value in (filing.get("defaults") or {}).items()
    }
    filing_tags = tuple(str(name) for name in (filing.get("tags") or ()))

    return Policy(
        ladder=ladder,
        required=tuple(str(name) for name in raw.get("required", ()) or ()),
        expectations=expectations,
        prefixed=tuple(str(name) for name in raw.get("prefixed", ()) or ()),
        infra_paths=infra_paths,
        filing_default_class=filing_default_class,
        filing_defaults=filing_defaults,
        filing_tags=filing_tags,
    )


# -- board -------------------------------------------------------------------


def load_snapshot(path: Path) -> List[Mapping[str, Any]]:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError:
        return []
    except ValueError:
        return []
    issues = raw.get("issues") if isinstance(raw, Mapping) else None
    return list(issues) if isinstance(issues, list) else []


# -- the recorded label vocabulary (issue #1160) ------------------------------


class LabelsUnavailable(Exception):
    """The recorded label inventory cannot be read.

    ABSENCE FAILS CLOSED (issue #1160). The inventory is the authority a
    resolvability claim is graded against, so a missing, unparseable or empty file
    is CANNOT-ASSESS — never a pass, and never an empty vocabulary that would
    resolve every label by accident.
    """


def load_label_inventory(path: Path) -> FrozenSet[str]:
    """The repository's recorded label vocabulary, or :class:`LabelsUnavailable`.

    An empty `labels` list is unreadable for the same reason a missing file is:
    neither can support the claim that a derived label exists on the repository.
    """
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise LabelsUnavailable(
            "cannot read the label inventory %s: %s — record it with `%s`"
            % (path, exc, LABELS_REFRESH_VERB)
        ) from exc
    except ValueError as exc:
        raise LabelsUnavailable(
            "the label inventory %s is not valid JSON: %s — re-record it with `%s`"
            % (path, exc, LABELS_REFRESH_VERB)
        ) from exc

    names = raw.get("labels") if isinstance(raw, Mapping) else None
    if not isinstance(names, list) or not names:
        raise LabelsUnavailable(
            "the label inventory %s records no `labels` list — a vocabulary that "
            "records nothing cannot support a resolvability claim; record it with "
            "`%s`" % (path, LABELS_REFRESH_VERB)
        )
    return frozenset(str(name).strip() for name in names if str(name).strip())


def default_filing_labels(policy: Policy) -> Tuple[str, ...]:
    """The labels a filing that declares nothing would carry.

    The policy's own claim, measured through the seam's own plan rather than
    re-deriving the label set here: a second implementation of the derivation is a
    second thing that can drift from the first, and then the gate would be
    grading the wrong artifact.
    """
    from filing import FilingRequest, plan_filing  # noqa: PLC0415 - flat sibling

    return tuple(plan_filing(FilingRequest(title="t", body="b"), policy).labels)


def unresolvable_labels(labels: Sequence[str], inventory: Iterable[str]) -> Tuple[str, ...]:
    """The labels of ``labels`` the recorded inventory does not carry, in order."""
    known = set(inventory)
    return tuple(dict.fromkeys(label for label in labels if label and label not in known))


def audit_filing_labels(
    policy: Policy,
    inventory: Iterable[str],
    *,
    policy_path: Path = POLICY_RELPATH,
    inventory_path: Path = LABELS_RELPATH,
) -> Tuple[Finding, ...]:
    """Every label a *defaulted* filing derives must exist on the repository (#1160).

    A `filing.defaults` entry naming a label `gh` does not have is not a cosmetic
    metadata gap: the default path is the one an auto-filed micro-task takes, and
    `gh issue create` refuses the whole create with
    ``could not add label: '<label>' not found``. The refusal names the file the
    default lives in, the label, and the one refresh verb — so the operator who
    sees it does not have to find out how to record a new label first.
    """
    findings: List[Finding] = []
    for label in unresolvable_labels(default_filing_labels(policy), inventory):
        findings.append(
            Finding(
                code=CODE_FILING_LABEL_UNRESOLVED,
                message="the filing defaults in %s derive the label '%s', which %s "
                "does not record on this repository; `gh issue create --label %s` "
                "is refused with `could not add label: '%s' not found`, so every "
                "filing that leaves this label to the default fails"
                % (policy_path, label, inventory_path, label, label),
                subject=str(policy_path),
                remediation="point `filing.defaults` at a label the repository has "
                "(or mint the declared one), then re-record the inventory with `%s`"
                % LABELS_REFRESH_VERB,
            )
        )
    return tuple(findings)


LABELS_SOURCE = "kushin77/agent-orchestrator"
LABELS_RECORD_ARGV = (
    "gh",
    "label",
    "list",
    "--repo",
    LABELS_SOURCE,
    "--limit",
    "400",
    "--json",
    "name",
)
LABELS_SCHEMA = "cmr.conformance/labels-v1"


def record_label_inventory(
    path: Path,
    *,
    repo: str = LABELS_SOURCE,
    runner: Optional[Callable[..., "subprocess.CompletedProcess"]] = None,
) -> Tuple[bool, str]:
    """Perform the ONE inventory refresh; return ``(recorded, detail)``.

    The counterpart of ``governance/dispatch/snapshot.py``'s ``refresh``: one path
    touches the network, one place the command is composed, and a refused network or
    a failing ``gh`` is reported as ``(False, reason)`` — a first-class outcome,
    never a crash. `--jq` is deliberately not used here: the recording must not
    depend on a query language being installed, so the `name` field is projected in
    Python from the JSON `gh` returns.
    """
    import subprocess  # noqa: PLC0415 - only the refresh path needs it

    argv = [token if token != LABELS_SOURCE else repo for token in LABELS_RECORD_ARGV]
    run = runner or subprocess.run
    try:
        result = run(argv, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, "cannot run `%s`: %s" % (" ".join(argv), exc)
    if result.returncode != 0:
        return False, "`%s` failed: %s" % (
            " ".join(argv),
            (result.stderr or "").strip()[-200:],
        )
    try:
        raw = json.loads(result.stdout or "[]")
    except ValueError as exc:
        return False, "`%s` returned no JSON: %s" % (" ".join(argv), exc)
    names = sorted(
        {
            str(entry.get("name", "")).strip()
            for entry in raw
            if isinstance(entry, Mapping) and str(entry.get("name", "")).strip()
        }
    )
    if not names:
        return False, "`%s` returned no labels; recording an empty vocabulary" % (
            " ".join(argv),
        )
    payload = {
        "schema": LABELS_SCHEMA,
        "generated_at": now_iso(),
        "source": repo,
        "recorded_with": " ".join(argv),
        "refresh": LABELS_REFRESH_VERB,
        "labels": names,
    }
    target = Path(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        return False, "cannot write the label inventory %s: %s" % (target, exc)
    return True, "recorded %d label(s) from %s" % (len(names), repo)


def classify(issue: Mapping[str, Any]) -> Classified:
    labels = tuple(str(label) for label in (issue.get("labels") or ()))
    classes = tuple(
        label.split(":", 1)[1] for label in labels if label.startswith("class:")
    )
    return Classified(
        issue=str(issue.get("number", "")),
        title=str(issue.get("title", "")),
        milestone=str(issue.get("milestone") or ""),
        classes=classes,
        labels=labels,
    )


def check_issue(item: Classified, policy: Policy, *, strict: bool = False) -> List[Finding]:
    """Check one classified issue against the policy."""
    findings: List[Finding] = []
    subject = "issue-%s" % item.issue

    if not item.classes:
        default_class = policy.filing_defaults.get("class", policy.ladder[0] if policy.ladder else "")
        findings.append(
            Finding(
                code=CODE_CLASS_MISSING,
                message="issue #%s declares no class; it cannot be held to any rung "
                "of the ladder" % item.issue,
                subject=subject,
                remediation="add a `class:<rung>` label from: %s "
                "(one paste: `gh issue edit %s --add-label class:%s`)"
                % (", ".join(policy.ladder), item.issue, default_class),
            )
        )
        return findings

    if len(item.classes) > 1:
        findings.append(
            Finding(
                code=CODE_CLASS_AMBIGUOUS,
                message="issue #%s declares %d classes (%s); a claim must name one rung"
                % (item.issue, len(item.classes), ", ".join(item.classes)),
                subject=subject,
                remediation="keep the single rung the work is being held to",
            )
        )

    declared = item.declared
    if declared not in policy.allowed_classes:
        findings.append(
            Finding(
                code=CODE_CLASS_UNKNOWN,
                message="issue #%s declares class '%s', which is not a rung of the "
                "CMR ladder" % (item.issue, declared),
                subject=subject,
                remediation="use one of: %s" % ", ".join(policy.ladder),
            )
        )

    for name in policy.required:
        if name == "class":
            continue  # already covered above
        if not item.has(name):
            suggested = policy.filing_defaults.get(name)
            remediation = "add a `%s:<value>` label" % name
            if suggested:
                remediation += " (one paste: `gh issue edit %s --add-label %s:%s`)" % (
                    item.issue,
                    name,
                    suggested,
                )
            findings.append(
                Finding(
                    code=CODE_CLASSIFICATION_INCOMPLETE,
                    message="issue #%s declares class '%s' but no `%s:` label"
                    % (item.issue, declared or "(none)", name),
                    subject=subject,
                    remediation=remediation,
                )
            )

    severity = "error" if strict else SEVERITY_WARNING
    for name in policy.expectations_for(declared):
        if not item.has(name):
            suggested = policy.filing_defaults.get(name, declared)
            findings.append(
                Finding(
                    code=CODE_CLASS_EXPECTATION_UNMET,
                    message="issue #%s is class '%s', which expects a `%s:` label "
                    "(declared-vs-actual mismatch)" % (item.issue, declared, name),
                    severity=severity,
                    subject=subject,
                    remediation="add the label, or lower the declared class to match "
                    "what the work actually meets (one paste: "
                    "`gh issue edit %s --add-label %s:%s`)"
                    % (item.issue, name, suggested),
                )
            )

    return findings


def check_board(
    issues: Sequence[Mapping[str, Any]],
    policy: Policy,
    *,
    milestone: Optional[str] = None,
    include_unmilestoned: bool = False,
    strict: bool = False,
    generated_at: Optional[str] = None,
) -> ConformanceReport:
    """Conformance of in-scope board items.

    Scope is open issues that belong to a milestone. A milestoned issue is a
    commitment to a standard, so classification is enforced there; the un-milestoned
    backlog predates the convention and is counted, not failed, unless asked for.
    """
    findings: List[Finding] = []
    scanned = 0
    counts: Dict[str, int] = {}
    skipped_unmilestoned = 0

    for issue in issues:
        if str(issue.get("state", "")).upper() != "OPEN":
            continue
        item = classify(issue)
        if milestone and item.milestone != milestone:
            continue
        if not item.milestone and not include_unmilestoned:
            skipped_unmilestoned += 1
            continue

        scanned += 1
        findings.extend(check_issue(item, policy, strict=strict))
        key = item.declared or "(none)"
        counts[key] = counts.get(key, 0) + 1

    if skipped_unmilestoned:
        findings.append(
            Finding(
                code=CODE_SCOPE_MISMATCH,
                message="%d open issue(s) carry no milestone and were not classified "
                "in this run" % skipped_unmilestoned,
                severity=SEVERITY_WARNING,
                subject="scope",
                remediation="run with --include-unmilestoned to bring them into scope",
            )
        )

    scope = milestone or ("open+milestoned")
    return ConformanceReport(
        generated_at=generated_at or now_iso(),
        scope=scope,
        scanned=scanned,
        findings=findings,
        class_counts=counts,
    )


# -- change set --------------------------------------------------------------


def check_change_set(
    changed_paths: Iterable[str],
    policy: Policy,
    *,
    added: Iterable[str] = (),
    root: Optional[Path] = None,
) -> List[Finding]:
    """Check a change set against the cross-cutting mandates.

    Paths are **repository-relative**, which is what git reports. ``root`` resolves
    them for the content probe; without it the current working directory is used.
    ``changed_paths`` is every path the change touches; ``added`` is the subset it
    creates. Creation is what the IaC mandate constrains: modifying an existing
    declaration is normal, introducing an undeclared-off one is not.
    """
    findings: List[Finding] = []
    added_list = [str(p).replace("\\", "/") for p in added]
    infra = tuple(policy.infra_paths) + INFRA_PREFIXES

    for path in added_list:
        if path.startswith(WORKFLOW_PREFIXES):
            findings.append(
                Finding(
                    code=CODE_IAC_MANDATE_UNMET,
                    message="the change set adds a GitHub Actions workflow (%s); "
                    "fleet GR-15 keeps automation code-native" % path,
                    subject=path,
                    remediation="drive the automation from a Makefile target run by "
                    "the ops runner instead of a workflow file",
                )
            )

    new_infra = [p for p in added_list if p.startswith(infra)]
    for path in new_infra:
        if not _has_flag_marker(path, root):
            findings.append(
                Finding(
                    code=CODE_IAC_MANDATE_UNMET,
                    message="the change set adds infrastructure (%s) without a "
                    "flag-gated posture" % path,
                    subject=path,
                    remediation="declare it with a feature flag (policy-gr5-"
                    "enabled-by-default: on unless a recorded owner exception "
                    "keeps it off)",
                )
            )

    return findings


def _has_flag_marker(path: str, root: Optional[Path] = None) -> bool:
    """A newly added declaration counts as flag-gated when it names its flag.

    ``path`` is repository-relative; ``root`` resolves it on disk.
    """
    candidate = (Path(root) / path) if root is not None else Path(path)
    try:
        text = candidate.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return True  # cannot read it here; the IaC gate owns that surface
    lowered = text.lower()
    return any(marker.lower() in lowered for marker in FLAG_MARKERS)


def missing_suite_registration(
    changed_paths: Iterable[str], suite_lines: Sequence[str]
) -> List[Finding]:
    """A new package under a pillar should arrive with a declared test suite."""
    declared = {line.strip() for line in suite_lines if line.strip()}
    findings: List[Finding] = []
    seen_packages: set = set()

    for raw in changed_paths:
        path = str(raw).replace("\\", "/")
        if not path.startswith(SUITE_TRACKED_PREFIXES) or not path.endswith(".py"):
            continue
        parts = path.split("/")
        if len(parts) < 3:
            continue
        package = "/".join(parts[:2])
        if package in seen_packages or package in declared:
            continue
        if any(entry.startswith(package) for entry in declared):
            continue
        seen_packages.add(package)
        findings.append(
            Finding(
                code=CODE_DEPENDENCY_MISSING,
                message="package '%s' has changes but no suite is declared for it"
                % package,
                severity=SEVERITY_WARNING,
                subject=package,
                remediation="register the suite in scripts/pytest-suites.txt",
            )
        )
    return findings


def write_report(report: ConformanceReport, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report.as_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return path
