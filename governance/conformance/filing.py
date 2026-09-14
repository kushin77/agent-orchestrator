"""Derive declaring labels at the issue-filing path (issue #320).

The conformance gate (#140) refuses an issue that declares no class — but it can
only refuse it *after* the issue exists, and `governance/lifecycle`'s
`FILING_LABELS_MISSING` invariant likewise only *detects* the result. A board that
is wrong until someone notices is the gap this module closes. #174 owns *repairing*
the legacy issues that were filed unclassified (#253, #254, #255, #259, #297);
**this module owns prevention**: the filing path derives every declaring label from
`governance/conformance/policy.yaml`, passes them to `gh issue create`, and refuses
— loudly, before any subprocess is built — when it cannot derive them, so no code
path can file an issue the gate will reject later.

Two properties make the guarantee mechanical rather than aspirational:

* the labels are a *derivation* from the policy — its ``required`` set, the declared
  class's ``expectations``, and the ``filing`` defaults the policy declares —
  never literals in the caller (see ``Policy.filing_label_names``);
* derivation happens before the command is assembled, so a refusal files nothing
  at all: it cannot leave a half-classified issue behind.

Importing this module pulls in the conformance checker (and with it this package's
flat `model`), so a caller that already has *another* package's flat `model` on
`sys.path` must load the seam without disturbing it — `fleet/brain.py` puts
`governance/dispatch/model.py` there, and does so under a swapped flat-name window
(see `brain._conformance`).

Every in-repo filing path goes through :func:`file_issue`. `audit_filing_seam`
exists so the conformance gate can *prove* that rather than trust it (GR-12): it
is checked from `scripts/check-conformance.sh` via
``governance/conformance/cli.py filing-check``, which also feeds the path an
underivable filing and requires the refusal.

Usage::

    python3 governance/conformance/cli.py file --title ... --body ... --dry-run
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

if TYPE_CHECKING:  # pragma: no cover - typing only, keeps this module import-light
    from model import Policy

CLASS_FIELD = "class"
LABEL_SEPARATOR = ":"
DEFAULT_REPO = "kushin77/agent-orchestrator"
GH = "gh"

# The fleet's filing path, and the seam it must delegate to. The audit below reads
# the source rather than importing it: importing the brain from the gate would pull
# the whole loop (and its `.fleet/` runtime dir) into a governance check.
BRAIN_RELPATH = ("fleet", "brain.py")
SEAM_MODULE = "filing"
SEAM_CALL = "file_issue("
# A `gh issue create` argv of the brain's own: the shape that filed unclassified
# issues before this issue (issue #320), and must not come back.
BARE_CREATE = re.compile(r"""["']issue["']\s*,\s*["']create["']""")


class FilingRefused(Exception):
    """A filing that cannot derive its declaring labels. Nothing was filed."""

    def __init__(self, reason: str, *, missing: Sequence[str] = ()) -> None:
        super().__init__(reason)
        self.reason = reason
        self.missing = tuple(missing)

    @property
    def loud_message(self) -> str:
        """The operator-facing refusal: what could not be derived, and where it is
        prevented."""
        return (
            "FILING REFUSED — %s. Nothing was filed: the conformance gate would have "
            "rejected the issue as unclassified (issue #320 owns prevention; #174 "
            "owns repairing the legacy issues that were filed this way)." % self.reason
        )


@dataclass(frozen=True)
class FilingRequest:
    """One issue about to be filed.

    ``declaring`` carries only the fields the caller wants to state explicitly;
    everything it leaves out is derived from the policy, and a field neither the
    caller nor the policy can supply is a refusal.
    """

    title: str
    body: str
    repo: str = DEFAULT_REPO
    declared_class: str = ""
    declaring: Mapping[str, str] = field(default_factory=dict)
    labels: Tuple[str, ...] = ()
    dry_run: bool = False

    @property
    def declaring_class(self) -> str:
        return str(self.declared_class or self.declaring.get(CLASS_FIELD, "") or "").strip()


@dataclass(frozen=True)
class FilingPlan:
    """The exact command the filing path would run, and the labels it carries."""

    argv: Tuple[str, ...]
    labels: Tuple[str, ...]
    declared_class: str

    @property
    def command(self) -> str:
        return " ".join(self.argv)

    def label(self, name: str) -> str:
        prefix = name + LABEL_SEPARATOR
        for label in self.labels:
            if label.startswith(prefix):
                return label
        return ""


@dataclass(frozen=True)
class FilingResult:
    """What a filing did: the issue number, or a dry run that filed nothing."""

    plan: FilingPlan
    number: Optional[int] = None
    dry_run: bool = False

    @property
    def labels(self) -> Tuple[str, ...]:
        return self.plan.labels


@dataclass(frozen=True)
class FilingSeamProblem:
    """One structural defect in an in-repo filing path."""

    code: str
    message: str


def declared_class_for(request: FilingRequest, policy: "Policy") -> str:
    """The rung the filing will declare: its own, else the policy's filing default."""
    return (request.declaring_class or str(policy.filing_default_class or "")).strip()


def derive_declaring_labels(request: FilingRequest, policy: "Policy") -> Tuple[str, ...]:
    """The declaring labels for ``request``, or :class:`FilingRefused`.

    The class is the request's own, else the policy's ``filing.default_class``; the
    companions are the request's own, else the policy's ``filing.defaults``. Any
    label the policy says the work must declare and neither source can supply is a
    refusal — that is the difference between preventing an unclassified issue and
    discovering it on the board later.
    """
    declared = declared_class_for(request, policy)
    if not declared:
        raise FilingRefused(
            "the filing declares no class and the conformance policy declares no "
            "`filing.default_class` to derive one from",
            missing=(CLASS_FIELD,),
        )
    if declared not in policy.allowed_classes:
        raise FilingRefused(
            "the filing declares class '%s', which is not a rung of the CMR ladder (%s)"
            % (declared, ", ".join(policy.ladder)),
            missing=(CLASS_FIELD,),
        )

    values: Dict[str, str] = {CLASS_FIELD: declared}
    missing: List[str] = []
    for name in policy.filing_label_names(declared):
        if name == CLASS_FIELD:
            continue
        value = str(request.declaring.get(name, "") or "").strip()
        if not value:
            value = str(policy.filing_defaults.get(name, "") or "").strip()
        if not value:
            missing.append(name)
            continue
        values[name] = value

    if missing:
        raise FilingRefused(
            "cannot derive %s for class '%s' — the filing declares no value and the "
            "policy's `filing.defaults` declares none either"
            % (" and ".join("`%s:`" % name for name in missing), declared),
            missing=tuple(missing),
        )

    return tuple(
        "%s%s%s" % (name, LABEL_SEPARATOR, values[name])
        for name in policy.filing_label_names(declared)
    )


def plan_filing(request: FilingRequest, policy: "Policy") -> FilingPlan:
    """Build the ``gh issue create`` command, declaring labels included.

    Raises :class:`FilingRefused` — before the subprocess exists — when the labels
    cannot be derived.
    """
    labels = derive_declaring_labels(request, policy) + tuple(request.labels)
    deduped = tuple(dict.fromkeys(label for label in labels if label))
    argv: List[str] = [
        GH,
        "issue",
        "create",
        "--repo",
        request.repo,
        "--title",
        request.title,
        "--body",
        request.body,
    ]
    for label in deduped:
        argv.extend(["--label", label])
    return FilingPlan(
        argv=tuple(argv), labels=deduped, declared_class=declared_class_for(request, policy)
    )


def file_issue(
    request: FilingRequest,
    policy: "Policy",
    *,
    runner: Optional[Callable[..., "subprocess.CompletedProcess"]] = None,
) -> FilingResult:
    """File one issue through the seam, or refuse without filing.

    The plan is built (and may be refused) before the runner is ever called, so a
    refusal is observable as *nothing happened*: no issue, no partial label set.
    """
    plan = plan_filing(request, policy)
    if request.dry_run:
        return FilingResult(plan=plan, number=None, dry_run=True)

    run = runner or subprocess.run
    result = run(list(plan.argv), capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            "gh issue create failed: %s" % (result.stderr or "").strip()[-400:]
        )
    return FilingResult(plan=plan, number=_issue_number(result.stdout), dry_run=False)


def _issue_number(stdout: str) -> int:
    """``gh issue create`` prints the new issue's URL; parse its number."""
    match = re.search(r"/issues/(\d+)", stdout or "")
    if match:
        return int(match.group(1))
    tail = (stdout or "").strip().splitlines()[-1:] or [""]
    try:
        return int(tail[-1].rstrip("/").rsplit("/", 1)[-1])
    except ValueError:
        raise RuntimeError(
            "gh issue create returned no issue number: %r" % (stdout or "").strip()
        ) from None


def audit_filing_seam(root) -> Tuple[FilingSeamProblem, ...]:
    """Prove the fleet's filing path delegates to this seam (issue #320).

    A structural check, not a promise: the brain must not assemble its own
    ``gh issue create`` command, must import the seam, and must call it. A filing
    path that builds its own argv is exactly how an unclassified issue got filed in
    the first place.
    """
    from pathlib import Path

    path = Path(root).joinpath(*BRAIN_RELPATH)
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as exc:
        return (
            FilingSeamProblem(
                "filing-seam-bypassed",
                "cannot read the fleet filing path %s: %s" % (path, exc),
            ),
        )

    problems: List[FilingSeamProblem] = []
    if BARE_CREATE.search(source):
        problems.append(
            FilingSeamProblem(
                "filing-seam-bypassed",
                "%s builds its own `gh issue create` command; a filing path must "
                "derive its declaring labels through the %s seam" % (path, SEAM_MODULE),
            )
        )
    if "import %s" % SEAM_MODULE not in source:
        problems.append(
            FilingSeamProblem(
                "filing-seam-bypassed",
                "%s does not import the conformance %s seam" % (path, SEAM_MODULE),
            )
        )
    if SEAM_CALL not in source:
        problems.append(
            FilingSeamProblem(
                "filing-seam-bypassed",
                "%s never calls %s; a filing path that derives labels nowhere is a "
                "filing path that files unclassified issues"
                % (path, SEAM_CALL.rstrip("(")),
            )
        )
    return tuple(problems)
