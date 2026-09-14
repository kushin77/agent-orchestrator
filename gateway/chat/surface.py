"""gateway.chat.surface — the serving surface (issue #503, ADR-0023).

This module is the **mount** the proxy's transport-free handler was waiting for:
``gateway/proxy/handler.py`` says outright that the phase-7 REST surface mounts
its handler behind real HTTP, and this is that mount for the conversational
endpoints.  Nothing here re-implements a model path — a turn is one dispatch
through the merged funnel, so routing, tier choice, caps, budgets, fallback and
the audit/metering record are the already-tested ones.

The order of a turn is the decision, and it is fixed:

```mermaid
flowchart LR
    F{surfaces.chat<br/>flag} -- off --> X[404 feature_disabled<br/>before AuthN]
    F -- on --> A[verify the scoped chat credential<br/>identity/chat]
    A --> C{messages, model claim<br/>grounding, tools}
    C -- malformed --> E[OpenAI error shape<br/>fail closed]
    C -- ok --> G[guardrails/chat<br/>retrieval + egress + policy]
    G -- aborted --> R[403 guardrail_blocked<br/>no model call]
    G -- admitted --> B[telemetry/chat<br/>budget + kill switch]
    B -- refused --> Q[429 budget_blocked<br/>still attributed]
    B -- allowed --> D[gateway/proxy dispatch<br/>ONE dispatch, ONE record]
    D -- not served --> U[proxy outcome -> compatible error]
    D -- served --> I[guardrails/chat<br/>inbound re-validation]
    I -- ungrounded --> N[422 ungrounded_response]
    I -- accepted --> T[telemetry/chat attribution<br/>+ conversation record]
```

Three properties are structural rather than conventional:

* **the flag is checked first** — an unpromoted surface is *absent* (404
  ``feature_disabled``) to an anonymous probe and to a valid credential alike;
* **identity comes from the credential** — a body's ``tenantId``, ``role``,
  ``api_key`` or ``budget`` are recorded as claims and never read as facts, and
  a foreign tenant claim is refused;
* **a refusal is a refusal** — every non-served path answers with the
  OpenAI-compatible error shape and no content, and a turn refused before
  dispatch never reaches a provider.

A turn is prepared in two steps so the streaming endpoints can relay the
dispatch's own incremental stage trace: :meth:`_plan` runs everything up to the
dispatch, and :meth:`_finish` closes the turn (inbound re-validation,
attribution, the conversation record).  Both the single-body and the streaming
endpoints run **the same two steps** — the streaming one simply relays the
proxy's ``dispatch_stream`` seam as it arrives.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterator, Mapping, Optional, Sequence

from . import contract
from .conversation import DEFAULT_HISTORY_TURNS, ConversationStore
from .errors import (
    BudgetRefused,
    ChatSurfaceError,
    CredentialRefused,
    CredentialRequired,
    CrossTenantRefused,
    GroundingUnreadable,
    MalformedRequest,
    ModelNotSelectable,
    TurnRefused,
    UngroundedResponse,
    UnknownModel,
    refusal_for_outcome,
)
from .flags import DEFAULT_REGISTRY_PATH, SURFACE_KEY, require_surface
from .models import ModelCatalogue
from .resolver import (
    TASK_TYPE_ANSWER,
    TASK_TYPE_REFUSE,
    ChatTaskResolver,
    task_type_for,
)

#: Relay slice size for a streamed answer.  The incremental signal of this
#: surface is the **pipeline trace** (one frame per dispatch stage, relayed as
#: the proxy emits it); the answer text itself comes from a synchronous
#: dispatch core, so it is relayed in ordered slices of this size.  Documented,
#: not implied: the concatenation of the slices is exactly the non-streaming
#: body's content.
STREAM_CHUNK_CHARS = 240

#: The two grounding states the extension reports (``registry/chat``'s own
#: vocabulary, mirrored by the portal client).
GROUNDING_OK = "OK"
GROUNDING_NO_DATA = "NO_DATA"

#: The `finish_reason` this surface emits.
FINISH_STOP = "stop"


@dataclass(frozen=True)
class TurnPlan:
    """A turn prepared up to (and not including) the dispatch."""

    turn_id: str
    container: str
    credential: Any
    request: contract.ChatTurnRequest
    tier_claim: str
    task_type: str
    prompt_id: str
    citation_floor: int
    tier: Any
    chat_turn: Any
    budget: Any
    guarded: Any
    envelope: Any
    grounding_prefix: str
    task_request: Any

    @property
    def admitted(self) -> tuple:
        retrieval = getattr(self.guarded, "retrieval", None)
        return tuple(getattr(retrieval, "admitted_ids", ()) or ())

    @property
    def quarantined(self) -> tuple:
        retrieval = getattr(self.guarded, "retrieval", None)
        return tuple(getattr(retrieval, "quarantined_ids", ()) or ())


@dataclass(frozen=True)
class TurnRecord:
    """A closed turn: what it answered, and every authority's verdict on it."""

    plan: TurnPlan
    result: Any
    record: Any
    answer: str
    citations: tuple
    inbound: Any
    attribution: Any
    conversation_key: str

    # -- convenience passthroughs ------------------------------------------ #
    @property
    def turn_id(self) -> str:
        return self.plan.turn_id

    @property
    def credential(self) -> Any:
        return self.plan.credential

    @property
    def tools(self) -> tuple:
        return tuple(self.plan.request.tools)

    @property
    def admitted(self) -> tuple:
        return self.plan.admitted

    @property
    def quarantined(self) -> tuple:
        return self.plan.quarantined

    def to_log(self) -> dict[str, Any]:
        """The conversation-record form: the turn's text and its provenance."""
        return {
            "turn_id": self.turn_id,
            "task_type": self.plan.task_type,
            "prompt_id": self.plan.prompt_id,
            "messages": [
                {"role": role, "content": content}
                for role, content in self.plan.request.messages
            ],
            "answer": self.answer,
            "citations": [dict(citation) for citation in self.citations],
            "grounding": {
                "admitted": list(self.admitted),
                "quarantined": list(self.quarantined),
            },
            "tier": self.plan.tier.to_dict() if self.plan.tier is not None else None,
            "record": self.record.to_dict() if self.record is not None else None,
        }


class ChatSurface:
    """The OpenAI- and Ollama-compatible serving surface.

    Every authority is injected: the ``gateway`` (``gateway/proxy``'s dispatch
    core), the credential verifier (``identity/chat``), the budget guard and
    attributor (``telemetry/chat``), the turn guard (``guardrails/chat``) and
    the conversation store (which rides ``identity/chat``'s isolation onto
    ``engine/memory``).  A surface built with a missing authority *reports* the
    absence rather than guessing: no budget guard means no budget figure, never
    a fabricated ``$0.00``.
    """

    def __init__(
        self,
        gateway: Any,
        *,
        registry_path: Optional[Path] = None,
        catalogue: Optional[ModelCatalogue] = None,
        catalogue_paths: Optional[Mapping[str, Path]] = None,
        credential_verifier: Any = None,
        signing_key: Optional[bytes] = None,
        revocation_store: Any = None,
        conversation: Optional[ConversationStore] = None,
        task_resolver: Optional[ChatTaskResolver] = None,
        turn_guard: Any = None,
        budget_guard: Any = None,
        attributor: Any = None,
        clock: Any = None,
        id_factory: Any = None,
        chunk_chars: int = STREAM_CHUNK_CHARS,
    ) -> None:
        self.gateway = gateway
        self.registry_path = (
            Path(registry_path) if registry_path is not None else DEFAULT_REGISTRY_PATH
        )
        self._catalogue = catalogue
        self._catalogue_paths = dict(catalogue_paths or {})
        self.signing_key = signing_key
        self.revocation_store = revocation_store
        self.credential_verifier = credential_verifier
        self.conversation = (
            conversation if conversation is not None else ConversationStore()
        )
        self.resolver = task_resolver if task_resolver is not None else ChatTaskResolver()
        if turn_guard is None:
            from guardrails.chat.turn import ChatTurnGuard

            turn_guard = ChatTurnGuard()
        self.turn_guard = turn_guard
        self.budget_guard = budget_guard
        self.attributor = attributor
        self._clock = clock or time.time
        self._id_factory = id_factory or (lambda: uuid.uuid4().hex)
        self.chunk_chars = max(1, int(chunk_chars))

    # ------------------------------------------------------------------ #
    # Endpoints
    # ------------------------------------------------------------------ #
    def models(self) -> dict[str, Any]:
        """``GET /v1/models`` — the derived advertisement (flag-gated)."""
        require_surface(self.registry_path, SURFACE_KEY)
        return self.catalogue.to_openai_list()

    def completions(
        self, body: Any, *, token: Optional[str] = None, now: Optional[float] = None
    ) -> dict[str, Any]:
        """``POST /v1/chat/completions`` — one JSON body (``stream: false``)."""
        plan = self._plan(
            body, token=token, now=now, parser=contract.parse_openai_request
        )
        record = self._finish(plan, self._dispatch(plan))
        return contract.completion_body(
            completion_id=self._completion_id(record.turn_id),
            created=int(self._clock()),
            model=self._resolved_model(record),
            content=record.answer,
            finish_reason=FINISH_STOP,
            usage=self._usage(record),
            ao=self._extension(record),
        )

    def completions_stream(
        self, body: Any, *, token: Optional[str] = None, now: Optional[float] = None
    ) -> Iterator[str]:
        """``POST /v1/chat/completions`` with ``stream: true`` (SSE frames).

        Yields one frame per dispatch stage — relayed from the proxy's own
        ``dispatch_stream`` seam as it arrives — then the answer in ordered
        slices, then a terminal frame carrying the full ``ao`` extension, then
        the ``data: [DONE]`` sentinel every compatible client waits for.  A
        refusal raised while preparing the turn (flag, identity, model,
        guardrails, budget) is a terminal error frame followed by the sentinel:
        the stream never ends silently and never carries half an answer.
        """
        try:
            plan = self._plan(
                body, token=token, now=now, parser=contract.parse_openai_request
            )
        except ChatSurfaceError as error:
            yield contract.sse_error(self.error_body(error))
            yield contract.SSE_DONE
            return
        completion_id = self._completion_id(plan.turn_id)
        created = int(self._clock())
        model = self._resolved_model(plan)
        result = None
        for item in self._dispatch_stream(plan):
            if self._is_event(item):
                yield contract.sse_data(
                    contract.delta_frame(
                        completion_id=completion_id,
                        created=created,
                        model=model,
                        ao={"stage": item.to_dict()},
                    )
                )
                continue
            result = item
        if result is None:  # pragma: no cover - the proxy always yields a result
            yield contract.sse_error(
                self.error_body(
                    refusal_for_outcome("failed", "the dispatch produced no result")
                )
            )
            yield contract.SSE_DONE
            return
        try:
            record = self._finish(plan, result)
        except ChatSurfaceError as error:
            yield contract.sse_error(self.error_body(error))
            yield contract.SSE_DONE
            return
        for index, slice_ in enumerate(self._slices(record.answer)):
            yield contract.sse_data(
                contract.delta_frame(
                    completion_id=completion_id,
                    created=created,
                    model=self._resolved_model(record),
                    content=slice_,
                    ao={"delta": {"index": index}} if index == 0 else None,
                )
            )
        yield contract.sse_data(
            contract.delta_frame(
                completion_id=completion_id,
                created=created,
                model=self._resolved_model(record),
                finish_reason=FINISH_STOP,
                ao=self._extension(record),
                usage=self._usage(record),
            )
        )
        yield contract.SSE_DONE

    def ollama_chat(
        self, body: Any, *, token: Optional[str] = None, now: Optional[float] = None
    ) -> dict[str, Any]:
        """``POST /api/chat`` — the Ollama-shaped response (one JSON body)."""
        plan = self._plan(
            body, token=token, now=now, parser=contract.parse_ollama_request
        )
        record = self._finish(plan, self._dispatch(plan))
        return contract.ollama_chat_body(
            model=self._resolved_model(record),
            created_at=self._timestamp(),
            content=record.answer,
            usage=self._usage(record),
            ao=self._extension(record),
        )

    def ollama_chat_stream(
        self, body: Any, *, token: Optional[str] = None, now: Optional[float] = None
    ) -> Iterator[str]:
        """``POST /api/chat`` with ``stream: true`` (newline-delimited JSON)."""
        try:
            plan = self._plan(
                body, token=token, now=now, parser=contract.parse_ollama_request
            )
        except ChatSurfaceError as error:
            yield contract.ndjson(self.error_body(error))
            return
        result = None
        for item in self._dispatch_stream(plan):
            if not self._is_event(item):
                result = item
        model = self._resolved_model(plan)
        created_at = self._timestamp()
        if result is None:  # pragma: no cover - the proxy always yields a result
            yield contract.ndjson(
                self.error_body(
                    refusal_for_outcome("failed", "the dispatch produced no result")
                )
            )
            return
        try:
            record = self._finish(plan, result)
        except ChatSurfaceError as error:
            yield contract.ndjson(self.error_body(error))
            return
        for slice_ in self._slices(record.answer):
            yield contract.ndjson(
                contract.ollama_stream_frame(
                    model=model, created_at=created_at, content=slice_
                )
            )
        final = contract.ollama_stream_frame(
            model=model, created_at=created_at, content="", done=True
        )
        final["done_reason"] = FINISH_STOP
        final["ao"] = self._extension(record)
        yield contract.ndjson(final)

    def error_body(self, error: ChatSurfaceError) -> dict[str, Any]:
        """The serialisable refusal (the compatible shape, plus evidence)."""
        body = error.to_openai_error()
        extra = getattr(error, "extra", None)
        if extra:
            body["ao"] = dict(extra)
        return body

    # ------------------------------------------------------------------ #
    # The turn, in two steps
    # ------------------------------------------------------------------ #
    def _plan(
        self,
        body: Any,
        *,
        token: Optional[str],
        now: Optional[float],
        parser: Any,
    ) -> TurnPlan:
        # 1. the flag, BEFORE AuthN: an unpromoted surface is absent.
        require_surface(self.registry_path, SURFACE_KEY)
        # 2. the request's content (its claims are kept separate).
        request = parser(body)
        # 3. identity, from the verified credential only.
        credential = self._authenticate(token, request, now=now)
        # 4. the model claim must be one of the advertised selectable ids.
        tier_claim = self._resolve_model(request.model_claim)
        turn_id = str(self._id_factory())
        container = self.conversation.container(credential)
        # 5. the grounding hand-off: the assembled block, read but not rewritten.
        envelope, prefix = self._grounding(request)
        # 6. guardrails, before anything reaches a model.
        guarded = self._guard(request, envelope=envelope, prefix=prefix, turn_id=turn_id)
        admitted = tuple(
            getattr(getattr(guarded, "retrieval", None), "admitted_ids", ()) or ()
        )
        task_type = task_type_for(admitted_fragments=len(admitted))
        resolved_task = self.resolver.resolved(task_type)
        # 7. telemetry: the budget guard consults the kill switch first.
        chat_turn = self._chat_turn(
            credential=credential,
            turn_id=turn_id,
            tier_claim=tier_claim,
            task_type=task_type,
            prefix=self._dispatch_prefix(guarded, prefix),
            question=request.user_prompt,
        )
        budget_outcome = self._check_budget(chat_turn)
        task_request = self._task_request(
            credential=credential,
            request=request,
            task_type=task_type,
            turn_id=turn_id,
            guarded=guarded,
            prefix=prefix,
        )
        return TurnPlan(
            turn_id=turn_id,
            container=container,
            credential=credential,
            request=request,
            tier_claim=tier_claim,
            task_type=task_type,
            prompt_id=resolved_task.prompt_id,
            citation_floor=int(getattr(resolved_task, "citation_floor", 0)),
            tier=self._tier_claim_resolution(tier_claim),
            chat_turn=chat_turn,
            budget=budget_outcome,
            guarded=guarded,
            envelope=envelope,
            grounding_prefix=prefix,
            task_request=task_request,
        )

    def _finish(self, plan: TurnPlan, result: Any) -> TurnRecord:
        """Close the turn: the record, the answer, and the two post-checks."""
        record = getattr(result, "record", None)
        if not result.served():
            raise self._dispatch_refusal(plan, result, record)
        tier = self._tier_from_record(record, plan.tier_claim, plan.tier)
        settled = replace(plan, tier=tier)
        answer, citations = self._extract(result.content)
        inbound = self._validate_output(settled, answer, citations)
        attribution = self._attribute(settled, record)
        closed = TurnRecord(
            plan=settled,
            result=result,
            record=record,
            answer=answer,
            citations=citations,
            inbound=inbound,
            attribution=attribution,
            conversation_key="",
        )
        key = self.conversation.append(plan.credential, closed.to_log())
        return replace(closed, conversation_key=key)

    @staticmethod
    def _tier_from_record(record: Any, tier_claim: str, fallback: Any) -> Any:
        """The chooser's routing stamp is the authority; the claim never is."""
        from telemetry.chat.tiering import resolve_turn_tier

        stamped = getattr(record, "tier", None) if record is not None else None
        try:
            return resolve_turn_tier(stamped, client_tier=tier_claim or None)
        except ValueError:
            return fallback

    def _dispatch(self, plan: TurnPlan) -> Any:
        """The single dispatch the ADR requires (one call, one call record)."""
        return self.gateway.dispatch(plan.credential.agent_id, plan.task_request)

    def _dispatch_stream(self, plan: TurnPlan) -> Iterator[Any]:
        """The proxy's streaming seam when it is there; the trace otherwise.

        ``ModelGateway.dispatch_stream`` yields the incremental pipeline events
        and then the terminal result.  A gateway that does not expose it (a
        deliberately tiny double) still has the same trace on the result, so the
        fallback relays that — the frames are identical, only their arrival is
        later.  Either way the proxy's own stream is what is relayed; no second
        model path is opened.
        """
        stream = getattr(self.gateway, "dispatch_stream", None)
        if callable(stream):
            yield from stream(plan.credential.agent_id, plan.task_request)
            return
        result = self.gateway.dispatch(plan.credential.agent_id, plan.task_request)
        for event in getattr(result, "events", ()) or ():
            yield event
        yield result

    def _dispatch_refusal(
        self, plan: TurnPlan, result: Any, record: Any
    ) -> ChatSurfaceError:
        refusal = refusal_for_outcome(
            result.outcome,
            f"the gateway did not serve the turn ({result.outcome}): "
            f"{result.error or 'no detail'}",
        )
        extra: dict[str, Any] = {
            "turnId": plan.turn_id,
            "conversationId": plan.credential.conversation_id,
            "promptModule": plan.task_type,
            "record": record.to_dict() if record is not None else None,
            "claim": {"model": plan.request.model_claim, "honoured": False},
            "verdicts": self._verdicts(plan.guarded),
        }
        if self.attributor is not None and record is not None:
            extra["attribution"] = self._attribute(plan, record).to_dict()
        refusal.extra = extra
        return refusal

    # ------------------------------------------------------------------ #
    # Steps
    # ------------------------------------------------------------------ #
    def _authenticate(
        self,
        token: Optional[str],
        request: contract.ChatTurnRequest,
        *,
        now: Optional[float],
    ) -> Any:
        if not token or not str(token).strip():
            raise CredentialRequired(
                "no chat credential was presented; the conversational surface "
                "has no anonymous mode"
            )
        verifier = self._verifier()
        try:
            credential = verifier(
                str(token), conversation_id=request.conversation_claim or None
            )
        except ChatSurfaceError:
            raise
        except Exception as exc:  # noqa: BLE001 - any failure is a refusal
            raise self._credential_refusal(exc) from exc
        if request.tenant_claim and request.tenant_claim != credential.tenant_id:
            raise CrossTenantRefused(
                f"the request claims tenant {request.tenant_claim!r} but the "
                f"credential is scoped to {credential.tenant_id!r} (a request "
                "never selects a tenant)"
            )
        return credential

    def _verifier(self) -> Any:
        if self.credential_verifier is not None:
            return self.credential_verifier
        if self.signing_key is None or self.revocation_store is None:
            raise CredentialRefused(
                "no credential verifier is configured (a signing key and a "
                "revocation store are both required); refusing the turn"
            )
        from identity.chat.credential import verify_chat_credential

        key = self.signing_key
        store = self.revocation_store

        def _verify(token: str, *, conversation_id: Optional[str] = None) -> Any:
            return verify_chat_credential(
                token, key, revocation_store=store, conversation_id=conversation_id
            )

        return _verify

    @staticmethod
    def _credential_refusal(exc: Exception) -> ChatSurfaceError:
        """Map ``identity/chat``'s refusals onto the compatible taxonomy."""
        from identity.chat import errors as identity_errors

        forbidden = (
            identity_errors.CrossTenantRefused,
            identity_errors.ConversationScopeMismatch,
            identity_errors.InvalidScope,
        )
        unauthenticated = (
            identity_errors.CredentialRequired,
            identity_errors.CredentialKeyMissing,
            identity_errors.FrontDoorRefused,
            identity_errors.InvalidChatCredential,
            identity_errors.ChatCredentialExpired,
            identity_errors.ChatSessionRevoked,
            identity_errors.UnmappedClientIdentity,
            identity_errors.AmbiguousClientIdentity,
        )
        if isinstance(exc, forbidden):
            return CrossTenantRefused(str(exc))
        if isinstance(exc, unauthenticated):
            return CredentialRefused(f"the chat credential was refused: {exc}")
        return CredentialRefused(f"the chat credential could not be verified: {exc}")

    @property
    def catalogue(self) -> ModelCatalogue:
        if self._catalogue is None:
            self._catalogue = ModelCatalogue.discover(**self._catalogue_paths)
        return self._catalogue

    def _resolve_model(self, claim: str) -> str:
        """The selectable tier id a ``model`` field names (fail closed)."""
        catalogue = self.catalogue
        if not claim:
            raise MalformedRequest(
                "'model' is required and must name a selectable tier: "
                + ", ".join(catalogue.selectable_ids),
                param="model",
            )
        entry = catalogue.find(claim)
        if entry is None:
            raise UnknownModel(
                f"model {claim!r} is not advertised by GET /v1/models; "
                "selectable ids: " + ", ".join(catalogue.selectable_ids),
                param="model",
            )
        if not entry["ao"]["selectable"]:
            raise ModelNotSelectable(
                f"model {claim!r} is advertised for discovery only; this surface "
                "selects a tier, not a provider model: "
                + ", ".join(catalogue.selectable_ids),
                param="model",
            )
        return claim

    def _grounding(self, request: contract.ChatTurnRequest) -> tuple[Any, str]:
        """The grounding lane's block: consumed, never rewritten here."""
        block = request.grounding
        if block is None:
            return None, ""
        from guardrails.chat.envelope import GroundingEnvelope, GroundingError

        fragments = block.get("fragments")
        envelope = None
        if fragments is not None:
            try:
                envelope = GroundingEnvelope.from_mapping({"fragments": fragments})
            except GroundingError as exc:
                raise GroundingUnreadable(
                    f"the grounding envelope is not readable: {exc}", param="grounding"
                ) from exc
        prefix = block.get("prefix")
        if prefix is not None and not isinstance(prefix, str):
            raise GroundingUnreadable(
                "the assembled grounding prefix must be a string",
                param="grounding.prefix",
            )
        return envelope, (prefix or "")

    def _guard(
        self,
        request: contract.ChatTurnRequest,
        *,
        envelope: Any,
        prefix: str,
        turn_id: str,
    ) -> Any:
        from guardrails.chat.egress import OutboundTurn

        outbound = OutboundTurn(
            user_prompt=request.user_prompt,
            grounding_prefix=prefix,
            # The scan input: the declarations as text, so an instruction hidden
            # in a tool argument is analysed.  The dispatch carries the tool
            # declarations themselves, unchanged.
            tool_arguments=self._tool_scan_input(request.tools),
        )
        guarded = self.turn_guard.guard_turn(
            outbound, envelope=envelope, turn_id=turn_id
        )
        if getattr(guarded, "aborted", False):
            refusal = TurnRefused(
                "the turn was refused by the guardrails before dispatch: "
                + ", ".join(self._rule_ids(guarded))
            )
            refusal.extra = {
                "turnId": turn_id,
                "decision": str(getattr(guarded.decision, "value", guarded.decision)),
                "verdicts": self._verdicts(guarded),
            }
            raise refusal
        return guarded

    @staticmethod
    def _rule_ids(guarded: Any) -> list[str]:
        ids: list[str] = []
        for outcome in getattr(guarded, "guards", lambda: ())():
            record = outcome.to_dict() if hasattr(outcome, "to_dict") else {}
            if record.get("decision") == "block":
                for finding in record.get("findings") or ():
                    rule_id = finding.get("rule_id") or finding.get("ruleId")
                    if rule_id:
                        ids.append(str(rule_id))
        return ids or ["unnamed"]

    @staticmethod
    def _verdicts(guarded: Any) -> list[dict[str, Any]]:
        return [
            outcome.to_dict() if hasattr(outcome, "to_dict") else dict(outcome)
            for outcome in getattr(guarded, "guards", lambda: ())()
        ]

    @staticmethod
    def _dispatch_prefix(guarded: Any, prefix: str) -> str:
        """The grounding the model is actually given (the guard's own output)."""
        retrieval = getattr(guarded, "retrieval", None)
        if retrieval is not None and retrieval.prompt_prefix:
            return retrieval.prompt_prefix
        return prefix or ""

    @staticmethod
    def _tool_scan_input(tools: Sequence[Mapping[str, Any]]) -> Mapping[str, str]:
        if not tools:
            return {}
        return {"tools": json.dumps([dict(tool) for tool in tools], sort_keys=True)}

    def _chat_turn(
        self,
        *,
        credential: Any,
        turn_id: str,
        tier_claim: str,
        task_type: str,
        prefix: str,
        question: str,
    ) -> Any:
        from telemetry.chat.model import ChatTurn

        return ChatTurn(
            turn_id=turn_id,
            conversation_id=credential.conversation_id,
            tenant_id=credential.tenant_id,
            agent_id=credential.agent_id,
            ts=self._timestamp(),
            client_tier=tier_claim,
            static_prefix=prefix,
            user_delta=question,
            prompt_module=task_type,
        )

    def _check_budget(self, turn: Any) -> Any:
        """Consult the budget rails **before** the model call (kill switch first)."""
        if self.budget_guard is None:
            return None
        outcome = self.budget_guard.check(turn)
        if outcome.allowed:
            return outcome
        refusal = BudgetRefused(
            f"the turn was refused before the model call: {outcome.code} "
            f"({outcome.reason})"
        )
        extra: dict[str, Any] = {"budget": outcome.to_dict()}
        if self.attributor is not None:
            attribution = self.attributor.attribute_refusal(
                turn,
                outcome=outcome.outcome or "blocked",
                decision=outcome.decision,
                code=outcome.code,
                reason=outcome.reason,
            )
            extra["attribution"] = attribution.to_dict()
        refusal.extra = extra
        raise refusal

    def _task_request(
        self,
        *,
        credential: Any,
        request: contract.ChatTurnRequest,
        task_type: str,
        turn_id: str,
        guarded: Any,
        prefix: str,
    ) -> Any:
        from proxy.model import TaskRequest

        fragments = self._dispatch_prefix(guarded, prefix)
        variables: dict[str, Any] = {
            "tenant": credential.tenant_id,
            "question": request.user_prompt,
        }
        if task_type == TASK_TYPE_ANSWER:
            variables["fragments"] = fragments
        elif task_type == TASK_TYPE_REFUSE:
            variables["reason"] = self._refusal_reason(guarded, request)
        return TaskRequest(
            tenant_id=credential.tenant_id,
            task_type=task_type,
            input=variables,
            stream=request.stream,
            request_id=turn_id,
            metadata={
                "conversationId": credential.conversation_id,
                "promptModule": task_type,
                # The tool declarations ride through **unchanged** (same objects).
                "tools": request.tools,
                "groundingPrefix": prefix,
                "claims": dict(request.ignored_claims),
            },
        )

    @staticmethod
    def _refusal_reason(guarded: Any, request: contract.ChatTurnRequest) -> str:
        retrieval = getattr(guarded, "retrieval", None)
        if retrieval is not None and getattr(retrieval, "quarantined_ids", ()):
            return (
                "a supplied fragment was quarantined by the retrieval-injection "
                "guard, so the turn is refused rather than answered around it"
            )
        if not request.grounding:
            return "the turn supplied no grounding material to answer from"
        return "none of the supplied fragments answers the question"

    def _validate_output(
        self,
        plan: TurnPlan,
        answer: str,
        citations: Sequence[Mapping[str, Any]],
    ) -> Any:
        """Re-validate the answer; an ungrounded answer is refused, not returned."""
        from guardrails.chat.inbound import Claim, ModelOutput

        claims = tuple(
            Claim(
                text=str(citation.get("claim") or answer)[:240],
                source_id=str(citation.get("source_id") or ""),
            )
            for citation in citations
        )
        inbound = self.turn_guard.validate_output(
            plan.guarded,
            ModelOutput(text=answer, claims=claims),
            envelope=plan.envelope,
        )
        floor = plan.citation_floor
        if not inbound.accepted or (floor > 0 and len(citations) < floor):
            refusal = UngroundedResponse(
                "the model's answer did not survive inbound re-validation "
                f"(citation floor {floor}, cited {len(citations)})"
            )
            refusal.extra = {
                "turnId": plan.turn_id,
                "inbound": inbound.to_dict(),
                "citationFloor": floor,
                "citations": len(citations),
            }
            raise refusal
        return inbound

    def _attribute(self, plan: TurnPlan, record: Any) -> Any:
        """One attribution per turn — served, or refused by the authority.

        A dispatch that did not serve (a denied capability, an exhausted chain)
        carries no routing stamp, and ``telemetry/chat`` refuses to invent one
        from the client's claim (``CHAT-UNRESOLVED-TIER``).  That is the right
        refusal, so the turn is attributed as a **refusal** instead: metered,
        audited, and never billed — the honest row for spend that never
        happened, rather than an attribution built on an assumed tier.
        """
        if self.attributor is None:
            return None
        decision = "allow" if plan.budget is None else plan.budget.decision
        try:
            return self.attributor.attribute(plan.chat_turn, record, decision=decision)
        except Exception as exc:  # noqa: BLE001 - an unattributable turn is a refusal
            code = getattr(exc, "code", None) or type(exc).__name__
            return self.attributor.attribute_refusal(
                plan.chat_turn,
                outcome=str(getattr(record, "outcome", "failed") or "failed"),
                decision="block",
                code=str(code),
                reason=str(exc),
                model=getattr(record, "model", None),
                tier=getattr(record, "tier", None),
            )

    @staticmethod
    def _tier_claim_resolution(tier_claim: str) -> Any:
        """A claim is recorded and never honoured — resolved before the stamp."""
        from telemetry.chat.tiering import TIER_SOURCE_NONE, TierResolution

        return TierResolution(
            tier=None,
            source=TIER_SOURCE_NONE,
            client_tier=tier_claim or None,
            claim_honoured=False,
            claim_agrees=False,
            reason=(
                "the tier is the chooser's routing stamp, resolved from the "
                "gateway call record after dispatch"
            ),
        )

    # ------------------------------------------------------------------ #
    # Rendering helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _extract(content: Any) -> tuple[str, tuple[dict[str, Any], ...]]:
        """The answer text and its citations, taken from the module's own output."""
        if isinstance(content, Mapping):
            answer = content.get("answer")
            if not isinstance(answer, str) or not answer.strip():
                reason = content.get("reason")
                outcome = content.get("outcome")
                answer = (
                    f"{outcome}: {reason}"
                    if isinstance(outcome, str) and isinstance(reason, str)
                    else str(reason or "")
                )
            citations = tuple(
                dict(citation)
                for citation in (content.get("citations") or ())
                if isinstance(citation, Mapping)
            )
            return answer, citations
        if isinstance(content, str):
            return content, ()
        if content is None:
            return "", ()
        return json.dumps(content, sort_keys=True), ()

    def _slices(self, text: str) -> tuple[str, ...]:
        if not text:
            return ("",)
        return tuple(
            text[index : index + self.chunk_chars]
            for index in range(0, len(text), self.chunk_chars)
        )

    @staticmethod
    def _is_event(item: Any) -> bool:
        """A stream item is a dispatch event, not the terminal result."""
        return type(item).__name__ == "DispatchEvent" and hasattr(item, "stage")

    @staticmethod
    def _events(record: TurnRecord) -> tuple[Any, ...]:
        return tuple(getattr(record.result, "events", ()) or ())

    @staticmethod
    def _resolved_model(record: Any) -> str:
        result = getattr(record, "result", None)
        value = getattr(result, "model", None) if result is not None else None
        if value:
            return str(value)
        call = getattr(record, "record", None)
        stamp = getattr(call, "model", None) if call is not None else None
        if stamp:
            return str(stamp)
        plan = getattr(record, "plan", record)
        return str(getattr(plan, "tier_claim", "") or "")

    @staticmethod
    def _usage(record: TurnRecord) -> dict[str, int]:
        result = record.result
        prompt = int(getattr(result, "input_tokens", 0) or 0)
        completion = int(getattr(result, "output_tokens", 0) or 0)
        return {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": prompt + completion,
        }

    def _completion_id(self, turn_id: str) -> str:
        return f"{contract.ID_PREFIX}{turn_id[:24]}"

    def _timestamp(self) -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self._clock()))

    def _citation_sources(
        self, citations: Sequence[Mapping[str, Any]]
    ) -> list[dict[str, Any]]:
        """One source per cited source_id — derived from the id, never invented."""
        sources: list[dict[str, Any]] = []
        seen: set[str] = set()
        for citation in citations:
            source_id = str(citation.get("source_id") or "")
            if not source_id or source_id in seen:
                continue
            seen.add(source_id)
            family, _, _rest = source_id.partition(":")
            source: dict[str, Any] = {
                "id": source_id,
                "kind": family or "source",
                "label": source_id,
            }
            revision = citation.get("revision")
            if isinstance(revision, str) and revision:
                source["revision"] = revision
            sources.append(source)
        return sources

    def _admitted_texts(self, plan: TurnPlan) -> dict[str, str]:
        """The admitted fragment's own text, by source id (what the model saw)."""
        retrieval = getattr(plan.guarded, "retrieval", None)
        texts: dict[str, str] = {}
        for fragment in getattr(retrieval, "fragments", ()) or ():
            if getattr(fragment, "admitted", False):
                texts[str(fragment.source_id)] = str(getattr(fragment, "text", ""))
        return texts

    def _fragment_views(
        self, plan: TurnPlan, citations: Sequence[Mapping[str, Any]]
    ) -> list[dict[str, Any]]:
        """Each citation, joined to the admitted fragment it cites.

        The consumer (``portal/server/chat.py``) reads ``sourceId`` and ``text``
        off a fragment and marks the claim *unsupported* when the envelope does
        not back it.  Both keys are filled from the fragment the turn actually
        admitted, so the label is a fact of the turn rather than a rendering
        choice — and a citation with no admitted fragment keeps no text, which is
        what makes it visibly unsupported.
        """
        admitted = self._admitted_texts(plan)
        views: list[dict[str, Any]] = []
        for citation in citations:
            source_id = str(citation.get("source_id") or "")
            view: dict[str, Any] = {
                "fragmentId": str(citation.get("fragment_id") or ""),
                "sourceId": source_id,
                "text": admitted.get(source_id, ""),
            }
            for key in ("revision", "claim"):
                if citation.get(key):
                    view[key] = citation[key]
            views.append(view)
        return views

    def _extension(self, record: TurnRecord) -> dict[str, Any]:
        """The ``ao`` extension: the platform's own facts, additive only."""
        plan = record.plan
        admitted = list(record.admitted)
        citations = [dict(citation) for citation in record.citations]
        grounded = bool(admitted) and bool(citations)
        extension: dict[str, Any] = {
            "turnId": record.turn_id,
            "container": plan.container,
            "conversationId": plan.credential.conversation_id,
            "tenantId": plan.credential.tenant_id,
            "agentId": plan.credential.agent_id,
            "promptId": plan.prompt_id,
            "promptModule": plan.task_type,
            "tier": {
                "requested": plan.tier_claim,
                "resolved": getattr(plan.tier, "tier", None),
                "resolvedModel": self._resolved_model(record),
                "claimHonoured": getattr(plan.tier, "claim_honoured", False),
                "source": getattr(plan.tier, "source", ""),
                "reason": getattr(plan.tier, "reason", ""),
            },
            "degraded": self._degradation(record),
            "grounding": {
                "state": GROUNDING_OK if grounded else GROUNDING_NO_DATA,
                "admitted": admitted,
                "quarantined": list(record.quarantined),
                "note": (
                    f"{len(admitted)} admitted fragment(s); "
                    f"{len(citations)} citation(s)"
                    if grounded
                    else "the turn carried no citation backed by the admitted grounding"
                ),
            },
            "citations": {
                "sources": self._citation_sources(citations),
                # The compatible view: the consumer's own vocabulary (`sourceId`,
                # `text`), with the text being exactly the fragment the model was
                # given — never a re-rendering of it.  The model's raw envelope
                # rides beside it, verbatim.
                "fragments": self._fragment_views(plan, citations),
                "envelope": citations,
            },
            "usage": {
                "estimatedCostUsd": getattr(record.record, "estimated_cost_usd", None),
                "latencyMs": getattr(record.record, "latency_ms", None),
                "cacheHitShare": getattr(record.attribution, "cache_hit_share", None)
                if record.attribution is not None
                else None,
            },
            "budget": (
                plan.budget.to_dict()
                if plan.budget is not None
                else {"state": "not_wired"}
            ),
            "attribution": (
                record.attribution.to_dict()
                if record.attribution is not None
                else {"state": "not_wired"}
            ),
            "verdicts": self._verdicts(plan.guarded),
            "inbound": record.inbound.to_dict(),
            "claim": {
                "model": plan.request.model_claim,
                "honoured": False,
                "ignored": sorted(plan.request.ignored_claims),
            },
            "dispatch": {
                "record": record.record.to_dict() if record.record is not None else None,
                "stages": [event.to_dict() for event in self._events(record)],
                "toolsSupplied": len(record.tools),
                "declaresTools": False,
            },
            "conversationKey": record.conversation_key,
        }
        if plan.request.grounding:
            # The hand-off is disclosed, not re-decided: what the grounding lane
            # assembled, and nothing this surface added.
            retrieval = getattr(plan.guarded, "retrieval", None)
            extension["groundingHandoff"] = {
                "suppliedPrefix": plan.grounding_prefix,
                "admitted": admitted,
                "fragments": list(getattr(retrieval, "admitted_ids", ()) or ()),
                "toolDeclarations": len(record.tools),
                "declaresTools": False,
            }
        return extension

    def _degradation(self, record: TurnRecord) -> dict[str, Any]:
        """Whether the answer came from a tier other than the one requested."""
        resolved = getattr(record.plan.tier, "tier", None)
        claim = record.plan.tier_claim
        if resolved is None or resolved == claim:
            return {"degraded": False}
        return {
            "degraded": True,
            "reason": "the chooser resolved a different tier than the one requested",
            "fromTier": claim,
            "toTier": resolved,
        }

    # ------------------------------------------------------------------ #
    # Composition helpers (documented, not decorative)
    # ------------------------------------------------------------------ #
    def guarded_runner(self) -> Any:
        """The fused guard+attribute runner over this surface's own rails.

        ``telemetry/chat`` ships ``GuardedTurnRunner`` for callers that want the
        budget check and the attribution fused around a provider call.  This
        surface performs the same two steps explicitly because it needs the
        dispatch *result* itself (the answer, the citations, the inbound
        validation and the conversation record), and a fused runner returns only
        the attribution — so the helper is offered for parity rather than
        re-implemented, and a test pins the two compositions to the same verdict.
        """
        from telemetry.chat.budget_guard import GuardedTurnRunner

        if self.budget_guard is None or self.attributor is None:
            raise ValueError(
                "guarded_runner() needs both a budget guard and an attributor"
            )
        return GuardedTurnRunner(self.budget_guard, self.attributor)


def history_for(
    surface: ChatSurface,
    credential: Any,
    *,
    limit: int = DEFAULT_HISTORY_TURNS,
) -> list[dict[str, Any]]:
    """Convenience: the conversation's own stored turns (scoped by credential)."""
    return surface.conversation.history(credential, limit=limit)
