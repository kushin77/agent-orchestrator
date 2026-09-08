"""Output throttle: per-taskType caps, trim vs refuse, runaway-loop guard."""

from __future__ import annotations

import pytest

from limits.throttle import OutputThrottle

THROTTLE = OutputThrottle()


class TestCaps:
    def test_known_task_types(self):
        assert THROTTLE.max_tokens("boolean") == 100
        assert THROTTLE.max_tokens("patch") == 500
        assert THROTTLE.max_tokens("architecture") == 4000

    def test_unknown_task_type_falls_back(self):
        assert THROTTLE.max_tokens("no-such-task") == 2000
        assert THROTTLE.max_tokens(None) == 2000

    def test_config_caps_override_builtins(self):
        # limits.config merges file caps over the built-in taxonomy before
        # constructing; direct OutputThrottle(caps=...) uses exactly the caps
        # given. The config path is what runtime wiring uses.
        from limits.config import LimitsConfig, ThrottleConfig, build_throttle

        cfg = LimitsConfig(throttle=ThrottleConfig(caps={"boolean": 25}))
        throttle = build_throttle(cfg)
        assert throttle.max_tokens("boolean") == 25
        assert throttle.max_tokens("architecture") == 4000  # built-in retained


class TestEnforce:
    def test_under_cap_passes_through(self):
        verdict = THROTTLE.enforce("boolean", "yes")
        assert verdict.action == "ok"
        assert verdict.allowed is True
        assert verdict.trimmed is False
        assert verdict.output == "yes"

    def test_trim_over_cap(self):
        long_output = "word " * 3000  # ~12k chars -> ~3000 tokens > 2000 cap
        verdict = THROTTLE.enforce("standard", long_output)
        assert verdict.action == "trim"
        assert verdict.allowed is True
        assert verdict.trimmed is True
        assert verdict.output is not None
        assert len(verdict.output) < len(long_output)
        assert verdict.output_tokens > verdict.cap

    def test_refuse_over_cap(self):
        throttle = OutputThrottle(mode="refuse")
        long_output = "word " * 3000
        verdict = throttle.enforce("standard", long_output)
        assert verdict.action == "refuse"
        assert verdict.allowed is False
        assert verdict.refused is True
        assert verdict.output is None
        assert verdict.reason == "output_cap_exceeded"

    def test_refuse_mode_ok_under_cap(self):
        throttle = OutputThrottle(mode="refuse")
        verdict = throttle.enforce("boolean", "yes")
        assert verdict.allowed is True
        assert verdict.refused is False

    def test_none_output_passes(self):
        verdict = THROTTLE.enforce("standard", None)
        assert verdict.action == "ok"
        assert verdict.output is None

    def test_trim_respects_small_boolean_cap(self):
        throttle = OutputThrottle(mode="trim")
        verdict = throttle.enforce("boolean", "yes " * 500)  # ~2000 chars -> 500 tokens
        assert verdict.trimmed is True
        assert len(verdict.output) <= 100 * 4  # capped to ~cap*chars_per_token

    def test_mode_validation(self):
        with pytest.raises(ValueError):
            OutputThrottle(mode="banana")
