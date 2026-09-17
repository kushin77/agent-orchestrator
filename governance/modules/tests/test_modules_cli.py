"""The CLI's exit-code contract and its refusals, observed at the boundary.

The gate of record drives these commands, so the contract is tested where the
gate observes it: the process boundary — 0 OK, 1 NOT-OK, 2 CANNOT-ASSESS, and
never a 0 that hides an unassessable registry.
"""

from __future__ import annotations

import json
import os
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
write_repo = _conftest.write_repo
write_targets = _conftest.write_targets

from governance.modules import registry
from governance.modules.cli import (
    EXIT_CANNOT_ASSESS,
    EXIT_NOT_OK,
    EXIT_OK,
    main,
)


def cli(argv) -> int:
    return main(list(argv))


def _args(hub: Path, consumer: Path, targets: Path):
    return ["--repo", str(consumer), "--hub", str(hub), "--targets", str(targets)]


def test_verify_is_green_on_a_clean_tree(hub, consumer, targets, capsys) -> None:
    assert cli(["verify", *_args(hub, consumer, targets)]) == EXIT_OK
    out = capsys.readouterr().out
    assert "registered-mandatory" in out
    assert "0 refusal(s)" in out


def test_verify_is_not_ok_when_a_refusal_exists(consumer, make_hub, targets, capsys) -> None:
    scratch = make_hub("noseed", seeds=["alpha.json"])
    assert cli(["verify", *_args(scratch, consumer, targets)]) == EXIT_NOT_OK
    assert "MODULE-ASSET-NO-SEED: beta" in capsys.readouterr().out


def test_verify_is_cannot_assess_without_the_hub(tmp_path, consumer, targets, capsys) -> None:
    assert cli(["verify", *_args(tmp_path / "absent", consumer, targets)]) == EXIT_CANNOT_ASSESS
    assert "CANNOT-ASSESS" in capsys.readouterr().err


def test_build_writes_the_canonical_document(hub, consumer, targets, tmp_path, capsys) -> None:
    out = tmp_path / "registry.json"
    assert cli(["build", *_args(hub, consumer, targets), "--out", str(out)]) == EXIT_OK
    text = out.read_text(encoding="utf-8")
    assert text == registry.render(registry.build(consumer, hub, targets))
    assert text.endswith("}\n")
    assert "wrote" in capsys.readouterr().out


def test_build_prints_to_stdout(hub, consumer, targets, capsys) -> None:
    assert cli(["build", *_args(hub, consumer, targets)]) == EXIT_OK
    assert json.loads(capsys.readouterr().out)["schema"] == "ao.module-registry/v1"


def test_build_reports_a_refusal_but_still_emits(consumer, make_hub, targets, capsys) -> None:
    scratch = make_hub("dup", seeds=["alpha.json"])
    copied = scratch / "catalog" / "modules" / "zz-copy"
    copied.mkdir(parents=True)
    (copied / "module.json").write_text(
        (scratch / "catalog" / "modules" / "alpha" / "module.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    assert cli(["build", *_args(scratch, consumer, targets)]) == EXIT_NOT_OK
    captured = capsys.readouterr()
    assert "MODULE-DUPLICATE-ID: alpha" in captured.err  # the finding is rendered
    assert "NOT-OK" in captured.err
    assert json.loads(captured.out)["refusals"]  # and it is in the document too


def test_membership_resolves_and_refuses(hub, consumer, targets, capsys) -> None:
    assert cli(["membership", "zeta", *_args(hub, consumer, targets)]) == EXIT_OK
    assert "target-pending: zeta" in capsys.readouterr().out
    assert cli(["membership", "epsilon", *_args(hub, consumer, targets)]) == EXIT_NOT_OK
    out = capsys.readouterr().out
    assert "not-a-module: epsilon" in out
    assert "a claim is not membership" in out


def test_membership_is_cannot_assess_without_the_hub(tmp_path, consumer, targets, capsys) -> None:
    assert cli(["membership", "alpha", *_args(tmp_path / "absent", consumer, targets)]) == EXIT_CANNOT_ASSESS
    assert "CANNOT-ASSESS" in capsys.readouterr().err


def test_vendoring_scans_a_scratch_tree(hub, tmp_path, capsys) -> None:
    clean = write_repo(tmp_path / "clean")
    assert cli(["vendoring", "--repo", str(clean), "--hub", str(hub)]) == EXIT_OK
    dirty = write_repo(tmp_path / "dirty")
    (dirty / "gammapkg").mkdir()
    assert cli(["vendoring", "--repo", str(dirty), "--hub", str(hub)]) == EXIT_NOT_OK
    assert "VENDOR-IN-TREE-PACKAGE: gammapkg" in capsys.readouterr().out


def test_vendoring_checks_the_references_of_a_built_document(hub, consumer, targets, tmp_path, capsys) -> None:
    doc = registry.build(consumer, hub, targets)
    doc["modules"][0]["reference"]["path"] = "../../elsewhere/module.json"
    registry_file = tmp_path / "registry.json"
    registry_file.write_text(registry.render(doc), encoding="utf-8")
    assert cli(["vendoring", "--repo", str(consumer), "--hub", str(hub), "--registry", str(registry_file)]) == EXIT_NOT_OK
    assert "VENDOR-PATH-OUTSIDE-HUB" in capsys.readouterr().out


def test_probe_offline_is_cannot_assess_never_a_pass(hub, consumer, targets, capsys) -> None:
    assert cli(["probe", *_args(hub, consumer, targets)]) == EXIT_CANNOT_ASSESS
    out = capsys.readouterr().out
    assert "not-run" in out
    assert "CANNOT-ASSESS" in out


@pytest.mark.skipif(
    os.environ.get("AO_MODULE_REGISTRY_LIVE") != "1",
    reason="live pin probe needs the network; set AO_MODULE_REGISTRY_LIVE=1 to run it",
)
def test_probe_live_records_what_it_found(capsys) -> None:
    PACKAGE_TARGETS = _conftest.PACKAGE_TARGETS

    repo = str(Path(__file__).resolve().parents[3])
    code = cli(
        ["probe", "--live", "--repo", repo, "--hub", "vendor/CMR", "--targets", str(PACKAGE_TARGETS)]
    )
    out = capsys.readouterr().out
    assert code in (EXIT_OK, EXIT_NOT_OK, EXIT_CANNOT_ASSESS)
    assert "ok" in out or "unreachable" in out or "no-tag" in out
    if code == EXIT_OK:
        assert "verified" in out


def test_a_missing_subcommand_is_a_usage_error() -> None:
    with pytest.raises(SystemExit):
        cli([])
