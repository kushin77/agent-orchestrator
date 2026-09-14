"""engine.conversation.store — where a conversation lives (issue #513, EPIC #500).

A conversation is an **ordered transcript**, not a memory: ``engine/memory`` is a
semantic key→entry store whose SESSION container is a *related* thing, not the
same thing. This module owns the transcript, its lifecycle, its tenancy and its
retention/data-subject surface; ``engine/memory`` stays the memory store and is
**consumed unchanged** (``engine/memory/gdpr.py`` supplies the erasure and export
boundary this module delegates to for the SESSION container).

Design rules, each held by ``tests/``:

* **One shape, not two.** ``Conversation.to_dict()`` is the stable serialisation
  the serving surface (#503) and the UX lane (#508) both consume; the round-trip
  is asserted.
* **Reachable only by its exact scope.** Every read takes ``(tenant, agent,
  conversation)``; anything else raises ``ConversationIsolationError`` and is
  **counted**, so a probe is visible rather than merely refused.
* **Declared retention.** A conversation carries ``retention_seconds``; the
  default is configurable per store and overridable per conversation.
  ``purge_expired`` removes it from the transcript **and** from its memory
  SESSION container — nothing outlives its declared window.
* **Soft-delete is not erasure.** ``delete`` marks; ``delete(hard=True)`` and
  ``forget`` erase. The distinction is explicit, and ``forget(dry_run=True)``
  reports what would go without touching it.
* **Offline and deterministic.** Python stdlib only, no network, no model calls,
  and the clock is injected (``now=``) exactly as ``engine/memory`` does it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

SCHEMA_VERSION = "conversation.transcript/v1"

#: Roles a transcript may carry. A closed set, so a typo cannot invent a role.
ROLES = ("system", "user", "assistant")

DEFAULT_RETENTION_SECONDS = 30 * 24 * 60 * 60  # 30 days


# --- errors -----------------------------------------------------------------


class ConversationError(Exception):
    """Base class for this module's refusals."""


class ConversationNotFound(ConversationError):
    """No conversation matches the requested scope."""


class ConversationIsolationError(ConversationError):
    """A read or write crossed a tenant / agent / conversation boundary.

    Raised rather than returning ``None`` on purpose: silently empty is
    indistinguishable from "no such conversation", which is how a cross-tenant
    probe becomes a false absence.
    """


class ConversationValidationError(ConversationError):
    """The input cannot be represented as a transcript."""


# --- clock ------------------------------------------------------------------


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def to_utc(value: Any) -> datetime:
    """Accept an ISO-8601 string or a datetime; return an aware UTC datetime."""
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, str):
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            moment = datetime.fromisoformat(text)
        except ValueError as exc:  # pragma: no cover - defensive
            raise ConversationValidationError(f"unparsable timestamp: {value!r}") from exc
    else:
        raise ConversationValidationError(f"not a timestamp: {value!r}")
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


# --- value objects ----------------------------------------------------------


@dataclass(frozen=True)
class Citation:
    """One entry of the citations envelope produced upstream (#504).

    Consumed, never re-derived: ``from_envelope`` accepts the upstream shape and
    keeps the fields it publishes, so this module cannot drift into inventing a
    citation. Unknown extra keys are preserved verbatim in ``extra``.
    """

    source_id: str
    title: str = ""
    locator: str = ""
    extra: Tuple[Tuple[str, str], ...] = ()

    @classmethod
    def from_envelope(cls, envelope: Mapping[str, Any]) -> "Citation":
        if not isinstance(envelope, Mapping):
            raise ConversationValidationError(
                f"citation envelope must be a mapping, got {type(envelope).__name__}"
            )
        known = {"source_id", "title", "locator"}
        source_id = str(
            envelope.get("source_id") or envelope.get("id") or envelope.get("source") or ""
        )
        if not source_id:
            raise ConversationValidationError("citation envelope carries no source id")
        extra = tuple(
            sorted(
                (str(k), str(v))
                for k, v in envelope.items()
                if k not in known and k not in {"id", "source"}
            )
        )
        return cls(
            source_id=source_id,
            title=str(envelope.get("title") or ""),
            locator=str(envelope.get("locator") or ""),
            extra=extra,
        )

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"source_id": self.source_id}
        if self.title:
            payload["title"] = self.title
        if self.locator:
            payload["locator"] = self.locator
        payload.update(dict(self.extra))
        return payload


@dataclass(frozen=True)
class Message:
    """One ordered entry of a transcript."""

    message_id: str
    role: str
    content: str
    created_at: str
    citations: Tuple[Citation, ...] = ()
    model: str = ""
    tier: str = ""
    tokens: Optional[int] = None
    cost_ref: str = ""
    unsupported_claims: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.role not in ROLES:
            raise ConversationValidationError(
                f"role {self.role!r} is not one of {', '.join(ROLES)}"
            )

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "message_id": self.message_id,
            "role": self.role,
            "content": self.content,
            "created_at": self.created_at,
            "citations": [c.to_dict() for c in self.citations],
        }
        if self.model:
            payload["model"] = self.model
        if self.tier:
            payload["tier"] = self.tier
        if self.tokens is not None:
            payload["tokens"] = self.tokens
        if self.cost_ref:
            payload["cost_ref"] = self.cost_ref
        if self.unsupported_claims:
            payload["unsupported_claims"] = list(self.unsupported_claims)
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Message":
        return cls(
            message_id=str(data["message_id"]),
            role=str(data["role"]),
            content=str(data.get("content", "")),
            created_at=str(data["created_at"]),
            citations=tuple(
                Citation.from_envelope(c) for c in (data.get("citations") or ())
            ),
            model=str(data.get("model") or ""),
            tier=str(data.get("tier") or ""),
            tokens=data.get("tokens"),
            cost_ref=str(data.get("cost_ref") or ""),
            unsupported_claims=tuple(data.get("unsupported_claims") or ()),
        )


@dataclass
class Conversation:
    """A transcript and its lifecycle state."""

    conversation_id: str
    tenant_id: str
    agent_id: str
    title: str
    created_at: str
    updated_at: str
    retention_seconds: int = DEFAULT_RETENTION_SECONDS
    pinned: bool = False
    deleted_at: Optional[str] = None
    messages: List[Message] = field(default_factory=list)

    # -- derived ---------------------------------------------------------
    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    def expires_at(self, now: datetime) -> datetime:
        """Retention is measured from ``created_at``, not from last activity.

        A conversation cannot be kept alive indefinitely by using it; that would
        make the declared window meaningless.
        """
        return to_utc(self.created_at) + timedelta(seconds=self.retention_seconds)

    def is_expired(self, now: datetime) -> bool:
        return to_utc(now) >= self.expires_at(now)

    # -- serialisation ---------------------------------------------------
    def to_dict(self, *, include_messages: bool = True) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "schema": SCHEMA_VERSION,
            "conversation_id": self.conversation_id,
            "tenant_id": self.tenant_id,
            "agent_id": self.agent_id,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "retention_seconds": self.retention_seconds,
            "pinned": self.pinned,
            "deleted_at": self.deleted_at,
            "message_count": len(self.messages),
        }
        if include_messages:
            payload["messages"] = [m.to_dict() for m in self.messages]
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Conversation":
        return cls(
            conversation_id=str(data["conversation_id"]),
            tenant_id=str(data["tenant_id"]),
            agent_id=str(data["agent_id"]),
            title=str(data.get("title", "")),
            created_at=str(data["created_at"]),
            updated_at=str(data["updated_at"]),
            retention_seconds=int(data.get("retention_seconds") or DEFAULT_RETENTION_SECONDS),
            pinned=bool(data.get("pinned", False)),
            deleted_at=data.get("deleted_at"),
            messages=[Message.from_dict(m) for m in (data.get("messages") or ())],
        )


@dataclass(frozen=True)
class PurgeReport:
    """What a retention sweep did (or would do)."""

    expired: Tuple[str, ...]
    purged: Tuple[str, ...]
    memory_erased: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "expired": list(self.expired),
            "purged": list(self.purged),
            "memory_erased": self.memory_erased,
        }


@dataclass(frozen=True)
class ForgetReport:
    """What a data-subject erasure did (or would do)."""

    conversation_id: str
    tenant_id: str
    agent_id: str
    matched_messages: int
    erased_conversation: bool
    memory_erased: int
    dry_run: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "conversation_id": self.conversation_id,
            "tenant_id": self.tenant_id,
            "agent_id": self.agent_id,
            "matched_messages": self.matched_messages,
            "erased_conversation": self.erased_conversation,
            "memory_erased": self.memory_erased,
            "dry_run": self.dry_run,
        }


# --- the store --------------------------------------------------------------


class TranscriptStore:
    """In-memory, offline transcript store with declared retention.

    ``memory_store`` is the ``engine/memory`` store this module delegates the
    SESSION container to; it is optional so the store is usable (and testable)
    without one, and its absence never silently *skips* erasure — a hard delete
    with no memory store records ``memory_erased=0`` and says so in the report.
    """

    def __init__(
        self,
        *,
        memory_store: Optional[Any] = None,
        retention_seconds: int = DEFAULT_RETENTION_SECONDS,
        now: Optional[Callable[[], datetime]] = None,
    ) -> None:
        if retention_seconds <= 0:
            raise ConversationValidationError("retention_seconds must be positive")
        self.memory_store = memory_store
        self.retention_seconds = int(retention_seconds)
        self._now = now or _utc_now
        self._conversations: Dict[str, Conversation] = {}
        self._stats: Dict[str, int] = {
            "created": 0,
            "messages_appended": 0,
            "renamed": 0,
            "pinned": 0,
            "unpinned": 0,
            "soft_deleted": 0,
            "hard_deleted": 0,
            "expired_purged": 0,
            "forgotten": 0,
            "isolation_violations": 0,
        }

    # -- helpers ---------------------------------------------------------
    def _stamp(self) -> str:
        return _iso(self._now())

    def _require(
        self, conversation_id: str, *, tenant_id: str, agent_id: str
    ) -> Conversation:
        """Resolve a conversation inside its exact scope, or refuse loudly."""
        convo = self._conversations.get(conversation_id)
        if convo is None:
            raise ConversationNotFound(
                f"no conversation {conversation_id!r} for tenant {tenant_id!r}"
            )
        if convo.tenant_id != tenant_id or convo.agent_id != agent_id:
            self._stats["isolation_violations"] += 1
            raise ConversationIsolationError(
                "conversation "
                f"{conversation_id!r} belongs to ({convo.tenant_id!r}, {convo.agent_id!r}), "
                f"not ({tenant_id!r}, {agent_id!r})"
            )
        return convo

    def stats(self) -> Dict[str, int]:
        return dict(self._stats)

    # -- creation + writes ----------------------------------------------
    def create_conversation(
        self,
        *,
        tenant_id: str,
        agent_id: str,
        title: str = "New conversation",
        conversation_id: Optional[str] = None,
        retention_seconds: Optional[int] = None,
    ) -> Conversation:
        identifier = conversation_id or f"conv-{tenant_id}-{agent_id}-{len(self._conversations) + 1:04d}"
        if identifier in self._conversations:
            raise ConversationValidationError(f"conversation {identifier!r} already exists")
        stamp = self._stamp()
        convo = Conversation(
            conversation_id=identifier,
            tenant_id=tenant_id,
            agent_id=agent_id,
            title=title,
            created_at=stamp,
            updated_at=stamp,
            retention_seconds=int(retention_seconds or self.retention_seconds),
        )
        self._conversations[identifier] = convo
        self._stats["created"] += 1
        return convo

    def append_message(
        self,
        conversation_id: str,
        *,
        tenant_id: str,
        agent_id: str,
        role: str,
        content: str,
        citations: Sequence[Mapping[str, Any]] = (),
        model: str = "",
        tier: str = "",
        tokens: Optional[int] = None,
        cost_ref: str = "",
        unsupported_claims: Sequence[str] = (),
    ) -> Message:
        convo = self._require(conversation_id, tenant_id=tenant_id, agent_id=agent_id)
        if convo.is_deleted:
            raise ConversationValidationError(
                f"conversation {conversation_id!r} is deleted; append is refused"
            )
        stamp = self._stamp()
        message = Message(
            message_id=f"{conversation_id}:m{len(convo.messages) + 1:04d}",
            role=role,
            content=content,
            created_at=stamp,
            citations=tuple(Citation.from_envelope(c) for c in citations),
            model=model,
            tier=tier,
            tokens=tokens,
            cost_ref=cost_ref,
            unsupported_claims=tuple(unsupported_claims),
        )
        convo.messages.append(message)
        convo.updated_at = stamp
        self._stats["messages_appended"] += 1
        return message

    # -- reads -----------------------------------------------------------
    def get(self, conversation_id: str, *, tenant_id: str, agent_id: str,
            include_deleted: bool = False) -> Conversation:
        convo = self._require(conversation_id, tenant_id=tenant_id, agent_id=agent_id)
        if convo.is_deleted and not include_deleted:
            raise ConversationNotFound(
                f"conversation {conversation_id!r} is deleted (pass include_deleted=True to read it)"
            )
        return convo

    def list_conversations(
        self,
        *,
        tenant_id: str,
        agent_id: Optional[str] = None,
        cursor: Optional[str] = None,
        limit: int = 20,
        include_deleted: bool = False,
    ) -> Dict[str, Any]:
        """Tenant-scoped, cursor-paged listing (newest activity first).

        The cursor is the last ``conversation_id`` of the previous page, so
        paging is stable without an opaque server-side token.
        """
        if limit <= 0:
            raise ConversationValidationError("limit must be positive")
        rows = [
            c
            for c in self._conversations.values()
            if c.tenant_id == tenant_id
            and (agent_id is None or c.agent_id == agent_id)
            and (include_deleted or not c.is_deleted)
        ]
        rows.sort(key=lambda c: (-to_utc(c.updated_at).timestamp(), c.conversation_id))
        start = 0
        if cursor:
            for index, convo in enumerate(rows):
                if convo.conversation_id == cursor:
                    start = index + 1
                    break
        page = rows[start:start + limit]
        next_cursor = page[-1].conversation_id if len(page) == limit and start + limit < len(rows) else None
        return {
            "conversations": [c.to_dict(include_messages=False) for c in page],
            "cursor": next_cursor,
            "total": len(rows),
        }

    def search(
        self, query: str, *, tenant_id: str, agent_id: Optional[str] = None
    ) -> List[Conversation]:
        """Tenant-scoped search over title and message content."""
        needle = query.strip().lower()
        if not needle:
            raise ConversationValidationError("search query must not be empty")
        hits = []
        for convo in self._conversations.values():
            if convo.tenant_id != tenant_id or convo.is_deleted:
                continue
            if agent_id is not None and convo.agent_id != agent_id:
                continue
            haystack = [convo.title] + [m.content for m in convo.messages]
            if any(needle in value.lower() for value in haystack):
                hits.append(convo)
        hits.sort(key=lambda c: c.conversation_id)
        return hits

    # -- lifecycle -------------------------------------------------------
    def rename(self, conversation_id: str, *, tenant_id: str, agent_id: str,
               title: str) -> Conversation:
        convo = self._require(conversation_id, tenant_id=tenant_id, agent_id=agent_id)
        convo.title = title
        convo.updated_at = self._stamp()
        self._stats["renamed"] += 1
        return convo

    def pin(self, conversation_id: str, *, tenant_id: str, agent_id: str,
            pinned: bool = True) -> Conversation:
        convo = self._require(conversation_id, tenant_id=tenant_id, agent_id=agent_id)
        convo.pinned = pinned
        convo.updated_at = self._stamp()
        self._stats["pinned" if pinned else "unpinned"] += 1
        return convo

    def delete(self, conversation_id: str, *, tenant_id: str, agent_id: str,
               hard: bool = False, dry_run: bool = False) -> ForgetReport:
        """Soft-delete (default) or erase.

        Soft-delete only marks: the transcript is hidden from listing but still
        present, which is **not** erasure. ``hard=True`` removes the transcript
        and delegates the SESSION container to ``engine/memory``'s ``forget``.
        """
        convo = self._require(conversation_id, tenant_id=tenant_id, agent_id=agent_id)
        if dry_run:
            # Report what WOULD happen, touching neither the transcript nor the
            # memory container (the container is asked with its own dry_run, so
            # the count is measured rather than estimated).
            return ForgetReport(
                conversation_id=conversation_id,
                tenant_id=tenant_id,
                agent_id=agent_id,
                matched_messages=len(convo.messages),
                erased_conversation=hard,
                memory_erased=self._memory_container(
                    conversation_id, tenant_id=tenant_id, agent_id=agent_id, dry_run=True
                ),
                dry_run=True,
            )
        if hard:
            self._stats["hard_deleted"] += 1
            return self.forget(conversation_id, tenant_id=tenant_id, agent_id=agent_id)
        # Soft delete MARKS ONLY: the transcript is hidden from listing but is
        # still present. Erasure is forget() / hard=True, and conflating the two
        # would make "deleted" mean two different things.
        convo.deleted_at = self._stamp()
        self._stats["soft_deleted"] += 1
        return ForgetReport(
            conversation_id=conversation_id,
            tenant_id=tenant_id,
            agent_id=agent_id,
            matched_messages=len(convo.messages),
            erased_conversation=False,
            memory_erased=0,
            dry_run=False,
        )

    # -- retention + data subject ---------------------------------------
    def expired(self, *, now: Optional[datetime] = None) -> List[str]:
        moment = now or self._now()
        return sorted(
            c.conversation_id
            for c in self._conversations.values()
            if c.is_expired(moment)
        )

    def purge_expired(self, *, now: Optional[datetime] = None) -> PurgeReport:
        """Remove every conversation past its declared window, in both places."""
        moment = now or self._now()
        due = self.expired(now=moment)
        erased = 0
        for identifier in due:
            outcome = self.forget(
                identifier,
                tenant_id=self._conversations[identifier].tenant_id,
                agent_id=self._conversations[identifier].agent_id,
            )
            erased += outcome.memory_erased
            self._stats["expired_purged"] += 1
        return PurgeReport(expired=tuple(due), purged=tuple(due), memory_erased=erased)

    def _memory_container(self, conversation_id: str, *, tenant_id: str,
                          agent_id: str, dry_run: bool) -> int:
        """Delegate the SESSION container to engine/memory's own forget()."""
        if self.memory_store is None:
            return 0
        from engine.memory.gdpr import forget as memory_forget
        from engine.memory.model import MemoryScope

        outcome = memory_forget(
            self.memory_store,
            tenant_id=tenant_id,
            agent_id=agent_id,
            session_id=conversation_id,
            scope=MemoryScope.SESSION,
            dry_run=dry_run,
        )
        return int(getattr(outcome, "matched", 0) if dry_run else getattr(outcome, "deleted", 0))

    def forget(self, conversation_id: str, *, tenant_id: str, agent_id: str,
               dry_run: bool = False) -> ForgetReport:
        """Erase this conversation on tenant authority (GDPR data-subject right).

        ``dry_run`` reports what would go and touches nothing — including the
        memory container, which is queried with its own ``dry_run`` so the count
        is honest rather than estimated.
        """
        convo = self._require(conversation_id, tenant_id=tenant_id, agent_id=agent_id)
        matched = len(convo.messages)
        memory_hits = self._memory_container(
            conversation_id, tenant_id=tenant_id, agent_id=agent_id, dry_run=True
        )
        if dry_run:
            return ForgetReport(
                conversation_id=conversation_id,
                tenant_id=tenant_id,
                agent_id=agent_id,
                matched_messages=matched,
                erased_conversation=False,
                memory_erased=memory_hits,
                dry_run=True,
            )
        memory_erased = self._memory_container(
            conversation_id, tenant_id=tenant_id, agent_id=agent_id, dry_run=False
        )
        del self._conversations[conversation_id]
        self._stats["forgotten"] += 1
        return ForgetReport(
            conversation_id=conversation_id,
            tenant_id=tenant_id,
            agent_id=agent_id,
            matched_messages=matched,
            erased_conversation=True,
            memory_erased=memory_erased,
            dry_run=False,
        )

    def export(self, conversation_id: str, *, tenant_id: str, agent_id: str) -> Dict[str, Any]:
        """A deterministic JSON snapshot (the data-subject export).

        Deterministic means byte-identical for identical state: keys are sorted
        and no wall-clock value is read here, so two exports of the same
        conversation compare equal.
        """
        convo = self._require(conversation_id, tenant_id=tenant_id, agent_id=agent_id)
        return {
            "schema": SCHEMA_VERSION,
            "exported": "conversation",
            "conversation": convo.to_dict(),
        }

    def export_json(self, conversation_id: str, *, tenant_id: str, agent_id: str) -> str:
        return json.dumps(
            self.export(conversation_id, tenant_id=tenant_id, agent_id=agent_id),
            sort_keys=True,
            indent=2,
        )

    # -- snapshot / restore (durable shape, no files) --------------------
    def snapshot(self) -> Dict[str, Any]:
        return {
            "schema": SCHEMA_VERSION,
            "conversations": [c.to_dict() for c in self._conversations.values()],
        }

    def restore(self, data: Mapping[str, Any]) -> None:
        if data.get("schema") != SCHEMA_VERSION:
            raise ConversationValidationError(
                f"unknown transcript schema {data.get('schema')!r}; expected {SCHEMA_VERSION!r}"
            )
        restored: Dict[str, Conversation] = {}
        for row in data.get("conversations") or ():
            convo = Conversation.from_dict(row)
            restored[convo.conversation_id] = convo
        self._conversations = restored


__all__ = [
    "SCHEMA_VERSION",
    "ROLES",
    "DEFAULT_RETENTION_SECONDS",
    "Conversation",
    "ConversationError",
    "ConversationIsolationError",
    "ConversationNotFound",
    "ConversationValidationError",
    "Citation",
    "ForgetReport",
    "Message",
    "PurgeReport",
    "TranscriptStore",
    "to_utc",
]
