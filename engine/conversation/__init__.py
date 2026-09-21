"""engine.conversation — the conversation transcript store (issue #513, EPIC #500).

Where a conversation lives: the ordered transcript, its lifecycle, its tenancy
and its retention / export / erasure surface.

This module does **not** replace ``engine.memory``. That is a semantic
key→entry memory store whose SESSION container is a related-but-different thing;
this package owns the transcript and delegates the memory half of an erasure to
``engine.memory.gdpr.forget`` rather than reimplementing it.

---knowledge---
module_id: engine.conversation.__init__
system: engine
app: conversation
solution_class: template
patterns: [package-contract, public-surface]
derives_from: null
owner_sme: platform-sme
tier: L0
interfaces: [SCHEMA_VERSION, ROLES, DEFAULT_RETENTION_SECONDS, Citation, Conversation, ConversationError, ConversationIsolationError, ConversationNotFound, (+6 more)]
invariants: ""
gotchas: ""
related: ["#513", "#500"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

from .store import (
    DEFAULT_RETENTION_SECONDS,
    ROLES,
    SCHEMA_VERSION,
    Citation,
    Conversation,
    ConversationError,
    ConversationIsolationError,
    ConversationNotFound,
    ConversationValidationError,
    ForgetReport,
    Message,
    PurgeReport,
    TranscriptStore,
    to_utc,
)

__all__ = [
    "SCHEMA_VERSION",
    "ROLES",
    "DEFAULT_RETENTION_SECONDS",
    "Citation",
    "Conversation",
    "ConversationError",
    "ConversationIsolationError",
    "ConversationNotFound",
    "ConversationValidationError",
    "ForgetReport",
    "Message",
    "PurgeReport",
    "TranscriptStore",
    "to_utc",
]
