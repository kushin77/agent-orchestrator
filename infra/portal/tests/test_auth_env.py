"""Tests for `infra/portal/auth_env.py` (#881, lane L2 of EPIC #878).

`scripts/check-portal-auth-env.sh` already drives this module end to end with
shell-level provocations against the real tree (see that script for the full
list). This suite adds the pytest-visible piece `pytest infra/portal -q`
picks up on its own: a positive check against the committed
declaration/module pair, and a DRIFT negative control — the module's
Terraform projection is mutated to name an env the declaration does not
carry, `check_pair` must refuse it by the `env-name-restated` finding class,
and the unmodified twin (the real committed pair) must be accepted by the
same call. That is the "python manifest vs terraform variable list" drift
test the lane's acceptance names, expressed as code rather than only shell.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


def _load_auth_env():
    """Load `infra/portal/auth_env.py` by path.

    Not a package (no `__init__.py`, and its name collides with nothing else
    importable), so it is loaded directly rather than relying on sys.path
    order, which pytest's own import machinery does not leave stable.
    """
    spec = importlib.util.spec_from_file_location("portal_auth_env", ROOT / "infra" / "portal" / "auth_env.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


auth_env = _load_auth_env()

DECLARATION = ROOT / auth_env.DECLARATION_REL
MODULE = ROOT / auth_env.MODULE_REL


def test_declaration_and_module_exist():
    assert DECLARATION.is_file(), DECLARATION
    assert MODULE.is_file(), MODULE


def test_committed_pair_is_accepted():
    """The manifest, as committed, matches the console and its projection."""
    findings = auth_env.check_pair(ROOT, DECLARATION, MODULE)
    assert findings == [], findings


def test_committed_tree_has_no_findings():
    findings, evidence = auth_env.check_tree(ROOT)
    assert findings == [], findings
    assert evidence, "check_tree must report evidence for the clean case"


def test_drift_env_name_restated_in_module_is_refused(tmp_path):
    """DRIFT negative control: the module stops projecting the declaration
    and instead restates an env name literally. `check_pair` must refuse
    this by name (`env-name-restated`), proving the drift test can fail."""
    module_text = MODULE.read_text(encoding="utf-8")
    needle = "secret.value.env"
    assert needle in module_text, "this control is stale: the module no longer projects secret.value.env"

    mutated = module_text.replace(needle, '"PORTAL_AUTH_GATE_JWKS_FILE"', 1)
    mutated_module = tmp_path / "main.tf"
    mutated_module.write_text(mutated, encoding="utf-8")

    findings = auth_env.check_pair(ROOT, DECLARATION, mutated_module)
    assert any(f.startswith("env-name-restated") for f in findings), findings


def test_drift_undeclared_console_env_is_refused(tmp_path):
    """DRIFT negative control: the declaration drops a secret the console
    still reads. `check_pair` must refuse it as `undeclared-console-env`."""
    import json

    doc = json.loads(DECLARATION.read_text(encoding="utf-8"))
    assert doc["secrets"], "the declaration has no secrets to remove"
    doc["secrets"].pop()

    mutated_declaration = tmp_path / "auth-env.json"
    mutated_declaration.write_text(json.dumps(doc), encoding="utf-8")

    findings = auth_env.check_pair(ROOT, mutated_declaration, MODULE)
    assert any(f.startswith("undeclared-console-env") for f in findings), findings


def test_scan_file_refuses_a_literal_value_for_a_declared_env(tmp_path):
    # Built from parts so this fixture is not itself a hit when `check_tree`
    # scans the committed tree (it would otherwise flag its own source line).
    env_name = "ROOT_ADMIN" + "_EMAILS"
    fixture = tmp_path / "allowlist.env"
    fixture.write_text(f"{env_name}=root@purebliss.app\n", encoding="utf-8")
    findings, scanned = auth_env.scan_one(ROOT, str(fixture))
    assert scanned == 1
    assert any(f.startswith("literal-value-for-declared-env") for f in findings), findings


def test_scan_file_accepts_indirection(tmp_path):
    fixture = tmp_path / "indirect.env"
    fixture.write_text('PORTAL_AUTH_GATE_JWKS="${PORTAL_AUTH_GATE_JWKS:-}"\n', encoding="utf-8")
    findings, _scanned = auth_env.scan_one(ROOT, str(fixture))
    assert findings == [], findings


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
