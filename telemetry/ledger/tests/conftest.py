"""Pytest bootstrap for telemetry/ledger (issue #31).

``telemetry/`` has no ``__init__.py`` (it is a per-issue package directory,
mirroring ``registry/``), so this inserts ``telemetry/`` - three levels above
this file - at the front of ``sys.path``. Every test can then
``from ledger import ...`` no matter where pytest is invoked from.

Also provides shared fixtures and tamper helpers used by the negative tests.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from typing import Any, Callable, Dict, List

import pytest

_here = os.path.dirname(os.path.abspath(__file__))
# telemetry/ledger/tests -> telemetry/ledger -> telemetry
_telemetry_root = os.path.dirname(os.path.dirname(_here))
if _telemetry_root not in sys.path:
    sys.path.append(_telemetry_root)

from ledger import DictKeystore, KeyMaterial  # noqa: E402  (after sys.path)


def make_key(seed: str) -> bytes:
    """Deterministic 32-byte AES-256 test key derived from ``seed``.

    Keys are generated at runtime (never literal in source), so the repo-wide
    mechanical secret scan cannot false-positive on a test key.
    """
    return hashlib.sha256(("telemetry-ledger-test:" + seed).encode("utf-8")).digest()


def make_keystore(*tenants: str) -> DictKeystore:
    """A DictKeystore with one derived key per tenant."""
    return DictKeystore(
        {
            tenant: KeyMaterial(key=make_key(tenant), key_id=f"test:{tenant}:v1")
            for tenant in tenants
        }
    )


def rewrite_ledger_file(
    path: str,
    mutate: Callable[[int, Dict[str, Any]], Dict[str, Any]],
) -> None:
    """Rewrite a JSON Lines ledger file, applying ``mutate(seq, record)`` to
    every non-comment line. Header (``#``) lines are preserved untouched. This
    is how the negative tests simulate an external tamper on disk.
    """
    with open(path, "r", encoding="utf-8") as handle:
        lines = handle.read().splitlines()
    out: List[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            out.append(line)
            continue
        record = json.loads(stripped)
        mutated = mutate(int(record["seq"]), dict(record))
        out.append(
            json.dumps(mutated, sort_keys=True, separators=(",", ":"))
        )
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(out) + "\n")


@pytest.fixture
def keystore() -> DictKeystore:
    """A keystore holding keys for tenants ``acme`` and ``other``."""
    return make_keystore("acme", "other")


@pytest.fixture
def file_store(tmp_path, keystore):
    """A file-backed LedgerStore in a temp dir with an acme/other keystore."""
    from ledger import open_ledger

    return open_ledger(str(tmp_path / "audit"), keystore=keystore)


@pytest.fixture
def mem_store(keystore):
    """An in-memory LedgerStore (directory=None) with the shared keystore."""
    from ledger import open_ledger

    return open_ledger(None, keystore=keystore)
