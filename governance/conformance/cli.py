#!/usr/bin/env python3
"""Conformance CLI — CMR class / pattern / template enforcement (issue #140).

Checks the board (is in-scope work classified, and does the declared class hold?),
a change set (does the work honour the cross-cutting mandates?), and the filing
path itself (can an unclassified issue still be filed? — issue #320).

Honest tri-state exit codes (repo convention, GR-12 / no-false-green):

* ``0`` — OK
* ``1`` — NOT-OK (a conformance error, deviations under ``--strict``, or a filing
  that could not derive its declaring labels)
* ``2`` — CANNOT-ASSESS (no policy, no snapshot, not a git work tree)

Subcommands::

    check        classify the board and report findings (the gate of record)
    change-set   check the current diff against the mandates
    filing-check the filing path derives declaring labels, and refuses when it cannot
    file         the supported hand-run filing path (derives the labels for you)
    labels       show the recorded label inventory, or re-record it (--refresh)
    policy       print the declared policy
    report       write .verify/conformance-report.json without failing

Examples::

    python3 governance/conformance/cli.py check
    python3 governance/conformance/cli.py check --milestone "M24 - ..." --strict
    python3 governance/conformance/cli.py change-set --base origin/master
    python3 governance/conformance/cli.py filing-check
    python3 governance/conformance/cli.py file --title "..." --body "..." --dry-run
    python3 governance/conformance/cli.py labels --refresh
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

_PKG_DIR = os.path.dirname(os.path.abspath(__file__))
if _PKG_DIR not in sys.path:
    sys.path.insert(0, _PKG_DIR)

from checker import (  # noqa: E402
    CODE_FILING_LABEL_UNRESOLVED,
    LABELS_RELPATH,
    LABELS_REFRESH_VERB,
    POLICY_RELPATH,
    REPORT_RELPATH,
    SNAPSHOT_RELPATH,
    SUITES_RELPATH,
    LabelsUnavailable,
    PolicyUnavailable,
    audit_filing_labels,
    check_board,
    check_change_set,
    default_filing_labels,
    load_label_inventory,
    load_policy,
    load_snapshot,
    missing_suite_registration,
    record_label_inventory,
    unresolvable_labels,
    write_report,
)
from filing import (  # noqa: E402
    CLASS_FIELD,
    DEFAULT_REPO,
    FilingRefused,
    FilingRequest,
    audit_filing_seam,
    file_issue,
    plan_filing,
)
from model import errors, warnings  # noqa: E402

DEFAULT_ROOT = Path(_PKG_DIR).parent.parent

EXIT_OK = 0
EXIT_NOT_OK = 1
EXIT_CANNOT_ASSESS = 2


def _print_findings(findings) -> None:
    if not findings:
        print("  (no findings)")
        return
    for finding in findings:
        print(
            "  %-7s %-28s %s"
            % (finding.severity.upper(), finding.code, finding.message)
        )
        if finding.remediation:
            print("          REMEDY: %s" % finding.remediation)


def _git(root: Path, *args: str) -> tuple:
    try:
        out = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return (-1, "")
    return (out.returncode, out.stdout)


def _resolve_base(root: Path, base: str) -> str:
    code, out = _git(root, "merge-base", base, "HEAD")
    if code == 0 and out.strip():
        return out.strip()
    return base


def cmd_check(args: argparse.Namespace) -> int:
    try:
        policy = load_policy(args.root / POLICY_RELPATH)
    except PolicyUnavailable as exc:
        print("conformance: CANNOT-ASSESS — %s" % exc, file=sys.stderr)
        return EXIT_CANNOT_ASSESS

    snapshot_path = args.root / SNAPSHOT_RELPATH
    if not snapshot_path.is_file():
        print(
            "conformance: CANNOT-ASSESS — no board snapshot at %s"
            % snapshot_path,
            file=sys.stderr,
        )
        return EXIT_CANNOT_ASSESS

    issues = load_snapshot(snapshot_path)
    if not issues:
        print(
            "conformance: CANNOT-ASSESS — board snapshot holds no issues",
            file=sys.stderr,
        )
        return EXIT_CANNOT_ASSESS

    report = check_board(
        issues,
        policy,
        milestone=args.milestone,
        include_unmilestoned=args.include_unmilestoned,
        strict=args.strict,
        generated_at=None,
    )
    write_report(report, args.root / REPORT_RELPATH)

    print(
        "scope: %s | scanned: %d | classes: %s"
        % (
            report.scope,
            report.scanned,
            ", ".join("%s=%d" % kv for kv in sorted(report.class_counts.items())) or "(none)",
        )
    )
    _print_findings(report.findings)

    hard = errors(report.findings)
    if hard:
        print(
            "conformance: FAIL (%d error(s), %d warning(s))"
            % (len(hard), len(warnings(report.findings))),
            file=sys.stderr,
        )
        return EXIT_NOT_OK
    print(
        "conformance: OK (%d item(s) conform, %d deviation(s) reported)"
        % (report.scanned, len(warnings(report.findings)))
    )
    return EXIT_OK


def cmd_change_set(args: argparse.Namespace) -> int:
    try:
        policy = load_policy(args.root / POLICY_RELPATH)
    except PolicyUnavailable as exc:
        print("conformance: CANNOT-ASSESS — %s" % exc, file=sys.stderr)
        return EXIT_CANNOT_ASSESS

    base = _resolve_base(args.root, args.base)
    code, out = _git(args.root, "diff", "--name-only", "%s...HEAD" % base)
    if code != 0:
        print(
            "conformance: CANNOT-ASSESS — cannot diff against %s" % base,
            file=sys.stderr,
        )
        return EXIT_CANNOT_ASSESS

    changed = [line.strip() for line in out.splitlines() if line.strip()]
    _, added_out = _git(args.root, "diff", "--name-only", "--diff-filter=A",
                        "%s...HEAD" % base)
    added = [line.strip() for line in added_out.splitlines() if line.strip()]

    findings = check_change_set(changed, policy, added=added, root=args.root)
    suites_path = args.root / SUITES_RELPATH
    suite_lines = (
        suites_path.read_text(encoding="utf-8").splitlines()
        if suites_path.is_file()
        else []
    )
    findings.extend(missing_suite_registration(changed, suite_lines))

    print("base: %s | changed: %d | added: %d" % (base[:12], len(changed), len(added)))
    _print_findings(findings)

    hard = errors(findings)
    if hard:
        print("conformance: FAIL (%d error(s))" % len(hard), file=sys.stderr)
        return EXIT_NOT_OK
    print("conformance: OK (change set honours the mandates)")
    return EXIT_OK


def cmd_policy(args: argparse.Namespace) -> int:
    try:
        policy = load_policy(args.root / POLICY_RELPATH)
    except PolicyUnavailable as exc:
        print("conformance: CANNOT-ASSESS — %s" % exc, file=sys.stderr)
        return EXIT_CANNOT_ASSESS
    print(json.dumps(policy.as_dict(), indent=2, sort_keys=True))
    return EXIT_OK


def cmd_labels(args: argparse.Namespace) -> int:
    """The recorded label vocabulary, and the defaults it resolves (issue #1160).

    Two modes, one artifact. Without ``--refresh`` this is offline and read-only:
    it answers "do the labels the filing defaults derive actually exist on this
    repository?" against the committed inventory. With ``--refresh`` it re-records
    that inventory from the live label set — the one path that touches the network,
    and the verb every refusal names.
    """
    inventory_path = args.root / LABELS_RELPATH

    if args.refresh:
        recorded, detail = record_label_inventory(inventory_path, repo=args.repo)
        if not recorded:
            print("conformance: CANNOT-ASSESS — %s" % detail, file=sys.stderr)
            return EXIT_CANNOT_ASSESS
        print("labels: RECORDED — %s" % detail)
        print("  inventory: %s" % inventory_path)

    try:
        inventory = load_label_inventory(inventory_path)
    except LabelsUnavailable as exc:
        print("conformance: CANNOT-ASSESS — %s" % exc, file=sys.stderr)
        return EXIT_CANNOT_ASSESS

    print("labels: %d recorded in %s" % (len(inventory), inventory_path))

    try:
        policy = load_policy(args.root / POLICY_RELPATH)
    except PolicyUnavailable as exc:
        print("conformance: CANNOT-ASSESS — %s" % exc, file=sys.stderr)
        return EXIT_CANNOT_ASSESS

    derived = default_filing_labels(policy)
    missing = unresolvable_labels(derived, inventory)
    print("  filing defaults derive: %s" % ", ".join(derived))
    if missing:
        for finding in audit_filing_labels(
            policy,
            inventory,
            policy_path=POLICY_RELPATH,
            inventory_path=LABELS_RELPATH,
        ):
            print("  %-7s %-28s %s" % ("ERROR", finding.code, finding.message))
        print(
            "labels: FAIL — %d derived label(s) are not recorded on this repository"
            % len(missing),
            file=sys.stderr,
        )
        return EXIT_NOT_OK
    print("labels: OK — every label the filing defaults derive is recorded")
    return EXIT_OK


def cmd_report(args: argparse.Namespace) -> int:
    """Write the report and never fail — for board reporting."""
    namespace = argparse.Namespace(**vars(args))
    namespace.strict = False
    return cmd_check(namespace)


def _parse_declares(pairs) -> list:
    """``--declare name=value`` arguments, as (name, value)."""
    parsed = []
    for pair in pairs or ():
        name, _, value = str(pair).partition("=")
        if not name.strip() or not value.strip():
            raise SystemExit(
                "conformance: cannot parse --declare %r (expected name=value)" % pair
            )
        parsed.append((name.strip(), value.strip()))
    return parsed


def _policy_or_cannot_assess(root: Path):
    """The declared policy, or ``None`` with CANNOT-ASSESS already reported."""
    try:
        return load_policy(root / POLICY_RELPATH)
    except PolicyUnavailable as exc:
        print("conformance: CANNOT-ASSESS — %s" % exc, file=sys.stderr)
        return None


def cmd_file(args: argparse.Namespace) -> int:
    """The supported hand-run filing path (issue #320).

    A hand-run ``gh issue create`` is the other way an unclassified issue reaches
    the board. This subcommand *is* that path with the declaring labels derived from
    the policy: it refuses an underivable filing instead of filing it.
    """
    policy = _policy_or_cannot_assess(args.root)
    if policy is None:
        return EXIT_CANNOT_ASSESS

    declaring = dict(_parse_declares(args.declare))
    request = FilingRequest(
        title=args.title,
        body=args.body,
        repo=args.repo,
        declared_class=args.declared_class or "",
        declaring=declaring,
        labels=tuple(args.label or ()),
        dry_run=bool(args.dry_run),
    )
    try:
        result = file_issue(request, policy)
    except FilingRefused as exc:
        print("conformance: %s" % exc.loud_message, file=sys.stderr)
        return EXIT_NOT_OK

    if result.dry_run:
        print("filing: DRY-RUN — nothing was filed")
        print("  labels:  %s" % ", ".join(result.labels))
        print("  command: %s" % result.plan.command)
        return EXIT_OK

    print("filing: filed #%s with %s" % (result.number, ", ".join(result.labels)))
    return EXIT_OK


def cmd_filing_check(args: argparse.Namespace) -> int:
    """Prove the filing path derives declaring labels, and REFUSES when it cannot.

    The gate's self-control for issue #320 (GR-12): a control whose refusal path
    cannot be reached is a formality, so every expectation below is provoked for
    real — the underivable filings must raise, the refusal must name itself, and the
    runner must never be reached (a refusal that files nothing is the whole point).
    """
    policy = _policy_or_cannot_assess(args.root)
    if policy is None:
        return EXIT_CANNOT_ASSESS

    # The recorded label vocabulary (issue #1160) is REQUIRED: the resolvability
    # expectation below is graded against it, and absence must fail closed rather
    # than resolve every label by default. A missing, unparseable or empty
    # inventory is therefore CANNOT-ASSESS for the whole self-control, never a pass.
    try:
        inventory = load_label_inventory(args.root / LABELS_RELPATH)
    except LabelsUnavailable as exc:
        print("conformance: CANNOT-ASSESS — %s" % exc, file=sys.stderr)
        return EXIT_CANNOT_ASSESS

    results: list = []

    def expect(name: str, ok: bool, detail: str = "") -> None:
        results.append(ok)
        print("filing-check: %s — %s" % ("PASS" if ok else "FAIL", name))
        if detail:
            print("    %s" % detail)

    # 1. A filing that declares nothing still carries every declaring label the
    #    policy demands, derived from the policy's `filing` block.
    plan = None
    try:
        plan = plan_filing(FilingRequest(title="t", body="b"), policy)
        expected = policy.filing_label_names(plan.declared_class)
        carried = [name for name in expected if plan.label(name)]
        expect(
            "derives declaring labels from the policy",
            bool(expected) and len(carried) == len(expected),
            "class=%s | labels=%s" % (plan.declared_class, ", ".join(plan.labels)),
        )
    except FilingRefused as exc:
        expect("derives declaring labels from the policy", False, exc.loud_message)

    # 2. The derived labels are PASSED to `gh issue create`, not merely computed.
    if plan is None:
        expect("passes the derived labels to `gh issue create`", False, "no plan to check")
    else:
        argv = list(plan.argv)
        pairs = [
            argv[index + 1]
            for index, token in enumerate(argv[:-1])
            if token == "--label"
        ]
        passed = [label for label in plan.labels if label in pairs]
        expect(
            "passes the derived labels to `gh issue create`",
            argv[:3] == ["gh", "issue", "create"]
            and len(passed) == len(plan.labels)
            and any(label.startswith(CLASS_FIELD + ":") for label in passed),
            "argv[0:3]=%s | --label pairs=%d of %d" % (argv[:3], len(passed), len(plan.labels)),
        )

    # 3. REFUSAL: a policy that declares no default class + a filing that declares
    #    none is refused, not filed unclassified.
    underivable = FilingRequest(title="t", body="b")
    classless_policy = replace(policy, filing_default_class="")
    refusal = None
    try:
        plan_filing(underivable, classless_policy)
        expect("refuses a filing with no derivable class", False, "it planned a filing anyway")
    except FilingRefused as exc:
        refusal = exc
        expect(
            "refuses a filing with no derivable class",
            CLASS_FIELD in exc.missing,
            "missing=%s" % ", ".join(exc.missing),
        )

    # 4. REFUSAL: a class that is not a rung of the ladder.
    try:
        plan_filing(
            FilingRequest(title="t", body="b", declared_class="platinum"), policy
        )
        expect("refuses a class outside the ladder", False, "it planned a filing anyway")
    except FilingRefused as exc:
        expect(
            "refuses a class outside the ladder",
            "platinum" in exc.reason,
            exc.reason,
        )

    # 5. REFUSAL: a required companion the filing and the policy both omit — the
    #    failure that filed #297 (`class:enterprise`, no `priority:`).
    thin_policy = replace(
        policy,
        filing_defaults={
            "type": "feature",
            "area": "governance",
            "gdc": "enterprise",
            "posture": "overall",
            "lifecycle": "build",
        },
    )
    try:
        plan_filing(FilingRequest(title="t", body="b"), thin_policy)
        expect("refuses an underivable companion label", False, "it planned a filing anyway")
    except FilingRefused as exc:
        expect(
            "refuses an underivable companion label",
            exc.missing == ("priority",),
            "missing=%s" % ", ".join(exc.missing),
        )

    # 6. The refusal files NOTHING: no subprocess is ever reached.
    calls: list = []

    def recorder(*call_args, **call_kwargs):  # pragma: no cover - must not run
        calls.append((call_args, call_kwargs))
        raise AssertionError("a refused filing must not invoke `gh`")

    try:
        file_issue(underivable, classless_policy, runner=recorder)
        expect("refusal files nothing (runner never reached)", False, "file_issue returned")
    except FilingRefused:
        expect(
            "refusal files nothing (runner never reached)",
            not calls,
            "runner invocations=%d" % len(calls),
        )

    # 7. The refusal is LOUD and names where prevention lives, so an operator sees
    #    a refused filing rather than discovering an unclassified issue later.
    text = refusal.loud_message if refusal is not None else ""
    expect(
        "refusal is explicit and points at #174/#320",
        "FILING REFUSED" in text and "#320" in text and "#174" in text,
        text,
    )

    # 8. The fleet's filing path DELEGATES to this seam (no `gh issue create` argv
    #    of its own — the shape that filed unclassified issues before this issue).
    problems = audit_filing_seam(args.root)
    expect(
        "fleet/brain.py filing path delegates to the seam",
        not problems,
        "; ".join(problem.message for problem in problems) or "no bypass found",
    )

    # 9. PASS-THROUGH (issue #517): a companion the policy recognises but the
    #    declared class does not require reaches the command instead of being
    #    dropped on the floor. `pillar` is in the policy's `prefixed` vocabulary and
    #    is NOT an `enterprise` expectation, which is exactly the shape that was
    #    silently discarded before this issue.
    with_pillar = FilingRequest(
        title="t", body="b", declaring={"pillar": "autonomous-ops", "phase": "8"}
    )
    if "pillar" in policy.expectations_for("enterprise"):
        expect(
            "passes through a recognised companion the class does not require",
            False,
            "the policy's own `enterprise` expectations now require `pillar`; this "
            "expectation needs a companion the rung does not require",
        )
    else:
        try:
            passed = plan_filing(with_pillar, policy)
            carried = [passed.label("pillar"), passed.label("phase")]
            expect(
                "passes through a recognised companion the class does not require",
                carried == ["pillar:autonomous-ops", "phase:8"],
                "labels=%s" % ", ".join(passed.labels),
            )
        except FilingRefused as exc:
            expect(
                "passes through a recognised companion the class does not require",
                False,
                exc.loud_message,
            )

    # 10. REFUSAL, not a drop: a declared label the policy does not recognise is
    #     refused BY NAME, so a typo (`priorty=`) cannot land an issue missing
    #     `priority:` while its filer believes it declared one.
    unknown = FilingRequest(title="t", body="b", declaring={"priorty": "P1"})
    try:
        plan_filing(unknown, policy)
        expect(
            "refuses a declared label the policy does not recognise",
            False,
            "it planned a filing anyway, silently dropping `priorty:`",
        )
    except FilingRefused as exc:
        expect(
            "refuses a declared label the policy does not recognise",
            exc.missing == ("priorty",) and "priorty" in exc.reason,
            exc.reason,
        )

    # 11. The unrecognised-declaration refusal also files NOTHING. The recorder
    #     returns a success rather than raising, so a mutant that removes the
    #     refusal is REPORTED as a failure instead of crashing the gate.
    unknown_calls: list = []

    def unknown_recorder(argv, **_kwargs):  # pragma: no cover - must not run
        unknown_calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, "https://github.com/o/r/issues/1", "")

    try:
        file_issue(unknown, policy, runner=unknown_recorder)
        expect("the unrecognised-declaration refusal files nothing", False, "it filed")
    except FilingRefused:
        expect(
            "the unrecognised-declaration refusal files nothing",
            not unknown_calls,
            "runner invocations=%d" % len(unknown_calls),
        )

    # 12. A class stated twice with two different values is refused: honouring one
    #     would drop the other silently, which is the defect class #517 removes.
    try:
        plan_filing(
            FilingRequest(
                title="t", body="b", declared_class="elite", declaring={"class": "enterprise"}
            ),
            policy,
        )
        expect("refuses two different classes on one filing", False, "it planned a filing")
    except FilingRefused as exc:
        expect(
            "refuses two different classes on one filing",
            CLASS_FIELD in exc.missing and "elite" in exc.reason and "enterprise" in exc.reason,
            exc.reason,
        )

    # 13. `--dry-run` shows the label set the real filing would carry: both plan
    #     through the same path, so a dry run cannot advertise labels the filing
    #     would not pass.
    dry_calls: list = []

    def dry_recorder(*call_args, **call_kwargs):  # pragma: no cover - must not run
        dry_calls.append((call_args, call_kwargs))
        raise AssertionError("a dry run must not invoke `gh`")

    dry = FilingRequest(
        title="t", body="b", declaring={"pillar": "autonomous-ops"}, dry_run=True
    )
    try:
        dry_result = file_issue(dry, policy, runner=dry_recorder)
        expect(
            "a dry run shows the same labels the filing would pass",
            dry_result.dry_run
            and not dry_calls
            and list(dry_result.labels) == list(plan_filing(dry, policy).labels)
            and "pillar:autonomous-ops" in dry_result.labels,
            "labels=%s" % ", ".join(dry_result.labels),
        )
    except FilingRefused as exc:
        expect("a dry run shows the same labels the filing would pass", False, exc.loud_message)

    # 14. TAG DIMENSIONS (issue #1182): a filing is BORN with the tag
    #     authority's two dimensions — `posture` and `lifecycle` — derived from
    #     the policy's `filing.tags` + `filing.defaults`, so the board stops
    #     accruing the `required-missing` deviations the tag authority reports.
    #     The same prevention #297 filed for `priority:` now holds for the tag
    #     dimensions: a filing that cannot derive them is refused, not filed
    #     half-classified.
    base_plan = None
    try:
        base_plan = plan_filing(FilingRequest(title="t", body="b"), policy)
        expect(
            "derives the tag dimensions for a bare filing",
            base_plan.label("posture") == "posture:overall"
            and base_plan.label("lifecycle") == "lifecycle:build",
            "labels=%s" % ", ".join(base_plan.labels),
        )
    except FilingRefused as exc:
        expect("derives the tag dimensions for a bare filing", False, exc.loud_message)

    tagless_defaults = {
        name: value
        for name, value in policy.filing_defaults.items()
        if name not in ("posture", "lifecycle")
    }
    tagless = replace(policy, filing_defaults=tagless_defaults)
    try:
        plan_filing(FilingRequest(title="t", body="b"), tagless)
        expect(
            "refuses a filing whose tag dimensions cannot be derived",
            False,
            "it planned a filing anyway, without the tag dimensions",
        )
    except FilingRefused as exc:
        expect(
            "refuses a filing whose tag dimensions cannot be derived",
            set(exc.missing) == {"posture", "lifecycle"},
            "missing=%s" % ", ".join(exc.missing),
        )

    # 15. RESOLVABILITY (issue #1160): deriving a label is not the same as the
    #     repository HAVING it. `gh issue create` refuses a label that does not
    #     exist, so a `filing.defaults` entry naming one breaks the DEFAULT filing
    #     path in production while a derivation-only control stays green — measured:
    #     the policy derived `area:governance`, which this repository has never had,
    #     so every filing that left `area` to the default was refused by GitHub.
    derived: tuple = ()
    try:
        derived = default_filing_labels(policy)
        unresolved = unresolvable_labels(derived, inventory)
        expect(
            "every label the filing defaults derive exists on the repository",
            not unresolved,
            "derived=%s | unresolved=%s"
            % (", ".join(derived), ", ".join(unresolved) or "(none)"),
        )
    except FilingRefused as exc:
        expect(
            "every label the filing defaults derive exists on the repository",
            False,
            exc.loud_message,
        )

    #     The refusal must be REACHABLE, and it must name the file, the label and
    #     the one refresh verb: a default naming a label the inventory does not
    #     record is refused by name, so the operator does not have to work out
    #     which artifact to re-record. The probe's premise is asserted rather than
    #     assumed — if the label it plants is ever recorded, this expectation FAILS
    #     instead of passing vacuously.
    probe_label = "area:conformance-label-probe"
    if probe_label in inventory:
        expect(
            "refuses a filing default naming a label the repository does not have",
            False,
            "%s IS recorded, so this probe cannot demonstrate the refusal"
            % probe_label,
        )
    else:
        planted = replace(
            policy,
            filing_defaults=dict(policy.filing_defaults, area="conformance-label-probe"),
        )
        planted_findings = audit_filing_labels(
            planted,
            inventory,
            policy_path=POLICY_RELPATH,
            inventory_path=LABELS_RELPATH,
        )
        planted_text = "; ".join(
            "%s REMEDY: %s" % (finding.message, finding.remediation)
            for finding in planted_findings
        )
        expect(
            "refuses a filing default naming a label the repository does not have",
            len(planted_findings) == 1
            and planted_findings[0].code == CODE_FILING_LABEL_UNRESOLVED
            and probe_label in planted_text
            and str(POLICY_RELPATH) in planted_text
            and LABELS_REFRESH_VERB in planted_text,
            planted_text or "no finding for `%s`" % probe_label,
        )

    #     ABSENCE FAILS CLOSED: the loader must refuse an inventory it cannot read
    #     (missing, unparseable, or empty) rather than return an empty vocabulary
    #     against which every label resolves. Without this half, deleting
    #     `labels.json` would turn the control off silently.
    absent = args.root / LABELS_RELPATH.with_name("labels.absent-probe.json")
    try:
        load_label_inventory(absent)
        expect(
            "refuses an unreadable label inventory (absence fails closed)",
            False,
            "it returned a vocabulary for a file that does not exist",
        )
    except LabelsUnavailable as exc:
        expect(
            "refuses an unreadable label inventory (absence fails closed)",
            LABELS_REFRESH_VERB in str(exc),
            str(exc),
        )

    unmet = [index for index, ok in enumerate(results, start=1) if not ok]
    if unmet:
        print(
            "filing-check: FAIL (%d of %d expectation(s) unmet: %s)"
            % (len(unmet), len(results), ", ".join(str(i) for i in unmet)),
            file=sys.stderr,
        )
        return EXIT_NOT_OK
    print("filing-check: OK (%d of %d expectations held)" % (len(results), len(results)))
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="governance/conformance/cli.py",
        description="CMR class/pattern/template conformance (issue #140).",
    )
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    sub = parser.add_subparsers(dest="command", required=True)

    p_check = sub.add_parser("check", help="classify the board and report")
    p_check.add_argument("--milestone", default=None)
    p_check.add_argument("--include-unmilestoned", action="store_true")
    p_check.add_argument(
        "--strict",
        action="store_true",
        help="escalate declared-class deviations to errors",
    )
    p_check.set_defaults(func=cmd_check)

    p_change = sub.add_parser("change-set", help="check the diff against mandates")
    p_change.add_argument("--base", default="origin/master")
    p_change.set_defaults(func=cmd_change_set)

    p_policy = sub.add_parser("policy", help="print the declared policy")
    p_policy.set_defaults(func=cmd_policy)

    p_labels = sub.add_parser(
        "labels",
        help="show the recorded label inventory, or re-record it (--refresh)",
    )
    p_labels.add_argument(
        "--refresh",
        action="store_true",
        help="re-record the inventory from the live label set (needs network)",
    )
    p_labels.add_argument(
        "--repo",
        default=DEFAULT_REPO,
        help="the repository whose labels the inventory records",
    )
    p_labels.set_defaults(func=cmd_labels)

    p_filing_check = sub.add_parser(
        "filing-check",
        help="the filing path derives declaring labels and refuses when it cannot",
    )
    p_filing_check.set_defaults(func=cmd_filing_check)

    p_file = sub.add_parser(
        "file", help="the supported hand-run filing path (declaring labels derived)"
    )
    p_file.add_argument("--title", required=True)
    p_file.add_argument("--body", required=True)
    p_file.add_argument("--repo", default=DEFAULT_REPO)
    p_file.add_argument(
        "--class",
        dest="declared_class",
        default="",
        help="the rung to declare; omit to take the policy's filing.default_class",
    )
    p_file.add_argument(
        "--declare",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help=(
            "declare a companion label explicitly (type/priority/area/gdc/pillar/"
            "phase/...); a name the policy does not recognise as a declaring label "
            "is REFUSED by name, never dropped — use --label for a label the policy "
            "does not declare"
        ),
    )
    p_file.add_argument(
        "--label", action="append", default=[], help="an extra non-declaring label"
    )
    p_file.add_argument(
        "--dry-run",
        action="store_true",
        help="print the command and the derived labels without filing anything",
    )
    p_file.set_defaults(func=cmd_file)

    p_report = sub.add_parser("report", help="write the report only")
    p_report.add_argument("--milestone", default=None)
    p_report.add_argument("--include-unmilestoned", action="store_true")
    p_report.set_defaults(func=cmd_report)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
