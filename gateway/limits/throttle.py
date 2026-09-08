"""Output throttle per task type: token caps that stop runaway loops.

A provider response whose token count exceeds the cap for its taskType is
either trimmed to the cap (mode=trim) or refused entirely (mode=refuse), so a
runaway agent loop cannot grow unbounded output.  Caps are keyed by the
kebab-case taskType vocabulary (registry/prompts); an unknown task type falls
back to ``default_cap``.  The offline token estimate is characters/4 unless
the caller supplies a real usage count.
"""

from __future__ import annotations

from dataclasses import dataclass

OK = "ok"
TRIM = "trim"
REFUSE = "refuse"

ACTIONS = frozenset({OK, TRIM, REFUSE})

# Compact built-in fallback taxonomy (runtime source of truth lives in
# config/limits.yaml; this is the constructor default when no caps map is
# passed, mirroring the cannibalized fleet output-throttle.sh cap table).
DEFAULT_CAPS: dict[str, int] = {
    "boolean": 100,
    "classify": 100,
    "check": 100,
    "verify": 100,
    "lint": 100,
    "triage": 100,
    "typo": 300,
    "format": 300,
    "style": 300,
    "comment": 300,
    "patch": 500,
    "fix": 500,
    "bug-fix": 500,
    "commit-code": 500,
    "summarize": 800,
    "code-gen": 2000,
    "refactor": 2000,
    "test-gen": 1500,
    "run-test": 1500,
    "devops": 2000,
    "migration": 2000,
    "documentation": 2000,
    "document-review": 1500,
    "code-review-verdict": 1500,
    "classify-route": 1000,
    "route-task": 2000,
    "orchestrate": 3000,
    "diagnose-failure": 3000,
    "architecture": 4000,
    "design": 4000,
    "security": 3000,
    "audit": 3000,
    "advisor": 3000,
}

DEFAULT_CAP = 2000
DEFAULT_CHARS_PER_TOKEN = 4
REASON_OUTPUT_CAP_EXCEEDED = "output_cap_exceeded"


@dataclass(frozen=True)
class ThrottleVerdict:
    """Result of enforcing the output cap for one task type."""

    task_type: str
    cap: int
    output_tokens: int
    action: str  # ok | trim | refuse
    allowed: bool  # False only when the output was refused
    trimmed: bool = False
    refused: bool = False
    output: str | None = None  # effective output (trimmed or original, None if refused)
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.action not in ACTIONS:
            raise ValueError(f"unknown throttle action: {self.action!r}")


class OutputThrottle:
    """Enforces per-taskType output token caps."""

    def __init__(
        self,
        caps: dict[str, int] | None = None,
        *,
        default_cap: int = DEFAULT_CAP,
        mode: str = TRIM,
        chars_per_token: int = DEFAULT_CHARS_PER_TOKEN,
    ) -> None:
        self.caps = dict(caps if caps is not None else DEFAULT_CAPS)
        self.default_cap = int(default_cap)
        if self.default_cap <= 0:
            raise ValueError("default_cap must be positive")
        self.mode = str(mode).lower()
        if self.mode not in ACTIONS - {OK}:
            raise ValueError(f"mode must be trim or refuse, got {mode!r}")
        self.chars_per_token = int(chars_per_token)
        if self.chars_per_token <= 0:
            raise ValueError("chars_per_token must be positive")

    def max_tokens(self, task_type: str | None) -> int:
        """Token cap for a taskType (default when unknown)."""
        if task_type is None:
            return self.default_cap
        return self.caps.get(task_type, self.default_cap)

    def token_estimate(self, text: str) -> int:
        """Offline token estimate (chars / chars_per_token), minimum 1."""
        return max(1, len(text or "") // self.chars_per_token)

    def enforce(self, task_type: str | None, output: str | None) -> ThrottleVerdict:
        """Enforce the cap on ``output`` and return the effective output.

        mode=trim returns output truncated to the cap (word-boundary safe);
        mode=refuse returns output=None when over cap.  Refusing does NOT
        unwrite the provider spend (already metered); it stops the runaway
        loop from receiving ever-larger output.
        """
        if output is None:
            return ThrottleVerdict(
                task_type=task_type or "",
                cap=self.max_tokens(task_type),
                output_tokens=0,
                action=OK,
                allowed=True,
                output=output,
            )
        cap = self.max_tokens(task_type)
        estimated = self.token_estimate(output)
        if estimated <= cap:
            return ThrottleVerdict(
                task_type=task_type or "",
                cap=cap,
                output_tokens=estimated,
                action=OK,
                allowed=True,
                output=output,
            )
        if self.mode == REFUSE:
            return ThrottleVerdict(
                task_type=task_type or "",
                cap=cap,
                output_tokens=estimated,
                action=REFUSE,
                allowed=False,
                refused=True,
                output=None,
                reason=REASON_OUTPUT_CAP_EXCEEDED,
            )
        # trim to the cap, cutting at a word boundary when possible
        limit_chars = cap * self.chars_per_token
        head = output[:limit_chars]
        if len(output) > limit_chars:
            cut = head.rfind(" ")
            if cut > limit_chars // 2:
                head = head[:cut]
        return ThrottleVerdict(
            task_type=task_type or "",
            cap=cap,
            output_tokens=estimated,
            action=TRIM,
            allowed=True,
            trimmed=True,
            output=head,
        )
