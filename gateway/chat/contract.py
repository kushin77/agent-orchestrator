"""gateway.chat.contract — the OpenAI- and Ollama-compatible wire contract.

Two flavours of the same turn, and one rule that governs both: **nothing a
request body says about identity or spend is authoritative.**  The parser reads
the turn (messages, model claim, stream flag), records the fields that are
*claims* — ``tenantId``/``tenant``, ``role``, ``api_key``, ``budget`` — and
never returns them as facts.  The tenant, the agent and the conversation come
from the verified credential (``identity/chat``), and the budget comes from
``telemetry/chat``; a claim is either ignored or refused, never honoured.

* ``POST /v1/chat/completions`` — OpenAI: ``model``, ``messages``, ``stream``,
  ``tools``.  Streaming is ``data: {...}`` frames terminated by
  ``data: [DONE]`` (the sentinel the portal client already waits for).
* ``POST /api/chat`` — Ollama: the same fields with Ollama's own response shape
  (``{model, created_at, message:{role, content}, done, prompt_eval_count,
  eval_count}``, streaming as newline-delimited JSON), so an Ollama-shaped
  client points at this surface unchanged — the shape ``gateway/providers/ollama.py``
  already speaks.

The ``ao`` extension rides inside the compatible envelope.  It is additive (an
OpenAI-only client ignores it) and it is where the platform's own facts live:
the resolved tier, the tier the client claimed, the citations envelope, the
grounding state, the usage/cost figures and the guardrail verdicts.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

from .errors import MalformedRequest

#: Roles the compatible contracts carry.
MESSAGE_ROLES = ("system", "user", "assistant", "tool")

#: Body fields that are a *claim*, never an authority.  They are recorded for
#: the audit trail, and a foreign tenant claim is refused outright.
TENANT_CLAIM_FIELDS = ("tenantId", "tenant", "tenant_id")
IGNORED_AUTHORITY_FIELDS = (
    "api_key",
    "apiKey",
    "key",
    "budget",
    "budgetUsd",
    "budget_usd",
    "role",
    "user",
    "userId",
    "scopes",
)

#: The streaming sentinel both compatible contracts terminate with.
SSE_DONE = "data: [DONE]\n\n"

#: Completion id prefix (the OpenAI shape).
ID_PREFIX = "chatcmpl-"


@dataclass(frozen=True)
class ChatTurnRequest:
    """One parsed turn: the request's content, plus its claims kept separate."""

    messages: tuple[tuple[str, str], ...]
    model_claim: str = ""
    stream: bool = False
    tools: tuple[Mapping[str, Any], ...] = ()
    grounding: Optional[Mapping[str, Any]] = None
    conversation_claim: str = ""
    tenant_claim: str = ""
    ignored_claims: Mapping[str, Any] = field(default_factory=dict)
    parameters: Mapping[str, Any] = field(default_factory=dict)

    @property
    def user_prompt(self) -> str:
        """The last user message — the question the turn asks."""
        for role, content in reversed(self.messages):
            if role == "user":
                return content
        return ""

    @property
    def system_prompt(self) -> str:
        return "\n".join(
            content for role, content in self.messages if role == "system"
        )


def _require_object(body: Any) -> Mapping[str, Any]:
    if not isinstance(body, Mapping):
        raise MalformedRequest("the request body must be a JSON object")
    return body


def _parse_messages(raw: Any) -> tuple[tuple[str, str], ...]:
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)) or not raw:
        raise MalformedRequest(
            "the request must carry a non-empty 'messages' array", param="messages"
        )
    messages: list[tuple[str, str]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise MalformedRequest(
                f"message #{index} is not an object", param=f"messages[{index}]"
            )
        role = item.get("role")
        if not isinstance(role, str) or role not in MESSAGE_ROLES:
            raise MalformedRequest(
                f"message #{index} has role {role!r}; expected one of "
                f"{', '.join(MESSAGE_ROLES)}",
                param=f"messages[{index}].role",
            )
        messages.append((role, _parse_content(item.get("content"), index)))
    if not any(role == "user" for role, _ in messages):
        raise MalformedRequest(
            "the request carries no user message", param="messages"
        )
    return tuple(messages)


def _parse_content(raw: Any, index: int) -> str:
    """A message's text: a string, or the OpenAI parts array's text parts."""
    if isinstance(raw, str):
        if not raw.strip():
            raise MalformedRequest(
                f"message #{index} has empty content", param=f"messages[{index}].content"
            )
        return raw
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
        parts: list[str] = []
        for part in raw:
            if not isinstance(part, Mapping):
                raise MalformedRequest(
                    f"message #{index} carries a non-object content part",
                    param=f"messages[{index}].content",
                )
            text = part.get("text")
            if not isinstance(text, str):
                raise MalformedRequest(
                    f"message #{index} carries a {part.get('type')!r} content part; "
                    "only text parts are supported",
                    param=f"messages[{index}].content",
                )
            parts.append(text)
        if not parts:
            raise MalformedRequest(
                f"message #{index} carries no text part", param=f"messages[{index}].content"
            )
        return "\n".join(parts)
    raise MalformedRequest(
        f"message #{index} content must be a string", param=f"messages[{index}].content"
    )


def _parse_tools(raw: Any) -> tuple[Mapping[str, Any], ...]:
    """Consume tool declarations **unchanged** — this surface declares none."""
    if raw is None:
        return ()
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise MalformedRequest("'tools' must be an array", param="tools")
    tools: list[Mapping[str, Any]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise MalformedRequest(
                f"tool #{index} is not an object", param=f"tools[{index}]"
            )
        tools.append(item)
    return tuple(tools)


def _parse_grounding(raw: Any) -> Optional[Mapping[str, Any]]:
    """The grounding lane's assembled block, handed through **unchanged**."""
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise MalformedRequest(
            "'grounding' must be an object assembled by the grounding lane",
            param="grounding",
        )
    return raw


def _claim_fields(body: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    """Split the body's claims from its content (claims are never authorities)."""
    tenant_claim = ""
    for name in TENANT_CLAIM_FIELDS:
        value = body.get(name)
        if isinstance(value, str) and value.strip():
            tenant_claim = value.strip()
            break
    ignored = {
        name: body[name]
        for name in IGNORED_AUTHORITY_FIELDS
        if name in body
    }
    for name in TENANT_CLAIM_FIELDS:
        if name in body:
            ignored[name] = body[name]
    return tenant_claim, ignored


def _string_or_empty(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def parse_openai_request(body: Any) -> ChatTurnRequest:
    """Parse ``POST /v1/chat/completions`` (fail closed on anything malformed)."""
    document = _require_object(body)
    tenant_claim, ignored = _claim_fields(document)
    return ChatTurnRequest(
        messages=_parse_messages(document.get("messages")),
        model_claim=_string_or_empty(document.get("model")),
        stream=bool(document.get("stream", False)),
        tools=_parse_tools(document.get("tools")),
        grounding=_parse_grounding(document.get("grounding")),
        conversation_claim=_string_or_empty(
            document.get("conversationId") or document.get("conversation")
        ),
        tenant_claim=tenant_claim,
        ignored_claims=ignored,
        parameters={
            key: document[key]
            for key in ("temperature", "top_p", "max_tokens", "stop", "seed")
            if key in document
        },
    )


def parse_ollama_request(body: Any) -> ChatTurnRequest:
    """Parse ``POST /api/chat`` — the Ollama shape the local adapter speaks.

    The field names are the ones ``gateway/providers/ollama.py`` already sends
    (``model``/``messages``/``stream``/``options``), so an Ollama-shaped client
    needs no translation to point at this surface.
    """
    document = _require_object(body)
    tenant_claim, ignored = _claim_fields(document)
    options = document.get("options")
    parameters = dict(options) if isinstance(options, Mapping) else {}
    return ChatTurnRequest(
        messages=_parse_messages(document.get("messages")),
        model_claim=_string_or_empty(document.get("model")),
        stream=bool(document.get("stream", False)),
        tools=_parse_tools(document.get("tools")),
        grounding=_parse_grounding(document.get("grounding")),
        conversation_claim=_string_or_empty(
            document.get("conversationId") or document.get("conversation")
        ),
        tenant_claim=tenant_claim,
        ignored_claims=ignored,
        parameters=parameters,
    )


# --------------------------------------------------------------------------- #
# Response builders
# --------------------------------------------------------------------------- #
def completion_body(
    *,
    completion_id: str,
    created: int,
    model: str,
    content: str,
    finish_reason: str,
    usage: Mapping[str, int],
    ao: Mapping[str, Any],
    tool_calls: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """One non-streaming ``chat.completion`` (exactly one JSON body)."""
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        # Pass-through only: a tool call the grounding lane declared, never one
        # this surface invented.
        message["tool_calls"] = [dict(call) for call in tool_calls]
    return {
        "id": completion_id,
        "object": "chat.completion",
        "created": created,
        "model": model,
        "choices": [
            {"index": 0, "message": message, "finish_reason": finish_reason}
        ],
        "usage": dict(usage),
        "ao": dict(ao),
    }


def delta_frame(
    *,
    completion_id: str,
    created: int,
    model: str,
    content: str = "",
    finish_reason: Optional[str] = None,
    ao: Optional[Mapping[str, Any]] = None,
    usage: Optional[Mapping[str, int]] = None,
) -> dict[str, Any]:
    """One ``chat.completion.chunk`` (a streaming increment).

    ``usage`` rides the **terminal** frame, which is the compatible contract's
    own placement for it: an OpenAI-compatible client that asks for usage reads
    it off the final chunk (with an empty delta), and the portal client merges
    that chunk's usage with the platform extension rather than letting the last
    one win.
    """
    delta: dict[str, Any] = {"content": content} if content else {}
    frame: dict[str, Any] = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [
            {"index": 0, "delta": delta, "finish_reason": finish_reason}
        ],
    }
    if ao:
        frame["ao"] = dict(ao)
    if usage is not None:
        frame["usage"] = dict(usage)
    return frame


def ollama_chat_body(
    *,
    model: str,
    created_at: str,
    content: str,
    usage: Mapping[str, int],
    ao: Mapping[str, Any],
) -> dict[str, Any]:
    """One non-streaming Ollama ``/api/chat`` response (its own shape)."""
    return {
        "model": model,
        "created_at": created_at,
        "message": {"role": "assistant", "content": content},
        "done": True,
        "done_reason": "stop",
        "prompt_eval_count": int(usage.get("prompt_tokens", 0)),
        "eval_count": int(usage.get("completion_tokens", 0)),
        "ao": dict(ao),
    }


def ollama_stream_frame(
    *, model: str, created_at: str, content: str, done: bool = False
) -> dict[str, Any]:
    """One newline-delimited Ollama streaming frame."""
    frame: dict[str, Any] = {
        "model": model,
        "created_at": created_at,
        "message": {"role": "assistant", "content": content},
        "done": done,
    }
    if done:
        frame["done_reason"] = "stop"
    return frame


def sse_data(payload: Mapping[str, Any]) -> str:
    """Render one SSE ``data:`` frame (JSON, one line)."""
    return "data: " + json.dumps(payload, sort_keys=False) + "\n\n"


def sse_error(error: Mapping[str, Any]) -> str:
    """Render a refusal as a terminal SSE frame (the compatible error shape)."""
    return sse_data(error)


def ndjson(payload: Mapping[str, Any]) -> str:
    """Render one newline-delimited JSON frame (Ollama streaming)."""
    return json.dumps(payload, sort_keys=False) + "\n"
