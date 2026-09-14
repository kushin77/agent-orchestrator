"""Gateway provider-catalog parity suite (issue #349).

Every registered provider adapter must have exactly one gateway-owned catalog
module, and every catalog module must name a registered provider. The tests
assert both directions directly and then exercise the fail-closed parity gate
(``scripts/check-gateway-catalog-parity.sh``) on the committed catalog and on two
mutated copies — a removed module and an orphan module — so a gate that cannot
fail would be caught here too (no-false-green, GR-12).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from providers.registry import PROVIDER_NAMES

REPO_ROOT = Path(__file__).resolve().parents[3]
MODULES_DIR = REPO_ROOT / "gateway" / "catalog" / "modules"
GATE = REPO_ROOT / "scripts" / "check-gateway-catalog-parity.sh"
PREFIX = "gateway.providers."


def registered_providers() -> set[str]:
    return set(PROVIDER_NAMES)


def module_providers() -> dict[str, str | None]:
    """Map every catalog module directory to the provider id it declares."""
    declared: dict[str, str | None] = {}
    for entry in sorted(p for p in MODULES_DIR.iterdir() if p.is_dir()):
        doc = json.loads((entry / "module.json").read_text(encoding="utf-8"))
        package = (doc.get("distribution") or {}).get("package")
        provider = None
        if isinstance(package, str) and package.startswith(PREFIX):
            provider = package[len(PREFIX):]
        elif doc.get("id"):
            provider = doc["id"]
        declared[entry.name] = provider
    return declared


def run_gate(modules_dir: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(GATE), "--modules-dir", str(modules_dir)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )


def test_registry_reports_the_shipped_providers() -> None:
    providers = registered_providers()
    # Guard against a vacuous pass: the seven shipped adapters must be present.
    assert {
        "anthropic",
        "deepseek",
        "openai",
        "gemini",
        "ollama",
        "hermes",
        "paperclip",
    } <= providers


def test_every_registered_provider_has_a_catalog_module() -> None:
    covered = {p for p in module_providers().values() if p}
    missing = sorted(registered_providers() - covered)
    assert not missing, f"registered provider(s) with no catalog module: {missing}"


def test_every_catalog_module_names_a_registered_provider() -> None:
    providers = registered_providers()
    offenders = {
        name: declared
        for name, declared in module_providers().items()
        if declared is None or declared not in providers
    }
    assert not offenders, f"catalog module(s) naming no registered provider: {offenders}"


def test_gate_passes_on_the_committed_catalog() -> None:
    result = run_gate(MODULES_DIR)
    assert result.returncode == 0, result.stderr


def test_gate_refuses_a_missing_module(tmp_path: Path) -> None:
    mutated = tmp_path / "modules"
    shutil.copytree(MODULES_DIR, mutated)
    shutil.rmtree(mutated / "deepseek")
    result = run_gate(mutated)
    assert result.returncode == 1, (result.returncode, result.stdout, result.stderr)
    assert "deepseek" in result.stderr


def test_gate_refuses_an_orphan_module(tmp_path: Path) -> None:
    mutated = tmp_path / "modules"
    shutil.copytree(MODULES_DIR, mutated)
    orphan = mutated / "zzz-orphan"
    orphan.mkdir()
    (orphan / "module.json").write_text(
        json.dumps(
            {
                "schema": "cmr.module/v1",
                "id": "zzz-orphan",
                "distribution": {"package": "gateway.providers.zzz-orphan"},
            }
        ),
        encoding="utf-8",
    )
    result = run_gate(mutated)
    assert result.returncode == 1, (result.returncode, result.stdout, result.stderr)
    assert "zzz-orphan" in result.stderr


if __name__ == "__main__":  # pragma: no cover - manual convenience
    raise SystemExit(pytest.main([__file__, "-q"]))
