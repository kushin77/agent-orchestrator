"""Every refusal, provoked for real (issue #646).

A rule that cannot fail is a formality, so each one is provoked here against a
scratch copy and must refuse **by name** — the code, and the subject the refusal
is about. The provocation for a restated fact reads its fact from the catalogue
(`validate.declared_vocabulary`) rather than hard-coding one, because a check
that carries its own copy of the data it checks is a second store with extra
steps.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from integrations.erp.catalog import model, validate
from conftest import catalogue_dir, documents_dir, read_json, rewrite, write_json

MANIFEST = model.MANIFEST_PATH
README = model.README_PATH
SOURCES = model.SOURCES_PATH


def _first_document(tree: Path) -> Path:
    documents = sorted(documents_dir(tree).glob("*.json"))
    assert documents, "the scratch catalogue declares no document types"
    return documents[0]


def _codes(report: model.Report) -> list[str]:
    return [finding.code for finding in report.findings]


def _named(report: model.Report) -> str:
    return " ".join(
        [finding.line() for finding in report.findings] + list(report.cannot_assess)
    )


def test_a_clean_copy_is_refused_nothing(scratch: Path) -> None:
    """The control for every provocation below: the rule is not always-red."""
    report = validate.verify(scratch)
    assert report.cannot_assess == ()
    assert report.findings == (), [finding.line() for finding in report.findings]
    assert report.exit_code == model.EXIT_OK


def test_a_manifest_that_stops_declaring_its_admission_is_refused(scratch: Path) -> None:
    rewrite(scratch / MANIFEST, "mandatory: true\n", "")
    report = validate.verify(scratch)
    assert report.exit_code == model.EXIT_NOT_OK
    assert model.CODE_MANIFEST_INVALID in _codes(report)
    assert "mandatory" in _named(report)


def test_a_manifest_that_stops_being_mandatory_is_refused(scratch: Path) -> None:
    rewrite(scratch / MANIFEST, "mandatory: true", "mandatory: false")
    report = validate.verify(scratch)
    assert model.CODE_MANIFEST_INVALID in _codes(report)
    assert "mandatory: expected True, found False" in _named(report)


def test_a_flag_that_defaults_on_is_refused(scratch: Path) -> None:
    rewrite(scratch / MANIFEST, "default: off", "default: on")
    report = validate.verify(scratch)
    assert model.CODE_MANIFEST_INVALID in _codes(report)
    assert "features/0/default" in _named(report)


def test_a_data_source_that_is_not_the_indexer_is_refused(scratch: Path) -> None:
    rewrite(scratch / MANIFEST, "data_source: indexer", "data_source: local-files")
    report = validate.verify(scratch)
    assert model.CODE_MANIFEST_INVALID in _codes(report)
    assert "data_source: expected 'indexer', found 'local-files'" in _named(report)


def test_a_catalogue_file_that_loses_its_provenance_is_refused(scratch: Path) -> None:
    path = _first_document(scratch)
    document = read_json(path)
    del document["provenance"]
    write_json(path, document)
    report = validate.verify(scratch)
    assert model.CODE_CATALOGUE_INVALID in _codes(report)
    assert str(path.relative_to(scratch)) in _named(report)
    assert "provenance" in _named(report)


def test_provenance_that_disagrees_with_the_manifest_is_refused(scratch: Path) -> None:
    path = _first_document(scratch)
    document = read_json(path)
    document["provenance"]["license"] = "MIT"
    write_json(path, document)
    report = validate.verify(scratch)
    assert model.CODE_PROVENANCE_MISMATCH in _codes(report)
    assert "license" in _named(report)
    assert str(path.relative_to(scratch)) in _named(report)


def test_a_fact_restated_on_the_declaration_surface_is_refused(scratch: Path) -> None:
    facts = validate.declared_vocabulary(scratch)
    name = next(value for field, value in facts if field == "name")
    readme = scratch / README
    readme.write_text(
        readme.read_text(encoding="utf-8") + "\nThe module declares %s.\n" % name,
        encoding="utf-8",
    )
    report = validate.verify(scratch)
    assert model.CODE_DUPLICATE_FACT in _codes(report)
    assert README.as_posix() in [
        finding.path for finding in report.findings if finding.code == model.CODE_DUPLICATE_FACT
    ]


def test_a_glob_the_indexer_does_not_carry_is_refused(scratch: Path) -> None:
    glob = "integrations/erp/catalog/**/*.json"
    rewrite(scratch / SOURCES, glob, "integrations/erp/catalog/not-registered.json")
    report = validate.verify(scratch)
    assert model.CODE_SOURCE_UNREGISTERED in _codes(report)
    assert glob in _named(report)


def test_a_manifest_registers_no_catalogue_glob_is_refused(scratch: Path) -> None:
    rewrite(scratch / MANIFEST, "  - kind: pattern-template\n    glob: integrations/erp/catalog/**/*.json\n", "")
    report = validate.verify(scratch)
    assert model.CODE_CATALOGUE_UNINDEXED in _codes(report)


def test_a_schema_using_a_keyword_that_cannot_be_enforced_is_refused(scratch: Path) -> None:
    """The schema cannot quietly become a decoration (GR-12)."""
    path = scratch / model.DOCUMENT_SCHEMA
    document = read_json(path)
    document["properties"]["id"]["minLength"] = 1
    write_json(path, document)
    report = validate.verify(scratch)
    assert report.exit_code == model.EXIT_CANNOT_ASSESS
    assert any("minLength" in message for message in report.cannot_assess), report.cannot_assess


def test_a_family_that_is_not_declared_is_refused(scratch: Path) -> None:
    path = _first_document(scratch)
    document = read_json(path)
    document["family"] = "undeclared-family"
    write_json(path, document)
    report = validate.verify(scratch)
    assert model.CODE_CROSS_REFERENCE in _codes(report)
    assert "undeclared-family" in _named(report)


def test_an_owning_issue_that_is_not_a_child_is_refused(scratch: Path) -> None:
    path = _first_document(scratch)
    document = read_json(path)
    document["owning_issue"] = 999999
    write_json(path, document)
    report = validate.verify(scratch)
    assert model.CODE_CROSS_REFERENCE in _codes(report)
    assert "999999" in _named(report)


def test_two_files_declaring_one_id_are_refused(scratch: Path) -> None:
    source = _first_document(scratch)
    ident = read_json(source)["id"]
    shutil.copy2(source, source.with_name("a-second-copy-of-one-fact.json"))
    report = validate.verify(scratch)
    assert model.CODE_CATALOGUE_DUPLICATE in _codes(report)
    assert ident in _named(report)


def test_a_readme_that_stops_naming_the_catalogue_is_refused(scratch: Path) -> None:
    manifest = model.load_manifest(scratch / MANIFEST)
    rewrite(scratch / README, manifest["catalogue"]["root"], "the catalogue directory")
    report = validate.verify(scratch)
    assert model.CODE_CATALOGUE_POINTER in _codes(report)
    assert README.as_posix() in _named(report)


def test_a_docs_index_that_stops_referencing_the_gap_analysis_is_refused(scratch: Path) -> None:
    rewrite(scratch / model.DOCS_INDEX_PATH, model.GAPS_PATH.name, "SOME-OTHER-PLAN.md")
    report = validate.verify(scratch)
    assert model.CODE_GAPS_UNREFERENCED in _codes(report)


def test_a_missing_catalogue_file_is_refused(scratch: Path) -> None:
    (catalogue_dir(scratch) / "capabilities.json").unlink()
    report = validate.verify(scratch)
    assert model.CODE_CATALOGUE_MISSING in _codes(report)
    assert "capabilities" in _named(report)
