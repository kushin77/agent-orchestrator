"""Fingerprint stability, normalization, and tenant/tier separation."""

from __future__ import annotations

import pytest

from limits.fingerprint import (
    cache_key,
    normalize_prompt,
    normalize_tier,
    prompt_fingerprint,
    scope_key,
)

PROMPT = "Summarize the attached incident report for the on-call engineer."


class TestPromptFingerprint:
    def test_stable_same_prompt(self):
        assert prompt_fingerprint(PROMPT) == prompt_fingerprint(PROMPT)

    def test_whitespace_drift_collapses(self):
        messy = "   Summarize   the attached incident report\n\tfor  the on-call engineer.  "
        assert prompt_fingerprint(messy) == prompt_fingerprint(PROMPT)

    def test_different_prompt_differs(self):
        other = "Write a haiku about a token bucket."
        assert prompt_fingerprint(PROMPT) != prompt_fingerprint(other)

    def test_normalize_removes_unicode_lookalikes(self):
        # Full-width space (U+3000) collapses to a normal space via NFKC.
        wide = PROMPT.replace(" ", "\u3000")
        assert normalize_prompt(wide) == normalize_prompt(PROMPT)

    def test_optional_lowercase_flag_changes_fingerprint_for_case(self):
        mixed = "Summarize THIS doc"
        base = prompt_fingerprint(mixed)
        lowered = prompt_fingerprint(mixed, lowercase=True)
        assert base != lowered
        assert prompt_fingerprint("summarize this doc", lowercase=True) == lowered

    def test_optional_punctuation_flag(self):
        base = prompt_fingerprint(PROMPT)
        stripped = prompt_fingerprint(PROMPT, strip_punctuation=True)
        assert base != stripped


class TestCacheKey:
    def test_same_prompt_tenant_tier_same_key(self):
        k1 = cache_key("acme", "LOW", PROMPT)
        k2 = cache_key("acme", "LOW", PROMPT)
        assert k1 == k2

    def test_whitespace_drift_same_key(self):
        messy = "Summarize   the attached incident report   for  the on-call engineer."
        assert cache_key("acme", "LOW", messy) == cache_key("acme", "LOW", PROMPT)

    def test_tenant_separation(self):
        assert cache_key("acme", "LOW", PROMPT) != cache_key("globex", "LOW", PROMPT)

    def test_tier_separation(self):
        assert cache_key("acme", "LOW", PROMPT) != cache_key("acme", "HIGH", PROMPT)

    def test_tier_case_insensitive_key_collision(self):
        assert cache_key("acme", "low", PROMPT) == cache_key("acme", "LOW", PROMPT)

    def test_task_type_scopes_key(self):
        assert cache_key("acme", "LOW", PROMPT) != cache_key(
            "acme", "LOW", PROMPT, task_type="summarize"
        )

    def test_is_sha256_hex(self):
        key = cache_key("acme", "LOW", PROMPT)
        assert len(key) == 64
        int(key, 16)  # raises if not hex

    def test_empty_tier_rejected(self):
        with pytest.raises(ValueError):
            cache_key("acme", "", PROMPT)


class TestScopes:
    def test_scope_key_normalizes_tier(self):
        assert scope_key("acme", "coder", "low") == scope_key("acme", "coder", "LOW")
        assert scope_key("acme", "coder", "LOW") == "acme::coder::LOW"

    def test_scope_key_isolates_dimensions(self):
        assert scope_key("a", "x", "LOW") != scope_key("b", "x", "LOW")
        assert scope_key("a", "x", "LOW") != scope_key("a", "y", "LOW")
        assert scope_key("a", "x", "LOW") != scope_key("a", "x", "HIGH")

    def test_normalize_tier(self):
        assert normalize_tier("med") == "MED"
        with pytest.raises(ValueError):
            normalize_tier("")
