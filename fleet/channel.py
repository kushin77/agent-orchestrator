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

Messages are validated against `fleet/schema/message.schema.json` semantics
before they move. Exit codes are the repo tri-state: 0 OK / 1 NOT-OK /
2 CANNOT-ASSESS.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = ROOT / "fleet" / "schema" / "message.schema.json"
INBOX = ROOT / ".fleet" / "inbox"
SENT = ROOT / ".fleet" / "sent"
OUTBOX = ROOT / ".fleet" / "outbox"

MESSAGE_TYPES = ("directive", "ack", "result", "halt")
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
    if message_type == "directive" and message.get("to") != "sister":
        problems.append("directives may only be addressed to the sister")
    if message.get("from") == "sister" and message_type == "directive":
        problems.append("the sister is a dumb terminal: it cannot issue directives")
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
    message_id = message["id"]
    SENT.mkdir(parents=True, exist_ok=True)
    INBOX.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(message, indent=2) + "\n"
    for directory in (SENT, INBOX):
        (directory / f"{message_id}.json").write_text(payload, encoding="utf-8")
    print(f"channel send: OK — {message_id} queued for the sister")
    return EXIT_OK


def cmd_status(args: argparse.Namespace) -> int:
    def count(directory: Path) -> int:
        return len(list(directory.glob("*.json"))) if directory.exists() else 0

    print(f"inbox: {count(INBOX)} pending | sent: {count(SENT)} | outbox: {count(OUTBOX)}")
    return EXIT_OK


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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
