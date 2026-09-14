# engine/conversation — where a conversation lives (issue #513, EPIC #500)

The console has a chat surface (#503 serving, #508 view, #505 identity, #506
FinOps, #507 guardrails). None of them owned **what a conversation is**: its
ordered transcript, its lifecycle, its tenancy, or its retention and
data-subject surface. This package owns that, and only that.

> Pillar 3 · State-machine execution · phase 3.

## Why this is not `engine/memory`

`engine/memory` is a **semantic key→entry store** with three scopes
(`TENANT`/`AGENT`/`SESSION`), TTL and a capacity cap: you ask it for *memories
relevant to a query*. A conversation is an **ordered transcript** with roles,
citations, per-message cost and a title you can rename. A conversation has one
SESSION container in the memory store; it is not the same object.

So this module **consumes** `engine/memory` (unchanged) rather than replacing or
duplicating it, and delegates the memory half of an erasure to
`engine/memory/gdpr.py`:

```
TranscriptStore ── owns ──▶ Conversation / Message / Citation  (the transcript)
      │
      ├── delete(hard=True) / forget()  ──▶ engine.memory.gdpr.forget(
      │                                        scope=SESSION, session_id=<conversation>)
      └── export()                      ──▶ a deterministic JSON snapshot (ours)
```

Nothing in `engine/memory` is edited; the deletion is *delegated* so the
data-subject boundary stays in the module that already proved it.

## The shape

```
Conversation
├── conversation_id, (tenant_id, agent_id), title
├── created_at, updated_at, retention_seconds, pinned, deleted_at
└── messages[]           Message
                         ├── message_id, role ∈ {system,user,assistant}
                         ├── content, created_at
                         ├── citations[]     ← the #504 envelope, CONSUMED
                         ├── model, tier, tokens, cost_ref   ← #503 / #506 refs
                         └── unsupported_claims[]            ← #507 marker
```

`Conversation.to_dict()` is the **stable serialisation** — one shape, not two —
and the round trip is asserted by the tests.

## The rules the tests hold

| Rule | Why it matters |
|---|---|
| **Exact scope, or it raises.** Every read takes `(tenant, agent, conversation)`. | A cross-tenant or cross-agent read raises `ConversationIsolationError` **and increments `isolation_violations`**, so a probe is visible rather than merely refused. `ConversationNotFound` is a *different* error, so "absent" and "forbidden" never collapse into one answer. |
| **The refusal never quotes content.** | A refusal is a scope statement, never a leak. |
| **Retention is measured from `created_at`.** | Otherwise using a conversation keeps it alive forever and the declared window means nothing. |
| **Soft-delete is not erasure.** | `delete()` marks (`deleted_at`); `delete(hard=True)` / `forget()` erase from the transcript **and** the SESSION container. Conflating them would make "deleted" mean two different things. |
| **`dry_run` measures, never estimates.** | The memory container is asked with its *own* `dry_run`, so the reported count is real. |
| **Erasure is scoped.** | Purging one conversation removes that conversation's container, not the tenant's memory. |
| **Offline and deterministic.** | Python stdlib only, no network, no model calls; the clock is injected (`now=`), the `engine/memory` convention. Two exports of identical state are byte-identical. |

## Usage

```python
from engine.conversation import TranscriptStore

store = TranscriptStore(memory_store=memory)          # memory is optional
convo = store.create_conversation(tenant_id="acme", agent_id="coder", title="planning")
store.append_message(convo.conversation_id, tenant_id="acme", agent_id="coder",
                     role="assistant", content="…",
                     citations=[{"source_id": "s-1"}], model="deepseek-v4", tier="HIGH")
store.list_conversations(tenant_id="acme", limit=20, cursor=None)
store.search("planning", tenant_id="acme")
store.export_json(convo.conversation_id, tenant_id="acme", agent_id="coder")
store.forget(convo.conversation_id, tenant_id="acme", agent_id="coder", dry_run=True)
store.purge_expired()                                  # retention sweep, both halves
```

## Verify

```bash
python3 -m pytest engine/conversation -q -p no:cacheprovider
make verify
```
