"""Gate admission control: one composite gate per worktree, bounded box-wide.

Measured (2026-09-14): 49 concurrent `make verify` runs, 43 of them stacked in
two worktrees, ~16 hours of duplicated work. Nothing bounded them.

The contract these tests pin, and the reason each half is asserted rather than
assumed:

* a second gate in ONE worktree is REFUSED and names the holder — the bound is
  the worktree, and two different worktrees must NOT collide;
* the box-wide permit bound PARKs the next gate, naming the cap and the holders;
* the lock is released on signal (the trap) and on crash (SIGKILL, where no trap
  can run), and a holder killed outright leaves a STALE record that the next
  gate reclaims while NAMING its owner;
* a 0-byte record — a write `/tmp`, a 16 GB tmpfs on this box, has silently
  dropped — is never read as an empty slot, and release never signals a pid it
  cannot evidence.

Every test points `AO_GATE_LOCK_ROOT` at a scratch store, so the suite can never
touch the box's real permit store or a sibling lane's lock state.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import gatelock  # noqa: E402

ENTRY = REPO / "scripts" / "gate-lock.sh"

TRAP_GATE = """#!/usr/bin/env bash
set -u
entry="$1"
worktree="$2"
bash "$entry" acquire --worktree "$worktree" --owner-pid $$ || exit $?
trap 'bash "$entry" release --worktree "$worktree" --owner-pid $$ >/dev/null; echo TRAP-RELEASED' EXIT
trap 'exit 143' TERM
echo GATE-RUNNING
while :; do sleep 0.1; done
"""


def _wait_for_text(path: Path, marker: str, timeout=15.0) -> bool:
    """Wait until the simulated gate is past `acquire` with its traps installed.

    The lock is taken *inside* acquire, before the traps exist, so signalling on
    "the lock is held" races trap installation and would prove nothing about the
    signal path.
    """
    return _until(
        lambda: path.exists() and marker in path.read_text(encoding="utf-8"), timeout=timeout
    )


def _until(predicate, timeout=15.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def _kill(pid) -> None:
    """Kill a holder — never this process or the shell that started it.

    A record can legitimately name a pid this process wrote itself (the write
    guard's test), and a reaper that signals its own runner turns every green
    suite red for a reason that looks like nothing.
    """
    if not pid or int(pid) in (os.getpid(), os.getppid()):
        return
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass
    try:
        os.waitpid(pid, os.WNOHANG)
    except (ChildProcessError, OSError):
        pass


@pytest.fixture(autouse=True)
def gate_store(tmp_path, monkeypatch):
    """A scratch permit store: no test may reach the box's real one."""
    root = tmp_path / "gate-store"
    monkeypatch.setenv("AO_GATE_LOCK_ROOT", str(root))
    monkeypatch.delenv("AO_GATE_MAX_CONCURRENT", raising=False)
    monkeypatch.delenv("AO_GATE_LOCK_TTL", raising=False)
    return root


@pytest.fixture(autouse=True)
def reap_holders(gate_store):
    """Leave no holder process behind, whatever the test did."""
    yield
    for candidate in sorted(gate_store.rglob("*.lock")):
        owner = gatelock.read_owner(candidate)
        if owner and gatelock.pid_alive(owner.pid):
            _kill(owner.pid)


@pytest.fixture
def lane(tmp_path):
    def _lane(name):
        path = tmp_path / "lanes" / name
        path.mkdir(parents=True, exist_ok=True)
        return path

    return _lane


def _hold_externally(path: Path) -> subprocess.Popen:
    """A process that holds the file's flock and writes no record at all."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    script = path.parent / "hold.py"
    script.write_text(
        "import fcntl, os, sys, time\n"
        "fd = os.open(sys.argv[1], os.O_RDWR)\n"
        "fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
        "print('held', flush=True)\n"
        "time.sleep(120)\n",
        encoding="utf-8",
    )
    proc = subprocess.Popen(
        [sys.executable, str(script), str(path)], stdout=subprocess.PIPE, text=True
    )
    proc.stdout.readline()
    return proc


# --- the grant --------------------------------------------------------------


def test_the_first_gate_in_a_worktree_is_admitted_and_the_grant_is_evidenced(
    gate_store, lane
):
    worktree = lane("ao-a")
    handle = gatelock.acquire(
        worktree,
        root=gate_store,
        issue="724",
        session="ao-session",
        agent="copilot",
        mode="verify",
        owner_pid=os.getpid(),
    )
    assert handle.lock_path == gatelock.worktree_lock_path(worktree, gate_store)
    assert handle.permit_path.parent == gate_store / "permits"
    assert "ADMITTED" in handle.receipt() and f"holder={handle.holder_pid}" in handle.receipt()
    assert gatelock.pid_alive(handle.holder_pid)
    owner = gatelock.read_owner(handle.lock_path)
    assert owner is not None
    assert owner.pid == handle.holder_pid
    assert owner.worktree == str(worktree.resolve())
    assert owner.issue == "724"
    assert owner.mode == "verify"
    assert gatelock.record_problem(handle.lock_path) is None
    assert gatelock.probe(handle.lock_path).held


def test_the_grant_is_a_real_flock_not_a_file_that_looks_held(gate_store, lane):
    worktree = lane("ao-a")
    gatelock.acquire(worktree, root=gate_store, owner_pid=os.getpid())
    lock = gatelock.worktree_lock_path(worktree, gate_store)
    fd = os.open(lock, os.O_RDWR)
    try:
        assert gatelock._try_lock(fd) is False, "the exclusion is not a real flock"
    finally:
        os.close(fd)


def test_flock_fresh_detects_an_open_that_lost_its_path(gate_store, tmp_path):
    """The unlink-racing-open re-check: a flocked fd whose path is gone is stale."""
    path = tmp_path / "x.lock"
    path.write_bytes(b"")
    fd = os.open(path, os.O_RDWR)
    try:
        assert gatelock._flock_fresh(fd, path), "a present, untouched file must read fresh"
        os.unlink(path)
        assert not gatelock._flock_fresh(fd, path), "an unlinked open must read stale"
    finally:
        os.close(fd)


# --- the refusals -----------------------------------------------------------


def test_a_second_gate_in_the_same_worktree_is_refused_and_names_the_holder(
    gate_store, lane
):
    worktree = lane("ao-a")
    first = gatelock.acquire(worktree, root=gate_store, owner_pid=os.getpid())
    with pytest.raises(gatelock.Refused) as raised:
        gatelock.acquire(worktree, root=gate_store, owner_pid=os.getpid())
    text = str(raised.value)
    assert "REFUSED" in text
    assert f"pid {first.holder_pid}" in text
    assert str(worktree.resolve()) in text
    assert first.holder_pid == gatelock.read_owner(first.lock_path).pid, (
        "the refusal must name the holder that is actually there"
    )


def test_a_refused_gate_leaves_the_holder_holding(gate_store, lane):
    worktree = lane("ao-a")
    first = gatelock.acquire(worktree, root=gate_store, owner_pid=os.getpid())
    with pytest.raises(gatelock.Refused):
        gatelock.acquire(worktree, root=gate_store, owner_pid=os.getpid())
    assert gatelock.probe(first.lock_path).held
    assert gatelock.pid_alive(first.holder_pid)


def test_two_worktrees_are_two_keys_and_never_collide(gate_store, lane):
    first_lane, second_lane = lane("ao-a"), lane("ao-b")
    assert gatelock.worktree_key(first_lane) != gatelock.worktree_key(second_lane)
    assert gatelock.worktree_key(first_lane) == gatelock.worktree_key(first_lane / ".")
    first = gatelock.acquire(first_lane, root=gate_store, owner_pid=os.getpid())
    second = gatelock.acquire(second_lane, root=gate_store, owner_pid=os.getpid())
    assert first.lock_path != second.lock_path
    assert first.permit_path != second.permit_path
    assert gatelock.probe(first.lock_path).held
    assert gatelock.probe(second.lock_path).held


def test_the_box_wide_cap_parks_the_next_gate_and_names_the_holders(
    gate_store, lane, monkeypatch
):
    monkeypatch.setenv("AO_GATE_MAX_CONCURRENT", "2")
    first = gatelock.acquire(lane("ao-a"), root=gate_store, owner_pid=os.getpid())
    second = gatelock.acquire(lane("ao-b"), root=gate_store, owner_pid=os.getpid())
    third_lane = lane("ao-c")
    with pytest.raises(gatelock.Parked) as raised:
        gatelock.acquire(third_lane, root=gate_store, owner_pid=os.getpid())
    text = str(raised.value)
    assert "PARKED" in text and "cap (2)" in text
    assert f"pid {first.holder_pid}" in text and f"pid {second.holder_pid}" in text
    parked_lock = gatelock.worktree_lock_path(third_lane, gate_store)
    assert not gatelock.probe(parked_lock).held
    assert gatelock.read_owner(parked_lock) is None, "a parked gate must leave nothing"
    assert len(gatelock.permit_paths(gate_store)) == 2


# --- release ----------------------------------------------------------------


def test_release_frees_the_slot_and_is_idempotent(gate_store, lane):
    worktree = lane("ao-a")
    first = gatelock.acquire(worktree, root=gate_store, owner_pid=os.getpid())
    text = gatelock.release(worktree, root=gate_store)
    assert "RELEASED" in text and f"holder={first.holder_pid}" in text
    assert _until(lambda: not gatelock.probe(first.lock_path).held)
    again = gatelock.acquire(worktree, root=gate_store, owner_pid=os.getpid())
    assert again.holder_pid != first.holder_pid
    released_again = gatelock.release(worktree, root=gate_store)
    assert "RELEASED" in released_again and f"holder={again.holder_pid}" in released_again
    assert "already free" in gatelock.release(worktree, root=gate_store)
    assert "already free" in gatelock.release(worktree, root=gate_store)


def test_release_refuses_to_unlock_a_live_gate_it_does_not_own(gate_store, lane):
    """An EXIT trap on a gate that was REFUSED must not unlock the refuser."""
    worktree = lane("ao-a")
    owning_gate = subprocess.Popen(["sleep", "30"])
    third_party = subprocess.Popen(["sleep", "30"])
    try:
        handle = gatelock.acquire(worktree, root=gate_store, owner_pid=owning_gate.pid)
        with pytest.raises(gatelock.StoreUnusable) as raised:
            gatelock.release(worktree, root=gate_store, caller_pid=third_party.pid)
        assert "another gate's lock" in str(raised.value)
        assert gatelock.probe(handle.lock_path).held, "a third party released a live gate's lock"
        released = gatelock.release(worktree, root=gate_store, caller_pid=owning_gate.pid)
        assert "RELEASED" in released
    finally:
        for process in (owning_gate, third_party):
            process.kill()
            process.wait(timeout=20)


def test_a_holder_killed_outright_leaves_a_stale_record_that_names_the_owner(
    gate_store, lane
):
    worktree = lane("ao-a")
    handle = gatelock.acquire(worktree, root=gate_store, owner_pid=os.getpid())
    _kill(handle.holder_pid)
    assert _until(lambda: not gatelock.probe(handle.lock_path).held), (
        "the flock must be freed by the kernel when the holder dies"
    )
    state = gatelock.probe(handle.lock_path)
    assert state.record_bytes > 0, "the killed holder could not tidy up — the record stays"
    assert state.stale and not state.held
    assert f"pid {handle.holder_pid}" in gatelock.state_text(state)
    assert "STALE" in gatelock.state_text(state)
    reclaimed = gatelock.acquire(worktree, root=gate_store, owner_pid=os.getpid())
    assert reclaimed.reclaimed, "the reclaim was not reported"
    assert f"pid {handle.holder_pid}" in reclaimed.reclaimed[0]
    assert f"pid {handle.holder_pid}" in reclaimed.receipt()


def test_the_holder_releases_when_the_gate_it_was_minted_for_dies(gate_store, lane):
    """The crash half: SIGKILL the gate, and no trap can clean up."""
    worktree = lane("ao-a")
    gate_process = subprocess.Popen(["sleep", "30"])
    handle = gatelock.acquire(worktree, root=gate_store, owner_pid=gate_process.pid)
    assert handle.owner.owner_pid == gate_process.pid
    assert gatelock.probe(handle.lock_path).held
    gate_process.kill()
    gate_process.wait()
    assert _until(lambda: not gatelock.probe(handle.lock_path).held), (
        "the holder must notice its gate is gone and release"
    )


def test_the_trap_releases_the_lock_on_sigterm(gate_store, lane, tmp_path):
    worktree = lane("ao-a")
    script = tmp_path / "trap-gate.sh"
    script.write_text(TRAP_GATE, encoding="utf-8")
    log = tmp_path / "trap-gate.log"
    env = dict(os.environ)
    env["AO_GATE_LOCK_ROOT"] = str(gate_store)
    with open(log, "w", encoding="utf-8") as handle:
        process = subprocess.Popen(
            ["bash", str(script), str(ENTRY), str(worktree)],
            stdout=handle,
            stderr=subprocess.STDOUT,
            env=env,
            start_new_session=True,
        )
    try:
        assert _until(
            lambda: gatelock.probe(gatelock.worktree_lock_path(worktree, gate_store)).held
        )
        assert _wait_for_text(log, "GATE-RUNNING"), "the gate never reached its run loop"
        process.send_signal(signal.SIGTERM)
        process.wait(timeout=20)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=20)
    assert "TRAP-RELEASED" in log.read_text(encoding="utf-8")
    lock = gatelock.worktree_lock_path(worktree, gate_store)
    assert _until(lambda: not gatelock.probe(lock).held)
    assert _until(
        lambda: not any(gatelock.probe(slot).held for slot in gatelock.permit_paths(gate_store))
    )


# --- the 0-byte trap --------------------------------------------------------


def test_a_zero_byte_record_is_never_read_as_a_free_slot(gate_store, lane):
    worktree = lane("ao-a")
    lock = gatelock.worktree_lock_path(worktree, gate_store)
    holder = _hold_externally(lock)
    try:
        state = gatelock.probe(lock)
        assert state.held, "a held flock with no bytes is not a free slot"
        assert state.owner is None
        assert "unnamed owner" in gatelock.owner_text(state)
        assert "0 record bytes" in gatelock.state_text(state)
        with pytest.raises(gatelock.Refused):
            gatelock.acquire(worktree, root=gate_store, owner_pid=os.getpid())
    finally:
        holder.kill()
        holder.wait(timeout=20)
    assert _until(lambda: not gatelock.probe(lock).held)


def test_release_refuses_to_signal_a_pid_it_cannot_evidence(gate_store, lane):
    worktree = lane("ao-a")
    holder = _hold_externally(gatelock.worktree_lock_path(worktree, gate_store))
    try:
        with pytest.raises(gatelock.StoreUnusable) as raised:
            gatelock.release(worktree, root=gate_store)
        assert "refusing to signal" in str(raised.value)
        assert holder.poll() is None, "release killed a process it could not evidence"
    finally:
        holder.kill()
        holder.wait(timeout=20)


def test_a_truncated_write_is_never_reported_as_a_grant(gate_store, lane, monkeypatch):
    worktree = lane("ao-a")
    lock = gatelock.worktree_lock_path(worktree, gate_store)
    empty = gate_store / "empty.lock"
    empty.parent.mkdir(parents=True, exist_ok=True)
    empty.write_bytes(b"")
    assert "0 bytes" in (gatelock.record_problem(empty) or "")
    assert "missing" in (gatelock.record_problem(gate_store / "absent.lock") or "")
    bad = gate_store / "bad.lock"
    bad.write_text("{not json", encoding="utf-8")
    assert "not JSON" in (gatelock.record_problem(bad) or "")
    original = gatelock._write_all
    monkeypatch.setattr(gatelock, "_write_all", lambda fd, payload: 0)
    with pytest.raises(gatelock.StoreUnusable) as raised:
        gatelock.write_owner(bad, gatelock.Owner(pid=os.getpid(), kind="worktree"))
    assert "0 bytes" in str(raised.value)
    monkeypatch.setattr(gatelock, "_write_all", original)
    owner = gatelock.Owner(pid=os.getpid(), kind="worktree", worktree=str(worktree.resolve()))
    gatelock.write_owner(lock, owner)
    assert gatelock.record_problem(lock, expect_pid=os.getpid()) is None, (
        "the guard must pass a real write, or it would refuse every grant"
    )


# --- the store --------------------------------------------------------------


def test_the_store_is_overridable_and_defaults_outside_every_workspace(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("AO_GATE_LOCK_ROOT", raising=False)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    assert gatelock.store_root() == tmp_path / "agent-orchestrator-gates"
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    assert gatelock.store_root() == Path("/tmp/agent-orchestrator-gates")
    assert not str(gatelock.store_root()).startswith(str(REPO)), (
        "a bound stored inside a workspace is edited per worktree"
    )
    monkeypatch.setenv("AO_GATE_LOCK_ROOT", str(tmp_path / "explicit"))
    assert gatelock.store_root() == tmp_path / "explicit"
    assert gatelock.store_root(tmp_path / "argument") == tmp_path / "argument"


# --- the #948 leftover reaping ----------------------------------------------


def test_release_reaps_a_zero_byte_leftover_lock_file(gate_store, lane):
    """A 0-byte owner-less lock file must not survive `release`'s verdict."""
    worktree = lane("ao-948-a")
    lock = gatelock.worktree_lock_path(worktree, gate_store)
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_bytes(b"")
    text = gatelock.release(worktree, root=gate_store)
    assert "already free" in text
    assert not lock.exists(), "a 0-byte leftover survives an 'already free' release"


def test_release_reaps_a_stale_record_lock_file(gate_store, lane):
    """A dead holder's record is removed outright, not truncated to a 0-byte file."""
    worktree = lane("ao-948-b")
    handle = gatelock.acquire(worktree, root=gate_store, owner_pid=os.getpid())
    _kill(handle.holder_pid)
    assert _until(lambda: not gatelock.probe(handle.lock_path).held)
    state = gatelock.probe(handle.lock_path)
    assert state.stale and state.record_bytes > 0
    text = gatelock.release(worktree, root=gate_store)
    assert f"pid {handle.holder_pid}" in text
    assert not handle.lock_path.exists(), "release truncated but left the stale file behind"


def test_status_names_a_zero_byte_leftover_lock_file(gate_store, lane):
    """Status names a 0-byte leftover rather than omitting it (#948)."""
    worktree = lane("ao-948-c")
    lock = gatelock.worktree_lock_path(worktree, gate_store)
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_bytes(b"")
    report = gatelock.status(root=gate_store)
    assert "needing attention" in report and lock.name in report
    one = gatelock.status(worktree, root=gate_store)
    assert "FREE" in one and "0-byte" in one and lock.name in one


def test_status_names_a_stale_record_lock_file(gate_store, lane):
    """Status names a dead holder's record by pid, in the leftover listing."""
    worktree = lane("ao-948-d")
    handle = gatelock.acquire(worktree, root=gate_store, owner_pid=os.getpid())
    _kill(handle.holder_pid)
    assert _until(lambda: not gatelock.probe(handle.lock_path).held)
    report = gatelock.status(root=gate_store)
    assert "needing attention" in report
    assert handle.lock_path.name in report
    assert f"pid {handle.holder_pid}" in report


def test_reap_free_lock_leaves_a_held_lock_alone(gate_store, lane):
    """The reaper takes the flock first, so a live holder is never unlinked."""
    worktree = lane("ao-948-e")
    lock = gatelock.worktree_lock_path(worktree, gate_store)
    holder = _hold_externally(lock)
    try:
        gatelock._reap_free_lock(lock)
        assert lock.exists(), "the reaper unlinked a held lock"
        assert gatelock.probe(lock).held, "the reaper broke a live flock"
    finally:
        holder.kill()
        holder.wait(timeout=20)


def test_max_concurrent_and_ttl_read_their_env_vars_and_reject_nonsense(monkeypatch):
    monkeypatch.delenv("AO_GATE_MAX_CONCURRENT", raising=False)
    monkeypatch.delenv("AO_GATE_LOCK_TTL", raising=False)
    assert gatelock.max_concurrent() == gatelock.DEFAULT_MAX_CONCURRENT
    assert gatelock.ttl_seconds() == gatelock.DEFAULT_TTL_SECONDS
    monkeypatch.setenv("AO_GATE_MAX_CONCURRENT", "7")
    monkeypatch.setenv("AO_GATE_LOCK_TTL", "60")
    assert gatelock.max_concurrent() == 7
    assert gatelock.ttl_seconds() == 60
    assert len(gatelock.permit_paths("/nonexistent")) == 7
    for nonsense in ("0", "-3", "banana", ""):
        monkeypatch.setenv("AO_GATE_MAX_CONCURRENT", nonsense)
        monkeypatch.setenv("AO_GATE_LOCK_TTL", nonsense)
        assert gatelock.max_concurrent() == gatelock.DEFAULT_MAX_CONCURRENT
        assert gatelock.ttl_seconds() == gatelock.DEFAULT_TTL_SECONDS


def test_status_names_the_store_the_cap_and_every_live_slot(gate_store, lane):
    worktree = lane("ao-a")
    empty = gatelock.status(worktree, root=gate_store)
    assert f"store={gate_store}" in empty and "cap=" in empty
    assert "FREE" in empty and "permits in use: 0" in empty
    assert gatelock.status_code(worktree, root=gate_store) == gatelock.EXIT_ADMIT
    handle = gatelock.acquire(worktree, root=gate_store, owner_pid=os.getpid())
    held = gatelock.status(worktree, root=gate_store)
    assert "HELD" in held and f"pid {handle.holder_pid}" in held
    assert "permits in use: 1" in held
    assert gatelock.status_code(worktree, root=gate_store) == gatelock.EXIT_REFUSED


# --- the entrypoint the gate calls -----------------------------------------


def _entry(*args, store, cap=None, owner=None, timeout=60):
    env = dict(os.environ)
    env["AO_GATE_LOCK_ROOT"] = str(store)
    if cap is not None:
        env["AO_GATE_MAX_CONCURRENT"] = str(cap)
    command = ["bash", str(ENTRY), *args]
    if owner is not None:
        command += ["--owner-pid", str(owner)]
    return subprocess.run(command, capture_output=True, text=True, env=env, timeout=timeout)


def test_the_entrypoint_maps_every_refusal_to_its_own_exit_code(gate_store, lane):
    worktree = lane("ao-a")
    other = lane("ao-b")
    admit = _entry("acquire", "--worktree", str(worktree), store=gate_store, owner=os.getpid())
    assert admit.returncode == gatelock.EXIT_ADMIT and "ADMITTED" in admit.stdout
    refused = _entry("acquire", "--worktree", str(worktree), store=gate_store, owner=os.getpid())
    assert refused.returncode == gatelock.EXIT_REFUSED and "REFUSED" in refused.stderr
    parked = _entry(
        "acquire", "--worktree", str(other), store=gate_store, cap=1, owner=os.getpid()
    )
    assert parked.returncode == gatelock.EXIT_PARKED and "PARKED" in parked.stderr
    status = _entry("status", "--worktree", str(worktree), store=gate_store)
    assert status.returncode == gatelock.EXIT_REFUSED and "HELD" in status.stdout
    assert _entry("release", "--worktree", str(worktree), store=gate_store).returncode == 0
    assert _entry("status", "--worktree", str(worktree), store=gate_store).returncode == 0


def test_the_entrypoint_refuses_to_guess_when_it_has_no_command(gate_store):
    bare = _entry(store=gate_store)
    assert bare.returncode == gatelock.EXIT_STORE_UNUSABLE
    assert "usage:" in bare.stderr


def test_the_entrypoint_leaves_bytecode_caches_out_of_the_tree():
    text = ENTRY.read_text(encoding="utf-8")
    assert "PYTHONDONTWRITEBYTECODE=1" in text


# --- the proactive health sweep (RCA-0007, the #948 follow-up) -------------


def test_health_reports_ok_when_the_store_is_clean(gate_store, lane):
    worktree = lane("ao-a")
    handle = gatelock.acquire(worktree, root=gate_store, owner_pid=os.getpid())
    gatelock.release(worktree, root=gate_store, caller_pid=os.getpid())
    report, needs_attention = gatelock.health(root=gate_store)
    assert needs_attention is False
    assert "needing attention" not in report


def test_health_finds_a_zero_byte_owner_less_leftover_without_touching_it(gate_store, lane):
    worktree = lane("ao-a")
    lock = gatelock.worktree_lock_path(worktree, gate_store)
    # #948's exact shape: a lock file survives with nothing holding it and no
    # readable record. Recreate it directly so the SWEEP is what's under test,
    # not the (already-fixed) release path that would normally reap it.
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.touch()
    assert lock.exists() and lock.stat().st_size == 0

    report, needs_attention = gatelock.health(root=gate_store)

    assert needs_attention is True
    assert "needing attention" in report
    assert lock.name in report
    # Alert-only: the sweep must never delete what it found.
    assert lock.exists()


def test_health_does_not_flag_a_live_holder(gate_store, lane):
    worktree = lane("ao-a")
    gatelock.acquire(worktree, root=gate_store, owner_pid=os.getpid())
    report, needs_attention = gatelock.health(root=gate_store)
    assert needs_attention is False
    assert "needing attention" not in report


def test_the_entrypoint_doctor_exits_13_on_a_leftover_and_0_when_clean(gate_store, lane):
    worktree = lane("ao-a")
    gatelock.acquire(worktree, root=gate_store, owner_pid=os.getpid())
    gatelock.release(worktree, root=gate_store, caller_pid=os.getpid())
    clean = _entry("doctor", store=gate_store)
    assert clean.returncode == gatelock.EXIT_ADMIT

    lock = gatelock.worktree_lock_path(worktree, gate_store)
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.touch()
    dirty = _entry("doctor", store=gate_store)
    assert dirty.returncode == gatelock.EXIT_HEALTH_ATTENTION
    assert "needing attention" in dirty.stdout


# --- RCA 2026-09-17 fix #3: a holder reaps its OWN leftover on exit --------
#
# RCA-0015 (governance/lessons/rca/RCA-0015-zero-byte-gate-lock-wedge.md)
# already reviewed and refused a box-wide auto-heal of these leftovers:
# `_reap_free_lock` cannot unlink a lock a live holder flocks (correctness is
# fine), but a sweeper walking every worktree can still race a DIFFERENT
# worktree's in-flight `acquire` between that acquirer's `os.open(O_CREAT)`
# and its `_try_lock` — the file is briefly unflocked and looks exactly like
# a leftover, so the sweep would unlink it, the acquirer's `_flock_fresh`
# re-check would fail, and a legitimately-starting gate would report rc 12
# CANNOT-ASSESS. Scoped to ONE worktree — the sweeper's own — the only
# acquirer that could ever be in that window is the sweeper itself, so the
# race disappears. These tests pin: (1) a holder unlinks its own worktree
# lock on its own exit path (not just truncates it — that truncate-only
# ending is exactly what manufactured the 157 leftovers RCA `2026-09-17
# pr-pileup` measured), (2) permit slot files are left in place (truncated,
# never unlinked — they are a fixed, reused pool with no `_flock_fresh`
# re-check in `acquire`'s permit loop), and (3) a worktree-scoped reap never
# touches a live lock or a DIFFERENT worktree's leftover.


def test_the_holder_unlinks_its_own_lock_file_when_its_gate_dies(gate_store, lane):
    """The crash half, RCA fix #3: no trap can run, but the holder still reaps."""
    worktree = lane("ao-1109-a")
    gate_process = subprocess.Popen(["sleep", "30"])
    handle = gatelock.acquire(worktree, root=gate_store, owner_pid=gate_process.pid)
    gate_process.kill()
    gate_process.wait()
    assert _until(lambda: not handle.lock_path.exists()), (
        "the holder must unlink its own leftover, not merely truncate it"
    )


def test_the_holder_unlinks_its_own_lock_file_on_sigterm(gate_store, lane, tmp_path):
    worktree = lane("ao-1109-b")
    script = tmp_path / "trap-gate.sh"
    script.write_text(TRAP_GATE, encoding="utf-8")
    log = tmp_path / "trap-gate.log"
    env = dict(os.environ)
    env["AO_GATE_LOCK_ROOT"] = str(gate_store)
    lock = gatelock.worktree_lock_path(worktree, gate_store)
    with open(log, "w", encoding="utf-8") as handle:
        process = subprocess.Popen(
            ["bash", str(script), str(ENTRY), str(worktree)],
            stdout=handle,
            stderr=subprocess.STDOUT,
            env=env,
            start_new_session=True,
        )
    try:
        assert _until(lambda: gatelock.probe(lock).held)
        assert _wait_for_text(log, "GATE-RUNNING"), "the gate never reached its run loop"
        process.send_signal(signal.SIGTERM)
        process.wait(timeout=20)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=20)
    assert _until(lambda: not lock.exists()), (
        "a SIGTERM'd holder must unlink its own leftover, not merely truncate it"
    )


def test_the_holder_leaves_permit_slot_files_in_place_after_exit(gate_store, lane):
    """Negative control: permit files are a reused pool — truncate, never unlink."""
    worktree = lane("ao-1109-c")
    handle = gatelock.acquire(worktree, root=gate_store, owner_pid=os.getpid())
    permit_path = handle.permit_path
    text = gatelock.release(worktree, root=gate_store)
    assert "RELEASED" in text
    assert _until(lambda: not gatelock.probe(handle.lock_path).held)
    assert permit_path.exists(), (
        "unlinking a permit slot lets two future gates flock two different "
        "inodes both named the same slot path"
    )


def test_reap_own_worktree_reaps_a_free_leftover_for_that_worktree(gate_store, lane):
    worktree = lane("ao-1109-d")
    lock = gatelock.worktree_lock_path(worktree, gate_store)
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_bytes(b"")
    gatelock.reap_own_worktree(worktree, root=gate_store)
    assert not lock.exists()


def test_reap_own_worktree_leaves_a_live_lock_alone(gate_store, lane):
    """Negative control: a lock flocked by a live process is never unlinked."""
    worktree = lane("ao-1109-e")
    lock = gatelock.worktree_lock_path(worktree, gate_store)
    holder = _hold_externally(lock)
    try:
        gatelock.reap_own_worktree(worktree, root=gate_store)
        assert lock.exists(), "the own-worktree reap unlinked a live lock"
        assert gatelock.probe(lock).held, "the own-worktree reap broke a live flock"
    finally:
        holder.kill()
        holder.wait(timeout=20)


def test_reap_own_worktree_does_not_touch_a_different_worktrees_leftover(gate_store, lane):
    """Scoped, not box-wide: worktree A's reap must never touch worktree B's file."""
    worktree_a = lane("ao-1109-f")
    worktree_b = lane("ao-1109-g")
    lock_a = gatelock.worktree_lock_path(worktree_a, gate_store)
    lock_b = gatelock.worktree_lock_path(worktree_b, gate_store)
    lock_a.parent.mkdir(parents=True, exist_ok=True)
    lock_a.write_bytes(b"")
    lock_b.write_bytes(b"")
    gatelock.reap_own_worktree(worktree_a, root=gate_store)
    assert not lock_a.exists(), "worktree A's own leftover must still be reaped"
    assert lock_b.exists(), (
        "a worktree-scoped reap must never remove a DIFFERENT worktree's "
        "leftover, even though it is provably free — that is the box-wide "
        "auto-heal RCA-0015 refused"
    )
