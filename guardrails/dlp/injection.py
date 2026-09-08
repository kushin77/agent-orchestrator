"""guardrails.dlp.injection — prompt-injection defense.

Detection model
===============

External content (documents, web pages, tool output, email) is **untrusted**
and may carry a prompt injection or an indirect attack. This module scores
payloads against a catalog of injection signals and applies two defenses:

1. **Prompt gate (inbound)** — :meth:`InjectionDetector.analyze` classifies a
   payload that is about to be sent to a model as ``benign``, ``suspicious``,
   or ``blocked``. A ``blocked`` payload never reaches egress.
2. **Output filter (inbound)** — :meth:`InjectionDetector.filter_output`
   classifies a model's *response*: a response that echoes the system prompt,
   restates internal instructions, or carries role-tag injection is
   quarantined and never integrated.

Signal taxonomy (categories)

* ``instruction_override`` — attempts to discard prior instructions
  ("ignore all previous instructions", "disregard everything above").
* ``prompt_leak`` — attempts to extract the system prompt or instructions.
* ``role_escalation`` — persona/jailbreak switches ("you are now DAN",
  "developer mode", "no rules apply").
* ``exfiltration`` — directives to emit credentials/secrets/tokens.
* ``role_tag_injection`` — embedded ``system:/assistant:`` role lines used to
  smuggle instructions into the conversation.
* ``delimiter_escape`` — closing the untrusted-content delimiter and then
  issuing instructions.
* ``output_echo`` — model output that repeats its own system prompt (only
  meaningful on the output side).

Verdict policy (deterministic, documented)

* **blocked** — any single ``high`` signal, or two or more distinct signals
  (concurrent markers are a strong injection signature).
* **suspicious** — exactly one ``medium`` signal (human review, no hard block).
* **benign** — no signal.

The signal patterns are deliberately precise so ordinary enterprise prose does
not trip them; the negative-control suite in ``tests/test_injection.py``
asserts a benign corpus produces zero blocked/suspicious verdicts (no-false-
green discipline: the detector can genuinely fail on an attack and genuinely
pass on benign text).

Untrusted-content tagging
=========================

Callers MUST delimit untrusted content before it reaches the model. Use
:func:`wrap_untrusted`; the module also neutralizes an embedded closing
delimiter so untrusted text cannot early-close the tag and escape its bounds.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

# Untrusted-content delimiters (documented convention for the gateway/engine
# layers that call this module).
UNTRUSTED_OPEN = "<untrusted>"
UNTRUSTED_CLOSE = "</untrusted>"


@dataclass(frozen=True)
class Signal:
    """A named, compiled injection signal."""

    id: str
    label: str
    category: str
    severity: str  # "high" | "medium"
    pattern: str
    output_only: bool = False  # evaluated only by filter_output


def _sig(signal_id, label, category, severity, pattern, output_only=False) -> Signal:
    return Signal(
        id=signal_id,
        label=label,
        category=category,
        severity=severity,
        pattern=pattern,
        output_only=output_only,
    )


SIGNALS: tuple = (
    # -- instruction override (high) ----------------------------------------
    _sig(
        "inj.override.ignore_previous",
        "ignore/disregard previous instructions",
        "instruction_override",
        "high",
        r"(?i)\b(?:ignore|disregard)\s+(?:all\s+)?(?:previous|prior|earlier|the\s+above|everything\s+above)\s+(?:instructions?|prompts?|messages?|context|content|directions?|guidelines?)\b",
    ),
    _sig(
        "inj.override.ignore_all",
        "ignore everything above",
        "instruction_override",
        "high",
        r"(?i)\bignore\s+(?:everything|all)\s+(?:above|before|prior|you\s+have\s+been\s+told)\b",
    ),
    _sig(
        "inj.override.forget_instructions",
        "forget/override/undo instructions or policy",
        "instruction_override",
        "high",
        r"(?i)\b(?:forget|override|bypass|undo|discard)\s+(?:all\s+|any\s+|your\s+)?(?:previous\s+|prior\s+)?(?:instructions?|guidelines?|policy|rules?|constraints?|system\s+prompt)\b",
    ),
    _sig(
        "inj.override.remove_safety",
        "disable safety/moderation guardrails",
        "instruction_override",
        "high",
        r"(?i)\b(?:ignore|bypass|disable|remove|turn\s+off|override)\s+(?:all\s+|any\s+|the\s+|your\s+)?(?:safety|content|ethical|value)\s+(?:policy|guardrails?|constraints?|moderation|alignment)\b",
    ),
    # -- prompt leak (high) --------------------------------------------------
    _sig(
        "inj.leak.reveal_system_prompt",
        "reveal/show the system prompt",
        "prompt_leak",
        "high",
        r"(?i)\b(?:reveal|show|print|display|output|dump|repeat|recite|echo|write\s+out)\b[^.\n]{0,40}\b(?:system\s+prompt|your\s+(?:system\s+)?instructions?)\b",
    ),
    _sig(
        "inj.leak.ask_instructions",
        "ask what your instructions are",
        "prompt_leak",
        "high",
        r"(?i)\b(?:what\s+are|tell\s+me|state)\b[^.\n]{0,30}\b(?:your|your\s+own)\s+(?:instructions?|system\s+prompt|guidelines?)\b",
    ),
    # -- role escalation (high for jailbreak, medium for mode) --------------
    _sig(
        "inj.role.dan_jailbreak",
        "DAN / unrestricted jailbreak persona",
        "role_escalation",
        "high",
        r"(?i)\byou\s+are\s+now\s+(?:dan|jailbreak(?:ed)?|unrestricted|ungoverned)\b",
    ),
    _sig(
        "inj.role.unrestricted_act",
        "act without rules/restrictions",
        "role_escalation",
        "high",
        r"(?i)\b(?:act\s+as|pretend\s+(?:to\s+be|you\s+are)|behave\s+as)\b[^.\n]{0,30}\b(?:without\s+(?:any\s+)?(?:rules|restrictions|limits|guardrails)|no\s+rules|unrestricted)\b",
    ),
    _sig(
        "inj.role.developer_mode",
        "developer/god/do-anything mode switch",
        "role_escalation",
        "medium",
        r"(?i)\b(?:developer\s+mode|god\s+mode|superior\s+mode|do\s+anything\s+now)\b",
    ),
    # -- exfiltration (high) -------------------------------------------------
    _sig(
        "inj.exfil.secrets",
        "directive to emit credentials or secrets",
        "exfiltration",
        "high",
        r"(?i)\b(?:exfiltrate|leak|dump|print|output|send|upload|email|post|reveal|hand\s+over)\b[^.\n]{0,50}\b(?:credentials?|api\s+keys?|passwords?|secrets?|access\s+tokens?|private\s+keys?)\b",
    ),
    # -- role-tag injection (medium) -----------------------------------------
    _sig(
        "inj.roletag.embedded",
        "embedded system/assistant role line",
        "role_tag_injection",
        "medium",
        r"(?i)(?:^|\n)[ \t]*(?:system|assistant|developer)\s*:",
    ),
    # -- delimiter escape (medium; high when combined with instructions) -----
    _sig(
        "inj.escape.close_delimiter",
        "close untrusted delimiter then instruct",
        "delimiter_escape",
        "medium",
        r"(?i)</\s*untrusted\s*>\s*(?:ignore|disregard|now\s+you|from\s+now\s+on|you\s+are|forget|override)\b",
    ),
    # -- output echo (output-side only) --------------------------------------
    _sig(
        "out.echo.system_prompt",
        "model output repeats its own system prompt",
        "output_echo",
        "high",
        r"(?i)\b(?:here\s+(?:are|is)|these\s+are|the\s+following\s+are|my|the)\b[^.\n]{0,30}\b(?:system\s+prompt|internal\s+instructions?)\b",
        output_only=True,
    ),
    _sig(
        "out.echo.self_identity",
        "model output states its system identity boilerplate",
        "output_echo",
        "high",
        r"(?i)\byou\s+are\s+an?\s+(?:AI|artificial\s+intelligence)\s+(?:language\s+model|assistant|chatbot)\b",
        output_only=True,
    ),
    _sig(
        "out.echo.instructions",
        "model output restates instructions verbatim",
        "output_echo",
        "high",
        r"(?i)\b(?:repeat|restate|recite)\b[^.\n]{0,30}\b(?:instructions?|system\s+prompt)\b",
        output_only=True,
    ),
)

_SEVERITY_RANK = {"high": 3, "medium": 2, "low": 1}


@dataclass(frozen=True)
class SignalHit:
    """One concrete signal match."""

    signal_id: str
    label: str
    category: str
    severity: str
    start: int
    matched: str


@dataclass
class InjectionReport:
    """Result of analyzing one payload."""

    verdict: str  # "benign" | "suspicious" | "blocked"
    hits: list = field(default_factory=list)  # list[SignalHit]
    reasons: list = field(default_factory=list)  # list[str]

    @property
    def blocked(self) -> bool:
        return self.verdict == "blocked"

    @property
    def score(self) -> int:
        return sum(_SEVERITY_RANK.get(h.severity, 1) for h in self.hits)


def _decide(hits: list) -> str:
    """Deterministic verdict from the matched hits (see module docstring)."""
    if not hits:
        return "benign"
    highs = [h for h in hits if h.severity == "high"]
    if highs:
        return "blocked"
    if len(hits) >= 2:
        return "blocked"
    return "suspicious"


class InjectionDetector:
    """Heuristic prompt-injection / output-echo detector."""

    def __init__(self, signals: Optional[tuple] = None) -> None:
        self._signals = list(signals or SIGNALS)
        self._compiled = [
            (sig, re.compile(sig.pattern)) for sig in self._signals if not sig.output_only
        ]
        self._output_compiled = [
            (sig, re.compile(sig.pattern)) for sig in self._signals if sig.output_only
        ]

    # -- prompt gate ---------------------------------------------------------

    @staticmethod
    def _report(hits: list) -> InjectionReport:
        """Build a report from hits: sort them, derive reasons once per signal."""
        hits.sort(key=lambda h: (h.start, h.signal_id))
        reasons: list = []
        seen: set = set()
        for h in hits:
            if h.signal_id not in seen:
                seen.add(h.signal_id)
                reasons.append(f"{h.signal_id}: {h.label}")
        return InjectionReport(verdict=_decide(hits), hits=hits, reasons=reasons)

    def _scan(self, text: str, compiled: list) -> list:
        """Return SignalHit objects for every match of ``compiled`` signals."""
        hits: list = []
        for sig, rx in compiled:
            for m in rx.finditer(text):
                hits.append(
                    SignalHit(
                        signal_id=sig.id,
                        label=sig.label,
                        category=sig.category,
                        severity=sig.severity,
                        start=m.start(),
                        matched=m.group(0)[:80],
                    )
                )
        return hits

    def analyze(self, text: str) -> InjectionReport:
        """Classify an outbound payload as benign/suspicious/blocked."""
        return self._report(self._scan(text, self._compiled))

    # -- output filter -------------------------------------------------------

    def filter_output(self, text: str) -> InjectionReport:
        """Classify a model *response*; quarantinable output is ``blocked``.

        Output-only signals (system-prompt echo) plus the prompt-side
        role-tag/instruction-override sets are evaluated: a response that
        restates instructions or smuggles role lines is quarantined.
        """
        hits = self._scan(text, self._output_compiled)
        prompt_side = [
            (sig, rx)
            for (sig, rx) in self._compiled
            if sig.category in ("role_tag_injection", "instruction_override")
        ]
        hits.extend(self._scan(text, prompt_side))
        return self._report(hits)


# -- untrusted-content tagging helpers ---------------------------------------


def wrap_untrusted(content: str) -> str:
    """Wrap untrusted content in the standard delimiters.

    Any embedded closing delimiter inside ``content`` is neutralized first so
    the content cannot escape its own bounds and smuggle instructions into the
    trusted region.
    """
    return f"{UNTRUSTED_OPEN}\n{neutralize_untrusted(content)}\n{UNTRUSTED_CLOSE}"


def neutralize_untrusted(content: str) -> str:
    """Replace an embedded closing delimiter token so it cannot early-close."""
    return content.replace(UNTRUSTED_CLOSE, "<untrusted-end-neutralized>")
