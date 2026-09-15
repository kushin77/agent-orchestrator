"""The catalogue must be valid, complete and cross-referenced (issue #646).

Every assertion here is a property the gate also measures, stated once against
the live tree so the suite fails with a message a reader can act on before the
gate does.
"""

from __future__ import annotations

from pathlib import Path

from integrations.erp.catalog import model, validate
from conftest import catalogue_dir, documents_dir, read_json

REPO_ROOT = Path(__file__).resolve().parents[4]


def _manifest():
    return model.load_manifest(REPO_ROOT / model.MANIFEST_PATH)


def test_every_catalogue_file_is_declared_and_carries_the_manifest_provenance() -> None:
    manifest = _manifest()
    declared = manifest["provenance"]
    files = [catalogue_dir(REPO_ROOT) / "module-map.json", catalogue_dir(REPO_ROOT) / "capabilities.json"]
    files.extend(sorted(documents_dir(REPO_ROOT).glob("*.json")))
    assert len(files) >= 3
    for path in files:
        document = read_json(path)
        assert document["provenance"] == declared, "%s disagrees with the manifest" % path.name


def test_every_document_declares_an_upstream_pattern_pointer() -> None:
    for path in sorted(documents_dir(REPO_ROOT).glob("*.json")):
        document = read_json(path)
        upstream = document["upstream"]
        assert upstream["repo"] == "frappe/erpnext"
        assert upstream["license"] == "GPL-3.0"
        assert upstream["pattern_source_only"] is True
        assert upstream["doctype"], "%s declares no upstream entity" % path.name


def test_every_reference_resolves() -> None:
    capabilities = read_json(catalogue_dir(REPO_ROOT) / "capabilities.json")
    module_map = read_json(catalogue_dir(REPO_ROOT) / "module-map.json")
    families = {entry["id"] for entry in capabilities["capabilities"]}
    pillars = {entry["id"] for entry in module_map["pillars"]}
    children = {entry["issue"] for entry in module_map["children"]}

    for entry in capabilities["capabilities"]:
        assert entry["pillar"] in pillars, entry["id"]
        assert set(entry["child_issues"]) <= children, entry["id"]
    for path in sorted(documents_dir(REPO_ROOT).glob("*.json")):
        document = read_json(path)
        assert document["family"] in families, path.name
        assert document["owning_issue"] in children, path.name


def test_the_pillars_the_map_names_are_real() -> None:
    module_map = read_json(catalogue_dir(REPO_ROOT) / "module-map.json")
    for entry in module_map["pillars"]:
        assert (REPO_ROOT / entry["path"] / "README.md").is_file(), entry["id"]


def test_document_ids_and_names_are_unique() -> None:
    seen: dict[str, str] = {}
    for path in sorted(documents_dir(REPO_ROOT).glob("*.json")):
        document = read_json(path)
        for field in ("id", "name"):
            value = document[field]
            assert value not in seen, "%s restates %s from %s" % (path.name, value, seen.get(value))
            seen[value] = path.name


def test_the_catalogue_declares_a_vocabulary() -> None:
    facts = validate.declared_vocabulary(REPO_ROOT)
    ids = [value for field, value in facts if field == "id"]
    names = [value for field, value in facts if field == "name"]
    assert len(ids) == len(names) >= 10
    assert all(ids) and all(names)


def test_a_document_type_is_declared_exactly_once_across_the_catalogue() -> None:
    """One store: a document type is declared by the file that carries its id."""
    for path in sorted(documents_dir(REPO_ROOT).glob("*.json")):
        document = read_json(path)
        assert path.stem == document["id"], "%s does not declare its own id" % path.name
