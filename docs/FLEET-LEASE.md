# fleet/lease.py — single-writer lease for the active-active pair

Issue #713 (fleet-cron D5). When two fleet-cron nodes run active-active, only
one of them may run a given job at a time. `fleet/lease.py` is the primitive
that decides who: one `Lease` interface, two backends, selected by an env
flag that defaults to a no-op for every existing checkout.

## Backend selection

```
AO_FLEET_LOCK_BACKEND=fcntl   # default — local flock, unchanged behavior
AO_FLEET_LOCK_BACKEND=keydb   # distributed SETNX+TTL lease
```

The default is `fcntl`. A bare laptop checkout that never sets the env var
sees no behavior change from this PR — `singleton.py` and `gatelock.py` still
take a local `flock` exactly as before; they now call into `fleet/lease.py`'s
`fcntl_try_lock` / `fcntl_flock_nb` helpers instead of each keeping its own
copy of the same `os.open` + `fcntl.flock` sequence, but the bytes on disk and
the leaked-fd contract are unchanged.

## KeyDB backend

Matches the idiom already running in `shared-services/docker/cronrunner`
(`docker/cronrunner/README.md`, "Distributed Locking (Active-Active HA)"):

| | |
|---|---|
| Lock key | `scheduler:lock:ao-fleet-<job>` |
| Lock TTL | 2x the job's own timeout |
| Owner | the container/host hostname |
| Loser | logs `{"status": "skipped", ...}` and does not run the job |
| Stale lock | expires on TTL; the next node reclaims it |

Configuration, read from the environment and never logged:

```
KEYDB_HOST=192.168.168.31
KEYDB_PORT=6379          # default 6379
KEYDB_PASSWORD=...       # optional
```

`fleet/lease.py` speaks the RESP wire protocol directly over a stdlib
`socket` — no `redis` package dependency. The repo currently has no
`requirements*.txt` entry for `redis`/`hiredis`, and this lock is narrow
enough (`AUTH`, `SET ... NX PX`, `GET`, `DEL`) that adding a client library
for it would be the heavier choice.

```python
from lease import KeyDBLease, skipped_log

held = KeyDBLease(job="platform-backup", ttl_seconds=2 * job_timeout_seconds)
if held.acquire():
    try:
        run_job()
    finally:
        held.release()
else:
    log(skipped_log("platform-backup", backend="keydb", owner=held.owner))
```

`KeyDBLease.release()` is a best-effort GET-then-DEL compare-and-delete, not a
single atomic Lua `EVAL` (not every KeyDB deployment enables scripting). The
narrow race this leaves — another node's `SET` landing between the `GET` and
the `DEL` — only matters within the TTL window, which is sized (2x the job
timeout) specifically so a slightly-early or slightly-late release never
matters in practice.

## Why `claims.py` and `channel.py` are untouched

Two other primitives in this codebase already do single-writer arbitration,
and issue #713 deliberately leaves both alone:

* **`governance/dispatch/claims.py`** creates one file per claim event with
  `O_CREAT|O_EXCL` — the local-filesystem equivalent of `SETNX`. It is
  already exclusion-correct for what it protects today: a directory only one
  node's process reads. Routing it through `Lease` would only mean something
  once the claims directory itself moves to a shared mount (NFS/S3) that a
  second node can also see — that is a storage-topology decision, not a
  locking-primitive one, and it is out of scope here.
* **`fleet/channel.py`**'s mailbox `flock` (~line 947, the `_slog` audit-log
  append) guards one append-only file on local disk against concurrent
  writers *within one process's mailbox directory*. Same property as
  `claims.py`: nothing on a second node reads that file today, so there is
  nothing for a distributed lease to arbitrate yet.

Both are candidates for a future `Lease`-backed migration once the fleet's
shared-mount story lands; wiring them in now would be locking a resource that
is not actually shared.

## What is NOT in this PR

`fleet/cron.py` (fleet-cron's own scheduler loop) is out of this PR's owned
files. This PR ships the `Lease` primitive and proves it against a fake KeyDB
server (`fleet/tests/test_lease.py`); wiring `cron.py`'s job dispatch to call
`make_lease(job=..., ttl_seconds=2 * timeout)` before running a job is the
next, separate change.

## Tests

`fleet/tests/test_lease.py` covers:

* `FcntlLease` win/lose/release against a real lock file (and that
  `fcntl_try_lock` still hands back a leakable fd, matching `singleton.py`'s
  contract);
* `AO_FLEET_LOCK_BACKEND` unset resolves to `fcntl`, and `make_lease` builds
  an `FcntlLease` by default;
* `KeyDBLease` against an in-process fake RESP server: `SET ... NX PX` wins
  for the first caller and is refused for the second, the TTL sent on the
  wire is exactly `2 * job_timeout_seconds * 1000` ms, and the loser's
  `skipped_log()` record carries `status: "skipped"` and names the current
  owner;
* the KeyDB password is never present in a lease's `repr()`.

`fleet/tests/test_gatelock.py` (20 tests) and `fleet/tests/test_singleton.py`
(4 tests) both still pass unchanged against the refactored call path.
