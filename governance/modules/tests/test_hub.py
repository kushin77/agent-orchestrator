"""The hub side: two surfaces read, drift and seed gaps refused by name.

Every test mutates a **scratch** hub. The point of each one is that the refusal
names the module it refuses: a finding a human cannot act on is a formality.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import (
    BASE_MODULES,
    BASE_ROWS,
    BASE_SEEDS,
    codes,
    manifest,
    write_hub,
    write_repo,
)

from governance.modules import load_hub
from governance.modules.hub import REVISION_UNAVAILABLE
from governance.modules.model import CannotAssess


def test_reads_both_surfaces(hub: Path, consumer: Path) -> None:
    catalog = load_hub(hub, consumer, recorded_root="hub")
    assert [module.id for module in catalog.modules] == ["alpha", "beta", "gamma"]
    assert [row.id for row in catalog.mandatory_rows] == ["alpha", "beta"]
    assert catalog.ids == frozenset({"alpha", "beta", "gamma"})
    assert catalog.mandatory_ids == frozenset({"alpha", "beta"})
    assert catalog.refusals == ()
    assert catalog.root == "hub"


def test_seeds_resolve_to_the_shipped_template(hub: Path, consumer: Path) -> None:
    catalog = load_hub(hub, consumer)
    alpha = catalog.by_id("alpha")
    assert alpha is not None
    assert [(seed.asset, seed.path, seed.present) for seed in alpha.seeds] == [
        ("alpha.json", "templates/module/alpha.json", True)
    ]
    assert alpha.repo == "kushin77/alpha"
    assert alpha.pin == "v1.0.0"
    assert alpha.board_ref == "CMR:ONBOARD-0001 (#1)"
    assert alpha.manifest_path == "catalog/modules/alpha/module.json"


def test_missing_hub_is_cannot_assess(tmp_path: Path, consumer: Path) -> None:
    with pytest.raises(CannotAssess):
        load_hub(tmp_path / "absent", consumer)
    with pytest.raises(CannotAssess):
        # a catalog without its mandatory registry is not a catalog
        (tmp_path / "half" / "catalog").mkdir(parents=True)
        load_hub(tmp_path / "half", consumer)


def test_malformed_registry_is_cannot_assess(consumer: Path, make_hub) -> None:
    bad = make_hub("bad-header", header="id\tmodule\tassets")
    with pytest.raises(CannotAssess) as excinfo:
        load_hub(bad, consumer)
    assert "bad header" in str(excinfo.value)


def test_duplicate_module_id_refused_by_name(consumer: Path, make_hub) -> None:
    scratch = make_hub("dup", modules=tuple(BASE_MODULES))
    copied = scratch / "catalog" / "modules" / "zz-copy"
    copied.mkdir(parents=True)
    (copied / "module.json").write_text(json.dumps(BASE_MODULES[0]["manifest"]), encoding="utf-8")
    catalog = load_hub(scratch, consumer)
    assert "MODULE-DUPLICATE-ID: alpha" in codes(catalog.refusals)
    assert "MODULE-MANIFEST-INVALID: alpha" in codes(catalog.refusals)
    assert sorted(module.id for module in catalog.modules) == ["alpha", "beta", "gamma"]


def test_asset_without_seed_refused_by_name(consumer: Path, make_hub) -> None:
    scratch = make_hub("noseed", seeds=["alpha.json"])  # beta.yaml seed removed
    catalog = load_hub(scratch, consumer)
    assert codes(catalog.refusals) == ["MODULE-ASSET-NO-SEED: beta"]
    beta = catalog.by_id("beta")
    assert beta is not None and beta.seeds[0].present is False


def test_registry_names_a_module_the_flags_do_not(consumer: Path, make_hub) -> None:
    scratch = make_hub("tsv-only", rows=list(BASE_ROWS) + [["ghost", "ghost", "ghost.json", "probe"]])
    catalog = load_hub(scratch, consumer)
    assert codes(catalog.refusals) == ["MODULE-UNREGISTERED-MANDATORY: ghost"]


def test_flags_name_a_module_the_registry_does_not(consumer: Path, make_hub) -> None:
    modules = list(BASE_MODULES)
    modules[2] = {"dir": "gamma", "manifest": manifest("gamma", mandatory=True, assets=["gamma.json"])}
    scratch = make_hub("flag-only", modules=tuple(modules))
    catalog = load_hub(scratch, consumer)
    assert "MODULE-UNREGISTERED-FLAG: gamma" in codes(catalog.refusals)
    assert "MODULE-ASSET-NO-SEED: gamma" in codes(catalog.refusals)


def test_consumer_assets_drift_refused_by_name(consumer: Path, make_hub) -> None:
    rows = [["alpha", "alpha", "alpha.json,extra.json", "drifted"], BASE_ROWS[1]]
    scratch = make_hub("drift", rows=rows)
    catalog = load_hub(scratch, consumer)
    assert codes(catalog.refusals) == ["MODULE-ASSET-DRIFT: alpha"]


def test_module_name_drift_refused_by_name(consumer: Path, make_hub) -> None:
    rows = [["alpha", "renamed", "alpha.json", "drifted"], BASE_ROWS[1]]
    scratch = make_hub("namedrift", rows=rows)
    catalog = load_hub(scratch, consumer)
    assert codes(catalog.refusals) == ["MODULE-NAME-DRIFT: alpha"]


def test_manifest_id_must_match_its_directory(consumer: Path, make_hub) -> None:
    scratch = make_hub("mismatch", modules=({"dir": "wrongdir", "manifest": manifest("alpha")},))
    catalog = load_hub(scratch, consumer)
    assert "MODULE-MANIFEST-INVALID: alpha" in codes(catalog.refusals)


def test_non_mandatory_module_may_not_declare_assets(consumer: Path, make_hub) -> None:
    modules = list(BASE_MODULES)
    modules[2] = {"dir": "gamma", "manifest": manifest("gamma", mandatory=False, assets=["gamma.json"])}
    scratch = make_hub("nonmandatory", modules=tuple(modules))
    catalog = load_hub(scratch, consumer)
    assert "MODULE-NONMANDATORY-DECLARES-ASSETS: gamma" in codes(catalog.refusals)


def test_unsafe_consumer_asset_path_refused(consumer: Path, make_hub) -> None:
    modules = list(BASE_MODULES)
    modules[0] = {
        "dir": "alpha",
        "manifest": manifest("alpha", mandatory=True, assets=["../escape.json"]),
    }
    scratch = make_hub("unsafe", modules=tuple(modules), rows=[BASE_ROWS[1]])
    catalog = load_hub(scratch, consumer)
    assert "MODULE-ASSET-UNSAFE: alpha" in codes(catalog.refusals)


def test_unparseable_manifest_refused_by_name(consumer: Path, make_hub) -> None:
    scratch = write_hub(make_hub("unparseable"), seeds=BASE_SEEDS)
    (scratch / "catalog" / "modules" / "delta").mkdir(parents=True)
    (scratch / "catalog" / "modules" / "delta" / "module.json").write_text("{not json", encoding="utf-8")
    catalog = load_hub(scratch, consumer)
    assert codes(catalog.refusals) == ["MODULE-MANIFEST-INVALID: delta"]


def test_revision_falls_back_when_the_hub_is_not_a_checkout(tmp_path: Path) -> None:
    scratch = write_hub(tmp_path / "hub")
    repo = write_repo(tmp_path / "repo")
    catalog = load_hub(scratch, repo)
    assert catalog.revision is None
    assert catalog.revision_source == REVISION_UNAVAILABLE
