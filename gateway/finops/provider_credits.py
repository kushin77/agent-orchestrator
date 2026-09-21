#!/usr/bin/env python3
"""Prepaid provider-credit budget: declaration loader + credit arithmetic (#1559).

`gateway/finops/` carries two budget axes over USD spend in ``budgets.yaml``:
per-tenant (issue #17) and per-role (issue #633). This module adds the third
axis - a provider that bills against **prepaid account credits** - for the
declaration in ``provider-credits.yaml``.

Why a declaration rather than a hardcoded table: the FinOps doctrine that a
price must come from a stated source holds here too. Each provider's numbers are
declared with the contract they came from, and **unknown -> null, never 0** (the
rate-card estimator rule, issue #33) is enforced by the arithmetic: a model whose
price is declared unpublished answers ``None`` for its debit, so an unmetered
call surfaces in the ledger as unmetered rather than as free.

Nous Research (issue #1559) is the first declared provider. Its API contract
(``https://portal.nousresearch.com/api-docs``) documents API-key billing against
account credits but declares **no balance endpoint**, so the balance is tracked
against the declared plan ceiling plus recorded top-ups
(``balanceTracking: manual-top-ups``) rather than read back - a budget meter, not
a ledger readback, and it says so.

Validation is fail-closed: an unknown schema version, a missing/ill-typed plan,
a half-declared price (``pricePublished: true`` with a null rate, or the
reverse), a non-ascending or non-positive top-up list, or a top-up amount that is
not a declared purchase amount all raise ``CreditDeclarationError`` instead of
being silently accepted.

Standalone module (stdlib + PyYAML, the repo's declared dependency); no
cross-package imports, so the pytest bootstrap
(``gateway/finops/tests/conftest.py``) simply puts this directory on ``sys.path``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import yaml  # type: ignore

PKG_DIR = Path(__file__).resolve().parent
CREDITS_PATH = PKG_DIR / "provider-credits.yaml"

SCHEMA_VERSION = 1
BILLING_CREDITS = "credits"
TRACKING_MANUAL_TOP_UPS = "manual-top-ups"

TOKENS_PER_MTOK = 1_000_000.0


class CreditDeclarationError(ValueError):
    """A provider-credit declaration failed validation (fail closed)."""


class UnknownProviderError(CreditDeclarationError):
    """The requested provider has no credit declaration."""


class UnknownPlanError(CreditDeclarationError):
    """The requested plan is not declared for that provider."""


class UnknownTopUpError(CreditDeclarationError):
    """A top-up amount outside the provider's declared purchase set."""


# --------------------------------------------------------------------------- #
# Declaration model
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class CreditPlan:
    """One subscription plan: what it costs and the credits it grants."""

    name: str
    price_usd_per_month: float
    granted_credits_usd: float
    rollover_cap_usd: float


@dataclass(frozen=True)
class ModelRate:
    """One chat model's declared price, published or explicitly not."""

    model: str
    context_window: int
    price_published: bool
    input_usd_per_mtok: Optional[float] = None
    output_usd_per_mtok: Optional[float] = None

    def cost_usd(self, input_tokens: int, output_tokens: int) -> Optional[float]:
        """Credits debited for one call, or ``None`` when the price is unknown.

        ``None`` means *unmetered*, never zero: an unpublished price must never
        be reported as a free call. A negative token count is a caller bug and
        is refused rather than clamped.
        """
        if input_tokens < 0 or output_tokens < 0:
            raise ValueError("token counts must not be negative")
        if not self.price_published:
            return None
        if self.input_usd_per_mtok is None or self.output_usd_per_mtok is None:
            return None
        return (
            (input_tokens / TOKENS_PER_MTOK) * self.input_usd_per_mtok
            + (output_tokens / TOKENS_PER_MTOK) * self.output_usd_per_mtok
        )


@dataclass(frozen=True)
class ProviderCreditBudget:
    """The declared credit budget for one provider."""

    provider: str
    display_name: str
    billing: str
    plans: Mapping[str, CreditPlan] = field(default_factory=dict)
    top_ups_usd: Tuple[float, ...] = ()
    models: Mapping[str, ModelRate] = field(default_factory=dict)
    base_url: str = ""
    docs: str = ""
    balance_endpoint: Optional[str] = None
    balance_tracking: str = TRACKING_MANUAL_TOP_UPS
    balance_tracking_note: str = ""

    # -- plans ------------------------------------------------------------- #
    def plan(self, plan: str) -> CreditPlan:
        """The named plan; an undeclared plan is refused by name."""
        try:
            return self.plans[plan]
        except KeyError:
            raise UnknownPlanError(
                f"{self.provider}: no declared plan {plan!r}; "
                f"declared: {', '.join(sorted(self.plans)) or '(none)'}"
            ) from None

    def credit_ceiling_usd(self, plan: str) -> float:
        """Credits granted per period by ``plan`` (the declared budget line)."""
        return self.plan(plan).granted_credits_usd

    def remaining_credits_usd(
        self,
        plan: str,
        top_ups_usd: Sequence[float] = (),
        debited_usd: float = 0.0,
    ) -> float:
        """Ceiling plus recorded top-ups, minus what has been debited.

        Every top-up amount must be one the provider actually sells; an
        undeclared amount is refused so a typo cannot invent a balance.
        """
        ceiling = self.credit_ceiling_usd(plan)
        for amount in top_ups_usd:
            if amount not in self.top_ups_usd:
                raise UnknownTopUpError(
                    f"{self.provider}: top-up {amount} is not a declared "
                    f"purchase amount; declared: {list(self.top_ups_usd)}"
                )
            ceiling += amount
        return ceiling - debited_usd

    # -- models / metering ------------------------------------------------- #
    def rate_for(self, model: str) -> Optional[ModelRate]:
        """The declared rate for ``model``, or ``None`` when undeclared."""
        return self.models.get(model)

    def debit_usd(
        self, model: str, input_tokens: int, output_tokens: int
    ) -> Optional[float]:
        """Credits one call debits, or ``None`` when the price is unknown.

        An undeclared model is unmetered (``None``) rather than refused, for the
        same reason a rate-card miss is: the call happened, and the honest
        answer is that its cost is not known here.
        """
        rate = self.rate_for(model)
        if rate is None:
            return None
        return rate.cost_usd(input_tokens, output_tokens)

    def unmetered_models(self) -> Tuple[str, ...]:
        """Declared models whose price is not published (sorted)."""
        return tuple(
            sorted(m for m, rate in self.models.items() if not rate.price_published)
        )

    # -- ledger projection ------------------------------------------------- #
    def budget_line(
        self,
        plan: str,
        top_ups_usd: Sequence[float] = (),
        debited_usd: float = 0.0,
    ) -> Dict[str, Any]:
        """The FinOps row for this provider's credit budget.

        ``balanceSource`` is deliberately explicit: a meter derived from
        declared ceilings and recorded top-ups is not a readback of the
        provider's balance, and the row must not read as if it were.
        """
        return {
            "provider": self.provider,
            "billing": self.billing,
            "plan": plan,
            "ceilingUsd": self.credit_ceiling_usd(plan),
            "topUpsUsd": [float(a) for a in top_ups_usd],
            "debitedUsd": float(debited_usd),
            "remainingUsd": self.remaining_credits_usd(
                plan, top_ups_usd, debited_usd
            ),
            "balanceSource": self.balance_tracking,
            "balanceEndpoint": self.balance_endpoint,
        }


@dataclass(frozen=True)
class CreditsTable:
    """Every declared provider, loaded and validated."""

    providers_by_name: Mapping[str, ProviderCreditBudget] = field(default_factory=dict)
    path: str = ""

    def providers(self) -> Tuple[str, ...]:
        return tuple(sorted(self.providers_by_name))

    def card(self, provider: str) -> Optional[ProviderCreditBudget]:
        return self.providers_by_name.get(provider)

    def card_or_raise(self, provider: str) -> ProviderCreditBudget:
        card = self.card(provider)
        if card is None:
            raise UnknownProviderError(
                f"no credit declaration for provider {provider!r}; declared: "
                f"{', '.join(self.providers()) or '(none)'}"
            )
        return card

    def debit_usd(
        self, provider: str, model: str, input_tokens: int, output_tokens: int
    ) -> Optional[float]:
        """Credits a call debits, or ``None`` when provider or model is unknown.

        An undeclared **provider** is also ``None`` here rather than a refusal:
        the caller is asking "what does this call cost", and for an undeclared
        provider the answer is that this declaration cannot say.
        """
        card = self.card(provider)
        if card is None:
            return None
        return card.debit_usd(model, input_tokens, output_tokens)


# --------------------------------------------------------------------------- #
# Loader (fail closed)
# --------------------------------------------------------------------------- #


def _number(value: Any, where: str, *, minimum: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CreditDeclarationError(f"{where}: expected a number, got {value!r}")
    number = float(value)
    if number < minimum:
        raise CreditDeclarationError(
            f"{where}: expected a number >= {minimum}, got {number}"
        )
    return number


def _text(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CreditDeclarationError(f"{where}: expected a non-empty string")
    return value


def _plan(name: str, raw: Any, where: str) -> CreditPlan:
    if not isinstance(raw, dict):
        raise CreditDeclarationError(f"{where}: expected a mapping")
    return CreditPlan(
        name=name,
        price_usd_per_month=_number(
            raw.get("priceUsdPerMonth"), f"{where}.priceUsdPerMonth"
        ),
        granted_credits_usd=_number(
            raw.get("grantedCreditsUsd"), f"{where}.grantedCreditsUsd"
        ),
        rollover_cap_usd=_number(
            raw.get("rolloverCapUsd"), f"{where}.rolloverCapUsd"
        ),
    )


def _rate(model: str, raw: Any, where: str) -> ModelRate:
    if not isinstance(raw, dict):
        raise CreditDeclarationError(f"{where}: expected a mapping")
    published = raw.get("pricePublished")
    if not isinstance(published, bool):
        raise CreditDeclarationError(
            f"{where}.pricePublished: expected a boolean, got {published!r}"
        )
    window = raw.get("contextWindow")
    if isinstance(window, bool) or not isinstance(window, int) or window <= 0:
        raise CreditDeclarationError(
            f"{where}.contextWindow: expected a positive integer, got {window!r}"
        )
    raw_in, raw_out = raw.get("inputUsdPerMTok"), raw.get("outputUsdPerMTok")
    declared = [v is not None for v in (raw_in, raw_out)]
    # Half-declared prices are refused: a published price with a null rate (or a
    # null price with a rate) is a declaration bug, not a zero.
    if published and not all(declared):
        raise CreditDeclarationError(
            f"{where}: pricePublished is true but a rate is null "
            f"(input={raw_in!r}, output={raw_out!r})"
        )
    if not published and any(declared):
        raise CreditDeclarationError(
            f"{where}: pricePublished is false but a rate is declared "
            f"(input={raw_in!r}, output={raw_out!r})"
        )
    if not published:
        return ModelRate(model=model, context_window=window, price_published=False)
    return ModelRate(
        model=model,
        context_window=window,
        price_published=True,
        input_usd_per_mtok=_number(raw_in, f"{where}.inputUsdPerMTok"),
        output_usd_per_mtok=_number(raw_out, f"{where}.outputUsdPerMTok"),
    )


def _top_ups(raw: Any, where: str) -> Tuple[float, ...]:
    if not isinstance(raw, list) or not raw:
        raise CreditDeclarationError(f"{where}: expected a non-empty list")
    amounts = tuple(_number(a, f"{where}[{i}]") for i, a in enumerate(raw))
    if any(a <= 0 for a in amounts):
        raise CreditDeclarationError(f"{where}: every amount must be positive")
    if list(amounts) != sorted(amounts) or len(set(amounts)) != len(amounts):
        raise CreditDeclarationError(
            f"{where}: amounts must be strictly ascending, got {list(amounts)}"
        )
    return amounts


def _budget(provider: str, raw: Any, where: str) -> ProviderCreditBudget:
    if not isinstance(raw, dict):
        raise CreditDeclarationError(f"{where}: expected a mapping")
    plans_raw = raw.get("plans")
    if not isinstance(plans_raw, dict) or not plans_raw:
        raise CreditDeclarationError(f"{where}.plans: expected a non-empty mapping")
    models_raw = raw.get("models")
    if not isinstance(models_raw, dict) or not models_raw:
        raise CreditDeclarationError(f"{where}.models: expected a non-empty mapping")

    endpoint = raw.get("balanceEndpoint")
    if endpoint is not None and not isinstance(endpoint, str):
        raise CreditDeclarationError(
            f"{where}.balanceEndpoint: expected null or a string"
        )

    return ProviderCreditBudget(
        provider=provider,
        display_name=_text(raw.get("displayName"), f"{where}.displayName"),
        billing=_text(raw.get("billing"), f"{where}.billing"),
        plans={
            name: _plan(name, body, f"{where}.plans.{name}")
            for name, body in plans_raw.items()
        },
        top_ups_usd=_top_ups(raw.get("topUpsUsd"), f"{where}.topUpsUsd"),
        models={
            model: _rate(model, body, f"{where}.models.{model}")
            for model, body in models_raw.items()
        },
        base_url=str(raw.get("baseUrl") or ""),
        docs=str(raw.get("docs") or ""),
        balance_endpoint=endpoint,
        balance_tracking=_text(
            raw.get("balanceTracking"), f"{where}.balanceTracking"
        ),
        balance_tracking_note=str(raw.get("balanceTrackingNote") or ""),
    )


def load_credits(path: Optional[Path] = None) -> CreditsTable:
    """Load and validate the provider-credit declaration (fail closed)."""
    target = Path(path) if path is not None else CREDITS_PATH
    try:
        document = yaml.safe_load(target.read_text(encoding="utf-8"))
    except OSError as exc:
        raise CreditDeclarationError(f"{target}: unreadable ({exc})") from exc
    except yaml.YAMLError as exc:
        raise CreditDeclarationError(f"{target}: not valid YAML ({exc})") from exc

    if not isinstance(document, dict):
        raise CreditDeclarationError(f"{target}: expected a top-level mapping")

    version = document.get("schemaVersion")
    if version != SCHEMA_VERSION:
        raise CreditDeclarationError(
            f"{target}: schemaVersion {version!r} is not the supported "
            f"{SCHEMA_VERSION}"
        )

    providers_raw = document.get("providers")
    if not isinstance(providers_raw, dict) or not providers_raw:
        raise CreditDeclarationError(f"{target}.providers: expected a non-empty mapping")

    table: Dict[str, ProviderCreditBudget] = {}
    for provider, raw in providers_raw.items():
        name = _text(provider, f"{target}.providers: provider key")
        table[name] = _budget(
            name, raw, f"{target}.providers.{name}"
        )
    return CreditsTable(providers_by_name=table, path=str(target))


__all__: List[str] = [
    "BILLING_CREDITS",
    "CREDITS_PATH",
    "CreditDeclarationError",
    "CreditPlan",
    "CreditsTable",
    "ModelRate",
    "ProviderCreditBudget",
    "TRACKING_MANUAL_TOP_UPS",
    "UnknownPlanError",
    "UnknownProviderError",
    "UnknownTopUpError",
    "load_credits",
]
