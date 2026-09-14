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
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = ROOT / "fleet" / "schema" / "message.schema.json"
INBOX = ROOT / ".fleet" / "inbox"
BRAIN_INBOX = ROOT / ".fleet" / "brain" / "inbox"
BRAIN_OUTBOX = ROOT / ".fleet" / "brain" / "outbox"
BRAIN_DONE = ROOT / ".fleet" / "brain" / "done"
BRAIN_SENT = ROOT / ".fleet" / "brain" / "sent"
SENT = ROOT / ".fleet" / "sent"
OUTBOX = ROOT / ".fleet" / "outbox"
DONE = ROOT / ".fleet" / "done"
SLOG = ROOT / ".fleet" / "slog.jsonl"
HEARTBEAT = ROOT / ".fleet" / "sister.heartbeat.json"
BRAIN_HEARTBEAT = ROOT / ".fleet" / "brain.heartbeat.json"

# The FinOps vocabulary is harvested, not invented (issue #164) and is declared
# once in governance/finops/policy.json: tiers from capital-underwriting
# config/leaderboard/tier-policy.json, thinking effort from leaderboard
# lib/fleet-roster.sh role_effort(). scripts/check-finops-chooser.sh fails if
# these constants, the message schema and the policy stop agreeing.
MESSAGE_TYPES = ("directive", "ack", "result", "halt", "escalate")
SEVERITIES = ("info", "warn", "critical")
CONTROL_ACTIONS = (
    "poke",
    "status",
    "pause",
    "resume",
    "refresh",
    "restart",
    "stop",
    "kill",
    "halt",
    "override",
)
# Controls that act on the loop process itself rather than on the work queue.
PROCESS_CONTROLS = ("refresh", "restart", "stop", "kill", "halt")
# Control actions whose whole point is to re-dispatch a named issue.
TASK_CONTROLS = ("override",)
MODEL_TIERS = ("flash", "pro", "auditor")
THINKING_LEVELS = ("none", "low", "medium", "high")
# Order kinds (schema v1, additive): `work` needs an issue; the others are
# answered by the brain without dispatching anything to the sister.
TASK_KINDS = ("work", "status", "report", "ping")
NON_WORK_KINDS = ("status", "report", "ping")
_ROLE_RE = re.compile(r"^(operator|brain|sister|subagent(-[a-z0-9]+)?)$")

EXIT_OK = 0
EXIT_NOT_OK = 1
EXIT_CANNOT_ASSESS = 2

# A beat older than this means the loop died rather than that it is busy: the
# loop beats every poll cycle (default 30s) and before each directive.
STALE_HEARTBEAT_SECONDS = 120


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
            problems.append(f"{field} must match operator|brain|sister|subagent(-name)?")
    if "id" in message and (not isinstance(message["id"], str) or not message["id"].strip()):
        problems.append("id must be a non-empty string")
    if "ts" in message and (not isinstance(message["ts"], str) or not _parse_ts(message["ts"])):
        problems.append("ts must be an ISO-8601 timestamp")
    if "correlation_id" in message and not isinstance(message["correlation_id"], str):
        problems.append("correlation_id must be a string")
    if "nonce" in message and (not isinstance(message["nonce"], str) or not message["nonce"].strip()):
        problems.append("nonce must be a non-empty string (the anti-replay token)")
    if message_type == "directive" and message.get("to") not in ("sister", "brain"):
        problems.append("directives are addressed to the sister (from the brain) or to the brain (from the operator)")
    # Hierarchy (contract §4, rule 1b): the operator commands the brain, and the
    # brain commands the sister. Neither step may be skipped — an operator that
    # could address the sister directly would make the brain advisory. Reports
    # and escalations still travel *up* to the brain, so the rule is scoped to
    # the operator's own traffic and to directives addressed to the brain.
    if message.get("from") == "operator":
        if message.get("to") != "brain":
            problems.append(
                "the operator does not address the sister: it orders the brain, and the brain orders the sister"
            )
        elif message_type != "directive":
            problems.append("an operator order to the brain must be a directive")
    if message.get("to") == "brain" and message_type == "directive" and message.get("from") != "operator":
        problems.append("the brain takes orders only from the operator")
    if message_type == "directive" and message.get("to") == "sister" and message.get("from") != "brain":
        problems.append("only the brain may issue directives to the sister")
    if message.get("from") == "sister" and message_type == "directive":
        problems.append("the sister is a dumb terminal: it cannot issue directives")
    if message_type in ("ack", "result") and not message.get("correlation_id"):
        problems.append(f"{message_type} must carry correlation_id (the directive it answers)")
    if message_type in ("ack", "result") and message.get("from") == "brain" and message.get("to") != "operator":
        problems.append("the brain does not ack or report on its own directives (only back to the operator)")
    if message_type == "halt" and message.get("from") != "brain":
        problems.append("only the brain may issue a halt")
    if message_type == "escalate":
        if not message.get("correlation_id"):
            problems.append("escalate must carry correlation_id (the directive that hit trouble)")
        if message.get("from") == "brain":
            if message.get("to") != "operator":
                problems.append("a brain escalation is addressed to the operator (the next level up)")
        elif message.get("to") != "brain":
            problems.append("escalations go up: subagents and the sister escalate to the brain")
        severity = message.get("severity")
        if severity is not None and severity not in SEVERITIES:
            problems.append(f"severity must be one of {', '.join(SEVERITIES)}")
    control = message.get("control")
    if control is not None:
        if message.get("from") != "brain":
            problems.append("only the brain may issue control")
        if control not in CONTROL_ACTIONS:
            problems.append(f"control must be one of {', '.join(CONTROL_ACTIONS)}")
        if control in TASK_CONTROLS and not (message.get("task") or {}).get("issue"):
            problems.append(f"control '{control}' must name the task.issue it overrides")
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
            kind = task.get("kind")
            if kind is not None and kind not in TASK_KINDS:
                problems.append(f"task.kind must be one of {', '.join(TASK_KINDS)}")
            issue = task.get("issue")
            # A decompose order carries task.decompose (a micro-task plan) instead of
            # an issue; like a non-work kind, it needs no issue of its own.
            carries_decompose = isinstance(task.get("decompose"), dict) and bool(task["decompose"].get("children"))
            if kind not in NON_WORK_KINDS and not carries_decompose and (
                not isinstance(issue, int) or isinstance(issue, bool) or issue < 1
            ):
                problems.append("task.issue must be a positive integer")
            for field in ("epic",):
                if field in task and (not isinstance(task[field], int) or isinstance(task[field], bool) or task[field] < 1):
                    problems.append(f"task.{field} must be a positive integer")
    if "body" in message and not isinstance(message["body"], str):
        problems.append("body must be a string")
    return problems


def load_message(source: Path | str) -> dict:
    """Accept a path to a JSON file *or* inline JSON.

    The brain is a live terminal, so forcing it to write a temp file for every
    directive is pure friction — and a bare JSON argument was previously read as
    a filename (``File name too long``). Inline JSON is the natural form.
    """
    text = str(source)
    if text.lstrip().startswith("{"):
        raw, label = text, "<inline message>"
    else:
        target = Path(text)
        label = str(target)
        try:
            raw = target.read_text(encoding="utf-8")
        except OSError as exc:
            print(f"channel: CANNOT-ASSESS — cannot read {target}: {exc}", file=sys.stderr)
            raise SystemExit(EXIT_CANNOT_ASSESS)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"channel: CANNOT-ASSESS — {label} is not valid JSON: {exc.msg}", file=sys.stderr)
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


def replay_conflict(message: dict, directories: tuple[Path, ...] | None = None) -> str | None:
    """Reason this message replays an earlier delivery, or None when it is fresh.

    A directive is identified by its ``id`` and carries a ``nonce`` as its
    anti-replay token (contract §3). Either one recurring in the mailboxes this
    call path owns means the same order is being pushed twice — which the contract
    refuses rather than silently overwriting the queued copy.

    The mailbox set is a parameter, not a constant, because the two paths write to
    *different* mailboxes: ``send`` (brain → sister) writes ``SENT``/``INBOX`` and
    ``order`` (operator → brain) writes ``BRAIN_SENT``/``BRAIN_INBOX``. Scanning
    the sister's mailboxes from ``order`` made that guard unreachable — measured,
    an identical operator order was accepted twice (#278). ``None`` keeps the
    default a run-time lookup of the sister's mailboxes.
    """
    if not message.get("id") and not message.get("nonce"):
        return None
    for directory in (SENT, INBOX, DONE) if directories is None else directories:
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
    task = message.get("task") or {}
    entry = {
        "ts": message.get("ts") or now_iso(),
        "id": message.get("id", ""),
        "from": message.get("from", ""),
        "to": message.get("to", ""),
        "type": message.get("type", ""),
        "correlation_id": message.get("correlation_id", ""),
        "issue": message.get("issue") or task.get("issue"),
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
    conflict = replay_conflict(message, (SENT, INBOX, DONE))
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


def head_commit() -> str:
    """The current HEAD sha — what a freshly started loop would be running."""
    try:
        result = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return result.stdout.strip() or "unknown"


def read_heartbeat() -> dict | None:
    try:
        return json.loads(HEARTBEAT.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def heartbeat_age_seconds(beat: dict, moment: float | None = None) -> float | None:
    stamp = beat.get("ts")
    if not stamp:
        return None
    try:
        seen = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    reference = datetime.fromtimestamp(moment, tz=timezone.utc) if moment is not None else datetime.now(timezone.utc)
    return (reference - seen).total_seconds()


def running_loop_pids() -> list[int]:
    """PIDs of live `fleet/terminal.py` loops, to disambiguate NO-HEARTBEAT."""
    try:
        result = subprocess.run(
            ["pgrep", "-f", "fleet/terminal.py"], capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return [int(line) for line in result.stdout.split() if line.strip().isdigit()]


def report_rung(name: str, heartbeat_path: Path, process: str, start_cmd: str) -> bool:
    """Report one rung's liveness and code drift; True when it is live and current.

    Shared by the brain and the sister so neither can be silently absent from
    `status`, and so a rung running merged-but-unrestarted code is reported as
    such instead of looking dead.
    """
    try:
        beat = json.loads(heartbeat_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        beat = None
    if beat is None:
        try:
            alive = subprocess.run(
                ["pgrep", "-f", process], capture_output=True, text=True, timeout=10
            ).returncode == 0
        except (OSError, subprocess.SubprocessError):
            alive = False
        if alive:
            print(
                f"{name}: NO HEARTBEAT from a loop that IS running — it is executing a build older "
                f"than the heartbeat check, so merged fixes are not live. Restart: {start_cmd}"
            )
        else:
            print(f"{name}: NO HEARTBEAT and no process — this rung is down (start: {start_cmd})")
        return False

    age = heartbeat_age_seconds(beat)
    state = beat.get("state", "?")
    if age is None:
        print(f"{name}: heartbeat present (pid {beat.get('pid', '?')}, state {state}) but undated")
        return False
    verdict = "live" if age <= STALE_HEARTBEAT_SECONDS else f"STALE ({int(age)}s since last beat)"
    running = str(beat.get("commit", "unknown"))
    current = head_commit()
    print(f"{name}: {verdict} — pid {beat.get('pid', '?')}, state {state}, last beat {int(age)}s ago")
    print(f"{name}: running commit {running} | HEAD {current}")
    if current != "unknown" and running != current:
        print(
            f"{name}: CODE DRIFT — it is running {running}, not HEAD {current}; merged fixes are not "
            f"live. Restart: {start_cmd}"
        )
        return False
    return age <= STALE_HEARTBEAT_SECONDS


def cmd_status(args: argparse.Namespace) -> int:
    def count(directory: Path) -> int:
        return len(list(directory.glob("*.json"))) if directory.exists() else 0

    print(
        f"inbox: {count(INBOX)} pending | sent: {count(SENT)} | outbox: {count(OUTBOX)}\n"
        f"brain: {count(BRAIN_INBOX)} order(s) pending | {count(BRAIN_DONE)} dispatched | "
        f"{count(BRAIN_OUTBOX)} reply(ies)"
    )
    brain_ok = report_rung("brain", BRAIN_HEARTBEAT, "fleet/brain.py", "bash fleet/brain.sh")
    sister_ok = report_rung("sister", HEARTBEAT, "fleet/terminal.py", "bash fleet/terminal.sh")
    return EXIT_OK if (brain_ok and sister_ok) else EXIT_NOT_OK


def cmd_order(args: argparse.Namespace) -> int:
    """Top of the hierarchy: the operator orders the *brain*, never the sister.

    The operator trigger exists so the chain is real code — operator → brain →
    sister — rather than a convention the transport cannot enforce. `send` is
    brain→sister and refuses an operator sender, so this is the only way in.

    The anti-replay scan covers the mailboxes *this* path writes
    (``BRAIN_SENT``/``BRAIN_INBOX``/``BRAIN_DONE``): the shared default scans the
    sister's mailboxes, so the guard could never fire here and an identical order
    was accepted twice (#278).
    """
    message = load_message(args.message)
    message.setdefault("from", "operator")
    message.setdefault("to", "brain")
    message.setdefault("type", "directive")
    problems = validate(message)
    if problems:
        print(f"channel order: REFUSED ({len(problems)} violation(s))", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return EXIT_NOT_OK
    if not message.get("id"):
        message["id"] = str(uuid.uuid4())
    if not message.get("ts"):
        message["ts"] = now_iso()
    if not message.get("nonce"):
        message["nonce"] = str(uuid.uuid4())
    conflict = replay_conflict(message, (BRAIN_SENT, BRAIN_INBOX, BRAIN_DONE))
    if conflict:
        print(f"channel order: REFUSED — replay detected ({conflict})", file=sys.stderr)
        return EXIT_NOT_OK
    message_id = message["id"]
    payload = json.dumps(message, indent=2) + "\n"
    for directory in (BRAIN_SENT, BRAIN_INBOX):
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{message_id}.json").write_text(payload, encoding="utf-8")
    _slog(message)
    print(f"channel order: OK — {message_id} queued for the brain")
    return EXIT_OK


def cmd_brain_inbox(args: argparse.Namespace) -> int:
    """The brain's own watch: the oldest order from the operator, if any."""
    deadline = time.monotonic() + args.timeout_seconds if args.timeout_seconds > 0 else None
    while True:
        pending = sorted(BRAIN_INBOX.glob("*.json")) if BRAIN_INBOX.exists() else []
        if pending:
            print(pending[0].read_text(encoding="utf-8"), flush=True)
            return EXIT_OK
        if deadline is not None and time.monotonic() >= deadline:
            print("channel brain-inbox: IDLE — no order", file=sys.stderr)
            return EXIT_NOT_OK
        time.sleep(args.interval)


def consume_order(message_id: str) -> bool:
    """The brain has dispatched (or refused) the order: move it to done/."""
    source = BRAIN_INBOX / f"{message_id}.json"
    if not source.exists():
        return False
    BRAIN_DONE.mkdir(parents=True, exist_ok=True)
    source.replace(BRAIN_DONE / source.name)
    return True


def brain_reply(order: dict, message_type: str, body: str) -> None:
    """Answer the operator in the brain outbox — the report the operator reads."""
    message = {
        "from": "brain",
        "to": "operator",
        "type": message_type,
        "correlation_id": str(order.get("id") or order.get("correlation_id") or ""),
        "id": str(uuid.uuid4()),
        "ts": now_iso(),
        "nonce": str(uuid.uuid4()),
        "body": body[:2000],
    }
    BRAIN_OUTBOX.mkdir(parents=True, exist_ok=True)
    (BRAIN_OUTBOX / f"{message['id']}.json").write_text(json.dumps(message, indent=2) + "\n", encoding="utf-8")
    _slog(message)


def cmd_brain_outbox(args: argparse.Namespace) -> int:
    """Operator side: read the brain's replies (acks and refusals), newest last.

    Ordered by the message timestamp, not by filename: ids are uuid4, so a
    filename sort returns the replies in arbitrary order and the operator reads
    a stale answer as if it were the current one (observed live).
    """

    def by_time(path: Path) -> float:
        try:
            message = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return 0.0
        stamp = str(message.get("ts") or "")
        try:
            seen = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            return path.stat().st_mtime if path.exists() else 0.0
        return seen.timestamp()

    replies = sorted(BRAIN_OUTBOX.glob("*.json"), key=by_time) if BRAIN_OUTBOX.exists() else []
    if not replies:
        print("channel brain-outbox: no replies from the brain yet")
        return EXIT_NOT_OK
    for path in replies[-args.limit :] if args.limit else replies:
        try:
            message = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            print(f"channel brain-outbox: unreadable {path.name}: {exc}", file=sys.stderr)
            continue
        print(
            f"{message.get('ts', '-')} {message.get('type', '-')} "
            f"(order {message.get('correlation_id', '-')}): {message.get('body', '')}"
        )
    return EXIT_OK


def cmd_consume(args: argparse.Namespace) -> int:
    """Mark a directive handled without reporting a result.

    Process controls (`kill`, `halt`, `refresh`, `restart`) act on the loop
    itself, so there is no result to report — but the message must still leave the
    inbox. A control that acted and stayed pending re-fires against the next loop:
    measured, an unconsumed `kill` would have taken down every loop started after
    it.
    """
    if consume_directive(args.id):
        print(f"channel consume: OK — {args.id} handled (moved to done)")
        return EXIT_OK
    print(f"channel consume: nothing to consume for {args.id}", file=sys.stderr)
    return EXIT_NOT_OK


def cmd_head_commit(args: argparse.Namespace) -> int:
    print(head_commit())
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
    offset = 0 if getattr(args, "from_start", False) else (SLOG.stat().st_size if SLOG.exists() else 0)
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
                    print(
                        f"channel listen: {entry.get('ts', '-')} {entry.get('type', '-')} "
                        f"from {entry.get('from', '-')} -> {entry.get('to', '-')} "
                        f"(issue {entry.get('issue') or (entry.get('task') or {}).get('issue') or '-'}, "
                        f"severity {entry.get('severity') or '-'})",
                        flush=True,
                    )
                    print(json.dumps(entry, indent=2), flush=True)
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


def ordered_by_time(directory: Path) -> list[Path]:
    """Messages in the order they were sent, not in uuid order.

    Ids are uuid4, so a filename sort is arbitrary: measured, the sister took a
    `resume` before the `pause` it was meant to lift and the fetched order changed
    run to run. The envelope's `ts` is the only ordering the transport has.
    """

    def sent_at(path: Path) -> tuple[str, str]:
        try:
            message = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return ("", path.name)
        return (str(message.get("ts") or ""), path.name)

    return sorted(directory.glob("*.json"), key=sent_at)


def cmd_watch(args: argparse.Namespace) -> int:
    """Sister side listener: return the oldest pending directive, or block for one.

    This is what makes the sister a dumb terminal with a pulse: it runs
    ``watch`` in a loop, executes the directive it prints, reports the result
    (which consumes the directive), then runs ``watch`` again. Exit 0 = a
    directive was returned; 1 = IDLE (nothing arrived before the timeout);
    2 = CANNOT-ASSESS (the inbox is unusable).

    ``--skip ID`` (repeatable) removes directives the caller has already
    dispatched but not yet consumed, so a pool of N concurrent workers can keep
    draining the inbox past the directives still in flight instead of re-reading
    the oldest one forever.
    """
    INBOX.mkdir(parents=True, exist_ok=True)
    skip = set(getattr(args, "skip", None) or [])
    deadline = time.monotonic() + args.timeout_seconds if args.timeout_seconds > 0 else None
    while True:
        pending = [path for path in ordered_by_time(INBOX) if path.stem not in skip]
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
    watch.add_argument("--skip", action="append", default=[], help="directive ids to skip (already dispatched)")
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
    listen.add_argument("--from-start", action="store_true", help="replay the whole slog instead of tailing from now")
    listen.set_defaults(func=cmd_listen)

    order = sub.add_parser("order", help="operator side: order the BRAIN (never the sister)")
    order.add_argument("--message", required=True)
    order.set_defaults(func=cmd_order)

    brain_inbox = sub.add_parser("brain-inbox", help="brain side: the oldest operator order, or block for one")
    brain_inbox.add_argument("--timeout-seconds", type=float, default=0.0)
    brain_inbox.add_argument("--interval", type=float, default=1.0)
    brain_inbox.set_defaults(func=cmd_brain_inbox)

    brain_outbox = sub.add_parser("brain-outbox", help="operator side: the brain's replies")
    brain_outbox.add_argument("--limit", type=int, default=10)
    brain_outbox.set_defaults(func=cmd_brain_outbox)

    head = sub.add_parser("head-commit", help="print the commit a freshly started loop would run")
    head.set_defaults(func=cmd_head_commit)

    consume = sub.add_parser("consume", help="mark a directive handled without a result (process controls)")
    consume.add_argument("--id", required=True)
    consume.set_defaults(func=cmd_consume)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
