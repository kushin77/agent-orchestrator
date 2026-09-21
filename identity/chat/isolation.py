"""Conversation memory isolation - one container, bound to the credential.

A conversation's memory/history lives in exactly one ``engine/memory``
container: ``session:<tenant>:<agent>:<conversation>``. The container string is
not re-derived here - it is produced by the merged
``engine.memory.model.container_id`` for ``MemoryScope.SESSION``, so the chat
surface and the store can never disagree about what a container is named.

:class:`ChatIsolation` is the chat path onto that store. Every read and write:

1. recomputes the credential's container and refuses a request that names a
   *different* container (a conversation whose credential scope disagrees with
   its container is refused, and the refusal is counted);
2. delegates to ``engine.memory.store`` with the credential's
   ``(tenant, agent, conversation)`` as the caller identity, so the store's own
   cover check runs underneath; a crossing raises ``MemoryIsolationError``
   there, which is re-raised here **chained** (``__cause__``) rather than
   swallowed - the chat path cannot bypass the engine's isolation, only add a
   counting layer in front of it;
3. for a write, recomputes the container the store actually produced and
   refuses when it is not the credential's container.

Two counters make a crossing observable, and both are exposed:
``cross_scope_denials`` (refusals this layer issued) and
``engine_cross_scope_violations`` (the store's own count, read back from
``MemoryStore.stats()``). A test that only read the first could pass while the
engine's guard was bypassed; reading both proves the guard underneath ran.


---knowledge---
module_id: identity.chat.isolation
system: identity
app: chat
solution_class: enterprise
patterns: [tenant-scoped, fail-closed, consume-never-restate]
derives_from: null
owner_sme: security-sme
tier: L1
interfaces: [ChatIsolation, conversation_container, MemoryRef]
invariants: "a conversation's memory lives in exactly one container, produced by the merged engine.memory.model and never re-derived here"
gotchas: "a request naming a container other than the credential's is refused, and the refusal is counted"
related: ["#505", "#500"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, Optional

from engine.memory.model import MemoryIsolationError, MemoryScope, container_id
from engine.memory.store import MemoryStore

from .errors import ConversationScopeMismatch, InvalidScope

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids an import cycle
    from .credential import ChatCredential

#: Every conversation container is a SESSION-scope container.
CONTAINER_PREFIX = "session:"

#: Refusal codes this layer counts.
DENIAL_CONVERSATION_SCOPE = "conversation_scope_mismatch"
DENIAL_ENGINE_ISOLATION = "engine_memory_isolation"


def conversation_container(
    tenant_id: str, agent_id: str, conversation_id: str
) -> str:
    """The one ``engine/memory`` container a conversation owns.

    Delegates to the merged engine vocabulary so the chat surface cannot drift
    from the store: it is ``session:<tenant>:<agent>:<session>`` because that
    is what ``engine.memory.model.container_id`` returns, not because this
    module formats that string.
    """
    return container_id(MemoryScope.SESSION, tenant_id, agent_id, conversation_id)


@dataclass(frozen=True)
class MemoryRef:
    """A memory entry addressed inside one container."""

    container_id: str
    memory_id: str


class ChatIsolation:
    """The chat path onto a scoped ``engine/memory`` store."""

    def __init__(self, store: MemoryStore) -> None:
        self.store = store
        self._denials: Dict[str, int] = {}
        self._cross_scope_denials = 0

    # --- counters --------------------------------------------------------- #

    @property
    def cross_scope_denials(self) -> int:
        """How many refusals this layer has issued (any crossing)."""
        return self._cross_scope_denials

    @property
    def denials(self) -> Dict[str, int]:
        """Refusal counts by code (a copy; the counter itself is the property)."""
        return dict(self._denials)

    @property
    def engine_cross_scope_violations(self) -> int:
        """The store's own crossing count, read back from ``MemoryStore.stats()``."""
        return int(self.store.stats().get("cross_scope_violations", 0))

    def _refuse(self, code: str, message: str, exc_type: type, **details: Any):
        self._cross_scope_denials += 1
        self._denials[code] = self._denials.get(code, 0) + 1
        return exc_type(message, **details)

    # --- container resolution --------------------------------------------- #

    def container_for(self, credential: "ChatCredential") -> str:
        """The single container the credential's conversation owns."""
        return conversation_container(
            credential.tenant_id, credential.agent_id, credential.conversation_id
        )

    def assert_container(
        self, credential: "ChatCredential", requested_container: Optional[str] = None
    ) -> str:
        """Resolve the credential's container, refusing a named container that differs."""
        expected = self.container_for(credential)
        if requested_container is not None and requested_container != expected:
            raise self._refuse(
                DENIAL_CONVERSATION_SCOPE,
                f"conversation {credential.conversation_id!r} is bound to "
                f"container {expected!r}, not {requested_container!r}",
                ConversationScopeMismatch,
                expectedContainer=expected,
                requestedContainer=requested_container,
            )
        return expected

    # --- reads / writes ---------------------------------------------------- #

    def read(
        self,
        credential: "ChatCredential",
        *,
        memory_id: str,
        requested_container: Optional[str] = None,
        now: Optional[Any] = None,
    ):
        """Read one entry through the engine store, under the credential's scope."""
        self.assert_container(credential, requested_container)
        if not memory_id:
            raise InvalidScope("memory_id is required")
        try:
            return self.store.get(
                memory_id,
                tenant_id=credential.tenant_id,
                agent_id=credential.agent_id,
                session_id=credential.conversation_id,
                now=now,
            )
        except MemoryIsolationError as exc:
            raise self._refuse(
                DENIAL_ENGINE_ISOLATION,
                f"engine/memory refused the read: {exc}",
                ConversationScopeMismatch,
            ) from exc

    def write(
        self,
        credential: "ChatCredential",
        *,
        key: str,
        text: str,
        metadata: Optional[Dict[str, Any]] = None,
        ttl_seconds: Optional[int] = None,
        requested_container: Optional[str] = None,
        now: Optional[Any] = None,
    ):
        """Write one entry into the credential's conversation container."""
        expected = self.assert_container(credential, requested_container)
        if not key:
            raise InvalidScope("key is required")
        entry = self.store.put(
            tenant_id=credential.tenant_id,
            agent_id=credential.agent_id,
            session_id=credential.conversation_id,
            scope=MemoryScope.SESSION,
            key=key,
            text=text,
            metadata=metadata,
            ttl_seconds=ttl_seconds,
            now=now,
        )
        landed = container_id(
            entry.scope, entry.tenant_id, entry.agent_id, entry.session_id
        )
        if landed != expected:
            raise self._refuse(
                DENIAL_CONVERSATION_SCOPE,
                f"write landed in container {landed!r}, not the credential's "
                f"{expected!r}",
                ConversationScopeMismatch,
                expectedContainer=expected,
                landedContainer=landed,
            )
        return entry

    def ref_of(self, credential: "ChatCredential", *, key: str) -> MemoryRef:
        """The ``(container, memory_id)`` a conversation's key addresses."""
        from engine.memory.model import memory_id_for

        container = self.container_for(credential)
        memory_id = memory_id_for(
            credential.tenant_id,
            credential.agent_id,
            credential.conversation_id,
            key,
        )
        return MemoryRef(container_id=container, memory_id=memory_id)
