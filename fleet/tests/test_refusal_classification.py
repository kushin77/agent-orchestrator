"""A refusal that is terminal BY DEFINITION is classified, not merely bounded (#861).

The defect these tests exist for was measured, not imagined. #723 bounds a failure:
it counts the attempt, spaces the next one with the harvested backoff and retires
the order at K. But bounding is not classifying — it treats every refusal as worth
retrying, so a refusal that can never become retryable pays the whole budget first.
At the documented defaults (``AO_RUNAWAY_ATTEMPTS=5``, base 30s, cap 300s) that is
30+60+120+240 ≈ **450s of a held queue slot** spent re-reading an order whose issue
is closed on the board of record. The terminal state was reached; it was the slow
road to work that was dead on arrival.

So the two halves of the contract are asserted here, and each is a probe of its own:

* a refusal for a **closed issue / closed epic / unit no lane owns** retires the
  order on the FIRST refusal, and the record names WHY — the classification, not
  just a count;
* every other refusal keeps the bounded retry it had, because for those the passage
  of time IS the remedy (a stale snapshot is #727's whole subject). Asserting only
  the first half would pass a guard that dead-lettered *everything*, which is not a
  classification at all — which is why the second half is here;
* an **unclassifiable** refusal is retried. Not knowing why a claim was refused is
  never a licence to discard the order;
* the classification's KEYS are the claim layer's own reason codes, checked against
  the real module rather than restated, so the two vocabularies cannot drift apart;
* the brain mints no second authorisation for a unit a live claim already holds
  (acceptance item 3 of #366), and an EXPIRED claim does not block it — the guard
  must not become the wedge it exists to prevent.

Isolation: every test points ``terminal.RUNS`` and ``channel.INBOX`` at a tmp
directory (the seam ``test_dead_letter.py`` and ``test_runaway_guard.py`` use), so
no test writes into the live fleet's ``.fleet/``.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

FLEET_DIR = Path(__file__).resolve().parents[1]
if str(FLEET_DIR) not in sys.path:
    sys.path.insert(0, str(FLEET_DIR))

import channel  # noqa: E402
import runaway  # noqa: E402
import terminal  # noqa: E402

import brain  # noqa: E402

#: The real event type, taken from the layer that WRITES the ledger rather than
#: re-imported from `model` (importing `model` here would put a second flat module
#: named `model` on the path for the whole fleet suite).
ClaimEvent = brain.claims.ClaimEvent

#: A record always wins over a stale .pyc (measured elsewhere in this repo).
sys.dont_write_bytecode = True

#: The claim layer's own refusal line, spelled the way ``dispatch/cli.py`` prints it.
#: Built from the code's shape rather than a paraphrase, so a test cannot pass on a
#: friendlier wording than the loop will actually read.
REFUSED = "claim REFUSED: {reason} \u2014 {detail}"


def refusal(reason: str, detail: str = "#42 is closed \u2014 evidence: board") -> str:
    """One refusal exactly as the claim CLI prints it (stdout+stderr, as the loop gets it)."""
    return REFUSED.format(reason=reason, detail=detail)


def wire_scratch(tmp_path: Path, monkeypatch) -> Path:
    """Point the loop's guard and the mailbox at ONE scratch fleet directory."""
    monkeypatch.setattr(terminal, "RUNS", tmp_path / "runs")
    monkeypatch.setattr(channel, "INBOX", tmp_path / "inbox")
    monkeypatch.setattr(channel, "DONE", tmp_path / "done")
    monkeypatch.setattr(terminal, "stream_run_event", lambda *a, **k: None)
    return tmp_path


def record_reporting(calls: list[dict]):
    """A ``report_once`` stand-in that records instead of shelling out to channel."""

    def report_once(directive_id, key, message_type, body, severity="warn"):
        calls.append(
            {"directive": directive_id, "key": key, "type": message_type, "severity": severity}
        )
        return True

    return report_once


def plant(directory: Path, directive_id: str, *, issue: int) -> Path:
    """Put one pending directive envelope in `directory` (the mailbox shape)."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{directive_id}.json"
    path.write_text(
        json.dumps(
            {
                "id": directive_id,
                "ts": "2026-09-16T00:00:00Z",
                "type": "directive",
                "from": "brain",
                "to": "sister",
                "correlation_id": f"c-{directive_id}",
                "model": {"tier": "flash", "thinking": "none"},
                "task": {"kind": "work", "issue": issue, "lane": "fleet"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return path


# --- the classification table ------------------------------------------------


def test_the_terminal_classes_are_exactly_the_refusals_that_cannot_wait():
    """The declared table, in both directions.

    The four terminal reasons are the issue's own list (closed issue, closed epic,
    unowned unit) plus the epic-is-not-a-unit case, which the board will never stop
    saying. Everything else is OUT — and the OUT half is the load-bearing one: a
    table that grew to cover `snapshot-stale` would retire the very orders #727's
    bounded refresh exists to rescue.
    """
    assert runaway.TERMINAL_REFUSAL_CLASSES == {
        "issue-closed": "issue-closed",
        "epic-closed": "epic-closed",
        "epic-not-workable": "not-a-unit",
        "unowned": "unowned-unit",
    }
    for terminal_reason in runaway.TERMINAL_REFUSAL_CLASSES:
        assert runaway.classify_refusal(terminal_reason) is not None
    for retryable in (
        "snapshot-stale",
        "blocked",
        "unknown-issue",
        "already-claimed",
        "provenance-mismatch",
        "invalid-directive",
        "not-claimed",
        "not-owner",
    ):
        assert runaway.classify_refusal(retryable) is None, (
            f"{retryable!r} must keep the bounded retry — waiting is its remedy"
        )
    assert runaway.classify_refusal(None) is None
    assert runaway.classify_refusal("") is None


def test_the_classification_keys_are_the_claim_layers_own_reason_codes():
    """The anti-drift control: the keys are READ from the module that raises them.

    A second vocabulary restated here would be free to disagree with the layer that
    actually refuses. This asserts against the real ``claims`` module (which imports
    its reasons from ``model``), so a renamed code fails here rather than silently
    switching the classification off.
    """
    declared = {
        name: value for name, value in vars(brain.claims).items()
        if name.startswith("REASON_") and isinstance(value, str)
    }
    assert declared, "the claim layer's REASON_* vocabulary could not be read at all"
    missing = sorted(set(runaway.TERMINAL_REFUSAL_CLASSES) - set(declared.values()))
    assert not missing, (
        f"the classification names reason code(s) the claim layer does not raise: {missing} "
        f"(the layer's vocabulary is {sorted(declared.values())})"
    )


def test_a_structured_refusal_line_is_read_rather_than_scanned():
    """The reason is parsed from the CLI's own line; prose is not classified.

    A detail that merely *mentions* a terminal word must not classify the refusal —
    that is the difference between reading a refusal and pattern-matching an essay.
    """
    text = refusal("snapshot-stale", "#42's epic is closed and no lane owns it")
    assert runaway.refusal_reason(text) == "snapshot-stale"
    assert runaway.classify_refusal(runaway.refusal_reason(text)) is None


def test_an_unparseable_refusal_is_never_classified_as_terminal():
    """Not knowing why a claim was refused is not a licence to discard the order."""
    for text in ("", "nothing to consume", "claim accepted: #42 as subagent"):
        reason = runaway.refusal_reason(text)
        assert reason is None, f"{text!r} yielded {reason!r} — it names no refusal"
        assert runaway.classify_refusal(reason) is None


# --- half 1: the first refusal retires ---------------------------------------


@pytest.mark.parametrize(
    "reason,expected",
    [
        ("issue-closed", "issue-closed"),
        ("epic-closed", "epic-closed"),
        ("epic-not-workable", "not-a-unit"),
        ("unowned", "unowned-unit"),
    ],
)
def test_a_terminal_refusal_dead_letters_on_the_first_refusal(
    tmp_path, monkeypatch, reason, expected
):
    """One refusal, one attempt, one terminal record — carrying the classification."""
    wire_scratch(tmp_path, monkeypatch)
    calls: list[dict] = []
    monkeypatch.setattr(terminal, "report_once", record_reporting(calls))
    plant(channel.INBOX, "d-terminal", issue=42)

    verdict = terminal.guard_retire_refusal("d-terminal", 42, refusal(reason))

    assert verdict is True, "a terminal-by-definition refusal must retire the order"
    assert runaway.dead_lettered("d-terminal", base=terminal.guard_base())
    assert not (channel.INBOX / "d-terminal.json").exists(), (
        "the order is still in the inbox — it can be returned again"
    )
    artifact = runaway.record_shape(terminal.guard_base(), "d-terminal")
    assert artifact["terminal_class"] == expected, artifact
    assert artifact["issue"] == 42, artifact
    assert artifact["attempts"] == 1, (
        f"a terminal refusal must cost ONE attempt, not the cap: {artifact}"
    )
    assert artifact["dropped_by"] == "runaway-guard"
    assert expected in str(artifact["reason"]), artifact
    # The escalation names the classification, so the operator reads why it was
    # terminal rather than only how many times it was tried.
    assert calls and calls[0]["type"] == "escalate" and calls[0]["severity"] == "critical"
    assert expected in calls[0]["key"], calls[0]


def test_a_retryable_refusal_still_gets_the_bounded_retry(tmp_path, monkeypatch):
    """The mutation control for the test above.

    Without this, a ``guard_retire_refusal`` that dead-lettered everything would
    satisfy it — and would retire the orders a stale snapshot was about to make
    dispatchable (#727). The order must be counted, held, and still in the inbox.
    """
    wire_scratch(tmp_path, monkeypatch)
    monkeypatch.setattr(terminal, "report_once", record_reporting([]))
    plant(channel.INBOX, "d-retryable", issue=42)

    verdict = terminal.guard_retire_refusal("d-retryable", 42, refusal("snapshot-stale"))

    assert verdict is False, "a stale snapshot is retryable — the refresh is its remedy"
    assert not runaway.dead_lettered("d-retryable", base=terminal.guard_base())
    assert (channel.INBOX / "d-retryable.json").exists(), "the order must stay dispatchable"
    record = runaway.load("d-retryable", base=terminal.guard_base())
    assert record is not None and record.attempts == 1 and not record.exhausted
    assert record.next_attempt_at is not None, "the retry must be spaced, not spun"


def test_an_unclassifiable_refusal_is_retried_rather_than_retired(tmp_path, monkeypatch):
    """An unknown refusal is not evidence of terminality."""
    wire_scratch(tmp_path, monkeypatch)
    monkeypatch.setattr(terminal, "report_once", record_reporting([]))
    plant(channel.INBOX, "d-unknown", issue=42)

    verdict = terminal.guard_retire_refusal("d-unknown", 42, "the runner printed nothing at all")

    assert verdict is False
    assert not runaway.dead_lettered("d-unknown", base=terminal.guard_base())
    assert (channel.INBOX / "d-unknown.json").exists()


def test_a_terminal_refusal_retires_even_when_the_budget_is_misconfigured(
    tmp_path, monkeypatch
):
    """The classification is the decision; the budget is not a precondition for it.

    A typo'd ``AO_RUNAWAY_ATTEMPTS`` makes the guard refuse to *count*. It must not
    therefore leave an order for a closed issue in the queue — the order cannot be
    executed whether or not anybody can read the cap.
    """
    wire_scratch(tmp_path, monkeypatch)
    monkeypatch.setenv(runaway.ENV_ATTEMPT_CAP, "zero")
    monkeypatch.setattr(terminal, "report_once", record_reporting([]))
    plant(channel.INBOX, "d-misconfigured", issue=42)

    verdict = terminal.guard_retire_refusal("d-misconfigured", 42, refusal("issue-closed"))

    assert verdict is True
    assert runaway.dead_lettered("d-misconfigured", base=terminal.guard_base())
    assert runaway.record_shape(terminal.guard_base(), "d-misconfigured")["terminal_class"] == (
        "issue-closed"
    )


def test_the_ordinary_retirement_carries_no_classification(tmp_path, monkeypatch):
    """A budget exhaustion is not a classification, and the record says so by carrying null.

    The two callers share ONE record shape (#754); the new field must be present and
    empty for the old path rather than absent, or a reader would have to guess which
    shape it was looking at.
    """
    wire_scratch(tmp_path, monkeypatch)
    monkeypatch.setenv(runaway.ENV_ATTEMPT_CAP, "1")
    monkeypatch.setattr(terminal, "report_once", record_reporting([]))
    plant(channel.INBOX, "d-exhausted", issue=42)

    assert terminal.guard_retire("d-exhausted", 42, "run did not land: failed (rc=1)") is True

    artifact = runaway.record_shape(terminal.guard_base(), "d-exhausted")
    assert set(artifact) == set(runaway.RECORD_FIELDS), artifact
    assert artifact["terminal_class"] is None, artifact
    assert artifact["attempts"] == 1


# --- acceptance item 3 of #366: no second order for a claimed unit ------------


def ledger_with(directory: Path, *events: ClaimEvent) -> Path:
    """Write real claim events the way the dispatch layer writes them."""
    for event in events:
        brain.claims.append_event(event, directory)
    return directory


def claim(issue: int, agent: str = "subagent-861", hours_ago: float = 0.0) -> ClaimEvent:
    """A claim event, stamped ``hours_ago`` so liveness can be driven from the TTL."""
    at = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return ClaimEvent(
        event="claim",
        issue=issue,
        agent=agent,
        at=at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        lane="fleet",
        reason="active-epic-child",
    )


def test_a_live_claim_is_a_refusal_naming_the_holder(tmp_path):
    """The guard's own case: a unit another lane holds gets no second authorisation."""
    ledger = ledger_with(tmp_path / "claims", claim(861, "subagent-861"))

    refusal_text = brain.live_claim_refusal(861, ledger)

    assert refusal_text is not None, "a live claim must refuse a second directive"
    assert brain.ISSUE_CLAIMED in refusal_text
    assert "#861" in refusal_text and "subagent-861" in refusal_text, refusal_text
    assert "no sent-marker" in refusal_text, "the refusal must say nothing was written"


def test_an_unclaimed_unit_is_not_refused(tmp_path):
    """The mutation control: the guard is ledger-driven, not a blanket refusal."""
    ledger = ledger_with(tmp_path / "claims", claim(861))

    assert brain.live_claim_refusal(999, ledger) is None


def test_an_expired_claim_does_not_block_a_re_dispatch(tmp_path):
    """The TTL is what stops a dead agent wedging the unit; the guard must honour it.

    A guard that refused on every surviving ledger record would re-create the wedge
    it exists to prevent — so an expired claim is not a live claim, exactly as
    ``claims.active_claims`` defines it.
    """
    ledger = ledger_with(tmp_path / "claims", claim(861, hours_ago=99))

    assert brain.live_claim_refusal(861, ledger) is None


def test_a_released_claim_does_not_block_a_re_dispatch(tmp_path):
    """A release clears the unit in the replay, so the next order is not refused."""
    directory = ledger_with(tmp_path / "claims", claim(861))
    brain.claims.append_event(
        ClaimEvent(
            event="release",
            issue=861,
            agent="subagent-861",
            at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            lane="fleet",
        ),
        directory,
    )

    assert brain.live_claim_refusal(861, directory) is None


def test_an_unreadable_ledger_is_a_refusal_never_an_allow(tmp_path):
    """No ledger, no verdict: the brain does not mint a second order it cannot check."""
    unreadable = tmp_path / "claims-as-a-file"
    unreadable.write_text("not a directory", encoding="utf-8")

    refusal_text = brain.live_claim_refusal(861, unreadable)

    assert refusal_text is not None
    assert brain.BOARD_UNREADABLE in refusal_text, refusal_text


# --- the guard where it runs: dispatch(), the one funnel ----------------------


def write_board(path: Path, *entries: tuple[int, str]) -> Path:
    """A committed-board-shaped snapshot (the shape ``test_issue_closure.py`` uses)."""
    path.write_text(
        json.dumps(
            {
                "generated_at": "2026-09-14T00:00:00Z",
                "source": "kushin77/agent-orchestrator",
                "issues": [
                    {"number": number, "title": f"issue {number}", "state": state}
                    for number, state in entries
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


@pytest.fixture(autouse=True)
def _redirect_dispatch_markers(tmp_path, monkeypatch):
    """Keep sent-markers out of the live `.fleet/` tree (the cover the #693 suite uses)."""
    monkeypatch.setattr(brain, "DISPATCH_MARKERS", tmp_path / "brain" / "dispatched")


@pytest.fixture
def recorded_channel(tmp_path, monkeypatch):
    """A `fleet/channel.py` stand-in that records every send and exits 0."""

    def install() -> Path:
        log = tmp_path / "sent.jsonl"
        script = tmp_path / "recording-channel.py"
        script.write_text(
            "import pathlib, sys\n"
            f"pathlib.Path({str(log)!r}).open('a', encoding='utf-8').write(sys.argv[-1] + '\\n')\n"
            "print('channel send: OK \u2014 queued for the sister')\n"
            "sys.exit(0)\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(brain, "CHANNEL", str(script))
        return log

    return install


def workload(number: int, reference: str = "ao-861-claim-guard") -> dict:
    """An operator work order naming one issue."""
    return {
        "from": "operator",
        "to": "brain",
        "type": "directive",
        "id": reference,
        "task": {"issue": number, "lane": "fleet"},
        "body": f"dispatch one subagent for issue #{number}",
    }


def test_dispatch_refuses_a_claimed_unit_and_sends_nothing(
    tmp_path, monkeypatch, recorded_channel
):
    """The guard sits in the funnel every directive passes through, so every caller is covered."""
    log = recorded_channel()
    board = write_board(tmp_path / "snapshot.json", (861, "OPEN"))
    monkeypatch.setattr(brain, "BOARD_PATH", board)
    monkeypatch.setattr(brain, "CLAIMS_LEDGER", ledger_with(tmp_path / "claims", claim(861)))

    ok, report = brain.handle_order(workload(861))

    assert ok is False, f"a second authorisation for a claimed unit must be refused: {report}"
    assert brain.ISSUE_CLAIMED in report and "#861" in report and "subagent-861" in report, report
    assert not log.exists(), f"the channel was invoked for a claimed unit: {log.read_text()}"
    marker = brain.order_marker(workload(861))
    assert marker is not None and not marker.exists(), "a refused order must leave no sent-marker"


def test_dispatch_still_sends_when_no_claim_holds_the_unit(
    tmp_path, monkeypatch, recorded_channel
):
    """The mutation control: an open, unclaimed issue still dispatches.

    Without this, a guard that refused every order would satisfy the test above.
    """
    log = recorded_channel()
    board = write_board(tmp_path / "snapshot.json", (861, "OPEN"))
    monkeypatch.setattr(brain, "BOARD_PATH", board)
    monkeypatch.setattr(brain, "CLAIMS_LEDGER", ledger_with(tmp_path / "claims", claim(1)))

    ok, report = brain.handle_order(workload(861))

    assert ok is True and "dispatched #861" in report, report
    sent = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert [message["task"]["issue"] for message in sent] == [861], sent


def test_the_board_guard_is_asked_first_and_still_refuses_a_closed_issue(
    tmp_path, monkeypatch, recorded_channel
):
    """Both guards run in one funnel; the board's refusal is not weakened by the ledger's.

    An order for a CLOSED issue is refused by the closure guard before the claim
    guard is reached, so the #693 behaviour is preserved and the new one is additive.
    """
    log = recorded_channel()
    board = write_board(tmp_path / "snapshot.json", (861, "CLOSED"))
    monkeypatch.setattr(brain, "BOARD_PATH", board)
    monkeypatch.setattr(brain, "CLAIMS_LEDGER", ledger_with(tmp_path / "claims", claim(861)))

    ok, report = brain.handle_order(workload(861))

    assert ok is False
    assert brain.ISSUE_CLOSED in report, report
    assert not log.exists()
