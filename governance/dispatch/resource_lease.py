#!/usr/bin/env python3
"""Live-RESOURCE leases: a claim on a thing that is not a file (issue #1545).

The dispatch claim ledger (`claims.py`) leases FILES. A whole class of
contention is not about files at all: two owners running a terraform apply
against the same state, or two operators pushing a phase of the same Cloudflare
surface, each mutate ONE live resource and nothing in this repo mediates which
of them goes first. The known incident is a two-owner Cloudflare phase
(capital-underwriting vs. shared-services) where both sides pushed a phase of
one surface; a live-resource lease would have refused the second holder.

This module extends the SAME claim machinery to a new subtype rather than
building a parallel claim system:

* the record shape is frozen in ``dispatch.schema.json``
  (``$defs/resourceClaim``) and validated by ``schema.py`` exactly like every
  other dispatch shape — asserted in the test suite and in
  ``scripts/check-resource-lease.sh``, the way ``audit.py`` freezes its own
  trail record;
* the TTL policy is declared ONCE in ``governance/policy/lease.py``
  (``RESOURCE_CLAIM_TTL_SECONDS``, per resource type) and read from there — a
  module that restates a value fails ``make lease-policy``;
* the ledger is append-only, one compact JSON object per line, with the
  read-back prefix proof ``audit.py`` uses, so a concurrent rewrite cannot pass
  as this writer's doing.

TWO THINGS THIS DELIBERATELY IS NOT
    1. **Not file-region mediation.** ``resource_id`` names a resource, not a
       path; the file-lease machinery (``FileClaim``/``file_claims_conflict``)
       is untouched and keeps its own tests, which is how "additive, not a
       breaking change" is proved rather than claimed.
    2. **Not a mutation.** This module holds leases. It never runs terraform,
       never talks to Cloudflare, and has no code path that applies anything
       (GR-5); the entrypoints that DO mutate are the ones that consult it.

FAIL-CLOSED CONTRACT (the repo's honesty tri-state)
    A ledger that cannot be read — absent is empty, but malformed or unreadable
    is not — is :class:`ResourceLeaseUnavailable` (exit 2, NEVER "the resource
    is free"). A refusal names the holder and when its lease expires, so the
    refused caller learns who to wait for.

    Exit codes: 0 OK / 1 REFUSED / 2 CANNOT-ASSESS. ``guard`` propagates the
    wrapped command's own exit code, having printed its own refusal BEFORE the
    command ran (a refused guard never runs the command).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_PKG_DIR = Path(__file__).resolve().parent
if str(_PKG_DIR) not in sys.path:
    sys.path.insert(0, str(_PKG_DIR))

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from governance.policy import lease  # noqa: E402

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX platform
    fcntl = None  # type: ignore[assignment]

#: One JSON object per line, adjacent to — never a replacement for — the issue
#: claim ledger and the arbitration trail.
DEFAULT_LEDGER = Path(".board/resource-claims.jsonl")

#: The ledger's location may be moved by an environment seam so a caller that
#: must not touch the working tree's board (a gate, a CI step) can point it at a
#: scratch path. An explicit ``--ledger`` always wins over this.
LEDGER_ENV = "AO_RESOURCE_CLAIM_LEDGER"

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_CANNOT_ASSESS = 2

KIND_ACQUIRE = "acquire"
KIND_RELEASE = "release"
KINDS = (KIND_ACQUIRE, KIND_RELEASE)

#: The refusal reasons, as a closed vocabulary a caller can test against.
REASON_CLAIMED = "resource-claimed"
REASON_NOT_HELD = "not-held"
REASON_NOT_THE_HOLDER = "not-the-holder"
REASON_MALFORMED_RESOURCE = "malformed-resource-id"

_REQUIRED = ("event", "resource_id", "holder", "at")


class ResourceLeaseUnavailable(Exception):
    """The ledger cannot be read or trusted — CANNOT-ASSESS, never "free"."""


class ResourceClaimRefused(Exception):
    """A live-resource claim was refused, by name."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


def ledger_path(explicit: Path | str | None = None) -> Path:
    """The ledger this call reads and writes: explicit > environment > default."""
    if explicit:
        return Path(explicit)
    return Path(os.environ.get(LEDGER_ENV) or DEFAULT_LEDGER)


def resource_type(resource_id: str) -> str:
    """The declared type of ``resource_id`` (the part before the colon).

    The type is what the TTL is keyed by, so an id without one is refused here
    rather than silently defaulted: a lease nobody can state the type of is a
    lease whose duration cannot be justified.
    """
    text = (resource_id or "").strip()
    if ":" not in text:
        raise ResourceClaimRefused(
            REASON_MALFORMED_RESOURCE,
            f"{resource_id!r} is not a resource id of the form '<type>:<name>' "
            "(e.g. 'tf-state:onprem', 'cloudflare-phase:3')",
        )
    kind, _, name = text.partition(":")
    if not kind.strip() or not name.strip():
        raise ResourceClaimRefused(
            REASON_MALFORMED_RESOURCE,
            f"{resource_id!r} names no {('type' if not kind.strip() else 'resource')} "
            "on one side of the colon",
        )
    return kind.strip()


def ttl_seconds(resource_id: str) -> int:
    """The lease duration for ``resource_id``, from the declared policy.

    Per resource type and read from ``governance/policy/lease.py`` — never a
    literal here and never one global number: an apply on shared state outlives
    a one-shot phase, and a lease that expires mid-apply is a second writer.
    """
    kind = resource_type(resource_id)
    declared = lease.RESOURCE_CLAIM_TTL_SECONDS
    return int(declared.get(kind, lease.RESOURCE_CLAIM_TTL_DEFAULT_SECONDS))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(stamp: str) -> datetime | None:
    try:
        return datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def expires_at(record: dict) -> str:
    """When ``record``'s lease lapses, as a stamp (``""`` when unknowable)."""
    at = _parse(str(record.get("at") or ""))
    ttl = record.get("ttl_seconds")
    if at is None or not isinstance(ttl, int):
        return ""
    return _stamp(at + timedelta(seconds=ttl))


def is_expired(record: dict, now: datetime | None = None) -> bool:
    """True when the lease has lapsed, or when its stamps cannot be trusted.

    An untrustworthy record is treated as EXPIRED rather than as live: a lease
    that blocks every other holder forever because its own timestamps are
    unreadable is the wedge this registry exists to remove.
    """
    at = _parse(str(record.get("at") or ""))
    ttl = record.get("ttl_seconds")
    if at is None or not isinstance(ttl, int):
        return True
    return at + timedelta(seconds=ttl) <= (now or _now())


def _record(
    event: str,
    resource_id: str,
    holder: str,
    moment: datetime,
    *,
    ttl: int | None = None,
    lane: str = "",
    issue: int | None = None,
    reason: str = "",
) -> dict:
    """The single producer of a ledger record (one shape, one place)."""
    if event not in KINDS:
        raise ResourceLeaseUnavailable(f"event {event!r} is not one of {KINDS}")
    record: dict = {
        "event": event,
        "resource_id": resource_id,
        "holder": holder,
        "at": _stamp(moment),
    }
    if event == KIND_ACQUIRE:
        record["ttl_seconds"] = int(ttl if ttl is not None else ttl_seconds(resource_id))
    if lane:
        record["lane"] = lane
    if issue is not None:
        record["issue"] = int(issue)
    if reason:
        record["reason"] = reason
    return record


def render(record: dict) -> str:
    """Canonical serialisation: compact JSON, sorted keys, one object per line."""
    return json.dumps(record, sort_keys=True, separators=(",", ":"))


def _parse_records(text: str, target: Path) -> list[dict]:
    """Parse ledger ``text`` into records, or raise naming the bad line."""
    records: list[dict] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError as exc:
            raise ResourceLeaseUnavailable(
                f"{target}:{number} is not JSON ({exc}) — refusing to judge leases against a ledger "
                "that cannot be read"
            ) from exc
        if not isinstance(record, dict):
            raise ResourceLeaseUnavailable(f"{target}:{number} is not a JSON object")
        missing = [key for key in _REQUIRED if key not in record]
        if missing:
            raise ResourceLeaseUnavailable(
                f"{target}:{number} is missing {missing} — it is not a resource-claim record"
            )
        records.append(record)
    return records


def read(path: Path | str | None = None) -> list[dict]:
    """Every record in the ledger, in file order.

    An ABSENT ledger is empty (nothing has ever been claimed — the common case
    on a fresh checkout). A ledger that exists but cannot be read, or that
    carries a line which is not a record of the frozen shape, is
    :class:`ResourceLeaseUnavailable`: "I could not read the leases" and
    "nothing is leased" are not the same answer, and only one of them is safe to
    act on.
    """
    target = ledger_path(path)
    if not target.exists():
        return []
    try:
        text = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise ResourceLeaseUnavailable(f"the resource-claim ledger {target} is unreadable: {exc}") from exc
    return _parse_records(text, target)


@contextlib.contextmanager
def _locked(path: Path | str | None = None):
    """Hold ONE exclusive lock on the ledger for a whole read-decide-append.

    ``acquire``/``release`` are check-then-act: reading the live leases, then
    deciding, then appending are three separate moments, and without a lock
    held across all three, two callers can both read "free" and both append an
    ``acquire`` — a double lease on the exact single-writer resource this
    registry exists to prevent. Yielding the open file lets the caller read the
    CURRENT bytes and append inside the same critical section a plain
    ``append()`` only locks for the write.
    """
    target = ledger_path(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.touch(exist_ok=True)
    except OSError as exc:
        raise ResourceLeaseUnavailable(f"the resource-claim ledger {target} is not writable: {exc}") from exc
    with target.open("r+", encoding="utf-8") as fh:
        if fcntl is not None:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield fh, target
        finally:
            if fcntl is not None:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def _append_locked(fh, target: Path, record: dict) -> None:
    """Append ``record`` to the already-locked, already-positioned-at-0 ``fh``.

    Proves the same prefix property ``append()`` does: the bytes present
    before this write must be an exact prefix of the bytes after it.
    """
    line = render(record) + "\n"
    before = fh.read()
    fh.seek(0, 2)
    fh.write(line)
    fh.flush()
    fh.seek(0)
    after = fh.read()
    if not after.startswith(before) or after != before + line:
        raise ResourceLeaseUnavailable(
            f"the resource-claim ledger {target} was rewritten concurrently — refusing to trust the append"
        )


def append(record: dict, path: Path | str | None = None) -> None:
    """Append one record, proving the ledger only ever grew.

    The bytes present before the write must be an exact prefix of the bytes
    after it, so a concurrent truncation or rewrite is refused rather than
    mistaken for this writer's doing; concurrent APPENDS are serialised with an
    advisory lock, because two holders recording two different resources at once
    is legitimate. Callers that must decide something (is this resource free?)
    BEFORE writing use :func:`_locked` instead, so the decision and the write
    share one lock — see ``acquire``/``release``.
    """
    with _locked(path) as (fh, target):
        _append_locked(fh, target, record)


def live_leases(records: list[dict], now: datetime | None = None) -> dict[str, dict]:
    """Replay the ledger into ``resource_id -> live acquire record``.

    A ``release`` clears the resource only for the holder that wrote it, so a
    third party cannot clear somebody else's lease by appending one line. An
    expired lease is dropped: the TTL is what keeps a holder that died mid-apply
    from wedging the resource forever.
    """
    moment = now or _now()
    held: dict[str, dict] = {}
    for record in records:
        resource = str(record.get("resource_id") or "")
        if not resource:
            continue
        if record.get("event") == KIND_ACQUIRE:
            held[resource] = record
        elif record.get("event") == KIND_RELEASE:
            current = held.get(resource)
            if current is None or current.get("holder") == record.get("holder"):
                held.pop(resource, None)
    return {resource: record for resource, record in held.items() if not is_expired(record, moment)}


def holder_of(resource_id: str, path: Path | str | None = None, now: datetime | None = None) -> dict | None:
    """The live acquire record for ``resource_id``, or ``None`` when unleased."""
    return live_leases(read(path), now).get(resource_id)


def acquire(
    resource_id: str,
    holder: str,
    *,
    lane: str = "",
    issue: int | None = None,
    ttl: int | None = None,
    path: Path | str | None = None,
    now: datetime | None = None,
) -> dict:
    """Take (or renew) the lease on ``resource_id`` for ``holder``.

    Refused, by name and with the holder's id and expiry quoted, when a
    DIFFERENT holder holds a live lease. The SAME holder re-acquiring renews its
    own lease: a route that runs its own phases, or a retried CI step, is one
    holder doing one job, not two owners contending.
    """
    moment = now or _now()
    with _locked(path) as (fh, target):
        records = _parse_records(fh.read(), target)
        fh.seek(0)
        current = live_leases(records, moment).get(resource_id)
        if current is not None and current.get("holder") != holder:
            raise ResourceClaimRefused(
                REASON_CLAIMED,
                f"{resource_id} is held by {current.get('holder')} since {current.get('at')} "
                f"(expires {expires_at(current) or 'unknown'}); this holder is {holder}",
            )
        expired_holder = ""
        if current is None:
            previous = next(
                (
                    record
                    for record in reversed(records)
                    if record.get("resource_id") == resource_id and record.get("event") == KIND_ACQUIRE
                ),
                None,
            )
            if previous is not None and previous.get("holder") != holder:
                expired_holder = str(previous.get("holder"))
        reason = "renewed by its own holder" if current is not None else ""
        if expired_holder:
            reason = f"taken over from {expired_holder}, whose lease lapsed"
        record = _record(
            KIND_ACQUIRE, resource_id, holder, moment, ttl=ttl, lane=lane, issue=issue, reason=reason
        )
        _append_locked(fh, target, record)
    return record


def release(
    resource_id: str,
    holder: str,
    *,
    path: Path | str | None = None,
    now: datetime | None = None,
    strict: bool = False,
) -> dict | None:
    """Release ``resource_id`` if ``holder`` holds it.

    An unheld resource is a no-op (``None``) — the release path runs from an
    exit trap, and a cleanup that fails the script it is cleaning up after is
    worse than one that reports what it found. ``--strict`` turns that into a
    refusal, which is what a gate uses. A lease held by ANOTHER holder is
    always refused: nobody releases somebody else's lease.
    """
    moment = now or _now()
    with _locked(path) as (fh, target):
        records = _parse_records(fh.read(), target)
        fh.seek(0)
        current = live_leases(records, moment).get(resource_id)
        if current is None:
            if strict:
                raise ResourceClaimRefused(
                    REASON_NOT_HELD, f"{resource_id} is not leased, so {holder} cannot release it"
                )
            return None
        if current.get("holder") != holder:
            raise ResourceClaimRefused(
                REASON_NOT_THE_HOLDER,
                f"{resource_id} is held by {current.get('holder')}, not {holder} — only the holder releases its lease",
            )
        record = _record(KIND_RELEASE, resource_id, holder, moment)
        _append_locked(fh, target, record)
    return record


def guard(
    resource_id: str,
    holder: str,
    argv: list[str],
    *,
    lane: str = "",
    issue: int | None = None,
    path: Path | str | None = None,
    now: datetime | None = None,
) -> int:
    """Run ``argv`` holding the lease on ``resource_id``, or refuse without running it.

    This is the claim-check wrapper a mutating entrypoint calls: acquire, run,
    release. Raises :class:`ResourceClaimRefused` (the CLI maps that to
    :data:`EXIT_REFUSED`) when another holder holds the resource, and the command
    is NEVER started when it does — the property
    ``scripts/check-resource-lease.sh`` observes by wrapping a command that would
    leave a trace. Otherwise returns the wrapped command's own exit code.

    The release is judged at the same instant the acquire was, so a caller that
    injects a clock cannot have its own release land in a different one.
    """
    acquire(resource_id, holder, lane=lane, issue=issue, path=path, now=now)
    try:
        return subprocess.call(argv)
    finally:
        try:
            release(resource_id, holder, path=path, now=now)
        except ResourceClaimRefused:
            # The acquire succeeded, so this can only be a concurrent take-over
            # after our own lease lapsed; the TTL already frees it.
            pass


# ── the self-control (a registry whose refusals cannot fire is a formality) ──
def self_control() -> list[str]:
    """Drive every refusal through the real code, and report what failed.

    Each arm is a claim about behaviour, not about the source text: a refusal
    that cannot fire, or a renewal that silently became a refusal, shows up here
    rather than in production.
    """
    import tempfile

    failures: list[str] = []
    moment = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    with tempfile.TemporaryDirectory() as scratch:
        ledger = Path(scratch) / "resource-claims.jsonl"

        def arm(label: str, ok: bool, detail: str = "") -> None:
            if ok:
                print(f"  OK    {label}")
            else:
                failures.append(label)
                print(f"  FAIL  {label} {detail}")

        acquire("tf-state:onprem", "#1", path=ledger, now=moment)
        try:
            acquire("tf-state:onprem", "#2", path=ledger, now=moment)
            arm("a second holder is refused", False, "=> the second acquire was ACCEPTED")
        except ResourceClaimRefused as refused:
            arm(
                "a second holder is refused",
                refused.reason == REASON_CLAIMED and "#1" in refused.detail,
                f"=> reason={refused.reason!r} detail={refused.detail!r}",
            )

        renewed = acquire("tf-state:onprem", "#1", path=ledger, now=moment)
        arm(
            "the same holder renews instead of being refused",
            renewed.get("reason") == "renewed by its own holder",
            f"=> reason={renewed.get('reason')!r}",
        )

        try:
            release("tf-state:onprem", "#2", path=ledger, now=moment)
            arm("a third party cannot release the lease", False, "=> the release was ACCEPTED")
        except ResourceClaimRefused as refused:
            arm(
                "a third party cannot release the lease",
                refused.reason == REASON_NOT_THE_HOLDER,
                f"=> reason={refused.reason!r}",
            )

        try:
            release("cloudflare-phase:3", "#1", path=ledger, now=moment, strict=True)
            arm("releasing an unleased resource is refused in strict mode", False, "=> it was ACCEPTED")
        except ResourceClaimRefused as refused:
            arm(
                "releasing an unleased resource is refused in strict mode",
                refused.reason == REASON_NOT_HELD,
                f"=> reason={refused.reason!r}",
            )

        arm(
            "an unleased resource passes",
            holder_of("cloudflare-phase:9", path=ledger, now=moment) is None,
            "=> the unleased resource reported a holder",
        )

        ttl = ttl_seconds("tf-state:onprem")
        acquire("tf-state:onprem", "#3", path=ledger, now=moment + timedelta(seconds=ttl + 1))
        arm(
            "a lapsed lease frees the resource for the next holder",
            (holder_of("tf-state:onprem", path=ledger, now=moment + timedelta(seconds=ttl + 1)) or {}).get("holder")
            == "#3",
            "=> the lapsed lease still blocked a new holder",
        )

        arm(
            "the TTL is per resource type, not one global number",
            ttl_seconds("tf-state:onprem") > ttl_seconds("cloudflare-phase:3"),
            f"=> tf-state={ttl_seconds('tf-state:onprem')}s phase={ttl_seconds('cloudflare-phase:3')}s",
        )

        try:
            resource_type("onprem")
            arm("a resource id with no type is refused", False, "=> it was ACCEPTED")
        except ResourceClaimRefused as refused:
            arm(
                "a resource id with no type is refused",
                refused.reason == REASON_MALFORMED_RESOURCE,
                f"=> reason={refused.reason!r}",
            )

        broken = Path(scratch) / "broken.jsonl"
        broken.write_text('{"event":"acquire","resource_id":"tf-state:onprem"}\n', encoding="utf-8")
        try:
            read(broken)
            arm("an unreadable record is CANNOT-ASSESS, never 'free'", False, "=> the bad ledger was ACCEPTED")
        except ResourceLeaseUnavailable:
            arm("an unreadable record is CANNOT-ASSESS, never 'free'", True)

        absent = Path(scratch) / "nothing-here.jsonl"
        arm(
            "an absent ledger is empty, not an error",
            read(absent) == [] and holder_of("tf-state:onprem", path=absent, now=moment) is None,
            "=> an absent ledger did not read as empty",
        )
    return failures


# ── the CLI ─────────────────────────────────────────────────────────────────
def _parse_issue(value: str) -> int | None:
    text = (value or "").strip().lstrip("#")
    if not text:
        return None
    try:
        return int(text)
    except ValueError as exc:
        raise SystemExit(f"resource-lease: --issue must be a number or '#<n>', not {value!r}") from exc


def cmd_acquire(args: argparse.Namespace) -> int:
    record = acquire(
        args.resource,
        args.holder,
        lane=args.lane,
        issue=_parse_issue(args.issue),
        ttl=args.ttl,
        path=args.ledger,
    )
    print(
        "resource-lease: ACQUIRED {resource} for {holder} until {until}{extra}".format(
            resource=args.resource,
            holder=args.holder,
            until=expires_at(record),
            extra=f" ({record['reason']})" if record.get("reason") else "",
        )
    )
    return EXIT_OK


def cmd_release(args: argparse.Namespace) -> int:
    record = release(args.resource, args.holder, path=args.ledger, strict=args.strict)
    if record is None:
        print(f"resource-lease: NOT-HELD {args.resource} (nothing to release for {args.holder})")
        return EXIT_OK
    print(f"resource-lease: RELEASED {args.resource} (holder was {args.holder})")
    return EXIT_OK


def cmd_held(args: argparse.Namespace) -> int:
    holder = holder_of(args.resource, path=args.ledger)
    if holder is None:
        print(f"resource-lease: FREE {args.resource}")
        return EXIT_REFUSED
    print(
        "resource-lease: HELD {resource} by {holder} since {at} (expires {until})".format(
            resource=args.resource,
            holder=holder.get("holder"),
            at=holder.get("at"),
            until=expires_at(holder) or "unknown",
        )
    )
    return EXIT_OK


def cmd_status(args: argparse.Namespace) -> int:
    live = live_leases(read(args.ledger))
    if not live:
        print("resource-lease: no live resource leases")
        return EXIT_OK
    for resource in sorted(live):
        record = live[resource]
        print(
            "{resource:34s} holder={holder:20s} since={at} expires={until}".format(
                resource=resource,
                holder=str(record.get("holder")),
                at=record.get("at"),
                until=expires_at(record) or "unknown",
            )
        )
    return EXIT_OK


def cmd_guard(args: argparse.Namespace) -> int:
    argv = list(args.command)
    if argv and argv[0] == "--":
        argv = argv[1:]
    if not argv:
        print("resource-lease: CANNOT-ASSESS — guard needs a command after '--'", file=sys.stderr)
        return EXIT_CANNOT_ASSESS
    return guard(
        args.resource,
        args.holder,
        argv,
        lane=args.lane,
        issue=_parse_issue(args.issue),
        path=args.ledger,
    )


def cmd_self_control(_args: argparse.Namespace) -> int:
    print("resource-lease self-control:")
    failures = self_control()
    if failures:
        print(f"resource-lease: FAIL — {len(failures)} self-control arm(s) did not hold", file=sys.stderr)
        return EXIT_REFUSED
    print("resource-lease: OK — every refusal fires and every allowance is real")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="resource-lease", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(target: argparse.ArgumentParser) -> None:
        target.add_argument("--ledger", default="", help="the ledger file (default: %s)" % DEFAULT_LEDGER)

    acquire_cmd = sub.add_parser("acquire", help="take (or renew) the lease on a live resource")
    acquire_cmd.add_argument("--resource", required=True, help="e.g. tf-state:onprem, cloudflare-phase:3")
    acquire_cmd.add_argument("--holder", required=True, help="the claiming identity (a lane id, an issue id)")
    acquire_cmd.add_argument("--lane", default="")
    acquire_cmd.add_argument("--issue", default="", help="the issue this holder works, as '<n>' or '#<n>'")
    acquire_cmd.add_argument("--ttl", type=int, default=None, help="override the declared TTL (seconds)")
    add_common(acquire_cmd)
    acquire_cmd.set_defaults(func=cmd_acquire)

    release_cmd = sub.add_parser("release", help="release a lease this holder holds")
    release_cmd.add_argument("--resource", required=True)
    release_cmd.add_argument("--holder", required=True)
    release_cmd.add_argument("--strict", action="store_true", help="refuse when nothing is held")
    add_common(release_cmd)
    release_cmd.set_defaults(func=cmd_release)

    held_cmd = sub.add_parser("held", help="print the holder of a resource (exit 0 = held, 1 = free)")
    held_cmd.add_argument("--resource", required=True)
    add_common(held_cmd)
    held_cmd.set_defaults(func=cmd_held)

    status_cmd = sub.add_parser("status", help="list every live resource lease")
    add_common(status_cmd)
    status_cmd.set_defaults(func=cmd_status)

    guard_cmd = sub.add_parser("guard", help="run a command holding a lease, or refuse without running it")
    guard_cmd.add_argument("--resource", required=True)
    guard_cmd.add_argument("--holder", required=True)
    guard_cmd.add_argument("--lane", default="")
    guard_cmd.add_argument("--issue", default="")
    add_common(guard_cmd)
    guard_cmd.add_argument("command", nargs=argparse.REMAINDER, help="the command to run under the lease")
    guard_cmd.set_defaults(func=cmd_guard)

    control_cmd = sub.add_parser("self-control", help="prove every refusal fires (the anti-formality arm)")
    control_cmd.set_defaults(func=cmd_self_control)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except ResourceClaimRefused as refused:
        print(f"resource-lease: REFUSED {refused.reason}: {refused.detail}", file=sys.stderr)
        return EXIT_REFUSED
    except ResourceLeaseUnavailable as unavailable:
        print(f"resource-lease: CANNOT-ASSESS — {unavailable}", file=sys.stderr)
        return EXIT_CANNOT_ASSESS


if __name__ == "__main__":
    raise SystemExit(main())
