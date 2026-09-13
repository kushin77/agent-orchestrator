"""fleet/prune.py — retention for .fleet/, and the safety floor it must never cross (issue #280).

The pruner deletes things, so the tests that matter here are the *negative* ones:
an un-consumed inbox entry, a live run's artifact, a live claim's directive and
every heartbeat/lock/wave file must survive an ``--apply`` that has a genuine
appetite to delete. A dry run must mutate nothing at all. Each of those is
asserted directly, and the liveness rules are proved non-vacuous by showing the
same artifact *is* pruned once the run is dead.

These tests never touch the live ``.fleet/``: the autouse fixture below
redirects every runtime path the module reads to ``tmp_path``, and
``test_the_isolation_cover_points_away_from_the_live_fleet`` proves the cover is
real rather than assumed.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

import cron
import prune

DAY_SECONDS = 86400


@pytest.fixture(autouse=True)
def isolate_prune(tmp_path, monkeypatch):
    """Point every path `prune` reads at a tmp dir for the duration of a test."""
    fleet = tmp_path / ".fleet"
    (fleet / "runs").mkdir(parents=True)
    ledger = tmp_path / ".board" / "claims.jsonl"
    monkeypatch.setattr(prune, "FLEET_DIR", fleet)
    monkeypatch.setattr(prune, "RUNS_DIR", fleet / "runs")
    monkeypatch.setattr(prune, "CLAIMS_LEDGER", ledger)
    monkeypatch.setattr(prune, "LOGS", (fleet / "slog.jsonl", fleet / "runs.jsonl"))
    return fleet, ledger


def live_fleet_dir() -> Path:
    """The real `.fleet/` beside the fleet package — what must stay untouched."""
    return Path(prune.__file__).resolve().parent.parent / ".fleet"


def set_age(path: Path, days: float) -> None:
    """Back-date a file so the age cutoff (not the clock) decides its fate."""
    when = time.time() - days * DAY_SECONDS
    os.utime(path, (when, when))


def write_message(path: Path, *, age_days: float = 30.0, **fields: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"from": "sister", "to": "brain", "type": "result", "ts": "2026-01-01T00:00:00Z"}
    payload.update(fields)
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    set_age(path, age_days)
    return path


def fingerprint(root: Path) -> dict[str, tuple[int, float]]:
    """Every file under `root` -> (size, mtime). Equality is 'nothing changed'."""
    state: dict[str, tuple[int, float]] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            stat = path.stat()
            state[str(path.relative_to(root))] = (stat.st_size, stat.st_mtime)
    return state


def dead_pid() -> int:
    """A PID that has certainly exited (so a marker citing it is not live)."""
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def marker(runs: Path, directive_id: str, pid: int) -> Path:
    path = runs / f"{directive_id}.json"
    path.write_text(json.dumps({"issue": 280, "agent": "subagent-x", "pid": pid}) + "\n", encoding="utf-8")
    return path


def run_prune(fleet: Path, ledger: Path, *extra: str, apply: bool = True) -> int:
    argv = ["run", "--fleet-dir", str(fleet), "--ledger", str(ledger), *extra]
    if apply:
        argv.append("--apply")
    return prune.main(argv)


# --- the isolation cover itself -------------------------------------------------


def test_the_isolation_cover_points_away_from_the_live_fleet(isolate_prune):
    fleet, _ = isolate_prune
    assert prune.FLEET_DIR == fleet, "FLEET_DIR was not redirected"
    assert prune.FLEET_DIR != live_fleet_dir(), "a test would prune the live .fleet"


# --- the safety floor (MUST NEVER delete) ---------------------------------------


def test_an_unconsumed_inbox_entry_is_never_removed(isolate_prune):
    """`inbox` is pending work, not garbage: age does not make it deletable."""
    fleet, ledger = isolate_prune
    pending = write_message(fleet / "inbox" / "pending.json", age_days=999, id="pending")
    write_message(fleet / "outbox" / "old.json", age_days=30, id="old", correlation_id="pending")

    assert run_prune(fleet, ledger) == prune.EXIT_OK

    assert pending.exists(), "an un-consumed directive was deleted"
    assert not (fleet / "outbox" / "old.json").exists(), "the answered reply should have aged out"


def test_an_artifact_of_a_live_run_is_never_removed(isolate_prune):
    fleet, ledger = isolate_prune
    directive = "live-directive"
    marker(fleet / "runs", directive, os.getpid())  # this test process is the live run
    aged_sent = write_message(fleet / "sent" / f"{directive}.json", age_days=90, id=directive)
    aged_done = write_message(fleet / "done" / f"{directive}.json", age_days=90, id=directive)
    aged_reply = write_message(
        fleet / "outbox" / "reply.json", age_days=90, id="reply", correlation_id=directive
    )

    assert run_prune(fleet, ledger) == prune.EXIT_OK

    for kept in (aged_sent, aged_done, aged_reply):
        assert kept.exists(), f"a live run's artifact was deleted: {kept}"


def test_the_same_artifact_is_pruned_once_the_run_is_dead(isolate_prune):
    """Proves the live-run rule is liveness, not 'any marker protects forever'."""
    fleet, ledger = isolate_prune
    directive = "dead-directive"
    marker(fleet / "runs", directive, dead_pid())
    aged_sent = write_message(fleet / "sent" / f"{directive}.json", age_days=90, id=directive)

    assert run_prune(fleet, ledger) == prune.EXIT_OK

    assert not aged_sent.exists(), "a dead run's aged artifact should age out"


def test_an_unreadable_run_marker_protects_its_artifact(isolate_prune):
    """Fail closed: a marker we cannot parse must not expose its artifact."""
    fleet, ledger = isolate_prune
    directive = "corrupt-directive"
    (fleet / "runs" / f"{directive}.json").write_text("{not json", encoding="utf-8")
    aged = write_message(fleet / "sent" / f"{directive}.json", age_days=90, id=directive)

    assert run_prune(fleet, ledger) == prune.EXIT_OK

    assert aged.exists(), "an unreadable marker did not protect its artifact"


def test_a_live_claim_directive_is_never_removed(isolate_prune):
    fleet, ledger = isolate_prune
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(
        json.dumps({"event": "claim", "issue": 145, "agent": "a", "at": "2026-01-01T00:00:00Z",
                    "directive_id": "claimed-directive"}) + "\n",
        encoding="utf-8",
    )
    claimed = write_message(fleet / "sent" / "claimed-directive.json", age_days=90, id="claimed-directive")
    other = write_message(fleet / "sent" / "unrelated.json", age_days=90, id="unrelated")

    assert run_prune(fleet, ledger) == prune.EXIT_OK

    assert claimed.exists(), "a live claim's directive was deleted"
    assert not other.exists(), "the unrelated aged directive should have gone"


def test_a_released_claim_no_longer_protects_its_directive(isolate_prune):
    fleet, ledger = isolate_prune
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(
        "\n".join(
            json.dumps(event)
            for event in (
                {"event": "claim", "issue": 145, "agent": "a", "at": "2026-01-01T00:00:00Z",
                 "directive_id": "claimed-directive"},
                {"event": "release", "issue": 145, "agent": "a", "at": "2026-01-02T00:00:00Z"},
            )
        )
        + "\n",
        encoding="utf-8",
    )
    aged = write_message(fleet / "sent" / "claimed-directive.json", age_days=90, id="claimed-directive")

    assert run_prune(fleet, ledger) == prune.EXIT_OK

    assert not aged.exists(), "a released claim should not protect its directive forever"


def test_a_message_naming_a_held_issue_is_kept(isolate_prune):
    fleet, ledger = isolate_prune
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(
        json.dumps({"event": "claim", "issue": 145, "agent": "a", "at": "2026-01-01T00:00:00Z"}) + "\n",
        encoding="utf-8",
    )
    held = write_message(fleet / "done" / "held.json", age_days=90, task={"issue": 145})
    free = write_message(fleet / "done" / "free.json", age_days=90, task={"issue": 999})

    assert run_prune(fleet, ledger) == prune.EXIT_OK

    assert held.exists(), "a message naming a held issue was deleted"
    assert not free.exists(), "a message naming a free issue should have gone"


def test_heartbeats_locks_waves_and_reported_are_untouched(isolate_prune):
    fleet, ledger = isolate_prune
    protected = [
        write_message(fleet / "waves" / "219.json", age_days=999),
        write_message(fleet / "reported" / "r1.json", age_days=999),
        write_message(fleet / "lifecycle" / "269.json", age_days=999),
    ]
    (fleet / "sister.heartbeat.json").write_text("{}\n", encoding="utf-8")
    (fleet / "sister.lock").write_text("12345\n", encoding="utf-8")
    for path in (fleet / "sister.heartbeat.json", fleet / "sister.lock"):
        set_age(path, 999)
        protected.append(path)

    assert run_prune(fleet, ledger) == prune.EXIT_OK

    for kept in protected:
        assert kept.exists(), f"a protected artifact was deleted: {kept}"


def test_an_unreadable_claim_ledger_fails_closed(isolate_prune):
    """Unknown liveness => prune nothing from the mailboxes; rotation still runs."""
    fleet, ledger = isolate_prune
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text("{not json\n", encoding="utf-8")
    aged = write_message(fleet / "outbox" / "old.json", age_days=90, id="old")
    (fleet / "slog.jsonl").write_text("x" * 400 + "\n", encoding="utf-8")

    assert run_prune(fleet, ledger, "--max-log-bytes", "100") == prune.EXIT_OK

    assert aged.exists(), "an unreadable claim ledger must keep mailbox entries"
    assert (fleet / "slog.jsonl.1").exists(), "the size cap still applies to a live log"


# --- dry-run by default ---------------------------------------------------------


def test_dry_run_mutates_nothing(isolate_prune, capsys):
    fleet, ledger = isolate_prune
    write_message(fleet / "outbox" / "old.json", age_days=30, id="old")
    write_message(fleet / "sent" / "old.json", age_days=30, id="old2")
    (fleet / "slog.jsonl").write_text("y" * 400 + "\n", encoding="utf-8")
    before = fingerprint(fleet)

    assert run_prune(fleet, ledger, "--max-log-bytes", "100", apply=False) == prune.EXIT_OK

    assert fingerprint(fleet) == before, "a dry run changed the tree"
    assert "DRY-RUN" in capsys.readouterr().out


def test_apply_removes_only_the_aged_answered_entries(isolate_prune, capsys):
    fleet, ledger = isolate_prune
    aged_outbox = write_message(fleet / "outbox" / "old.json", age_days=30, id="old")
    aged_sent = write_message(fleet / "sent" / "old.json", age_days=30, id="old")
    aged_done = write_message(fleet / "done" / "old.json", age_days=30, id="old")
    fresh_outbox = write_message(fleet / "outbox" / "new.json", age_days=0, id="new")

    assert run_prune(fleet, ledger) == prune.EXIT_OK

    for gone in (aged_outbox, aged_sent, aged_done):
        assert not gone.exists(), f"an aged answered entry survived: {gone}"
    assert fresh_outbox.exists(), "an entry inside the retention window was deleted"
    assert "APPLIED" in capsys.readouterr().out


def test_the_brain_rungs_mailboxes_are_pruned_too(isolate_prune):
    fleet, ledger = isolate_prune
    aged = write_message(fleet / "brain" / "outbox" / "old.json", age_days=30, id="old")
    assert run_prune(fleet, ledger) == prune.EXIT_OK
    assert not aged.exists(), "the brain rung's mailbox was not pruned"


# --- log rotation ---------------------------------------------------------------


def test_rotation_keeps_a_bounded_number_of_generations(isolate_prune):
    fleet, ledger = isolate_prune
    log = fleet / "slog.jsonl"
    log.write_text("A" * 400 + "\n", encoding="utf-8")
    (fleet / "slog.jsonl.1").write_text("B\n", encoding="utf-8")
    (fleet / "slog.jsonl.2").write_text("C\n", encoding="utf-8")

    assert run_prune(fleet, ledger, "--max-log-bytes", "100", "--keep-generations", "2") == prune.EXIT_OK

    assert log.exists() and log.stat().st_size == 0, "the live log was not recreated empty"
    assert (fleet / "slog.jsonl.1").read_text(encoding="utf-8").startswith("A" * 400)
    assert (fleet / "slog.jsonl.2").read_text(encoding="utf-8") == "B\n"
    assert not (fleet / "slog.jsonl.3").exists(), "generations were not bounded"


def test_a_log_under_the_cap_is_not_rotated(isolate_prune):
    fleet, ledger = isolate_prune
    log = fleet / "runs.jsonl"
    log.write_text("small\n", encoding="utf-8")

    assert run_prune(fleet, ledger, "--max-log-bytes", "1024") == prune.EXIT_OK

    assert log.read_text(encoding="utf-8") == "small\n"
    assert not (fleet / "runs.jsonl.1").exists()


# --- exit codes -----------------------------------------------------------------


def test_a_missing_fleet_dir_is_cannot_assess(isolate_prune, tmp_path, capsys):
    _, ledger = isolate_prune
    missing = tmp_path / "nope"
    rc = prune.main(["run", "--fleet-dir", str(missing), "--ledger", str(ledger)])
    assert rc == prune.EXIT_CANNOT_ASSESS
    assert "CANNOT-ASSESS" in capsys.readouterr().err


def test_status_reports_without_mutating(isolate_prune, capsys):
    fleet, _ = isolate_prune
    write_message(fleet / "outbox" / "old.json", age_days=30, id="old")
    before = fingerprint(fleet)

    assert prune.main(["status", "--fleet-dir", str(fleet)]) == prune.EXIT_OK

    assert fingerprint(fleet) == before
    assert "outbox" in capsys.readouterr().out


def test_status_totals_the_whole_fleet_dir(isolate_prune, capsys):
    """The reported total must include the surfaces no table row enumerates."""
    fleet, _ = isolate_prune
    write_message(fleet / "outbox" / "m.json", age_days=1, id="m")
    (fleet / "sister.log").write_text("x" * 1234, encoding="utf-8")

    assert prune.main(["status", "--fleet-dir", str(fleet)]) == prune.EXIT_OK

    total = sum(path.stat().st_size for path in fleet.rglob("*") if path.is_file())
    assert f"{prune._human(total)} (all of .fleet)" in capsys.readouterr().out


def test_a_negative_generation_count_is_refused(isolate_prune, capsys):
    fleet, ledger = isolate_prune
    rc = prune.main(["run", "--fleet-dir", str(fleet), "--ledger", str(ledger),
                     "--keep-generations", "-1", "--apply"])
    assert rc == prune.EXIT_NOT_OK
    assert "REFUSED" in capsys.readouterr().err


# --- the cron wiring (the retention job must be owned by cron) ------------------


def test_prune_line_is_marked_and_names_the_pruner():
    text = cron.prune_line()
    assert text.endswith(f"# {cron.PRUNE_MARKER}")
    assert "fleet/prune.py run --apply" in text
    assert text.startswith(cron.PRUNE_SCHEDULE), "the prune job should run on its own slower cadence"


def test_the_watchdog_line_is_unchanged_by_the_second_line():
    text = cron.line(2)
    assert text.endswith(f"# {cron.MARKER}")
    assert "fleet/watchdog.py run" in text
    assert cron.PRUNE_MARKER not in text


def test_install_adds_both_lines_and_keeps_foreign_ones():
    foreign = "0 * * * * /usr/bin/true # someone-else"
    merged = cron.install_lines([foreign, cron.line(2)], 2)
    assert foreign in merged, "a foreign crontab line was dropped"
    assert merged[-1] == cron.prune_line()
    assert merged[-2] == cron.line(2)
    assert len([entry for entry in merged if cron._is_ours(entry)]) == 2


def test_install_is_idempotent():
    once = cron.install_lines([], 2)
    twice = cron.install_lines(once, 2)
    assert twice == once


def test_uninstall_removes_only_our_lines():
    foreign = "0 * * * * /usr/bin/true # someone-else"
    kept, ours = cron.remove_lines([foreign, cron.line(2), cron.prune_line()])
    assert kept == [foreign]
    assert len(ours) == 2


def test_a_disabled_line_is_still_recognised_as_ours():
    assert cron._is_ours("# " + cron.prune_line())
    assert cron._is_ours("# " + cron.line(2))
    assert not cron._is_ours("0 * * * * /usr/bin/true # someone-else")
