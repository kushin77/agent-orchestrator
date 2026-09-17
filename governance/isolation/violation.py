"""The one ``Violation`` shape shared by the lane audit and its extensions.

Split out from :mod:`governance.isolation.audit` so a module that needs to
report a named finding (e.g. :mod:`governance.isolation.speculative`) does not
have to import the audit module itself and risk a cycle back into it.
``governance.isolation.audit`` re-exports :class:`Violation` for every existing
caller — this is not a second implementation, just where the dataclass lives.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Violation:
    """One broken isolation property, named so it can be quoted as evidence."""

    code: str
    detail: str

    def __str__(self) -> str:
        return f"{self.code}: {self.detail}"
