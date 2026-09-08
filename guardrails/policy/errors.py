"""Policy-gate exception hierarchy (issue #26).

Every failure mode of the policy-as-code + gate engine is a typed exception so
a caller can tell a *malformed policy* (fails at deploy time, in the startup
validation gate) from an *evaluation failure* (fails closed at runtime) and a
*missing resource* — without string-matching messages.
"""

from __future__ import annotations


class PolicyError(Exception):
    """Base class for every policy-gate error."""


class PolicyValidationError(PolicyError):
    """A policy document failed schema or semantic validation.

    Raised by the startup validation gate so an invalid policy fails the
    deploy, never at runtime (issue #26 acceptance #1).
    """


class PolicyLoadError(PolicyError):
    """A bundle path/file could not be discovered, read or YAML-parsed."""


class DuplicatePolicyError(PolicyError):
    """Two policies in the same bundle carry the same ``id``.

    A duplicate id would make evaluation ambiguous (which one governs?) so the
    bundle assembler refuses it (startup-time failure).
    """


class UnknownPolicyError(PolicyError):
    """An operation referenced a policy id that is not present in the bundle."""


class ControlError(PolicyError):
    """A controls-registry problem: malformed registry, duplicate or unknown id."""


class EvaluationError(PolicyError):
    """A rule could not be evaluated for the supplied context.

    The gate engine converts evaluation errors into a fail-closed BLOCK with
    the error attached to the evidence (AO-GR-4 / AO-GR-19, no-false-green).
    """


class ConditionError(EvaluationError):
    """A rule condition could not be evaluated.

    Raised on an unknown operator, a required path absent from the context, an
    invalid regular expression, or a value/type mismatch. Absence of an
    attribute is never a silent pass — the engine fails closed instead.
    """
