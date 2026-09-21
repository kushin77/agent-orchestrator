"""Determinism, and the frozen artifact that rides on it (issue #447)."""

from __future__ import annotations

from conftest import require_real_hub

import hashlib
import subprocess
import sys
from pathlib import Path

from integrations.paperclip.reporting import composer
from integrations.paperclip.reporting.model import ARTIFACT

HUB = "vendor/CMR"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_two_compositions_over_one_revision_are_byte_identical(repo_root: Path, registry_document):
    first = composer.compose(registry_document, repo_root, HUB)
    second = composer.compose(registry_document, repo_root, HUB)
    assert first.text == second.text
    assert _sha(first.text) == _sha(second.text)
    assert [claim.line for claim in first.claims] == [claim.line for claim in second.claims]
    assert [claim.citations for claim in first.claims] == [
        claim.citations for claim in second.claims
    ]


def test_the_composition_records_no_time_of_its_own(composition):
    """Determinism means the brief does not move when the clock does."""
    import re

    assert not re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", composition.text)


def test_the_committed_artifact_is_current(repo_root: Path, composition):
    committed = (repo_root / ARTIFACT).read_text(encoding="utf-8")
    assert committed == composition.text, (
        "{} differs from a fresh composition — regenerate it rather than editing it".format(
            ARTIFACT
        )
    )


def test_the_composition_really_reads_the_hub_it_names(repo_root: Path):
    """A control: drop the revision and the composition must change."""
    require_real_hub()
    import copy

    from governance.modules import registry as module_registry

    document = module_registry.build(repo_root, repo_root / HUB)
    baseline = composer.compose(document, repo_root, HUB)
    doctored = copy.deepcopy(document)
    doctored["hub"]["revision"] = "0" * 40
    changed = composer.compose(doctored, repo_root, HUB)
    assert _sha(changed.text) != _sha(baseline.text)
    assert "0" * 40 in changed.text


def test_a_module_with_no_pin_really_is_refused_from_a_scratch_tree(tree: Path):
    """The same provocation the gate runs, through the CLI and a fresh interpreter."""
    cli = tree / "integrations/paperclip/reporting/cli.py"
    hub = tree / HUB / "catalog/modules/code-indexing/module.json"
    before = hub.read_text(encoding="utf-8")
    hub.write_text(
        before.replace('"latest": "v0.1.0"', '"latest": null'), encoding="utf-8"
    )
    assert hub.read_text(encoding="utf-8") != before, "the mutation did not land"

    proc = subprocess.run(
        [sys.executable, str(cli), "compose", "--repo", str(tree)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 1, proc.stderr
    assert "BRIEF-MODULE-NO-PIN: code-indexing" in proc.stderr
