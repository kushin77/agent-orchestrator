"""CLI contract (0 OK / 1 NOT-OK / 2 CANNOT-ASSESS) and the drop-in apply path."""

from __future__ import annotations

import json

from conftest import engine


def test_cli_exits_zero_on_a_clean_checkout(make_repo, run_subprocess):
    root = make_repo("cli-clean")
    rc, out = run_subprocess(root)
    assert rc == engine.EXIT_OK
    assert "CTO overlay verdict: OK" in out


def test_cli_exits_one_when_a_blocking_layer_fails(make_repo, run_subprocess):
    root = make_repo("cli-block", drop=["docs/decision-records/ADR-0001-fixture.md"])
    rc, out = run_subprocess(root)
    assert rc == engine.EXIT_NOT_OK
    assert "BLOCKING-FAIL" in out
    assert "CTO overlay verdict: NOT-OK" in out


def test_cli_exits_two_on_a_malformed_config(make_repo, run_subprocess):
    def mutate(document):
        del document["layers"]["devops"]

    root = make_repo("cli-broken", mutate=mutate)
    rc, out = run_subprocess(root)
    assert rc == engine.EXIT_CANNOT_ASSESS
    assert "CANNOT-ASSESS" in out


def test_cli_json_output_is_machine_readable(make_repo, run_subprocess):
    root = make_repo("cli-json")
    rc, out = run_subprocess(root, "--format", "json")
    assert rc == engine.EXIT_OK
    payload = json.loads(out)
    assert payload["exit_code"] == engine.EXIT_OK
    assert payload["tier"] == "standard"
    assert {entry["layer"] for entry in payload["layers"]} == set(engine.LAYERS) | {
        engine.SIGNAL_SCOPE
    }
    assert payload["counts"][engine.STATE_PASS] > 0
    assert payload["cannot_assess"] == []


def test_validate_command_accepts_the_shipped_config(make_repo):
    root = make_repo("validate")
    assert engine.main(["validate", "--root", str(root)]) == engine.EXIT_OK


def test_list_layers_answers_from_the_config(make_repo, capsys):
    root = make_repo("list")
    assert (
        engine.main(["list-layers", "--root", str(root), "--tier", "standard"])
        == engine.EXIT_OK
    )
    out = capsys.readouterr().out
    assert "devops: warning at tier standard" in out
    assert "terraform-fmt" in out


def test_unknown_tier_is_cannot_assess(make_repo):
    root = make_repo("bad-tier")
    assert engine.main(["run", "--root", str(root), "--tier", "max"]) == engine.EXIT_CANNOT_ASSESS
    assert engine.main(["run", "--root", str(root), "--tier", "T2"]) == engine.EXIT_OK


def test_self_test_command_proves_the_gate_discriminates():
    assert engine.main(["self-test"]) == engine.EXIT_OK


def test_apply_is_a_real_drop_in(tmp_path):
    target = tmp_path / "consumer"
    target.mkdir()
    assert engine.main(["apply", "--target", str(target)]) == engine.EXIT_OK
    config = engine.load_config(target)
    assert config.repo_name == "consumer"
    for artifact in engine.ARTIFACTS:
        assert (target / "governance" / "cto-overlay" / artifact).is_file()
    # An ungoverned repository is not magically green: the applied overlay runs
    # there and refuses to report a pass it cannot evidence.
    assert engine.main(["run", "--root", str(target)]) != engine.EXIT_OK


def test_apply_refuses_its_own_source_checkout():
    source_root = engine.OVERLAY_DIR.parents[1]
    assert engine.main(["apply", "--target", str(source_root)]) == engine.EXIT_NOT_OK


def test_apply_dry_run_writes_nothing(tmp_path):
    target = tmp_path / "dry"
    target.mkdir()
    assert engine.main(["apply", "--target", str(target), "--dry-run"]) == engine.EXIT_OK
    assert not (target / "governance").exists()
