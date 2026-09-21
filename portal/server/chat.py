"""portal.server.chat — the conversational surface (issue #508, ADR-0023).

---knowledge---
module_id: portal.server.chat
system: portal
app: server
solution_class: pattern
patterns: [cross-engine-adapter, join-not-own]
derives_from: null
owner_sme: sync-sme
tier: L1
interfaces: [tiers, ChatError, parse_turn_request, CancelToken, BudgetState, sse_frame, render_source, unsupported_label]
invariants: ""
gotchas: ""
related: ["#508"]
do_not_duplicate: null
---knowledge---

WHY this exists: the console could *show* the control plane but not *talk* to
it. This module is the experience half of EPIC #500 — the conversational
surface a user actually judges — and it is deliberately the **client** half of
the decision the ADR freezes: the surface is **gateway-authoritative**. The
portal owns transport, provenance rendering and honest degradation; it owns no
model, no price and no verdict.

Two boundaries are held absolutely here:

* **Consumed over HTTP, never imported.** ``gateway/**``, ``identity/**``,
  ``telemetry/**``, ``guardrails/**`` and ``registry/**`` are authorities on the
  other side of the serving surface, so nothing in this module imports them.
  The turn goes out as one OpenAI-/Ollama-compatible request
  (``POST {gateway}/v1/chat/completions`` with ``stream: true``) and every fact
  this surface renders — the resolved model, the citations, the degradation,
  the usage figures, the budget action — comes back from that contract or is
  reported as **absent**. The vocabulary it does pin (the tier ladder and the
  budget actions) is a *declaration to be cross-checked*, and
  ``scripts/check-chat-ux.sh`` fails if the authority drifts from it.
* **A tier, never a model.** The request's ``model`` field carries a **tier**
  token from the frozen ladder, because the chooser is the authority that maps
  a tier to a provider model. A client that asks for an arbitrary provider model
  is refused (``chat_tier_only``); the model the chooser *resolved* is displayed
  and never chosen.

Honest degradation is the point of the surface, so it is a state machine rather
than a happy path. A turn always lands in exactly one terminal state
(``complete`` / ``cancelled`` / ``failed``) and always carries an explicit
grounding state: a turn whose envelope backs no fragment is ``NO_DATA`` — never
an empty success. Usage absent from the read model is ``NO_DATA``, never
``$0.00``. A degraded (fallback-tier) turn says so, with the tiers it moved
between. A budget read that does not answer is ``no_data`` and the turn is
**refused** — the pre-flight check fails closed, because an unreadable budget
cannot be shown not to be a hard stop. The refusal to send and the refusal to
answer are different refusals with different codes, because they mean different
things to the operator.

Offline by construction in tests: the upstream is an ordinary HTTP endpoint, so
the suite points it at a loopback fake serving the same contract — no network
egress, no live gateway.

The surface ships **feature-flag-gated OFF** (GR-5): the flag is
``surfaces.chat`` in ``infra/feature-flags/registry.yaml``, read through the
fail-closed reader the fleet projection already uses. While it is off the whole
``/api/chat/*`` family *and* the view's own static assets are absent — checked
before AuthN, so an unpromoted surface is invisible rather than distinguishable
by an authentication probe.
"""

from __future__ import annotations

import http.client
import json
import os
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Optional
from urllib.parse import urlsplit

from portal.server.fleet import surface_enabled

#: The tier ladder is READ from its declared authority (#1494): the issue-#9
#: AgentProfile catalog `registry/profiles/catalog.yaml` ``tiers``, through its
#: one reader `registry/profiles/tiers.py`. The portal used to pin a second copy
#: of the ladder "from the gateway's provider contract"; that copy is gone, so a
#: rename in the authority is followed here rather than cross-checked later.
#: The read is LAZY: this module is copied into scratch trees by a gate fixture
#: (`scripts/check-control-audit.sh` copies `portal/` alone), and an import-time
#: read of an authority those trees do not carry would make the surface
#: unloadable there — the exact #967 failure shape that check already guards
#: against for `portal/server/fleet.py`.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_TIERS: tuple[str, ...] | None = None


def tiers() -> tuple[str, ...]:
    """The tier ladder, in the authority's declared order (read once, cached)."""
    global _TIERS
    if _TIERS is None:
        from registry.profiles.tiers import authority

        _TIERS = authority()
    return _TIERS


def __getattr__(name: str) -> object:
    """Resolve the declared name ``TIERS`` through :func:`tiers` (PEP 562).

    ``chat.TIERS`` and ``from portal.server.chat import TIERS`` therefore get
    the one ladder while the read stays lazy. Any OTHER unknown name is still an
    ``AttributeError`` — a resolver that answered everything would turn a typo
    into a silent empty vocabulary.
    """
    if name == "TIERS":
        return tiers()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

#: The registry surface key that gates this endpoint family (issue #500).
CHAT_SURFACE = "chat"

#: The default tier, a single rung of the authority (an id, never a second
#: declaration of the ladder).
DEFAULT_TIER = "MED"

#: The pre-flight budget actions, pinned from ``gateway/finops/budget.py``
#: ``BudgetAction``. ``warn``/``fallback`` are the *soft* band; ``stop`` is the
#: hard one. The two are rendered distinctly and the hard one refuses the turn.
BUDGET_ACTIONS: tuple[str, ...] = ("allow", "warn", "fallback", "stop")

#: The visible severities the surface renders. Deliberately four, not two: a
#: soft warning, a hard stop and "the authority did not answer" are different
#: states and must not collapse into one another.
SEVERITIES: tuple[str, ...] = ("ok", "warning", "hard_stop", "no_data")

#: The grounding states. ``NO_DATA`` is the honest empty: no source, named.
GROUNDING_OK = "OK"
GROUNDING_NO_DATA = "NO_DATA"

#: The terminal states a turn can reach.
TURN_COMPLETE = "complete"
TURN_CANCELLED = "cancelled"
TURN_FAILED = "failed"

#: The upstream contract this surface speaks (OpenAI-/Ollama-compatible).
COMPLETIONS_PATH = "/v1/chat/completions"
BUDGET_PATH = "/v1/ao/finops/budget"

#: The static assets that *are* the surface. While the flag is off they are
#: absent too — the view is invisible, not a 403 on a document that exists.
CHAT_ASSETS: tuple[str, ...] = ("views/chat.html", "js/chat.js")

#: The console's default conversation store: a runtime directory that is never
#: committed (the same shape as the telemetry usage store).
DEFAULT_CHAT_STORE = Path(".portal") / "chat"

#: Env overrides, so a deployment moves the store and the authority without a
#: code change (env only — never a secret, never a file in the tree).
STORE_ENV = "AO_PORTAL_CHAT_STORE"
GATEWAY_ENV = "AO_PORTAL_CHAT_GATEWAY"

#: Longest history a single turn replays upstream (the context cap is the
#: gateway's business; this is only a transport guard).
MAX_REPLAY_TURNS = 40

#: Longest user message the console will forward.
MAX_MESSAGE_CHARS = 8000


class ChatError(Exception):
    """An HTTP-addressable chat refusal (the route renders it as an envelope)."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def parse_turn_request(body: dict[str, Any]) -> tuple[str, str]:
    """Validate a turn request; return ``(text, tier)``.

    Pure, and deliberately strict about the one thing the client must not
    choose: **the model**. A body carrying ``model`` (or ``provider`` /
    ``modelId``) is refused with ``chat_tier_only`` — the tier is the only
    selection this surface accepts, and the chooser resolves it. An unknown
    tier is refused with ``chat_unknown_tier`` rather than forwarded, so an
    arbitrary model id can never reach the authority dressed as a tier.
    """
    for forbidden in ("model", "modelId", "model_id", "provider"):
        if str(body.get(forbidden) or "").strip():
            raise ChatError(
                400,
                "chat_tier_only",
                f"the client selects a tier, never a provider model "
                f"({forbidden!r} is refused; send 'tier' instead)",
            )
    tier = str(body.get("tier") or DEFAULT_TIER).strip().upper()
    if tier not in tiers():
        raise ChatError(
            400,
            "chat_unknown_tier",
            f"unknown tier {tier!r}; the ladder is {', '.join(tiers())}",
        )
    text = str(body.get("text") or body.get("message") or "").strip()
    if not text:
        raise ChatError(400, "chat_text_required", "a turn needs non-empty text")
    if len(text) > MAX_MESSAGE_CHARS:
        raise ChatError(
            400,
            "chat_message_too_long",
            f"the message exceeds {MAX_MESSAGE_CHARS} characters",
        )
    return text, tier


@dataclass
class CancelToken:
    """The stop-generation flag for one in-flight turn."""

    cancelled: bool = False


@dataclass
class BudgetState:
    """The tenant's budget state as the FinOps read model reports it.

    ``severity`` is the *rendered* distinction the issue requires: ``warning``
    is the soft band (the turn proceeds), ``hard_stop`` is the absolute one (the
    turn is refused), ``no_data`` is "the authority did not answer" (the turn is
    refused too, but for a different reason and with a different code), and
    ``ok`` means spend is inside the budget.
    """

    action: str = ""
    severity: str = "no_data"
    can_send: bool = False
    budget_usd: Optional[float] = None
    spent_usd: Optional[float] = None
    pct_used: Optional[float] = None
    warn_at_pct: Optional[float] = None
    hard_cap_pct: Optional[float] = None
    note: str = ""

    def as_json(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "severity": self.severity,
            "canSend": self.can_send,
            "budgetUsd": self.budget_usd,
            "spentUsd": self.spent_usd,
            "pctUsed": self.pct_used,
            "warnAtPct": self.warn_at_pct,
            "hardCapPct": self.hard_cap_pct,
            "note": self.note,
        }


def sse_frame(event: str, payload: dict[str, Any]) -> str:
    """One server-sent-events frame in the surface's own ``ao.chat/v1`` shape."""
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n"


def _digest(value: Any, length: int = 12) -> str:
    """A short, display-safe form of a revision digest (never invented)."""
    text = str(value or "")
    return text[:length]


def render_source(source: dict[str, Any]) -> str:
    """The human label for a cited source: family, revision and the id.

    A bridge-family citation reads ``bridge registry@<rev> · ticket #129``; a
    tool call reads ``tool kb.query@<rev> · kb.query``. Every part shown is a
    part the envelope carried — nothing is composed from a guess.
    """
    family = str(source.get("family") or source.get("kind") or "source")
    revision = _digest(source.get("revision"))
    identifier = str(source.get("label") or source.get("id") or "")
    prefix = "bridge " if source.get("family") else ""
    parts = [f"{prefix}{family}" + (f"@{revision}" if revision else "")]
    if identifier:
        parts.append(identifier)
    return " · ".join(parts)


def unsupported_label() -> str:
    """The label a fragment gets when the envelope does not back it."""
    return "unsupported — no source in the citations envelope"


class ChatSurface:
    """The conversational surface: transport, provenance, honest degradation.

    ``enabled`` resolves from ``surfaces.chat`` unless supplied explicitly
    (tests and the gate supply it; the server lets the registry decide).
    ``store_dir`` and ``gateway_base_url`` are overridable for the same reason —
    a test points them at a tmp path and a loopback fake — and default to
    ``AO_PORTAL_CHAT_STORE`` / ``AO_PORTAL_CHAT_GATEWAY``, then to
    ``<repo>/.portal/chat`` and ``127.0.0.1:8788``.
    """

    def __init__(
        self,
        *,
        repo_root: Path | str,
        enabled: Optional[bool] = None,
        registry_path: Optional[Path | str] = None,
        store_dir: Optional[Path | str] = None,
        gateway_base_url: Optional[str] = None,
        timeout: float = 30.0,
    ) -> None:
        self.repo_root = Path(repo_root)
        self.registry_path = Path(registry_path) if registry_path is not None else None
        if enabled is None:
            enabled = surface_enabled(
                self.repo_root,
                registry_path=self.registry_path,
                surface=CHAT_SURFACE,
            )
        self.enabled = bool(enabled)
        raw_store = store_dir or os.environ.get(STORE_ENV) or ""
        self.store_dir = (
            Path(raw_store) if raw_store else self.repo_root / DEFAULT_CHAT_STORE
        )
        self.gateway_base_url = (
            gateway_base_url
            or os.environ.get(GATEWAY_ENV)
            or "127.0.0.1:8788"
        )
        self.timeout = float(timeout)
        #: One stop-generation flag per conversation, replaced per turn.
        self._tokens: dict[str, CancelToken] = {}

    # -- vocabulary ---------------------------------------------------------
    def tiers(self, tenant_id: str = "") -> dict[str, Any]:
        """The picker's payload: the ladder, and what the chooser resolved.

        ``tiers`` are the *only* selectable values. ``resolvedModels`` is what
        the authority has already told this surface, per tier, for this tenant —
        observations read back out of the turn history, so a tier never observed
        is simply absent rather than guessed.
        """
        resolved: dict[str, str] = {}
        if tenant_id:
            for conversation in self._load(tenant_id)["conversations"]:
                for turn in conversation["turns"]:
                    model = str(turn.get("resolvedModel") or "")
                    tier = str(turn.get("tier") or "")
                    if model and tier in tiers():
                        resolved[tier] = model
        return {
            "vocabulary": list(tiers()),
            "defaultTier": DEFAULT_TIER,
            "selects": "tier",
            "tiers": [
                {"id": tier, "label": tier, "default": tier == DEFAULT_TIER}
                for tier in tiers()
            ],
            "resolvedModels": resolved,
            "note": (
                "the client selects a tier; the serving surface's chooser "
                "resolves the provider model and this surface only displays it"
            ),
        }

    # -- conversations ------------------------------------------------------
    def conversations(self, tenant_id: str) -> list[dict[str, Any]]:
        """The tenant's conversations, newest first (summaries only)."""
        rows = [
            {
                "id": conversation["id"],
                "title": conversation["title"],
                "createdAt": conversation["createdAt"],
                "updatedAt": conversation["updatedAt"],
                "turnCount": len(conversation["turns"]),
                "lastTier": _last_tier(conversation),
            }
            for conversation in self._load(tenant_id)["conversations"]
        ]
        return sorted(rows, key=lambda row: (row["updatedAt"], row["id"]), reverse=True)

    def create_conversation(self, tenant_id: str, title: str = "") -> dict[str, Any]:
        """Open a conversation. The title is the caller's; nothing is invented."""
        store = self._load(tenant_id)
        stamp = _now_iso()
        clean = str(title or "").strip()[:120]
        conversation = {
            "id": f"cvs_{uuid.uuid4().hex[:12]}",
            "title": clean or "New conversation",
            "createdAt": stamp,
            "updatedAt": stamp,
            "turns": [],
        }
        store["conversations"].append(conversation)
        self._save(tenant_id, store)
        return _conversation_json(conversation)

    def conversation(self, tenant_id: str, conversation_id: str) -> dict[str, Any]:
        """One conversation with its full turn history (provenance included)."""
        return _conversation_json(self._require_conversation(tenant_id, conversation_id))

    # -- turns --------------------------------------------------------------
    def turn_stream(
        self,
        *,
        tenant_id: str,
        conversation_id: str,
        text: str,
        tier: str,
        budget: Optional[BudgetState] = None,
        retry_of: str = "",
    ) -> Iterator[str]:
        """Stream one turn: token-by-token deltas, then exactly one terminal frame.

        The frames are the surface's own ``ao.chat/v1`` events, terminated by the
        OpenAI-compatible ``[DONE]`` sentinel. Every exit — completion, a
        cancelled stream, an upstream fault, a stream that ends without a
        terminal frame — persists the turn and reaches a terminal state; a
        cancelled stream closes the upstream connection cleanly rather than
        leaving it dangling.
        """
        store = self._load(tenant_id)
        conversation = _find(store, conversation_id)
        if conversation is None:
            raise ChatError(
                404, "unknown_conversation", f"no such conversation {conversation_id!r}"
            )
        pre_flight = budget if budget is not None else self.budget(tenant_id)
        messages = _replay_messages(conversation)
        messages.append({"role": "user", "content": text})

        stamp = _now_iso()
        user_turn = {
            "id": f"trn_{uuid.uuid4().hex[:12]}",
            "role": "user",
            "text": text,
            "tier": tier,
            "state": TURN_COMPLETE,
            "createdAt": stamp,
        }
        conversation["turns"].append(user_turn)
        conversation["updatedAt"] = stamp
        if conversation["title"] == "New conversation":
            conversation["title"] = text[:60]
        self._save(tenant_id, store)

        turn_id = f"trn_{uuid.uuid4().hex[:12]}"
        # A fresh flag per turn: a stale one from a previous turn must never
        # stop this one, and this one is dropped when the turn ends.
        token = CancelToken()
        self._tokens[conversation_id] = token

        yield sse_frame(
            "turn.started",
            {
                "contract": "ao.chat/v1",
                "conversationId": conversation_id,
                "turnId": turn_id,
                "tier": tier,
                "retryOf": retry_of or None,
            },
        )
        yield sse_frame("turn.budget", pre_flight.as_json())

        started = time.monotonic()
        collected = _TurnCapture(tier=tier, retry_of=retry_of)
        upstream = self._stream_upstream(messages=messages, tier=tier)
        try:
            for chunk in upstream:
                if token.cancelled:
                    collected.state = TURN_CANCELLED
                    break
                if chunk.get("done"):
                    collected.saw_terminal = True
                    continue
                event = collected.absorb(chunk)
                if event == "delta":
                    yield sse_frame(
                        "turn.delta",
                        {"turnId": turn_id, "delta": collected.last_delta,
                         "text": collected.text},
                    )
                elif event == "degraded":
                    yield sse_frame("turn.degraded", collected.degraded)
                elif event == "citations":
                    yield sse_frame(
                        "turn.citations", collected.citations_payload()
                    )
                if collected.text and token.cancelled:
                    collected.state = TURN_CANCELLED
                    break
        except ChatError as exc:
            collected.state = TURN_FAILED
            collected.failure = {"code": exc.code, "status": exc.status,
                                 "message": exc.message}
        except Exception as exc:  # noqa: BLE001 - an upstream fault is a turn state
            collected.state = TURN_FAILED
            collected.failure = {
                "code": "chat_upstream_error",
                "status": 502,
                "message": f"the serving surface failed mid-stream: {exc}",
            }
        else:
            if collected.state != TURN_CANCELLED:
                if collected.saw_terminal or collected.finish_reason:
                    collected.state = TURN_COMPLETE
                else:
                    collected.state = TURN_FAILED
                    collected.failure = {
                        "code": "chat_upstream_truncated",
                        "status": 502,
                        "message": (
                            "the serving surface ended the stream without a "
                            "terminal frame — the answer is incomplete"
                        ),
                    }
        finally:
            upstream.close()
            self._tokens.pop(conversation_id, None)

        if collected.state == TURN_CANCELLED:
            collected.finish_reason = "cancelled"
        collected.latency_ms = (
            collected.latency_ms
            if collected.latency_ms is not None
            else round((time.monotonic() - started) * 1000.0, 3)
        )
        assistant_turn = collected.as_turn(turn_id)
        conversation = _find(store, conversation_id)
        if conversation is not None:
            conversation["turns"].append(assistant_turn)
            conversation["updatedAt"] = assistant_turn["createdAt"]
            self._save(tenant_id, store)

        if collected.state == TURN_CANCELLED:
            yield sse_frame(
                "turn.cancelled",
                {"turnId": turn_id, "state": TURN_CANCELLED, "text": collected.text},
            )
        elif collected.state == TURN_FAILED:
            yield sse_frame(
                "turn.error",
                {"turnId": turn_id, "state": TURN_FAILED,
                 "failure": collected.failure, "text": collected.text},
            )
        else:
            yield sse_frame(
                "turn.completed",
                {"turnId": turn_id, "state": TURN_COMPLETE,
                 "turn": _turn_json(assistant_turn)},
            )
        yield "data: [DONE]\n\n"

    def cancel(self, tenant_id: str, conversation_id: str) -> dict[str, Any]:
        """Stop generation for the conversation's in-flight turn.

        An honest no-op when nothing is in flight: the caller is told
        ``cancelled: false`` and why, rather than shown a success it did not
        achieve.
        """
        self._require_conversation(tenant_id, conversation_id)
        token = self._tokens.get(conversation_id)
        if token is None:
            return {"cancelled": False, "note": "no turn is in flight"}
        token.cancelled = True
        return {"cancelled": True, "note": "generation stopped"}

    def retry_request(
        self, tenant_id: str, conversation_id: str, tier: str = ""
    ) -> tuple[str, str, str]:
        """``(text, tier, retryOf)`` for a retry of the last user turn.

        Eager (not streamed) on purpose: "there is nothing to retry" is a
        request-level refusal with its own code, not a stalled stream, so the
        route can answer it before a single frame is written.
        """
        conversation = self._require_conversation(tenant_id, conversation_id)
        last_user = next(
            (turn for turn in reversed(conversation["turns"]) if turn["role"] == "user"),
            None,
        )
        if last_user is None:
            raise ChatError(
                409, "chat_nothing_to_retry", "this conversation has no user turn yet"
            )
        previous = next(
            (
                turn
                for turn in reversed(conversation["turns"])
                if turn["role"] == "assistant"
            ),
            None,
        )
        return (
            str(last_user["text"]),
            (tier or str(last_user.get("tier") or DEFAULT_TIER)).upper(),
            str(previous["id"]) if previous else "",
        )

    def require_conversation(self, tenant_id: str, conversation_id: str) -> None:
        """Fail fast (before streaming) when the conversation does not exist."""
        self._require_conversation(tenant_id, conversation_id)

    # -- budget -------------------------------------------------------------
    def budget(self, tenant_id: str) -> BudgetState:
        """The tenant's pre-flight budget state, from the FinOps read model.

        Consumed over HTTP; never restated here. A read that does not answer is
        ``no_data`` and does **not** send: the pre-flight check fails closed, so
        a budget that cannot be read can never be shown not to be a hard stop.
        ``warn``/``fallback`` are the soft band and the turn proceeds; ``stop``
        refuses it. An action the authority does not declare is treated as
        unreadable, not as permission.
        """
        document, problem = self._fetch_json(BUDGET_PATH + f"?tenant={tenant_id}")
        if document is None:
            return BudgetState(
                action="",
                severity="no_data",
                can_send=False,
                note=f"{problem} — the budget authority did not answer, so the "
                "turn is refused rather than sent unverified",
            )
        action = str(document.get("action") or "").strip().lower()
        if action not in BUDGET_ACTIONS:
            return BudgetState(
                action=action,
                severity="no_data",
                can_send=False,
                note=f"the budget authority declared an unknown action "
                f"{action!r}; the turn is refused rather than sent unverified",
            )
        severity = {
            "allow": "ok",
            "warn": "warning",
            "fallback": "warning",
            "stop": "hard_stop",
        }[action]
        note = str(document.get("reason") or document.get("note") or "")
        if not note:
            note = {
                "allow": "inside budget",
                "warn": "at/above the warn threshold — spend is flagged",
                "fallback": "at/above the warn threshold — the chooser "
                "downgrades the tier",
                "stop": "budget exhausted or hard cap reached",
            }[action]
        return BudgetState(
            action=action,
            severity=severity,
            can_send=severity != "hard_stop",
            budget_usd=_number(document.get("monthlyBudgetUsd")
                               or document.get("budgetUsd")),
            spent_usd=_number(document.get("spentUsd")),
            pct_used=_number(document.get("pctUsed")),
            warn_at_pct=_number(document.get("warnAtPct")),
            hard_cap_pct=_number(document.get("hardCapPct")),
            note=note,
        )

    def refusal(self, tenant_id: str, state: BudgetState) -> Optional[ChatError]:
        """The refusal a non-sendable budget state earns, or ``None``.

        A hard stop and an unreadable budget are refused with *different* codes
        and different statuses, because an operator must be able to tell "you
        have no money" from "I could not ask".
        """
        if state.severity == "hard_stop":
            return ChatError(
                402,
                "chat_budget_hard_stop",
                f"tenant {tenant_id!r} is at the hard budget cap "
                f"({state.action!r}); the turn is refused before the model call",
            )
        if state.severity == "no_data":
            return ChatError(
                503,
                "chat_budget_no_data",
                f"the budget state for tenant {tenant_id!r} could not be read "
                f"({state.note})",
            )
        return None

    # -- upstream transport -------------------------------------------------
    def _stream_upstream(self, *, messages: list[dict[str, str]],
                         tier: str) -> Iterator[dict[str, Any]]:
        """The serving surface's SSE stream, as parsed chunks.

        OpenAI-/Ollama-compatible: ``data: {chunk}`` frames ended by
        ``data: [DONE]``. The tier travels in the ``model`` field, which is the
        one place the compatible contract has for it — and the surface is the
        only thing that sets it.
        """
        connection = self._connect()
        payload = json.dumps({"model": tier, "messages": messages, "stream": True})
        try:
            connection.request(
                "POST",
                COMPLETIONS_PATH,
                body=payload.encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Accept": "text/event-stream",
                },
            )
            response = connection.getresponse()
        except OSError as exc:
            connection.close()
            raise ChatError(
                503,
                "chat_upstream_unavailable",
                f"the serving surface at {self.gateway_base_url} is unreachable: {exc}",
            ) from exc
        if response.status != 200:
            detail = response.read().decode("utf-8", "replace")[:300]
            connection.close()
            raise ChatError(
                502,
                "chat_upstream_error",
                f"the serving surface refused the turn ({response.status}): {detail}",
            )
        try:
            while True:
                raw = response.readline()
                if not raw:
                    return
                line = raw.decode("utf-8", "replace").strip()
                if not line or line.startswith(":") or not line.startswith("data:"):
                    continue
                body = line[len("data:"):].strip()
                if body == "[DONE]":
                    yield {"done": True}
                    return
                try:
                    chunk = json.loads(body)
                except ValueError as exc:
                    raise ChatError(
                        502,
                        "chat_upstream_error",
                        f"the serving surface sent a non-JSON frame: {body[:120]!r}",
                    ) from exc
                if isinstance(chunk, dict):
                    yield chunk
        finally:
            connection.close()

    def _connect(self) -> http.client.HTTPConnection:
        """A connection to the serving surface (loopback in tests)."""
        parsed = urlsplit(
            self.gateway_base_url
            if "//" in self.gateway_base_url
            else f"//{self.gateway_base_url}"
        )
        host = parsed.hostname or "127.0.0.1"
        secure = parsed.scheme == "https"
        port = parsed.port or (443 if secure else 80)
        cls = http.client.HTTPSConnection if secure else http.client.HTTPConnection
        return cls(host, port, timeout=self.timeout)

    def _fetch_json(self, path: str) -> tuple[Optional[dict[str, Any]], str]:
        """One upstream JSON read; ``(document, problem)`` — never a raise.

        Every failure mode is *reported* rather than raised: the caller renders
        an honest ``NO_DATA`` instead of a 500 or an invented number.
        """
        connection = self._connect()
        try:
            connection.request("GET", path, headers={"Accept": "application/json"})
            response = connection.getresponse()
            body = response.read().decode("utf-8", "replace")
            if response.status != 200:
                return None, (
                    f"the FinOps read model answered {response.status} for {path}"
                )
        except OSError as exc:
            return None, f"the FinOps read model is unreachable ({exc})"
        finally:
            connection.close()
        try:
            document = json.loads(body)
        except ValueError:
            return None, f"the FinOps read model did not answer JSON for {path}"
        if not isinstance(document, dict):
            return None, f"the FinOps read model answered a non-object for {path}"
        return document, ""

    # -- conversation store -------------------------------------------------
    def _store_path(self, tenant_id: str) -> Path:
        safe = "".join(char for char in tenant_id if char.isalnum() or char in "-_")
        return self.store_dir / f"{safe or 'unknown'}.json"

    def _load(self, tenant_id: str) -> dict[str, Any]:
        path = self._store_path(tenant_id)
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"schema": "ao.portal-chat/v1", "tenantId": tenant_id,
                    "conversations": []}
        if not isinstance(document, dict):
            return {"schema": "ao.portal-chat/v1", "tenantId": tenant_id,
                    "conversations": []}
        conversations = document.get("conversations")
        if not isinstance(conversations, list):
            document["conversations"] = []
        else:
            document["conversations"] = [
                conversation
                for conversation in conversations
                if isinstance(conversation, dict)
                and isinstance(conversation.get("turns"), list)
            ]
        document.setdefault("schema", "ao.portal-chat/v1")
        document["tenantId"] = tenant_id
        return document

    def _save(self, tenant_id: str, store: dict[str, Any]) -> None:
        path = self._store_path(tenant_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(
            prefix=f".{path.name}.", dir=str(path.parent)
        )
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(store, stream, indent=2, sort_keys=True)
                stream.write("\n")
            os.replace(temporary, path)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise

    def _require_conversation(self, tenant_id: str,
                              conversation_id: str) -> dict[str, Any]:
        conversation = _find(self._load(tenant_id), conversation_id)
        if conversation is None:
            raise ChatError(
                404, "unknown_conversation", f"no such conversation {conversation_id!r}"
            )
        return conversation


class _TurnCapture:
    """Accumulates one upstream stream into a turn record, honestly.

    Every field is filled only from what the stream actually carried:
    ``grounding`` starts as ``NO_DATA`` and is only promoted when a source
    arrived, ``usage`` stays ``NO_DATA`` unless the read model sent figures, and
    a fragment the envelope does not back is marked unsupported rather than
    rendered as fact.
    """

    def __init__(self, *, tier: str, retry_of: str = "") -> None:
        self.tier = tier
        self.retry_of = retry_of
        self.text = ""
        self.last_delta = ""
        self.resolved_model = ""
        self.finish_reason = ""
        self.saw_terminal = False
        self.state = TURN_COMPLETE
        self.failure: dict[str, Any] = {}
        self.degraded: dict[str, Any] = {"degraded": False}
        self.grounding = {
            "state": GROUNDING_NO_DATA,
            "note": "the serving surface returned no grounding sources for this turn",
        }
        self.usage: dict[str, Any] = {
            "state": GROUNDING_NO_DATA,
            "promptTokens": None,
            "completionTokens": None,
            "totalTokens": None,
            "estimatedCostUsd": None,
            "latencyMs": None,
            "note": "the FinOps read model returned no usage for this turn",
        }
        self.latency_ms: Optional[float] = None
        self.sources: list[dict[str, Any]] = []
        self.fragments: list[dict[str, Any]] = []
        self._saw_citations = False
        self._saw_degraded = False
        self._saw_usage = False

    # -- absorption ---------------------------------------------------------
    def absorb(self, chunk: dict[str, Any]) -> str:
        """Fold one upstream chunk in; return the event worth emitting."""
        event = ""
        if chunk.get("model"):
            self.resolved_model = str(chunk["model"])
        choices = chunk.get("choices")
        if isinstance(choices, list) and choices:
            choice = choices[0] if isinstance(choices[0], dict) else {}
            delta = choice.get("delta") or {}
            content = str(delta.get("content") or "")
            if content:
                self.last_delta = content
                self.text += content
                event = "delta"
            if choice.get("finish_reason"):
                self.finish_reason = str(choice["finish_reason"])
        extension = chunk.get("ao")
        if isinstance(extension, dict):
            if self._absorb_tier(extension) and not event:
                event = "degraded"
            if self._absorb_citations(extension) and not event:
                event = "citations"
            self._absorb_usage(extension)
            self._absorb_grounding(extension)
        if isinstance(chunk.get("usage"), dict):
            # The compatible contract carries usage at the top level while the
            # cost/latency read model rides the ``ao`` extension: merge the two
            # rather than letting the last one win, or the turn would report
            # tokens with no cost.
            self._absorb_usage({"usage": chunk["usage"], "ao": extension})
        return event

    def _absorb_tier(self, extension: dict[str, Any]) -> bool:
        tier_block = extension.get("tier")
        if not isinstance(tier_block, dict):
            return False
        if tier_block.get("resolvedModel"):
            self.resolved_model = str(tier_block["resolvedModel"])
        degraded = extension.get("degraded")
        if not isinstance(degraded, dict) or not degraded.get("degraded"):
            return False
        if self._saw_degraded:
            return False
        self._saw_degraded = True
        reason = str(degraded.get("reason") or "")
        self.degraded = {
            "degraded": True,
            "reason": reason or "the serving surface fell back to a lower tier",
            "fromTier": degraded.get("fromTier") or tier_block.get("requested"),
            "toTier": degraded.get("toTier") or tier_block.get("resolved"),
            "note": "this answer was produced by a fallback model, not the "
            "tier that was requested",
        }
        return True

    def _absorb_citations(self, extension: dict[str, Any]) -> bool:
        envelope = extension.get("citations")
        if not isinstance(envelope, dict) or self._saw_citations:
            return False
        raw_sources = envelope.get("sources")
        sources = [
            source for source in raw_sources if isinstance(source, dict)
        ] if isinstance(raw_sources, list) else []
        raw_fragments = envelope.get("fragments")
        fragments = [
            fragment for fragment in raw_fragments if isinstance(fragment, dict)
        ] if isinstance(raw_fragments, list) else []
        if not sources and not fragments:
            return False
        self._saw_citations = True
        self.sources = sources
        self.fragments = _render_fragments(fragments, sources)
        self.grounding = {
            "state": GROUNDING_OK if sources else GROUNDING_NO_DATA,
            "note": (
                f"{len(sources)} source(s) in the citations envelope"
                if sources
                else "the envelope carried fragments but no source to back them"
            ),
        }
        return True

    def _absorb_usage(self, payload: dict[str, Any]) -> None:
        usage = payload.get("usage")
        if not isinstance(usage, dict):
            return
        extension = payload.get("ao")
        ao_usage = extension.get("usage") if isinstance(extension, dict) else None
        merged = dict(usage)
        if isinstance(ao_usage, dict):
            merged.update(
                {key: value for key, value in ao_usage.items() if key not in merged}
            )
        prompt = _number(merged.get("prompt_tokens", merged.get("promptTokens")))
        completion = _number(
            merged.get("completion_tokens", merged.get("completionTokens"))
        )
        total = _number(merged.get("total_tokens", merged.get("totalTokens")))
        cost = _number(
            merged.get("estimatedCostUsd", merged.get("estimated_cost_usd"))
        )
        latency = _number(merged.get("latencyMs", merged.get("latency_ms")))
        if prompt is None and completion is None and cost is None and latency is None:
            return
        if total is None and (prompt is not None or completion is not None):
            total = (prompt or 0.0) + (completion or 0.0)
        self._saw_usage = True
        self.usage = {
            "state": GROUNDING_OK,
            "promptTokens": prompt,
            "completionTokens": completion,
            "totalTokens": total,
            "estimatedCostUsd": cost,
            "latencyMs": latency,
            "note": "per-turn figures from the FinOps read model",
        }
        if latency is not None:
            self.latency_ms = latency

    def _absorb_grounding(self, extension: dict[str, Any]) -> None:
        grounding = extension.get("grounding")
        if not isinstance(grounding, dict):
            return
        state = str(grounding.get("state") or "").upper()
        if state not in (GROUNDING_OK, GROUNDING_NO_DATA):
            return
        note = str(grounding.get("note") or "")
        if state == GROUNDING_OK and not self.sources and not self.fragments:
            return
        self.grounding = {
            "state": state,
            "note": note or (
                "the serving surface reported no data for this turn"
                if state == GROUNDING_NO_DATA
                else f"{len(self.sources)} source(s) in the citations envelope"
            ),
        }

    # -- rendering ----------------------------------------------------------
    def citations_payload(self) -> dict[str, Any]:
        return {
            "grounding": self.grounding,
            "sources": self.sources,
            "fragments": self.fragments,
        }

    def as_turn(self, turn_id: str) -> dict[str, Any]:
        return {
            "id": turn_id,
            "role": "assistant",
            "text": self.text,
            "tier": self.tier,
            "resolvedModel": self.resolved_model,
            "state": self.state,
            "createdAt": _now_iso(),
            "finishReason": self.finish_reason,
            "retryOf": self.retry_of or None,
            "grounding": self.grounding,
            "sources": self.sources,
            "fragments": self.fragments,
            "degraded": self.degraded,
            "usage": self.usage,
            "failure": self.failure,
        }


# -- module helpers ---------------------------------------------------------
def _render_fragments(fragments: list[dict[str, Any]],
                      sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach each fragment to the source the envelope backs it with.

    A fragment whose ``sourceId`` is missing from the envelope is rendered as
    **unsupported** — the claim is shown, and shown to be unsupported, rather
    than promoted to fact. Nothing is dropped: the reader sees every fragment
    and its provenance status.
    """
    by_id = {str(source.get("id") or ""): source for source in sources}
    rendered = []
    for fragment in fragments:
        text = str(fragment.get("text") or "")
        source_id = str(fragment.get("sourceId") or fragment.get("source") or "")
        source = by_id.get(source_id)
        rendered.append(
            {
                "text": text,
                "sourceId": source_id,
                "supported": source is not None,
                "label": render_source(source) if source else unsupported_label(),
                "source": source or None,
            }
        )
    return rendered


def _replay_messages(conversation: dict[str, Any]) -> list[dict[str, str]]:
    """The upstream history for the next turn (role + text, oldest first)."""
    history = [
        {"role": str(turn["role"]), "content": str(turn.get("text") or "")}
        for turn in conversation["turns"]
        if turn.get("role") in ("user", "assistant") and str(turn.get("text") or "")
        and str(turn.get("state") or TURN_COMPLETE) != TURN_FAILED
    ]
    return history[-MAX_REPLAY_TURNS:]


def _find(store: dict[str, Any], conversation_id: str) -> Optional[dict[str, Any]]:
    for conversation in store.get("conversations", []):
        if conversation.get("id") == conversation_id:
            return conversation
    return None


def _last_tier(conversation: dict[str, Any]) -> str:
    for turn in reversed(conversation["turns"]):
        tier = str(turn.get("tier") or "")
        if tier:
            return tier
    return ""


def _conversation_json(conversation: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": conversation["id"],
        "title": conversation["title"],
        "createdAt": conversation["createdAt"],
        "updatedAt": conversation["updatedAt"],
        "turns": [
            _turn_json(turn) if turn.get("role") == "assistant" else dict(turn)
            for turn in conversation["turns"]
        ],
    }


def _turn_json(turn: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": turn["id"],
        "role": turn["role"],
        "text": turn["text"],
        "tier": turn.get("tier") or "",
        "resolvedModel": turn.get("resolvedModel") or "",
        "state": turn.get("state") or TURN_COMPLETE,
        "createdAt": turn.get("createdAt") or "",
        "finishReason": turn.get("finishReason") or "",
        "retryOf": turn.get("retryOf") or None,
        "grounding": turn.get("grounding")
        or {"state": GROUNDING_NO_DATA, "note": "no grounding envelope was recorded"},
        "sources": turn.get("sources") or [],
        "fragments": turn.get("fragments") or [],
        "degraded": turn.get("degraded") or {"degraded": False},
        "usage": turn.get("usage")
        or {"state": GROUNDING_NO_DATA, "estimatedCostUsd": None, "note": ""},
        "failure": turn.get("failure") or {},
    }


def _number(value: Any) -> Optional[float]:
    """A finite float from an upstream value, or ``None`` (never a fake 0)."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
