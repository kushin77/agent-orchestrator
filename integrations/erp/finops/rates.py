"""The ERP lane's price list, and the coverage rule that keeps it honest (#654).

The issue's second acceptance criterion is that a roll-up produces **per-tenant
ERP usage + cost**. A cost figure needs a price, and a price needs a declaration
somewhere a reviewer can read, so this lane ships one in
``catalog/rate-card.json`` and validates it against
``schema/rate-card.schema.json`` with the keyword freeze of :mod:`.schema`.

Two design decisions carry the weight:

* **there is no wildcard.** A rate card that can price "everything else" cannot
  be checked for completeness, so every ERP document kind the core model
  (ERP-02, ``integrations/erp/core``) declares must have an explicit entry.
  :meth:`RateCard.coverage` reports the kinds that do not, and the lane's check
  runs it against the *live* core model, so a kind added upstream is a measured
  gap here rather than an unpriced operation discovered on a bill.
* **"unpriced" is a declaration, not an omission.** An entry may declare
  ``priced: false``, which means *this operation is metered and this lane
  publishes no price for it*. It is then recorded as unmetered usage and no cost
  is ever invented for it (:mod:`.rollup` refuses to bill it). An entry that is
  simply **absent** is a different thing and is refused by name
  (``rate-missing``) — silence is not a price.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple, Union

from . import schema as schemas
from .model import OPERATIONS, SCHEMA_VERSION, Finding, Refused

__all__ = ["DEFAULT_RATE_CARD", "Rate", "RateCard", "load", "load_and_validate"]

PACKAGE = Path(__file__).resolve().parent
RATE_CARD_SCHEMA = PACKAGE / "schema" / "rate-card.schema.json"
DEFAULT_RATE_CARD = PACKAGE / "catalog" / "rate-card.json"

#: Minimum length of an ISO-4217 currency code, so a two-letter string is not
#: accepted as a currency merely because it is a string.
_CURRENCY_LEN = 3


@dataclass(frozen=True)
class Rate:
    """One declared price: a document kind, an operation, and what it costs.

    ``priced`` is the honesty flag. ``priced: false`` with ``price_usd=None``
    means *declared and deliberately unpriced* (the operation is metered, its
    cost is unknown to this lane, and no number is fabricated for it). A
    :class:`Rate` with ``priced`` true always carries a positive ``price_usd``,
    which is enforced in :meth:`__post_init__` so the invariant holds for any
    caller, not only for the loader.
    """

    kind: str
    operation: str
    priced: bool
    price_usd: Optional[float] = None
    currency: str = "USD"
    note: str = ""

    def __post_init__(self) -> None:
        if self.operation not in OPERATIONS:
            raise Refused(
                "unknown-operation",
                f"{self.operation!r} is not one of {', '.join(OPERATIONS)}",
            )
        if self.priced:
            if self.price_usd is None:
                raise Refused(
                    "rate-invalid",
                    f"{self.kind}/{self.operation} is priced but carries no price",
                )
            if self.price_usd <= 0:
                raise Refused(
                    "rate-invalid",
                    f"{self.kind}/{self.operation} price {self.price_usd!r} is not positive",
                )
        elif self.price_usd is not None:
            raise Refused(
                "rate-invalid",
                f"{self.kind}/{self.operation} is declared unpriced but carries "
                f"price {self.price_usd!r}",
            )
        if len(self.currency) != _CURRENCY_LEN:
            raise Refused("unknown-currency", f"{self.currency!r} is not a currency code")

    @property
    def key(self) -> Tuple[str, str]:
        return (self.kind, self.operation)

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "kind": self.kind,
            "operation": self.operation,
            "priced": self.priced,
            "currency": self.currency,
        }
        if self.price_usd is not None:
            payload["price"] = round(self.price_usd, 8)
        if self.note:
            payload["note"] = self.note
        return payload


@dataclass(frozen=True)
class RateCard:
    """A validated price list, and the lookups the meter and roll-up use."""

    currency: str
    supported_currencies: Tuple[str, ...]
    rates: Tuple[Rate, ...]
    source: str = "<memory>"
    schema_version: str = SCHEMA_VERSION

    # --- lookups ---------------------------------------------------------- #

    def rate_for(self, kind: str, operation: str) -> Rate:
        """The declared rate for one operation, or refuse ``rate-missing``.

        An absent entry is refused rather than defaulted: the whole point of the
        coverage rule is that this cannot happen silently, and a lookup that
        returned a default would be the one place the rule could be bypassed.
        """
        for rate in self.rates:
            if rate.kind == kind and rate.operation == operation:
                return rate
        raise Refused(
            "rate-missing",
            f"no rate is declared for {kind}/{operation}",
            where=self.source,
        )

    def price_for(self, kind: str, operation: str) -> Optional[float]:
        """The price of one operation, or ``None`` when it is declared unpriced."""
        rate = self.rate_for(kind, operation)
        return rate.price_usd if rate.priced else None

    def kinds(self) -> Tuple[str, ...]:
        """Every document kind this card prices (sorted, deduplicated)."""
        return tuple(sorted({rate.kind for rate in self.rates}))

    def operations_for(self, kind: str) -> Tuple[str, ...]:
        """Every operation priced for ``kind`` (declaration order)."""
        return tuple(rate.operation for rate in self.rates if rate.kind == kind)

    def coverage(self, declared_kinds: Iterable[str], lifecycle_kinds: Iterable[str]) -> List[Finding]:
        """Every way the card fails to cover the live core document model.

        ``declared_kinds`` is every kind the core model declares; ``create`` is
        priced for all of them. ``lifecycle_kinds`` is the subset that declares
        a lifecycle, and only those are priced for ``transition`` — pricing a
        transition a master document cannot perform would be a fabricated
        operation. Findings are returned rather than raised so the caller
        reports the whole gap at once.
        """
        findings: List[Finding] = []
        priced = {(rate.kind, rate.operation) for rate in self.rates}
        for kind in sorted(set(declared_kinds)):
            if (kind, "create") not in priced:
                findings.append(
                    Finding(
                        "rate-missing",
                        f"{kind} is declared by the core document model but has no "
                        f"create rate",
                        where=self.source,
                    )
                )
        for kind in sorted(set(lifecycle_kinds)):
            if (kind, "transition") not in priced:
                findings.append(
                    Finding(
                        "rate-missing",
                        f"{kind} declares a lifecycle but has no transition rate",
                        where=self.source,
                    )
                )
        for rate in self.rates:
            if rate.operation == "transition" and rate.kind not in set(lifecycle_kinds):
                findings.append(
                    Finding(
                        "rate-invalid",
                        f"{rate.kind} is priced for transition but declares no lifecycle",
                        where=self.source,
                    )
                )
        return findings

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "currency": self.currency,
            "supportedCurrencies": list(self.supported_currencies),
            "rates": [rate.to_dict() for rate in self.rates],
            "source": self.source,
        }


def _read(source: Union[str, Path, Mapping[str, Any]]) -> Tuple[Mapping[str, Any], str]:
    if isinstance(source, Mapping):
        return source, "<memory>"
    path = Path(source)
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, Mapping):
        raise Refused("rate-card-invalid", f"{path}: a rate card must be a JSON object")
    return payload, str(path)


def load(source: Union[str, Path, Mapping[str, Any]] = DEFAULT_RATE_CARD) -> RateCard:
    """Load, validate and interpret a rate card (path or in-memory mapping).

    The in-memory seam exists because every negative control needs a card that
    is *almost* right, and a control that has to write a file to be provoked is
    a control that will not be written.
    """
    raw, where = _read(source)

    violations = schemas.validate(
        dict(raw), schemas.load_and_refuse(RATE_CARD_SCHEMA), where=where
    )
    if violations:
        raise Refused("rate-card-invalid", "; ".join(violations), where=where)

    card_currency = str(raw["currency"])
    supported = tuple(str(code) for code in raw.get("supportedCurrencies") or ())
    if card_currency not in supported:
        raise Refused(
            "unknown-currency",
            f"the card's own currency {card_currency!r} is not in {list(supported)!r}",
            where=where,
        )

    rates: List[Rate] = []
    seen: Dict[Tuple[str, str], str] = {}
    for index, entry in enumerate(raw.get("rates") or []):
        entry_where = f"{where}:rates[{index}]"
        kind = str(entry["kind"])
        operation = str(entry["operation"])
        if (kind, operation) in seen:
            raise Refused(
                "rate-card-invalid",
                f"{kind}/{operation} is declared twice",
                where=entry_where,
            )
        seen[(kind, operation)] = entry_where
        currency = str(entry.get("currency") or card_currency)
        if currency not in supported:
            raise Refused(
                "unknown-currency",
                f"{currency!r} is not one of {list(supported)!r}",
                where=entry_where,
            )
        priced = bool(entry.get("priced", True))
        price = entry.get("price")
        if priced:
            if isinstance(price, bool) or not isinstance(price, (int, float)):
                raise Refused(
                    "rate-invalid",
                    f"{kind}/{operation} is priced but its price is {price!r}",
                    where=entry_where,
                )
            if price <= 0:
                raise Refused(
                    "rate-invalid",
                    f"{kind}/{operation} price {price!r} is not positive",
                    where=entry_where,
                )
        elif price is not None:
            raise Refused(
                "rate-invalid",
                f"{kind}/{operation} is declared unpriced but carries price {price!r}",
                where=entry_where,
            )
        rates.append(
            Rate(
                kind=kind,
                operation=operation,
                priced=priced,
                price_usd=float(price) if priced else None,
                currency=currency,
                note=str(entry.get("note") or ""),
            )
        )

    return RateCard(
        currency=card_currency,
        supported_currencies=supported,
        rates=tuple(rates),
        source=where,
        schema_version=str(raw["schemaVersion"]),
    )


def load_and_validate(
    source: Union[str, Path, Mapping[str, Any]] = DEFAULT_RATE_CARD,
    *,
    declared_kinds: Iterable[str] = (),
    lifecycle_kinds: Iterable[str] = (),
) -> RateCard:
    """Load a card and, when a document model is supplied, enforce coverage.

    Coverage is not part of :func:`load` because a card can be perfectly valid
    and still not cover the surface; keeping the two apart lets a caller report
    "the card is valid but incomplete" instead of one undifferentiated refusal.
    """
    card = load(source)
    findings = card.coverage(declared_kinds, lifecycle_kinds)
    if findings:
        raise Refused(
            "rate-missing",
            "; ".join(finding.detail for finding in findings),
            where=card.source,
        )
    return card
