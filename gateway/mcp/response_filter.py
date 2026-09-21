"""Localized MCP response-filtering layer (trim verbose tool output before context).

Issue #672 (PF-7, parent #665): the Phase-3 FinOps control that truncates
verbose tool outputs **before** they are serialized into the model context, so
cache misses and extraneous token consumption are avoided on the read-heavy
tools whose payloads can balloon (``code.references``, ``code.search``,
``kb.query``, ``code.definitions``).

The layer is:

- **Declarative** - each tool has a :class:`TruncationRule` (a hard char
  budget plus ``keep_head_chars`` leading characters and a tuple of
  ``key_sections`` whose JSON values survive truncation). There is no
  imperative per-tool truncation code: a new tool is bounded by adding a rule,
  not by writing a branch.
- **Closed** - the rule registry only answers for tools it declares. Asking
  the filter for a tool with no rule raises :class:`UnknownRuleError` (the
  allowlist-not-denylist doctrine) rather than silently passing the payload
  through, so an unbudgeted tool can never reach the context unmeasured.
- **Flag-gated OFF** - :class:`ResponseFilter` defaults to ``enabled=False``
  and is then inert (a byte-identical serialization, no budget applied). It is
  applied in the MCP response path (``_dispatch_tool``) only once promoted;
  the module-level default mirrors the rate-gate's ``None``-injection OFF
  default and maps to a feature-flag entry at promotion time (IaC mandate:
  new surfaces ship flag-gated OFF).

The budget is measured in **characters** (deterministic and testable), the
gateway's own canonical unit for a serialized response; token accounting is
the metering layer's job downstream (``gateway/finops``), not this layer's.

---knowledge---
module_id: gateway.mcp.response_filter
system: gateway
app: mcp
solution_class: enterprise
patterns: [declarative-rules, truncate-before-context, closed-rule-registry]
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [ResponseFilter, TruncationRule, default_rule_registry, UnknownRuleError]
invariants: "truncation happens before the output is serialized into the model context, so a verbose payload cannot inflate a cache miss"
gotchas: "there is no imperative per-tool truncation code: a new tool is bounded by adding a declared rule"
related: ["#672", "#665"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Dict, Mapping, Tuple

#: Appended between the kept head and the key sections when a payload exceeds
#: its rule's budget, so a consumer can see at a glance that output was elided.
TRUNCATION_MARKER = "\n... [truncated: response exceeded tool budget] ...\n"


class UnknownRuleError(Exception):
    """A tool has no declared truncation rule (the rule registry is closed)."""


@dataclass(frozen=True)
class TruncationRule:
    """Declarative per-tool truncation rule.

    ``budget_chars`` is the hard ceiling for the serialized response. Within
    that ceiling the head of the payload (``keep_head_chars``) and the listed
    ``key_sections`` (JSON keys whose values are retained verbatim, e.g. the
    identifying ``symbol``/``repo`` so a truncated hit is still attributable)
    survive; everything else is elided behind the truncation marker.
    """

    tool: str
    budget_chars: int
    keep_head_chars: int = 0
    key_sections: Tuple[str, ...] = ()


def default_rule_registry() -> Dict[str, TruncationRule]:
    """The closed, declarative rule set (one rule per truncatable read tool).

    Only tools that can return unbounded payloads are declared; the registry is
    intentionally not generated from ``declared_tools()`` so a rule is added
    deliberately (and reviewed) rather than inferred.
    """
    return {
        "code.definitions": TruncationRule(
            tool="code.definitions",
            budget_chars=2048,
            keep_head_chars=512,
            key_sections=("symbol", "repo"),
        ),
        "code.references": TruncationRule(
            tool="code.references",
            budget_chars=2048,
            keep_head_chars=512,
            key_sections=("symbol", "repo"),
        ),
        "code.search": TruncationRule(
            tool="code.search",
            budget_chars=2048,
            keep_head_chars=512,
            key_sections=("q", "repo"),
        ),
        "kb.query": TruncationRule(
            tool="kb.query",
            budget_chars=2048,
            keep_head_chars=512,
            key_sections=("module_id", "repo"),
        ),
    }


class ResponseFilter:
    """Truncates verbose tool outputs to their declared budget.

    A disabled filter is inert: ``filter`` serializes the payload and returns
    it byte-identically, applying no budget. An enabled filter refuses a tool
    with no declared rule and truncates a payload that exceeds its rule's
    budget to at most ``budget_chars``, keeping the declared head + key
    sections.
    """

    def __init__(
        self,
        *,
        enabled: bool = False,
        rules: Mapping[str, TruncationRule] | None = None,
    ) -> None:
        self.enabled = enabled
        self._rules: Dict[str, TruncationRule] = dict(
            rules if rules is not None else default_rule_registry()
        )

    def has_rule(self, tool: str) -> bool:
        """Whether the closed registry declares a truncation rule for ``tool``."""
        return tool in self._rules

    def rules(self) -> Dict[str, TruncationRule]:
        """A read-only view of the closed rule registry."""
        return dict(self._rules)

    def filter(self, tool: str, payload) -> str:
        """Return the (possibly truncated) serialized response for ``tool``.

        Raises :class:`UnknownRuleError` when enabled and ``tool`` has no
        declared rule (fail closed). Returns ``json.dumps(payload, ...)``
        byte-identically when disabled or when the payload is within budget.
        """
        serialized = json.dumps(payload, sort_keys=True, indent=2)
        if not self.enabled:
            return serialized
        rule = self._rules.get(tool)
        if rule is None:
            raise UnknownRuleError(
                f"no truncation rule for tool {tool!r} (closed rule registry)"
            )
        if len(serialized) <= rule.budget_chars:
            return serialized
        return self._truncate(payload, serialized, rule)

    # ------------------------------------------------------------------ #
    # truncation
    # ------------------------------------------------------------------ #
    @staticmethod
    def _truncate(payload, serialized: str, rule: TruncationRule) -> str:
        """Trim ``serialized`` to ``rule.budget_chars`` keeping head + sections."""
        head = (
            serialized[: rule.keep_head_chars]
            if rule.keep_head_chars > 0
            else ""
        )
        sections = ResponseFilter._key_sections(payload, rule)
        body = head + TRUNCATION_MARKER + sections
        if len(body) <= rule.budget_chars:
            return body
        # Hard ceiling: never exceed the budget (head + marker win first).
        return body[: rule.budget_chars]

    @staticmethod
    def _key_sections(payload, rule: TruncationRule) -> str:
        """Serialize the rule's key sections that are present in the payload."""
        if not isinstance(payload, dict) or not rule.key_sections:
            return ""
        kept = {key: payload[key] for key in rule.key_sections if key in payload}
        if not kept:
            return ""
        return json.dumps(kept, sort_keys=True, indent=2)
