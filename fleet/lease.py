"""Single-writer lease: one abstraction, two backends, one flag decides.

Issue #713 (fleet-cron D5). Two fleet-cron nodes running active-active must
never both run the same job at the same time. `shared-services/docker/cronrunner`
(peer clone, read-only, `docker/cronrunner/README.md` lines 127-141) already
solved this for its own jobs with KeyDB `SETNX`-with-TTL:

* lock key   ``scheduler:lock:ao-fleet-<job>``
* lock TTL   2x the job timeout (so a crashed holder's lock expires instead of
  wedging the rung forever)
* owner      the container hostname
* loser      logs ``skipped`` and does not run the job

This module gives fleet-cron the same idiom without forcing every laptop
checkout to stand up KeyDB: ``AO_FLEET_LOCK_BACKEND`` picks the backend
(default ``fcntl``, so a bare checkout behaves exactly as before — GR-28,
flag OFF changes nothing). ``keydb`` opts a box into the distributed lease
once one is actually running active-active.

This module does NOT replace the fleet's other single-writer primitives:

* ``governance/dispatch/claims.py`` already uses ``O_CREAT|O_EXCL`` to create
  one file per claim event, which is SETNX-equivalent for a *local* directory
  mailbox — there is no second node reading that directory today, so there is
  nothing to arbitrate across a network. Migrating it to ``Lease`` is a
  separate call: it needs a decision about whether the claims directory itself
  moves to a shared mount (NFS/S3) before a distributed lock in front of it
  would mean anything.
* ``fleet/channel.py``'s mailbox flock (~line 947, the ``_slog`` append lock)
  guards one append-only audit file on local disk for one process at a time;
  it has the same "no second node reads this directory" property as claims.py
  and is left untouched for the same reason.

``fleet/singleton.py`` (one loop per rung) and ``fleet/gatelock.py`` (one gate
per worktree, box-wide permits) are genuinely single-writer arbitration for a
resource that *could* be contended by a second node, so both now call this
module's ``fcntl_try_lock`` / ``fcntl_flock_nb`` functions directly — same
bytes on the wire, same lock file, same leaked-fd contract, just no longer a
second copy-pasted `flock` call site. (`FcntlLease`, the `Lease`-shaped
wrapper around those same functions, is reached through `make_lease()`;
neither module goes through `make_lease()` today, since neither currently
switches backend on `AO_FLEET_LOCK_BACKEND`.) That backend switch is scoped to
fleet-cron job execution (cron.py, out of this PR's owned files) once D5 lands
there.
"""

from __future__ import annotations

import errno
import fcntl
import os
import socket
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_BACKEND = "fcntl"
BACKEND_ENV = "AO_FLEET_LOCK_BACKEND"

KEYDB_HOST_ENV = "KEYDB_HOST"
KEYDB_PORT_ENV = "KEYDB_PORT"
KEYDB_PASSWORD_ENV = "KEYDB_PASSWORD"
DEFAULT_KEYDB_HOST = "127.0.0.1"
DEFAULT_KEYDB_PORT = 6379

LOCK_KEY_PREFIX = "scheduler:lock:ao-fleet-"


class LeaseError(Exception):
    """A lease backend could not be reached or misbehaved. Never a silent grant."""


class Lease(ABC):
    """One single-writer lease.

    ``acquire()`` returns True when this process now holds the lease and
    False on plain contention (another owner holds it) — it never raises for
    the ordinary "someone else has it" case. It may raise ``LeaseError`` when
    the backend itself is unusable (e.g. KeyDB unreachable), because a lease
    that cannot prove exclusivity must refuse to grant one.

    ``release()`` is idempotent: calling it twice, or calling it when
    ``acquire()`` never succeeded, is a no-op.
    """

    @abstractmethod
    def acquire(self) -> bool:
        ...

    @abstractmethod
    def release(self) -> None:
        ...

    @property
    @abstractmethod
    def held(self) -> bool:
        ...


# --- fcntl backend (default; GR-28 flag OFF) ---------------------------------


def fcntl_try_lock(path: Path) -> int | None:
    """Open ``path`` and take a non-blocking exclusive flock.

    Returns the fd on success (with the caller's pid written into the file,
    matching the existing `singleton.py`/`gatelock.py` record format) or
    ``None`` when another holder has it. This is the exact sequence both
    modules used before this refactor; it now lives in one place.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT, 0o644)
    if not fcntl_flock_nb(fd):
        os.close(fd)
        return None
    os.truncate(fd, 0)
    os.write(fd, f"{os.getpid()}\n".encode("utf-8"))
    return fd


def fcntl_flock_nb(fd: int, *, strict: bool = False) -> bool:
    """True when a non-blocking exclusive flock on ``fd`` was taken.

    Ordinary contention (EACCES/EAGAIN/EWOULDBLOCK) always returns False. With
    ``strict=True`` — what `gatelock.py` needs, since "held" and "failed" must
    stay distinguishable per issue #713's acceptance criteria — any other
    OSError raises ``LeaseError`` instead of being folded into a plain False;
    with the default ``strict=False`` every OSError just returns False, which
    is what `singleton.py`'s fire-and-forget callers want.
    """
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        if strict and exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK):
            raise LeaseError(f"flock failed: {exc}") from exc
        return False
    return True


@dataclass
class FcntlLease(Lease):
    """A lease backed by ``flock`` on a local lock file.

    Matches `singleton.py`'s "leak the fd on purpose" contract: the lock must
    live exactly as long as the holding process does, so `release()` is
    provided for backends that need it (KeyDB) but is safe to simply never
    call here — the fd closes when the process exits, which is when the lock
    must go away.
    """

    path: Path
    _fd: int | None = field(default=None, repr=False, compare=False)

    def acquire(self) -> bool:
        fd = fcntl_try_lock(self.path)
        if fd is None:
            return False
        self._fd = fd
        return True

    def release(self) -> None:
        if self._fd is None:
            return
        try:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
        except OSError:
            pass
        self._fd = None

    @property
    def held(self) -> bool:
        return self._fd is not None


# --- KeyDB backend (AO_FLEET_LOCK_BACKEND=keydb) -----------------------------


def lock_key(job: str) -> str:
    return f"{LOCK_KEY_PREFIX}{job}"


def keydb_config() -> tuple[str, int, str | None]:
    """(host, port, password) from env. The password is never returned in a
    form a caller would print — do not log this tuple's third element."""
    host = os.environ.get(KEYDB_HOST_ENV, "").strip() or DEFAULT_KEYDB_HOST
    raw_port = os.environ.get(KEYDB_PORT_ENV, "").strip()
    try:
        port = int(raw_port) if raw_port else DEFAULT_KEYDB_PORT
    except ValueError:
        port = DEFAULT_KEYDB_PORT
    password = os.environ.get(KEYDB_PASSWORD_ENV) or None
    return host, port, password


class _RespClient:
    """A minimal RESP (Redis/KeyDB wire protocol) client over stdlib socket.

    Only the handful of commands a lease needs: AUTH, SET (NX/PX), GET, DEL.
    No third-party `redis` dependency — none is currently a project
    requirement (checked: no `requirements*.txt` references `redis`), and
    GR- style doctrine here is "no new heavy deps" for a lock primitive this
    narrow.
    """

    def __init__(self, host: str, port: int, password: str | None, timeout: float = 5.0) -> None:
        self._sock = socket.create_connection((host, port), timeout=timeout)
        self._buf = b""
        if password:
            self._command("AUTH", password)

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass

    def __enter__(self) -> "_RespClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _send(self, parts: list[bytes]) -> None:
        out = [f"*{len(parts)}\r\n".encode("ascii")]
        for part in parts:
            out.append(f"${len(part)}\r\n".encode("ascii"))
            out.append(part)
            out.append(b"\r\n")
        self._sock.sendall(b"".join(out))

    def _readline(self) -> bytes:
        while b"\r\n" not in self._buf:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise LeaseError("keydb: connection closed while reading a reply")
            self._buf += chunk
        line, self._buf = self._buf.split(b"\r\n", 1)
        return line

    def _read_exact(self, n: int) -> bytes:
        while len(self._buf) < n + 2:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise LeaseError("keydb: connection closed while reading a bulk reply")
            self._buf += chunk
        data, rest = self._buf[:n], self._buf[n + 2:]
        self._buf = rest
        return data

    def _read_reply(self) -> object:
        line = self._readline()
        if not line:
            raise LeaseError("keydb: empty reply")
        kind, payload = line[:1], line[1:]
        if kind == b"+":
            return payload.decode("utf-8", "replace")
        if kind == b"-":
            raise LeaseError(f"keydb: {payload.decode('utf-8', 'replace')}")
        if kind == b":":
            return int(payload)
        if kind == b"$":
            n = int(payload)
            if n == -1:
                return None
            return self._read_exact(n)
        if kind == b"*":
            n = int(payload)
            if n == -1:
                return None
            return [self._read_reply() for _ in range(n)]
        raise LeaseError(f"keydb: unrecognized reply type {kind!r}")

    def _command(self, *parts: str | bytes) -> object:
        encoded = [p if isinstance(p, bytes) else p.encode("utf-8") for p in parts]
        self._send(encoded)
        return self._read_reply()

    def set_nx_px(self, key: str, value: str, ttl_ms: int) -> bool:
        """SET key value NX PX ttl_ms -> True when this call created the key."""
        reply = self._command("SET", key, value, "NX", "PX", str(ttl_ms))
        return reply == "OK"

    def get(self, key: str) -> str | None:
        reply = self._command("GET", key)
        if reply is None:
            return None
        return reply.decode("utf-8", "replace") if isinstance(reply, bytes) else str(reply)

    def delete_if_owner(self, key: str, owner: str) -> bool:
        """Best-effort compare-and-delete: GET then DEL, not a single atomic op.

        A true compare-and-delete needs EVAL (Lua), which not every KeyDB
        deployment enables; GET+DEL leaves a narrow race (another node's SET
        landing between the two) that only matters at the exact TTL boundary,
        which the 2x-timeout TTL is sized to make irrelevant in practice.
        """
        current = self.get(key)
        if current != owner:
            return False
        self._command("DEL", key)
        return True


@dataclass
class KeyDBLease(Lease):
    """A SETNX+TTL lease against KeyDB, matching `docker/cronrunner`'s idiom.

    ``job`` is the bare job id (``lock_key()`` adds the ``scheduler:lock:
    ao-fleet-`` prefix so fleet-cron's keys never collide with cronrunner's
    own ``scheduler:lock:<job-id>`` keys on a shared KeyDB instance).
    ``owner`` defaults to the container/host hostname, matching cronrunner's
    "Owner: container hostname" convention.
    """

    job: str
    ttl_seconds: float
    owner: str = field(default_factory=socket.gethostname)
    host: str | None = None
    port: int | None = None
    password: str | None = field(default=None, repr=False)
    _acquired: bool = field(default=False, repr=False, compare=False)

    def _client(self) -> _RespClient:
        host, port, password = keydb_config()
        return _RespClient(
            self.host or host,
            self.port if self.port is not None else port,
            self.password if self.password is not None else password,
        )

    def acquire(self) -> bool:
        key = lock_key(self.job)
        ttl_ms = max(1, int(self.ttl_seconds * 1000))
        with self._client() as client:
            won = client.set_nx_px(key, self.owner, ttl_ms)
        self._acquired = won
        return won

    def release(self) -> None:
        if not self._acquired:
            return
        key = lock_key(self.job)
        try:
            with self._client() as client:
                client.delete_if_owner(key, self.owner)
        except LeaseError:
            # A dead KeyDB at release time is not this process's problem to
            # crash over: the TTL already bounds how long the lock outlives
            # it. Losing the delete just means the next node waits out the
            # remaining TTL instead of reclaiming immediately.
            pass
        self._acquired = False

    @property
    def held(self) -> bool:
        return self._acquired


# --- backend selection --------------------------------------------------------


def backend_name() -> str:
    """The selected backend, ``AO_FLEET_LOCK_BACKEND`` (default ``fcntl``)."""
    raw = os.environ.get(BACKEND_ENV, "").strip().lower()
    return raw or DEFAULT_BACKEND


def make_lease(
    *,
    job: str,
    path: Path,
    ttl_seconds: float = 300.0,
    backend: str | None = None,
) -> Lease:
    """Build the lease the current env selects.

    ``path`` is used by the ``fcntl`` backend (a local lock file); ``job`` and
    ``ttl_seconds`` are used by the ``keydb`` backend (the SETNX key and its
    TTL, conventionally 2x the job's own timeout). Callers that only ever run
    one backend can ignore the arguments the other one needs.
    """
    selected = (backend or backend_name()).strip().lower()
    if selected == "keydb":
        return KeyDBLease(job=job, ttl_seconds=ttl_seconds)
    if selected != "fcntl":
        raise LeaseError(
            f"{BACKEND_ENV}={selected!r} is not a known backend (fcntl|keydb)"
        )
    return FcntlLease(path=path)


def skipped_log(job: str, *, backend: str, owner: str | None = None, detail: str = "") -> dict:
    """The structured `{"status": "skipped", ...}` record a lease loser logs.

    Shape matches `docker/cronrunner`'s own skip line (job/status/host/detail)
    so fleet-cron's logs parse the same way cronrunner's already do.
    """
    record = {
        "job": job,
        "status": "skipped",
        "host": socket.gethostname(),
        "backend": backend,
        "duration_ms": 0,
        "ts": time.time(),
    }
    if owner:
        record["detail"] = detail or f"lock held by {owner}"
    elif detail:
        record["detail"] = detail
    return record
