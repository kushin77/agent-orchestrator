"""pytest bootstrap for the routines adapter (issue #418).

Puts the repo root on ``sys.path`` so the tests import the adapter by its real
package path (``integrations.paperclip.adapters.routines``) regardless of where
pytest is invoked, and provides a minimal *schedule tree* — the real
``fleet/cron.py`` beside its ``runtime`` dependency AND the real
``config/fleet-jobs.json``, so a test can mutate the schedule the code declares
without touching the checkout.

``fleet/cron.py`` no longer carries the schedule as literals: since issue
#241/#962 it RENDERS it from ``config/fleet-jobs.json``, so the manifest is the
only place a marker can enter the schedule. A tree that carries only ``cron.py``
falls back to the module's ``_LEGACY_JOBS`` and would exercise a path the real
root never takes — which is exactly how the adapter's own drift control came to
provoke nothing (#1176). Hence ``mutate`` takes the parsed MANIFEST, and every
mutation names its anchor and asserts it matched exactly once.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[5]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FLEET = ROOT / "fleet"
MANIFEST = ROOT / "config" / "fleet-jobs.json"


def job(manifest: dict, name: str) -> dict:
    """The single job named ``name`` — or a loud failure, never a silent no-op."""
    matching = [entry for entry in manifest["jobs"] if entry.get("name") == name]
    assert len(matching) == 1, (
        "the manifest anchor moved: expected exactly one job named %r, found %d"
        % (name, len(matching))
    )
    return matching[0]


def build_schedule_tree(tmp_path: Path, mutate=None) -> Path:
    """A tree carrying ``fleet/cron.py`` (+ ``runtime``) and the real manifest.

    ``mutate`` receives the parsed manifest and may change it — that is how a test
    provokes drift (an entry added, retagged or removed) without editing the
    checkout. The PMO side is deliberately absent: the schedule tests must not
    depend on a board.
    """
    tree = tmp_path / "tree"
    (tree / "fleet").mkdir(parents=True, exist_ok=True)
    (tree / "config").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(FLEET / "runtime.py", tree / "fleet" / "runtime.py")
    shutil.copyfile(FLEET / "cron.py", tree / "fleet" / "cron.py")
    document = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if mutate is not None:
        before = json.dumps(document, sort_keys=True)
        mutate(document)
        assert json.dumps(document, sort_keys=True) != before, (
            "the mutation changed nothing — the manifest anchor moved"
        )
    (tree / "config" / "fleet-jobs.json").write_text(
        json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return tree


@pytest.fixture()
def schedule_tree(tmp_path: Path) -> Path:
    """A pristine schedule tree: exactly the four entries the code declares."""
    return build_schedule_tree(tmp_path)
