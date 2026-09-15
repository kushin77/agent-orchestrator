"""The dumb-terminal loop and its helpers (issue for session launchers)."""

from __future__ import annotations

import argparse
import json
import signal
import subprocess
import threading
import time

import terminal


def test_build_prompt_contains_issue_and_directive():
    directive = {
        "id": "d-1",
        "task": {"issue": 163, "lane": "fleet"},
        "model": {"tier": "flash", "thinking": "low"},
        "body": "harvest-first",
    }
    prompt = terminal.build_prompt(directive)
    assert "#163" in prompt
    assert "d-1" in prompt
    assert "harvest-first" in prompt
    # The loop owns the claim, so the subagent must not re-claim or release it.
    assert "ALREADY CLAIMED" in prompt
    assert "do NOT run claim" in prompt


def test_build_prompt_mandates_merge_and_rehandles_an_existing_pr():
    directive = {
        "id": "d-merge",
        "task": {"issue": 164, "lane": "fleet"},
        "model": {"tier": "flash", "thinking": "low"},
        "body": "merge-mandate",
    }
    prompt = terminal.build_prompt(directive)
    # A completed PR must be merged, never left open.
    assert "gh pr merge <number> --squash --delete-branch" in prompt
    assert "NEVER leave a completed PR unmerged" in prompt
    # A re-dispatch that finds an existing PR verifies + merges, not bails out.
    assert "ALREADY exists" in prompt
    assert "do NOT bail out" in prompt
    # The report must carry the merge result.
    assert "merge result" in prompt
    assert "merged/closed" in prompt


def test_build_prompt_names_the_isolated_worktree(tmp_path):
    tree = tmp_path / "ao-163-dabc1234"
    prompt = terminal.build_prompt(
        {"id": "d-abc12345", "task": {"issue": 163}, "body": "x"},
        "subagent-dabc1234",
        tree,
    )
    assert str(tree) in prompt
    assert "never in the " in prompt
    assert "shared checkout" in prompt


def test_build_prompt_states_the_session_identity_and_the_ticket_trailer():
    """An agent that is not told its identity cannot keep its commits traceable."""
    prompt = terminal.build_prompt(
        {"id": "d-abc12345", "task": {"issue": 163}, "body": "x"},
        "subagent-dabc1234",
        None,
        {"AO_SESSION_ID": "abc123def456", "AO_BRANCH": "issue-163"},
    )
    assert "abc123def456" in prompt
    assert "issue-163" in prompt
    assert "Refs kushin77/agent-orchestrator#163" in prompt


def test_build_prompt_frontloads_the_live_cicd_and_replaceability_mandate():
    """The standing mandate must be in the FIRST paragraph, not buried at the end.

    The operator's standing order (2026-09-14) is that every agent complies to a
    live CI/CD SDLC and is replaceable at any moment. The repo's own convention is
    to frontload the goal, so a subagent that reads only the first lines still
    knows it is gated and that it is not authoritative.
    """
    directive = {
        "id": "d-mandate",
        "task": {"issue": 163, "lane": "fleet"},
        "model": {"tier": "flash", "thinking": "low"},
        "body": "frontload-me",
    }
    prompt = terminal.build_prompt(directive)
    first_paragraph = prompt.split("\n\n", 1)[0]
    assert "STANDING MANDATE" in first_paragraph
    assert "LIVE CI/CD SDLC" in first_paragraph
    assert "REPLACEABILITY" in first_paragraph
    # The mandate precedes the order and the role statement.
    assert prompt.index("STANDING MANDATE") < prompt.index("BRAIN DIRECTIVE d-mandate")
    assert prompt.index("STANDING MANDATE") < prompt.index("epic-focused subagent")
    assert "frontload-me" in prompt
    # The gate of record: the issue's own Verify: AND make verify, real output.
    assert "`make verify`" in prompt
    assert "REAL output" in prompt
    # Atomic + green, and the apply pipeline rather than a console click.
    assert "one issue = one lane = one self-contained, green, reversible commit" in prompt
    assert "never a console click" in prompt
    # GR-15: no GitHub Actions workflow.
    assert "no agent adds a GitHub Actions workflow" in prompt.lower() or "GitHub Actions" in prompt
    # Replaceability: execute only this directive; state in artifacts; terminal.
    assert "Execute ONLY this directive" in prompt
    assert "never only in your context" in prompt
    assert "LEAVE EVERY ARTIFACT TERMINAL" in prompt


def test_build_prompt_standing_mandate_is_carried_verbatim_from_the_directive():
    """The prompt's mandate is the standing directive's clauses, not a paraphrase."""
    standing = terminal.load_standing_body()
    assert "LIVE CI/CD SDLC (standing clause)" in standing
    assert "REPLACEABILITY / LIVE INSTRUCTIONS (standing clause)" in standing
    prompt = terminal.build_prompt({"id": "d-1", "task": {"issue": 163}, "body": "x"})
    assert standing in prompt


def test_build_prompt_degrades_when_the_standing_directive_is_unreadable(tmp_path):
    """A missing directive file must not kill the spawn: the mandate degrades."""
    missing = tmp_path / "absent.json"
    assert terminal.load_standing_body(missing) == ""
    # An empty standing body still yields the explicit block (the inline default).
    assert "STANDING MANDATE" in terminal.build_prompt({"id": "d-1", "task": {"issue": 163}, "body": "x"})


def test_extract_json_parses_watch_output():
    text = '{\n "id": "d-1",\n "type": "directive"\n}\nchannel watch: DIRECTIVE d-1 — 1 pending'
    assert terminal.extract_json(text) == {"id": "d-1", "type": "directive"}


def test_extract_json_returns_empty_for_garbage():
    assert terminal.extract_json("no json here") == {}


def test_build_command_appends_the_prompt_as_the_final_argument():
    command = terminal.build_command({"id": "d-1", "task": {"issue": 163}, "body": "x"}, "claude -p", "subagent-d1")
    assert command[0] == "claude"
    assert command[1] == "-p"
    assert "issue #163" in command[-1]
    assert "subagent-d1" in command[-1]


def test_run_once_dry_run_prints_and_returns_zero():
    directive = {"id": "d-1", "task": {"issue": 163}, "body": "x"}
    rc, output = terminal.run_once(directive, "claude -p", 10.0, dry_run=True, agent_id="subagent-d1")
    assert rc == 0
    assert "DRY-RUN" in output


def test_build_prompt_uses_the_unique_agent_id():
    directive = {"id": "d-abc12345", "task": {"issue": 163, "lane": "fleet"}, "body": "x"}
    prompt = terminal.build_prompt(directive, "subagent-dabc1234")
    assert "subagent-dabc1234" in prompt


def test_provision_worktree_returns_none_when_the_provisioner_refuses(monkeypatch):
    """A failed provisioning must be visible, not silently shared-checkout."""

    class Failed:
        returncode = 1
        stdout = ""
        stderr = "open: REFUSED — base ref 'origin/master' does not resolve"

    monkeypatch.setattr(terminal.subprocess, "run", lambda *a, **k: Failed())
    assert terminal.provision_worktree(163, "d-abc12345", "subagent-dabc1234", "fleet") is None


def test_provision_worktree_returns_the_lane_and_its_session_environment(monkeypatch):
    """The loop gets the lane plus the identity the subagent must run under."""
    payload = json.dumps(
        {
            "identity": {
                "session_id": "abc123def456",
                "worktree": "/lanes/ao-163-abc123de",
                "branch": "issue-163",
            },
            "env": {"AO_SESSION_ID": "abc123def456", "AO_BRANCH": "issue-163"},
            "problems": [],
        }
    )

    class Ok:
        returncode = 0
        stdout = payload
        stderr = ""

    monkeypatch.setattr(terminal.subprocess, "run", lambda *a, **k: Ok())
    path, branch, env = terminal.provision_worktree(163, "d-abc12345", "subagent-dabc1234", "fleet")
    assert str(path) == "/lanes/ao-163-abc123de"
    assert branch == "issue-163"
    assert env["AO_SESSION_ID"] == "abc123def456"


def test_provision_worktree_refuses_unreadable_provisioner_output(monkeypatch):
    class Garbage:
        returncode = 0
        stdout = "not json at all"
        stderr = ""

    monkeypatch.setattr(terminal.subprocess, "run", lambda *a, **k: Garbage())
    assert terminal.provision_worktree(163, "d-abc12345", "subagent-dabc1234", "fleet") is None


def test_provision_worktree_names_the_issue_and_the_agent_in_the_order(monkeypatch):
    """The lane is minted *through the isolation module*, for this issue and agent."""
    seen = {}

    class Ok:
        returncode = 0
        stdout = json.dumps(
            {
                "identity": {"worktree": "/lanes/ao-163-abc123de", "branch": "issue-163"},
                "env": {},
                "problems": [],
            }
        )
        stderr = ""

    def capture(command, **kwargs):
        seen["command"] = command
        return Ok()

    monkeypatch.setattr(terminal.subprocess, "run", capture)
    terminal.provision_worktree(163, "d-abc12345", "subagent-dabc1234", "fleet")
    assert "governance/isolation/cli.py" in " ".join(seen["command"])
    assert seen["command"][seen["command"].index("--agent") + 1] == "subagent-dabc1234"
    assert seen["command"][seen["command"].index("--issue") + 1] == "163"


def test_claim_issue_reports_the_refusal_verbatim(monkeypatch):
    class Refused:
        returncode = 1
        stdout = ""
        stderr = "no-chain-edge: #5 is not the next step"

    monkeypatch.setattr(terminal.subprocess, "run", lambda *a, **k: Refused())
    ok, output = terminal.claim_issue(5, "subagent-x", "fleet", "d-1")
    assert ok is False and "no-chain-edge" in output


class _Ok:
    returncode = 0


def test_held_action_distinguishes_in_flight_from_orphaned():
    """The regression: an untracked holder read as 'in flight' and the directive
    was consumed, so the run was recorded nowhere and the claim stayed wedged."""
    assert terminal.held_action("subagent-abc", "subagent-abc", "live") == "in-flight"
    assert terminal.held_action("other-agent", "subagent-abc", "live") == "in-flight"
    assert terminal.held_action("subagent-abc", "subagent-abc", "orphaned") == "self-heal"
    assert terminal.held_action("other-agent", "subagent-abc", "orphaned") == "orphaned"
    assert terminal.held_action("other-agent", "subagent-abc", "none") == "orphaned"
    assert terminal.held_action(None, "subagent-abc", "none") == "orphaned"


def test_a_run_marker_is_live_then_orphaned_when_its_loop_dies(tmp_path, monkeypatch):
    monkeypatch.setattr(terminal, "RUNS", tmp_path / "runs")
    assert terminal.run_state("d-1") == "none"

    terminal.mark_run("d-1", 142, "subagent-d1")
    assert terminal.run_state("d-1") == "live", "our own live loop must read as tracked"

    dead = subprocess.Popen(["true"])
    dead.wait()
    (terminal.RUNS / "d-2.json").write_text(
        json.dumps({"issue": 142, "agent": "subagent-d2", "pid": dead.pid}), encoding="utf-8"
    )
    assert terminal.run_state("d-2") == "orphaned", "a run whose loop is gone is not in flight"

    terminal.clear_run("d-1")
    assert terminal.run_state("d-1") == "none"


def test_report_once_says_it_once_per_state(tmp_path, monkeypatch):
    """A pending directive is re-read every cycle; the report must not repeat."""
    sent = []

    def fake_run(command, **kwargs):
        sent.append(command)
        return _Ok()

    monkeypatch.setattr(terminal, "REPORTED", tmp_path / "reported")
    monkeypatch.setattr(terminal.subprocess, "run", fake_run)

    assert terminal.report_once("d-1", "orphaned:agent-x", "escalate", "body") is True
    assert terminal.report_once("d-1", "orphaned:agent-x", "escalate", "body") is False
    assert len(sent) == 1, "the same state must not be reported twice"
    assert terminal.report_once("d-1", "orphaned:agent-y", "escalate", "body") is True
    assert len(sent) == 2, "a changed state is news again"


def test_clear_reported_lets_a_new_cycle_speak(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(terminal, "REPORTED", tmp_path / "reported")
    monkeypatch.setattr(terminal.subprocess, "run", lambda command, **kwargs: (sent.append(command), _Ok())[1])
    terminal.report_once("d-1", "in-flight:agent-x", "result", "body")
    terminal.clear_reported("d-1")
    terminal.report_once("d-1", "in-flight:agent-x", "result", "body")
    assert len(sent) == 2


def test_a_run_keeps_its_beat_fresh_while_the_child_works(tmp_path, monkeypatch):
    """A long run must not look like a dead loop — the misread cost two restarts."""
    monkeypatch.setattr(terminal, "RUNS", tmp_path / "runs")
    slot = {"child": None}
    terminal.mark_run("d-1", 142, "subagent-x")

    beater = terminal.start_beating("d-1", interval=0.05, slot=slot)
    try:
        time.sleep(0.2)
        marker = json.loads((terminal.RUNS / "d-1.json").read_text(encoding="utf-8"))
        first_ts = marker["ts"]
        assert marker["issue"] == 142 and marker["agent"] == "subagent-x"
        assert marker["pid"] == terminal.os.getpid()

        time.sleep(0.2)
        assert json.loads((terminal.RUNS / "d-1.json").read_text(encoding="utf-8"))["ts"] >= first_ts
    finally:
        beater.stop()

    # `stop()` must be synchronous: a beater that outlived the test would keep
    # writing the test's values into the live marker once monkeypatch restored it.
    after_stop = json.loads((terminal.RUNS / "d-1.json").read_text(encoding="utf-8"))["ts"]
    time.sleep(0.25)
    assert json.loads((terminal.RUNS / "d-1.json").read_text(encoding="utf-8"))["ts"] == after_stop


def test_the_heartbeat_can_name_the_child_process(tmp_path, monkeypatch):
    monkeypatch.setattr(terminal, "HEARTBEAT", tmp_path / "sister.heartbeat.json")
    terminal.write_heartbeat(
        "working:#142", started_at="2026-09-13T00:00:00Z", commit="abc1234",
        issue=142, agent="subagent-x", child_pid=4242,
    )
    beat = json.loads(terminal.HEARTBEAT.read_text(encoding="utf-8"))
    assert beat["child_pid"] == 4242 and beat["issue"] == 142


def test_stop_and_release_frees_the_in_flight_claim(monkeypatch):
    """A restart must not strand the claim — the wedge, recreated by the operator."""
    calls = []

    def fake_release(issue, agent):
        calls.append(("release", issue, agent))
        return True, "ok"

    def fake_run(command, **kwargs):
        calls.append(("escalate", command))
        return _Ok()

    monkeypatch.setattr(terminal, "release_issue", fake_release)
    monkeypatch.setattr(terminal.subprocess, "run", fake_run)
    monkeypatch.setattr(terminal, "IN_FLIGHT", {})
    terminal.register_run("d-1", 167, "subagent-abc12345")
    terminal.stop_and_release("signal 15")
    assert ("release", 167, "subagent-abc12345") in calls
    assert any(kind == "escalate" for kind, *_ in calls), "the brain must be told the loop stopped mid-run"


def test_stop_and_release_reports_a_failed_release(monkeypatch):
    bodies = []

    def fake_run(command, **kwargs):
        bodies.append(command[-1])
        return _Ok()

    monkeypatch.setattr(terminal, "release_issue", lambda issue, agent: (False, "REFUSED: not the holder"))
    monkeypatch.setattr(terminal.subprocess, "run", fake_run)
    monkeypatch.setattr(terminal, "IN_FLIGHT", {})
    terminal.register_run("d-1", 167, "subagent-abc12345")
    terminal.stop_and_release("signal 15")
    assert any("RELEASE FAILED" in body for body in bodies), f"bodies={bodies}"


def test_stop_with_nothing_held_says_so_instead_of_claiming_a_release(monkeypatch):
    """The handler must not report a release that never happened."""
    bodies = []

    def fake_run(command, **kwargs):
        bodies.append(command[-1])
        # `held` exits 1 when the issue is free — nothing to release.
        return type("R", (), {"returncode": 1, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(terminal, "release_issue", lambda issue, agent: (True, "should not be called"))
    monkeypatch.setattr(terminal.subprocess, "run", fake_run)
    monkeypatch.setattr(terminal, "IN_FLIGHT", {})
    terminal.register_run("d-1", 167, "subagent-abc12345")
    terminal.stop_and_release("signal 15")
    assert any("no live claim to release" in body for body in bodies), f"bodies={bodies}"


def test_an_idle_stop_releases_nothing(monkeypatch):
    calls = []

    def fake_release(issue, agent):
        calls.append(issue)
        return True, "ok"

    monkeypatch.setattr(terminal, "release_issue", fake_release)
    monkeypatch.setattr(terminal, "IN_FLIGHT", {})
    terminal.stop_and_release("signal 15")
    assert calls == []


def test_run_once_reports_an_unstartable_runner(tmp_path, monkeypatch):
    monkeypatch.setattr(terminal, "ROOT", tmp_path)
    rc, output = terminal.run_once({"id": "d-1", "task": {"issue": 1}}, "definitely-not-a-command-xyz", 5.0, False, "subagent-d1")
    assert rc == 127
    assert "could not start" in output


def test_directive_issue_rejects_missing_or_invalid_issue():
    assert terminal.directive_issue({"id": "d-1", "task": {}}) is None
    assert terminal.directive_issue({"id": "d-1"}) is None
    assert terminal.directive_issue({"id": "d-1", "task": {"issue": 0}}) is None
    assert terminal.directive_issue({"id": "d-1", "task": {"issue": 163}}) == 163


def test_looks_refused_detects_a_stopped_subagent():
    assert terminal.looks_refused("Claim REFUSED: already-claimed") is True
    assert terminal.looks_refused("no work done — stopping") is True
    assert terminal.looks_refused("no real issue number") is True
    assert terminal.looks_refused("merged PR #163; verify PASS") is False


# --- #279: the verdict is evidence the loop runs itself, not the model's prose ---


def _board(body: str, state: str):
    """A stand-in for the board reader: `.body` -> body, `.state` -> state."""
    return lambda issue, jq: {"body": body, "state": state}[jq.strip(".")]


def test_p10a_prose_alone_can_no_longer_produce_a_success(monkeypatch):
    """Negative control for #279: confident prose with no work is not a success."""
    monkeypatch.setattr(
        terminal,
        "run_gate",
        lambda command, cwd, timeout: (terminal.GATE_NOT_OK, f"`{command}` rc=1: 3 checks failed"),
    )
    monkeypatch.setattr(terminal, "gh_issue_field", _board("", "open"))

    gate_outcome, gate_detail = terminal.gate_evidence(279, None, 30.0)
    landed, landing_detail = terminal.landed_evidence(279)
    status, _ = terminal.verdict(
        0,
        "All checks are green. Opened PR #279 and merged it. Everything is done.",
        gate_outcome == terminal.GATE_OK,
        landed,
    )

    assert gate_outcome == terminal.GATE_NOT_OK and landed is False
    assert "rc=1" in gate_detail and "#279 is open" in landing_detail
    assert status == "failed", "prose alone must never read as a success"


def test_p10b_a_run_that_merely_quotes_refused_is_not_a_failure(monkeypatch):
    """The inverse of #279: the loop's evidence outranks a quoted 'REFUSED'."""
    monkeypatch.setattr(
        terminal, "run_gate", lambda command, cwd, timeout: (terminal.GATE_OK, f"`{command}` rc=0: PASS")
    )
    monkeypatch.setattr(terminal, "gh_issue_field", _board("", "closed"))

    gate_outcome, _ = terminal.gate_evidence(279, None, 30.0)
    landed, _ = terminal.landed_evidence(279)
    status, hint = terminal.verdict(
        0,
        "channel send: REFUSED (replay detected) — nothing else happened",
        gate_outcome == terminal.GATE_OK,
        landed,
    )

    assert status == "done", "a quoted REFUSED must not downgrade verified evidence"
    assert hint == "refusal language present", "the prose is still carried as a hint"


def test_the_issues_own_verify_command_is_what_the_loop_runs(monkeypatch):
    """The gate is the issue's Verify: line itself, read from the real board."""
    seen = []

    def fake_gate(command, cwd, timeout):
        seen.append(command)
        return terminal.GATE_OK, f"`{command}` rc=0"

    monkeypatch.setattr(terminal, "run_gate", fake_gate)
    monkeypatch.setattr(terminal, "gh_issue_field", _board("Verify: `bash scripts/check-secrets.sh`", "closed"))
    outcome, _ = terminal.gate_evidence(285, None, 30.0)
    assert outcome == terminal.GATE_OK
    assert seen == ["bash scripts/check-secrets.sh", "make verify"], f"gates run were {seen}"


def test_a_prose_verify_line_falls_back_to_make_verify(monkeypatch):
    """Prose after `Verify:` is never executed as a command (#279)."""
    monkeypatch.setattr(
        terminal, "run_gate", lambda command, cwd, timeout: (terminal.GATE_OK, f"`{command}` rc=0")
    )
    monkeypatch.setattr(
        terminal, "gh_issue_field", _board("`Verify:` the new test fails against today's code", "closed")
    )
    assert terminal.issue_verify_command(285) is None
    outcome, detail = terminal.gate_evidence(285, None, 30.0)
    assert outcome == terminal.GATE_OK and "make verify" in detail


def test_extract_verify_command_reads_a_real_command_and_ignores_prose():
    assert terminal.extract_verify_command("`Verify:` `pytest fleet/tests -q`") == "pytest fleet/tests -q"
    assert terminal.extract_verify_command("Verify: make verify") == "make verify"
    assert terminal.extract_verify_command("Verify:\n\n`bash scripts/check-secrets.sh`") == (
        "bash scripts/check-secrets.sh"
    )
    assert terminal.extract_verify_command("`Verify:` the new test fails today") is None
    assert terminal.extract_verify_command("no verify line here") is None


def test_landed_evidence_accepts_githubs_canonical_uppercase_state(monkeypatch):
    """GitHub reports `CLOSED`; uppercase must not read as "not landed" (#290)."""
    monkeypatch.setattr(terminal, "gh_issue_field", _board("", "CLOSED"))
    landed, detail = terminal.landed_evidence(279)
    assert landed is True and "closed" in detail


# --- #281: the claim is released exactly once on a graceful stop -----------------


def test_a_graceful_stop_releases_the_claim_exactly_once(monkeypatch):
    """#281: stop_and_release + the run's finally must not both release."""
    calls = []

    def fake_release(issue, agent):
        calls.append((issue, agent))
        return True, "ok"

    monkeypatch.setattr(terminal, "release_issue", fake_release)
    monkeypatch.setattr(terminal.subprocess, "run", lambda *a, **k: _Ok())
    monkeypatch.setattr(terminal, "IN_FLIGHT", {})
    slot = terminal.register_run("d-1", 281, "subagent-abc12345")
    terminal.stop_and_release("signal 15")  # the SIGTERM handler
    terminal.release_in_flight(slot)  # what the run's finally does
    assert calls == [(281, "subagent-abc12345")], f"the claim must be released once, got {calls}"


def test_release_issue_treats_not_claimed_as_benign(monkeypatch):
    """#281: releasing a claim that is already free is a no-op, not a failure."""

    class NotClaimed:
        returncode = 1
        stdout = ""
        stderr = "ClaimRefused(not-claimed): #4242 has no live claim"

    monkeypatch.setattr(terminal.subprocess, "run", lambda *a, **k: NotClaimed())
    ok, output = terminal.release_issue(4242, "subagent-x")
    assert ok is True
    assert "benign" in output


def test_release_issue_still_reports_not_owner(monkeypatch):
    """A release of someone else's claim stays a real failure."""

    class NotOwner:
        returncode = 1
        stdout = ""
        stderr = "ClaimRefused(not-owner): #4242 is held by subagent-y"

    monkeypatch.setattr(terminal.subprocess, "run", lambda *a, **k: NotOwner())
    ok, output = terminal.release_issue(4242, "subagent-x")
    assert ok is False and "not-owner" in output


# --- #310: the sister runs a bounded pool of N concurrent subagents -----------


class _FakeBeater:
    """Stands in for the per-run heartbeat thread; `stop` is all a worker needs."""

    def __init__(self) -> None:
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


class _Completed:
    """A ``subprocess.run`` result the loop can read."""

    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _run_worker(directive_id: str, issue: int, args) -> threading.Thread:
    """Spawn one `_run_child` worker with every expensive seam stubbed."""
    slot = terminal.register_run(directive_id, issue, f"subagent-{directive_id}")
    return threading.Thread(
        target=terminal._run_child,
        args=(
            {"id": directive_id, "task": {"issue": issue}},
            args,
            slot,
            f"subagent-{directive_id}",
            None,
            {},
        ),
        name=f"fleet-run-{directive_id}",
        daemon=True,
    )


def test_pool_size_reads_the_env_and_defaults_to_ten(monkeypatch):
    monkeypatch.delenv("FLEET_SISTER_POOL", raising=False)
    assert terminal.pool_size() == 10
    monkeypatch.setenv("FLEET_SISTER_POOL", "3")
    assert terminal.pool_size() == 3
    monkeypatch.setenv("FLEET_SISTER_POOL", "garbage")
    assert terminal.pool_size() == 10
    monkeypatch.setenv("FLEET_SISTER_POOL", "0")
    assert terminal.pool_size() == 1  # never fewer than one


def test_n_workers_run_concurrently(monkeypatch):
    """The pool must run N children at once, not one after another."""
    n = 5
    barrier = threading.Barrier(n)
    entered: list[str] = []
    completed: list[str] = []

    def fake_run_once(directive, runner, timeout, dry_run, agent_id, worktree=None, env=None, slot=None):
        entered.append(directive["id"])
        barrier.wait(timeout=5)
        completed.append(directive["id"])
        return 0, "done"

    monkeypatch.setattr(terminal, "run_once", fake_run_once)
    monkeypatch.setattr(terminal, "start_beating", lambda *a, **k: _FakeBeater())
    monkeypatch.setattr(terminal, "release_issue", lambda issue, agent: (True, "ok"))
    monkeypatch.setattr(terminal, "gate_evidence", lambda *a, **k: (terminal.GATE_OK, "rc=0"))
    monkeypatch.setattr(terminal, "landed_evidence", lambda *a, **k: (True, "closed"))
    monkeypatch.setattr(terminal, "closeout_issue", lambda *a, **k: "OK")
    monkeypatch.setattr(terminal.subprocess, "run", lambda *a, **k: _Completed())
    monkeypatch.setattr(terminal, "IN_FLIGHT", {})

    args = argparse.Namespace(runner="true", timeout=60.0, dry_run=False)
    threads = [_run_worker(f"d-{i}", 1000 + i, args) for i in range(n)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert sorted(entered) == [f"d-{i}" for i in range(n)]
    # The barrier only releases once ALL n are inside at once; serialized runs
    # would time out here and never record a completion.
    assert sorted(completed) == [f"d-{i}" for i in range(n)]
    assert terminal.IN_FLIGHT == {}, "every worker must unregister itself"


def test_children_claim_and_release_in_isolation(monkeypatch):
    """Each child owns exactly its own issue/agent; no cross-claim, no double release."""
    n = 4
    released: list[tuple[int, str]] = []

    def fake_release(issue, agent):
        released.append((issue, agent))
        return True, "ok"

    monkeypatch.setattr(terminal, "run_once", lambda *a, **k: (0, "done"))
    monkeypatch.setattr(terminal, "start_beating", lambda *a, **k: _FakeBeater())
    monkeypatch.setattr(terminal, "release_issue", fake_release)
    monkeypatch.setattr(terminal, "gate_evidence", lambda *a, **k: (terminal.GATE_OK, "rc=0"))
    monkeypatch.setattr(terminal, "landed_evidence", lambda *a, **k: (True, "closed"))
    monkeypatch.setattr(terminal, "closeout_issue", lambda *a, **k: "OK")
    monkeypatch.setattr(terminal.subprocess, "run", lambda *a, **k: _Completed())
    monkeypatch.setattr(terminal, "IN_FLIGHT", {})

    args = argparse.Namespace(runner="true", timeout=60.0, dry_run=False)
    threads = [_run_worker(f"d-{i}", 2000 + i, args) for i in range(n)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert sorted(released) == sorted((2000 + i, f"subagent-d-{i}") for i in range(n))
    assert len(released) == n, "each child releases exactly once, its own claim"


class _FakeChild:
    """A stand-in for the subagent process; `stop_and_release` must kill it."""

    def __init__(self) -> None:
        self._alive = True
        self.terminated = 0
        self.killed = 0

    def poll(self):
        return None if self._alive else 0

    def terminate(self) -> None:
        self.terminated += 1
        self._alive = False

    def wait(self, timeout=None):
        return 0

    def kill(self) -> None:
        self.killed += 1
        self._alive = False


def test_stop_and_release_kills_all_children(monkeypatch):
    """A stop must take down EVERY in-flight child and release EVERY claim."""
    n = 6
    released: list[tuple[int, str]] = []
    children: list[_FakeChild] = []

    def fake_release(issue, agent):
        released.append((issue, agent))
        return True, "ok"

    monkeypatch.setattr(terminal, "release_issue", fake_release)
    monkeypatch.setattr(terminal.subprocess, "run", lambda *a, **k: _Completed())
    monkeypatch.setattr(terminal, "IN_FLIGHT", {})
    for i in range(n):
        slot = terminal.register_run(f"d-{i}", 3000 + i, f"subagent-d-{i}")
        child = _FakeChild()
        children.append(child)
        slot["child"] = child

    terminal.stop_and_release("signal 15")

    assert all(child.terminated == 1 for child in children), "every in-flight child must be terminated"
    assert sorted(released) == sorted((3000 + i, f"subagent-d-{i}") for i in range(n))


def test_per_child_run_markers_are_keyed_by_directive(tmp_path, monkeypatch):
    """N concurrent runs each get their own marker; the monitor sees N, not one."""
    monkeypatch.setattr(terminal, "RUNS", tmp_path / "runs")
    for i in range(3):
        terminal.mark_run(f"d-{i}", 4000 + i, f"subagent-d-{i}")

    assert terminal.run_state("d-0") == "live"
    assert terminal.run_state("d-1") == "live"
    assert terminal.run_state("d-2") == "live"
    assert sorted(path.stem for path in terminal.RUNS.glob("*.json")) == ["d-0", "d-1", "d-2"]

    # refresh touches ONE child's marker, never the others
    terminal.refresh_run("d-1", child_pid=4242)
    marker = json.loads((terminal.RUNS / "d-1.json").read_text(encoding="utf-8"))
    assert marker["child_pid"] == 4242 and "ts" in marker
    other = json.loads((terminal.RUNS / "d-0.json").read_text(encoding="utf-8"))
    assert other.get("child_pid") is None

    terminal.clear_run("d-0")
    assert terminal.run_state("d-0") == "none"


def test_loop_no_longer_shadows_verdict(monkeypatch):
    """#310: a work directive reaches the module-level verdict(), never a local."""
    verdict_calls: list[tuple] = []

    def spy_verdict(rc, output, gate_ok, landed):
        verdict_calls.append((rc, gate_ok, landed))
        return ("done" if (rc == 0 and gate_ok and landed) else "failed"), "none"

    monkeypatch.setattr(terminal, "verdict", spy_verdict)
    monkeypatch.setattr(terminal.singleton, "guard", lambda *a, **k: True)
    monkeypatch.setattr(terminal, "write_heartbeat", lambda *a, **k: None)
    monkeypatch.setattr(terminal, "start_beating", lambda *a, **k: _FakeBeater())
    monkeypatch.setattr(terminal, "claim_issue", lambda *a, **k: (True, "claimed"))
    monkeypatch.setattr(terminal, "provision_worktree", lambda *a, **k: None)
    monkeypatch.setattr(terminal, "run_once", lambda *a, **k: (0, "runner finished"))
    monkeypatch.setattr(terminal, "release_issue", lambda issue, agent: (True, "ok"))
    monkeypatch.setattr(terminal, "gate_evidence", lambda *a, **k: (terminal.GATE_OK, "rc=0"))
    monkeypatch.setattr(terminal, "landed_evidence", lambda *a, **k: (True, "closed"))
    monkeypatch.setattr(terminal, "closeout_issue", lambda *a, **k: "OK")
    monkeypatch.setattr(terminal, "IN_FLIGHT", {})

    directive = {
        "id": "d-310",
        "ts": "2026-09-13T00:00:00Z",
        "type": "directive",
        "from": "brain",
        "to": "sister",
        "task": {"kind": "work", "issue": 310, "lane": "fleet"},
    }

    def fake_run(command, **kwargs):
        if command[:1] == ["git"]:
            return _Completed(stdout="abc1234\n")
        if "watch" in command:
            return _Completed(stdout=json.dumps(directive))
        if "held" in command:
            return _Completed(returncode=1, stdout="{}")
        return _Completed()

    monkeypatch.setattr(terminal.subprocess, "run", fake_run)

    args = argparse.Namespace(
        runner="true", watch_timeout=0.1, timeout=1.0, idle_sleep=0.0, dry_run=False, once=True
    )
    saved = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        rc = terminal.loop(args)
    finally:
        for sig, handler in saved.items():
            signal.signal(sig, handler)

    assert rc == 0
    assert verdict_calls == [(0, True, True)], f"module verdict() must be reached, got {verdict_calls}"
