"""guardrails.dlp.engine — the DLP scrub engine (detect + block + redact).

Implements the outbound **scrub gate** of the external-LLM egress doctrine:
before any commercial-model call is dispatched, the payload is run against the
policy catalog (``scrub-rules.yml``). A ``block`` match aborts the egress
(fail closed); a ``redact`` match replaces the sensitive value with a stable
placeholder so structure survives without the secret.

Decision model:

* every rule in the catalog is evaluated over the full payload;
* if **any** ``block``-class match is found the verdict is ``blocked`` and the
  payload is never returned for dispatch (the caller aborts the consult);
* otherwise every ``redact`` match is replaced with its rule placeholder and
  the verdict is ``sent`` with the redacted text;
* the engine refuses to run on an empty or invalid catalog
  (:class:`~guardrails.dlp.catalog.CatalogError` at construction) — an
  ambiguous rule set never downgrades to a silent pass;
* optional ``luhn`` rules validate the digit checksum before counting a match
  (credit-card detection) so long random digit runs do not over-trigger.

The audit-relevant counts (per rule id and per class) are always returned so a
blocked or redacted consult is fully attributable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from .catalog import (
    CatalogError,
    RuleCatalog,
    ScrubMatch,
    ScrubRule,
)

_STRIP_NONDIGIT = re.compile(r"[^0-9]")


def luhn_valid(digits: str) -> bool:
    """Return True when ``digits`` (a string of 0-9) passes the Luhn checksum."""
    total = 0
    parity = len(digits) % 2
    for idx, ch in enumerate(digits):
        if ch < "0" or ch > "9":
            return False
        val = ord(ch) - 48
        if idx % 2 == parity:
            val *= 2
            if val > 9:
                val -= 9
        total += val
    return total % 10 == 0


@dataclass(frozen=True)
class ScrubResult:
    """Outcome of running one payload through the scrub gate."""

    verdict: str  # "sent" | "blocked"
    text: str  # redacted text when sent; original payload when blocked
    ruleset_version: str
    matches: tuple  # tuple[ScrubMatch, ...] sorted by position
    blocked_by: tuple  # tuple[str, ...] rule ids that forced the block
    counts: dict  # rule id -> number of detections
    class_counts: dict  # class -> number of detections

    @property
    def blocked(self) -> bool:
        return self.verdict == "blocked"


@dataclass
class ScrubEngine:
    """Stateless DLP scrub engine bound to one validated rule catalog."""

    catalog: Optional[RuleCatalog] = None
    fail_closed: bool = True

    def __post_init__(self) -> None:
        self.catalog = self.catalog or RuleCatalog.load_default()
        if self.fail_closed and not self.catalog.rules:
            raise CatalogError("refusing to run: empty scrub catalog (fail closed)")
        if self.fail_closed and not self.catalog.ruleset_version:
            raise CatalogError("refusing to run: catalog has no ruleset_version (fail closed)")

    # -- detection -----------------------------------------------------------

    def _rule_matches(self, rule: ScrubRule, text: str) -> list:
        """Return ScrubMatch objects for a rule, honoring optional Luhn checks."""
        out = []
        for m in rule.compiled.finditer(text):
            value = m.group(0)
            if rule.luhn:
                digits = _STRIP_NONDIGIT.sub("", value)
                if not (13 <= len(digits) <= 19) or not luhn_valid(digits):
                    continue
            out.append(
                ScrubMatch(
                    rule_id=rule.id,
                    rule_class=rule.class_,
                    severity=rule.severity,
                    action=rule.action,
                    placeholder=rule.placeholder,
                    start=m.start(),
                    end=m.end(),
                    value=value,
                )
            )
        return out

    def detect(self, text: str) -> list:
        """Detect every rule match in ``text`` (block and redact alike).

        Returns matches sorted by start position. A block-class match anywhere
        means the payload must not leave.
        """
        matches: list = []
        for rule in self.catalog.rules:
            matches.extend(self._rule_matches(rule, text))
        matches.sort(key=lambda m: (m.start, -(m.end - m.start)))
        return matches

    # -- scrub ---------------------------------------------------------------

    def scrub(self, text: str) -> ScrubResult:
        """Run the payload through the gate; returns a :class:`ScrubResult`.

        ``blocked`` verdicts return the original text untouched (it is never
        dispatched); ``sent`` verdicts return the fully redacted text.
        """
        matches = self.detect(text)
        blocked_by: list = []
        redact_matches: list = []
        for m in matches:
            if m.action == "block":
                blocked_by.append(m.rule_id)
            else:
                redact_matches.append(m)

        counts: dict = {}
        class_counts: dict = {}
        for m in matches:
            counts[m.rule_id] = counts.get(m.rule_id, 0) + 1
            class_counts[m.rule_class] = class_counts.get(m.rule_class, 0) + 1

        if blocked_by:
            return ScrubResult(
                verdict="blocked",
                text=text,
                ruleset_version=self.catalog.ruleset_version,
                matches=tuple(matches),
                blocked_by=tuple(dict.fromkeys(blocked_by)),
                counts=counts,
                class_counts=class_counts,
            )

        redacted = self._apply_redactions(text, redact_matches)
        return ScrubResult(
            verdict="sent",
            text=redacted,
            ruleset_version=self.catalog.ruleset_version,
            matches=tuple(matches),
            blocked_by=(),
            counts=counts,
            class_counts=class_counts,
        )

    @staticmethod
    def _apply_redactions(text: str, redact_matches: list) -> str:
        """Replace non-overlapping redact matches with their placeholders.

        Matches are already sorted by (start, -length); the greedy pass keeps
        the longest span at an equal start (an internal URL outranks the host it
        contains). Replacement is applied back-to-front so earlier offsets stay
        valid.
        """
        kept: list = []
        cursor = -1
        for m in redact_matches:
            if m.start < cursor:
                continue
            kept.append(m)
            cursor = m.end
        out = text
        for m in reversed(kept):
            out = out[: m.start] + m.placeholder + out[m.end :]
        return out
