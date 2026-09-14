"""No vendoring — the registry stores references, and every copy is refused."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from conftest import codes, write_repo

from governance.modules import hub, vendoring


def _catalog(hub_root: Path, consumer: Path):
    return hub.load(hub_root, consumer, recorded_root="hub")


def test_a_clean_tree_refuses_nothing(hub: Path, consumer: Path) -> None:
    assert vendoring.scan(consumer, _catalog(hub, consumer)) == []


def test_a_copied_module_manifest_is_in_tree_source(hub: Path, consumer: Path) -> None:
    copied = consumer / "alpha"
    copied.mkdir()
    shutil.copy(hub / "catalog" / "modules" / "alpha" / "module.json", copied / "module.json")
    findings = vendoring.scan(consumer, _catalog(hub, consumer))
    assert codes(findings) == ["VENDOR-SOURCE-IN-TREE: alpha"]
    assert findings[0].source == "alpha/module.json"


def test_the_repos_own_root_manifest_is_not_a_module_copy(hub: Path, consumer: Path) -> None:
    """The consumer's own ``module.json`` is this repo's identity, not a module."""
    (consumer / "module.json").write_text(
        json.dumps({"schema": "cmr.module/v1", "id": "probe-repo"}), encoding="utf-8"
    )
    assert vendoring.scan(consumer, _catalog(hub, consumer)) == []


def test_an_extra_submodule_path_is_refused(hub: Path, consumer: Path) -> None:
    (consumer / ".gitmodules").write_text(
        '[submodule "vendor/CMR"]\n\tpath = vendor/CMR\n'
        '[submodule "third-party"]\n\tpath = third_party/widget\n',
        encoding="utf-8",
    )
    assert codes(vendoring.scan(consumer, _catalog(hub, consumer))) == [
        "VENDOR-EXTRA-SUBMODULE: third_party/widget"
    ]


def test_a_submodule_that_vendors_a_module_directly_is_refused(hub: Path, consumer: Path) -> None:
    (consumer / ".gitmodules").write_text(
        '[submodule "vendor/CMR"]\n\tpath = vendor/CMR\n'
        '[submodule "alpha"]\n\tpath = alpha\n',
        encoding="utf-8",
    )
    findings = vendoring.scan(consumer, _catalog(hub, consumer))
    assert codes(findings) == ["VENDOR-SOURCE-IN-TREE: alpha"]
    assert findings[0].source == ".gitmodules"


def test_an_in_tree_distribution_package_is_refused(hub: Path, consumer: Path) -> None:
    package = consumer / "gammapkg"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    findings = vendoring.scan(consumer, _catalog(hub, consumer))
    assert codes(findings) == ["VENDOR-IN-TREE-PACKAGE: gammapkg"]
    assert findings[0].source == "gammapkg"


def test_a_scoped_node_package_is_refused(tmp_path: Path) -> None:
    from conftest import write_hub

    consumer = write_repo(tmp_path / "repo")
    package = consumer / "node_modules" / "@kushin77" / "scoped"
    package.mkdir(parents=True)
    modules = (
        {
            "dir": "scoped",
            "manifest": {
                "schema": "cmr.module/v1",
                "id": "scoped",
                "name": "scoped",
                "source": {"repo": "kushin77/scoped"},
                "distribution": {"package": "@kushin77/scoped", "language": "typescript"},
            },
        },
    )
    scratch = write_hub(tmp_path / "hub", modules=modules, rows=[], seeds=[])
    findings = vendoring.scan(consumer, _catalog(scratch, consumer))
    assert codes(findings) == ["VENDOR-IN-TREE-PACKAGE: @kushin77/scoped"]


def test_a_reference_outside_the_hub_is_refused(hub: Path, consumer: Path) -> None:
    entries = [
        {"id": "alpha", "reference": {"kind": "hub-catalog-entry", "path": "catalog/modules/alpha/module.json"}},
        {"id": "copy-of-beta", "reference": {"kind": "hub-catalog-entry", "path": "../../beta/module.json"}},
    ]
    findings = vendoring.check_references(hub, entries)
    assert codes(findings) == ["VENDOR-PATH-OUTSIDE-HUB: copy-of-beta"]


def test_the_real_registry_references_stay_inside_the_hub(hub: Path, consumer: Path, tmp_path) -> None:
    from conftest import write_targets
    from governance.modules import registry

    targets = write_targets(tmp_path / "t.json", {"schema": "ao.module-targets/v1", "targets": [], "watch": []})
    doc = registry.build(consumer, hub, targets)
    assert vendoring.check_references(hub, doc["modules"]) == []


def test_the_register_scan_skips_hidden_and_vendor_trees(tmp_path: Path) -> None:
    consumer = write_repo(tmp_path / "repo")
    for relative in (".git/objects/x", "vendor/CMR/catalog/modules/alpha/module.json", ".research/clone/module.json"):
        path = consumer / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"schema": "cmr.module/v1", "id": "alpha"}), encoding="utf-8"
        )
    scratch = tmp_path / "hub"
    from conftest import write_hub

    write_hub(scratch)
    assert vendoring.scan(consumer, _catalog(scratch, consumer)) == []
