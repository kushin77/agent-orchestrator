"""gateway.chat.conversation — the conversation primitive (issue #503 scope 3).

A conversation is **one ``engine/memory`` container**: the SESSION-scope
container ``session:<tenant>:<agent>:<session>`` that the engine's own
``container_id`` returns.  Nothing here formats that string — the key comes from
``identity/chat``'s :class:`ChatIsolation`, which delegates to
``engine.memory.model`` — so the chat surface cannot drift from the store's
vocabulary.

Every term is bound to the **verified credential**: a turn is written with the
credential's tenant/agent/conversation, and a request that claims a different
container is refused by the isolation layer rather than trusted.  The store is a
thin log on top of that: turns are appended under deterministic, sequential
keys, so a conversation's history is readable without any index this lane would
have to keep in sync.
"""

from __future__ import annotations

import json
from typing import Any, Mapping, Optional, Sequence

from identity.chat.isolation import ChatIsolation

#: The key prefix of one stored turn ("turn:000001").
TURN_KEY_PREFIX = "turn:"

#: How many turns a history read will look back by default.
DEFAULT_HISTORY_TURNS = 40

#: How many turns this store will write before it refuses (a bound, not a
#: policy: the gateway's context cap is the authority on what is sent).
MAX_STORED_TURNS = 500


class ConversationError(ValueError):
    """The conversation store refused an operation."""


def turn_key(index: int) -> str:
    """The deterministic key of the ``index``-th turn (1-based, zero padded)."""
    if index < 1:
        raise ConversationError(f"turn index must be >= 1, got {index}")
    return f"{TURN_KEY_PREFIX}{index:06d}"


class ConversationStore:
    """Append-only conversation log over a credential-scoped memory store."""

    def __init__(
        self,
        isolation: Optional[ChatIsolation] = None,
        *,
        memory_store: Any = None,
        max_turns: int = MAX_STORED_TURNS,
    ) -> None:
        if isolation is None:
            if memory_store is None:
                from engine.memory.store import InMemoryStore

                memory_store = InMemoryStore()
            isolation = ChatIsolation(memory_store)
        self.isolation = isolation
        self.max_turns = int(max_turns)

    # -- scope ------------------------------------------------------------- #
    def container(
        self, credential: Any, *, requested_container: Optional[str] = None
    ) -> str:
        """The credential's container, refusing a claim that names another.

        ``requested_container`` is whatever a request *claimed* (never an
        authority): naming a container other than the credential's own is a
        refusal, and naming the right one changes nothing.
        """
        return self.isolation.assert_container(credential, requested_container)

    # -- writes ------------------------------------------------------------ #
    def append(self, credential: Any, turn: Mapping[str, Any]) -> str:
        """Append one turn to the conversation log; returns its key."""
        history = self.history(credential, limit=self.max_turns)
        if len(history) >= self.max_turns:
            raise ConversationError(
                f"conversation {credential.conversation_id!r} holds {len(history)} "
                f"turns (cap {self.max_turns}); refusing to grow unbounded"
            )
        key = turn_key(len(history) + 1)
        payload = dict(turn)
        self.isolation.write(
            credential,
            key=key,
            text=json.dumps(payload, sort_keys=True),
            metadata={
                "kind": "chat-turn",
                "turnId": str(payload.get("turn_id") or ""),
                "tenantId": credential.tenant_id,
                "agentId": credential.agent_id,
                "conversationId": credential.conversation_id,
            },
        )
        return key

    # -- reads ------------------------------------------------------------- #
    def history(
        self,
        credential: Any,
        *,
        limit: int = DEFAULT_HISTORY_TURNS,
        requested_container: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """The conversation's turns, oldest first (at most ``limit``)."""
        self.container(credential, requested_container=requested_container)
        turns: list[dict[str, Any]] = []
        for index in range(1, max(1, int(limit)) + 1):
            entry = self.isolation.read(credential, memory_id=self._memory_id(credential, index))
            if entry is None:
                break
            turns.append(self._decode(entry))
        return turns

    # -- internals --------------------------------------------------------- #
    def _memory_id(self, credential: Any, index: int) -> str:
        return self.isolation.ref_of(credential, key=turn_key(index)).memory_id

    @staticmethod
    def _decode(entry: Any) -> dict[str, Any]:
        text = getattr(entry, "text", "")
        try:
            document = json.loads(text)
        except (TypeError, ValueError) as exc:
            raise ConversationError(
                f"stored conversation entry is not readable JSON: {exc}"
            ) from exc
        if not isinstance(document, dict):
            raise ConversationError("stored conversation entry is not a JSON object")
        return document


def replay_messages(
    history: Sequence[Mapping[str, Any]], *, limit: int = DEFAULT_HISTORY_TURNS
) -> list[dict[str, str]]:
    """The OpenAI-shaped replay of a conversation's turns (context, not truth).

    Only the roles and text are replayed — a stored turn's *derived* fields
    (tier, citations, usage) are never replayed as if the model had just said
    them.
    """
    messages: list[dict[str, str]] = []
    for turn in list(history)[-max(1, int(limit)):]:
        for message in turn.get("messages") or ():
            if isinstance(message, Mapping) and message.get("role") in (
                "user",
                "assistant",
                "system",
            ):
                messages.append(
                    {"role": str(message["role"]), "content": str(message.get("content", ""))}
                )
    return messages
