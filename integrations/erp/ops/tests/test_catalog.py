"""The catalogue, its frozen schema, and what the gate must be able to refuse."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from integrations.erp.ops import catalog as catalog_mod
from integrations.erp.ops.catalog import Catalog
from .helpers import assert_refused


def test_the_catalogue_matches_its_frozen_schema(catalog: Catalog) -> None:
    assert catalog.lane == "erp-ops"
    assert catalog.owning_issue == 649
    assert catalog.epic == 645
    assert catalog.module == "erp"
    assert len(catalog.kinds) == 12
    assert len(catalog.postings) == 4


def test_the_lane_ships_off(catalog: Catalog) -> None:
    """GR-5: a new surface is flag-gated OFF until a reviewed go-live."""
    assert catalog.flag["id"] == "erp-module"
    assert catalog.flag["default"] == "off"


def test_every_kind_names_a_shipped_lifecycle(catalog: Catalog) -> None:
    shipped = {kind.lifecycle for kind in catalog.kinds if kind.lifecycle}
    for kind in catalog.kinds:
        assert kind.role
        assert kind.family in {"buying", "manufacturing", "stock", "accounting", "master"}
        if kind.source == "local":
            assert kind.lifecycle, f"{kind.id} declares a local family with no lifecycle"
    for lifecycle in shipped:
        assert lifecycle.endswith("-lifecycle")


def test_the_purchase_cycle_is_declared(catalog: Catalog) -> None:
    assert catalog.vocabulary("purchase-cycle") == (
        "rfq",
        "purchase-order",
        "purchase-receipt",
        "purchase-invoice",
    )


def test_every_stock_purpose_declares_its_effect_and_its_warehouses(
    catalog: Catalog,
) -> None:
    purposes = catalog.vocabulary("stock-purposes")
    for purpose in purposes:
        effects = catalog.stock_effect(purpose)
        assert effects, f"{purpose} moves nothing"
        assert set(effects) <= set(catalog.stock_warehouses_for(purpose))


def test_a_purpose_outside_the_vocabulary_is_refused(catalog: Catalog) -> None:
    assert_refused(
        lambda: catalog.stock_effect("material_teleport"),
        "unknown-vocabulary-term",
        needle="material_teleport",
    )


def test_an_undeclared_vocabulary_is_refused(catalog: Catalog) -> None:
    assert_refused(
        lambda: catalog.vocabulary("sprocket-purposes"),
        "unknown-vocabulary",
        needle="sprocket-purposes",
    )


def test_an_undeclared_posting_rule_is_refused(catalog: Catalog) -> None:
    assert_refused(
        lambda: catalog.posting("material-teleport"),
        "unknown-posting-rule",
        needle="material-teleport",
    )


def test_an_undeclared_kind_is_refused(catalog: Catalog) -> None:
    assert_refused(lambda: catalog.kind("sprocket"), "unknown-kind", needle="sprocket")


def test_every_posting_rule_names_both_sides(catalog: Catalog) -> None:
    for rule in catalog.postings:
        sides = {line["side"] for line in rule.gl}
        assert sides == {"debit", "credit"}, rule.key


def test_the_frozen_schema_is_the_one_used(tmp_path: Path) -> None:
    """A catalogue that stops declaring its lane is refused by the schema."""
    root = tmp_path / "catalog"
    shutil.copytree(catalog_mod.CATALOG_ROOT, root)
    target = root / catalog_mod.CATALOG_FILE
    document = json.loads(target.read_text(encoding="utf-8"))
    document.pop("lane")
    target.write_text(json.dumps(document), encoding="utf-8")
    assert_refused(
        lambda: catalog_mod.load(root), "declarations-invalid", needle="does not match"
    )


def test_a_schema_using_an_unenforceable_keyword_is_refused() -> None:
    """The validator refuses to run a schema it cannot enforce."""
    assert_refused(
        lambda: catalog_mod.assert_supported_schema(
            {"type": "object", "minProperties": 1}, where="scratch.schema.json"
        ),
        "unsupported-schema-keyword",
        needle="minProperties",
    )


def test_a_property_named_like_a_keyword_is_not_a_keyword() -> None:
    """``properties`` keys are property names: a property called ``enum`` is fine."""
    catalog_mod.assert_supported_schema(
        {"type": "object", "properties": {"enum": {"type": "string"}}}
    )
