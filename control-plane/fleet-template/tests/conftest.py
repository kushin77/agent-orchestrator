"""Shared fixtures for the fleet-template suite (issue #146).

The lane directory is intentionally NOT a Python package (it is named
``fleet-template``, which is not importable), so the suite bootstraps
``sys.path`` to import ``render`` directly -- the same way the sibling
``control-plane/instructions`` suite bootstraps its package.
"""

from __future__ import annotations

import copy
import os
import shutil
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
LANE_ROOT = os.path.dirname(HERE)
REPO_ROOT = os.path.dirname(os.path.dirname(LANE_ROOT))

if LANE_ROOT not in sys.path:
    sys.path.insert(0, LANE_ROOT)

import render  # noqa: E402  (deliberate: import follows the sys.path bootstrap)

PILOTS = ("agent-orchestrator", "shared-frontend")


@pytest.fixture(scope="session")
def pilots() -> tuple:
    return PILOTS


@pytest.fixture(params=PILOTS)
def pilot(request) -> str:
    """Parametrized over the committed pilots, so every pilot-level property is
    measured on both of them rather than on the one that happens to be handy."""
    return request.param


@pytest.fixture(scope="session")
def lane_root() -> str:
    return LANE_ROOT


@pytest.fixture(scope="session")
def repo_root() -> str:
    return REPO_ROOT


@pytest.fixture(scope="session")
def bundle() -> dict:
    return render.load_bundle(LANE_ROOT)[0]


@pytest.fixture(scope="session")
def template_bytes() -> bytes:
    return render.read_bytes(os.path.join(LANE_ROOT, render.TEMPLATE_FILE))


@pytest.fixture(scope="session")
def template(template_bytes: bytes) -> dict:
    return render.load_yaml(os.path.join(LANE_ROOT, render.TEMPLATE_FILE))


@pytest.fixture(scope="session")
def renderer(bundle, template, template_bytes):
    """Render a pilot (or a mutated variant of one) without touching the tree."""

    def _render(
        pilot: str = "agent-orchestrator",
        *,
        values_overrides: dict | None = None,
        observations: object = "auto",
        params_doc: dict | None = None,
        template_doc: dict | None = None,
    ) -> dict:
        params_path = os.path.join(LANE_ROOT, render.PILOTS_DIR, f"{pilot}.params.yaml")
        params_bytes = render.read_bytes(params_path)
        doc = params_doc if params_doc is not None else render.load_yaml(params_path)
        if values_overrides:
            doc = copy.deepcopy(doc)
            doc["values"].update(values_overrides)
            params_bytes = render.dump_yaml(doc).encode("utf-8")
        if observations == "auto":
            declared = doc.get("observations")
            observations_doc = (
                render.load_yaml(os.path.join(os.path.dirname(params_path), declared))
                if declared
                else None
            )
        else:
            observations_doc = observations
        return render.render_instance(
            template_doc if template_doc is not None else template,
            doc,
            bundle,
            render.dump_yaml(template_doc).encode("utf-8") if template_doc is not None else template_bytes,
            params_bytes,
            os.path.relpath(params_path, LANE_ROOT),
            observations_doc,
        )

    return _render


@pytest.fixture
def lane_copy(tmp_path):
    """A writable copy of the whole lane, for drift/corruption controls.

    ``lane_copy("name")`` gives each caller its own tree, so one test can hold
    several differently-broken copies at once.
    """

    def _copy(name: str = "fleet-template") -> str:
        dest = str(tmp_path / name)
        shutil.copytree(
            LANE_ROOT,
            dest,
            ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"),
        )
        return dest

    return _copy


@pytest.fixture(scope="session")
def committed_instance(lane_root):
    """Load a committed pilot instance from the tree."""

    def _load(pilot: str) -> dict:
        return render.load_yaml(os.path.join(lane_root, render.PILOTS_DIR, f"{pilot}.fleet.yaml"))

    return _load


def write_yaml(path: str, document: object) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(render.dump_yaml(document))


def replace_instance(root: str, pilot: str, mutate) -> None:
    """Load a committed instance from ``root``, mutate it, write it back."""
    path = os.path.join(root, render.PILOTS_DIR, f"{pilot}.fleet.yaml")
    document = render.load_yaml(path)
    mutate(document)
    write_yaml(path, document)
