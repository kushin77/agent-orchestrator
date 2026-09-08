"""Isolation-lane exceptions (issue #30).

All fail-closed by design: a caller that cannot determine safety must get an
exception, never a silently permissive answer.
"""

from __future__ import annotations


class IsolationError(Exception):
    """Base class for every error raised by the isolation lane."""


class IsolationScopeError(IsolationError):
    """A store-layer access crossed (or could not prove) its tenant scope.

    Raised by the tenant-scope-gated store when a caller attempts to read or
    mutate a record it is not scoped for, or when a record would violate the
    denormalized-tenant invariant.  Mirrors the ``MemoryIsolationError``
    fail-closed contract of the engine/memory store.
    """

    def __init__(self, message: str, *, tenant_id: str | None = None) -> None:
        super().__init__(message)
        self.tenant_id = tenant_id


class ScanError(IsolationError):
    """The code scanner could not parse or walk a target."""


class RepairAbortError(IsolationError):
    """A repair was requested but validation failed; nothing was committed.

    Raised when an opt-in repair cannot be applied transactionally.  Carries
    the reasons so the caller can audit *why* no mutation happened.
    """

    def __init__(self, message: str, *, reasons: list[str] | None = None) -> None:
        super().__init__(message)
        self.reasons = reasons or []
