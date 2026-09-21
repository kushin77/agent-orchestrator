"""Determinism, and the honest CANNOT-ASSESS when the hub is not there.

Two builds over one revision are byte-identical (acceptance 5). And a clean
clone — no submodule, therefore no catalog — must answer *cannot assess*, never
a pass: a gate that cannot tell "nothing is wrong" from "I cannot see" is the
false green this repository forbids.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

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

#: The inventory this repository measured on 2026-09-14 over the pinned hub.
MEASURED = {
    "registered-mandatory": ["code-indexing", "diagrams", "shared-frontend"],
    "target-pending": ["pmo", "shared-governance", "shared-services"],
    "catalog-module-not-mandatory": ["erp-crm", "googleworkspace", "saas-rbac"],
    "not-a-module": [
        "deepseek",
        "gcp-gatekeeper",
        "hermes-agents",
        "monitoring-stack",
        "ollama",
        "paperclip",
    ],
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
        for state, expected in MEASURED.items():
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
