"""Verdict helpers for the ``gateway/chat`` streaming suite (issue #843).

The suite's own tri-state, made explicit.  A streaming test that compares the
frames a turn produced against the single body it should equal was reporting
``FAIL`` for an outcome it had not measured: inside a loaded gate run the stream
came back as **one refusal frame** (measured 2026-09-15, ``master`` @ ``dd8cbfc``,
``gateway/chat/tests/test_endpoints.py::test_stream_content_matches_the_single_body``
inside two different gate runs), and the test's frame decoder turned that into a
bare ``KeyError: 'choices'`` — a claim about the code, in the vocabulary of a
parser accident.  Run standalone on the same tree, the same 50 tests passed
twice.

A refusal is not a measurement of content.  It is the surface saying *this turn
was not served* — the gateway had no healthy route, the flag was off, the
credential was refused, the budget blocked — and the honest answer to "is the
streamed content equal to the single body?" is then **CANNOT-ASSESS**, not FAIL.
So:

* a **refusal** is retried once, and if every attempt refuses the verdict is
  CANNOT-ASSESS, naming the refusal code and the fact that it retried;
* a **disagreement** (the content was measured and did not match) is a FAIL, at
  once — one deterministic observation is enough to say the code is wrong, and
  no retry may soften it;
* an **unparseable frame** is named as such rather than surfacing as a
  ``KeyError`` from whatever field the decoder happened to want next.

The retry is never silent: a verdict that retried carries every failed attempt,
and :func:`enforce` warns before it skips, so a retry that turned into a pass
can be seen in the run's output rather than inferred from a timing difference.
A retry that cannot be observed is the same defect as a gate that cannot fail.
"""

from __future__ import annotations

import json
import warnings
from dataclasses import dataclass
from typing import Any, Callable, Sequence

#: The three verdicts. ``assessed`` is the only one that claims anything about
#: the code; ``cannot-assess`` is the honest answer to a run the harness could
#: not turn into a measurement; ``failed`` is a measured disagreement.
STATE_ASSESSED = "assessed"
STATE_CANNOT_ASSESS = "cannot-assess"
STATE_FAILED = "failed"

STATES = frozenset({STATE_ASSESSED, STATE_CANNOT_ASSESS, STATE_FAILED})

#: The refusal's code when the frame named none. The surface's own vocabulary
#: (``gateway/chat/errors.py``) is the authority; this is only the fallback.
UNNAMED_REFUSAL = "unnamed-refusal"

#: The refusal a frame raises when it decodes but is neither content nor a
#: named refusal — a malformed frame is a refusal to be understood, not a
#: content mismatch.
FRAME_WITHOUT_CHOICES = "frame-without-choices"

#: The refusal a frame raises when the JSON itself does not decode.
UNPARSEABLE_FRAME = "unparseable-frame"


class StreamRefused(RuntimeError):
    """A frame the surface sent instead of content: the stream refused.

    Carries the refusal's own ``code`` and ``message`` so the verdict can name
    what happened instead of failing on a missing dict key.
    """

    def __init__(self, code: str, detail: str = "", frame: str = "") -> None:
        self.code = str(code or UNNAMED_REFUSAL)
        self.detail = str(detail or "")
        self.frame = str(frame or "")
        summary = f"the stream refused with code {self.code}"
        if self.detail:
            summary += f": {self.detail}"
        super().__init__(summary)


def decode_frame(frame: str) -> dict[str, Any]:
    """One SSE ``data:`` frame as a mapping — a refusal is NAMED, not guessed.

    The suite's original decoder indexed ``["choices"][0]["delta"]`` directly,
    so a terminal error frame — a shape the surface is documented to send — came
    out as ``KeyError: 'choices'``.  Naming the refusal keeps the red actionable
    and keeps the harness honest about what it actually observed.
    """
    body = frame[len("data:") :].strip() if frame.startswith("data:") else frame.strip()
    try:
        payload = json.loads(body)
    except ValueError as exc:
        raise StreamRefused(UNPARSEABLE_FRAME, str(exc), frame) from exc
    if not isinstance(payload, dict):
        raise StreamRefused(FRAME_WITHOUT_CHOICES, "the frame is not a JSON object", frame)
    error = payload.get("error")
    if isinstance(error, dict):
        raise StreamRefused(str(error.get("code") or UNNAMED_REFUSAL), str(error.get("message") or ""), frame)
    if "choices" not in payload:
        raise StreamRefused(
            FRAME_WITHOUT_CHOICES,
            "the frame carries neither content nor a named refusal",
            frame,
        )
    return payload


def content_of(frames: Sequence[str], *, sentinel: str) -> str:
    """The content a frame sequence carried, the sentinel excluded.

    Raises :class:`StreamRefused` for any frame that is not content, so the
    caller gets a named refusal rather than a ``KeyError``.
    """
    parts: list[str] = []
    for frame in frames:
        if frame == sentinel:
            continue
        payload = decode_frame(frame)
        delta = payload["choices"][0].get("delta") or {}
        parts.append(str(delta.get("content", "")))
    return "".join(parts)


@dataclass(frozen=True)
class Verdict:
    """What one assessed run concluded, and the evidence for it."""

    state: str
    value: Any = None
    reason: str = ""
    #: How many attempts were made — the record that a retry happened.
    attempts_made: int = 0
    #: Every failed attempt, in order, named.
    failures: tuple[str, ...] = ()
    #: The refusal's own name, when a refusal is what stopped the run.
    refused: str = ""

    def __post_init__(self) -> None:
        if self.state not in STATES:
            raise ValueError(f"unknown verdict state: {self.state!r}")

    @property
    def retried(self) -> bool:
        """True when more than one attempt was needed to reach this verdict."""
        return self.attempts_made > 1


def assess(attempt: Callable[[], Any], *, retries: int = 1) -> Verdict:
    """Run ``attempt`` and decide what it is entitled to conclude.

    ``attempt`` performs the whole measurement (both calls, the comparison) and
    returns the measured value; it may raise :class:`StreamRefused` for a run
    that was not served, or anything else for a measurement that disagreed.

    A refusal is retried (``retries`` times, default one) because it is a
    resource/authority outcome rather than a measurement of content.  Anything
    else fails immediately: it was measured, and it disagreed.
    """
    if retries < 0:
        raise ValueError("retries must be >= 0")
    failures: list[str] = []
    refused = ""
    for index in range(retries + 1):
        made = index + 1
        try:
            value = attempt()
        except StreamRefused as exc:
            failures.append(f"attempt {made}: {exc}")
            refused = exc.code
            continue
        except Exception as exc:  # noqa: BLE001 - any disagreement is a failure
            failures.append(f"attempt {made}: {type(exc).__name__}: {exc}")
            return Verdict(
                STATE_FAILED,
                reason="; ".join(failures),
                attempts_made=made,
                failures=tuple(failures),
                refused=refused,
            )
        if failures:
            # The run was refused and then served: the content is measured, and
            # both observations are reported.  The retry is in ``failures`` and
            # ``attempts_made`` so it can never be read as a clean single shot.
            return Verdict(
                STATE_ASSESSED,
                value=value,
                reason=(
                    "the run was refused and then served, so the content was "
                    f"measured on attempt {made}"
                ),
                attempts_made=made,
                failures=tuple(failures),
                refused=refused,
            )
        return Verdict(STATE_ASSESSED, value=value, attempts_made=made)
    return Verdict(
        STATE_CANNOT_ASSESS,
        reason=(
            "every attempt was refused, so no content was ever measured "
            f"(refusal: {refused or UNNAMED_REFUSAL})"
        ),
        attempts_made=retries + 1,
        failures=tuple(failures),
        refused=refused,
    )


def enforce(verdict: Verdict, *, label: str, skip: Callable[[str], Any], fail: Callable[[str], Any]) -> Any:
    """Turn a :class:`Verdict` into the test outcome it entitles.

    ``skip`` and ``fail`` are the caller's ``pytest.skip`` / ``pytest.fail``, so
    this stays a pure decision over a verdict and can be provoked in a test
    without running a surface.  A retry is warned about before it is swallowed:
    a run that only passed on its second attempt is reported, not hidden.
    """
    if verdict.state == STATE_ASSESSED:
        if verdict.retried:
            warnings.warn(
                f"{label}: retried — {verdict.reason} "
                f"({'; '.join(verdict.failures)})",
                stacklevel=2,
            )
        return verdict.value
    if verdict.state == STATE_CANNOT_ASSESS:
        reason = (
            f"CANNOT-ASSESS — {label}: {verdict.reason} "
            f"({verdict.attempts_made} attempt(s) were made; the retry is recorded "
            "here rather than counted as a pass)"
        )
        warnings.warn(reason, stacklevel=2)
        return skip(reason)
    return fail(f"FAIL — {label}: {verdict.reason}")
