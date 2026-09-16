"""Retry/backoff policy for the conversion-event bridge (issue #671).

Only *transient* failures are retried — a downstream posting call that raises
something other than :class:`~.model.Refused` (a network error, a timeout: the
kind of failure that might succeed a moment later). A :class:`~.model.Refused`
is never retried: it means the event itself is unusable (bad schema, bad auth,
an unbalanced derivation), and retrying an unusable event just delays the
quarantine that was always going to happen. That split is what keeps retrying
from becoming a way to paper over a malformed event.

The policy is deliberately pure: :meth:`BackoffPolicy.delay` is a function of
the attempt number, not a sleeping loop, so the bridge and its tests can drive
it without a real clock.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class BackoffPolicy:
    """Exponential backoff with a ceiling: ``base * 2**attempt``, capped."""

    base_seconds: float = 0.5
    max_seconds: float = 30.0
    max_attempts: int = 5

    def delay(self, attempt: int) -> float:
        """The delay before ``attempt`` (0-indexed: 0 is the first retry)."""
        if attempt < 0:
            raise ValueError("attempt must be >= 0")
        return min(self.base_seconds * (2 ** attempt), self.max_seconds)


#: The bridge's default policy. Named so a caller can override it without
#: reaching into :mod:`bridge`.
DEFAULT_POLICY = BackoffPolicy()


def run_with_retry(
    fn: Callable[[], T],
    *,
    policy: BackoffPolicy = DEFAULT_POLICY,
    sleep: Callable[[float], None] = lambda _seconds: None,
) -> T:
    """Call ``fn`` up to ``policy.max_attempts`` times, backing off between tries.

    ``sleep`` defaults to a no-op so tests never actually wait; a real caller
    supplies ``time.sleep``. Only exceptions that are **not**
    :class:`~.model.Refused` are retried — see the module docstring.
    """
    from .model import Refused  # local import: avoids a cycle with model at load time

    last_exc: Exception = RuntimeError("policy declared zero attempts")
    for attempt in range(policy.max_attempts):
        try:
            return fn()
        except Refused:
            raise
        except Exception as exc:  # transient: retry
            last_exc = exc
            if attempt + 1 < policy.max_attempts:
                sleep(policy.delay(attempt))
    raise last_exc
