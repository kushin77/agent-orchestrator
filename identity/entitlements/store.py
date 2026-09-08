"""In-memory entitlement store (the persistence seam).

Mirrors the identity/rbac store philosophy: ``engine.py`` and ``overrides.py``
are written against the handful of accessors this module provides, so a later
phase (control plane, phase 7) can back it with a real database adapter
without touching the enforcement logic.

The in-memory implementation is the offline default used by tests and by
single-tenant embedded use. It keeps profiles and overrides in dicts keyed by
id and the audit log as an append-only list (order preserved - the audit trail
is the record of record; see ``events_for_org``).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from entitlements.model import (
    EntitlementAuditEvent,
    EntitlementProfile,
    Override,
)


@dataclass
class InMemoryStore:
    """Thread-unsafe in-memory entitlement store (tests / embedded use)."""

    _profiles: dict[str, EntitlementProfile] = field(default_factory=dict)
    _overrides: dict[str, Override] = field(default_factory=dict)
    _events: list[EntitlementAuditEvent] = field(default_factory=list)
    _next_id: int = 0

    # --- id generation ------------------------------------------------------

    def _new_id(self, prefix: str) -> str:
        self._next_id += 1
        return f"{prefix}_{self._next_id}"

    # --- profiles (plan assignment + subscription state) ---------------------

    def profile(self, org_id: str) -> EntitlementProfile | None:
        return self._profiles.get(org_id)

    def save_profile(self, profile: EntitlementProfile) -> EntitlementProfile:
        if not profile.org_id:
            raise ValueError("profile org_id must be non-empty")
        self._profiles[profile.org_id] = profile
        return profile

    def profiles(self) -> list[EntitlementProfile]:
        return sorted(self._profiles.values(), key=lambda p: p.org_id)

    # --- overrides -----------------------------------------------------------

    def add_override(self, override: Override) -> Override:
        if override.id in self._overrides:
            raise ValueError(f"override already exists: {override.id}")
        self._overrides[override.id] = override
        return override

    def override(self, override_id: str) -> Override | None:
        return self._overrides.get(override_id)

    def delete_override(self, override_id: str) -> bool:
        return self._overrides.pop(override_id, None) is not None

    def overrides_for_org(self, org_id: str) -> list[Override]:
        return sorted(
            (o for o in self._overrides.values() if o.org_id == org_id),
            key=lambda o: (o.granted_at, o.id),
        )

    # --- audit log -----------------------------------------------------------

    def record_event(self, event: EntitlementAuditEvent) -> EntitlementAuditEvent:
        self._events.append(event)
        return event

    def events_for_org(self, org_id: str) -> list[EntitlementAuditEvent]:
        """Append-order audit trail for an org (oldest first)."""
        return [e for e in self._events if e.org_id == org_id]

    def all_events(self) -> list[EntitlementAuditEvent]:
        return list(self._events)
