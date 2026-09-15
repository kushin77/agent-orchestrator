"""The manifest must declare what makes the module a module (issue #646).

The three facts the issue names — `mandatory: true`, a feature flag that
defaults OFF, and `data_source: indexer` — are constrained by the frozen schema,
so these tests assert the live manifest against the same shape the gate applies
and then assert the *values* a reader would look for.
"""

from __future__ import annotations

from pathlib import Path

from integrations.erp.catalog import model, validate

REPO_ROOT = Path(__file__).resolve().parents[4]


def test_the_live_module_validates_cleanly() -> None:
    report = validate.verify(REPO_ROOT)
    assert report.cannot_assess == ()
    assert report.findings == (), [finding.line() for finding in report.findings]
    assert report.exit_code == model.EXIT_OK


def test_the_manifest_declares_the_three_facts() -> None:
    manifest = model.load_manifest(REPO_ROOT / model.MANIFEST_PATH)
    assert manifest["mandatory"] is True
    assert manifest["data_source"] == "indexer"
    flags = {flag["id"]: flag["default"] for flag in manifest["features"]}
    assert "erp-module" in flags, "the module's own flag must be declared"
    assert flags["erp-module"] in (False, "off"), "the module must ship OFF (GR-5)"


def test_the_manifest_validates_against_the_frozen_schema() -> None:
    from governance.modules import schema as subset

    manifest = model.load_manifest(REPO_ROOT / model.MANIFEST_PATH)
    frozen = subset.load(REPO_ROOT / model.MANIFEST_SCHEMA)
    assert subset.problems(manifest, frozen) == ()


def test_every_declared_glob_is_registered_in_the_indexer() -> None:
    manifest = model.load_manifest(REPO_ROOT / model.MANIFEST_PATH)
    registry = (REPO_ROOT / model.SOURCES_PATH).read_text(encoding="utf-8")
    globs = [entry["glob"] for entry in manifest["indexer_sources"]]
    assert globs, "the module must declare the sources that serve it"
    for glob in globs:
        assert glob in registry, "%s is declared but the indexer does not carry it" % glob
    assert any(glob.startswith(manifest["catalogue"]["root"]) for glob in globs), (
        "at least one source must be the catalogue itself, or the module's own facts are unindexed"
    )


def test_every_declared_catalogue_pointer_resolves() -> None:
    manifest = model.load_manifest(REPO_ROOT / model.MANIFEST_PATH)
    catalogue = manifest["catalogue"]
    base = REPO_ROOT / catalogue["root"]
    assert base.is_dir()
    for key in ("module_map", "capabilities"):
        assert (base / catalogue[key]).is_file(), "%s does not resolve" % key
    for key in ("documents", "schema"):
        assert (base / catalogue[key]).is_dir(), "%s does not resolve" % key
    assert (base / catalogue["schema"]).resolve() == (REPO_ROOT / model.SCHEMA_DIR).resolve()


def test_the_readme_names_the_catalogue() -> None:
    manifest = model.load_manifest(REPO_ROOT / model.MANIFEST_PATH)
    readme = (REPO_ROOT / model.README_PATH).read_text(encoding="utf-8")
    assert manifest["catalogue"]["root"] in readme
