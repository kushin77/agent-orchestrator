"""The fan-out width is declared and backed off, never a literal (issue #1343 review)."""

from __future__ import annotations

import json
from pathlib import Path

from conftest import ROOT  # noqa: F401  (sys.path bootstrap)

from fleet.runner import capacity as cap, cli
from fleet.runner.verify import Ledger, Result


def test_declared_width_is_honoured_and_the_default_is_derived_from_nproc():
    assert cap.declared_capacity({"AO_RUNNER_CAPACITY": "5"}, nproc=32) == 5
    assert cap.declared_capacity({}, nproc=32) == 8, "min(8, nproc // 2)"
    assert cap.declared_capacity({}, nproc=6) == 3
    assert cap.declared_capacity({}, nproc=1) == 1, "never below 1"
    assert cap.declared_capacity({"AO_RUNNER_CAPACITY": "banana"}, nproc=6) == 3, "an invalid value falls back, never 0"
    assert cap.declared_capacity({"AO_RUNNER_CAPACITY": "0"}, nproc=6) == 3


def test_no_backoff_when_the_host_is_idle():
    width = cap.effective_capacity(6, load1=2.0, mem_available_gb=32.0, nproc=8)
    assert (width.declared, width.effective, width.reason) == (6, 6, "")


def test_load_backoff_reduces_the_width_and_names_itself():
    width = cap.effective_capacity(6, load1=9.5, mem_available_gb=32.0, nproc=8)
    assert width.effective < 6 and width.effective >= 1
    assert width.reason.startswith("capacity-backoff:load:9.5/8")


def test_memory_backoff_reduces_the_width_and_names_itself():
    width = cap.effective_capacity(6, load1=1.0, mem_available_gb=3.2, nproc=8, mem_floor_gb=8.0)
    assert width.effective == 3
    assert width.reason == "capacity-backoff:memory:3.2gb<8gb"


def test_backoff_never_goes_below_one():
    width = cap.effective_capacity(1, load1=100.0, mem_available_gb=0.1, nproc=2)
    assert width.effective == 1
    assert "load:" in width.reason and "memory:" in width.reason
    assert cap.effective_capacity(0, load1=None, mem_available_gb=None, nproc=2).effective == 1


def test_mem_floor_is_declared_with_a_default():
    assert cap.declared_mem_floor_gb({}) == 8.0
    assert cap.declared_mem_floor_gb({"AO_RUNNER_MEM_FLOOR_GB": "12"}) == 12.0
    assert cap.declared_mem_floor_gb({"AO_RUNNER_MEM_FLOOR_GB": "x"}) == 8.0


def test_a_cycle_ledgers_the_effective_width_and_status_shows_it(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "ROOT", tmp_path)

    def gh(argv, **kw):
        if argv[:2] == ["pr", "list"]:
            return Result(0, "[]")
        return Result(0, "{}")

    t = cli.Transports(git=lambda argv, **kw: Result(0, "c" * 40 + "\n"), gh=gh, gcloud=None, sh=lambda argv, **kw: Result(0), env={"AO_RUNNER_HOST_ROLE": "primary", "AO_RUNNER_CAPACITY": "6"})
    base = tmp_path / "runner"
    rc = cli.cycle(t, base=base, apply=False, host_probe=lambda: (20.0, 2.0, 8))
    assert rc == 0
    rows = Ledger(base / "ledger.jsonl").rows()
    caprow = [r for r in rows if r["event"] == "capacity"][0]
    assert caprow["declared"] == 6 and caprow["effective"] == 1
    assert caprow["reason"].startswith("capacity-backoff:load:20.0/8;memory:2.0gb<8gb")
    monkeypatch.setenv("AO_FLEET_DIR", str(tmp_path))
    assert cli.main(["status"]) == 0
    assert "capacity: declared 6 effective 1 (capacity-backoff:load:20.0/8;memory:2.0gb<8gb)" in capsys.readouterr().out
    # --capacity overrides the declared width, and is still backed off.
    rc = cli.cycle(t, base=tmp_path / "r2", apply=False, capacity=4, host_probe=lambda: (0.5, 64.0, 8))
    caprow = [r for r in Ledger(tmp_path / "r2" / "ledger.jsonl").rows() if r["event"] == "capacity"][0]
    assert (caprow["declared"], caprow["effective"], caprow["reason"]) == (4, 4, "none")


def test_probe_host_reads_meminfo_and_tolerates_a_missing_proc(tmp_path: Path):
    (tmp_path / "meminfo").write_text("MemTotal:  64 kB\nMemAvailable:  2097152 kB\n", encoding="utf-8")
    load1, mem, nproc = cap.probe_host(tmp_path)
    assert mem == 2.0 and nproc >= 1
    assert cap.probe_host(tmp_path / "missing")[1] is None
    assert json.dumps({"load1": load1}) is not None
