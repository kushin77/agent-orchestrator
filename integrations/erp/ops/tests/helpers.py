"""The two assertions every module in this lane's suite needs.

A refusal is asserted by **code**, never by message: the code is the part a
caller branches on and the part the gate can compare against the closed
vocabulary. Each helper also returns the detail, because a refusal that names
the wrong thing is a different bug from one that names nothing.
"""

from __future__ import annotations

from typing import Any, Callable, Tuple

from integrations.erp.ops.model import Refused


def refusal_from(action: Callable[[], Any]) -> Tuple[str, str]:
    """Run ``action`` and return the ``(reason, detail)`` of the refusal it raises."""
    try:
        action()
    except Refused as refusal:
        return refusal.reason, refusal.detail
    raise AssertionError(f"nothing was refused; {action!r} returned normally")


def assert_refused(action: Callable[[], Any], code: str, *, needle: str = "") -> str:
    """Assert ``action`` refuses with ``code`` (and names ``needle``)."""
    reason, detail = refusal_from(action)
    assert reason == code, f"expected {code!r}, got {reason!r}: {detail}"
    if needle:
        assert needle in detail, f"{code} did not name {needle!r}: {detail}"
    return detail
