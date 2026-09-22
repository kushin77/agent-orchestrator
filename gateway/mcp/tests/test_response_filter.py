"""Response-filtering layer: trim verbose tool output before context (issue #672).

Exercises the closed, flag-gated truncation layer:

- a verbose payload is truncated to its declared budget (and keeps its
  key-sections);
- a payload under budget passes byte-identical;
- the rule registry is closed (an unknown tool is refused, fail closed);
- the layer ships inert (disabled → byte-identical pass-through).
"""

from __future__ import annotations

import json

import pytest

from mcp.response_filter import (
    TRUNCATION_MARKER,
    ResponseFilter,
    TruncationRule,
    UnknownRuleError,
    default_rule_registry,
)


def _ser(payload) -> str:
    return json.dumps(payload, sort_keys=True, indent=2)


def _verbose_payload(symbol: str = "charge", hits: int = 400) -> dict:
    """A verbose ``code.references``-shaped payload far over the 2048 budget."""
    return {
        "symbol": symbol,
        "repo": "acme/payments",
        "count": hits,
        "references": [
            {
                "path": f"src/payments/{i % 11}.py",
                "line": i,
                "context": f"charge(account, amount={i})",
            }
            for i in range(hits)
        ],
    }


# --------------------------------------------------------------------------- #
# budget: verbose payload truncated to its declared budget
# --------------------------------------------------------------------------- #
def test_verbose_payload_truncated_to_declared_budget():
    f = ResponseFilter(enabled=True)
    payload = _verbose_payload()
    original = _ser(payload)
    rule = f.rules()["code.references"]

    out = f.filter("code.references", payload)

    assert len(original) > rule.budget_chars  # the fixture really is verbose
    assert len(out) < len(original)  # truncation happened
    assert len(out) <= rule.budget_chars  # ...to the declared budget
    assert TRUNCATION_MARKER in out  # ...with an explicit elision marker


def test_truncation_keeps_head_and_key_sections():
    f = ResponseFilter(enabled=True)
    payload = _verbose_payload(symbol="refund")
    rule = f.rules()["code.references"]

    out = f.filter("code.references", payload)

    # The head of the original survives verbatim.
    assert out.startswith(_ser(payload)[: rule.keep_head_chars])
    # The identifying key-sections survive even though the bulk is elided.
    assert '"symbol"' in out and '"refund"' in out
    assert '"repo"' in out and '"acme/payments"' in out


# --------------------------------------------------------------------------- #
# budget: a payload under budget passes byte-identical
# --------------------------------------------------------------------------- #
def test_under_budget_passes_byte_identical():
    f = ResponseFilter(enabled=True)
    small = {"symbol": "charge", "count": 1, "references": []}

    out = f.filter("code.references", small)

    assert out == _ser(small)


def test_disabled_filter_is_inert_byte_identical():
    f = ResponseFilter(enabled=False)  # the inline opt-out / fail-closed default
    payload = _verbose_payload()

    out = f.filter("code.references", payload)

    assert out == _ser(payload)  # no budget applied when disabled


# --------------------------------------------------------------------------- #
# closed rule registry: unknown tool/rule refused
# --------------------------------------------------------------------------- #
def test_unknown_tool_refused_fail_closed():
    f = ResponseFilter(enabled=True)
    assert not f.has_rule("code.delete_all")

    with pytest.raises(UnknownRuleError):
        f.filter("code.delete_all", {"symbol": "x"})


def test_unknown_tool_passes_when_disabled():
    # Disabled the layer is inert and never refuses (it does not apply rules).
    f = ResponseFilter(enabled=False)
    out = f.filter("code.delete_all", {"symbol": "x"})
    assert out == _ser({"symbol": "x"})


def test_default_registry_is_a_declared_closed_set():
    rules = default_rule_registry()
    # A rule is added deliberately; the set is closed and declares each tool.
    assert set(rules) == {"code.definitions", "code.references", "code.search", "kb.query"}
    for tool, rule in rules.items():
        assert isinstance(rule, TruncationRule)
        assert rule.tool == tool
        assert rule.budget_chars > rule.keep_head_chars >= 0
