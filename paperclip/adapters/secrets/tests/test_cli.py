"""CLI tests for the secret vault primitive (issue #417).

The CLI is the surface the gate drives, so its exit-code contract (0 OK / 1
NOT-OK / 2 CANNOT-ASSESS) is exercised here as well as in the gate itself.
"""

from __future__ import annotations

import copy
import json

import pytest

from paperclip.adapters.secrets import cli, vault
from paperclip.adapters.secrets.model import GSM_STORE


def _run(argv, root):
    return cli.main(["--root", str(root), *argv])


def test_validate_is_green_on_the_committed_tree(repo_root, capsys):
    assert _run(["validate"], repo_root) == 0
    out = capsys.readouterr().out
    assert "OK" in out and "the store of record" in out


def test_view_emits_the_projection(repo_root, capsys):
    assert _run(["view"], repo_root) == 0
    assert json.loads(capsys.readouterr().out) == vault.build_view(repo_root)


def test_catalog_is_value_free(repo_root, capsys):
    assert _run(["catalog"], repo_root) == 0
    document = json.loads(capsys.readouterr().out)
    assert document["store"] == GSM_STORE
    assert not _value_keys(document)
    assert {row["store"] for row in document["secrets"]} == {GSM_STORE}


def _value_keys(node):
    """Every value-bearing key anywhere in a JSON document (recursive)."""
    found = set()
    if isinstance(node, dict):
        found |= set(node) & vault.VALUE_KEYS
        for child in node.values():
            found |= _value_keys(child)
    elif isinstance(node, list):
        for child in node:
            found |= _value_keys(child)
    return found


def test_rotation_reports_every_secret(repo_root, capsys):
    assert _run(["rotation"], repo_root) == 0
    out = capsys.readouterr().out
    for record in vault.build_view(repo_root)["secrets"]:
        assert record["gsm_path"] in out


def test_orphans_is_green_on_the_committed_tree(repo_root, capsys):
    assert _run(["orphans"], repo_root) == 0
    assert "no orphaned secret" in capsys.readouterr().out


def test_orphans_reports_a_consumer_less_secret():
    document = {"store": GSM_STORE, "secrets": [
        {"gsm_path": "projects/example/secrets/orphan", "agent": "a", "scope": "agent:a",
         "consumer": None},
    ]}
    assert vault.orphans(document) == ["projects/example/secrets/orphan"]
    assert "no consumer" in vault.orphan_findings(document)[0]


def test_validate_refuses_a_mutated_view_file(repo_root, tmp_path, capsys):
    mutant = copy.deepcopy(vault.build_view(repo_root))
    mutant["secrets"][0]["value"] = "fake-placeholder-never-a-credential"
    path = tmp_path / "mutant.json"
    path.write_text(json.dumps(mutant), encoding="utf-8")
    assert _run(["validate", "--view", str(path)], repo_root) == 1
    assert "forbidden key 'value'" in capsys.readouterr().err


def test_validate_cannot_assess_without_a_schema(tmp_path, capsys):
    assert _run(["validate"], tmp_path) == 2
    assert "CANNOT-ASSESS" in capsys.readouterr().err


def test_view_refuses_a_value_bearing_catalog(tmp_path, capsys):
    catalog = tmp_path / vault.CATALOG_REL
    catalog.parent.mkdir(parents=True, exist_ok=True)
    catalog.write_text(json.dumps({"store": GSM_STORE, "secrets": [
        {"gsm_path": "projects/example/secrets/a", "agent": "a", "scope": "agent:a",
         "value": "fake-placeholder-never-a-credential"},
    ]}), encoding="utf-8")
    assert _run(["view"], tmp_path) == 1
    err = capsys.readouterr().err
    assert "forbidden key 'value'" in err
    assert "fake-placeholder-never-a-credential" not in err


def test_read_is_refused_for_an_unscoped_caller(repo_root, capsys):
    record = vault.build_view(repo_root)["secrets"][0]
    assert _run(["read", "--path", record["gsm_path"], "--principal", "s"], repo_root) == 1
    assert "lacks scope" in capsys.readouterr().err


def test_read_succeeds_for_a_scoped_caller_and_returns_a_reference(repo_root, capsys):
    record = vault.build_view(repo_root)["secrets"][0]
    rc = _run(["read", "--path", record["gsm_path"], "--principal", "s",
               "--scope", record["scope"]], repo_root)
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["gsm_path"] == record["gsm_path"]
    assert "value" not in out


def test_read_refuses_an_unknown_path(repo_root, capsys):
    rc = _run(["read", "--path", "projects/example/secrets/nope",
               "--principal", "s", "--scope", "secret:*"], repo_root)
    assert rc == 1
    assert "no secret named" in capsys.readouterr().err


def test_parser_requires_a_subcommand():
    with pytest.raises(SystemExit):
        cli.main([])
