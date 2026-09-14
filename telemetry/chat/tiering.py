"""telemetry/chat — tier discipline for chat turns (issue #506).

The FinOps chooser (``gateway/finops/chooser.py`` over ``tiers.yaml``) is the
one authority on which model tier serves a turn.  It runs *behind* the chat
surface, and it stamps the tier it picked onto the gateway call record.  This
module is the chat surface's half of that contract:

- ``resolve_turn_tier`` promotes the tier from the **record's routing stamp**
  and refuses to stand in a client-supplied tier for it.  A turn whose record
  carries no tier is *unresolved*: the surface may not invent one, and a
  client claim is never allowed to fill the gap (it is recorded for the audit
  trail and marked unhonoured).
- ``escalate_on_observed_failure`` escalates **only** on observed difficulty
  (a counted failure at the current tier).  A client asking for a higher tier
  changes nothing: the escalation is delegated to the chooser, which walks
  exactly one rung up its ladder, and a request with zero observed failures is
  refused rather than granted.

The chooser itself is *injected*, not imported: the ``gateway/finops``
modules are plain scripts imported with that directory on ``sys.path`` (see
``gateway/finops/tests/conftest.py``), so a chat lane cannot import them
without dragging the whole gateway directory onto the path.  Injection keeps
this lane's runtime dependency-free while the wrapped gate proves the real
chooser behaves this way (``scripts/check-chat-finops.sh``).

Vocabulary consumed, never redefined: the tier ids are the chooser's ladder
keys (``L0``/``L1``/``L2`` from ``gateway/finops/tiers.yaml``), passed in as
the ladder's own key list when a caller wants claims validated against it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Sequence

#: Where the authoritative tier came from.
TIER_SOURCE_RECORD = "gateway_record"
TIER_SOURCE_NONE = "unresolved"

#: Stable finding codes (the gate greps for these).
CODE_UNRESOLVED_TIER = "CHAT-UNRESOLVED-TIER"
CODE_UNKNOWN_TIER_CLAIM = "CHAT-UNKNOWN-TIER-CLAIM"
CODE_ESCALATION_NOT_OBSERVED = "CHAT-ESCALATION-NOT-OBSERVED"


class TierUnresolvedError(ValueError):
    """The turn carries no authoritative tier (no chooser routing stamp)."""

    code = CODE_UNRESOLVED_TIER


class TierClaimError(ValueError):
    """A client tier claim is not a tier this ladder knows."""

    code = CODE_UNKNOWN_TIER_CLAIM


class EscalationRefused(ValueError):
    """An escalation was requested with no observed difficulty behind it."""

    code = CODE_ESCALATION_NOT_OBSERVED


@dataclass(frozen=True)
class TierResolution:
    """The authoritative tier of a turn, plus what the client claimed.

    ``claim_honoured`` is always ``False`` — a claim is never the authority —
    and ``claim_agrees`` records whether the claim happened to match the
    chooser's stamp (observability only).
    """

    tier: Optional[str]
    source: str
    client_tier: Optional[str] = None
    claim_honoured: bool = False
    claim_agrees: bool = False
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tier": self.tier,
            "source": self.source,
            "clientTier": self.client_tier,
            "claimHonoured": self.claim_honoured,
            "claimAgrees": self.claim_agrees,
            "reason": self.reason,
        }


def resolve_turn_tier(
    recorded_tier: Optional[str],
    *,
    client_tier: Optional[str] = None,
    ladder_keys: Optional[Sequence[str]] = None,
) -> TierResolution:
    """Resolve the turn's tier from the chooser's routing stamp.

    ``recorded_tier`` is the tier the chooser stamped on the gateway call
    record — the authority.  ``client_tier`` (when the caller passes one) is
    validated against ``ladder_keys`` when supplied, then *ignored*.

    Raises ``TierClaimError`` for a claim outside the ladder vocabulary, and
    ``TierUnresolvedError`` when the record carries no tier — a client claim
    is explicitly *not* accepted as a substitute for the missing authority.
    """
    claim = client_tier.strip() if isinstance(client_tier, str) else None

    if claim is not None and ladder_keys is not None:
        if claim not in tuple(ladder_keys):
            raise TierClaimError(
                f"{CODE_UNKNOWN_TIER_CLAIM}: client tier claim {claim!r} is not "
                f"a tier of this ladder ({', '.join(str(k) for k in ladder_keys)})"
            )

    stamp = recorded_tier.strip() if isinstance(recorded_tier, str) else None
    if not stamp:
        detail = "the gateway call record carries no routing stamp"
        if claim is not None:
            detail += (
                f"; the client claim {claim!r} cannot stand in for the chooser "
                "— a surface that fills the gap with a claim is a second "
                "authority and is not trusted"
            )
        raise TierUnresolvedError(f"{CODE_UNRESOLVED_TIER}: {detail}")

    agrees = claim is not None and claim == stamp
    if claim is None:
        reason = f"tier {stamp} is the chooser's routing stamp"
    elif agrees:
        reason = (
            f"tier {stamp} is the chooser's routing stamp; the client claim "
            "adds nothing (claims are never the authority)"
        )
    else:
        reason = (
            f"tier {stamp} is the chooser's routing stamp; the client claim "
            f"{claim!r} is recorded and ignored"
        )
    return TierResolution(
        tier=stamp,
        source=TIER_SOURCE_RECORD,
        client_tier=claim,
        claim_honoured=False,
        claim_agrees=agrees,
        reason=reason,
    )


def escalate_on_observed_failure(
    chooser: Any,
    choice: Any,
    *,
    observed_failures: int,
    client_requested_tier: Optional[str] = None,
    tokens: Optional[int] = None,
) -> Any:
    """Escalate one rung up the chooser's ladder — observed difficulty only.

    ``observed_failures`` is the *counted* difficulty at the current tier; at
    zero the request is refused (``EscalationRefused``) instead of granted.
    ``client_requested_tier`` is accepted so the surface can carry what the
    client asked for, and is deliberately not consulted: the rung is chosen
    by the injected chooser (``ModelChooser.escalate_on_failure``), which caps
    at the task class's ``maxTier``.
    """
    if observed_failures < 1:
        raise EscalationRefused(
            f"{CODE_ESCALATION_NOT_OBSERVED}: escalation is observed-difficulty "
            f"only — 0 observed failures at tier "
            f"{getattr(choice, 'tier', None)!r}"
            + (
                f"; a client request for {client_requested_tier!r} is not "
                "observed difficulty"
                if client_requested_tier is not None
                else ""
            )
        )
    return chooser.escalate_on_failure(
        choice,
        trigger=f"observed-failure x{observed_failures}",
        tokens=tokens,
    )
