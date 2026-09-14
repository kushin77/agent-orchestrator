"""The operator → brain rung: orders go to the brain, and only the brain → sister.

Issue #160 named three rungs but the operator's only working trigger was to write
into the sister's inbox — i.e. to do the brain's job. These tests pin the
hierarchy the transport now enforces, and the brain loop that makes it real.
"""

from __future__ import annotations

import json
import signal
import time
from pathlib import Path

import pytest

import brain
import channel
from channel import validate


def order(**overrides):
    message = {
        "from": "operator",
        "to": "brain",
        "type": "directive",
        "correlation_id": "o-1",
        "task": {"issue": 5, "lane": "fleet"},
        "body": "dispatch one subagent for issue #5",
    }
    message.update(overrides)
    return message


# --- the hierarchy, enforced by the channel ---------------------------------


def test_an_operator_order_to_the_brain_is_valid():
    assert validate(order()) == []


def test_the_brain_takes_orders_only_from_the_operator():
    problems = validate(order(**{"from": "sister"}))
    assert any("only from the operator" in problem for problem in problems)


def test_an_order_to_the_brain_must_be_a_directive():
    problems = validate(order(type="ack"))
    assert any("must be a directive" in problem for problem in problems)


def test_the_operator_cannot_address_the_sister_directly():
    """The regression this rule exists for: bypassing the brain entirely."""
    problems = validate({"from": "operator", "to": "sister", "type": "result", "correlation_id": "x"})
    assert any("the operator does not address the sister" in problem for problem in problems)


def test_only_the_brain_may_issue_directives_to_the_sister():
    problems = validate(
        {"from": "subagent-x", "to": "sister", "type": "directive", "task": {"issue": 5}}
    )
    assert any("only the brain may issue directives to the sister" in problem for problem in problems)


def test_the_brain_reports_back_to_the_operator():
    assert validate({"from": "brain", "to": "operator", "type": "ack", "correlation_id": "o-1"}) == []


def test_the_brain_does_not_ack_the_sister():
    problems = validate({"from": "brain", "to": "sister", "type": "result", "correlation_id": "d-1"})
    assert any("does not ack or report on its own directives" in problem for problem in problems)


def test_a_brain_escalation_goes_to_the_operator():
    assert validate(
        {"from": "brain", "to": "operator", "type": "escalate", "severity": "warn", "correlation_id": "o-1"}
    ) == []
    problems = validate(
        {"from": "brain", "to": "sister", "type": "escalate", "severity": "warn", "correlation_id": "o-1"}
    )
    assert any("addressed to the operator" in problem for problem in problems)


def test_the_sister_still_escalates_to_the_brain():
    assert validate(
        {"from": "sister", "to": "brain", "type": "escalate", "severity": "critical", "correlation_id": "d-1"}
    ) == []


# --- the operator trigger ----------------------------------------------------


def test_a_non_work_order_needs_no_issue_and_a_work_order_still_does():
    """The live bug: `task.kind: status` was refused for lacking an issue."""
    assert validate({"from": "operator", "to": "brain", "type": "directive", "task": {"kind": "status"}}) == []
    problems = validate({"from": "operator", "to": "brain", "type": "directive", "task": {}})
    assert any("task.issue must be a positive integer" in problem for problem in problems)
    problems = validate(
        {"from": "operator", "to": "brain", "type": "directive", "task": {"kind": "whenever"}}
    )
    assert any("task.kind must be one of" in problem for problem in problems)


def test_a_decompose_order_needs_no_issue_of_its_own():
    """A decompose order carries task.decompose, not task.issue — the validator must
    let it through (the paper-feature class: schema extended, validator not)."""
    order = {
        "from": "operator",
        "to": "brain",
        "type": "directive",
        "task": {
            "decompose": {
                "parent_issue": 219,
                "children": [{"title": "a", "verify": "pytest a", "depends_on": []}],
            }
        },
    }
    assert validate(order) == []


def test_order_queues_for_the_brain(tmp_path, monkeypatch):
    monkeypatch.setattr(channel, "BRAIN_INBOX", tmp_path / "brain" / "inbox")
    monkeypatch.setattr(channel, "BRAIN_SENT", tmp_path / "brain" / "sent")
    monkeypatch.setattr(channel, "SLOG", tmp_path / "slog.jsonl")
    result = channel.cmd_order(_Args(message=json.dumps(order())))
    assert result == channel.EXIT_OK
    assert len(list(channel.BRAIN_INBOX.glob("*.json"))) == 1


def test_order_refuses_a_message_the_operator_may_not_send(tmp_path, monkeypatch):
    monkeypatch.setattr(channel, "BRAIN_INBOX", tmp_path / "brain" / "inbox")
    monkeypatch.setattr(channel, "BRAIN_SENT", tmp_path / "brain" / "sent")
    monkeypatch.setattr(channel, "SLOG", tmp_path / "slog.jsonl")
    bypass = json.dumps({"from": "operator", "to": "sister", "type": "directive", "task": {"issue": 5}})
    assert channel.cmd_order(_Args(message=bypass)) == channel.EXIT_NOT_OK


class _Args:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


# --- the brain's own runtime isolation (issue #274) --------------------------
#
# `conftest.RUNTIME_PATHS["brain"]` was written before the brain owned a marker
# directory, and conftest.py belongs to another lane — so the redirect for the
# new path lives here rather than silently writing into the live `.fleet/` tree
# (the exact failure the conftest fixture exists to prevent).


@pytest.fixture(autouse=True)
def _redirect_dispatch_markers(tmp_path, monkeypatch):
    monkeypatch.setattr(brain, "DISPATCH_MARKERS", tmp_path / "brain" / "dispatched")


@pytest.fixture
def stub_channel(tmp_path):
    """A stand-in for `fleet/channel.py`: answers with a fixed payload and exit code."""

    def make(payload: str = "", rc: int = 1):
        script = tmp_path / "stub-channel.py"
        script.write_text(
            "import sys\n"
            f"sys.stdout.write({payload!r})\n"
            f"sys.exit({rc})\n",
            encoding="utf-8",
        )
        return script

    return make


@pytest.fixture
def quiet_signals(monkeypatch):
    """Keep `loop` from installing process-global signal handlers inside pytest."""
    monkeypatch.setattr(brain.signal, "signal", lambda *args: None)


# --- the brain rung ----------------------------------------------------------


def test_build_directive_signs_it_as_the_brain():
    directive = brain.build_directive(order())
    assert directive["from"] == "brain" and directive["to"] == "sister"
    assert directive["correlation_id"] == "o-1"
    assert validate(directive) == []


def test_the_brain_raises_the_floor_for_security_lanes():
    """Security/auth/IaC never dispatch at flash/none (fleet doctrine)."""
    directive = brain.build_directive(order(task={"issue": 9, "lane": "secrets-handling"}))
    assert directive["model"] == {"tier": "pro", "thinking": "low"}


def test_the_brain_does_not_lower_a_tier_the_operator_raised():
    directive = brain.build_directive(order(model={"tier": "pro", "thinking": "high"}))
    assert directive["model"] == {"tier": "pro", "thinking": "high"}


# --- #300 / ADR-0012: dispatch is capability-routed through the policy --------
#
# The brain used to derive the FinOps block from a private dialect. It now consults
# `fleet/routing.py` (which reads `fleet/profiles/routing.policy.json` and the
# registry persona cards), so these tests are the brain's half of the contract: the
# persona it names in the directive is the one the registry declared, and the
# floors stay derived rather than re-declared.


def test_the_brain_routes_a_lane_through_the_policy_to_its_registry_persona():
    directive = brain.build_directive(order(task={"issue": 7, "lane": "hermes"}))
    body = directive["body"]
    assert "capability=code-author" in body
    assert "persona=hermes" in body
    assert "registry/personas/cards/hermes.yaml" in body, "the directive must name the card it read"
    # Hermes is a MED persona; MED maps onto flash, and thinking stays off there.
    assert directive["model"] == {"tier": "flash", "thinking": "none"}
    assert validate(directive) == []


def test_the_brain_routes_research_to_the_knowledge_persona():
    directive = brain.build_directive(order(task={"issue": 8, "lane": "research"}))
    assert "persona=paperclip" in directive["body"]
    assert "registry/personas/cards/paperclip.yaml" in directive["body"]
    assert directive["model"] == {"tier": "flash", "thinking": "none"}


def test_a_lane_that_claims_no_capability_says_so_instead_of_inventing_one():
    directive = brain.build_directive(order(task={"issue": 5, "lane": "fleet"}))
    assert "claims no capability" in directive["body"]
    assert directive["model"] == {"tier": "flash", "thinking": "none"}


def test_an_unroutable_capability_claim_is_refused_by_name():
    """A capability the registry cannot back is a named refusal, never a default."""
    ok, report = brain.handle_order(order(task={"issue": 11, "lane": "fleet", "capability": "make-coffee"}))
    assert ok is False
    assert "dispatch refused for #11" in report
    assert "capability-unknown" in report and "make-coffee" in report


def test_the_high_floor_still_wins_over_a_persona_route():
    """ADR-0012 decision (b): the floor outranks the persona tier, unchanged."""
    directive = brain.build_directive(
        order(task={"issue": 12, "lane": "hermes", "title": "harden the secrets path"})
    )
    assert directive["model"] == {"tier": "pro", "thinking": "low"}
    assert "capability=code-author" in directive["body"]


def test_the_brain_derives_its_floor_vocabulary_from_the_policy():
    """The brain has no dialect of its own: floors and defaults come from the policy."""
    assert brain.HIGH_FLOOR_LANES == brain.ROUTING.high_floor_tokens
    assert (brain.DEFAULT_TIER, brain.DEFAULT_THINKING) == brain.ROUTING.default_block
    assert (brain.HIGH_TIER, brain.HIGH_THINKING) == brain.ROUTING.high_floor_block
    assert set(brain.HIGH_FLOOR_LANES) == set(brain.PROFILE["finops"]["high_floor_lanes"])


def test_handle_order_refuses_one_that_names_no_issue():
    ok, report = brain.handle_order(order(task={"lane": "fleet"}))
    assert ok is False
    assert "names no issue" in report


def test_handle_order_dispatches_through_the_channel(monkeypatch):
    seen = {}

    def fake_dispatch(order_):
        seen["directive"] = brain.build_directive(order_)
        return True, "channel send: OK — abc queued for the sister"

    monkeypatch.setattr(brain, "dispatch", fake_dispatch)
    ok, report = brain.handle_order(order())
    assert ok is True
    assert seen["directive"]["to"] == "sister"
    assert "#5" in report


def test_handle_order_reports_a_refused_dispatch_verbatim(monkeypatch):
    monkeypatch.setattr(brain, "dispatch", lambda order_: (False, "REFUSED — no-chain-edge: #5 is not next"))
    ok, report = brain.handle_order(order())
    assert ok is False
    assert "no-chain-edge" in report


def test_non_work_orders_do_not_dispatch(monkeypatch):
    called = {"n": 0}

    def fake_dispatch(order_):
        called["n"] += 1
        return True, "x"

    monkeypatch.setattr(brain, "dispatch", fake_dispatch)
    monkeypatch.setattr(brain, "_health", lambda: (0, ["healthy"]))
    ok, report = brain.handle_order(order(task={"kind": "status"}))
    assert ok is True
    assert called["n"] == 0
    assert "no dispatch" in report


# --- mid-run steering (#367): operator → brain → sister, never a shortcut -------


class _RelayResult:
    """A ``subprocess.run`` result the steer relay can read."""

    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_a_steer_order_is_relayed_to_the_channel(monkeypatch):
    """`task.kind: steer` is forwarded as a brain-signed channel steer, not invented.

    The hierarchy stays intact (issue #367): the operator orders the brain, the
    brain relays a `steer` through `fleet/channel.py` — the operator never
    addresses the sister, and the brain adds no content of its own.
    """
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):  # noqa: ARG001
        calls.append(list(command))
        return _RelayResult(0, "channel steer: OK — steering hint queued for run d-367", "")

    monkeypatch.setattr(brain.subprocess, "run", fake_run)
    ok, report = brain.handle_order(
        order(task={"kind": "steer", "directive": "d-367"}, body="refresh the snapshot and re-run")
    )

    assert ok is True
    assert "d-367" in report
    assert len(calls) == 1, f"the relay must be exactly one channel invocation: {calls}"
    command = calls[0]
    assert command[:3] == ["python3", brain.CHANNEL, "steer"], (
        f"the relay must go through the channel's steer verb: {command!r}"
    )
    assert "--directive" in command and "d-367" in command, "the steer must name its run"
    assert "--body" in command and "refresh the snapshot and re-run" in command, (
        "the hint must travel verbatim"
    )


def test_a_steer_order_reports_a_channel_refusal_verbatim(monkeypatch):
    """A steer the channel refuses is a refusal here — never a silent drop."""
    monkeypatch.setattr(
        brain.subprocess,
        "run",
        lambda *a, **k: _RelayResult(1, "", "channel steer: REFUSED (1 violation(s))"),
    )
    ok, report = brain.handle_order(
        order(task={"kind": "steer", "directive": "d-367"}, body="refresh the snapshot")
    )
    assert ok is False
    assert "steer refused by the channel" in report


def test_a_steer_order_without_a_directive_is_refused():
    ok, report = brain.handle_order(order(task={"kind": "steer"}, body="a hint"))
    assert ok is False
    assert "names no directive" in report


def test_a_steer_order_with_an_unsafe_directive_id_is_refused():
    ok, report = brain.handle_order(
        order(task={"kind": "steer", "directive": "../outside"}, body="a hint")
    )
    assert ok is False
    assert "unsafe directive id" in report


def test_a_steer_order_without_a_hint_is_refused():
    ok, report = brain.handle_order(order(task={"kind": "steer", "directive": "d-367"}, body=""))
    assert ok is False
    assert "carries no hint" in report


def test_a_steer_order_never_dispatches_work(monkeypatch):
    """Steering is not a dispatch: no directive is sent to the sister queue."""
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):  # noqa: ARG001
        calls.append(list(command))
        return _RelayResult(0, "channel steer: OK", "")

    monkeypatch.setattr(brain.subprocess, "run", fake_run)

    def forbidden_dispatch(order_):
        raise AssertionError(f"a steer must never dispatch: {order_}")

    monkeypatch.setattr(brain, "dispatch", forbidden_dispatch)
    ok, report = brain.handle_order(
        order(task={"kind": "steer", "directive": "d-367"}, body="refresh the snapshot")
    )
    assert ok is True
    assert len(calls) == 1 and "steer" in calls[0], "the steer must go through the steer verb"


# --- the context stream (the brain window must show something) ----------------


def _facts(**overrides):
    facts = {
        "orders_pending": 0,
        "dispatched": 11,
        "waves": {219: [232]},
        "claims": 0,
        "head": "8d9c219",
        "watchdog": "healthy",
    }
    facts.update(overrides)
    return facts


def test_the_idle_status_line_carries_every_field_the_operator_needs():
    """The brain runs detached; this line is the only proof it is alive."""
    line = brain.status_line(_facts(), idle_seconds=30)
    assert line.startswith("[brain] idle 30s | ")
    for field in (
        "orders pending=0",
        "dispatched=11",
        "waves: #219=[232]",
        "claims=0",
        "HEAD=8d9c219",
        "watchdog=healthy",
    ):
        assert field in line, f"{field!r} is missing from {line!r}"


def test_the_startup_form_of_the_status_line_carries_the_same_fields():
    line = brain.status_line(_facts(orders_pending=2))
    assert line.startswith("[brain] up | ")
    assert "orders pending=2" in line and "HEAD=" in line and "watchdog=" in line


def test_format_waves_is_empty_safe():
    assert brain.format_waves({}) == "waves: none"
    assert brain.format_waves({219: [232], 240: []}) == "waves: #219=[232], #240=[]"


def test_wave_progress_reads_the_plan_files_and_survives_a_broken_one(tmp_path, monkeypatch):
    monkeypatch.setattr(brain, "WAVES", tmp_path)
    (tmp_path / "219.json").write_text(
        json.dumps({"parent": 219, "children": [], "dispatched": [232, 233]}), encoding="utf-8"
    )
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    assert brain.wave_progress() == {219: [232, 233]}


def test_the_status_header_says_where_the_stream_goes():
    header = brain.status_header(_facts(), pid=4242)
    assert "pid      4242" in header
    assert str(brain.LOG_PATH) in header, "the operator must be told which log this stream lands in"
    assert "[brain] up |" in header


def test_the_order_line_shows_the_id_the_task_and_the_body():
    line = brain.order_line(order())
    assert "order o-1" in line and "(#5)" in line
    assert '"issue": 5' in line
    assert "dispatch one subagent for issue #5" in line


def test_the_outcome_line_reports_a_dispatch_with_its_tier():
    line = brain.outcome_line(order(), True, "channel send: OK")
    assert line.endswith("→ dispatched #5 at flash/none — channel send: OK")


def test_the_outcome_line_quotes_the_refusal_verbatim():
    line = brain.outcome_line(order(), False, "order names no issue")
    assert line.endswith("→ refused: order names no issue")


def test_the_outcome_line_marks_a_non_work_order_as_an_ack():
    line = brain.outcome_line(order(task={"kind": "ping"}), True, "fleet health 0 healthy")
    assert "→ ack (no dispatch)" in line


def test_a_wave_advance_is_reported_with_the_issues_it_dispatched():
    assert brain.wave_line([232, 234]) == "[brain] → advanced waves: dispatched [232, 234]"


def test_brain_outbox_reads_newest_last(tmp_path, monkeypatch, capsys):
    """Ids are uuid4, so a filename sort shows the operator a stale reply."""
    monkeypatch.setattr(channel, "BRAIN_OUTBOX", tmp_path / "brain" / "outbox")
    channel.BRAIN_OUTBOX.mkdir(parents=True, exist_ok=True)
    for name, stamp, body in (
        ("zzz-first-id", "2026-09-13T19:00:00Z", "the older reply"),
        ("aaa-second-id", "2026-09-13T20:00:00Z", "the newer reply"),
    ):
        (channel.BRAIN_OUTBOX / f"{name}.json").write_text(
            json.dumps(
                {
                    "from": "brain",
                    "to": "operator",
                    "type": "ack",
                    "id": name,
                    "ts": stamp,
                    "correlation_id": "o-1",
                    "body": body,
                }
            ),
            encoding="utf-8",
        )
    assert channel.cmd_brain_outbox(type("Args", (), {"limit": 0})()) == channel.EXIT_OK
    out = capsys.readouterr().out
    assert out.index("the older reply") < out.index("the newer reply")


def test_brain_reply_lands_in_the_operator_outbox(tmp_path, monkeypatch):
    monkeypatch.setattr(channel, "BRAIN_OUTBOX", tmp_path / "brain" / "outbox")
    monkeypatch.setattr(channel, "SLOG", tmp_path / "slog.jsonl")
    channel.brain_reply(order(), "ack", "dispatched #5 at flash/none")
    files = list(channel.BRAIN_OUTBOX.glob("*.json"))
    assert len(files) == 1
    reply = json.loads(files[0].read_text(encoding="utf-8"))
    assert reply["to"] == "operator" and reply["correlation_id"] == "o-1"
    assert validate(reply) == []


def test_the_brain_writes_a_heartbeat(tmp_path, monkeypatch):
    monkeypatch.setattr(brain, "HEARTBEAT", tmp_path / "brain.heartbeat.json")
    brain.write_heartbeat("idle", started_at="2026-09-13T00:00:00Z", commit="abc1234")
    entry = json.loads(Path(brain.HEARTBEAT).read_text(encoding="utf-8"))
    assert entry["state"] == "idle" and entry["commit"] == "abc1234" and entry["pid"] > 0


def test_the_directive_carries_the_enterprise_instruction_stack():
    """A subagent gets the SAME enterprise instruction stack as the operator's
    top-level agent — the DeepSeek module, the CMR module, and the module files."""
    enterprise = brain.PROFILE["kb"]["enterprise_instructions"]
    assert "~/cmr/AGENTS.md" in enterprise
    assert "~/deepseek/AGENTS.md" in enterprise

    directive = brain.build_directive(order())
    body = directive["body"]
    # every `~`-prefixed path is expanded to a real, loadable path in the directive
    for source in enterprise:
        assert str(Path(source).expanduser()) in body
    assert str(Path("~/cmr/AGENTS.md").expanduser()) in body
    assert str(Path("~/deepseek/AGENTS.md").expanduser()) in body


# --- #274: a long order must not look dead, and a restart must not re-dispatch --


def test_the_brain_beats_while_an_order_is_handled(tmp_path, monkeypatch):
    """The beat has to move *during* the handler, not only before it.

    `handle_decompose` files N child issues and refreshes the board — longer than
    the watchdog's 120s stale threshold, and a stale brain is SIGTERMd mid-order.
    """
    monkeypatch.setattr(brain, "HEARTBEAT", tmp_path / "brain.heartbeat.json")
    beater = brain.OrderBeater(
        "dispatching", started_at="2026-09-13T00:00:00Z", commit="abc1234", interval=0.01
    ).start()
    try:
        time.sleep(0.05)
        seen = json.loads(brain.HEARTBEAT.read_text(encoding="utf-8"))
        assert seen["state"] == "dispatching"
        assert seen["commit"] == "abc1234"
        first_stamp = brain.HEARTBEAT.stat().st_mtime_ns
        time.sleep(0.05)
        assert brain.HEARTBEAT.stat().st_mtime_ns > first_stamp, "the beater stopped beating"
    finally:
        beater.stop()
    stopped_at = brain.HEARTBEAT.stat().st_mtime_ns
    time.sleep(0.05)
    assert brain.HEARTBEAT.stat().st_mtime_ns == stopped_at, "the beater outlived its owner"


def test_the_loop_keeps_beating_while_a_handler_runs(monkeypatch, stub_channel, quiet_signals):
    """End-to-end: the loop's own beat stays fresh for the whole of a slow order."""
    monkeypatch.setattr(brain, "CHANNEL", str(stub_channel(payload=json.dumps(order(id="o-beat")), rc=0)))
    monkeypatch.setattr(brain, "HEARTBEAT_INTERVAL_SECONDS", 0.01)
    stamps: set[int] = set()

    def slow_handler(order_):
        deadline = time.monotonic() + 0.2
        while time.monotonic() < deadline:
            if brain.HEARTBEAT.exists():
                stamps.add(brain.HEARTBEAT.stat().st_mtime_ns)
            time.sleep(0.005)
        return True, "handled slowly"

    monkeypatch.setattr(brain, "safe_handle_order", slow_handler)
    assert brain.loop(_Args(watch_timeout=0.02, once=True)) == 0
    assert len(stamps) > 1, "the beat never moved while the handler was running"
    stopped_at = brain.HEARTBEAT.stat().st_mtime_ns
    time.sleep(0.05)
    assert brain.HEARTBEAT.stat().st_mtime_ns == stopped_at, "the beater outlived the loop"


def test_a_sigterm_stops_the_brain_cleanly():
    """No handler meant the watchdog's SIGTERM killed the brain outright."""
    with pytest.raises(SystemExit) as exc:
        brain.handle_stop(signal.SIGTERM, None)
    assert exc.value.code == 128 + signal.SIGTERM


def test_the_loop_installs_a_clean_stop_for_sigterm_and_sigint(monkeypatch, stub_channel):
    installed = []
    monkeypatch.setattr(brain.signal, "signal", lambda signum, handler: installed.append((signum, handler)))
    monkeypatch.setattr(brain, "CHANNEL", str(stub_channel()))
    # The idle path now runs the completion-triggered advance (#701), which would
    # fetch the live board — stub it so this signal-install test stays offline.
    monkeypatch.setattr(brain, "advance_ready", lambda: [])
    assert brain.loop(_Args(watch_timeout=0.02, once=True)) == 0
    assert (signal.SIGTERM, brain.handle_stop) in installed
    assert (signal.SIGINT, brain.handle_stop) in installed


def test_the_directive_identity_is_derived_from_the_order_not_random():
    """Without a stable id the channel's replay guard can never fire (#274)."""
    first = brain.build_directive(order(id="4242"))["id"]
    assert first == brain.build_directive(order(id="4242"))["id"]
    assert brain.build_directive(order(id="4243"))["id"] != first
    assert validate(brain.build_directive(order(id="4242"))) == []


def test_a_restart_does_not_dispatch_the_same_order_twice(tmp_path, monkeypatch):
    """The P8 proof: re-reading one order dispatched it twice (`['4242', '4242']`).

    Also pins the *ordering* of the fix — the marker must be on disk before the
    channel is invoked. Moving the write after the send flips the probe to
    `marker-absent` and this test fails.
    """
    log = tmp_path / "sends.txt"
    marker = brain.DISPATCH_MARKERS / "4242.json"
    stub = tmp_path / "probe-channel.py"
    stub.write_text(
        "import pathlib, sys\n"
        f"marker = pathlib.Path({str(marker)!r})\n"
        f"log = pathlib.Path({str(log)!r})\n"
        "log.open('a', encoding='utf-8').write('marker-present\\n' if marker.exists() else 'marker-absent\\n')\n"
        "print('channel send: OK — queued for the sister')\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(brain, "CHANNEL", str(stub))
    same_order = order(id="4242")

    first = brain.handle_order(same_order)
    second = brain.handle_order(same_order)

    assert log.read_text(encoding="utf-8").split() == ["marker-present"], "the marker was written after the send"
    assert first[0] is True and "dispatched #5" in first[1]
    assert second[0] is True and "already dispatched" in second[1]
    assert json.loads(marker.read_text(encoding="utf-8"))["state"] == "sent"


def test_a_refused_send_leaves_no_marker_so_a_corrected_order_can_still_go_out(tmp_path, monkeypatch):
    monkeypatch.setattr(brain, "CHANNEL", str(_failing_channel(tmp_path)))
    ok, message = brain.handle_order(order(id="4242"))
    assert ok is False and "dispatch refused" in message
    assert brain.order_marker(order(id="4242")) is not None
    assert not brain.order_marker(order(id="4242")).exists()


def _failing_channel(tmp_path) -> Path:
    """A channel stand-in that refuses like `cmd_send` does: non-zero, nothing queued."""
    script = tmp_path / "refusing-channel.py"
    script.write_text(
        "import sys\n"
        "sys.stderr.write('channel send: REFUSED (1 violation(s))\\n')\n"
        "sys.exit(1)\n",
        encoding="utf-8",
    )
    return script


# --- #275: a malformed order is a refusal, never a dead brain -----------------


def test_a_decompose_order_without_a_parent_issue_is_refused_not_a_crash():
    """The P2 proof: `KeyError('parent_issue')` propagated out of the loop."""
    ok, report = brain.handle_order(order(task={"decompose": {"children": [{"title": "t"}]}}))
    assert ok is False
    assert "parent_issue" in report


def test_a_decompose_order_whose_children_are_not_objects_is_refused():
    ok, report = brain.handle_order(order(task={"decompose": {"parent_issue": 219, "children": ["nope"]}}))
    assert ok is False
    assert "children must each be an object" in report


def test_a_decompose_order_with_no_children_is_still_refused():
    ok, report = brain.handle_order(order(task={"decompose": {"parent_issue": 219, "children": []}}))
    assert ok is False
    assert "carries no children" in report


def test_the_loop_survives_a_handler_that_raises(monkeypatch, stub_channel, quiet_signals):
    """Whatever the handler does, the loop reports it and keeps running."""
    monkeypatch.setattr(brain, "CHANNEL", str(stub_channel(payload=json.dumps(order(id="o-boom")), rc=0)))

    def boom(order_):
        raise RuntimeError("handler exploded")

    monkeypatch.setattr(brain, "handle_order", boom)
    assert brain.loop(_Args(watch_timeout=0.02, once=True)) == 1, "a handler error must be a refusal, not a death"

    replies = sorted(channel.BRAIN_OUTBOX.glob("*.json"))
    assert replies, "the refusal must reach the operator"
    assert "handler error RuntimeError: handler exploded" in replies[-1].read_text(encoding="utf-8")


def test_safe_handle_order_does_not_swallow_a_stop(monkeypatch):
    """`SystemExit` is the stop handler: a stop must stop, not be reported."""

    def stop(order_):
        raise SystemExit(143)

    monkeypatch.setattr(brain, "handle_order", stop)
    with pytest.raises(SystemExit):
        brain.safe_handle_order(order())


def test_safe_handle_order_reports_any_other_failure(monkeypatch):
    def boom(order_):
        raise KeyError("parent_issue")

    monkeypatch.setattr(brain, "handle_order", boom)
    ok, report = brain.safe_handle_order(order())
    assert ok is False
    assert "handler error KeyError: 'parent_issue'" in report


# --- F10: a decomposed child must keep its title onto the wave directive ------


def test_a_decomposed_child_keeps_its_title_into_the_wave_directive(tmp_path, monkeypatch):
    """`title` was never stored, so every wave directive shipped `task.title == ""`
    and the title-based FinOps high-floor detection had nothing to read."""
    monkeypatch.setattr(brain, "WAVES", tmp_path / "waves")
    monkeypatch.setattr(brain, "gh_issue_create", lambda title, body: 4242)
    sent: list[dict] = []
    monkeypatch.setattr(brain, "dispatch", lambda order_: (sent.append(order_), (True, "channel send: OK"))[1])

    class _Refresh:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(brain.subprocess, "run", lambda *a, **k: _Refresh())

    ok, report = brain.handle_decompose(
        order(
            task={
                "decompose": {
                    "parent_issue": 219,
                    "children": [{"title": "harden the gate", "verify": "pytest -q", "lane": "fleet"}],
                }
            }
        )
    )

    assert ok is True and "decomposed #219" in report
    plan = json.loads((tmp_path / "waves" / "219.json").read_text(encoding="utf-8"))
    assert plan["children"][0]["title"] == "harden the gate"
    assert sent and sent[0]["task"]["title"] == "harden the gate"


# --- #701: completion-triggered advance --------------------------------------


def _advance_board():
    """A board whose child #11 is unlocked by a closed parent #10; #12 is unrelated."""
    parent = brain.snapshot_mod.Issue(10, "closed parent", state="closed")
    child = brain.snapshot_mod.Issue(11, "child of the parent", parent=10)
    unrelated = brain.snapshot_mod.Issue(12, "unrelated open issue")
    return brain.snapshot_mod.Snapshot(
        generated_at="2026-09-14T00:00:00Z",
        source="test",
        issues={10: parent, 11: child, 12: unrelated},
    )


def _stub_advance_board(monkeypatch, board):
    """Fetch the live board in memory, offline: no `gh`, no committed-file write."""
    monkeypatch.setattr(brain.snapshot_mod, "github_records", lambda repo: [])
    monkeypatch.setattr(brain.snapshot_mod, "build_snapshot", lambda records, source: board)
    monkeypatch.setattr(brain.claims, "read_ledger", lambda *a, **k: [])
    monkeypatch.setattr(brain.claims, "active_claims", lambda events: {})


def test_a_completion_advances_the_newly_ready_set(monkeypatch):
    """Closing a parent unblocks its child in the same cycle: `advance_ready`
    fetches the board, recomputes the ready set, and dispatches exactly the
    dependency-free issue — never the unrelated open one (kanban scavenging)."""
    _stub_advance_board(monkeypatch, _advance_board())

    sent: list[dict] = []
    monkeypatch.setattr(
        brain,
        "dispatch",
        lambda order_: (sent.append(order_), (True, "channel send: OK — queued"))[1],
    )

    dispatched = brain.advance_ready()

    assert dispatched == [11], f"only the unblocked child should advance, got {dispatched}"
    assert [order_["task"]["issue"] for order_ in sent] == [11]
    assert all(order_["task"]["issue"] != 12 for order_ in sent), (
        "the unrelated open issue must never be dispatched"
    )


def test_a_completion_with_no_ready_set_dispatches_nothing(monkeypatch):
    """A board where every child is still blocked yields no dispatch, not a crash."""
    parent = brain.snapshot_mod.Issue(20, "still-open parent")
    child = brain.snapshot_mod.Issue(21, "child of an open parent", parent=20)
    board = brain.snapshot_mod.Snapshot(
        generated_at="2026-09-14T00:00:00Z",
        source="test",
        issues={20: parent, 21: child},
    )
    _stub_advance_board(monkeypatch, board)

    sent: list[dict] = []
    monkeypatch.setattr(brain, "dispatch", lambda order_: (sent.append(order_), (True, "ok"))[1])

    assert brain.advance_ready() == []
    assert sent == []
