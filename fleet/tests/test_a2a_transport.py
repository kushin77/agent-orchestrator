"""The A2A transport extension: live logs, KB access and mid-run steering (#367).

Issue #367 extends the M26 file mailbox (ADR-0011, ``fleet/channel.py``) with
three capabilities the discrete-message transport could not carry:

* a **per-directive live log stream** — every stdout line the subagent writes
  is streamed by the loop into ``.fleet/runs/<directive>.log``, which the
  ``follow`` verb tails live (GR-21: localhost, file-based, no daemons — "live"
  is short-poll over the mailbox, not a socket service);
* a **``kb`` verb** that answers from the recorded knowledge catalogue
  (``governance/knowledge/``) so a running agent can pull KB/lessons mid-run;
* a **``steer`` message** the running loop honours mid-run — a brain-signed
  hint injected into the live child's stdin, echoed into its log stream and
  stamped into its run marker — so the brain can steer a stuck run without
  killing and re-dispatching it.

The house style here is the one #284/#286/#578 set: drive the REAL production
paths with out-of-process seams stubbed only where they are not the subject,
and assert on the EFFECT (the bytes the child actually received, the records
the stream and the marker actually hold), never on source text. Every claim
gets its negative control — a steer that never lands must leave the run
byte-for-byte unchanged, or "delivered" would be an untestable assertion.
"""

from __future__ import annotations

import argparse
import json
import shlex
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

import channel
import terminal

DIRECTIVE_ID = "d-a2a-367"
OTHER_ID = "d-a2a-368"
DIRECTIVE_ISSUE = 367
HINT = "the gate is green in the worktree — refresh the snapshot and re-run"

#: A child that ECHOES every line it reads from stdin: the strongest possible
#: effect assertion for `steer` — the loop writes the hint to the live child's
#: stdin and the child itself proves it received it, without being killed.
ECHO_CHILD = (
    "import sys\n"
    "for _ in range(5):\n"
    "    line = sys.stdin.readline()\n"
    "    if not line:\n"
    "        break\n"
    "    print('GOT:' + line.strip(), flush=True)\n"
)

#: A child that prints two marker lines and exits: the run the log stream must
#: capture per directive.
PRINT_CHILD = "print('ao367-log-alpha'); print('ao367-log-beta')"


def _dispatch(runner: str) -> dict:
    """A resolved FinOps block, so ``run_once`` exercises its real run path."""
    return {
        "tier": "flash",
        "thinking": "none",
        "model": "deepseek-v4-flash",
        "risk": "normal",
        "runner": runner,
        "env": {},
    }


def _directive(directive_id: str) -> dict:
    return {
        "id": directive_id,
        "ts": "2026-09-14T00:00:00Z",
        "type": "directive",
        "from": "brain",
        "to": "sister",
        "correlation_id": f"c-{directive_id}",
        "model": {"tier": "flash", "thinking": "none"},
        "task": {"kind": "work", "issue": DIRECTIVE_ISSUE, "lane": "fleet"},
        "body": "implement #367",
    }


def _stream_entries(directive_id: str) -> list[dict]:
    """The parsed JSONL entries of one directive's live log stream."""
    path = channel.log_stream_path(directive_id)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _read_child_lines(child) -> list[str]:
    """Drain the child's stdout on a thread; returns the collector it fills."""
    lines: list[str] = []

    def pump() -> None:
        stream = getattr(child, "stdout", None)
        if stream is None:
            return
        try:
            for raw in stream:
                lines.append(str(raw))
        except (OSError, ValueError):
            pass

    threading.Thread(target=pump, daemon=True).start()
    return lines


def _live_child(script: str):
    """A real child that stays alive until told otherwise, stdin as a pipe."""
    child = subprocess.Popen(
        [sys.executable, "-c", script],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    assert child.poll() is None, "the live child died before the steer was delivered"
    return child


def _kill(child) -> None:
    try:
        child.kill()
    except OSError:
        pass
    try:
        child.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


def _queue_steer(directive_id: str, body: str) -> int:
    """Queue a steer through the REAL channel verb (validate + write + slog)."""
    args = argparse.Namespace(directive=directive_id, body=body, message=None)
    return channel.cmd_steer(args)


class _FinishedChild:
    """A child whose run already ended: ``poll()`` returns its exit code."""

    def __init__(self) -> None:
        self.returncode = 0
        self.stdin = _StdinRecorder()

    def poll(self) -> int:
        return 0


class _StdinRecorder:
    """Stands in for a child's stdin: records what the loop wrote."""

    def __init__(self) -> None:
        self.written: list[str] = []

    def write(self, text: str) -> int:
        self.written.append(str(text))
        return len(text)

    def flush(self) -> None:
        pass


# --------------------------------------------------------------------------
# 1. the live log stream: captured per directive, tailed live by `follow`
# --------------------------------------------------------------------------


def test_run_once_streams_the_childs_stdout_into_the_directives_log(monkeypatch):
    """The REAL run path tees the child's stdout into `.fleet/runs/<id>.log`.

    ``run_once`` spawns the child with a drained pipe (the pump thread) and the
    stream captures every line the child prints — the same artifact `follow`
    tails. Asserted on the real child and the real stream file, per directive.
    """
    beaten: list[int] = []
    monkeypatch.setattr(
        terminal, "start_session_beat", lambda env, pid: (beaten.append(pid), None)[1]
    )
    runner = shlex.join([sys.executable, "-c", PRINT_CHILD])
    directive = _directive(DIRECTIVE_ID)

    rc, output = terminal.run_once(
        directive,
        runner,
        timeout=10.0,
        dry_run=False,
        agent_id="subagent-a2a",
        slot={"dispatch": _dispatch(runner)},
        context={},
    )

    assert rc == 0, f"the real child did not exit cleanly: {output}"
    assert "ao367-log-alpha" in output and "ao367-log-beta" in output, (
        f"the run path no longer returns the child's output: {output!r}"
    )
    entries = _stream_entries(DIRECTIVE_ID)
    lines = [entry["line"] for entry in entries]
    assert lines == ["ao367-log-alpha", "ao367-log-beta"], (
        f"the live log stream did not capture the child's stdout: {entries}"
    )
    assert all(entry["source"] == "subagent" for entry in entries), (
        "child stdout must be stamped as the subagent's own lines"
    )
    assert beaten, "the run path must still start the session beat around the child"


def test_each_directive_gets_its_own_log_stream(monkeypatch):
    """Two runs, two streams: a log stream is per directive, never shared."""
    monkeypatch.setattr(terminal, "start_session_beat", lambda env, pid: None)
    runner_a = shlex.join([sys.executable, "-c", PRINT_CHILD])
    runner_b = shlex.join([sys.executable, "-c", "print('ao367-log-other')"])

    rc, _ = terminal.run_once(
        _directive(DIRECTIVE_ID),
        runner_a,
        timeout=10.0,
        dry_run=False,
        agent_id="subagent-a2a",
        slot={"dispatch": _dispatch(runner_a)},
        context={},
    )
    assert rc == 0
    rc, _ = terminal.run_once(
        _directive(OTHER_ID),
        runner_b,
        timeout=10.0,
        dry_run=False,
        agent_id="subagent-a2a",
        slot={"dispatch": _dispatch(runner_b)},
        context={},
    )
    assert rc == 0

    first = [entry["line"] for entry in _stream_entries(DIRECTIVE_ID)]
    second = [entry["line"] for entry in _stream_entries(OTHER_ID)]
    assert first == ["ao367-log-alpha", "ao367-log-beta"], (
        f"the first run's stream changed when a second run streamed: {first}"
    )
    assert second == ["ao367-log-other"], f"the second run's stream is not its own: {second}"


def test_follow_tails_the_stream_live():
    """`follow` prints what the stream already holds AND lines written while it runs.

    The "live" half is the point (GR-21: short-poll over the mailbox, no socket):
    a line appended AFTER `follow` started still appears before its window ends.
    """
    channel.append_directive_log(DIRECTIVE_ID, "pre-existing line", source="sister")
    late = "ao367-written-while-following"

    def append_late() -> None:
        time.sleep(0.4)
        channel.append_directive_log(DIRECTIVE_ID, late, source="subagent")

    timer = threading.Timer(0.1, append_late)
    timer.start()
    try:
        args = argparse.Namespace(
            directive=DIRECTIVE_ID,
            timeout_seconds=2.0,
            interval=0.05,
            max_lines=0,
            from_start=True,
        )
        rc = channel.cmd_follow(args)
    finally:
        timer.join()

    assert rc == channel.EXIT_OK
    entries = _stream_entries(DIRECTIVE_ID)
    lines = [entry["line"] for entry in entries]
    assert "pre-existing line" in lines and late in lines, (
        f"the stream does not hold what follow must tail: {lines}"
    )


def test_follow_replays_and_bounds_with_max_lines(capsys):
    """`follow --from-start --max-lines N` replays the head and returns."""
    channel.append_directive_log(DIRECTIVE_ID, "one", source="sister")
    channel.append_directive_log(DIRECTIVE_ID, "two", source="sister")
    channel.append_directive_log(DIRECTIVE_ID, "three", source="sister")

    args = argparse.Namespace(
        directive=DIRECTIVE_ID,
        timeout_seconds=2.0,
        interval=0.05,
        max_lines=2,
        from_start=True,
    )
    rc = channel.cmd_follow(args)
    out = capsys.readouterr().out

    assert rc == channel.EXIT_OK
    assert "one" in out and "two" in out and "three" not in out, (
        f"follow did not honour --max-lines: {out!r}"
    )


def test_listen_is_the_follow_view(capsys):
    """The issue's own vocabulary: `follow`/`listen --directive <id>` — one view.

    ``listen`` is the alias operators reach for from a shell; it must tail the
    same per-directive stream `follow` tails.
    """
    channel.append_directive_log(DIRECTIVE_ID, "ao367-listen-line", source="sister")
    rc = channel.main(
        [
            "listen",
            "--directive",
            DIRECTIVE_ID,
            "--from-start",
            "--max-lines",
            "1",
            "--timeout-seconds",
            "2",
        ]
    )
    out = capsys.readouterr().out
    assert rc == channel.EXIT_OK
    assert "ao367-listen-line" in out, f"`listen` did not tail the stream: {out!r}"


def test_stream_run_event_appends_a_loop_owned_event():
    """The loop's own events (dispatch/claim/steer/verdict) ride the same stream."""
    terminal.stream_run_event(DIRECTIVE_ID, "claim taken for #367 by subagent-a2a")
    entries = _stream_entries(DIRECTIVE_ID)
    assert len(entries) == 1, f"expected exactly one event, got {entries}"
    assert entries[0]["source"] == "sister", "loop events are the sister's own lines"
    assert "claim taken for #367" in entries[0]["line"]


# --------------------------------------------------------------------------
# 2. `steer`: a mid-run hint the running loop honours — never a re-dispatch
# --------------------------------------------------------------------------


def test_steer_reaches_a_live_run_without_killing_or_redispatching_it():
    """A queued steer is injected into the LIVE child's stdin, stream and marker.

    The child echoes every stdin line back to stdout, so this is asserted at the
    receiving end: the run received the hint while it was still running, the
    stream recorded the delivery, the run marker carries the proof, and the
    queue drained. The child is never killed and nothing is re-dispatched.
    """
    child = _live_child(ECHO_CHILD)
    lines = _read_child_lines(child)
    try:
        terminal.mark_run(DIRECTIVE_ID, DIRECTIVE_ISSUE, "subagent-a2a")
        with terminal.RUNS_LOCK:
            terminal.IN_FLIGHT[DIRECTIVE_ID] = {"child": child}
        assert _queue_steer(DIRECTIVE_ID, HINT) == channel.EXIT_OK, "the steer was not queued"

        delivered = terminal.deliver_pending_steers()

        assert delivered == [DIRECTIVE_ID], f"the steer did not reach its run: {delivered}"
        assert child.poll() is None, "delivering a steer must never kill the run"
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and not any("GOT:" in line for line in lines):
            time.sleep(0.05)
        assert any("GOT:" in line and HINT in line for line in lines), (
            f"the live child never received the hint on its stdin: {lines}"
        )
        entries = _stream_entries(DIRECTIVE_ID)
        assert any(
            entry["source"] == "steer" and "STEER delivered" in entry["line"] and HINT in entry["line"]
            for entry in entries
        ), f"the log stream does not record the delivery: {entries}"
        marker = json.loads((terminal.RUNS / f"{DIRECTIVE_ID}.json").read_text(encoding="utf-8"))
        assert [entry["body"] for entry in marker.get("steered") or []] == [HINT], (
            f"the run marker does not carry the delivered steer: {marker}"
        )
        assert channel.pending_steers() == [], "a delivered steer must leave the queue"
    finally:
        with terminal.RUNS_LOCK:
            terminal.IN_FLIGHT.pop(DIRECTIVE_ID, None)
        _kill(child)


def test_without_a_steer_the_run_does_not_change():
    """Negative control: no queued steer means the run is byte-for-byte untouched.

    A "delivery" assertion that only ever saw a delivery is a formality (GR-12):
    the same live run, minus the steer, must show no stdin line, no stream
    record and no marker entry — otherwise "delivered" proves nothing.
    """
    child = _live_child(ECHO_CHILD)
    lines = _read_child_lines(child)
    try:
        terminal.mark_run(DIRECTIVE_ID, DIRECTIVE_ISSUE, "subagent-a2a")
        with terminal.RUNS_LOCK:
            terminal.IN_FLIGHT[DIRECTIVE_ID] = {"child": child}

        delivered = terminal.deliver_pending_steers()

        assert delivered == [], f"nothing was queued, yet a steer was reported delivered: {delivered}"
        time.sleep(0.4)  # the child would echo a steer almost immediately
        assert lines == [], f"the unsteered run received stdin lines: {lines}"
        assert all(entry["source"] != "steer" for entry in _stream_entries(DIRECTIVE_ID)), (
            "the log stream claims a delivery that never happened"
        )
        marker = json.loads((terminal.RUNS / f"{DIRECTIVE_ID}.json").read_text(encoding="utf-8"))
        assert "steered" not in marker, f"the marker changed without a steer: {marker}"
    finally:
        with terminal.RUNS_LOCK:
            terminal.IN_FLIGHT.pop(DIRECTIVE_ID, None)
        _kill(child)


def test_a_steer_for_a_run_that_has_not_started_stays_queued():
    """An early steer is not lost: no live slot means it stays pending."""
    assert _queue_steer(DIRECTIVE_ID, HINT) == channel.EXIT_OK
    assert len(channel.pending_steers()) == 1

    delivered = terminal.deliver_pending_steers()

    assert delivered == [], "a steer with no live run cannot be delivered"
    assert len(channel.pending_steers()) == 1, "the early steer must stay queued"


def test_a_steer_for_a_finished_run_is_consumed_never_redelivered():
    """A hint for a finished run must never steer the NEXT run of that directive."""
    finished = _FinishedChild()
    terminal.mark_run(DIRECTIVE_ID, DIRECTIVE_ISSUE, "subagent-a2a")
    with terminal.RUNS_LOCK:
        terminal.IN_FLIGHT[DIRECTIVE_ID] = {"child": finished}
    try:
        assert _queue_steer(DIRECTIVE_ID, HINT) == channel.EXIT_OK

        delivered = terminal.deliver_pending_steers()

        assert delivered == [], f"a finished run cannot receive a steer: {delivered}"
        assert finished.stdin.written == [], "the finished child was written to anyway"
        assert channel.pending_steers() == [], "the stale steer must be consumed, not redelivered"
        assert all(entry["source"] != "steer" for entry in _stream_entries(DIRECTIVE_ID)), (
            "the stream must not claim a delivery to a finished run"
        )
    finally:
        with terminal.RUNS_LOCK:
            terminal.IN_FLIGHT.pop(DIRECTIVE_ID, None)


def test_a_newer_steer_replaces_an_undelivered_older_one():
    """One pending steer per directive: the newest hint wins, the older never ships."""
    child = _live_child(ECHO_CHILD)
    lines = _read_child_lines(child)
    try:
        terminal.mark_run(DIRECTIVE_ID, DIRECTIVE_ISSUE, "subagent-a2a")
        with terminal.RUNS_LOCK:
            terminal.IN_FLIGHT[DIRECTIVE_ID] = {"child": child}
        older = "the first hint — already stale"
        assert _queue_steer(DIRECTIVE_ID, older) == channel.EXIT_OK
        assert _queue_steer(DIRECTIVE_ID, HINT) == channel.EXIT_OK
        assert len(channel.pending_steers()) == 1, "two steers for one directive must collapse to one"

        delivered = terminal.deliver_pending_steers()

        assert delivered == [DIRECTIVE_ID]
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and not any("GOT:" in line for line in lines):
            time.sleep(0.05)
        assert any("GOT:" in line and HINT in line for line in lines), "the newest hint was not delivered"
        assert not any(older in line for line in lines), "the stale hint was delivered anyway"
    finally:
        with terminal.RUNS_LOCK:
            terminal.IN_FLIGHT.pop(DIRECTIVE_ID, None)
        _kill(child)


def test_only_the_director_may_steer_and_the_hint_must_be_safe():
    """The steer verb rides the existing validator: hierarchy and safe ids hold.

    A `steer` is director->dispatcher, correlated to a directive whose id is a
    safe mailbox name — a dispatcher-issued steer, a principal-issued steer, a
    steer with no correlation or a traversal id are all REFUSED, never queued
    (#367 adds without weakening #162's boundary).
    """
    problems = channel.validate(
        {"from": "sister", "to": "brain", "type": "steer", "correlation_id": "d-1"}
    )
    assert "only the director may steer a run mid-flight" in problems, problems

    args = argparse.Namespace(
        directive="../outside", body=HINT, message=None
    )
    assert channel.cmd_steer(args) == channel.EXIT_NOT_OK
    assert channel.pending_steers() == [], "a refused steer must never reach the queue"


def test_log_follow_and_steer_refuse_an_unsafe_directive_id():
    """A directive id becomes a mailbox filename: `../` must not walk out."""
    unsafe = "../escaped"
    assert channel.cmd_log(
        argparse.Namespace(directive=unsafe, line="x", source=None)
    ) == channel.EXIT_NOT_OK
    assert channel.cmd_follow(
        argparse.Namespace(
            directive=unsafe, timeout_seconds=0.1, interval=0.01, max_lines=0, from_start=False
        )
    ) == channel.EXIT_NOT_OK
    assert channel.cmd_steer(
        argparse.Namespace(directive=unsafe, body=HINT, message=None)
    ) == channel.EXIT_NOT_OK
    with pytest.raises(ValueError, match="not a safe mailbox name"):
        channel.log_stream_path(unsafe)
    with pytest.raises(ValueError, match="not a safe mailbox name"):
        channel.append_directive_log(unsafe, "x")


# --------------------------------------------------------------------------
# 3. `kb`: a running agent pulls the institutional KB through the channel
# --------------------------------------------------------------------------


def test_kb_returns_the_index_record(capsys):
    """`kb --text <needle>` answers from the recorded catalogue, source-backed.

    The query goes through the same artefacts ``governance/knowledge/cli.py
    query`` reads (``catalog.json`` → ``Index.from_dict`` → ``query``), so a
    hit is a real index record, never an invented answer.
    """
    args = argparse.Namespace(
        text="ADR-0011", kind=None, owner=None, tag=None, limit=5, json=False
    )
    rc = channel.cmd_kb(args)
    out = capsys.readouterr().out

    assert rc == channel.EXIT_OK
    assert "docs/decision-records/ADR-0011-session-fleet-transport.md" in out, (
        f"the query did not return the transport ADR's index record: {out!r}"
    )


def test_kb_json_answers_with_source_backed_evidence(capsys):
    """`kb --json` returns the machine-readable summary with evidence per hit."""
    args = argparse.Namespace(
        text="ADR-0011", kind=None, owner=None, tag=None, limit=5, json=True
    )
    rc = channel.cmd_kb(args)
    out = capsys.readouterr().out

    assert rc == channel.EXIT_OK
    payload = json.loads(out)
    assert payload["result_count"] >= 1, f"no hits in the JSON answer: {payload}"
    record = payload["results"][0]
    assert record["id"] == "docs/decision-records/ADR-0011-session-fleet-transport.md", (
        f"the top hit is not the transport ADR record: {record}"
    )
    assert record["evidence"]["sha256"], "a source-backed hit must carry provenance evidence"


def test_kb_is_cannot_assess_when_the_catalogue_is_missing(tmp_path, monkeypatch, capsys):
    """No catalogue, no answer: `kb` reports CANNOT-ASSESS, never invents one."""
    knowledge_dir = tmp_path / "governance" / "knowledge"
    knowledge_dir.mkdir(parents=True)
    source = Path(channel.__file__).resolve().parent.parent / "governance" / "knowledge"
    for name in ("indexer.py", "model.py", "query.py", "sources.py", "secretpolicy.py", "crossref.py"):
        shutil.copy(source / name, knowledge_dir / name)
    monkeypatch.setattr(channel, "ROOT", tmp_path)

    rc = channel.cmd_kb(
        argparse.Namespace(text="anything", kind=None, owner=None, tag=None, limit=5, json=False)
    )
    err = capsys.readouterr().err

    assert rc == channel.EXIT_CANNOT_ASSESS
    assert "CANNOT-ASSESS" in err, f"a missing catalogue must be named, not guessed: {err!r}"
