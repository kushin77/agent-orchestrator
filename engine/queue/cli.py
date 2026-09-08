"""engine/queue.cli — offline operator CLI for the issue-#22 job queue.

Demonstrates the full lifecycle against a file-backed ``FileStore`` — no
network, no containers. The "replay API" for dead-lettered tasks is
``replay``; ``reap`` is the orphan reaper; ``stats``/``list`` give the
operator view.

Usage (from the repo root)::

    python3 -m engine.queue.cli enqueue build --tenant acme --priority high \\
        --payload '{"job": "build"}' --key build-42
    python3 -m engine.queue.cli claim --agent worker-1
    python3 -m engine.queue.cli start build --agent worker-1
    python3 -m engine.queue.cli ack build --agent worker-1
    python3 -m engine.queue.cli fail build --agent worker-1 --reason boom
    python3 -m engine.queue.cli reap
    python3 -m engine.queue.cli replay --all
    python3 -m engine.queue.cli stats
    python3 -m engine.queue.cli list --status PENDING

The state file defaults to ``$AO_QUEUE_STORE`` or ``<cwd>/.ao-queue.json``.
Exit codes: 0 ok, 1 operational error, 2 usage.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Optional

from engine.queue.config import load_config
from engine.queue.model import Priority, TaskState, coerce_priority
from engine.queue.queue import JobQueue, QueueError
from engine.queue.store import FileStore

DEFAULT_STORE = os.environ.get("AO_QUEUE_STORE") or os.path.join(
    os.getcwd(), ".ao-queue.json"
)

_OPERATIONAL = 1
_USAGE = 2


def _build_queue(store_path: str) -> JobQueue:
    return JobQueue(
        store=FileStore(store_path),
        config=load_config(),
    )


def _coerce_payload(args: argparse.Namespace):
    if args.payload is not None:
        return json.loads(args.payload)
    pairs = dict(args.data or [])
    return pairs if pairs else None


def _parse_priority(value: str) -> Priority:
    return coerce_priority(value)


def cmd_enqueue(q: JobQueue, args: argparse.Namespace) -> int:
    payload = _coerce_payload(args)
    task = q.enqueue(
        {
            "task_id": args.task_id,
            "tenant": args.tenant,
            "priority": _parse_priority(args.priority),
            "payload": payload,
            "idempotency_key": args.key,
        }
    )
    print(
        f"enqueued {task.task_id} tenant={task.tenant} "
        f"status={task.status.value}"
    )
    return 0


def cmd_claim(q: JobQueue, args: argparse.Namespace) -> int:
    task = q.claim(args.agent, tenant=args.tenant)
    if task is None:
        print("queue empty (nothing claimable)")
        return _OPERATIONAL
    print(
        f"claimed {task.task_id} status={task.status.value} "
        f"lease_until={task.lease_until:.0f}"
    )
    return 0


def cmd_start(q: JobQueue, args: argparse.Namespace) -> int:
    task = q.start(args.task_id, args.agent)
    print(f"started {task.task_id} status={task.status.value}")
    return 0


def cmd_renew(q: JobQueue, args: argparse.Namespace) -> int:
    task = q.renew(args.task_id, args.agent)
    print(f"renewed {task.task_id} lease_until={task.lease_until:.0f}")
    return 0


def cmd_ack(q: JobQueue, args: argparse.Namespace) -> int:
    task = q.ack(args.task_id, args.agent)
    print(f"acked {task.task_id} status={task.status.value}")
    return 0


def cmd_fail(q: JobQueue, args: argparse.Namespace) -> int:
    task = q.fail(args.task_id, args.agent, reason=args.reason)
    print(
        f"failed {task.task_id} status={task.status.value} "
        f"attempts={task.attempts}"
    )
    return 0


def cmd_reap(q: JobQueue, args: argparse.Namespace) -> int:
    reaped = q.reap()
    print(f"reaped {len(reaped)} stale claim(s): {', '.join(reaped) or '-'}")
    return 0


def cmd_replay(q: JobQueue, args: argparse.Namespace) -> int:
    if args.all:
        replayed = q.replay()
    elif args.task_id:
        replayed = q.replay(args.task_id)
    else:  # pragma: no cover - argparse requires one
        replayed = q.replay()
    print(
        f"replayed {len(replayed)} dead-lettered task(s): "
        f"{', '.join(replayed) or '-'}"
    )
    return 0


def cmd_stats(q: JobQueue, args: argparse.Namespace) -> int:
    print(json.dumps(q.stats(), indent=2, sort_keys=True))
    return 0


def cmd_list(q: JobQueue, args: argparse.Namespace) -> int:
    status = TaskState(args.status) if args.status else None
    tasks = q.list_tasks(status=status, tenant=args.tenant)
    if not tasks:
        print("no tasks")
        return 0
    for t in tasks:
        print(
            f"{t.task_id}\t{t.tenant}\t{t.priority.value}\t"
            f"{t.status.value}\tattempts={t.attempts}"
        )
    return 0


def cmd_audit(q: JobQueue, args: argparse.Namespace) -> int:
    for entry in q.audit_log():
        frm = entry.from_state.value if entry.from_state else "-"
        print(
            f"{entry.seq}\t{entry.task_id}\t{frm}->{entry.to_state.value}"
            f"\tagent={entry.agent or '-'}\t{entry.reason or ''}"
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="engine.queue.cli",
        description="offline job-queue operator CLI (issue #22)",
    )
    parser.add_argument(
        "--store",
        default=DEFAULT_STORE,
        help=f"queue state file (default {DEFAULT_STORE})",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("enqueue", help="enqueue a task (PENDING)")
    p.add_argument("task_id")
    p.add_argument("--tenant", default="default")
    p.add_argument("--priority", default="normal", choices=["high", "normal", "low"])
    p.add_argument("--payload", default=None, help="JSON payload")
    p.add_argument("--key", dest="key", default=None, help="idempotency key")
    p.add_argument("--data", action="append", nargs=2, metavar=("K", "V"))
    p.set_defaults(func=cmd_enqueue)

    p = sub.add_parser("claim", help="claim the best PENDING task")
    p.add_argument("--agent", required=True)
    p.add_argument("--tenant", default=None)
    p.set_defaults(func=cmd_claim)

    for name, help_ in (
        ("start", "mark a held task RUNNING"),
        ("renew", "extend a held task's lease"),
        ("ack", "acknowledge success (-> SUCCEEDED)"),
        ("fail", "record failure (retry or dead-letter)"),
    ):
        p = sub.add_parser(name, help=help_)
        p.add_argument("task_id")
        p.add_argument("--agent", required=True)
        if name == "fail":
            p.add_argument("--reason", default=None)
        p.set_defaults(func=globals()[f"cmd_{name}"])

    p = sub.add_parser("reap", help="reap lease-expired claims (orphan reaper)")
    p.set_defaults(func=cmd_reap)

    p = sub.add_parser("replay", help="dead-letter replay (FAILED/DEAD -> PENDING)")
    grp = p.add_mutually_exclusive_group(required=True)
    grp.add_argument("task_id", nargs="?", default=None)
    grp.add_argument("--all", action="store_true")
    p.set_defaults(func=cmd_replay)

    p = sub.add_parser("stats", help="queue statistics")
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("list", help="list tasks")
    p.add_argument("--status", default=None, help="PENDING|CLAIMED|RUNNING|...")
    p.add_argument("--tenant", default=None)
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("audit", help="print the append-only audit ledger")
    p.set_defaults(func=cmd_audit)
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    queue = _build_queue(args.store)
    try:
        return args.func(queue, args)
    except QueueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return _OPERATIONAL
    except (ValueError, json.JSONDecodeError) as exc:
        print(f"usage: {exc}", file=sys.stderr)
        return _USAGE


if __name__ == "__main__":
    sys.exit(main())
