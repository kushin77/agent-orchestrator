#!/usr/bin/env python3
"""Steering channel CLI — the transport between the brain and the fleet.

Operating model (M26, issue #160): the **brain** (advisor session) issues
directives; the **sister** session (a dumb terminal on DeepSeek v4.1 Flash, no
thinking) executes them by spawning epic-focused subagents; subagents report
results back through the sister. This CLI is the file-mailbox transport for
that loop — localhost mechanics (GR-21), no network, no daemons.

Mailbox layout (runtime state, gitignored):

    .fleet/inbox/    messages for the sister to drain (written by brain send)
    .fleet/sent/     the brain's own copy of everything it sent
    .fleet/outbox/   acks and results written back for the brain
    .fleet/done/     directives answered and consumed

Messages are validated against `fleet/schema/message.schema.json` semantics
before they move. The topology, the directive vocabulary and the trust rules the
validator enforces are the normative contract in `fleet/CONTRACT.md`, and the
transport itself is decided by ADR-0011 (docs/decision-records/) — this module is
the machine that runs that contract. Exit codes are the repo tri-state: 0 OK /
1 NOT-OK / 2 CANNOT-ASSESS.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = ROOT / "fleet" / "schema" / "message.schema.json"
INBOX = ROOT / ".fleet" / "inbox"
SENT = ROOT / ".fleet" / "sent"
OUTBOX = ROOT / ".fleet" / "outbox"
DONE = ROOT / ".fleet" / "done"
SLOG = ROOT / ".fleet" / "slog.jsonl"

MESSAGE_TYPES = ("directive", "ack", "result", "halt", "escalate")
SEVERITIES = ("info", "warn", "critical")
MODEL_TIERS = ("pro", "flash")
THINKING_LEVELS = ("none", "low", "high")
_ROLE_RE = re.compile(r"^(brain|sister|subagent(-[a-z0-9]+)?)$")

EXIT_OK = 0
EXIT_NOT_OK = 1
EXIT_CANNOT_ASSESS = 2


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_ts(value: str) -> bool:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        datetime.fromisoformat(text)
        return True
    except ValueError:
        return False


def validate(message: dict) -> list[str]:
    """Return every contract violation; empty list means the message is valid."""
    problems: list[str] = []
    if not isinstance(message, dict):
        return ["message must be a JSON object"]
    message_type = message.get("type")
    if message_type not in MESSAGE_TYPES:
        problems.append(f"type must be one of {', '.join(MESSAGE_TYPES)}")
    for field in ("from", "to"):
        value = message.get(field)
        if not isinstance(value, str) or not _ROLE_RE.match(value):
            problems.append(f"{field} must match brain|sister|subagent(-name)?")
    if "id" in message and (not isinstance(message["id"], str) or not message["id"].strip()):
        problems.append("id must be a non-empty string")
    if "ts" in message and (not isinstance(message["ts"], str) or not _parse_ts(message["ts"])):
        problems.append("ts must be an ISO-8601 timestamp")
    if "correlation_id" in message and not isinstance(message["correlation_id"], str):
        problems.append("correlation_id must be a string")
    if "nonce" in message and (not isinstance(message["nonce"], str) or not message["nonce"].strip()):
        problems.append("nonce must be a non-empty string (the anti-replay token)")
    if message_type == "directive" and message.get("to") != "sister":
        problems.append("directives may only be addressed to the sister")
    if message.get("from") == "sister" and message_type == "directive":
        problems.append("the sister is a dumb terminal: it cannot issue directives")
    if message_type in ("ack", "result") and not message.get("correlation_id"):
        problems.append(f"{message_type} must carry correlation_id (the directive it answers)")
    if message_type in ("ack", "result") and message.get("from") == "brain":
        problems.append("the brain does not ack or report on its own directives")
    if message_type == "halt" and message.get("from") != "brain":
        problems.append("only the brain may issue a halt")
    if message_type == "escalate":
        if not message.get("correlation_id"):
            problems.append("escalate must carry correlation_id (the directive that hit trouble)")
        if message.get("from") == "brain":
            problems.append("the brain does not escalate to itself")
        if message.get("to") != "brain":
            problems.append("escalations are addressed to the brain")
        severity = message.get("severity")
        if severity is not None and severity not in SEVERITIES:
            problems.append(f"severity must be one of {', '.join(SEVERITIES)}")
    model = message.get("model")
    if model is not None:
        if not isinstance(model, dict):
            problems.append("model must be an object")
        else:
            if model.get("tier") not in MODEL_TIERS:
                problems.append(f"model.tier must be one of {', '.join(MODEL_TIERS)}")
            if model.get("thinking") not in THINKING_LEVELS:
                problems.append(f"model.thinking must be one of {', '.join(THINKING_LEVELS)}")
    task = message.get("task")
    if task is not None:
        if not isinstance(task, dict):
            problems.append("task must be an object")
        else:
            issue = task.get("issue")
            if not isinstance(issue, int) or isinstance(issue, bool) or issue < 1:
                problems.append("task.issue must be a positive integer")
            for field in ("epic",):
                if field in task and (not isinstance(task[field], int) or isinstance(task[field], bool) or task[field] < 1):
                    problems.append(f"task.{field} must be a positive integer")
    if "body" in message and not isinstance(message["body"], str):
        problems.append("body must be a string")
    return problems


def load_message(path: Path | str) -> dict:
    target = Path(path)
    try:
        raw = target.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"channel: CANNOT-ASSESS — cannot read {target}: {exc}", file=sys.stderr)
        raise SystemExit(EXIT_CANNOT_ASSESS)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"channel: CANNOT-ASSESS — {target} is not valid JSON: {exc.msg}", file=sys.stderr)
        raise SystemExit(EXIT_CANNOT_ASSESS)
    return data


def cmd_verify(args: argparse.Namespace) -> int:
    message = load_message(args.message)
    problems = validate(message)
    if problems:
        print(f"channel verify: REFUSED ({len(problems)} violation(s))", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return EXIT_NOT_OK
    print("channel verify: OK")
    return EXIT_OK


def replay_conflict(message: dict) -> str | None:
    """Reason this message replays an earlier delivery, or None when it is fresh.

    A directive is identified by its ``id`` and carries a ``nonce`` as its
    anti-replay token (contract §3). Either one recurring in the sent, inbox or
    done mailbox means the same order is being pushed twice — which the contract
    refuses rather than silently overwriting the queued copy.
    """
    if not message.get("id") and not message.get("nonce"):
        return None
    for directory in (SENT, INBOX, DONE):
        if not directory.exists():
            continue
        for path in directory.glob("*.json"):
            try:
                prior = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if message.get("id") and prior.get("id") == message["id"]:
                return f"id {message['id']} was already sent"
            if message.get("nonce") and prior.get("nonce") == message["nonce"]:
                return f"nonce {message['nonce']} was already used"
    return None


def _slog(message: dict) -> None:
    """Append one structured line to .fleet/slog.jsonl — the audit + live tail."""
    entry = {
        "ts": message.get("ts") or now_iso(),
        "id": message.get("id", ""),
        "from": message.get("from", ""),
        "to": message.get("to", ""),
        "type": message.get("type", ""),
        "correlation_id": message.get("correlation_id", ""),
        "severity": message.get("severity", ""),
        "body": (message.get("body") or "")[:200],
    }
    SLOG.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(SLOG, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        os.write(fd, (json.dumps(entry) + "\n").encode("utf-8"))
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def cmd_send(args: argparse.Namespace) -> int:
    message = load_message(args.message)
    problems = validate(message)
    if problems:
        print(f"channel send: REFUSED ({len(problems)} violation(s))", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return EXIT_NOT_OK
    if not message.get("id"):
        message["id"] = str(uuid.uuid4())
    if not message.get("ts"):
        message["ts"] = now_iso()
    if not message.get("nonce"):
        message["nonce"] = str(uuid.uuid4())
    conflict = replay_conflict(message)
    if conflict:
        print(f"channel send: REFUSED — replay detected ({conflict})", file=sys.stderr)
        return EXIT_NOT_OK
    message_id = message["id"]
    SENT.mkdir(parents=True, exist_ok=True)
    INBOX.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(message, indent=2) + "\n"
    for directory in (SENT, INBOX):
        (directory / f"{message_id}.json").write_text(payload, encoding="utf-8")
    _slog(message)
    print(f"channel send: OK — {message_id} queued for the sister")
    return EXIT_OK


def cmd_status(args: argparse.Namespace) -> int:
    def count(directory: Path) -> int:
        return len(list(directory.glob("*.json"))) if directory.exists() else 0

    print(f"inbox: {count(INBOX)} pending | sent: {count(SENT)} | outbox: {count(OUTBOX)}")
    return EXIT_OK


def consume_directive(message_id: str) -> bool:
    """Mark a directive complete: move it out of the inbox into .fleet/done/.

    Reporting the result IS the completion, so the inbox count is always the
    number of outstanding orders — a directive that was answered is gone.
    """
    source = INBOX / f"{message_id}.json"
    if not source.exists():
        return False
    DONE.mkdir(parents=True, exist_ok=True)
    source.replace(DONE / source.name)
    return True


def cmd_report(args: argparse.Namespace) -> int:
    """Executor side: write an ack/result answering a directive into the outbox."""
    message = {
        "from": args.from_role,
        "to": "brain",
        "type": args.type,
        "correlation_id": args.correlation,
    }
    if args.body:
        message["body"] = args.body
    problems = validate(message)
    if problems:
        print(f"channel report: REFUSED ({len(problems)} violation(s))", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return EXIT_NOT_OK
    message["id"] = str(uuid.uuid4())
    message["ts"] = now_iso()
    OUTBOX.mkdir(parents=True, exist_ok=True)
    (OUTBOX / f"{message['id']}.json").write_text(json.dumps(message, indent=2) + "\n", encoding="utf-8")
    consumed = consume_directive(args.correlation)
    suffix = " (directive consumed)" if consumed else ""
    _slog(message)
    print(f"channel report: OK — {message['id']} answers {args.correlation}{suffix}")
    return EXIT_OK


def cmd_escalate(args: argparse.Namespace) -> int:
    """Sister/subagent side: raise a problem to the brain for elite steering."""
    message = {
        "from": args.from_role,
        "to": "brain",
        "type": "escalate",
        "correlation_id": args.correlation,
        "severity": args.severity,
    }
    if args.body:
        message["body"] = args.body
    problems = validate(message)
    if problems:
        print(f"channel escalate: REFUSED ({len(problems)} violation(s))", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return EXIT_NOT_OK
    message["id"] = str(uuid.uuid4())
    message["ts"] = now_iso()
    OUTBOX.mkdir(parents=True, exist_ok=True)
    (OUTBOX / f"{message['id']}.json").write_text(json.dumps(message, indent=2) + "\n", encoding="utf-8")
    _slog(message)
    print(f"channel escalate: OK — {message['id']} escalated to the brain ({args.severity})")
    return EXIT_OK


def cmd_listen(args: argparse.Namespace) -> int:
    """Brain side: tail the slog stream, printing each message as it flows through.

    This is the idle-watch: run with `--timeout-seconds 0` and the terminal
    blocks, printing every directive/ack/result/escalate the moment it lands —
    an escalation from the sister pings the brain here. `--max-messages` bounds
    it for tests.
    """
    deadline = time.monotonic() + args.timeout_seconds if args.timeout_seconds > 0 else None
    seen = 0
    offset = 0
    while True:
        if SLOG.exists():
            with open(SLOG, encoding="utf-8") as handle:
                handle.seek(offset)
                lines = handle.read().splitlines()
                offset = handle.tell()
            for line in lines:
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    print(f"channel listen: {line}", flush=True)
                else:
                    print(json.dumps(entry, indent=2), flush=True)
                    print(
                        f"channel listen: {entry.get('type')} from {entry.get('from')}"
                        f" (severity {entry.get('severity') or '-'})",
                        flush=True,
                    )
                seen += 1
                if args.max_messages and seen >= args.max_messages:
                    return EXIT_OK
        if deadline is not None and time.monotonic() >= deadline:
            print(f"channel listen: IDLE — {seen} message(s) in window", file=sys.stderr)
            return EXIT_OK
        nap = args.interval
        if deadline is not None:
            nap = min(nap, max(0.0, deadline - time.monotonic()))
        time.sleep(nap)


def cmd_wait(args: argparse.Namespace) -> int:
    """Brain side: block until a result for this id (or correlation) lands in the outbox.

    This is the completion trigger of the operating model: push a directive with
    ``send``, then ``wait`` until the executor answers. A timeout is NOT-OK (1),
    never a silent pass.
    """
    target = args.id
    deadline = time.monotonic() + args.timeout_seconds if args.timeout_seconds > 0 else None
    OUTBOX.mkdir(parents=True, exist_ok=True)
    while True:
        for path in sorted(OUTBOX.glob("*.json")):
            try:
                message = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if message.get("id") == target or message.get("correlation_id") == target:
                print(json.dumps(message, indent=2))
                print(f"channel wait: TRIGGERED — {target} answered by {message.get('from')}")
                return EXIT_OK
        if deadline is not None and time.monotonic() >= deadline:
            print(f"channel wait: TIMEOUT — no result for {target} after {args.timeout_seconds}s", file=sys.stderr)
            return EXIT_NOT_OK
        nap = args.interval
        if deadline is not None:
            nap = min(nap, max(0.0, deadline - time.monotonic()))
        time.sleep(nap)


def cmd_watch(args: argparse.Namespace) -> int:
    """Sister side listener: return the oldest pending directive, or block for one.

    This is what makes the sister a dumb terminal with a pulse: it runs
    ``watch`` in a loop, executes the directive it prints, reports the result
    (which consumes the directive), then runs ``watch`` again. Exit 0 = a
    directive was returned; 1 = IDLE (nothing arrived before the timeout);
    2 = CANNOT-ASSESS (the inbox is unusable).
    """
    INBOX.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + args.timeout_seconds if args.timeout_seconds > 0 else None
    while True:
        pending = sorted(INBOX.glob("*.json"))
        if pending:
            try:
                message = json.loads(pending[0].read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                print(f"channel watch: CANNOT-ASSESS — {pending[0]} is unreadable ({exc})", file=sys.stderr)
                return EXIT_CANNOT_ASSESS
            print(json.dumps(message, indent=2))
            print(f"channel watch: DIRECTIVE {message.get('id')} — {len(pending)} pending")
            return EXIT_OK
        if deadline is not None and time.monotonic() >= deadline:
            print(f"channel watch: IDLE — no directive within {args.timeout_seconds}s", file=sys.stderr)
            return EXIT_NOT_OK
        nap = args.interval
        if deadline is not None:
            nap = min(nap, max(0.0, deadline - time.monotonic()))
        time.sleep(nap)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fleet-channel", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    verify = sub.add_parser("verify", help="validate a message against the contract")
    verify.add_argument("--message", required=True)
    verify.set_defaults(func=cmd_verify)

    send = sub.add_parser("send", help="validate and queue a message to the sister inbox")
    send.add_argument("--message", required=True)
    send.set_defaults(func=cmd_send)

    status = sub.add_parser("status", help="mailbox counts")
    status.set_defaults(func=cmd_status)

    report = sub.add_parser("report", help="write an ack/result answering a directive (executor side)")
    report.add_argument("--from", dest="from_role", required=True)
    report.add_argument("--type", choices=("ack", "result"), required=True)
    report.add_argument("--correlation", required=True)
    report.add_argument("--body", default=None)
    report.set_defaults(func=cmd_report)

    wait = sub.add_parser("wait", help="block until the outbox answers this id/correlation (brain side)")
    wait.add_argument("--id", required=True)
    wait.add_argument("--timeout-seconds", type=float, default=300.0)
    wait.add_argument("--interval", type=float, default=0.5)
    wait.set_defaults(func=cmd_wait)

    watch = sub.add_parser("watch", help="return the next pending directive, or block for one (sister side)")
    watch.add_argument("--timeout-seconds", type=float, default=600.0)
    watch.add_argument("--interval", type=float, default=1.0)
    watch.set_defaults(func=cmd_watch)

    escalate = sub.add_parser("escalate", help="raise a problem to the brain (sister/subagent side)")
    escalate.add_argument("--from", dest="from_role", required=True)
    escalate.add_argument("--correlation", required=True)
    escalate.add_argument("--severity", choices=SEVERITIES, required=True)
    escalate.add_argument("--body", default=None)
    escalate.set_defaults(func=cmd_escalate)

    listen = sub.add_parser("listen", help="tail the slog stream (brain side, idle-watch)")
    listen.add_argument("--timeout-seconds", type=float, default=0.0)
    listen.add_argument("--interval", type=float, default=1.0)
    listen.add_argument("--max-messages", type=int, default=0)
    listen.set_defaults(func=cmd_listen)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
