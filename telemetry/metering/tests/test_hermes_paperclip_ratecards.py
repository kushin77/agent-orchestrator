"""hermes/paperclip rate cards (issue #953, parent #878).

Both agents have gateway providers (gateway/providers/hermes.py,
gateway/providers/paperclip.py) but no rate card before this — FinOps
metering could not attribute cost to either. This proves the two new cards
load, validate, and estimate honestly:

- hermes is a genuinely local hop (OllamaProvider subclass, requires_key=False,
  EPIC #253 frozen hermes -> hermes/ollama fallback) — same `local: true`
  explicit-$0 convention as ollama.yaml.
- paperclip is a cloud seam (OpenAICompatProvider subclass, Bearer auth,
  requires_key=True) with no per-token price list in this checkout, so it is
  NOT flagged `local: true` (that would misrepresent it as a local hop); its
  $0 is an unpriced placeholder, documented via the card's `note` field
  rather than invented.
"""

from __future__ import annotations

from telemetry.metering.ratecards import DEFAULT_RATE_CARD_DIR, RateCardStore


def test_hermes_and_paperclip_are_loaded():
    store = RateCardStore.load_dir()
    assert "hermes" in store.providers()
    assert "paperclip" in store.providers()


def test_hermes_model_is_local_explicit_zero():
    store = RateCardStore.load_dir()
    entry = store.lookup("hermes", "hermes3")
    assert entry is not None
    assert entry.local is True
    estimate = store.estimate("hermes", "hermes3", 10_000, 2_000)
    assert estimate is not None
    assert estimate.cost_usd == 0.0


def test_paperclip_model_is_unpriced_not_local():
    store = RateCardStore.load_dir()
    entry = store.lookup("paperclip", "paperclip-planner")
    assert entry is not None
    # Paperclip is a cloud operator seam (Bearer-auth, requires_key=True in
    # providers.config) -- never flagged local, unlike hermes/ollama.
    assert entry.local is False
    assert entry.standard.input_usd_per_million == 0.0
    assert entry.standard.output_usd_per_million == 0.0
    estimate = store.estimate("paperclip", "paperclip-planner", 10_000, 2_000)
    assert estimate is not None
    assert estimate.cost_usd == 0.0


def test_paperclip_card_documents_the_unpriced_placeholder():
    # The card's provenance note must say this is unpriced, not a sourced
    # local $0 -- otherwise a reader cannot tell the two apart.
    import yaml

    raw = yaml.safe_load((DEFAULT_RATE_CARD_DIR / "paperclip.yaml").read_text(encoding="utf-8"))
    assert "unpriced" in raw["note"].lower()


def test_unknown_model_on_either_card_is_still_none_not_zero():
    store = RateCardStore.load_dir()
    assert store.estimate("hermes", "hermes3-not-a-real-tier", 100, 100) is None
    assert store.estimate("paperclip", "paperclip-not-a-real-tier", 100, 100) is None
