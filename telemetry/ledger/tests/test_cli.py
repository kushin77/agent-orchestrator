"""CLI tests for telemetry/ledger (issue #31).

Exercises the actual CLI entry point (``ledger.cli.main``) end to end with a
keystore file, asserting tri-state exit codes (0/1/2) and on-disk behavior.
"""

from __future__ import annotations

import json
import os

from conftest import make_key
from ledger.cli import main


def _write_keystore(tmp_path) -> str:
    path = str(tmp_path / "keystore.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(
            {"acme": {"key": make_key("acme").hex(), "id": "cli:acme:v1"}},
            handle,
        )
    return path


def _records_on_disk(ledger_dir, tenant="acme"):
    path = os.path.join(ledger_dir, tenant + ".jsonl")
    with open(path, "r", encoding="utf-8") as handle:
        return [
            json.loads(line)
            for line in handle.read().splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]


def test_cli_append_verify_state_export_roundtrip(tmp_path):
    ledger_dir = str(tmp_path / "audit")
    ks = _write_keystore(tmp_path)
    rc = main([ledger_dir, "--keystore", ks, "append", "acme",
               "--actor", "agent:worker-1", "--action", "model.call",
               "--resource", "gateway/proxy", "--payload-json", '{"prompt": "hi"}'])
    assert rc == 0
    assert main([ledger_dir, "--keystore", ks, "verify", "acme"]) == 0
    assert main([ledger_dir, "--keystore", ks, "append", "acme",
                 "--actor", "user:alice", "--action", "policy.decision"]) == 0
    assert main([ledger_dir, "--keystore", ks, "verify", "acme"]) == 0
    # state prints the trusted tail; the command succeeds (no raise).
    assert main([ledger_dir, "--keystore", ks, "state", "acme"]) == 0
    assert [r["seq"] for r in _records_on_disk(ledger_dir)] == [1, 2]


def test_cli_verify_detects_tamper_exit_1(tmp_path):
    ledger_dir = str(tmp_path / "audit")
    ks = _write_keystore(tmp_path)
    assert main([ledger_dir, "--keystore", ks, "append", "acme",
                 "--actor", "user:alice", "--action", "a1"]) == 0
    assert main([ledger_dir, "--keystore", ks, "append", "acme",
                 "--actor", "user:alice", "--action", "a2"]) == 0
    assert main([ledger_dir, "--keystore", ks, "verify", "acme"]) == 0

    # Tamper the file directly, then verify must exit 1 (NOT-OK).
    path = os.path.join(ledger_dir, "acme.jsonl")
    lines = open(path, encoding="utf-8").read().splitlines()
    out = []
    for line in lines:
        if line.strip().startswith("#"):
            out.append(line)
        else:
            record = json.loads(line)
            if record["seq"] == 1:
                record["action"] = "tampered"
            out.append(json.dumps(record, sort_keys=True,
                                  separators=(",", ":")))
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(out) + "\n")
    assert main([ledger_dir, "--keystore", ks, "verify", "acme"]) == 1


def test_cli_read_payload_exit_codes(tmp_path):
    ledger_dir = str(tmp_path / "audit")
    ks = _write_keystore(tmp_path)
    assert main([ledger_dir, "--keystore", ks, "append", "acme",
                 "--actor", "user:alice", "--action", "a",
                 "--payload-json", '{"secret": "value"}']) == 0
    # With the key: decrypt OK (exit 0).
    assert main([ledger_dir, "--keystore", ks, "read", "acme", "1"]) == 0
    # Without any keystore: CANNOT-ASSESS (exit 2), never a silent pass.
    assert main([ledger_dir, "read", "acme", "1"]) == 2
    # Out-of-range seq: NOT-OK (exit 1).
    assert main([ledger_dir, "--keystore", ks, "read", "acme", "99"]) == 1


def test_cli_append_payload_fails_closed_without_key(tmp_path):
    ledger_dir = str(tmp_path / "audit")
    # No keystore and a sensitive payload -> append refused (exit 1, no file).
    rc = main([ledger_dir, "append", "acme", "--actor", "user:alice",
               "--action", "a", "--payload-json", '{"s": 1}'])
    assert rc == 1
    assert not os.path.exists(os.path.join(ledger_dir, "acme.jsonl"))
    # A payload-free append succeeds without any key.
    assert main([ledger_dir, "append", "acme", "--actor", "user:alice",
                 "--action", "no-payload"]) == 0


def test_cli_verify_all_and_expected_tail(tmp_path):
    ledger_dir = str(tmp_path / "audit")
    ks = _write_keystore(tmp_path)
    main([ledger_dir, "--keystore", ks, "append", "acme",
          "--actor", "user:alice", "--action", "a1"])
    main([ledger_dir, "--keystore", ks, "append", "acme",
          "--actor", "user:alice", "--action", "a2"])
    assert main([ledger_dir, "--keystore", ks, "verify", "--all"]) == 0
    # Wrong expected tail -> NOT-OK (exit 1).
    assert main([ledger_dir, "--keystore", ks, "verify", "acme",
                 "--expected-seq", "2", "--expected-hash", "0" * 64]) == 1


def test_cli_rechain_requires_ack(tmp_path):
    ledger_dir = str(tmp_path / "audit")
    ks = _write_keystore(tmp_path)
    main([ledger_dir, "--keystore", ks, "append", "acme",
          "--actor", "user:alice", "--action", "a"])
    # Without --ack the destructive repair is refused (exit 1).
    assert main([ledger_dir, "--keystore", ks, "rechain", "acme"]) == 1
    assert main([ledger_dir, "--keystore", ks, "rechain", "acme", "--ack"]) == 0
