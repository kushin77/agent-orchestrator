"""The queue-liveness signal: inbox depth, oldest-directive age, wedge count (issue #695).

The defect this signal exists to fix was measured, not imagined: the terminal loop
sat in an infinite retry on a closed-issue directive for many turns while every
health surface read *idle*, because `fleet/health.py` probes the RUNG (a live pid
and a fresh beat) and an idle loop whose mailbox is wedged has both.

So these tests do not merely assert that the numbers are computed. They assert the
signal can **leave healthy**, by name, for each arm: a directive retried with no
stage change reads `degraded`, a stale mailbox reads `degraded`, both together read
`failing`, and a clean queue reads `healthy`. A signal that cannot go non-healthy is
the exact formality this issue is about.
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import monitor  # noqa: E402

#: A clock pinned to "now" at collection time, so an age assertion is arithmetic
#: rather than a race, but stays valid however far the real calendar has moved —
#: a hardcoded past timestamp ages out and silently drifts past the directive TTL
#: whenever `monitor.main()`/`cmd_queue` fall back to the real wall clock
#: (`moment=None` -> `time.time()`), which is exactly what the two CLI-path tests
#: below do.
NOW = datetime.now(timezone.utc).timestamp()


def _stamp(seconds_ago: float = 0.0) -> str:
    return (datetime.fromtimestamp(NOW, tz=timezone.utc) - timedelta(seconds=seconds_ago)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _fleet(root: Path) -> Path:
    fleet = root / ".fleet"
    (fleet / "inbox").mkdir(parents=True, exist_ok=True)
    return fleet


def _directive(root: Path, directive_id: str, *, seconds_ago: float = 1.0) -> Path:
    """Plant one pending directive in the mailbox, stamped `seconds_ago`."""
    path = root / ".fleet" / "inbox" / f"{directive_id}.json"
    path.write_text(
        json.dumps(
            {
                "id": directive_id,
                "ts": _stamp(seconds_ago),
                "from": "brain",
                "to": "sister",
                "type": "directive",
                "task": {"kind": "work", "issue": 42},
            }
        ),
        encoding="utf-8",
    )
    return path


def _attempts(root: Path, directive_id: str, reasons: list[str], *, state: str = "pending") -> Path:
    """Plant the runaway guard's persisted retry history for one directive."""
    directory = root / ".fleet" / "attempts"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{directive_id}.json"
    path.write_text(
        json.dumps(
            {
                "directive_id": directive_id,
                "attempts": len(reasons),
                "cap": 5,
                "state": state,
                "first_seen": _stamp(600),
                "last_attempt": _stamp(5),
                "next_attempt_at": None,
                "reasons": reasons,
                "dead_lettered_at": None,
            }
        ),
        encoding="utf-8",
    )
    return path


def _dead_letter(root: Path, directive_id: str, reasons: list[str]) -> Path:
    """The terminal artifact: the guard retired this directive, but it is still queued."""
    directory = root / ".fleet" / "dead-letter"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{directive_id}.json"
    path.write_text(
        json.dumps(
            {
                "directive_id": directive_id,
                "attempts": len(reasons),
                "reasons": reasons,
                "reason": reasons[-1],
                "dropped_by": "runaway-guard",
                "dead_lettered_at": _stamp(5),
            }
        ),
        encoding="utf-8",
    )
    return path


def _measure(root: Path, **kwargs):
    return monitor.queue_liveness(root, moment=NOW, **kwargs)


# ── the healthy case, and the one that must never be reported as healthy ────


def test_a_clean_and_empty_mailbox_reads_healthy(tmp_path):
    _fleet(tmp_path)
    liveness = _measure(tmp_path)
    assert liveness.status == "healthy"
    assert (liveness.inbox_depth, liveness.oldest_directive_age, liveness.wedge_count) == (0, None, 0)
    assert liveness.level == monitor.HEALTHY


def test_an_absent_mailbox_is_cannot_assess_and_never_a_healthy_empty_queue(tmp_path):
    """A queue that cannot be read is not an empty queue — the whole point."""
    liveness = monitor.queue_liveness(tmp_path, moment=NOW)
    assert liveness.store_present is False
    assert liveness.status == "no-data"
    assert liveness.level == monitor.NO_DATA_LEVEL
    assert "no mailbox at" in liveness.reasons[0]
    assert str(tmp_path / ".fleet" / "inbox") in liveness.reasons[0]


def test_an_undatable_envelope_is_counted_but_its_age_is_unknown(tmp_path):
    """Counted, because it is pending; not aged, because guessing 0 hides staleness."""
    fleet = _fleet(tmp_path)
    (fleet / "inbox" / "brain-directive-undated.json").write_text(
        json.dumps({"id": "brain-directive-undated"}), encoding="utf-8"
    )
    liveness = _measure(tmp_path)
    assert liveness.inbox_depth == 1
    assert liveness.oldest_directive_age is None
    assert liveness.status == "healthy"


# ── the three measured fields ───────────────────────────────────────────────


def test_inbox_depth_and_oldest_age_are_measured_from_the_mailbox(tmp_path):
    _fleet(tmp_path)
    _directive(tmp_path, "brain-directive-newest", seconds_ago=10)
    _directive(tmp_path, "brain-directive-oldest", seconds_ago=400)
    _directive(tmp_path, "brain-directive-middle", seconds_ago=90)
    liveness = _measure(tmp_path)
    assert liveness.inbox_depth == 3
    assert liveness.oldest_directive_age == pytest.approx(400.0, abs=1.0)
    assert liveness.wedge_count == 0


def test_run_markers_counts_the_markers_only(tmp_path):
    fleet = _fleet(tmp_path)
    (fleet / "runs").mkdir(parents=True, exist_ok=True)
    (fleet / "runs" / "brain-directive-a.json").write_text("{}", encoding="utf-8")
    (fleet / "runs" / "brain-directive-a.log").write_text("", encoding="utf-8")
    liveness = _measure(tmp_path)
    assert liveness.run_markers == 1


# ── the wedge arm, by name ──────────────────────────────────────────────────


def test_a_directive_retried_with_no_stage_change_reads_degraded(tmp_path):
    _fleet(tmp_path)
    _directive(tmp_path, "brain-directive-stuck")
    _attempts(
        tmp_path,
        "brain-directive-stuck",
        ["run did not land: failed (rc=1)"] * 3,
    )
    liveness = _measure(tmp_path)
    assert liveness.status == "degraded"
    assert liveness.level == monitor.DEGRADED
    assert liveness.wedge_count == 1
    assert liveness.wedges[0].directive == "brain-directive-stuck"
    assert liveness.wedges[0].retries == 3
    assert "no stage change" in liveness.reasons[0]


def test_retries_that_change_stage_are_not_a_wedge(tmp_path):
    """Three different stages is a directive being diagnosed, not a wedge."""
    _fleet(tmp_path)
    _directive(tmp_path, "brain-directive-progressing")
    _attempts(
        tmp_path,
        "brain-directive-progressing",
        [
            "claim refused: no-chain-edge",
            "run did not land: failed (rc=1)",
            "orphaned claim held by another agent, no live run",
        ],
    )
    liveness = _measure(tmp_path)
    assert liveness.status == "healthy"
    assert liveness.wedge_count == 0


def test_one_stage_with_changing_numbers_is_still_a_wedge(tmp_path):
    """The measured live case: five attempts whose only difference is the numbers.

    `attempts/brain-directive-38136664…json` on this box records five
    "claim refused: stale — snapshot is Nm old" reasons. Compared as raw text they
    are five stages; as STAGES they are one attempt made five times.
    """
    _fleet(tmp_path)
    _directive(tmp_path, "brain-directive-stale-snapshot")
    _attempts(
        tmp_path,
        "brain-directive-stale-snapshot",
        [f"claim refused: stale — snapshot is {minutes}m old (threshold 15m)" for minutes in (29, 30, 31, 33, 36)],
    )
    liveness = _measure(tmp_path)
    assert liveness.status == "degraded"
    assert liveness.wedges[0].retries == 5


def test_a_retired_directive_that_is_still_pending_is_the_wedge(tmp_path):
    """Dead-lettered yet still in the mailbox: never retried AND never consumed.

    `runaway.dispatchable` excludes a retired directive from every future dispatch,
    so the order sits in the queue for good. Nothing else in the fleet reports it —
    this is the closed-issue directive the loop retried for many turns.
    """
    _fleet(tmp_path)
    _directive(tmp_path, "brain-directive-retired")
    _attempts(tmp_path, "brain-directive-retired", ["run did not land: failed (rc=1)"] * 5, state="dead-letter")
    _dead_letter(tmp_path, "brain-directive-retired", ["run did not land: failed (rc=1)"] * 5)
    liveness = _measure(tmp_path)
    assert liveness.status == "degraded"
    assert liveness.wedges[0].state == "dead-letter"
    assert "dead-letter" in liveness.reasons[0]


def test_a_directive_that_never_failed_is_waiting_not_wedged(tmp_path):
    _fleet(tmp_path)
    _directive(tmp_path, "brain-directive-queued")
    liveness = _measure(tmp_path)
    assert liveness.status == "healthy"
    assert liveness.wedge_count == 0


def test_a_directive_the_queue_already_shed_is_not_counted(tmp_path):
    """A retired directive that is NOT pending is the dead-letter mailbox's business."""
    _fleet(tmp_path)
    _attempts(tmp_path, "brain-directive-gone", ["run did not land: failed (rc=1)"] * 5)
    _dead_letter(tmp_path, "brain-directive-gone", ["run did not land: failed (rc=1)"] * 5)
    liveness = _measure(tmp_path)
    assert liveness.inbox_depth == 0
    assert liveness.wedge_count == 0
    assert liveness.status == "healthy"


# ── the age arm, and the two together ───────────────────────────────────────


def test_a_stale_directive_alone_reads_degraded(tmp_path):
    _fleet(tmp_path)
    _directive(tmp_path, "brain-directive-abandoned", seconds_ago=monitor.DIRECTIVE_TTL_SECONDS + 60)
    liveness = _measure(tmp_path)
    assert liveness.status == "degraded"
    assert liveness.level == monitor.DEGRADED
    assert liveness.wedge_count == 0
    assert "not being drained" in liveness.reasons[0]


def test_the_wedge_and_a_stale_age_together_read_failing(tmp_path):
    _fleet(tmp_path)
    _directive(tmp_path, "brain-directive-both", seconds_ago=monitor.DIRECTIVE_TTL_SECONDS + 60)
    _attempts(tmp_path, "brain-directive-both", ["run did not land: failed (rc=1)"] * 3)
    liveness = _measure(tmp_path)
    assert liveness.status == "failing"
    assert liveness.level == monitor.FAILING
    assert len(liveness.reasons) == 3  # the age arm, the wedge arm, and the ticket
    assert liveness.reasons[-1].startswith("ticket:")


def test_the_signal_drives_a_ticket_and_names_the_command(tmp_path):
    """ADR-0022 refusal 4: the signal drives a ticket; it takes no action itself."""
    _fleet(tmp_path)
    _directive(tmp_path, "brain-directive-ticket")
    _attempts(tmp_path, "brain-directive-ticket", ["run did not land: failed (rc=1)"] * 3)
    liveness = _measure(tmp_path)
    ticket = liveness.reasons[-1]
    assert ticket.startswith("ticket:")
    assert "ADR-0022 refusal 4" in ticket
    assert "monitor.py queue" in ticket


def test_the_threshold_is_honoured_and_is_not_hardcoded(tmp_path):
    """Raise the threshold and the same fixture is no longer a wedge."""
    _fleet(tmp_path)
    _directive(tmp_path, "brain-directive-threshold")
    _attempts(tmp_path, "brain-directive-threshold", ["run did not land: failed (rc=1)"] * 3)
    assert _measure(tmp_path).status == "degraded"
    assert _measure(tmp_path, wedge_retries=4).status == "healthy"
    assert _measure(tmp_path, wedge_retries=3).wedge_count == 1


def test_the_ttl_is_honoured_and_is_not_hardcoded(tmp_path):
    _fleet(tmp_path)
    _directive(tmp_path, "brain-directive-ttl", seconds_ago=600)
    assert _measure(tmp_path).status == "healthy"
    assert _measure(tmp_path, ttl_seconds=300).status == "degraded"


# ── what it writes, and what it must never write ────────────────────────────


def _digest(paths: list[Path]) -> str:
    """A content hash of a subtree, so a single mutated byte changes it."""
    digest = hashlib.sha256()
    for base in paths:
        for item in sorted(p for p in base.rglob("*") if p.is_file()):
            digest.update(str(item.relative_to(base)).encode())
            digest.update(item.read_bytes())
    return digest.hexdigest()


def test_the_measurement_writes_NOTHING_into_the_queue(tmp_path):
    """ADR-0022 refusal 4, as a negative control: read the queue, mutate none of it.

    The positive half matters as much as the assertion: the digest is proven to
    move when a queue file is touched, so this cannot pass by being blind.
    """
    _fleet(tmp_path)
    _directive(tmp_path, "brain-directive-untouched")
    _attempts(tmp_path, "brain-directive-untouched", ["run did not land: failed (rc=1)"] * 3)
    stores = [tmp_path / ".fleet" / name for name in ("inbox", "attempts", "runs")]

    before = _digest(stores)
    assert monitor.main(["queue", "--root", str(tmp_path)]) == monitor.DEGRADED
    assert monitor.main(["queue", "--root", str(tmp_path), "--json"]) == monitor.DEGRADED
    assert _digest(stores) == before

    victim = tmp_path / ".fleet" / "inbox" / "brain-directive-untouched.json"
    victim.write_text(victim.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    assert _digest(stores) != before


def test_publishing_writes_one_artifact_a_projection_can_join(tmp_path):
    _fleet(tmp_path)
    path = monitor.publish_queue_liveness(_measure(tmp_path), tmp_path)
    assert path == tmp_path / ".fleet" / "queue-liveness.json"
    entry = json.loads(path.read_text(encoding="utf-8"))
    assert entry["inbox_depth"] == 0
    assert entry["oldest_directive_age"] is None
    assert entry["wedge_count"] == 0
    assert entry["status"] == "healthy"
    assert not path.with_suffix(".tmp").exists()


def test_the_published_artifact_is_rewritten_atomically_each_tick(tmp_path):
    _fleet(tmp_path)
    path = monitor.publish_queue_liveness(_measure(tmp_path), tmp_path)
    first = path.read_text(encoding="utf-8")
    _directive(tmp_path, "brain-directive-new")
    monitor.publish_queue_liveness(_measure(tmp_path), tmp_path)
    second = path.read_text(encoding="utf-8")
    assert first != second
    assert json.loads(second)["inbox_depth"] == 1
    assert not path.with_suffix(".tmp").exists()


# ── the surfaces a human and a machine read ─────────────────────────────────


def test_the_change_only_line_carries_the_wedge(tmp_path):
    """The dashboard tails `.fleet/monitor.log`; the wedge must be legible there."""
    _fleet(tmp_path)
    _directive(tmp_path, "brain-directive-line")
    _attempts(tmp_path, "brain-directive-line", ["run did not land: failed (rc=1)"] * 3)
    line = monitor.queue_line(_measure(tmp_path))
    assert "queue=degraded" in line
    assert "depth=1" in line
    assert "wedge=1" in line


def test_the_cli_prints_the_published_shape_and_exits_with_the_signal(tmp_path, capsys):
    _fleet(tmp_path)
    _directive(tmp_path, "brain-directive-cli")
    _attempts(tmp_path, "brain-directive-cli", ["run did not land: failed (rc=1)"] * 3)
    assert monitor.main(["queue", "--root", str(tmp_path)]) == monitor.DEGRADED
    printed = capsys.readouterr().out
    assert "degraded (signal 1)" in printed
    assert "inbox_depth=1" in printed

    assert monitor.main(["queue", "--root", str(tmp_path), "--json"]) == monitor.DEGRADED
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "degraded"
    assert payload["wedge_count"] == 1
    assert payload["inbox_depth"] == 1
    assert payload["oldest_directive_age"] is not None


def test_the_cli_exits_three_for_cannot_assess(tmp_path, capsys):
    """3, outside the 0/1/2 health tri-state, so it can never read as a verdict."""
    assert monitor.main(["queue", "--root", str(tmp_path)]) == 3
    assert "no-data (signal 3)" in capsys.readouterr().out
