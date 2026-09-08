"""Prompt fingerprinting and cache-key construction.

A semantic-cache key is content-derived and scope-bound:

    sha256(schema_version, tenant, model tier, prompt_fingerprint, task_type?)

The prompt fingerprint is itself a stable digest of the NORMALIZED prompt, so
near-identical prompts (whitespace drift, unicode lookalikes) collide and are
deduplicated, while distinct tenants or model tiers never share an entry.
Normalization mirrors the fleet's prompt-cache normalization (leaderboard
lib/semantic-cache.sh, #708) adapted to Python; by default it lowercases
nothing and strips no punctuation (exact-ish dedup, provenance-safe).  Callers
may opt into stronger collapsing for prose-heavy task types.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata

# Bump when the canonicalization or key material layout changes; a bump
# invalidates every previously cached entry (versioned invalidation, mirroring
# the llm-cache #703 convention).
SCHEMA_VERSION = 1

# Consumed from registry/profiles/catalog.yaml (the tier vocabulary); keys are
# constructed from the uppercase form so "low" and "LOW" collide.
CANONICAL_TIERS = frozenset({"LOW", "MED", "HIGH", "MAX"})

_WS_RUN = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\w\s]", flags=re.UNICODE)


def normalize_tier(model_tier: str) -> str:
    """Normalize a model-tier label to the uppercase canonical form."""
    tier = (model_tier or "").strip().upper()
    if not tier:
        raise ValueError("model tier must not be empty")
    return tier


def normalize_prompt(
    prompt: str,
    *,
    lowercase: bool = False,
    strip_punctuation: bool = False,
) -> str:
    """Normalize a prompt for fingerprinting.

    Applies, in order: NFKC unicode normalization, strip, collapse every run of
    whitespace to a single space, and (optionally) lowercase and/or remove
    punctuation.  Collapsing whitespace alone keeps code prompts exact while
    making whitespace-only edits collide.
    """
    text = unicodedata.normalize("NFKC", prompt or "")
    text = text.strip()
    text = _WS_RUN.sub(" ", text)
    if lowercase:
        text = text.lower()
    if strip_punctuation:
        text = _PUNCT.sub("", text)
        text = _WS_RUN.sub(" ", text).strip()
    return text


def prompt_fingerprint(
    prompt: str,
    *,
    lowercase: bool = False,
    strip_punctuation: bool = False,
    schema_version: int = SCHEMA_VERSION,
) -> str:
    """Content-only digest of a normalized prompt (the dedup identity)."""
    normalized = normalize_prompt(
        prompt, lowercase=lowercase, strip_punctuation=strip_punctuation
    )
    material = f"ao-limits-fingerprint/v{schema_version}\n{normalized}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def cache_key(
    tenant: str,
    model_tier: str,
    prompt: str,
    *,
    task_type: str | None = None,
    lowercase: bool = False,
    strip_punctuation: bool = False,
    schema_version: int = SCHEMA_VERSION,
) -> str:
    """Full semantic-cache key: scope (tenant + tier) + prompt fingerprint.

    Two calls collide only when tenant, tier, task type and normalized prompt
    all match.  Same prompt under a different tenant or tier -> a different
    key -> a separate (correctly isolated) cache entry.
    """
    fp = prompt_fingerprint(
        prompt,
        lowercase=lowercase,
        strip_punctuation=strip_punctuation,
        schema_version=schema_version,
    )
    tier = normalize_tier(model_tier)
    parts = [f"ao-limits-cache/v{schema_version}", tenant, tier, fp]
    if task_type:
        parts.append(task_type)
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def scope_key(tenant: str, agent: str, model_tier: str) -> str:
    """Canonical cost-control scope for one (tenant, agent, tier) triple."""
    return f"{tenant}::{agent}::{normalize_tier(model_tier)}"


def tenant_scope(tenant: str) -> str:
    """Prefix scope for every (agent, tier) under one tenant."""
    return f"{tenant}::"
