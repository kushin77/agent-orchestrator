"""fleet/lease.py: single-writer lease, two backends (issue #713).

Pins the contract `docs/FLEET-LEASE.md` documents:

* the default backend is `fcntl` (`AO_FLEET_LOCK_BACKEND` unset or absent) —
  GR-28, so a bare checkout is unaffected by this module existing;
* `FcntlLease` wins/loses exactly like the pre-existing `singleton.py`/
  `gatelock.py` flock, because it now IS the code those modules call;
* `KeyDBLease` speaks real RESP against a fake KeyDB server: the winner sends
  `SET key value NX PX <ttl_ms>`, the loser's `SET ... NX` is refused, and the
  loser's caller logs `status: "skipped"`;
* `make_lease` raises rather than silently falling back for an unknown
  backend name.
"""

from __future__ import annotations

import socket
import threading
from pathlib import Path

import pytest

import lease


# --- FcntlLease ---------------------------------------------------------------


def test_fcntl_lease_second_acquire_loses(tmp_path: Path) -> None:
    lock_path = tmp_path / "rung.lock"
    winner = lease.FcntlLease(path=lock_path)
    loser = lease.FcntlLease(path=lock_path)

    assert winner.acquire() is True
    assert winner.held is True
    assert loser.acquire() is False
    assert loser.held is False

    winner.release()
    assert winner.held is False
    # once released, a fresh contender can take it
    assert loser.acquire() is True
    loser.release()


def test_fcntl_try_lock_matches_singleton_and_gatelock_contract(tmp_path: Path) -> None:
    """singleton.py leaks the fd; the helper must hand one back to leak."""
    path = tmp_path / "brain.lock"
    fd = lease.fcntl_try_lock(path)
    assert fd is not None
    assert lease.fcntl_try_lock(path) is None  # second caller refused
    import os

    os.close(fd)  # release for cleanliness; production code leaks this on purpose


def test_default_backend_is_fcntl(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(lease.BACKEND_ENV, raising=False)
    assert lease.backend_name() == "fcntl"


def test_make_lease_defaults_to_fcntl_lease(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(lease.BACKEND_ENV, raising=False)
    made = lease.make_lease(job="backup", path=tmp_path / "j.lock")
    assert isinstance(made, lease.FcntlLease)


def test_make_lease_unknown_backend_raises(tmp_path: Path) -> None:
    with pytest.raises(lease.LeaseError):
        lease.make_lease(job="x", path=tmp_path / "x.lock", backend="etcd")


# --- fake KeyDB (RESP) server --------------------------------------------------


class _FakeKeyDB:
    """A minimal in-process RESP server: enough SET/GET/DEL to prove the wire
    contract without a real KeyDB/Redis binary in the test box."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}
        self._lock = threading.Lock()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(5)
        self.host, self.port = self._sock.getsockname()
        self._stop = False
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        self.set_calls: list[tuple[str, str, int]] = []

    def close(self) -> None:
        self._stop = True
        try:
            self._sock.close()
        except OSError:
            pass

    def _serve(self) -> None:
        self._sock.settimeout(0.2)
        while not self._stop:
            try:
                conn, _ = self._sock.accept()
            except (socket.timeout, OSError):
                continue
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn: socket.socket) -> None:
        buf = b""
        conn.settimeout(2.0)
        try:
            while not self._stop:
                try:
                    chunk = conn.recv(4096)
                except (socket.timeout, OSError):
                    break
                if not chunk:
                    break
                buf += chunk
                while True:
                    parsed, buf, ok = self._parse_one(buf)
                    if not ok:
                        break
                    reply = self._dispatch(parsed)
                    conn.sendall(reply)
        finally:
            conn.close()

    def _parse_one(self, buf: bytes):
        if not buf.startswith(b"*"):
            return None, buf, False
        try:
            head, rest = buf.split(b"\r\n", 1)
            n = int(head[1:])
            parts = []
            for _ in range(n):
                len_line, rest = rest.split(b"\r\n", 1)
                length = int(len_line[1:])
                part = rest[:length]
                rest = rest[length + 2:]
                parts.append(part)
            return parts, rest, True
        except (ValueError, IndexError):
            return None, buf, False

    def _dispatch(self, parts: list[bytes]) -> bytes:
        cmd = parts[0].decode("utf-8").upper()
        with self._lock:
            if cmd == "AUTH":
                return b"+OK\r\n"
            if cmd == "SET":
                key = parts[1].decode()
                value = parts[2].decode()
                nx = b"NX" in parts[3:]
                px = None
                for i, p in enumerate(parts):
                    if p.upper() == b"PX" and i + 1 < len(parts):
                        px = int(parts[i + 1])
                self.set_calls.append((key, value, px or -1))
                if nx and key in self._store:
                    return b"$-1\r\n"
                self._store[key] = value
                return b"+OK\r\n"
            if cmd == "GET":
                key = parts[1].decode()
                val = self._store.get(key)
                if val is None:
                    return b"$-1\r\n"
                data = val.encode()
                return f"${len(data)}\r\n".encode() + data + b"\r\n"
            if cmd == "DEL":
                key = parts[1].decode()
                existed = key in self._store
                self._store.pop(key, None)
                return f":{1 if existed else 0}\r\n".encode()
        return b"-ERR unknown command\r\n"


@pytest.fixture()
def fake_keydb():
    server = _FakeKeyDB()
    try:
        yield server
    finally:
        server.close()


def test_keydb_lease_setnx_wins_and_loses(fake_keydb, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(lease.KEYDB_HOST_ENV, fake_keydb.host)
    monkeypatch.setenv(lease.KEYDB_PORT_ENV, str(fake_keydb.port))
    monkeypatch.delenv(lease.KEYDB_PASSWORD_ENV, raising=False)

    winner = lease.KeyDBLease(job="backup", ttl_seconds=10, owner="node-a")
    loser = lease.KeyDBLease(job="backup", ttl_seconds=10, owner="node-b")

    assert winner.acquire() is True
    assert winner.held is True
    assert loser.acquire() is False
    assert loser.held is False

    # the key on the wire matches the cronrunner idiom exactly
    assert lease.lock_key("backup") == "scheduler:lock:ao-fleet-backup"
    keys = {call[0] for call in fake_keydb.set_calls}
    assert keys == {"scheduler:lock:ao-fleet-backup"}


def test_keydb_lease_sets_ttl_as_two_x_job_timeout(fake_keydb, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(lease.KEYDB_HOST_ENV, fake_keydb.host)
    monkeypatch.setenv(lease.KEYDB_PORT_ENV, str(fake_keydb.port))

    job_timeout_seconds = 30
    held = lease.KeyDBLease(
        job="health-check", ttl_seconds=job_timeout_seconds * 2, owner="node-a"
    )
    assert held.acquire() is True
    _, _, px_ms = fake_keydb.set_calls[-1]
    assert px_ms == job_timeout_seconds * 2 * 1000


def test_keydb_lease_release_only_if_owner(fake_keydb, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(lease.KEYDB_HOST_ENV, fake_keydb.host)
    monkeypatch.setenv(lease.KEYDB_PORT_ENV, str(fake_keydb.port))

    a = lease.KeyDBLease(job="x", ttl_seconds=10, owner="node-a")
    assert a.acquire() is True
    a.release()

    b = lease.KeyDBLease(job="x", ttl_seconds=10, owner="node-b")
    assert b.acquire() is True  # released cleanly, so a fresh SETNX wins


def test_skipped_log_shape_matches_cronrunner_idiom() -> None:
    record = lease.skipped_log("ao-fleet-backup", backend="keydb", owner="node-31")
    assert record["job"] == "ao-fleet-backup"
    assert record["status"] == "skipped"
    assert "node-31" in record["detail"]
    assert "host" in record and "ts" in record and "duration_ms" in record
    assert record["duration_ms"] == 0


# --- strict flock: held vs failed must stay distinguishable (#713 acceptance) --


def test_fcntl_flock_nb_default_swallows_any_oserror(tmp_path: Path) -> None:
    import os

    path = tmp_path / "x.lock"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT, 0o644)
    os.close(fd)  # a closed fd makes flock raise EBADF, not ordinary contention
    assert lease.fcntl_flock_nb(fd) is False  # non-strict: never raises


def test_fcntl_flock_nb_strict_raises_on_non_contention_error(tmp_path: Path) -> None:
    import os

    path = tmp_path / "x.lock"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT, 0o644)
    os.close(fd)
    with pytest.raises(lease.LeaseError):
        lease.fcntl_flock_nb(fd, strict=True)


def test_fcntl_flock_nb_strict_still_returns_false_on_ordinary_contention(
    tmp_path: Path,
) -> None:
    path = tmp_path / "held.lock"
    winner_fd = lease.fcntl_try_lock(path)
    assert winner_fd is not None
    import os

    contender_fd = os.open(path, os.O_WRONLY | os.O_CREAT, 0o644)
    try:
        assert lease.fcntl_flock_nb(contender_fd, strict=True) is False
    finally:
        os.close(contender_fd)
        os.close(winner_fd)


def test_loser_logs_skipped_end_to_end(fake_keydb, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(lease.KEYDB_HOST_ENV, fake_keydb.host)
    monkeypatch.setenv(lease.KEYDB_PORT_ENV, str(fake_keydb.port))

    winner = lease.KeyDBLease(job="backup", ttl_seconds=10, owner="node-a")
    loser = lease.KeyDBLease(job="backup", ttl_seconds=10, owner="node-b")
    assert winner.acquire() is True

    if not loser.acquire():
        record = lease.skipped_log("ao-fleet-backup", backend="keydb", owner=winner.owner)
    else:  # pragma: no cover - would mean the lease is broken
        record = None
    assert record is not None
    assert record["status"] == "skipped"
    assert record["detail"] == "lock held by node-a"


def test_keydb_password_never_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(lease.KEYDB_PASSWORD_ENV, "super-secret-token")
    made = lease.KeyDBLease(job="x", ttl_seconds=5, password="super-secret-token")
    assert "super-secret-token" not in repr(made)
