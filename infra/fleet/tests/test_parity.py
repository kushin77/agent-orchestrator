"""Tests for infra/fleet/parity.py (issue #714, EPIC #706 D6).

No network, no live `.fleet`/`.board`: every fixture builds its own tmp
snapshot and points `AO_FLEET_DIR`/`AO_FLEET_BOARD_DIR` at it. The role table
under test is a fast, fully-controlled fake — real dry-run dispatch (prune.py,
reconcile) is exercised once, lightly, in `test_real_roles_dry_run_agrees`,
bounded and skipped if either module cannot import cleanly in this environment.

Every gate in this repo proves both its pass and fail path (AGENTS.md rule 8:
"no-false-green gates"), so this suite provokes: a diverging persona output, a
non-idempotent second pass, and a one_writer overlap — each must turn the
verdict `not-ok` and land in `diffs`/`idempotency`, never pass silently.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
PARITY_DIR = HERE.parent
REPO = PARITY_DIR.parent.parent
for path in (PARITY_DIR, REPO):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import parity  # noqa: E402


@pytest.fixture()
def snapshot(tmp_path: Path) -> tuple[Path, Path]:
    fleet_dir = tmp_path / "fleet"
    board_dir = tmp_path / "board"
    (fleet_dir / "sent").mkdir(parents=True)
    (fleet_dir / "sent" / "msg-1.json").write_text('{"id": 1}\n', encoding="utf-8")
    board_dir.mkdir(parents=True, exist_ok=True)
    (board_dir / "claims.jsonl").write_text("", encoding="utf-8")
    return fleet_dir, board_dir


class FakeRole:
    def __init__(self, marker: str, name: str, argv: tuple[str, ...], why: str = "fake"):
        self.marker = marker
        self.name = name
        self.argv = argv
        self.why = why

    @property
    def disposition(self) -> str:
        return "dry-run" if self.argv else "not-dispatched"


# ---------------------------------------------------------------------------
# Unit-level: normalization, manifest emptiness, role-table drift.
# ---------------------------------------------------------------------------


def test_normalize_strips_volatile_fields():
    text = f"at 2026-09-16T01:02:03Z pid=1234 took 0.42s session_id=abc123 under {REPO}"
    normalized = parity.normalize(text)
    assert "2026-09-16T01:02:03Z" not in normalized
    assert "pid=1234" not in normalized
    assert "0.42s" not in normalized
    assert "session_id=abc123" not in normalized
    assert "<repo>" in normalized


def test_normalize_collapses_the_live_orphan_fingerprint():
    """Reconcile's live session fingerprint is not a persona decision.

    ``reconcile watch --once`` reads the MAIN checkout's shared ``.fleet``
    (``orphans.fleet_root``, #1436), not this harness's isolated snapshot, so it
    names LIVE sessions by their own fingerprint. Two dispatches of the same role
    must normalize to the same text even when that live set differs.
    """
    line_a = (
        "finding: would-resolve  reconcile:suspect:0c4c7806e9d5 — the session "
        "0c4c7806e9d5 no longer beats; a pass with --apply closes #1952 and "
        "retires the fingerprint"
    )
    line_b = (
        "finding: would-resolve  reconcile:suspect:adb1892verify — the session "
        "adb1892verify no longer beats; a pass with --apply closes #1952 and "
        "retires the fingerprint"
    )
    assert "0c4c7806e9d5" not in parity.normalize(line_a)
    assert "adb1892verify" not in parity.normalize(line_b)
    assert parity.normalize(line_a) == parity.normalize(line_b)
    # Non-vacuity: two sessions closing DIFFERENT issues stay distinguished, so a
    # decision-level difference cannot hide behind the collapsed fingerprint.
    assert parity.normalize(line_b) != parity.normalize(line_b.replace("#1952", "#1888"))


def test_manifest_is_empty_true_for_no_changes():
    manifest = {"fleet": {"added": [], "removed": [], "changed": []}, "board": {"added": [], "removed": [], "changed": []}}
    assert parity.manifest_is_empty(manifest)


def test_manifest_is_empty_false_when_something_changed():
    manifest = {"fleet": {"added": ["x"], "removed": [], "changed": []}, "board": {"added": [], "removed": [], "changed": []}}
    assert not parity.manifest_is_empty(manifest)


def test_check_role_table_agrees_with_dev_run_roles():
    markers = [role.marker for role in importlib.import_module("dev_run").ROLES]
    assert parity.check_role_table(markers) == []


def test_check_role_table_flags_drift():
    findings = parity.check_role_table(["ao-fleet-watchdog", "ao-fleet-prune", "ao-fleet-reconcile", "ao-fleet-mystery"])
    assert any("ao-fleet-mystery" in item for item in findings)


# ---------------------------------------------------------------------------
# Integration: a full run() with a fake, fast, controllable role table.
# ---------------------------------------------------------------------------


def _run_with_roles(monkeypatch, tmp_path, roles, evidence_name="evidence.json", timeout=30):
    roles = tuple(roles)
    monkeypatch.setattr(parity.dev_run, "ROLES", roles)
    monkeypatch.setattr(parity, "load_markers", lambda: [role.marker for role in roles])
    monkeypatch.setenv("AO_FLEET_DIR", str(tmp_path / "live-fleet"))
    monkeypatch.setenv("AO_FLEET_BOARD_DIR", str(tmp_path / "live-board"))
    (tmp_path / "live-fleet").mkdir()
    (tmp_path / "live-board").mkdir()
    evidence_path = tmp_path / evidence_name
    rc = parity.run(ticks=1, timeout=timeout, evidence_path=evidence_path)
    document = json.loads(evidence_path.read_text(encoding="utf-8"))
    return rc, document


def test_agreeing_personas_pass_clean(monkeypatch, tmp_path):
    roles = [FakeRole("ao-fleet-watchdog", "watchdog", ())]
    rc, document = _run_with_roles(monkeypatch, tmp_path, roles)
    assert rc == parity.OK
    assert document["verdict"] == "ok"
    assert document["diffs"] == []
    assert document["one_writer"] is True
    assert document["ticks"] == 1


def test_diverging_persona_output_fails_and_is_reported(monkeypatch, tmp_path):
    """Provoked failure #1: local and container personas disagree — must go red."""
    marker = "ao-fleet-prune"
    role = FakeRole(marker, "prune", (str(tmp_path / "does-not-exist.py"),))
    # Force divergence by making dispatch_role report a different rc per persona.
    real_dispatch = parity.dispatch_role

    def fake_dispatch(role_, persona, timeout):
        record = real_dispatch(role_, persona, timeout)
        record["rc"] = 0 if persona.name == "local" else 1
        record["output"] = f"decision-for-{persona.name}"
        return record

    monkeypatch.setattr(parity, "dispatch_role", fake_dispatch)
    rc, document = _run_with_roles(monkeypatch, tmp_path, [role])
    assert rc == parity.NOT_OK
    assert document["verdict"] == "not-ok"
    assert document["diffs"], "a real divergence between personas must land in diffs"
    assert any(entry["role"] == "prune" for entry in document["diffs"])


def test_non_idempotent_second_pass_is_reported(monkeypatch, tmp_path):
    """Provoked failure #2: a role that writes again on its second (lost-lock) dispatch."""
    role = FakeRole("ao-fleet-reconcile", "reconcile", ("reconcile-fake",))
    calls = {"count": 0}
    real_dispatch = parity.dispatch_role

    def fake_dispatch(role_, persona, timeout):
        calls["count"] += 1
        # Every SECOND call for a given persona (the lost-lock re-dispatch)
        # writes an extra file — simulating a rung that is not idempotent.
        if calls["count"] % 2 == 0:
            (persona.fleet_dir / f"stray-{calls['count']}.json").write_text("{}\n", encoding="utf-8")
        return real_dispatch(FakeRole(role_.marker, role_.name, ()), persona, timeout)

    monkeypatch.setattr(parity, "dispatch_role", fake_dispatch)
    rc, document = _run_with_roles(monkeypatch, tmp_path, [role])
    assert rc == parity.NOT_OK
    assert document["idempotency"], "a non-idempotent second pass must be reported, not silently accepted"
    assert document["verdict"] == "not-ok"


def test_one_writer_overlap_is_reported(monkeypatch, tmp_path):
    """Provoked failure #3: both personas attributably write the SAME logical path."""
    role = FakeRole("ao-fleet-prune", "prune", ("prune-fake",))
    real_dispatch = parity.dispatch_role

    def fake_dispatch(role_, persona, timeout):
        (persona.fleet_dir / "shared-state.json").write_text("{}\n", encoding="utf-8")
        return real_dispatch(FakeRole(role_.marker, role_.name, ()), persona, timeout)

    monkeypatch.setattr(parity, "dispatch_role", fake_dispatch)
    rc, document = _run_with_roles(monkeypatch, tmp_path, [role])
    assert rc == parity.NOT_OK
    assert document["one_writer"] is False
    assert any(entry["field"] == "one_writer" for entry in document["diffs"])


def test_role_table_drift_refuses_and_is_cannot_assess(monkeypatch, tmp_path):
    monkeypatch.setattr(parity.dev_run, "ROLES", (FakeRole("ao-fleet-nonexistent", "nonexistent", ()),))
    monkeypatch.setenv("AO_FLEET_DIR", str(tmp_path / "live-fleet"))
    monkeypatch.setenv("AO_FLEET_BOARD_DIR", str(tmp_path / "live-board"))
    (tmp_path / "live-fleet").mkdir()
    (tmp_path / "live-board").mkdir()
    evidence_path = tmp_path / "evidence.json"
    rc = parity.run(ticks=1, timeout=30, evidence_path=evidence_path)
    document = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert rc == parity.CANNOT_ASSESS
    assert document["verdict"] == "cannot-assess"
    assert document["refusal"]["code"] == "role-table-drift"


def test_lease_asserted_is_false_when_fleet_lease_absent():
    """fleet/lease.py (D5) is not landed in this checkout; this must be honest, not hardcoded."""
    lease_path = REPO / "fleet" / "lease.py"
    if lease_path.exists():
        pytest.skip("fleet/lease.py has landed — lease_asserted may legitimately be true now")
    assert parity.lease_asserted() is False


class ArgparseNamespace:  # noqa: N801 — tiny stand-in, not a pytest fixture
    pass


def test_cron_disable_comments_lines_never_deletes(monkeypatch):
    """Issue #714's crontab acceptance box, exercised without touching the real crontab."""
    sys.path.insert(0, str(REPO / "fleet"))
    cron = importlib.import_module("cron")

    # The foreign line's schedule is referenced from fleet/cron.py (the crontab's
    # single owner), never a literal here: scripts/check-fleet-cron-image.sh
    # refuses any file under infra/fleet/ that embeds a five-field crontab
    # schedule. The line stays foreign because its marker is not one of
    # fleet/cron.DECLARED_MARKERS.
    foreign_line = f"{cron.PRUNE_SCHEDULE} echo foreign # not-ours"
    installed = [cron.line(2), cron.prune_line(), cron.reconcile_line(2), foreign_line]
    written: dict[str, list[str]] = {}

    monkeypatch.setattr(cron, "read_crontab", lambda: list(installed))
    monkeypatch.setattr(cron, "write_crontab", lambda lines: written.setdefault("lines", lines))

    rc = cron.cmd_disable(ArgparseNamespace())
    assert rc == 0
    result = written["lines"]
    assert len(result) == len(installed), "disable must comment, never delete, a marked line"
    assert result[-1] == foreign_line, "a foreign crontab line is never touched"
    for entry in result[:-1]:
        assert entry.startswith("# "), "every ao-fleet-* line must be commented out, not removed"


@pytest.mark.parametrize("module_name", ["fleet.prune", "governance.reconcile.cli"])
def test_real_roles_dry_run_agrees(monkeypatch, tmp_path, module_name):
    """One light real-dispatch check: the actual prune/reconcile dry-run forms must
    produce IDENTICAL normalized decisions across personas on an isolated, empty
    snapshot — asserted, not merely "some rc came back" (a test that cannot fail
    is the thing this repo's gates refuse by name). Bounded (single tick); this
    is a smoke check, not a substitute for `make fleet-parity`.

    The bound is 120s, not the 30s the fake-role harnesses use: the real
    `reconcile watch --once` dispatch MEASURES ~29.5s on an idle box (29.32s and
    29.62s, two runs), so a 30s bound left ~2% headroom and tipped one persona
    into a spurious `TIMEOUT` under any parallel gate load — a red whose diff was
    a BOUND, not a divergence. 120s matches `infra/fleet/parity.py`'s own
    `--timeout` default and still bounds the smoke check.
    """
    role_by_module = {
        "fleet.prune": parity.dev_run.ROLES[1],
        "governance.reconcile.cli": parity.dev_run.ROLES[2],
    }
    role = role_by_module[module_name]
    try:
        importlib.import_module(module_name)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"{module_name} could not be imported in this environment: {exc}")
    rc, document = _run_with_roles(
        monkeypatch, tmp_path, [role], evidence_name=f"{role.name}.json", timeout=120
    )
    assert document["ticks"] == 1
    assert document["diffs"] == [], "local and container personas must agree on the real dry-run decision"
    assert document["idempotency"] == [], "the real dry-run role must be a no-op on its lost-lock re-dispatch"
    assert document["one_writer"] is True
    assert rc == parity.OK
