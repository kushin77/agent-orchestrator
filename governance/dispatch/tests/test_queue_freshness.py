"""The committed dispatch queue's snapshot-age bound (issue #1189).

`governance/dispatch/queue_freshness.py` states the age this consumer tolerates
on the committed board snapshot, so the check that owns the committed queue
(`scripts/check-dispatch-queue.sh`) can reach a REAL verdict instead of a
permanent rc 2 that `verify.sh` records as SKIP.

Covers: a dated snapshot inside the tolerance is OK (including exactly at it);
one past it is refused BY NAME — file, timestamp, age, tolerance and the one
refresh verb; an unaged and a garbled timestamp are refused as `unaged`; a
missing, unreadable or non-object snapshot fails CLOSED as CannotAssess; the
tolerance is the committed-artifact one and demonstrably NOT the 15-minute
liveness bound the dispatch loop uses; and the two refusal codes mirror the
ticket projection's for this same artifact, so two consumers of one file cannot
drift into two vocabularies.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

import cli  # noqa: F401 — imported first: it puts governance/ on sys.path for board_selfheal
import board_selfheal
import queue_freshness
import snapshot as snapshot_mod
from model import REASON_SNAPSHOT_STALE

NOW = datetime(2026, 9, 18, 15, 0, 0, tzinfo=timezone.utc)


def _write(tmp_path: Path, payload: object) -> Path:
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _dated(tmp_path: Path, generated_at: str | None) -> Path:
    payload: dict[str, object] = {"source": "test", "issues": []}
    if generated_at is not None:
        payload["generated_at"] = generated_at
    return _write(tmp_path, payload)


def _ticket_model():
    """Load `governance/ticket/model.py` by path.

    By PATH, not by name: this suite's `conftest.py` puts the dispatch package
    first on `sys.path` and evicts the bare name `model`, precisely because
    sibling `governance/*` packages share that basename.

    The module is registered in `sys.modules` BEFORE `exec_module`, because that
    file declares dataclasses under `from __future__ import annotations`: with
    string annotations, `dataclasses` resolves each field's type through
    `sys.modules[cls.__module__]`, so an unregistered module raises
    `AttributeError: 'NoneType' object has no attribute '__dict__'`.
    """
    path = Path(__file__).resolve().parents[3] / "governance" / "ticket" / "model.py"
    spec = importlib.util.spec_from_file_location("ticket_model_for_mirror", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_a_snapshot_inside_the_tolerance_is_ok(tmp_path):
    verdict = queue_freshness.assess(_dated(tmp_path, "2026-09-18T14:00:00Z"), now=NOW)
    assert verdict.ok
    assert verdict.age_hours == pytest.approx(1.0)
    assert verdict.findings == ()
    assert "generated_at 2026-09-18T14:00:00Z" in verdict.render()
    assert "tolerance 72h" in verdict.render()


def test_a_snapshot_at_exactly_the_tolerance_is_still_ok(tmp_path):
    """The bound is `<=` tolerance: the boundary must not be a coin flip."""
    assert queue_freshness.assess(_dated(tmp_path, "2026-09-15T15:00:00Z"), now=NOW).ok


def test_the_tolerance_is_the_committed_artifact_age_not_the_liveness_one(tmp_path):
    """Two different questions about one file must not collapse into one number.

    15 minutes is what a LOOP that refreshes in-cycle can honour (and what
    `claims.arbitrate` keeps using); a file committed to the tree and read
    offline cannot meet it, and arming this check with it is what made the check
    permanently CANNOT-ASSESS (issue #1189).
    """
    assert queue_freshness.DEFAULT_MAX_AGE_HOURS == 72
    assert queue_freshness.tolerance_minutes() == 72 * 60
    liveness_minutes = snapshot_mod.DEFAULT_STALENESS_MINUTES
    assert liveness_minutes == 15
    assert queue_freshness.tolerance_minutes() > liveness_minutes
    # ... and a snapshot that the liveness rule calls stale is inside THIS bound,
    # which is exactly the case that was invisible while the check used it.
    aged_for_a_loop = _dated(tmp_path, "2026-09-18T04:00:00Z")  # 11h old
    assert snapshot_mod.is_stale(snapshot_mod.load(aged_for_a_loop), liveness_minutes, NOW)
    assert queue_freshness.assess(aged_for_a_loop, now=NOW).ok


def test_a_snapshot_past_the_tolerance_is_refused_by_name(tmp_path):
    path = _dated(tmp_path, "2026-09-01T00:00:00Z")
    verdict = queue_freshness.assess(path, now=NOW)
    assert not verdict.ok
    (finding,) = verdict.findings
    assert finding.code == queue_freshness.CODE_BOARD_STALE
    rendered = finding.render()
    assert str(path) in rendered
    assert "2026-09-01T00:00:00Z" in rendered
    assert "beyond the 72h" in rendered
    assert queue_freshness.REFRESH_COMMAND in rendered


def test_a_snapshot_with_no_generated_at_is_unaged(tmp_path):
    verdict = queue_freshness.assess(_dated(tmp_path, None), now=NOW)
    assert not verdict.ok
    (finding,) = verdict.findings
    assert finding.code == queue_freshness.CODE_BOARD_UNAGED
    assert queue_freshness.REFRESH_COMMAND in finding.render()


def test_a_garbled_generated_at_is_unaged(tmp_path):
    verdict = queue_freshness.assess(_dated(tmp_path, "not-a-timestamp"), now=NOW)
    assert not verdict.ok
    (finding,) = verdict.findings
    assert finding.code == queue_freshness.CODE_BOARD_UNAGED
    assert "not-a-timestamp" in finding.render()


def test_a_missing_snapshot_is_cannot_assess(tmp_path):
    with pytest.raises(queue_freshness.CannotAssess) as exc:
        queue_freshness.assess(tmp_path / "absent.json", now=NOW)
    assert "missing" in str(exc.value)


def test_an_unreadable_snapshot_is_cannot_assess(tmp_path):
    path = tmp_path / "snapshot.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(queue_freshness.CannotAssess) as exc:
        queue_freshness.assess(path, now=NOW)
    assert "unreadable" in str(exc.value)


def test_a_non_object_snapshot_is_cannot_assess(tmp_path):
    with pytest.raises(queue_freshness.CannotAssess) as exc:
        queue_freshness.assess(_write(tmp_path, ["not", "an", "object"]), now=NOW)
    assert "not a JSON object" in str(exc.value)


def test_a_naive_timestamp_is_read_as_utc_not_local(tmp_path):
    """A naive stamp is the fleet's UTC; reading it as local time would move the
    age by the host's offset and make the verdict venue-dependent."""
    verdict = queue_freshness.assess(_dated(tmp_path, "2026-09-18T14:00:00"), now=NOW)
    assert verdict.ok
    assert verdict.age_hours == pytest.approx(1.0)


def test_the_refusal_codes_mirror_the_ticket_consumers():
    """Same condition, same artifact, one vocabulary — mirrored AND PROVEN EQUAL,
    because a consumer that reads only its own values can never notice a drift."""
    ticket = _ticket_model()
    assert queue_freshness.CODE_BOARD_STALE == ticket.CODE_BOARD_STALE
    assert queue_freshness.CODE_BOARD_UNAGED == ticket.CODE_BOARD_UNAGED


def test_the_age_refusal_is_not_the_liveness_refusal():
    """`snapshot-stale` is the loop's liveness refusal, which a caller retries
    in-cycle; `board-snapshot-stale` is the committed artifact's age refusal.
    Conflating them is what hid six closed findings behind a SKIP."""
    assert queue_freshness.CODE_BOARD_STALE != REASON_SNAPSHOT_STALE
    assert REASON_SNAPSHOT_STALE == "snapshot-stale"


def test_cli_freshness_is_tri_state(tmp_path, capsys):
    fresh = _dated(tmp_path, snapshot_mod.now_iso())
    assert cli.main(["freshness", "--snapshot", str(fresh)]) == 0
    assert "freshness: OK" in capsys.readouterr().out

    aged = _dated(tmp_path, "2026-09-01T00:00:00Z")
    assert cli.main(["freshness", "--snapshot", str(aged)]) == 1
    assert queue_freshness.CODE_BOARD_STALE in capsys.readouterr().err

    assert cli.main(["freshness", "--snapshot", str(tmp_path / "absent.json")]) == 2
    assert "CANNOT-ASSESS" in capsys.readouterr().err

    assert cli.main(["freshness", "--snapshot", str(fresh), "--now", "not-a-time"]) == 2
    assert "CANNOT-ASSESS" in capsys.readouterr().err


def test_cli_freshness_without_refresh_never_touches_the_network(tmp_path, capsys, monkeypatch):
    """The FIX-BEFORE state (issue #1692): a stale snapshot fails outright, and
    no --refresh means self_heal is never even reachable — the read verb must
    not touch the network unasked."""

    def _boom(*args, **kwargs):
        raise AssertionError("refresh must not be called without --refresh")

    monkeypatch.setattr(board_selfheal, "refresh", _boom)
    aged = _dated(tmp_path, "2026-09-01T00:00:00Z")
    assert cli.main(["freshness", "--snapshot", str(aged)]) == 1
    err = capsys.readouterr().err
    assert queue_freshness.CODE_BOARD_STALE in err
    assert "no self-heal attempted" in err


def test_cli_freshness_self_heals_when_the_refresh_succeeds(tmp_path, capsys, monkeypatch):
    """The FIX (issue #1692): stale-but-refreshable passes — the gate refreshes
    the committed record itself through its own declared remedy and re-assesses,
    instead of failing on an age a self-heal could have cleared."""
    aged = _dated(tmp_path, "2026-09-01T00:00:00Z")

    def _fake_refresh(repo=None, *, runner=None, timeout=None):
        aged.write_text(
            json.dumps({"source": "test", "issues": [], "generated_at": snapshot_mod.now_iso()}),
            encoding="utf-8",
        )
        return True, "refreshed 0 issue(s) from test/repo"

    monkeypatch.setattr(board_selfheal, "refresh", _fake_refresh)
    assert cli.main(["freshness", "--snapshot", str(aged), "--refresh"]) == 0
    out = capsys.readouterr().out
    assert "freshness: OK (self-healed" in out


def test_cli_freshness_fails_with_reason_when_the_refresh_is_impossible(tmp_path, capsys, monkeypatch):
    """The FIX's other half: stale-and-unrefreshable still fails, and the
    refusal names WHY the self-heal did not clear it (no gh auth / offline)."""
    aged = _dated(tmp_path, "2026-09-01T00:00:00Z")

    def _offline_refresh(repo=None, *, runner=None, timeout=None):
        return False, "gh issue list failed (1): gh: not logged into any GitHub hosts"

    monkeypatch.setattr(board_selfheal, "refresh", _offline_refresh)
    assert cli.main(["freshness", "--snapshot", str(aged), "--refresh"]) == 1
    err = capsys.readouterr().err
    assert queue_freshness.CODE_BOARD_STALE in err
    assert "self-heal refresh failed: gh issue list failed" in err
