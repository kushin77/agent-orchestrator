"""External client identity -> **exactly one** tenant, declared and explicit.

The chat surface is reached from clients that have their own user table (the
shared-services OpenWebUI deployment, whose reverse proxy injects a trusted
``X-Forwarded-Email`` header). ADR-0023 fixes the rule this module enforces:
**a client's own user table is advisory, never authoritative.** The mapping
from a client identity to a tenant is therefore declared in a data file
(``identity-map.json``) and read here - not inferred, not defaulted, not
derived from anything the client can set.

Three refusals, all of them fail-closed:

* an identity the map does not name -> :class:`UnmappedClientIdentity`. It
  resolves to *no* tenant; "no tenant" is not a tenant.
* an identity the map names more than once -> :class:`AmbiguousClientIdentity`.
  The contract is exactly one tenant, so a duplicated declaration is refused
  rather than resolved first-match-wins.
* a structurally malformed map (unknown keys, an id outside our closed role
  vocabulary, a missing id) -> :class:`InvalidIdentityMap`. An unknown field
  is refused instead of ignored, so a typo such as ``tenantID`` cannot quietly
  drop a binding's tenant.

Where the client's role vocabulary and ours disagree, **ours wins**: the role
always comes from the declared binding, and
:class:`Resolution.role_ignored` records that a differing client claim was
seen and discarded (the credential is minted with our role regardless).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .errors import (
    AmbiguousClientIdentity,
    InvalidIdentityMap,
    UnmappedClientIdentity,
)

#: The client whose identity map this module ships (shared-services OpenWebUI).
CLIENT_OPENWEBUI = "openwebui"

#: The only header accepted as a trusted external identity.
#: The shared-services reverse proxy injects it after authenticating the
#: caller; anything else (a body field, an arbitrary header) is untrusted.
TRUSTED_FORWARDED_EMAIL_HEADER = "x-forwarded-email"

#: Our closed role vocabulary. A client claim outside it is simply ignored
#: (ours wins); a *declared* binding outside it makes the map invalid.
CHAT_ROLES: Tuple[str, ...] = ("chat-user", "chat-operator")

#: The declared map shipped beside this module.
DEFAULT_MAP_PATH = Path(__file__).resolve().with_name("identity-map.json")

_MAP_FORMAT = 1
_ALLOWED_MAP_KEYS = frozenset({"format", "note", "bindings"})
_ALLOWED_ENTRY_KEYS = frozenset(
    {"client", "externalId", "tenantId", "agentId", "role"}
)


def normalize_external_id(external_id: Any) -> str:
    """Normalize an external identity for lookup (trimmed, case-folded)."""
    if not isinstance(external_id, str):
        return ""
    return external_id.strip().lower()


@dataclass(frozen=True)
class ClientBinding:
    """One declared ``client identity -> (tenant, agent, role)`` binding."""

    client: str
    external_id: str
    tenant_id: str
    agent_id: str
    role: str


@dataclass(frozen=True)
class Resolution:
    """The outcome of resolving a client identity.

    ``claimed_role`` is whatever the client asserted; ``role_ignored`` says it
    was discarded. ``role`` - the only role ever used - is ours.
    """

    binding: ClientBinding
    claimed_role: str = ""
    role_ignored: bool = False

    @property
    def tenant_id(self) -> str:
        return self.binding.tenant_id

    @property
    def agent_id(self) -> str:
        return self.binding.agent_id

    @property
    def role(self) -> str:
        return self.binding.role

    @property
    def external_id(self) -> str:
        return self.binding.external_id


class IdentityMap:
    """The declared external-identity map, queried fail-closed."""

    def __init__(self, bindings: Sequence[ClientBinding]) -> None:
        self._bindings: Tuple[ClientBinding, ...] = tuple(bindings)

    # --- construction ---------------------------------------------------- #

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "IdentityMap":
        """Load the declared map; a malformed file is a refusal, not a default."""
        target = Path(path) if path is not None else DEFAULT_MAP_PATH
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
        except OSError as exc:
            raise InvalidIdentityMap(f"identity map {target} cannot be read") from exc
        except ValueError as exc:
            raise InvalidIdentityMap(f"identity map {target} is not JSON") from exc
        return cls.from_payload(payload)

    @classmethod
    def from_payload(cls, payload: Any) -> "IdentityMap":
        """Validate a map payload and build the map (strict, fail-closed)."""
        if not isinstance(payload, Mapping):
            raise InvalidIdentityMap("identity map must be a JSON object")
        unknown = sorted(set(payload) - _ALLOWED_MAP_KEYS)
        if unknown:
            raise InvalidIdentityMap(f"identity map has unknown keys {unknown}")
        if payload.get("format") != _MAP_FORMAT:
            raise InvalidIdentityMap(
                f"identity map format {payload.get('format')!r} is not {_MAP_FORMAT}"
            )
        raw_entries = payload.get("bindings")
        if not isinstance(raw_entries, list) or not raw_entries:
            raise InvalidIdentityMap("identity map must declare at least one binding")
        return cls([_parse_entry(entry) for entry in raw_entries])

    # --- inspection ------------------------------------------------------ #

    @property
    def bindings(self) -> Tuple[ClientBinding, ...]:
        return self._bindings

    def clients(self) -> Tuple[str, ...]:
        """The clients the map declares (sorted, deduplicated)."""
        return tuple(sorted({binding.client for binding in self._bindings}))

    def tenants(self) -> Tuple[str, ...]:
        """The tenants the map declares (sorted, deduplicated)."""
        return tuple(sorted({binding.tenant_id for binding in self._bindings}))

    # --- resolution ------------------------------------------------------ #

    def matching(self, *, client: str, external_id: str) -> List[ClientBinding]:
        """Every declared binding for one exact ``(client, identity)`` pair."""
        wanted = normalize_external_id(external_id)
        return [
            binding
            for binding in self._bindings
            if binding.client == client and binding.external_id == wanted
        ]

    def resolve(self, *, client: str, external_id: str) -> ClientBinding:
        """Resolve to **exactly one** binding; refuse zero or many."""
        wanted = normalize_external_id(external_id)
        if not wanted:
            raise UnmappedClientIdentity(
                "no external client identity was presented; the chat surface "
                "has no anonymous mode"
            )
        matches = self.matching(client=client, external_id=wanted)
        if not matches:
            raise UnmappedClientIdentity(
                f"client identity {wanted!r} is not declared for client "
                f"{client!r}; refusing rather than defaulting to a tenant"
            )
        tenants = {binding.tenant_id for binding in matches}
        if len(matches) > 1 or len(tenants) > 1:
            raise AmbiguousClientIdentity(
                f"client identity {wanted!r} is declared {len(matches)} times for "
                f"client {client!r} (tenants {sorted(tenants)}); the contract is "
                "exactly one tenant"
            )
        return matches[0]

    def resolve_request(
        self,
        *,
        client: str,
        headers: Mapping[str, str],
        claimed_role: Any = "",
    ) -> Resolution:
        """Resolve from a request: trusted header identity, our role.

        The identity is read from the proxy-injected trusted header **only**;
        ``claimed_role`` is recorded and then discarded - where the client's
        role vocabulary and ours disagree, ours wins.
        """
        external_id = forwarded_identity(headers)
        binding = self.resolve(client=client, external_id=external_id)
        claimed = claimed_role.strip() if isinstance(claimed_role, str) else ""
        return Resolution(
            binding=binding,
            claimed_role=claimed,
            role_ignored=bool(claimed) and claimed != binding.role,
        )


def _parse_entry(entry: Any) -> ClientBinding:
    if not isinstance(entry, Mapping):
        raise InvalidIdentityMap(f"identity map entry {entry!r} is not an object")
    unknown = sorted(set(entry) - _ALLOWED_ENTRY_KEYS)
    if unknown:
        raise InvalidIdentityMap(f"identity map entry has unknown keys {unknown}")
    values: Dict[str, str] = {}
    for key in ("client", "externalId", "tenantId", "agentId", "role"):
        raw = entry.get(key)
        if not isinstance(raw, str) or not raw.strip():
            raise InvalidIdentityMap(f"identity map entry field {key!r} is required")
        values[key] = raw.strip()
    if values["role"] not in CHAT_ROLES:
        raise InvalidIdentityMap(
            f"identity map entry declares role {values['role']!r}, outside our "
            f"vocabulary {list(CHAT_ROLES)}"
        )
    return ClientBinding(
        client=values["client"],
        external_id=normalize_external_id(values["externalId"]),
        tenant_id=values["tenantId"],
        agent_id=values["agentId"],
        role=values["role"],
    )


def forwarded_identity(headers: Optional[Mapping[str, str]]) -> str:
    """The trusted proxy-injected external identity, or ``""`` when absent.

    Header lookup is case-insensitive and *only* the declared trusted header
    is consulted, so a client cannot nominate its own identity through a body
    field or a header of its own choosing.
    """
    if not headers:
        return ""
    for name, value in headers.items():
        if str(name).strip().lower() == TRUSTED_FORWARDED_EMAIL_HEADER:
            return value if isinstance(value, str) else ""
    return ""


def load_default_map() -> IdentityMap:
    """The shipped declared map (``identity-map.json`` beside this module)."""
    return IdentityMap.load(DEFAULT_MAP_PATH)


def declared_fields(map_path: Optional[Path] = None) -> Iterable[str]:
    """The field names the declared map uses (used by the gate's leak scan)."""
    target = Path(map_path) if map_path is not None else DEFAULT_MAP_PATH
    payload = json.loads(target.read_text(encoding="utf-8"))
    for entry in payload.get("bindings", ()):
        if isinstance(entry, Mapping):
            yield from (str(key) for key in entry)
