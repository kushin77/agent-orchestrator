"""Shared fixtures for the CTO overlay suite (#147).

The engine lives in a hyphenated directory (`governance/cto-overlay/`), which
cannot be imported as a package path, so it is loaded by file path once and
handed to the tests as `engine`. Every test builds its own scratch repository:
the overlay's verdicts must be behavioural, so they are observed on real
checkouts, never read out of the source.

Config mutations go through the parsed document (`mutate`), so a test changes
the config the way a maintainer would — by editing the YAML — instead of
patching engine internals.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

OVERLAY_DIR = Path(__file__).resolve().parents[1]
ENGINE_PATH = OVERLAY_DIR / "overlay.py"


def _load_engine():
    # Idempotent: several test files in this directory load their own copy of
    # this conftest.py by absolute path (see their "whichever conftest is
    # imported LAST" comment) to dodge the bare-name "conftest" collision
    # across sibling governance/* suites. Each such reload re-executes this
    # function, and a non-idempotent version would mint a brand-new engine
    # module with its own `CHECKS` dict every time — leaving the real
    # `assess` fixture (bound to the FIRST module) and a test file's own
    # `engine` reference (bound to whichever reload ran last) pointing at two
    # different objects. `monkeypatch.setitem(engine.CHECKS, ...)` would then
    # patch a dict `run_overlay` never reads, and a crashing/indeterminate
    # check would silently keep reporting its real PASS instead of INDET
    # (#1501). Returning the cached singleton keeps every caller, in every
    # file, on the same module.
    cached = sys.modules.get("cto_overlay_engine")
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location("cto_overlay_engine", ENGINE_PATH)
    assert spec and spec.loader, f"cannot load {ENGINE_PATH}"
    module = importlib.util.module_from_spec(spec)
    sys.modules["cto_overlay_engine"] = module
    spec.loader.exec_module(module)
    return module


engine = _load_engine()


def git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        timeout=120,
    )


def commit_all(root: Path, message: str = "fixture") -> None:
    git(root, "add", "-A")
    git(
        root,
        "-c",
        "user.email=overlay@example.invalid",
        "-c",
        "user.name=overlay test",
        "commit",
        "-q",
        "-m",
        message,
    )


@pytest.fixture
def make_repo(tmp_path: Path):
    """Build a scratch governed repository carrying this repo's shipped config.

    Knobs: `mutate(document)` edits the parsed config, `schema_mutate(text)`
    edits the schema source, `extra_files` adds tracked content and `drop`
    removes fixture surfaces.
    """

    def _make(
        name: str = "fixture",
        mutate=None,
        schema_mutate=None,
        extra_files=None,
        drop=(),
        drop_config=False,
    ) -> Path:
        root = tmp_path / name
        overlay = root / "governance" / "cto-overlay"
        overlay.mkdir(parents=True)
        for artifact in (engine.SCHEMA_NAME, engine.CONFIG_NAME, "overlay.py"):
            shutil.copyfile(OVERLAY_DIR / artifact, overlay / artifact)
        files = dict(engine.MINIMAL_FIXTURE)
        for path in drop:
            files.pop(path, None)
        files.update(extra_files or {})
        for relative, content in files.items():
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        config_file = overlay / engine.CONFIG_NAME
        if drop_config:
            config_file.unlink()
        elif mutate is not None:
            document = yaml.safe_load(config_file.read_text(encoding="utf-8"))
            mutate(document)
            config_file.write_text(
                yaml.safe_dump(document, sort_keys=False), encoding="utf-8"
            )
        if schema_mutate is not None:
            schema_file = overlay / engine.SCHEMA_NAME
            schema_file.write_text(
                schema_mutate(schema_file.read_text(encoding="utf-8")), encoding="utf-8"
            )
        git(root, "init", "-q")
        commit_all(root)
        return root

    return _make


@pytest.fixture
def run_in_process():
    """Run the engine CLI in-process; returns the exit code."""

    def _run(root: Path, *args: str) -> int:
        return engine.main(["run", "--root", str(root), *args])

    return _run


@pytest.fixture
def run_subprocess():
    """Run the engine CLI as a child process; returns (rc, combined output)."""

    def _run(root: Path, *args: str):
        completed = subprocess.run(
            [sys.executable, str(ENGINE_PATH), "run", "--root", str(root), *args],
            capture_output=True,
            text=True,
            timeout=300,
        )
        return completed.returncode, completed.stdout + completed.stderr

    return _run


@pytest.fixture
def assess():
    """Load the config and run the overlay in-process, returning the report."""

    def _assess(root: Path, tier: str = "standard", layers=None, diff_base=None):
        config = engine.load_config(root)
        return engine.run_overlay(root, config, tier, layers, diff_base)

    return _assess


def verdicts_for(report, scope: str):
    return [verdict for verdict in report.tally.verdicts if verdict.scope == scope]


def state_of(report, scope: str, check: str) -> str:
    for verdict in report.tally.verdicts:
        if verdict.scope == scope and verdict.check == check:
            return verdict.state
    raise AssertionError(f"{scope}/{check} is missing from the tally")


def summary_of(report, layer: str):
    for summary in report.tally.layer_summaries():
        if summary.layer == layer:
            return summary
    raise AssertionError(f"layer {layer} is missing from the tally")
