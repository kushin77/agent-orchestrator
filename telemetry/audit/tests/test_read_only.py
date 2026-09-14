"""Structural read-only tests for the audit read model (issue #347).

Read-only is a *structural* property here, not a promise: the exposed name set
is closed, no name carries a mutating verb, and the module reaches the ledger
only through its read API (proved by parsing the source, not by grepping text).
"""

from __future__ import annotations

import ast
import os

import pytest

import read_model

#: Names that would betray a write path.
FORBIDDEN_VERBS = (
    "append",
    "clear",
    "commit",
    "create",
    "delete",
    "drop",
    "insert",
    "mutate",
    "patch",
    "purge",
    "put",
    "rechain",
    "remove",
    "reset",
    "save",
    "update",
    "write",
)

#: The only ledger methods the read model may call.
ALLOWED_STORE_METHODS = {"records", "tenant_ids", "tail_state", "verify"}

#: The complete public surface of a read model.
EXPECTED_PUBLIC_API = {
    "filter",
    "records",
    "stats",
    "store",
    "tenants",
    "trusted_tail",
    "verify_chain",
}


def _module_source() -> str:
    return open(read_model.__file__, "r", encoding="utf-8").read()


def _store_methods_called(source: str) -> set:
    """Attribute calls made on a store/ledger receiver in ``source``."""
    called = set()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        base = node.func.value
        if isinstance(base, ast.Attribute) and base.attr in ("_store", "store"):
            called.add(node.func.attr)
        elif isinstance(base, ast.Name) and base.id in ("_store", "store"):
            called.add(node.func.attr)
    return called


def test_public_api_is_closed(model):
    public = {name for name in dir(model) if not name.startswith("_")}
    assert public == EXPECTED_PUBLIC_API


def test_no_exposed_name_carries_a_mutating_verb(model):
    offenders = sorted(
        name
        for name in dir(model)
        if any(verb in name.lower() for verb in FORBIDDEN_VERBS)
    )
    assert offenders == []


def test_no_module_level_name_carries_a_mutating_verb():
    offenders = sorted(
        name
        for name in vars(read_model)
        if not name.startswith("_")
        and any(verb in name.lower() for verb in FORBIDDEN_VERBS)
    )
    assert offenders == []


def test_module_reaches_the_ledger_only_through_its_read_api():
    called = _store_methods_called(_module_source())
    assert called <= ALLOWED_STORE_METHODS, sorted(called - ALLOWED_STORE_METHODS)
    assert called == {"records", "tenant_ids", "tail_state", "verify"}


def test_cli_reaches_the_ledger_only_through_its_read_api():
    cli_path = os.path.join(os.path.dirname(read_model.__file__), "cli.py")
    with open(cli_path, "r", encoding="utf-8") as handle:
        called = _store_methods_called(handle.read())
    assert called <= ALLOWED_STORE_METHODS, sorted(called - ALLOWED_STORE_METHODS)


def test_instance_has_no_write_method(model):
    for verb in ("append", "update", "delete", "write", "rechain", "create"):
        with pytest.raises(AttributeError):
            getattr(model, verb)


def test_read_model_never_decrypts_a_payload():
    # The read model only projects stored fields; decryption stays in the ledger.
    assert "decrypt" not in _module_source().lower()
