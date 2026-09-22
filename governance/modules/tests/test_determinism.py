"""Determinism, and the honest CANNOT-ASSESS when the hub is not there.

Two builds over one revision are byte-identical (acceptance 5). And a clean
clone — no submodule, therefore no catalog — must answer *cannot assess*, never
a pass: a gate that cannot tell "nothing is wrong" from "I cannot see" is the
false green this repository forbids.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Dict, List

import pytest
import importlib.util as _importlib_util  # noqa: E402
from pathlib import Path as _ConftestPath  # noqa: E402

# A bare ``from conftest import ...`` is not safe here: when this suite is
# collected alongside other governance suites, every one of their
# ``tests/conftest.py`` files lands under the same bare module identity
# ``conftest`` in ``sys.modules``, so whichever conftest is imported LAST
# silently wins the name for the rest of collection (issues #699, #702, #1042).
# Loading this file's own conftest by absolute path guarantees this module
# always gets ITS directory's conftest regardless of collection order.
_conftest_spec = _importlib_util.spec_from_file_location(
    "governance_modules_tests_conftest", _ConftestPath(__file__).with_name("conftest.py")
)
_conftest = _importlib_util.module_from_spec(_conftest_spec)
_conftest_spec.loader.exec_module(_conftest)
PACKAGE_TARGETS = _conftest.PACKAGE_TARGETS
REAL_HUB = _conftest.REAL_HUB
write_repo = _conftest.write_repo
write_targets = _conftest.write_targets

from governance.modules import registry
from governance.modules.hub import DEFAULT_HUB

TIMESTAMPISH = re.compile(r'"(generated_at|timestamp|updated_at|built_at|now)"')


def _authority_states(repo_root: Path) -> Dict[str, List[str]]:
    """The four states, DERIVED from the pinned hub's own authority every run.

    A hand-recorded inventory goes stale the moment the pin moves, and the
    outage this replaced was exactly that: measured 2026-09-22, the pin carried
    `pmo` and `shared-governance` as mandatory (and four further catalog
    modules) while the recorded list still named three mandatory modules and a
    non-empty pending set — so the suite was red in every venue where the hub
    was materialised and green only where it was absent. The expectation is
    therefore READ, never restated:

    * ``catalog/mandatory.tsv`` is the machine-readable mandatory set — the
      hub's own ``catalog/validate.py`` keeps it in lockstep with the
      ``mandatory`` flags — so it, with the catalog it describes, is the
      authority on what a module is and what is mandatory;
    * the repository's declared target/watch set and its sub-module register
      supply the two name sets the catalog does not carry.
    """
    hub = repo_root / "vendor" / "CMR"
    catalog_ids = {
        path.parent.name for path in (hub / "catalog" / "modules").glob("*/module.json")
    }
    mandatory_ids = set()
    for line in (hub / "catalog" / "mandatory.tsv").read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#") or line.split("\t", 1)[0] == "id":
            continue
        mandatory_ids.add(line.split("\t", 1)[0])
    declared = json.loads(
        (repo_root / "governance" / "modules" / "targets.json").read_text(encoding="utf-8")
    )
    target_ids = {item["id"] for item in declared.get("targets", [])}
    watch_ids = {item["id"] for item in declared.get("watch", [])}
    register = json.loads((repo_root / "module.json").read_text(encoding="utf-8"))
    register_ids = {item["id"] for item in register.get("submodules", [])}
    return {
        "registered-mandatory": sorted(catalog_ids & mandatory_ids),
        "target-pending": sorted(target_ids - catalog_ids),
        "catalog-module-not-mandatory": sorted(catalog_ids - mandatory_ids),
        "not-a-module": sorted((register_ids | watch_ids) - catalog_ids - target_ids),
    }


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_two_builds_over_one_revision_are_byte_identical(consumer, hub, targets) -> None:
    first = registry.render(registry.build(consumer, hub, targets))
    second = registry.render(registry.build(consumer, hub, targets))
    assert first == second
    assert _sha(first) == _sha(second)


def test_a_rebuilt_document_is_identical_after_a_surfaces_change(consumer, hub, targets) -> None:
    """Order of reading is incidental: re-reading the same surfaces changes nothing."""
    first = registry.render(registry.build(consumer, hub, targets))
    for _ in range(3):
        assert registry.render(registry.build(consumer, hub, targets)) == first


def test_no_timestamp_is_recorded(consumer, hub, targets) -> None:
    text = registry.render(registry.build(consumer, hub, targets))
    assert TIMESTAMPISH.search(text) is None


def test_the_document_is_portable(consumer, hub, targets) -> None:
    """A scratch hub outside the repo is recorded as given, never absolutised."""
    text = registry.render(registry.build(consumer, hub, targets))
    assert str(hub) in text  # recorded as given (an absolute scratch path here)
    doc = registry.build(consumer, hub, targets)
    assert doc["hub"]["root"] == str(hub)


def test_a_default_hub_root_is_recorded_portably(tmp_path) -> None:
    """``vendor/CMR`` is recorded as ``vendor/CMR``, so the document travels."""
    write_hub = _conftest.write_hub

    repo = write_repo(tmp_path / "portable")
    write_hub(repo / DEFAULT_HUB)
    targets = write_targets(
        tmp_path / "t.json", {"schema": "ao.module-targets/v1", "targets": [], "watch": []}
    )
    doc = registry.build(repo, Path(DEFAULT_HUB), targets)
    text = registry.render(doc)
    assert doc["hub"]["root"] == DEFAULT_HUB
    assert doc["refusals"] == []
    assert str(tmp_path) not in text


@pytest.mark.skipif(
    not (REAL_HUB / "catalog").is_dir(),
    reason="hub submodule not initialised (vendor/CMR is an empty gitlink placeholder)",
)
class TestAgainstThePinnedHub:
    """The measured inventory, reproduced from the authority — not from prose."""

    @pytest.fixture
    def doc(self):
        return registry.build(Path(__file__).resolve().parents[3], Path(DEFAULT_HUB), PACKAGE_TARGETS)

    def test_the_measurable_states_match_the_measured_inventory(self, doc) -> None:
        by_state = {}
        for entry in doc["modules"]:
            by_state.setdefault(entry["state"], []).append(entry["id"])
        for state, expected in _authority_states(Path(__file__).resolve().parents[3]).items():
            if state == "not-a-module":
                assert [entry["id"] for entry in doc["not_modules"]] == expected
                continue
            assert sorted(by_state.get(state, [])) == expected, state

    def test_the_hub_revision_is_the_pinned_gitlink(self, doc) -> None:
        assert doc["hub"]["revision"] is not None
        assert re.fullmatch(r"[0-9a-f]{40}", doc["hub"]["revision"])
        assert doc["hub"]["revision_source"] in ("hub-checkout", "submodule-gitlink")

    def test_the_real_tree_is_clean_and_portable(self, doc) -> None:
        assert doc["refusals"] == []
        text = registry.render(doc)
        assert str(Path(__file__).resolve().parents[3]) not in text

    def test_missing_hub_is_cannot_assess_not_a_pass(self, tmp_path) -> None:
        from governance.modules.model import CannotAssess

        with pytest.raises(CannotAssess):
            registry.build(Path(__file__).resolve().parents[3], tmp_path / "no-hub", PACKAGE_TARGETS)
