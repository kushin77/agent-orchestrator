"""Gate admission control: one composite gate per worktree, bounded box-wide.

Principal-measured (2026-09-14): **49 concurrent ``make verify`` runs, 43 of them
stacked in two worktrees, ~16 hours of duplicated work.** Nothing bounded them —
every dispatcher could start a gate, and every gate ran to completion.

Two bounds fix that, and neither is advisory:

* **One gate per worktree.** A gate holds an exclusive ``flock`` on a lock file
  keyed by the worktree path, so a second gate in the *same* worktree refuses to
  start and names the process that holds it. Two different worktrees never
  collide: the key is the worktree, not the machine.
* **A box-wide permit.** A gate must also take one of
  ``AO_GATE_MAX_CONCURRENT`` permit slots before it starts. No free slot means
  PARKED — the gate runs no check at all.

The permit store lives OUTSIDE every workspace (``$AO_GATE_LOCK_ROOT``, default
``${XDG_RUNTIME_DIR:-/tmp}/agent-orchestrator-gates``) because each worktree has
its own copy of the repository, and a bound every worktree can edit is not a
bound.

**Why a holder process.** An ``fcntl`` lock belongs to the open file description
that holds it, so a shell entrypoint that took the lock and exited would release
it. ``acquire`` therefore forks a small holder that keeps both descriptors open
and outlives the ``acquire`` call; ``release`` signals it. The holder also
watches the pid of the gate it was minted for (``--owner-pid``, which the gate
passes as its own ``$$``), so a gate that dies — including ``SIGKILL``, where no
trap can run — still releases its permit. That is the crash half of the
contract. The stale half is the other side of the same coin: a holder killed
outright cannot tidy up, so its *record* survives, ``probe`` reports it STALE,
and the next gate reclaims it while naming who left it behind.

**The store is a tmpfs that can be full.** ``/tmp`` on this box is a 16 GB tmpfs
that has silently truncated writes to 0 bytes. ``write_owner`` therefore reads
its own record back and refuses to report a grant it cannot evidence, and a
0-byte lock file is never read as an empty (free) slot: ``flock``, not the bytes,
is the exclusion.

**A leftover must not look like a live claim (#948).** A worktree whose holder
exited leaves a lock file behind — 0 bytes on the normal release path, a stale
record when the holder was killed outright. That leftover wedged #619's
close-out: ``status`` reported the worktree free and ``release`` printed
"already free" while the file survived, and a later close-out read the residue
as a gate that held the worktree. So ``release`` now *reaps* a free lock file
(holding the flock while it unlinks, so a genuinely held lock is never touched),
``status`` names leftover worktree locks instead of omitting them, and a
0-byte free file is reported as a leftover rather than silently as ``FREE``.

Exit codes of ``scripts/gate-lock.sh`` are deliberately outside the gate's own
0/1/2 vocabulary so a parked run can never be read as a pass or as a failure:

=====  =========================================================================
0        ADMITTED — the gate may run
10       REFUSED — another gate already holds this worktree (PARKED, not started)
11       PARKED — every box-wide permit slot is taken (PARKED, not started)
12       CANNOT-ASSESS — the permit store cannot be trusted
=====  =========================================================================
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import socket
import sys
import time
from dataclasses import dataclass
from pathlib import Path

try:
    # Package-relative: how this module is reached when imported as
    # `fleet.gatelock` (e.g. `governance/lifecycle/gate.py`, which only puts
    # the repo ROOT — not `fleet/` — on `sys.path`).
    from fleet import lease
except ImportError:
    # Script-style: `python3 fleet/gatelock.py ...` or `PYTHONPATH=fleet`,
    # where `fleet/` itself is on `sys.path` and `fleet` is not a package.
    import lease

MODULE = "gate-lock"

DEFAULT_MAX_CONCURRENT = 4
DEFAULT_TTL_SECONDS = 900
HOLDER_POLL_SECONDS = 0.05
RENDEZVOUS_TIMEOUT = 10.0
RELEASE_TIMEOUT = 10.0

EXIT_ADMIT = 0
EXIT_REFUSED = 10
EXIT_PARKED = 11
EXIT_STORE_UNUSABLE = 12


class GateLockError(Exception):
    """Base class. Nothing in this module fails silently."""


class StoreUnusable(GateLockError):
    """The permit store cannot be created, written, or read back."""


class Refused(GateLockError):
    """A gate already holds this worktree. Carries the state that proves it."""

    def __init__(self, state: "LockState") -> None:
        self.state = state
        super().__init__(
            f"{MODULE}: REFUSED — another gate already holds worktree "
            f"{state.worktree or state.path} and holds it now ({owner_text(state)}); "
            f"refusing to run a second composite gate in one worktree"
        )


class Parked(GateLockError):
    """Every box-wide permit slot is taken. Carries the holders."""

    def __init__(self, cap: int, holders: list["LockState"]) -> None:
        self.cap = cap
        self.holders = holders
        named = "; ".join(owner_text(state) for state in holders)
        super().__init__(
            f"{MODULE}: PARKED — the box-wide gate cap ({cap}) is reached; "
            f"holders: {named or 'unnamed'}"
        )


# --- the store ---------------------------------------------------------------


def store_root(root: str | os.PathLike[str] | None = None) -> Path:
    """The shared permit store, override with ``AO_GATE_LOCK_ROOT``.

    It must not live in a workspace: every worktree has its own copy of the
    repository, so a bound stored there would be edited per worktree and bound
    nothing.
    """
    if root is not None:
        return Path(root)
    override = os.environ.get("AO_GATE_LOCK_ROOT", "").strip()
    if override:
        return Path(override)
    return Path(os.environ.get("XDG_RUNTIME_DIR") or "/tmp") / "agent-orchestrator-gates"


def max_concurrent() -> int:
    """The box-wide permit bound, override with ``AO_GATE_MAX_CONCURRENT``."""
    raw = os.environ.get("AO_GATE_MAX_CONCURRENT", "").strip()
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_MAX_CONCURRENT
    return value if value > 0 else DEFAULT_MAX_CONCURRENT


def ttl_seconds() -> int:
    """How long a record may be called 'possibly live', ``AO_GATE_LOCK_TTL``."""
    raw = os.environ.get("AO_GATE_LOCK_TTL", "").strip()
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_TTL_SECONDS
    return value if value > 0 else DEFAULT_TTL_SECONDS


def worktree_key(worktree: str | os.PathLike[str]) -> str:
    """A stable key for one worktree, so two of them never share a slot."""
    resolved = str(Path(worktree).expanduser().resolve())
    return hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:16]


def worktree_lock_path(
    worktree: str | os.PathLike[str], root: str | os.PathLike[str] | None = None
) -> Path:
    return store_root(root) / "worktrees" / f"{worktree_key(worktree)}.lock"


def permit_paths(root: str | os.PathLike[str] | None = None) -> list[Path]:
    base = store_root(root) / "permits"
    return [base / f"slot-{index:02d}.lock" for index in range(max_concurrent())]


def _ensure_dir(path: Path) -> None:
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise StoreUnusable(f"cannot create {path}: {exc.strerror or exc}") from exc
    if not os.path.isdir(path):
        raise StoreUnusable(f"{path} is not a directory")


# --- owner records -----------------------------------------------------------


@dataclass(frozen=True)
class Owner:
    """Who holds a lock, exactly as recorded at acquisition time."""

    pid: int
    kind: str
    worktree: str = ""
    slot: int | None = None
    issue: str | None = None
    session: str | None = None
    agent: str | None = None
    mode: str | None = None
    owner_pid: int | None = None
    started_ts: float = 0.0
    host: str = ""

    @property
    def started_at(self) -> str:
        if not self.started_ts:
            return "unknown"
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.started_ts))

    def payload(self) -> bytes:
        record = {
            "pid": self.pid,
            "kind": self.kind,
            "worktree": self.worktree,
            "slot": self.slot,
            "issue": self.issue,
            "session": self.session,
            "agent": self.agent,
            "mode": self.mode,
            "owner_pid": self.owner_pid,
            "started_ts": self.started_ts,
            "started_at": self.started_at,
            "host": self.host,
        }
        return json.dumps(record, sort_keys=True).encode("utf-8")


def pid_alive(pid: int | None) -> bool:
    """True when the pid can be signalled. A zombie still counts as present."""
    if not pid or int(pid) <= 0:
        return False
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _read_bytes(path: Path) -> bytes | None:
    """The record's bytes, ``b""`` when truncated, ``None`` when absent."""
    try:
        return Path(path).read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise StoreUnusable(f"cannot read {path}: {exc.strerror or exc}") from exc


def read_owner(path: str | os.PathLike[str]) -> Owner | None:
    """The named owner, or ``None`` when the record is absent, empty, or unreadable.

    A 0-byte record is ``None`` on purpose: a write the filesystem dropped must
    never be read as "no owner".
    """
    raw = _read_bytes(Path(path))
    if not raw:
        return None
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        pid = int(data["pid"])
    except (KeyError, TypeError, ValueError):
        return None
    slot = data.get("slot")
    try:
        slot = int(slot) if slot is not None else None
    except (TypeError, ValueError):
        slot = None
    try:
        started_ts = float(data.get("started_ts") or 0.0)
    except (TypeError, ValueError):
        started_ts = 0.0
    owner_pid = data.get("owner_pid")
    try:
        owner_pid = int(owner_pid) if owner_pid is not None else None
    except (TypeError, ValueError):
        owner_pid = None
    return Owner(
        pid=pid,
        kind=str(data.get("kind", "")),
        worktree=str(data.get("worktree", "")),
        slot=slot,
        issue=str(data["issue"]) if data.get("issue") is not None else None,
        session=str(data["session"]) if data.get("session") is not None else None,
        agent=str(data["agent"]) if data.get("agent") is not None else None,
        mode=str(data["mode"]) if data.get("mode") is not None else None,
        owner_pid=owner_pid,
        started_ts=started_ts,
        host=str(data.get("host", "")),
    )


def record_problem(
    path: str | os.PathLike[str], *, expect_pid: int | None = None
) -> str | None:
    """Why a record cannot be trusted, or ``None`` when it is readable.

    ``write_owner`` calls this on its own write, so a truncated write is an error
    here rather than a grant nobody can evidence.
    """
    path = Path(path)
    raw = _read_bytes(path)
    if raw is None:
        return f"{path} is missing"
    if not raw:
        return f"{path} is 0 bytes (a truncated write)"
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return f"{path} is not JSON ({exc})"
    if not isinstance(data, dict) or "pid" not in data:
        return f"{path} does not name a pid"
    if expect_pid is not None:
        try:
            if int(data["pid"]) != int(expect_pid):
                return f"{path} names pid {data['pid']}, not {expect_pid}"
        except (TypeError, ValueError):
            return f"{path} names a non-numeric pid"
    return None


def _write_all(fd: int, payload: bytes) -> int:
    """Write every byte or fail: a short write is how a record goes 0 bytes."""
    written = 0
    while written < len(payload):
        written += os.write(fd, payload[written:])
    return written


def write_owner(path: str | os.PathLike[str], owner: Owner) -> None:
    """Write the owner record and verify it, or raise ``StoreUnusable``."""
    path = Path(path)
    _ensure_dir(path.parent)
    payload = owner.payload()
    try:
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    except OSError as exc:
        raise StoreUnusable(f"cannot open {path}: {exc.strerror or exc}") from exc
    try:
        os.ftruncate(fd, 0)
        _write_all(fd, payload)
        os.fsync(fd)
    finally:
        os.close(fd)
    problem = record_problem(path, expect_pid=owner.pid)
    if problem:
        raise StoreUnusable(
            f"refusing to report a grant it cannot evidence: {problem}"
        )


def truncate_record(path: str | os.PathLike[str]) -> None:
    """Clear a record. An absent path is already clear."""
    try:
        os.truncate(Path(path), 0)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise StoreUnusable(f"cannot clear {path}: {exc.strerror or exc}") from exc


def _reap_free_lock(path: Path) -> None:
    """Remove a lock file whose flock is free, holding the flock while unlinking.

    The flock — not the bytes — is the exclusion, so a reaper must take the
    flock before it unlinks: a gate acquiring in the same instant would
    otherwise be unlinked out from under a live holder, which is exactly the
    "delete every file" regression #948 forbids. A held file is left alone.
    """
    try:
        fd = os.open(path, os.O_RDWR)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise StoreUnusable(f"cannot open {path}: {exc.strerror or exc}") from exc
    try:
        if not _try_lock(fd):
            return
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise StoreUnusable(f"cannot remove {path}: {exc.strerror or exc}") from exc
    finally:
        os.close(fd)


def _flock_fresh(fd: int, path: Path) -> bool:
    """True when the fd we flocked is the file that is still at ``path``.

    ``_reap_free_lock`` unlinks a lock it proved free, and that unlink can land
    between this process's ``os.open`` and its ``flock``; the flock would then
    sit on an inode no longer reachable at ``path``, and the next gate opening
    the path again would admit concurrently. Comparing inodes after the flock
    closes that window (issue #948).
    """
    try:
        path_stat = os.stat(path)
    except FileNotFoundError:
        return False
    return os.fstat(fd).st_ino == path_stat.st_ino


# --- flock ------------------------------------------------------------------


def _try_lock(fd: int) -> bool:
    """Non-blocking exclusive flock on `fd`, via `lease.fcntl_flock_nb`.

    Calls the shared helper in `strict=True` mode: this module needs "held"
    (ordinary contention, returns False) kept distinguishable from "the store
    is broken" (any other OSError) — issue #713's acceptance criteria calls
    this out explicitly ("held must be distinguishable from failed"). One
    flock call site now backs `singleton.py`, `gatelock.py`, and
    `fleet/lease.py`'s own `FcntlLease`.
    """
    try:
        return lease.fcntl_flock_nb(fd, strict=True)
    except lease.LeaseError as exc:
        raise StoreUnusable(str(exc)) from exc


def _is_held(path: Path) -> bool:
    """Is the flock held right now? The bytes are not consulted."""
    if not Path(path).exists():
        return False
    try:
        fd = os.open(path, os.O_RDWR)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise StoreUnusable(f"cannot open {path}: {exc.strerror or exc}") from exc
    try:
        return not _try_lock(fd)
    finally:
        os.close(fd)


@dataclass(frozen=True)
class LockState:
    """Everything known about one lock file, and nothing inferred."""

    path: Path
    held: bool
    owner: Owner | None
    record_bytes: int
    worktree: str = ""

    @property
    def free(self) -> bool:
        return not self.held

    @property
    def stale(self) -> bool:
        """A named record with nobody holding the lock.

        Reclaiming this is safe for exactly one reason: the ``flock`` is free, so
        no gate is running under this key. The record is evidence of who was
        here, not a live claim.
        """
        return self.record_bytes > 0 and not self.held

    @property
    def unreadable(self) -> bool:
        return self.record_bytes > 0 and self.owner is None


def probe(path: str | os.PathLike[str], *, held: bool | None = None) -> LockState:
    """Everything known about ``path``. ``held`` is queried when not supplied."""
    path = Path(path)
    raw = _read_bytes(path)
    size = len(raw) if raw else 0
    owner = read_owner(path)
    return LockState(
        path=path,
        held=_is_held(path) if held is None else held,
        owner=owner,
        record_bytes=size,
        worktree=owner.worktree if owner else "",
    )


def owner_text(state: LockState) -> str:
    """Name the owner of a lock, in the terms the refusal needs."""
    if state.owner is None:
        if state.record_bytes:
            return (
                f"an unnamed owner ({state.record_bytes} unreadable record bytes) "
                f"in {state.path}"
            )
        return f"an unnamed owner (0 record bytes) in {state.path}"
    owner = state.owner
    bits = [f"pid {owner.pid}"]
    if owner.worktree:
        bits.append(f"worktree {owner.worktree}")
    if owner.issue:
        bits.append(f"issue #{owner.issue}")
    if owner.mode:
        bits.append(f"mode {owner.mode}")
    if owner.session:
        bits.append(f"session {owner.session}")
    bits.append(f"started {owner.started_at}")
    return ", ".join(bits)


def state_text(state: LockState) -> str:
    if state.held:
        return f"HELD by {owner_text(state)}"
    if state.record_bytes:
        return f"STALE (free, left by {owner_text(state)})"
    return "FREE"


def _free_leftover_text(state: LockState) -> str | None:
    """The status line for a free 0-byte leftover, or ``None`` when not one.

    A queried worktree whose lock file is a 0-byte leftover IS free — ``release``
    reaps it and ``acquire`` admits — so the ``--worktree`` line says FREE and
    then names the file that makes it interesting (#948).
    """
    if not state.held and state.record_bytes == 0 and state.path.exists():
        return "FREE (a 0-byte owner-less lock file is present — release reaps it)"
    return None


def _leftover_text(state: LockState) -> str:
    """Name a lock that needs attention, never with the bare word FREE.

    The ``--worktree`` line reports a queried free worktree as FREE; this listing
    names leftover files for OTHER worktrees, where the word FREE would contradict
    the assertion that a HELD worktree is never reported free (#948).
    """
    if state.held:
        return f"HELD by {owner_text(state)}"
    if state.record_bytes:
        return f"STALE (left by {owner_text(state)})"
    return "LEFTOVER (0-byte owner-less lock file)"


# --- the holder -------------------------------------------------------------


def _record_empty(paths: list[Path]) -> bool:
    for path in paths:
        try:
            if _read_bytes(path):
                return False
        except StoreUnusable:
            return False
    return True


def _hold(fds: list[int], records: list[Path], watch_pid: int | None) -> int:
    """Keep the descriptors open until the gate goes away. Never returns."""
    devnull = os.open(os.devnull, os.O_RDWR)
    for target in (0, 1, 2):
        try:
            os.dup2(devnull, target)
        except OSError:
            pass
    if devnull > 2:
        os.close(devnull)
    try:
        os.setsid()
    except OSError:
        pass
    stop = {"flag": False}

    def _on_signal(_signum, _frame):
        stop["flag"] = True

    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        try:
            signal.signal(sig, _on_signal)
        except (ValueError, OSError):
            pass

    # The parent writes the records immediately after the fork; a holder whose
    # parent died first must not mistake "not written yet" for "released", and
    # must not hold a lock nobody can name either.
    deadline = time.time() + RENDEZVOUS_TIMEOUT
    while _record_empty(records) and not stop["flag"] and time.time() < deadline:
        time.sleep(HOLDER_POLL_SECONDS)

    while not stop["flag"]:
        if watch_pid and not pid_alive(watch_pid):
            break
        if _record_empty(records):
            break
        time.sleep(HOLDER_POLL_SECONDS)

    for path in records:
        try:
            os.truncate(path, 0)
        except OSError:
            pass
    for fd in fds:
        try:
            os.close(fd)
        except OSError:
            pass
    os._exit(0)


def _spawn_holder(fds: list[int], records: list[Path], watch_pid: int | None) -> int:
    sys.stdout.flush()
    sys.stderr.flush()
    pid = os.fork()
    if pid == 0:
        _hold(list(fds), list(records), watch_pid)
    return pid


# --- acquire / release / status ---------------------------------------------


@dataclass(frozen=True)
class Handle:
    """A granted gate slot: the evidence a caller can print and act on."""

    worktree: str
    lock_path: Path
    permit_path: Path
    holder_pid: int
    owner: Owner
    reclaimed: tuple[str, ...] = ()

    def receipt(self) -> str:
        line = (
            f"{MODULE}: ADMITTED worktree={self.worktree} "
            f"holder={self.holder_pid} permit={self.permit_path.stem} "
            f"lock={self.lock_path.name}"
        )
        if self.reclaimed:
            line += " reclaimed-stale=[" + " | ".join(self.reclaimed) + "]"
        return line


def _owner(
    pid: int,
    kind: str,
    worktree: str,
    *,
    slot: int | None = None,
    issue=None,
    session=None,
    agent=None,
    mode=None,
    owner_pid=None,
    started_ts: float = 0.0,
) -> Owner:
    return Owner(
        pid=pid,
        kind=kind,
        worktree=worktree,
        slot=slot,
        issue=issue,
        session=session,
        agent=agent,
        mode=mode,
        owner_pid=owner_pid,
        started_ts=started_ts,
        host=socket.gethostname(),
    )


def acquire(
    worktree: str | os.PathLike[str],
    *,
    root: str | os.PathLike[str] | None = None,
    issue=None,
    session=None,
    agent=None,
    mode=None,
    owner_pid: int | None = None,
) -> Handle:
    """Take this worktree's gate lock and one box-wide permit, or raise.

    Raises ``Refused`` when another gate holds the worktree, ``Parked`` when the
    box cap is reached, and ``StoreUnusable`` when the permit store cannot be
    trusted. Every raise is a refusal to run, never a silent grant.
    """
    resolved = str(Path(worktree).expanduser().resolve())
    base = store_root(root)
    lock = worktree_lock_path(resolved, base)
    _ensure_dir(lock.parent)
    _ensure_dir(base / "permits")

    # Open, flock, and verify the flock is on the inode still at ``lock``. A
    # concurrent release reaping a free lock (#948) can unlink the path between
    # the open and the flock; without the re-check this gate would flock an inode
    # no longer on the filesystem, and the next gate would open a fresh file and
    # admit concurrently. Bounded: an unlink racing three opens in a row is a
    # store that cannot be trusted.
    lock_fd = -1
    for _ in range(3):
        try:
            lock_fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o644)
        except OSError as exc:
            raise StoreUnusable(f"cannot open {lock}: {exc.strerror or exc}") from exc
        if not _try_lock(lock_fd):
            os.close(lock_fd)
            raise Refused(probe(lock, held=True))
        if _flock_fresh(lock_fd, lock):
            break
        os.close(lock_fd)
        lock_fd = -1
    else:
        raise StoreUnusable(
            f"cannot take {lock}: the lock file kept changing while it was opened "
            f"(a release reaping a free lock racing this acquire)"
        )

    # We hold the flock, so a record left in the file cannot belong to a running
    # gate: it is a stale record, and the receipt names who left it behind.
    reclaimed: list[str] = []
    if _read_bytes(lock):
        prior = probe(lock, held=False)
        reclaimed.append(f"worktree lock: {state_text(prior)}")

    permit_path = None
    permit_fd = None
    holders: list[LockState] = []
    for candidate in permit_paths(base):
        try:
            fd = os.open(candidate, os.O_RDWR | os.O_CREAT, 0o644)
        except OSError as exc:
            os.close(lock_fd)
            raise StoreUnusable(f"cannot open {candidate}: {exc.strerror or exc}") from exc
        if _try_lock(fd):
            permit_path, permit_fd = candidate, fd
            break
        os.close(fd)
        holders.append(probe(candidate, held=True))
    if permit_path is None or permit_fd is None:
        truncate_record(lock)
        os.close(lock_fd)
        raise Parked(max_concurrent(), holders)

    if _read_bytes(permit_path):
        reclaimed.append(
            f"permit {permit_path.stem}: {state_text(probe(permit_path, held=False))}"
        )

    started = time.time()
    watch_pid = int(owner_pid) if owner_pid else os.getppid()
    holder_pid = _spawn_holder([lock_fd, permit_fd], [lock, permit_path], watch_pid)
    lock_owner = _owner(
        holder_pid,
        "worktree",
        resolved,
        issue=issue,
        session=session,
        agent=agent,
        mode=mode,
        owner_pid=watch_pid,
        started_ts=started,
    )
    permit_owner = _owner(
        holder_pid,
        "permit",
        resolved,
        slot=None,
        issue=issue,
        session=session,
        agent=agent,
        mode=mode,
        owner_pid=watch_pid,
        started_ts=started,
    )
    try:
        write_owner(lock, lock_owner)
        write_owner(permit_path, permit_owner)
    except StoreUnusable:
        try:
            os.kill(holder_pid, signal.SIGKILL)
        except OSError:
            pass
        truncate_record(lock)
        truncate_record(permit_path)
        raise
    finally:
        os.close(lock_fd)
        os.close(permit_fd)
    if not pid_alive(holder_pid):
        raise StoreUnusable(f"holder pid {holder_pid} did not survive the fork")
    return Handle(
        worktree=resolved,
        lock_path=lock,
        permit_path=permit_path,
        holder_pid=holder_pid,
        owner=lock_owner,
        reclaimed=tuple(reclaimed),
    )


def _kill_holders(pids: list[int], *, timeout: float = RELEASE_TIMEOUT) -> None:
    for pid in pids:
        if not pid_alive(pid):
            continue
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                break
            except PermissionError as exc:
                raise StoreUnusable(f"cannot signal holder pid {pid}: {exc}") from exc
            deadline = time.time() + timeout
            while pid_alive(pid) and time.time() < deadline:
                time.sleep(HOLDER_POLL_SECONDS)


def release(
    worktree: str | os.PathLike[str],
    *,
    root: str | os.PathLike[str] | None = None,
    caller_pid: int | None = None,
    timeout: float = RELEASE_TIMEOUT,
) -> str:
    """Release this worktree's lock and permit. Safe to call twice.

    It refuses to guess: a lock held by a record it cannot name is reported, not
    killed, because signalling a pid nobody can evidence is how a release turns
    into a second runaway. It also refuses to release a lock that belongs to a
    *live* gate other than the caller: an EXIT trap that fires on a refused gate
    would otherwise unlock the gate it was refused by.
    """
    resolved = str(Path(worktree).expanduser().resolve())
    base = store_root(root)
    lock = worktree_lock_path(resolved, base)
    state = probe(lock)
    if not state.held and state.record_bytes == 0:
        _reap_free_lock(lock)
        return f"{MODULE}: RELEASED worktree={resolved} (already free)"
    if state.held and state.owner is None:
        raise StoreUnusable(
            f"{lock} is held by {owner_text(state)}; refusing to signal a pid that "
            f"cannot be read from the record"
        )
    if state.held and state.owner is not None and caller_pid is not None:
        owning_gate = state.owner.owner_pid
        if owning_gate is not None and owning_gate != int(caller_pid) and pid_alive(owning_gate):
            raise StoreUnusable(
                f"{lock} is held by a live gate (pid {owning_gate}, {owner_text(state)}); "
                f"refusing to release another gate's lock"
            )
    named = owner_text(state)
    holder = state.owner.pid if state.owner else None
    if holder is not None:
        try:
            os.kill(holder, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except PermissionError as exc:
            raise StoreUnusable(f"cannot signal holder pid {holder}: {exc}") from exc
        deadline = time.time() + timeout
        while _is_held(lock) and time.time() < deadline:
            time.sleep(HOLDER_POLL_SECONDS)
        if _is_held(lock):
            _kill_holders([holder], timeout=timeout)
    if _is_held(lock):
        raise StoreUnusable(f"holder pid {holder} still holds {lock} after SIGKILL")
    # The flock is free now. Remove the lock file outright, not merely its
    # record: a 0-byte leftover is exactly the artifact that wedged #948's
    # close-out, and a truncated-but-present file is the same wedge one release
    # later. The file and the verdict must agree.
    _reap_free_lock(lock)
    for candidate in permit_paths(base):
        holder_state = probe(candidate)
        if holder_state.record_bytes and not holder_state.held:
            if holder_state.owner is not None and holder_state.owner.worktree == resolved:
                truncate_record(candidate)
    return f"{MODULE}: RELEASED worktree={resolved} holder={holder} (was {named})"


def status(
    worktree: str | os.PathLike[str] | None = None,
    *,
    root: str | os.PathLike[str] | None = None,
) -> str:
    """A report a human can read: the store, the cap, and every live slot."""
    base = store_root(root)
    lines = [
        f"{MODULE}: store={base} cap={max_concurrent()} ttl={ttl_seconds()}s",
        f"  worktrees dir: {base / 'worktrees'}",
    ]
    if worktree is not None:
        resolved = str(Path(worktree).expanduser().resolve())
        state = probe(worktree_lock_path(resolved, base))
        lines.append(f"  worktree {resolved}: {_free_leftover_text(state) or state_text(state)}")
        lines.append(f"  worktree lock: {state.path}")
    live = 0
    for candidate in permit_paths(base):
        state = probe(candidate)
        if not state.held and state.record_bytes == 0:
            continue
        live += 1
        lines.append(f"  permit {candidate.stem}: {state_text(state)}")
    # Leftover worktree locks are named, not omitted (#948). A cleanly-held lock
    # is the normal state and is already named through its permit slot; anything
    # else — a stale record, a 0-byte leftover, or a held lock whose owner cannot
    # be read — is exactly what a wedged close-out must see in one command.
    needing_attention = []
    worktrees_dir = base / "worktrees"
    if worktrees_dir.is_dir():
        for candidate in sorted(worktrees_dir.glob("*.lock")):
            candidate_state = probe(candidate)
            if candidate_state.held and candidate_state.owner is not None:
                continue
            needing_attention.append(candidate_state)
    if needing_attention:
        lines.append(
            f"  worktree locks needing attention ({len(needing_attention)}):"
        )
        for candidate_state in needing_attention:
            lines.append(f"    {candidate_state.path.name}: {_leftover_text(candidate_state)}")
    lines.append(f"  permits in use: {live} of {max_concurrent()}")
    return "\n".join(lines)


def status_code(
    worktree: str | os.PathLike[str] | None = None,
    *,
    root: str | os.PathLike[str] | None = None,
) -> int:
    if worktree is None:
        return EXIT_ADMIT
    resolved = str(Path(worktree).expanduser().resolve())
    state = probe(worktree_lock_path(resolved, store_root(root)))
    return EXIT_REFUSED if state.held else EXIT_ADMIT


# --- CLI --------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=MODULE,
        description=(
            "Gate admission control (issue #724): one composite gate per worktree, "
            "bounded box-wide by a permit store outside every workspace."
        ),
    )
    commands = parser.add_subparsers(dest="command", required=True)

    acquire_cmd = commands.add_parser(
        "acquire", help="take this worktree's gate lock and one box-wide permit"
    )
    acquire_cmd.add_argument("--worktree", required=True)
    acquire_cmd.add_argument("--root", default=None)
    acquire_cmd.add_argument("--issue", default=None)
    acquire_cmd.add_argument("--session", default=None)
    acquire_cmd.add_argument("--agent", default=None)
    acquire_cmd.add_argument("--mode", default=None)
    acquire_cmd.add_argument(
        "--owner-pid",
        type=int,
        default=None,
        help="the gate's own pid ($$); the holder releases when it goes away",
    )

    release_cmd = commands.add_parser("release", help="release this worktree's gate lock")
    release_cmd.add_argument("--worktree", required=True)
    release_cmd.add_argument("--root", default=None)
    release_cmd.add_argument(
        "--owner-pid",
        type=int,
        default=None,
        help="the calling gate's own pid ($$); refuses to release a live gate's lock",
    )

    status_cmd = commands.add_parser("status", help="report the store and every live slot")
    status_cmd.add_argument("--worktree", default=None)
    status_cmd.add_argument("--root", default=None)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "acquire":
            handle = acquire(
                args.worktree,
                root=args.root,
                issue=args.issue,
                session=args.session,
                agent=args.agent,
                mode=args.mode,
                owner_pid=args.owner_pid,
            )
            print(handle.receipt())
            return EXIT_ADMIT
        if args.command == "release":
            print(release(args.worktree, root=args.root, caller_pid=args.owner_pid))
            return EXIT_ADMIT
        print(status(args.worktree, root=args.root))
        return status_code(args.worktree, root=args.root)
    except Refused as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_REFUSED
    except Parked as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_PARKED
    except StoreUnusable as exc:
        print(f"{MODULE}: CANNOT-ASSESS — {exc}", file=sys.stderr)
        return EXIT_STORE_UNUSABLE
    except GateLockError as exc:
        print(f"{MODULE}: CANNOT-ASSESS — {exc}", file=sys.stderr)
        return EXIT_STORE_UNUSABLE


if __name__ == "__main__":
    sys.exit(main())
