"""The streaming verdict harness is provoked, the unmeasured case included.

``chat_stream_probe`` decides whether a streaming test may claim the code is
wrong, so both halves are provoked here rather than assumed (issue #843):

* a run that was **never served** is retried once and, if every attempt refuses,
  reported CANNOT-ASSESS — never FAIL;
* a **measured disagreement** fails at once and no retry softens it, so a
  genuinely broken control still fails;
* a frame that is a refusal is **named**, instead of surfacing as the
  ``KeyError: 'choices'`` that two gate runs reported as a red.

A harness that cannot be shown to fail is a formality, so the FAIL path is
asserted here as explicitly as the CANNOT-ASSESS path.
"""

from __future__ import annotations

import json

import pytest

from chat_stream_probe import (
    FRAME_WITHOUT_CHOICES,
    STATE_ASSESSED,
    STATE_CANNOT_ASSESS,
    STATE_FAILED,
    StreamRefused,
    UNPARSEABLE_FRAME,
    assess,
    content_of,
    decode_frame,
    enforce,
)

#: The sentinel the surface terminates a stream with (``gateway.chat.contract``
#: pins the same literal; a literal here keeps this file importable alone).
SENTINEL = "data: [DONE]\n\n"


class _Skipped(Exception):
    """Stands in for ``pytest.skip`` so ``enforce`` is exercised directly."""


class _Failed(Exception):
    """Stands in for ``pytest.fail`` so ``enforce`` is exercised directly."""


def _skip(reason: str):
    raise _Skipped(reason)


def _fail(reason: str):
    raise _Failed(reason)


def refusal(code: str = "upstream_failed", detail: str = "no healthy route"):
    return StreamRefused(code, detail)


def content_frame(text: str) -> str:
    return "data: " + json.dumps({"choices": [{"delta": {"content": text}}]}) + "\n\n"


def error_frame(code: str = "budget_blocked", message: str = "the turn was refused") -> str:
    return "data: " + json.dumps({"error": {"code": code, "message": message}}) + "\n\n"


# --------------------------------------------------------------------------- #
# The served case: one clean attempt is an assessment
# --------------------------------------------------------------------------- #
def test_a_served_run_is_assessed_and_records_no_retry():
    calls: list[int] = []

    def attempt() -> str:
        calls.append(1)
        return "the answer"

    verdict = assess(attempt)
    assert verdict.state == STATE_ASSESSED
    assert verdict.value == "the answer"
    assert verdict.attempts_made == 1
    assert verdict.failures == ()
    assert verdict.retried is False
    assert calls == [1]


# --------------------------------------------------------------------------- #
# The resource case: a refusal cannot become a claim about the code
# --------------------------------------------------------------------------- #
def test_a_refusal_then_content_is_assessed_with_the_retry_recorded():
    calls: list[int] = []

    def attempt() -> str:
        calls.append(1)
        if len(calls) == 1:
            raise refusal()
        return "measured now"

    verdict = assess(attempt)
    assert verdict.state == STATE_ASSESSED
    assert verdict.value == "measured now"
    assert verdict.retried is True and verdict.attempts_made == 2
    assert verdict.refused == "upstream_failed"
    assert verdict.failures and "attempt 1" in verdict.failures[0]
    assert calls == [1, 1]


def test_a_refusal_on_every_attempt_is_cannot_assess_never_a_failure():
    calls: list[int] = []

    def attempt() -> str:
        calls.append(1)
        raise refusal("credential_required", "no chat credential was presented")

    verdict = assess(attempt)
    assert verdict.state == STATE_CANNOT_ASSESS
    assert verdict.retried is True and verdict.attempts_made == 2
    assert verdict.refused == "credential_required"
    assert "credential_required" in verdict.reason
    # the retry was exhausted, and the exhaustion is visible rather than implied
    assert len(verdict.failures) == 2
    assert calls == [1, 1]


def test_the_retry_budget_is_one_and_is_honoured():
    calls: list[int] = []

    def attempt() -> str:
        calls.append(1)
        raise refusal()

    verdict = assess(attempt)
    assert calls == [1, 1], "the harness retried more than once"
    assert verdict.attempts_made == 2


def test_enforce_skips_a_cannot_assess_verdict_and_warns_about_the_retry():
    def attempt() -> str:
        raise refusal("upstream_failed")

    verdict = assess(attempt)
    with pytest.warns(UserWarning, match="CANNOT-ASSESS"):
        with pytest.raises(_Skipped) as skipped:
            enforce(verdict, label="stream content", skip=_skip, fail=_fail)
    assert "upstream_failed" in str(skipped.value)
    assert "2 attempt(s)" in str(skipped.value)


# --------------------------------------------------------------------------- #
# The correctness case: a measured disagreement still fails
# --------------------------------------------------------------------------- #
def test_a_content_mismatch_fails_at_once_and_is_never_softened():
    calls: list[int] = []

    def attempt() -> str:
        calls.append(1)
        raise AssertionError("the streamed deltas do not spell the single body")

    verdict = assess(attempt)
    assert verdict.state == STATE_FAILED
    assert "AssertionError" in verdict.reason
    assert calls == [1], "a measured disagreement must not be retried"
    with pytest.raises(_Failed) as failed:
        enforce(verdict, label="stream content", skip=_skip, fail=_fail)
    assert "FAIL" in str(failed.value)


def test_a_mismatch_after_a_refusal_is_still_a_failure():
    calls: list[int] = []

    def attempt() -> str:
        calls.append(1)
        if len(calls) == 1:
            raise refusal()
        raise AssertionError("the streamed deltas do not spell the single body")

    verdict = assess(attempt)
    assert verdict.state == STATE_FAILED
    assert "AssertionError" in verdict.reason
    assert verdict.refused == "upstream_failed"
    assert "attempt 1" in verdict.reason and "attempt 2" in verdict.reason


def test_a_broken_stream_is_not_rescued_by_the_retry():
    """The half that must not be lost: a deterministic break fails both times."""

    calls: list[int] = []

    def attempt() -> str:
        calls.append(1)
        measured = content_of(
            [content_frame("truncated"), SENTINEL], sentinel=SENTINEL
        )
        assert measured == "the whole answer", f"measured {measured!r}"

    verdict = assess(attempt)
    assert verdict.state == STATE_FAILED
    assert calls == [1], "a measured disagreement must not be retried"
    assert "truncated" in verdict.reason


# --------------------------------------------------------------------------- #
# The frames themselves: a refusal is named, not a missing key
# --------------------------------------------------------------------------- #
def test_a_refusal_frame_is_named_instead_of_raising_a_missing_key():
    with pytest.raises(StreamRefused) as raised:
        decode_frame(error_frame("budget_blocked"))
    assert raised.value.code == "budget_blocked"
    assert "budget_blocked" in str(raised.value)


def test_the_original_failure_shape_now_names_the_refusal():
    """The measured red was ``KeyError: 'choices'`` on this exact frame."""
    frames = [error_frame("upstream_failed"), SENTINEL]
    with pytest.raises(StreamRefused) as raised:
        content_of(frames, sentinel=SENTINEL)
    assert raised.value.code == "upstream_failed"
    assert not isinstance(raised.value, KeyError)


def test_a_frame_that_is_neither_content_nor_refusal_is_named():
    with pytest.raises(StreamRefused) as raised:
        decode_frame('data: {"object": "chat.completion.chunk"}\n\n')
    assert raised.value.code == FRAME_WITHOUT_CHOICES


def test_an_unparseable_frame_is_named():
    with pytest.raises(StreamRefused) as raised:
        decode_frame("data: not-json\n\n")
    assert raised.value.code == UNPARSEABLE_FRAME


def test_content_of_sums_the_deltas_and_ignores_the_sentinel():
    frames = [content_frame("the "), content_frame("answer"), SENTINEL]
    assert content_of(frames, sentinel=SENTINEL) == "the answer"
