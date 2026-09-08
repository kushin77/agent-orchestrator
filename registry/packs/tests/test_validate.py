"""validate.py tests: release coverage + ledger + parity + attestation +
self-test, and the immutable-ledger tamper negatives (issue #40).
"""
import hashlib
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import packhelpers  # noqa: E402
from packs import validate  # noqa: E402


def _reload(packhelpers_mod=None):
    return validate


def test_release_file_is_valid():
    v = _reload()
    schema = packhelpers.load_schema()
    catalog = packhelpers.load_catalog()
    public_key = packhelpers.load_public_key()
    path = os.path.join(packhelpers.RELEASES_DIR, "worker-platform.1.0.0.yaml")
    errors = v.validate_release_file(path, schema, catalog, public_key)
    assert errors == [], errors


def test_release_attestation_detects_tamper():
    v = _reload()
    schema = packhelpers.load_schema()
    catalog = packhelpers.load_catalog()
    public_key = packhelpers.load_public_key()
    data = v.load_yaml(os.path.join(packhelpers.RELEASES_DIR,
                                    "worker-platform.1.0.0.yaml"))
    tampered = dict(data)
    tampered["version"] = "9.9.9"
    errors = v.attestation_errors(tampered, public_key, "tampered")
    assert errors, "tampered release attestation must fail"


def test_release_attestation_fails_closed_without_crypto(monkeypatch):
    v = _reload()
    schema = packhelpers.load_schema()
    catalog = packhelpers.load_catalog()
    public_key = packhelpers.load_public_key()
    data = v.load_yaml(os.path.join(packhelpers.RELEASES_DIR,
                                    "worker-platform.1.0.0.yaml"))
    monkeypatch.setattr(v.attestation, "HAS_CRYPTO", False)
    errors = v.attestation_errors(data, public_key, "release")
    assert any("cryptography unavailable" in e for e in errors)


def test_release_attestation_fails_without_key():
    v = _reload()
    schema = packhelpers.load_schema()
    catalog = packhelpers.load_catalog()
    data = v.load_yaml(os.path.join(packhelpers.RELEASES_DIR,
                                    "worker-platform.1.0.0.yaml"))
    errors = v.attestation_errors(data, None, "release")
    assert any("public key is missing" in e for e in errors)


def test_coverage_and_self_test_real_tree_ok():
    v = _reload()
    ok, errors = v.coverage_errors(packhelpers.RELEASES_DIR,
                                   packhelpers.MANIFEST_PATH,
                                   packhelpers.SCHEMA_PATH,
                                   packhelpers.CATALOG_PATH,
                                   packhelpers.PUBLIC_KEY_PATH)
    assert ok, errors
    st_errors = v.self_test_errors(packhelpers.FIXTURES_DIR,
                                   packhelpers.SCHEMA_PATH,
                                   packhelpers.CATALOG_PATH)
    assert st_errors == [], st_errors


def test_parity_and_catalog_self_ok():
    v = _reload()
    schema = packhelpers.load_schema()
    catalog = packhelpers.load_catalog()
    assert v.catalog_self_errors(catalog) == []
    assert v.parity_errors(schema, catalog) == []


def test_ledger_detects_published_mutation():
    v = _reload()
    # copy the packs tree to a temp dir, then mutate a published release file
    tmp = tempfile.mkdtemp(prefix="ao40-ledger-")
    dst_packs = os.path.join(tmp, "packs")
    shutil.copytree(packhelpers.PACKS_DIR, dst_packs,
                    ignore=shutil.ignore_patterns("__pycache__"))
    rel = os.path.join(dst_packs, "releases", "worker-platform.1.0.0.yaml")
    with open(rel, "a", encoding="utf-8") as fh:
        fh.write("# tampered\n")
    errors = v.ledger_errors(os.path.join(dst_packs, "releases"),
                             os.path.join(dst_packs, "versions",
                                          "manifest.yaml"))
    assert any("IMMUTABLE" in e for e in errors), errors


def test_ledger_detects_missing_release():
    v = _reload()
    tmp = tempfile.mkdtemp(prefix="ao40-ledger-")
    dst_packs = os.path.join(tmp, "packs")
    shutil.copytree(packhelpers.PACKS_DIR, dst_packs,
                    ignore=shutil.ignore_patterns("__pycache__"))
    os.remove(os.path.join(dst_packs, "releases", "data-ops.1.0.0.yaml"))
    errors = v.ledger_errors(os.path.join(dst_packs, "releases"),
                             os.path.join(dst_packs, "versions",
                                          "manifest.yaml"))
    assert any("missing" in e for e in errors), errors


def test_every_fixture_is_rejected():
    v = _reload()
    schema = packhelpers.load_schema()
    catalog = packhelpers.load_catalog()
    rejected = 0
    for name in sorted(os.listdir(packhelpers.FIXTURES_DIR)):
        if not name.endswith((".yaml", ".yml", ".json")):
            continue
        path = os.path.join(packhelpers.FIXTURES_DIR, name)
        data = v.load_yaml(path)
        errors = v.validate_pack_data(data, schema, catalog,
                                      "fixture/%s" % name)
        assert errors, "fixture %s was ACCEPTED (must fail)" % name
        rejected += 1
    assert rejected >= 8


def test_valid_doc_has_no_errors():
    v = _reload()
    schema = packhelpers.load_schema()
    catalog = packhelpers.load_catalog()
    doc = packhelpers.pack_doc()
    assert v.validate_pack_data(doc, schema, catalog, "doc") == []
