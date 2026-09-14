#!/usr/bin/env python3
"""DeepSeek prefix-template contract (PF-5, issue #670).

Maximises ``prompt_cache_hit_tokens`` by making the cacheable prefix of every
DeepSeek call a pure function of a canonical template. DeepSeek's automatic
prefix cache keys from token 0: one stray id, one embedded timestamp, or one
reordered block before the user delta invalidates the whole prefix and turns a
hit into a miss. This module makes that prefix byte-stable.

It owns three things:

1. ``PREFIX_TEMPLATES`` — the registry: one canonical, already-normalised
   template per call class (``system-head``, ``tool-schema-head``,
   ``memory-head``, ``instructions-head``, ``user-head``).
2. ``normalize`` — strips volatile tokens (ids, timestamps) from a composed
   prompt while keeping the cache-key-relevant prefix byte-identical.
3. ``validate_prefix`` — refuses a composed prompt whose normalised prefix
   deviates from its class's canonical template, naming the class.

The discipline mirrors ``engine/memory/prompt_cache.py`` (static-first /
delta-last; dynamic tokens kill the cache), but is scoped to the gateway
finops lane and the DeepSeek request shape: the system head, tool-schema head,
memory head and instructions head are the static, cacheable prefix, and only
the user message varies per call.

Standalone module (stdlib only); no cross-package imports. The pytest
bootstrap (``tests/conftest.py``) inserts this directory on ``sys.path`` so the
module imports plainly.
"""

from __future__ import annotations

import hashlib
import re
from typing import Dict, FrozenSet


class PrefixTemplateError(ValueError):
    """A composed prompt's canonical prefix deviates from its class template."""


# --- canonical prefix templates (already normalised: placeholders, not ids) --

PREFIX_TEMPLATES: Dict[str, str] = {
    "system-head": (
        "You are an enterprise AI-agent orchestration assistant.\n"
        "Session <uuid> started at <timestamp>.\n"
        "Respond concisely and accurately."
    ),
    "tool-schema-head": (
        "Tools available to this agent:\n"
        "- read_file(path: str) -> str\n"
        "- list_dir(path: str) -> list[str]\n"
        "- run_command(cmd: str) -> str"
    ),
    "memory-head": (
        "Memory context (scoped to this tenant's agent org):\n"
        "agent_id=<dynamic> | retrieved_at=<timestamp>"
    ),
    "instructions-head": (
        "Follow these instructions precisely:\n"
        "1. Stay in your lane.\n"
        "2. Verify before you declare done."
    ),
    "user-head": "User request:\n",
}

REGISTERED_CLASSES: FrozenSet[str] = frozenset(PREFIX_TEMPLATES)


# --- volatile-token normalisation ------------------------------------------
# Each family maps to one stable placeholder, so two prompts that differ only
# in a volatile token normalise to identical bytes.

_UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
_ISO_TS_RE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?"
    r"(?:Z|[+-]\d{2}:?\d{2})?)?\b"
)
_ID_KEY_RE = re.compile(
    r"\b(run_id|run-id|trace_id|trace-id|request_id|request-id|"
    r"session_id|session-id|agent_id|agent-id|tenant_id|tenant-id|"
    r"hostname|host_id|host-id)\s*[:=]\s*\S+"
)

_UUID_PLACEHOLDER = "<uuid>"
_TIMESTAMP_PLACEHOLDER = "<timestamp>"
_DYNAMIC_PLACEHOLDER = "<dynamic>"


def normalize(text: str) -> str:
    """Strip volatile tokens (ids, timestamps), keeping the prefix identical.

    ``text`` is a composed prompt that may carry per-call volatile tokens. The
    returned string has every UUID, ISO-8601 timestamp and keyed identity value
    replaced by a stable placeholder, so the cache-key-relevant prefix is
    byte-identical across requests that differ only in those tokens.

    Fully-static input (a template) is a fixed point: ``normalize(t) == t``.
    """
    if not text:
        return ""
    normalized = _ID_KEY_RE.sub(
        lambda m: "{}={}".format(m.group(1), _DYNAMIC_PLACEHOLDER), text
    )
    normalized = _ISO_TS_RE.sub(_TIMESTAMP_PLACEHOLDER, normalized)
    normalized = _UUID_RE.sub(_UUID_PLACEHOLDER, normalized)
    return normalized


def canonical_template(class_name: str) -> str:
    """Return the canonical (already-normalised) template for ``class_name``."""
    try:
        return PREFIX_TEMPLATES[class_name]
    except KeyError:
        raise PrefixTemplateError(
            "unknown call class: {!r}".format(class_name)
        ) from None


def validate_prefix(class_name: str, composed: str) -> None:
    """Refuse a composed prompt whose normalised prefix deviates from template.

    Normalises ``composed`` (stripping volatile ids/timestamps) and requires it
    to begin with ``class_name``'s canonical template. Raises
    :class:`PrefixTemplateError` naming the class on any deviation.
    """
    template = canonical_template(class_name)
    normalized = normalize(composed)
    if not normalized.startswith(template):
        raise PrefixTemplateError(
            "prefix deviation for call class {!r}: normalised prefix does "
            "not match the canonical template".format(class_name)
        )


def footprint(text: str) -> str:
    """sha256 of the normalised bytes — the cache-key fingerprint."""
    return hashlib.sha256(normalize(text).encode("utf-8")).hexdigest()
