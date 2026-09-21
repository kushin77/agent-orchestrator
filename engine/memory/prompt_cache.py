"""engine/memory - prompt-cache compatibility for injected memory.

Issue kushin77/agent-orchestrator#25, acceptance criterion "Prompt-cache
compatibility so memory doesn't defeat provider caching".

Providers (Anthropic ``cache_control``, DeepSeek automatic prefix caching)
cache **from token 0**: one stray space, one reordered block, or one embedded
run timestamp before the breakpoint invalidates the whole prefix. Memory
injection defeats caching when the injected block is non-deterministic or
sits ahead of the stable system spec. This module makes the injected memory
block a *pure function of the logical memory set* and enforces the
static-first / delta-last block discipline lifted from
``codeidx/docs/prompt-cache.md`` (see provenance in ``README.md``):

1. ``render_memory_block`` - deterministic, byte-stable serialization of a
   retrieved hit set. Entries are sorted by (scope, key), whitespace is
   presentation, and **none** of the entry's own run metadata (created_at /
   updated_at / last_accessed / container ids) is emitted - those are the
   exact tokens that would silently kill the cache.
2. ``scan_dynamic`` / ``validate_static_region`` - reject run/request/session
   identity markers and timestamps from a static region. Rejection is loud by
   design (a false negative silently kills the cache; a false positive is
   visible and fixable) - the codeidx no-false-green stance.
3. ``assemble_prefix`` - place the memory context after the stable system
   block and before the user delta, so the cacheable prefix stays intact and
   only the delta varies per request.
4. ``ContextPack`` - the *consumption seam* for a pre-fetched
   ``codeidx.context-pack/v1`` block. Alongside adapting the harvested
   pattern (above), this module is now a **consumer** of the published vendor
   contract: ``assemble_prefix`` places a pre-fetched pack as, or ahead of,
   the locally-derived memory block, so two independently-derived static
   regions collapse onto **one** shared, cacheable prefix per tenant/repo.
   The pack is consumed as opaque bytes and never re-derived - the ADR-0018
   no-re-derivation rule: a shape that exists in a published contract is
   *consumed, never mirrored*.

---knowledge---
module_id: engine.memory.prompt_cache
system: engine
app: memory
solution_class: enterprise
patterns: [content-digest, fail-closed]
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [estimate_tokens, scan_dynamic, contains_dynamic, PrefixError, render_memory_block, footprint, validate_block_order, split_static_dynamic, (+5 more)]
invariants: "a block that contains dynamic content is refused as a cache prefix, never silently accepted"
gotchas: ""
related: []
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import List, Sequence

from .model import MemoryKind, normalize_text

# ~4 characters per token - the same deterministic local approximation the
# harvested codeidx prompt-cache spec uses for budgeting/padding decisions.
# It is not a provider tokenizer; M-adapters substitute the real tokenizer.
_CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    """Deterministic token-count estimate (never 0 for non-empty text)."""
    length = len(text or "")
    if length == 0:
        return 0
    return max(1, length // _CHARS_PER_TOKEN)


# --------------------------------------------------------------------------- #
# dynamic-token discipline (rejection, not stripping)
# --------------------------------------------------------------------------- #

#: Run/transport/session identity markers that must never appear in a static
#: (cacheable) region - derived from the codeidx anti-pattern table.
_DYNAMIC_KEY_RE = re.compile(
    r"\b(?:run_id|run-id|trace_id|trace-id|request_id|request-id|"
    r"session_id|session-id|hostname|host_id|indexed_at|started_at|"
    r"duration_seconds)\b"
)
_ISO_TS_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")
_UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)


def scan_dynamic(text: str) -> List[str]:
    """Return the dynamic tokens found in ``text`` (deduplicated, ordered)."""
    found: List[str] = []
    for match in _DYNAMIC_KEY_RE.findall(text or ""):
        if match not in found:
            found.append(match)
    for match in _UUID_RE.findall(text or ""):
        if match not in found:
            found.append(match)
    for match in _ISO_TS_RE.findall(text or ""):
        snippet = match
        if snippet not in found:
            found.append(snippet)
    return found


def contains_dynamic(text: str) -> bool:
    return bool(scan_dynamic(text))


class PrefixError(ValueError):
    """The static-first / delta-last prefix discipline was violated."""


# --------------------------------------------------------------------------- #
# deterministic memory block rendering
# --------------------------------------------------------------------------- #


def render_memory_block(entries: Sequence, *, header: str = "") -> str:
    """Byte-stable rendering of a logical memory set.

    ``entries`` is any sequence of objects exposing ``scope`` (MemoryScope),
    ``kind`` (MemoryKind), ``key`` (str) and ``text`` (str) - the ``Hit`` and
    ``MemoryEntry`` shapes both qualify. The output is a pure function of the
    set:

    * sorted by (scope.rank, key, text) - caller/query presentation order is
      not content, so two queries returning the same memories inject the same
      bytes;
    * only scope/kind/key/text are emitted - never created_at, updated_at,
      last_accessed, or any container/run id (the cache-killing tokens);
    * whitespace is collapsed (normalize_text), so formatting drift cannot
      change the cached bytes.

    An empty set renders to ``""`` (callers skip injection entirely).
    """

    ordered = sorted(
        (e for e in entries if e is not None),
        key=lambda e: (e.scope.rank, str(e.key), str(e.text)),
    )
    lines = []
    for entry in ordered:
        kind = entry.kind.value if isinstance(entry.kind, MemoryKind) else "memory"
        text = normalize_text(str(entry.text))
        key = normalize_text(str(entry.key))
        lines.append("- [{}:{}] {}: {}".format(
            entry.scope.value, kind, key, text
        ))
    body = "\n".join(lines) if lines else ""
    if not body:
        return ""
    if header:
        return normalize_text(header) + "\n" + body
    return body


def footprint(text: str) -> str:
    """sha256 of the block bytes - the cache/stability fingerprint."""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# block ordering + prefix assembly (static first, delta last)
# --------------------------------------------------------------------------- #

_SYSTEM_ROLES = frozenset({"system", "context"})
_DELTA_ROLES = frozenset({"user", "delta"})


def validate_block_order(blocks: Sequence[tuple]) -> None:
    """Enforce ``(system | context)* (user)*``.

    Once a user/delta block appears, nothing else may follow; a leading delta
    or a static block sandwiched after a delta is a violation.
    """
    seen_delta = False
    for role, _text in blocks:
        role = role.lower()
        if role in _DELTA_ROLES:
            seen_delta = True
            continue
        if role not in _SYSTEM_ROLES:
            raise PrefixError(f"unknown block role: {role!r}")
        if seen_delta:
            raise PrefixError(
                f"static block ({role!r}) appears after a delta block"
            )


def split_static_dynamic(blocks: Sequence[tuple]):
    """Return ``(static_blocks, dynamic_blocks)`` split at the first delta."""
    validate_block_order(blocks)
    static: List[tuple] = []
    dynamic: List[tuple] = []
    for block in blocks:
        if block[0].lower() in _DELTA_ROLES:
            dynamic.append(block)
        else:
            static.append(block)
    return static, dynamic


def validate_static_region(text: str) -> None:
    """Raise ``PrefixError`` if dynamic tokens sit in a cacheable region."""
    hits = scan_dynamic(text)
    if hits:
        raise PrefixError(
            "dynamic tokens must not appear in the static (cacheable) "
            f"region; found: {', '.join(sorted(set(hits)))}"
        )


@dataclass(frozen=True)
class Prefix:
    """A validated static-first prompt: cacheable prefix + user delta."""

    static_text: str
    delta_text: str

    def render(self) -> str:
        if not self.static_text:
            return self.delta_text or ""
        if not self.delta_text:
            return self.static_text or ""
        return self.static_text + "\n" + self.delta_text

    @property
    def static_tokens(self) -> int:
        return estimate_tokens(self.static_text)

    @property
    def total_tokens(self) -> int:
        return estimate_tokens(self.render())


#: Fixed, marker-free paragraphs used only to pad a short static region. Pure
#: function of the input (cycled deterministically), so padding never drifts.
_PAD_PARAGRAPHS = (
    "Memory is scoped knowledge from this tenant's agent org.",
    "Retrieved memories are ordered and deduplicated deterministically.",
    "Only entries above the relevance threshold are injected.",
)


def _pad_static(text: str, min_tokens: int) -> str:
    if estimate_tokens(text) >= min_tokens:
        return text
    index = 0
    parts = [text] if text else []
    while estimate_tokens("\n".join(parts)) < min_tokens:
        parts.append(_PAD_PARAGRAPHS[index % len(_PAD_PARAGRAPHS)])
        index += 1
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# pre-fetched context-pack consumption (the codeidx contract seam)
# --------------------------------------------------------------------------- #

#: The published contract this module consumes. ``codeidx.context-pack/v1``
#: is the *vendor's* schema id - quoted from `kushin77/code-indexing`'s
#: context-pack story (#106) - not a shape invented here. The machine-readable
#: envelope is defined by the vendor's consumption contract, which is still
#: OPEN; until it lands this module ships the seam below plus a recorded
#: fixture and marks the consumption row ``UNVERIFIED`` in
#: :data:`CONSUMED_CONTRACTS` rather than guessing a schema.
CONTEXT_PACK_SCHEMA = "codeidx.context-pack/v1"

#: Consumed-contract register (ADR-0018 decision 3 - a shape that exists in a
#: published contract is *consumed, never mirrored*). Each row names the
#: producer, the published contract, and whether this consumer's use of it has
#: been verified against a landed contract. ``UNVERIFIED`` is an honest state:
#: the seam and its fixture exist, but the vendor has not published the
#: contract, so nothing here claims a shape.
CONSUMED_CONTRACTS = (
    {
        "id": CONTEXT_PACK_SCHEMA,
        "producer": "kushin77/code-indexing",
        "contract": "kushin77/code-indexing#128",
        "state": "UNVERIFIED",
        "note": (
            "Seam + fixture shipped; the vendor consumption contract is still "
            "open, so this consumer models no field of the pack - it consumes "
            "the bytes verbatim and checks only the declared schema id."
        ),
    },
)

#: Schema ids this consumer knows. A pack declaring anything else is refused
#: by name rather than silently consumed under a shape it does not match.
_KNOWN_CONTEXT_PACK_SCHEMAS = frozenset({CONTEXT_PACK_SCHEMA})


class ContextPackError(PrefixError):
    """A pre-fetched context pack cannot be consumed as a static block."""


@dataclass(frozen=True)
class ContextPack:
    """A pre-fetched ``codeidx.context-pack/v1`` block, at the seam.

    This is the **seam**, not a mirror of the vendor's contract (ADR-0018).
    Exactly two things are interpreted:

    * ``schema`` - the *declared* contract id, checked against
      :data:`_KNOWN_CONTEXT_PACK_SCHEMAS` so a pack built for an unknown
      version is refused rather than consumed under a shape it may not match;
    * the payload's validity *as text* - it must decode as UTF-8 and be
      non-empty.

    Nothing else about the pack is parsed, modelled or re-derived: the bytes
    are the consumer's input and are placed into the static region verbatim,
    so every consumer embedding the same pack shares the same provider prefix.
    """

    schema: str
    payload: bytes

    def __post_init__(self) -> None:
        if not isinstance(self.schema, str) or not self.schema:
            raise ContextPackError(
                "context pack must declare a schema id; got {!r}".format(
                    self.schema))
        if self.schema not in _KNOWN_CONTEXT_PACK_SCHEMAS:
            raise ContextPackError(
                "unknown context-pack schema {!r}; this consumer knows: {}"
                .format(self.schema,
                        ", ".join(sorted(_KNOWN_CONTEXT_PACK_SCHEMAS))))
        if not isinstance(self.payload, (bytes, bytearray, memoryview)):
            raise ContextPackError(
                "malformed context pack: payload must be bytes, got {}".format(
                    type(self.payload).__name__))
        try:
            bytes(self.payload).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ContextPackError(
                "malformed context pack: payload is not UTF-8 text ({})"
                .format(exc)) from None
        if not bytes(self.payload).decode("utf-8").strip():
            raise ContextPackError(
                "empty context pack: schema {!r} declares no bytes; an empty "
                "pack is a producer fault, not an absent one - pass "
                "context_pack=None to mean absent".format(self.schema))

    @property
    def text(self) -> str:
        """The pack bytes decoded verbatim (never normalised, never parsed)."""
        return bytes(self.payload).decode("utf-8")


def assemble_prefix(system_text: str, memory_block: str,
                    user_delta: str, *, min_static_tokens: int = 0,
                    strict: bool = True,
                    context_pack: ContextPack | None = None) -> Prefix:
    """Assemble ``system [+ pack] + memory context`` (static) before ``user
    delta``.

    The memory block is deterministic (``render_memory_block``), so for a
    given logical memory set the static prefix is byte-identical across
    requests - the provider's prefix cache keeps hitting. With ``strict`` the
    combined static region is scanned for dynamic tokens and rejected loudly.

    ``context_pack`` is an optional **pre-fetched**
    ``codeidx.context-pack/v1`` block (see :class:`ContextPack`). When supplied
    it is placed immediately after the stable system block and **ahead of**
    the locally-derived memory block, so the two independently-derived static
    regions collapse onto ONE shared, cacheable prefix: the pack carries the
    same bytes for every consumer of a given tenant/repo, which is what lets
    them share a single provider cache entry. The pack bytes are consumed
    verbatim and are scanned by the same anti-pattern list as the rest of the
    static region.

    With ``context_pack=None`` (the default) the assembly is **byte-identical**
    to the pre-consumer behaviour - asserted against the recorded vector in
    ``tests/fixtures/prompt_prefix_vector.json`` - because a silent change to
    the injected prefix would invalidate every existing cache entry.

    Raises ``PrefixError`` (and its ``ContextPackError`` subclass) on a
    discipline violation.
    """

    blocks = [("system", system_text or "")]
    if context_pack is not None:
        blocks.append(("context", context_pack.text))
    blocks.append(("context", memory_block or ""))
    blocks.append(("user", user_delta or ""))
    validate_block_order(blocks)
    static_blocks, dynamic_blocks = split_static_dynamic(blocks)
    static_text = "\n\n".join(t for _r, t in static_blocks if t).strip()
    delta_text = "\n".join(t for _r, t in dynamic_blocks if t).strip()
    if strict and static_text:
        validate_static_region(static_text)
    if min_static_tokens > 0:
        static_text = _pad_static(static_text, min_static_tokens)
    return Prefix(static_text=static_text, delta_text=delta_text)
