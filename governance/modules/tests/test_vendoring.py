"""No vendoring — the registry stores references, and every copy is refused."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import importlib.util as _importlib_util  # noqa: E402
from pathlib import Path as _ConftestPath  # noqa: E402

# A bare ``from conftest import ...`` is not safe here: when this suite is
# collected alongside other governance suites, every one of their
# ``tests/conftest.py`` files lands under the same bare module identity
# ``conftest`` in ``sys.modules``, so whichever conftest is imported LAST
# silently wins the name for the rest of collection (issues #699, #702, #1042).
# Loading this file's own conftest by absolute path guarantees this module
# always gets ITS directory's conftest regardless of collection order.
_conftest_spec = _importlib_util.spec_from_file_location(
    "governance_modules_tests_conftest", _ConftestPath(__file__).with_name("conftest.py")
)
_conftest = _importlib_util.module_from_spec(_conftest_spec)
_conftest_spec.loader.exec_module(_conftest)
codes = _conftest.codes
write_repo = _conftest.write_repo

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


def test_the_declared_vendor_submodules_are_not_refused(
    hub: Path, consumer: Path
) -> None:
    """The declared set is the authority: the paths it names are references.

    ``vendor/AgenticAutomationFramework`` was declared a vendored submodule
    (#2012) after this rule was written, so the rule must read the declaration
    rather than restate "the hub is the only one" — otherwise a deliberate
    doctrine move reds the suite in the hub-present venue alone.
    """
    assert "vendor/AgenticAutomationFramework" in vendoring.VENDOR_SUBMODULES
    (consumer / ".gitmodules").write_text(
        '[submodule "vendor/CMR"]\n\tpath = vendor/CMR\n'
        '[submodule "vendor/AgenticAutomationFramework"]\n'
        "\tpath = vendor/AgenticAutomationFramework\n",
        encoding="utf-8",
    )
    assert vendoring.scan(consumer, _catalog(hub, consumer)) == []


def test_an_undeclared_submodule_is_refused_while_declared_ones_are_not(
    hub: Path, consumer: Path
) -> None:
    """The negative control: the set accepts its entries and refuses the rest."""
    (consumer / ".gitmodules").write_text(
        '[submodule "vendor/CMR"]\n\tpath = vendor/CMR\n'
        '[submodule "vendor/AgenticAutomationFramework"]\n'
        "\tpath = vendor/AgenticAutomationFramework\n"
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
    write_hub = _conftest.write_hub

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
    write_targets = _conftest.write_targets
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
    write_hub = _conftest.write_hub

    write_hub(scratch)
    assert vendoring.scan(consumer, _catalog(scratch, consumer)) == []
