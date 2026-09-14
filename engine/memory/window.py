"""engine/memory - sliding-window memory policy: drop redundant context.

Issue kushin77/agent-orchestrator#674 ("PF-9: Sliding-window memory rules -
eliminate redundant context processing"), parent epic #665, aligned with the
prefix-template contract (#670 / ``prompt_cache.py``).

The enrichment window slides over the score-ordered retrieved hits and drops
any hit whose content is *already represented* in the window before it costs
a token. Providers cache from token 0 (``prompt_cache`` discipline), so a
redundant hit re-emits bytes the cache already holds - pure cost, zero new
information. Dropping it is free; keeping it is waste.

Two redundancy rules, both deterministic so "what stays" remains a pure
function of the logical memory set (byte-stable - cache friendliness holds):

1. **duplicate text** - a hit whose normalized ``text`` is byte-identical to
   an already-kept hit's text is dropped;
2. **subsumed text** - a hit whose normalized ``text`` is fully contained
   within an already-kept hit's text is dropped (a repeated prefix/suffix
   carries no new information, only tokens).

The policy is configurable: ``dedupe_text`` toggles rule 1, ``drop_subsumed``
toggles rule 2, so a tenant that deliberately keeps near-duplicate records
can opt out.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Sequence, Tuple

from .model import normalize_text


@dataclass(frozen=True)
class WindowPolicy:
    """Configurable sliding-window trimming rules.

    ``dedupe_text``  drop a hit whose text equals an already-kept hit's text.
    ``drop_subsumed`` drop a hit whose text is fully contained in an
                     already-kept hit's text (e.g. a repeated prefix).
    """

    dedupe_text: bool = True
    drop_subsumed: bool = True


def _content_key(hit: Any) -> str:
    """The normalized text a hit contributes - the redundancy unit."""
    return normalize_text(getattr(hit, "text", "") or "")


def slide_window(hits: Sequence[Any],
                 policy: WindowPolicy) -> Tuple[List[Any], int]:
    """Slide over score-ordered hits; return ``(kept, redundant_dropped)``.

    ``hits`` must be pre-sorted highest-score first (the retriever's order).
    The window keeps the first hit carrying any given content and drops later
    hits whose content is redundant with it. The result is deterministic for
    a fixed input order, so the surviving block stays a pure function of the
    logical memory set.
    """

    if not hits:
        return [], 0
    if not policy.dedupe_text and not policy.drop_subsumed:
        return list(hits), 0

    kept: List[Any] = []
    seen_texts: List[str] = []
    dropped = 0

    for hit in hits:
        text = _content_key(hit)
        redundant = False
        if text:
            if policy.dedupe_text and text in seen_texts:
                redundant = True
            elif policy.drop_subsumed:
                for kept_text in seen_texts:
                    if kept_text and text in kept_text:
                        redundant = True
                        break
        if redundant:
            dropped += 1
        else:
            kept.append(hit)
            seen_texts.append(text)

    return kept, dropped
