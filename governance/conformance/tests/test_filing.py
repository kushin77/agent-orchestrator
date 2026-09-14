"""The filing path derives declaring labels, or refuses (issue #320).

Negative controls first: a filing path whose refusal path cannot be reached is the
formality this issue exists to remove. Each test below either provokes a refusal
(class absent, class off the ladder, companion underivable) or proves that the
derived labels actually reach `gh issue create` — never that a call merely happened.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import replace
from pathlib import Path

from filing import (
    CLASS_FIELD,
    FilingRefused,
    FilingRequest,
    audit_filing_seam,
    derive_declaring_labels,
    file_issue,
    plan_filing,
)

ROOT = Path(__file__).resolve().parents[3]


def request(**overrides) -> FilingRequest:
    base = {"title": "a micro-task", "body": "Parent: #160\n"}
    base.update(overrides)
    return FilingRequest(**base)


class FakeRun:
    """A recording `subprocess.run` — proves what was (or was not) invoked."""

    def __init__(self, stdout: str = "https://github.com/o/r/issues/4321", returncode: int = 0,
                 stderr: str = "") -> None:
        self.calls: list = []
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr

    def __call__(self, argv, **_kwargs):
        self.calls.append(list(argv))
        return subprocess.CompletedProcess(argv, self.returncode, self.stdout, self.stderr)


# -- derivation: the labels come from the policy ------------------------------


def test_a_filing_that_declares_nothing_carries_every_declaring_label(policy):
    """POSITIVE CONTROL: the policy's `filing` block supplies class and companions."""
    plan = plan_filing(request(), policy)

    assert plan.label(CLASS_FIELD) == "class:enterprise"
    for name in policy.filing_label_names(plan.declared_class):
        assert plan.label(name), "the filing carries no `%s:` label" % name


def test_explicit_declarations_win_over_the_policy_defaults(policy):
    plan = plan_filing(
        request(declared_class="elite", declaring={"priority": "P0", "pillar": "governance"}),
        policy,
    )

    assert plan.label(CLASS_FIELD) == "class:elite"
    assert plan.label("priority") == "priority:P0"
    assert plan.label("pillar") == "pillar:governance"
    assert plan.label("type") == "type:feature"  # still derived


def test_the_labels_reach_the_gh_command(policy):
    """The labels are PASSED, not merely computed — the whole point of #320."""
    plan = plan_filing(request(declaring={"area": "session-fleet"}), policy)

    argv = list(plan.argv)
    assert argv[:3] == ["gh", "issue", "create"]
    passed = [argv[i + 1] for i, token in enumerate(argv[:-1]) if token == "--label"]
    assert passed == list(plan.labels)
    assert "class:enterprise" in passed
    assert "area:session-fleet" in passed


def test_extra_labels_are_appended_without_displacing_the_declaring_ones(policy):
    plan = plan_filing(request(labels=("pmo-sme", "area:board")), policy)

    assert plan.label("area") == "area:board"  # derived
    assert "pmo-sme" in plan.labels
    assert plan.labels.count("area:board") == 1  # deduped


# -- refusal: a filing that cannot derive a class fails loudly ----------------


def test_no_derivable_class_is_refused(policy):
    """NEGATIVE CONTROL: a policy with no default class cannot classify a filing."""
    classless = replace(policy, filing_default_class="")

    try:
        derive_declaring_labels(request(), classless)
    except FilingRefused as exc:
        assert "no class" in exc.reason
        assert CLASS_FIELD in exc.missing
    else:  # pragma: no cover - the failure this test exists to catch
        raise AssertionError("an underivable class was not refused")


def test_a_class_outside_the_ladder_is_refused(policy):
    """NEGATIVE CONTROL: 'platinum' names no rung, so it classifies nothing."""
    try:
        derive_declaring_labels(request(declared_class="platinum"), policy)
    except FilingRefused as exc:
        assert "platinum" in exc.reason
        assert "enterprise" in exc.reason  # the ladder is quoted back
    else:  # pragma: no cover
        raise AssertionError("a class outside the ladder was not refused")


def test_an_underivable_companion_is_refused_and_names_the_field(policy):
    """NEGATIVE CONTROL: the #297 shape — a class, no `priority:`."""
    thin = replace(policy, filing_defaults={"type": "feature", "area": "board", "gdc": "enterprise"})

    try:
        derive_declaring_labels(request(), thin)
    except FilingRefused as exc:
        assert exc.missing == ("priority",)
        assert "`priority:`" in exc.reason
    else:  # pragma: no cover
        raise AssertionError("an underivable companion was not refused")


def test_the_refusal_is_loud_and_names_prevention(policy):
    classless = replace(policy, filing_default_class="")
    try:
        plan_filing(request(), classless)
    except FilingRefused as exc:
        message = exc.loud_message
    else:  # pragma: no cover
        raise AssertionError("the filing was not refused")

    assert "FILING REFUSED" in message
    assert "#320" in message  # prevention
    assert "#174" in message  # the legacy repair


def test_a_refusal_files_nothing(policy):
    """The refusal precedes the command: `gh` is never invoked, so nothing lands."""
    classless = replace(policy, filing_default_class="")
    runner = FakeRun()

    try:
        file_issue(request(), classless, runner=runner)
    except FilingRefused:
        pass
    else:  # pragma: no cover
        raise AssertionError("an underivable filing was not refused")

    assert runner.calls == []


# -- the happy path still files -------------------------------------------------


def test_a_derivable_filing_runs_one_labelled_gh_command(policy):
    """POSITIVE CONTROL: exactly one call, from the seam, with every label on it."""
    runner = FakeRun()

    result = file_issue(request(declaring={"area": "session-fleet"}), policy, runner=runner)

    assert result.number == 4321
    assert result.dry_run is False
    assert len(runner.calls) == 1
    argv = runner.calls[0]
    assert argv[:3] == ["gh", "issue", "create"]
    assert "--label" in argv


def test_a_dry_run_files_nothing_and_plans_no_gh_call(policy):
    runner = FakeRun()

    result = file_issue(request(dry_run=True), policy, runner=runner)

    assert result.dry_run is True
    assert result.number is None
    assert runner.calls == []
    assert "class:enterprise" in result.labels
    assert result.plan.command.startswith("gh issue create")


def test_a_failed_gh_call_is_an_error_not_a_silent_success(policy):
    runner = FakeRun(stdout="", returncode=1, stderr="label not found")

    try:
        file_issue(request(), policy, runner=runner)
    except RuntimeError as exc:
        assert "label not found" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("a failed `gh issue create` was reported as success")


# -- a declaration is never silently dropped (issue #517) ----------------------
#
# Before #517 a `--declare`d companion outside the class's label set was accepted
# on the command line and discarded: `file --declare pillar=x --declare phase=8`
# planned `class:enterprise, type:…, priority:…, area:…, gdc:…` and no `pillar:`/
# `phase:` at all, with no warning and exit 0. The seam's doctrine is *refuse
# rather than drop*, so the tests below provoke BOTH halves of the fix: a
# recognised companion is passed through, an unrecognised one is refused by name.


def test_a_recognised_companion_is_passed_through_not_dropped(policy):
    """POSITIVE CONTROL (#517): `pillar`/`phase` are declaring labels the policy
    recognises (`prefixed`) but does not require at `enterprise`, so the old
    derivation discarded them."""
    plan = plan_filing(
        request(
            declared_class="enterprise",
            declaring={"pillar": "autonomous-ops", "phase": "8-autonomous-ops"},
        ),
        policy,
    )

    assert plan.label("pillar") == "pillar:autonomous-ops"
    assert plan.label("phase") == "phase:8-autonomous-ops"
    # passed to `gh`, not merely computed — and in policy order, after the derived set
    assert plan.labels[-2:] == ("pillar:autonomous-ops", "phase:8-autonomous-ops")
    argv = list(plan.argv)
    passed = [argv[i + 1] for i, token in enumerate(argv[:-1]) if token == "--label"]
    assert passed == list(plan.labels)


def test_the_issue_517_reproduction_now_carries_the_declared_labels(policy):
    """REGRESSION: the exact filing from the issue — every declared label survives."""
    plan = plan_filing(
        request(
            declared_class="enterprise",
            declaring={
                "type": "governance",
                "priority": "P1",
                "area": "fleet",
                "pillar": "autonomous-ops",
                "phase": "8-autonomous-ops",
            },
        ),
        policy,
    )

    assert plan.labels == (
        "class:enterprise",
        "type:governance",
        "priority:P1",
        "area:fleet",
        "gdc:enterprise",
        "pillar:autonomous-ops",
        "phase:8-autonomous-ops",
    )


def test_the_issue_517_reproduction_through_the_cli(policy):
    """The layer the defect was observed at: the CLI must PRINT the labels it would
    file. A `--dry-run` that hides a declaration is the same silent drop."""
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "governance" / "conformance" / "cli.py"),
            "file",
            "--dry-run",
            "--title",
            "T",
            "--body",
            "b",
            "--class",
            "enterprise",
            "--declare",
            "type=governance",
            "--declare",
            "priority=P1",
            "--declare",
            "area=fleet",
            "--declare",
            "pillar=autonomous-ops",
            "--declare",
            "phase=8-autonomous-ops",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "pillar:autonomous-ops" in result.stdout
    assert "phase:8-autonomous-ops" in result.stdout
    assert "--label pillar:autonomous-ops" in result.stdout


def test_an_unrecognised_declaration_is_refused_by_name(policy):
    """NEGATIVE CONTROL (#517): the policy does not know `priorty`, so refusing beats
    filing an issue that silently lacks the `priority:` its filer declared."""
    try:
        plan_filing(request(declaring={"priorty": "P1"}), policy)
    except FilingRefused as exc:
        assert exc.missing == ("priorty",)
        assert "`priorty:`" in exc.reason
        assert "--label" in exc.reason  # the explicit path for a label outside the policy
    else:  # pragma: no cover - the failure this test exists to catch
        raise AssertionError("an unrecognised declaration was dropped instead of refused")


def test_the_unrecognised_declaration_refusal_files_nothing(policy):
    runner = FakeRun()

    try:
        file_issue(request(declaring={"priorty": "P1"}), policy, runner=runner)
    except FilingRefused:
        pass
    else:  # pragma: no cover
        raise AssertionError("a filing with an unrecognised declaration was not refused")

    assert runner.calls == []


def test_the_cli_refuses_an_unrecognised_declaration_with_a_non_zero_exit():
    """The refusal is observable at the boundary an operator uses: exit 1, by name."""
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "governance" / "conformance" / "cli.py"),
            "file",
            "--dry-run",
            "--title",
            "T",
            "--body",
            "b",
            "--declare",
            "priorty=P1",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1, (result.returncode, result.stdout, result.stderr)
    assert "`priorty:`" in result.stderr
    assert "FILING REFUSED" in result.stderr


def test_two_different_classes_on_one_filing_are_refused(policy):
    """Honouring either one would drop the other silently."""
    try:
        plan_filing(
            request(declared_class="elite", declaring={"class": "enterprise"}), policy
        )
    except FilingRefused as exc:
        assert CLASS_FIELD in exc.missing
        assert "elite" in exc.reason and "enterprise" in exc.reason
    else:  # pragma: no cover
        raise AssertionError("one of two conflicting classes was dropped silently")


def test_a_declaration_with_no_value_is_refused(policy):
    """An empty declaration carries nothing, so falling back to the policy default
    would report a declaration that was never honoured."""
    try:
        plan_filing(request(declaring={"area": "   "}), policy)
    except FilingRefused as exc:
        assert exc.missing == ("area",)
        assert "`area:`" in exc.reason
    else:  # pragma: no cover
        raise AssertionError("a valueless declaration was dropped silently")


def test_a_dry_run_and_a_real_filing_plan_the_same_labels(policy):
    """A dry run that advertises labels the filing would not pass is the same lie."""
    runner = FakeRun()
    declaring = {"pillar": "autonomous-ops"}

    dry = file_issue(request(declaring=declaring, dry_run=True), policy, runner=runner)
    real = file_issue(request(declaring=declaring), policy, runner=runner)

    assert runner.calls == [list(real.plan.argv)]  # only the real filing called `gh`
    assert dry.labels == real.labels
    assert dry.plan.argv == real.plan.argv


# -- the seam audit: the fleet's filing path must delegate --------------------


def test_the_fleet_filing_path_delegates_to_the_seam():
    """The real tree: `fleet/brain.py` must not build its own `gh issue create`."""
    assert audit_filing_seam(ROOT) == ()


def test_a_filing_path_that_builds_its_own_gh_command_is_a_problem(tmp_path: Path):
    """NEGATIVE CONTROL: the pre-#320 shape — an argv of its own."""
    fleet = tmp_path / "fleet"
    fleet.mkdir(parents=True)
    (fleet / "brain.py").write_text(
        'def file_it():\n'
        '    subprocess.run(["gh", "issue", "create", "--title", "x"])\n',
        encoding="utf-8",
    )

    problems = audit_filing_seam(tmp_path)

    assert [problem.code for problem in problems] == ["filing-seam-bypassed"] * len(problems)
    assert any("its own `gh issue create`" in problem.message for problem in problems)


def test_a_filing_path_that_never_calls_the_seam_is_a_problem(tmp_path: Path):
    """NEGATIVE CONTROL: importing the seam and not using it is not a filing path."""
    fleet = tmp_path / "fleet"
    fleet.mkdir(parents=True)
    (fleet / "brain.py").write_text(
        "import filing\n\n\ndef file_it():\n    return 0\n", encoding="utf-8"
    )

    problems = audit_filing_seam(tmp_path)

    assert [problem.code for problem in problems] == ["filing-seam-bypassed"]
    assert "file_issue" in problems[0].message


def test_a_missing_filing_path_is_reported_not_assumed(tmp_path: Path):
    problems = audit_filing_seam(tmp_path)

    assert len(problems) == 1
    assert "cannot read" in problems[0].message


def test_the_seam_loads_when_another_flat_model_is_already_imported():
    """REGRESSION: the conformance package uses flat sibling imports, and the
    brain's process already has `governance/dispatch/model.py` importable under the
    same name. Loading the seam must neither fail nor resolve `filing`'s `model` to
    the dispatch one, and must leave the process's own `model` exactly as it was.
    """
    script = (
        "import sys\n"
        "sys.path.insert(0, %r)\n"
        "sys.path.insert(0, %r)\n"
        "import model\n"
        "print('shadow=' + model.__file__)\n"
        "import brain\n"
        "_, filing = brain._conformance()\n"
        "print('filing=' + filing.__file__)\n"
        "print('after=' + sys.modules['model'].__file__)\n"
        % (str(ROOT / "fleet"), str(ROOT / "governance" / "dispatch"))
    )
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=ROOT, capture_output=True, text=True
    )

    assert result.returncode == 0, result.stderr
    lines = dict(line.split("=", 1) for line in result.stdout.strip().splitlines())
    assert lines["filing"].endswith("governance/conformance/filing.py")
    assert lines["shadow"] == lines["after"], "the seam's import changed the caller's `model`"
    assert lines["after"].endswith("governance/dispatch/model.py")
