"""The reconciliation verdicts as they leave the process (issue #498).

`governance/reconcile` exists to make one refusal survive: a lane whose unmerged
work exists nowhere else is **shelved** — its worktree, branch and claim are kept
— and is never mistaken for a lane that was **reclaimed**. That refusal is only
worth anything if it survives the export, so this suite drives a REAL sweep (the
same `sweep()` the worker runs, over real heartbeat files) and then publishes its
`SweepReport` through `fleet/health_publish.py`, requiring each outcome to arrive
verbatim and the payload to carry no lane identity at all.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

FLEET = Path(__file__).resolve().parents[3] / "fleet"
if str(FLEET) not in sys.path:
    sys.path.insert(0, str(FLEET))

import health_publish as hp  # noqa: E402
import health_signals as hs  # noqa: E402
from governance.reconcile.heartbeat import ORPHAN, SUSPECT  # noqa: E402
from governance.reconcile.sweep import (  # noqa: E402
    FAILED_OUTCOME,
    PARKED,
    RECLAIMED,
    REPORTED,
    SHELVED_OUTCOME,
    sweep,
)

from conftest import AGENT, BRANCH, SESSION, WORKTREE, FakeOps, dead_pid  # noqa: E402

OLD = 1_000_000.0
HEAD = "0f1e2d3c"


def outcome_counts(collected) -> dict[str, float]:
    return {
        signal.labels[hs.LABEL_OUTCOME]: signal.value
        for signal in collected
        if signal.kind == hs.SIGNAL_RECONCILE_OUTCOME
    }


def publish(root: Path, report) -> dict[str, float]:
    """Publish a real sweep report the way production does — a dry, read-only pass."""
    (root / ".fleet").mkdir(exist_ok=True)
    observed = hp.observe(
        beats={},
        pids={},
        fleet_dir=root / ".fleet",
        root=root,
        sweep_report=report,
        head=HEAD,
    )
    return outcome_counts(hp.signals(observed))


def test_a_landed_lane_publishes_reclaimed(root: Path, beaten):
    beaten(at=OLD)
    report = sweep(root, at=OLD + 20 * 60, apply=True, ops=FakeOps(on_main=True))
    assert [action.outcome for action in report.actions] == [RECLAIMED]
    published = publish(root, report)
    assert published[RECLAIMED] == 1
    assert published[SHELVED_OUTCOME] == 0
    assert published[PARKED] == 0


def test_a_shelved_lane_publishes_shelved_and_never_reclaimed(root: Path, beaten):
    """The load-bearing negative: unmerged work is never traded for an unlocked issue."""
    beaten(at=OLD)
    ops = FakeOps(present=True, on_main=False, remotely=False)
    report = sweep(root, at=OLD + 20 * 60, apply=True, ops=ops)
    assert [action.outcome for action in report.actions] == [SHELVED_OUTCOME]
    assert "mark-shelved" in ops.calls

    published = publish(root, report)
    assert published[SHELVED_OUTCOME] == 1
    assert published[RECLAIMED] == 0
    assert published[PARKED] == 0
    # The judge's own verdict travels alongside, not instead: it IS an orphan.
    assert published[ORPHAN] == 1


def test_a_lane_preserved_only_remotely_publishes_parked_never_reclaimed(root: Path, beaten):
    beaten(at=OLD)
    report = sweep(root, at=OLD + 20 * 60, apply=True, ops=FakeOps(remotely=True))
    assert [action.outcome for action in report.actions] == [PARKED]
    published = publish(root, report)
    assert published[PARKED] == 1
    assert published[RECLAIMED] == 0


def test_a_suspect_lane_publishes_suspect_and_is_never_reported_as_reclaimed(root: Path, beaten):
    """A fresh beat behind a dead pid: reported, never torn down — and never healthy."""
    beaten(at=OLD + 20 * 60 - 10, pid=dead_pid())
    report = sweep(root, at=OLD + 20 * 60, ops=FakeOps())
    assert [action.status for action in report.actions] == [SUSPECT]
    assert [action.outcome for action in report.actions] == [REPORTED]
    published = publish(root, report)
    assert published[SUSPECT] == 1
    assert published[REPORTED] == 1
    assert published[RECLAIMED] == 0
    assert published[SHELVED_OUTCOME] == 0


def test_a_teardown_that_failed_publishes_failed(root: Path, beaten):
    """A step that could not finish is a finding, not a reclaimed lane."""
    beaten(at=OLD)
    report = sweep(root, at=OLD + 20 * 60, apply=True, ops=FakeOps(on_main=True, fail=("remove-worktree",)))
    assert [action.outcome for action in report.actions] == [FAILED_OUTCOME]
    published = publish(root, report)
    assert published[FAILED_OUTCOME] == 1
    assert published[RECLAIMED] == 0


def test_the_published_payload_names_no_lane(root: Path, beaten):
    """A session id is this fleet's `pod_id` (ADR-0022 D5); the ticket keeps the detail."""
    beaten(at=OLD)
    report = sweep(root, at=OLD + 20 * 60, apply=True, ops=FakeOps(present=True))
    assert publish(root, report)[SHELVED_OUTCOME] == 1

    (root / ".fleet").mkdir(exist_ok=True)
    observed = hp.observe(
        beats={},
        pids={},
        fleet_dir=root / ".fleet",
        root=root,
        sweep_report=report,
        head=HEAD,
    )
    rendered = json.dumps(hp.render(observed), sort_keys=True)
    for secret in (SESSION, WORKTREE, BRANCH, AGENT):
        assert secret not in rendered, f"the payload leaked {secret!r}"
    # And the only label keys present are the declared ones.
    keys = {
        attribute["key"]
        for point in rendered_points(rendered)
        for attribute in point["attributes"]
    }
    assert keys <= hs.LABEL_KEYS


def rendered_points(rendered_json: str) -> list[dict]:
    payload = json.loads(rendered_json)
    points: list[dict] = []
    for metric in payload["resourceMetrics"][0]["scopeMetrics"][0]["metrics"]:
        points.extend(metric["gauge"]["dataPoints"])
    return points


def test_an_unreadable_store_publishes_no_data_not_a_zero(root: Path):
    """Honest emptiness: "could not read" is never "nothing to reconcile"."""
    (root / ".fleet").mkdir(exist_ok=True)
    observed = hp.observe(
        beats={},
        pids={},
        fleet_dir=root / ".fleet",
        root=root,
        sweep_report=None,
        sweep_error="RuntimeError: git unavailable",
        head=HEAD,
    )
    assert outcome_counts(hp.signals(observed)) == {hs.NO_DATA: 1}
