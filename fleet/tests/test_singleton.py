"""Two loops = double dispatch. The rung lock is what makes that impossible.

Observed live: two sister loops ran at once, both watched the same pending
directive (the mailbox is a directory — `watch` returns the oldest file without
removing it), both tried to claim the same issue, and every escalation was logged
twice.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import singleton  # noqa: E402


def test_the_first_loop_acquires_and_records_its_pid(tmp_path, monkeypatch):
    monkeypatch.setattr(singleton, "FLEET", tmp_path)
    assert singleton.acquire("sister") is not None
    assert singleton.lock_path("sister").read_text(encoding="utf-8").strip().isdigit()


def test_a_second_loop_is_refused_and_told_which_pid_holds_it(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(singleton, "FLEET", tmp_path)
    assert singleton.guard("sister", "bash fleet/run-fleet.sh")
    monkeypatch.setattr(singleton, "holder_pid", lambda rung: "4242")
    assert singleton.guard("sister", "bash fleet/run-fleet.sh") is False
    out = capsys.readouterr().out
    assert "REFUSED" in out and "4242" in out
    assert "double-dispatch" in out


def test_rungs_do_not_block_each_other(tmp_path, monkeypatch):
    """The brain and the sister are different rungs and must both run."""
    monkeypatch.setattr(singleton, "FLEET", tmp_path)
    assert singleton.guard("brain", "x")
    assert singleton.guard("sister", "x")


def test_the_lock_is_real_and_is_released_with_the_process(tmp_path, monkeypatch):
    """Prove the guard against a separate process, not just this one."""
    monkeypatch.setattr(singleton, "FLEET", tmp_path)
    holder = tmp_path / "hold.py"
    holder.write_text(
        "import fcntl, os, sys, time\n"
        "fd = os.open(sys.argv[1], os.O_WRONLY | os.O_CREAT, 0o644)\n"
        "fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
        "print('held', flush=True)\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )
    child = subprocess.Popen(
        [sys.executable, str(holder), str(singleton.lock_path("sister"))],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "held"
        assert singleton.acquire("sister") is None, "a held rung must refuse a second loop"
    finally:
        child.kill()
        child.wait()
    assert singleton.acquire("sister") is not None, "a stopped loop must not lock the rung out"
