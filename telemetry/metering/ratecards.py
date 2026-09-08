"""telemetry/metering — multi-provider rate cards + cost estimator (issue #33).

Rate cards are YAML, one file per provider under ``rate_cards/``.  A model's
entry declares a ``standard`` USD price per 1,000,000 tokens (input and
output split — output is priced far above input on every provider, so a
single blended rate would misprice any non-1:1 workload) and may declare a
``longContext`` tier that both rates step up to once the input/prompt token
count exceeds ``thresholdTokens`` (the Gemini/Vertex long-context
convention).

The estimator's cardinal rule is **unknown -> None, never 0** (capitalized
from ``capital-underwriting`` ``aiCostRates.ts``): a provider/model with no
rate-card entry returns ``None`` so the intake can flag the call unmetered
(fail closed) instead of silently pricing it as zero.  The only deliberate
$0 rates on the shipped cards are the local Ollama models, whose card entry
declares ``local: true`` and an explicit $0 — that is a priced $0, not an
"unknown" $0.

These are POINT-IN-TIME list prices for cost estimation only — not live
pricing, and not customer billing.  Providers change prices without notice;
the figures are dated in the card files and need periodic review.  There is
deliberately no live-pricing fetch (cost figures must stay deterministic and
reconcilable against a specific historical rate).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

import yaml

RATE_CARD_SCHEMA_VERSION = 1

#: Default directory holding the per-provider YAML rate cards.
DEFAULT_RATE_CARD_DIR = Path(__file__).resolve().parent / "rate_cards"


class RateCardError(ValueError):
    """A rate-card file failed schema validation (fail closed on load)."""


@dataclass(frozen=True)
class ModelRate:
    """USD price per 1,000,000 tokens for one model (optionally context-tiered)."""

    input_usd_per_million: float
    output_usd_per_million: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "inputUsdPerMillion": self.input_usd_per_million,
            "outputUsdPerMillion": self.output_usd_per_million,
        }


@dataclass(frozen=True)
class RateCardEntry:
    """A model's card: a standard rate plus an optional long-context tier."""

    model: str
    standard: ModelRate
    long_context: Optional[ModelRate] = None
    long_context_threshold_tokens: Optional[int] = None
    local: bool = False

    def rate_for(self, input_tokens: int) -> ModelRate:
        """Pick the applicable rate for an input/prompt token count.

        The long-context tier applies once the input (prompt) token count
        strictly exceeds the model's threshold; otherwise the standard rate
        applies.
        """
        if (
            self.long_context is not None
            and self.long_context_threshold_tokens is not None
            and input_tokens > self.long_context_threshold_tokens
        ):
            return self.long_context
        return self.standard

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "model": self.model,
            "standard": self.standard.to_dict(),
            "local": self.local,
        }
        if self.long_context is not None:
            payload["longContext"] = self.long_context.to_dict()
            payload["longContextThresholdTokens"] = self.long_context_threshold_tokens
        return payload


@dataclass(frozen=True)
class CostEstimate:
    """A resolved cost figure plus the rate that produced it."""

    cost_usd: float
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    long_context_applied: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "inputTokens": self.input_tokens,
            "outputTokens": self.output_tokens,
            "costUsd": round(self.cost_usd, 10),
            "longContextApplied": self.long_context_applied,
        }


def _parse_rate(raw: Any, where: str) -> ModelRate:
    if not isinstance(raw, Mapping):
        raise RateCardError(f"{where}: expected a mapping with input/output prices")
    inp = raw.get("inputUsdPerMillion")
    out = raw.get("outputUsdPerMillion")
    if not isinstance(inp, (int, float)) or not isinstance(out, (int, float)):
        raise RateCardError(
            f"{where}: inputUsdPerMillion/outputUsdPerMillion must be numbers"
        )
    if inp < 0 or out < 0:
        raise RateCardError(f"{where}: negative prices are invalid")
    return ModelRate(float(inp), float(out))


def load_card_file(path: Path) -> "RateCard":
    """Load and validate one provider YAML rate-card file.

    Raises ``RateCardError`` on any schema violation (missing provider,
    unparseable YAML, non-numeric or negative prices, invalid long-context
    tiers) — a malformed card fails closed at load time, never silently
    drops a model or fabricates a rate.
    """
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise RateCardError(f"{path.name}: unparseable YAML: {exc}") from exc
    if not isinstance(raw, Mapping):
        raise RateCardError(f"{path.name}: card root must be a mapping")
    if raw.get("schemaVersion") != RATE_CARD_SCHEMA_VERSION:
        raise RateCardError(
            f"{path.name}: unsupported schemaVersion {raw.get('schemaVersion')!r}"
        )
    provider = raw.get("provider")
    if not isinstance(provider, str) or not provider:
        raise RateCardError(f"{path.name}: missing 'provider'")
    models_raw = raw.get("models")
    if not isinstance(models_raw, Mapping) or not models_raw:
        raise RateCardError(f"{path.name}: missing or empty 'models' mapping")

    entries: Dict[str, RateCardEntry] = {}
    for model, cfg in models_raw.items():
        where = f"{path.name}: models.{model}"
        if not isinstance(cfg, Mapping):
            raise RateCardError(f"{where}: model config must be a mapping")
        standard = _parse_rate(cfg.get("standard"), f"{where}.standard")
        long_context = None
        threshold = None
        if "longContext" in cfg or "longContextThresholdTokens" in cfg:
            if "longContext" not in cfg or "longContextThresholdTokens" not in cfg:
                raise RateCardError(
                    f"{where}: longContext requires both 'longContext' and "
                    "'longContextThresholdTokens'"
                )
            long_context = _parse_rate(cfg.get("longContext"), f"{where}.longContext")
            threshold = cfg.get("longContextThresholdTokens")
            if not isinstance(threshold, int) or threshold <= 0:
                raise RateCardError(
                    f"{where}: longContextThresholdTokens must be a positive integer"
                )
            if standard == long_context:
                raise RateCardError(
                    f"{where}: longContext duplicates standard (no actual tier)"
                )
        entries[str(model)] = RateCardEntry(
            model=str(model),
            standard=standard,
            long_context=long_context,
            long_context_threshold_tokens=threshold,
            local=bool(cfg.get("local", False)),
        )
    return RateCard(provider=provider, entries=entries, source=path.name)


@dataclass
class RateCard:
    """One provider's loaded rate card."""

    provider: str
    entries: Dict[str, RateCardEntry]
    source: str

    def lookup(self, model: str) -> Optional[RateCardEntry]:
        """The card entry for an exact model id, or ``None`` when unknown."""
        return self.entries.get(model)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "source": self.source,
            "models": {m: e.to_dict() for m, e in self.entries.items()},
        }


class RateCardStore:
    """The aggregate multi-provider rate card (loads every YAML under a dir)."""

    def __init__(self, cards: Optional[List[RateCard]] = None) -> None:
        self._cards: Dict[str, RateCard] = {}
        for card in cards or []:
            self._register(card)

    def _register(self, card: RateCard) -> None:
        if card.provider in self._cards:
            raise RateCardError(
                f"duplicate provider card: {card.provider!r} ({card.source})"
            )
        self._cards[card.provider] = card

    @classmethod
    def load_dir(cls, directory: Path = DEFAULT_RATE_CARD_DIR) -> "RateCardStore":
        """Load every ``*.yaml``/``*.yml`` rate card in ``directory``."""
        store = cls()
        for path in sorted(Path(directory).glob("*.y*ml")):
            store._register(load_card_file(path))
        if not store.providers():
            raise RateCardError(f"no rate cards found under {directory}")
        return store

    def providers(self) -> List[str]:
        """Sorted list of providers with a loaded card."""
        return sorted(self._cards)

    def card(self, provider: str) -> Optional[RateCard]:
        """The loaded card for one provider, or ``None`` if unknown."""
        return self._cards.get(provider)

    def lookup(self, provider: str, model: str) -> Optional[RateCardEntry]:
        """Resolve an exact (provider, model) entry, or ``None`` if unknown."""
        card = self._cards.get(provider)
        if card is None:
            return None
        return card.lookup(model)

    def estimate(
        self,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
    ) -> Optional[CostEstimate]:
        """Estimate the USD cost of one call from the rate card.

        Returns ``None`` for a provider/model with no rate-card entry — the
        caller must treat that as *unknown*, never as zero.  Token counts are
        clamped to non-negative finite values before pricing (a negative or
        NaN count is honest ``0`` usage, mirroring the fleet estimator).
        """
        entry = self.lookup(provider, model)
        if entry is None:
            return None
        inp = _safe_tokens(input_tokens)
        out = _safe_tokens(output_tokens)
        rate = entry.rate_for(inp)
        cost = (inp / 1_000_000) * rate.input_usd_per_million + (
            out / 1_000_000
        ) * rate.output_usd_per_million
        return CostEstimate(
            cost_usd=cost,
            provider=provider,
            model=model,
            input_tokens=inp,
            output_tokens=out,
            long_context_applied=rate is entry.long_context,
        )

    def fingerprint(self) -> str:
        """Deterministic hash over every loaded card (config-change detection)."""
        digest = hashlib.sha256()
        for provider in self.providers():
            card = self._cards[provider]
            digest.update(provider.encode("utf-8"))
            for model in sorted(card.entries):
                digest.update(model.encode("utf-8"))
                digest.update(
                    yaml.safe_dump(
                        card.entries[model].to_dict(), sort_keys=True
                    ).encode("utf-8")
                )
        return digest.hexdigest()


def _safe_tokens(count: int) -> int:
    try:
        value = float(count)
    except (TypeError, ValueError):
        return 0
    if not (value == value) or value in (float("inf"), float("-inf")) or value < 0:
        return 0
    return int(value)
