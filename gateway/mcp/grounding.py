"""The chat grounding assembler + the citations envelope (issue #504).

EPIC #500 makes the chat turn a **grounded** turn: everything the model is
allowed to assert about the enterprise arrives in the prompt as a cited
fragment, and the turn can be held to exactly what it was given.

Two frozen disciplines shape this module, and it *consumes* both rather than
re-deriving either:

* **ADR-0023 - read, never write.** A turn may read existing authorities;
  an action is only ever an **approval proposal** for the identity lane to
  route (:class:`ApprovalProposal`). No function here writes a file, and the
  family it assembles is read-only by construction.
* **Static-first / delta-last** (``engine/memory/prompt_cache.py``, issue #25).
  Providers cache from token 0, so the grounded block is a *pure function of
  the logical fragment set* - sorted, deduplicated, with no run identity and
  **no timestamps** in the cacheable region. A dynamic value in the static
  prefix is rejected loudly by the engine's own ``strict`` scan rather than
  quietly costing every request a cache miss.

The envelope's job is provenance: every fragment carries the identifier of its
source (a bridge family + revision, a tool-call id, or a ticket id), a
*citation* that cannot name a source the turn did not read is refused, and a
model that cites something it was not given is refused too - the two halves of
"hold the model to what it was given".
"""

from __future__ import annotations

import importlib
import json
import re
import sys
from dataclasses import dataclass
from typing import Any, Dict, FrozenSet, Mapping, Optional, Sequence, Tuple

from .sources import (
    MODE_PRODUCTION,
    STATUS_NO_DATA,
    STATUS_OK,
    TENANT_ARGUMENT_KEYS,
    TENANT_SCOPED_FAMILIES,
    Fragment,
    ReadResult,
    SourceCatalog,
    default_repo_root,
)

# --------------------------------------------------------------------------- #
# the stable platform spec (a constant: never assembled from run state)
# --------------------------------------------------------------------------- #
PLATFORM_SPEC = "\n".join(
    (
        "You are a grounded enterprise assistant for this platform.",
        "Every enterprise fact you state must come from a cited fragment below.",
        "A fragment marked [NO_DATA] means the source was absent; say so.",
        "You may read and propose, never act: an action is an approval proposal.",
        "Cite fragments by their source id; cite nothing you were not given.",
    )
)

#: The closed citation-kind vocabulary (the ticket's three source identifiers).
CITATION_TOOL_CALL = "tool_call"
CITATION_BRIDGE_FAMILY = "bridge_family"
CITATION_TICKET = "ticket"
CITATION_KINDS: Tuple[str, ...] = (
    CITATION_TOOL_CALL,
    CITATION_BRIDGE_FAMILY,
    CITATION_TICKET,
)

#: family -> the citation kind its fragments are identified by.
_CITATION_KIND_BY_FAMILY: Mapping[str, str] = {
    "board": CITATION_TICKET,
    "registry": CITATION_BRIDGE_FAMILY,
    "fleet": CITATION_BRIDGE_FAMILY,
    "kb-fixture": CITATION_BRIDGE_FAMILY,
}

#: Payload keys deliberately kept out of the cacheable prefix. A run timestamp
#: (or a per-call tail) inside a static region invalidates the provider's whole
#: prefix cache - the exact failure ``engine/memory/prompt_cache.py`` exists to
#: prevent - so the *grounding* rendering drops them while the tool result keeps
#: them. Dropping is declared here; anything else that is dynamic is refused.
VOLATILE_KEYS: FrozenSet[str] = frozenset(
    {"ts", "timestamp", "generated_at", "indexedAt", "closed_at", "chainTail"}
)

_DEFAULT_BUDGET_TOKENS = 4000


def citation_kind(family: str) -> str:
    """The citation kind a family's fragments are identified by."""
    return _CITATION_KIND_BY_FAMILY.get(family, CITATION_TOOL_CALL)


class GroundingError(ValueError):
    """The grounding discipline was violated (loud, never silently repaired)."""


class CitationError(GroundingError):
    """A citation names a source the turn did not read (fabricated provenance)."""


# --------------------------------------------------------------------------- #
# the citations envelope
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Citation:
    """One grounded fragment mapped to the real source identifier behind it."""

    source_id: str
    family: str
    authority: str
    revision: str
    kind: str
    call_id: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "sourceId": self.source_id,
            "family": self.family,
            "authority": self.authority,
            "revision": self.revision,
            "kind": self.kind,
            "callId": self.call_id,
        }


@dataclass(frozen=True)
class CitationsEnvelope:
    """The provenance map of one grounded turn.

    ``validate`` takes the *witness* set of source ids the turn actually read,
    so a citation that names anything else is refuted rather than trusted. The
    witness is supplied by the caller on purpose: an envelope that validated
    itself against its own contents would prove nothing.
    """

    citations: Tuple[Citation, ...] = ()

    def source_ids(self) -> Tuple[str, ...]:
        return tuple(citation.source_id for citation in self.citations)

    def validate(self, known_source_ids: Sequence[str]) -> None:
        """Refuse a citation that names a source outside the witness set."""
        known = set(known_source_ids)
        fabricated = sorted(
            citation.source_id
            for citation in self.citations
            if citation.source_id not in known
        )
        if fabricated:
            raise CitationError(
                "citations envelope refuses fabricated provenance: "
                f"{', '.join(fabricated)} name no source this turn read "
                f"(read: {', '.join(sorted(known)) or 'nothing'})"
            )
        if not self.citations and known:
            raise CitationError(
                "citations envelope refuses to drop provenance: "
                f"{len(set(known))} source(s) were read and none is cited"
            )

    def assert_grounded(self, response_source_ids: Sequence[str]) -> None:
        """Refuse a response that cites a source the model was not given."""
        granted = set(self.source_ids())
        invented = sorted({str(value) for value in response_source_ids} - granted)
        if invented:
            raise CitationError(
                "the response cites sources it was not given: "
                f"{', '.join(invented)} (granted: "
                f"{', '.join(sorted(granted)) or 'nothing'})"
            )

    def as_dict(self) -> Dict[str, Any]:
        return {"citations": [citation.as_dict() for citation in self.citations]}


# --------------------------------------------------------------------------- #
# the grounding request
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Need:
    """One thing the turn needs from one declared authority."""

    family: str
    operation: str
    target: str = ""
    arguments: Tuple[Tuple[str, Any], ...] = ()
    call_id: str = ""

    def kwargs(self) -> Dict[str, Any]:
        return {key: value for key, value in self.arguments}

    def sort_key(self) -> Tuple[str, str, str, str]:
        return (
            self.family,
            self.operation,
            self.target,
            json.dumps(self.kwargs(), sort_keys=True, default=str),
        )


@dataclass(frozen=True)
class GroundingRequest:
    """A turn to ground: the user delta plus the authorities it needs."""

    delta: str
    needs: Tuple[Need, ...] = ()
    budget_tokens: int = _DEFAULT_BUDGET_TOKENS

    def ordered_needs(self) -> Tuple[Need, ...]:
        """The needs in the canonical (caller-order-independent) order."""
        return tuple(sorted(self.needs, key=lambda need: need.sort_key()))


@dataclass(frozen=True)
class NoData:
    """One source the turn asked for and did not get, with the reason."""

    family: str
    operation: str
    target: str
    reason: str

    def line(self) -> str:
        return (
            f"family={self.family} operation={self.operation} "
            f"target={self.target} reason={self.reason}"
        )


@dataclass(frozen=True)
class TokenBudget:
    """What the budget was, and what this turn used of it."""

    limit: int
    static_tokens: int
    delta_tokens: int
    total_tokens: int

    @property
    def static_within_budget(self) -> bool:
        return self.static_tokens <= self.limit

    def as_dict(self) -> Dict[str, Any]:
        return {
            "limit": self.limit,
            "staticTokens": self.static_tokens,
            "deltaTokens": self.delta_tokens,
            "totalTokens": self.total_tokens,
            "withinBudget": self.static_within_budget,
        }


@dataclass(frozen=True)
class ApprovalProposal:
    """An action a turn *wants*, expressed as a proposal - never as a write.

    ADR-0023: a turn may read existing authorities and may **propose** writes
    only as approvals. This object is handed to the identity lane, which owns
    routing it; nothing in this package performs it.
    """

    action: str
    target: str
    tenant_id: str
    rationale: str
    requested_by: str
    kind: str = "approval-proposal"

    def as_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "action": self.action,
            "target": self.target,
            "tenantId": self.tenant_id,
            "rationale": self.rationale,
            "requestedBy": self.requested_by,
            "requiresApproval": True,
        }


@dataclass(frozen=True)
class GroundedTurn:
    """The assembled turn: a validated static prefix, a delta, and provenance."""

    static_text: str
    delta_text: str
    fragments: Tuple[Fragment, ...]
    envelope: CitationsEnvelope
    no_data: Tuple[NoData, ...]
    budget: TokenBudget
    cache_footprint: str
    sources_read: Tuple[str, ...]
    sources_skipped: Tuple[str, ...]
    truncated: Tuple[str, ...] = ()
    call_ids: Tuple[str, ...] = ()
    tenant_id: str = ""

    @property
    def status(self) -> str:
        """``NO_DATA`` when the turn got nothing at all (never an empty ok)."""
        return STATUS_OK if self.fragments else STATUS_NO_DATA

    def render(self) -> str:
        """The prompt bytes: static prefix first, delta last (cache-safe)."""
        if not self.static_text:
            return self.delta_text
        if not self.delta_text:
            return self.static_text
        return self.static_text + "\n" + self.delta_text

    def why(self) -> Tuple[str, ...]:
        """Every reason a source contributed nothing (never silently absent)."""
        return tuple(entry.line() for entry in self.no_data)

    def verify_response(self, response_source_ids: Sequence[str]) -> None:
        """Hold the model to what it was given (raises :class:`CitationError`)."""
        self.envelope.assert_grounded(response_source_ids)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "fragments": [fragment.source_id for fragment in self.fragments],
            "citations": self.envelope.as_dict()["citations"],
            "noData": [entry.line() for entry in self.no_data],
            "budget": self.budget.as_dict(),
            "cacheFootprint": self.cache_footprint,
            "sourcesRead": list(self.sources_read),
            "sourcesSkipped": list(self.sources_skipped),
            "truncated": list(self.truncated),
            "callIds": list(self.call_ids),
        }


# --------------------------------------------------------------------------- #
# cache-safe payload rendering
# --------------------------------------------------------------------------- #
_DYNAMIC_RE = re.compile(
    r"\b(?:run_id|run-id|trace_id|trace-id|request_id|request-id|"
    r"session_id|session-id|hostname|host_id|indexed_at|started_at|"
    r"duration_seconds)\b|\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}"
)


def _strip_volatile(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _strip_volatile(item)
            for key, item in value.items()
            if str(key) not in VOLATILE_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [_strip_volatile(item) for item in value]
    return value


def stable_payload(fragment: Fragment) -> Dict[str, Any]:
    """The fragment's cache-stable payload.

    Declared-volatile keys are dropped (see :data:`VOLATILE_KEYS`); anything
    *else* that still looks dynamic is a loud refusal, because silently
    stripping it would hide a source that injects run identity into a turn.
    """
    payload = _strip_volatile(fragment.payload)
    encoded = json.dumps(payload, sort_keys=True, default=str)
    offenders = sorted({match for match in _DYNAMIC_RE.findall(encoded)})
    if offenders:
        raise GroundingError(
            f"fragment {fragment.source_id} carries dynamic tokens that must not "
            f"enter a cacheable region: {', '.join(offenders)}; declare the field "
            f"volatile (grounding.VOLATILE_KEYS) or fix the source"
        )
    return payload


def render_fragment(fragment: Fragment) -> str:
    """One line of the grounded block: the citation first, the fact after it."""
    payload = json.dumps(stable_payload(fragment), sort_keys=True, ensure_ascii=False)
    return f"- source={fragment.source_id} {payload}"


# --------------------------------------------------------------------------- #
# the assembler
# --------------------------------------------------------------------------- #
class GroundingAssembler:
    """Builds a static-first, delta-last, cited prefix for a turn.

    ``tenant_id`` is the **verified session's** tenant. It is injected into the
    reads of :data:`mcp.sources.TENANT_SCOPED_FAMILIES` and can never be named
    by a :class:`Need` - so a turn cannot widen its own scope by asking, exactly
    as a tool argument cannot.
    """

    def __init__(
        self,
        catalog: Optional[SourceCatalog] = None,
        *,
        tenant_id: str = "",
        budget_tokens: int = _DEFAULT_BUDGET_TOKENS,
        spec: str = PLATFORM_SPEC,
        root: Optional[str] = None,
    ) -> None:
        self.catalog = catalog if catalog is not None else SourceCatalog.from_repo_root(
            root or default_repo_root(), mode=MODE_PRODUCTION
        )
        self.tenant_id = tenant_id
        self.budget_tokens = int(budget_tokens)
        self.spec = spec

    # -- consumption seam: the prompt-cache discipline ------------------- #
    @staticmethod
    def _prompt_cache() -> Any:
        try:
            if default_repo_root() not in sys.path:
                sys.path.insert(0, default_repo_root())
            return importlib.import_module("engine.memory.prompt_cache")
        except Exception as exc:
            raise GroundingError(
                "the prompt-cache discipline (engine/memory/prompt_cache.py, "
                f"issue #25) is not consumable from this checkout: {exc}"
            ) from exc

    # -- read scoping ---------------------------------------------------- #
    def _scoped_kwargs(self, need: Need) -> Optional[Dict[str, Any]]:
        """The read arguments for ``need``, with the session tenant injected.

        ``None`` means the need cannot be read honestly (a tenant-scoped read on
        a turn with no verified tenant). A :class:`Need` that tries to name a
        tenant is refused outright - the same refusal a tool argument gets.
        """
        kwargs = need.kwargs()
        offenders = sorted(set(kwargs) & set(TENANT_ARGUMENT_KEYS))
        if offenders:
            raise GroundingError(
                f"the turn's need for {need.family}.{need.operation} names a "
                f"tenant selector ({', '.join(offenders)}): the tenant is taken "
                f"from the verified session and no request may widen it"
            )
        if need.family in TENANT_SCOPED_FAMILIES:
            if not self.tenant_id:
                return None
            kwargs["tenant_id"] = self.tenant_id
        return kwargs

    # -- assembly -------------------------------------------------------- #
    def assemble(self, request: GroundingRequest) -> GroundedTurn:
        """Read what the turn needs and assemble the cited, cache-safe prefix."""
        needs = request.ordered_needs()
        wanted = {need.family for need in needs}
        sources_skipped = tuple(
            family for family in self.catalog.families() if family not in wanted
        )

        fragments: list = []
        no_data: list = []
        call_ids: list = []
        for need in needs:
            if need.call_id:
                call_ids.append(need.call_id)
            kwargs = self._scoped_kwargs(need)
            if kwargs is None:
                no_data.append(
                    NoData(
                        family=need.family,
                        operation=need.operation,
                        target=need.target,
                        reason=(
                            f"the turn has no verified session tenant, so "
                            f"{need.family}.{need.operation} (a tenant-scoped "
                            f"read) is refused rather than run unscoped"
                        ),
                    )
                )
                continue
            result = self.catalog.call(need.family, need.operation, **kwargs)
            if result.is_ok:
                fragments.extend(result.fragments)
            else:
                no_data.append(
                    NoData(
                        family=need.family,
                        operation=need.operation,
                        target=need.target,
                        reason=result.reason,
                    )
                )

        fragments.sort(key=lambda fragment: fragment.source_id)
        deduped: list = []
        seen: set = set()
        for fragment in fragments:
            if fragment.source_id in seen:
                continue
            seen.add(fragment.source_id)
            deduped.append(fragment)

        block, kept, truncated = self._render_block(deduped, no_data)
        prompt_cache = self._prompt_cache()
        prefix = prompt_cache.assemble_prefix(
            system_text=self.spec, memory_block=block, user_delta=request.delta
        )
        call_id_by_family = {
            need.family: need.call_id for need in needs if need.call_id
        }
        envelope = CitationsEnvelope(
            citations=tuple(
                Citation(
                    source_id=fragment.source_id,
                    family=fragment.family,
                    authority=fragment.authority,
                    revision=fragment.revision,
                    kind=citation_kind(fragment.family),
                    call_id=call_id_by_family.get(fragment.family, ""),
                )
                for fragment in kept
            )
        )
        envelope.validate(tuple(fragment.source_id for fragment in kept))
        budget = TokenBudget(
            limit=int(request.budget_tokens or self.budget_tokens),
            static_tokens=prefix.static_tokens,
            delta_tokens=prompt_cache.estimate_tokens(prefix.delta_text),
            total_tokens=prefix.total_tokens,
        )
        return GroundedTurn(
            static_text=prefix.static_text,
            delta_text=prefix.delta_text,
            fragments=tuple(kept),
            envelope=envelope,
            no_data=tuple(no_data),
            budget=budget,
            cache_footprint=prompt_cache.footprint(prefix.static_text),
            sources_read=tuple(sorted(wanted)),
            sources_skipped=sources_skipped,
            truncated=truncated,
            call_ids=tuple(call_ids),
            tenant_id=self.tenant_id,
        )

    # -- rendering ------------------------------------------------------- #
    def _render_block(
        self,
        fragments: Sequence[Fragment],
        no_data: Sequence[NoData],
    ) -> Tuple[str, list, Tuple[str, ...]]:
        """The grounded block, dropped to fit the budget - never silently.

        A fragment that does not fit is *named* in ``truncated`` (and the turn
        reports it); the absence lines are always kept, so a turn can never be
        left with no facts **and** no reason. When the budget admits no fragment
        at all while fragments existed, that is refused loudly rather than
        turned into an unexplained ``NO_DATA``.
        """
        prompt_cache = self._prompt_cache()
        limit = int(self.budget_tokens)
        spec_tokens = prompt_cache.estimate_tokens(self.spec)
        absence = [f"[NO_DATA] {entry.line()}" for entry in no_data]
        lines = ["[grounding] cited fragments:"]
        kept: list = []
        truncated: list = []
        for fragment in fragments:
            line = render_fragment(fragment)
            candidate = lines + [line]
            if spec_tokens + prompt_cache.estimate_tokens("\n".join(candidate)) > limit:
                truncated.append(fragment.source_id)
                continue
            lines = candidate
            kept.append(fragment)
        if fragments and not kept:
            raise GroundingError(
                "the turn's token budget admits no fragment at all "
                f"(budget={limit}); every fragment was dropped: "
                f"{', '.join(sorted(truncated))}"
            )
        block_lines = lines
        if absence:
            block_lines = block_lines + ["[grounding] absent sources:"] + absence
        return "\n".join(block_lines), kept, tuple(sorted(truncated))


def propose_approval(
    turn: GroundedTurn,
    *,
    action: str,
    target: str,
    rationale: str,
    requested_by: str,
) -> ApprovalProposal:
    """Express a wanted action as an approval proposal (never a write)."""
    if not rationale.strip():
        raise GroundingError("an approval proposal requires a rationale")
    return ApprovalProposal(
        action=action,
        target=target,
        tenant_id=turn.tenant_id,
        rationale=rationale,
        requested_by=requested_by,
    )


def read_result_line(result: ReadResult) -> str:
    """A one-line view of a read (used by tests and the gate)."""
    if result.is_ok:
        return f"{STATUS_OK} " + ",".join(result.citations())
    return f"{STATUS_NO_DATA} {result.reason}"
