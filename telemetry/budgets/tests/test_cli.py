"""telemetry/budgets — CLI tests (issue #34).

Exit-code contract: 0 allow/all-OK, 1 any block/refuse, 2 usage/config error.
"""

from __future__ import annotations

from telemetry.budgets.cli import main


def test_policies_exit_0(capsys):
    rc = main(["policies"])
    assert rc == 0
    out = capsys.readouterr().out
    assert '"tenantId": "acme"' in out


def test_quotas_exit_0(capsys):
    rc = main(["quotas"])
    assert rc == 0
    out = capsys.readouterr().out
    assert '"tenantId": "acme"' in out
    assert '"plan": "enterprise"' in out


def test_killswitch_status_off(capsys):
    rc = main(["killswitch", "status"])
    assert rc == 0
    out = capsys.readouterr().out
    assert '"globalPause": false' in out


def test_killswitch_pause_resume(tmp_path, capsys):
    state = tmp_path / "ks-state.yaml"
    audit = tmp_path / "audit.jsonl"
    rc = main(["killswitch", "pause", "--reason", "cli test",
               "--by", "pytest", "--state", str(state), "--audit", str(audit)])
    assert rc == 0
    assert "ENGAGED" in capsys.readouterr().out

    rc2 = main(["killswitch", "status", "--state", str(state)])
    assert rc2 == 0
    assert '"globalPause": true' in capsys.readouterr().out

    rc3 = main(["killswitch", "resume", "--state", str(state), "--audit", str(audit)])
    assert rc3 == 0
    assert "cleared" in capsys.readouterr().out


def test_check_over_budget_tenant_exits_1(capsys):
    # tenant-omega is observe; use acme (enforce) with a cost that blows its cap.
    # With an empty store the current spend is 0, so request beyond the cap.
    rc = main(["check", "--tenant", "acme", "--vendor", "deepseek",
               "--cost", "500.0", "--tokens", "1"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "REFUSED" in out


def test_check_within_budget_exits_0(capsys):
    rc = main(["check", "--tenant", "acme", "--vendor", "deepseek",
               "--cost", "0.01", "--tokens", "100"])
    assert rc == 0


def test_budget_rail_over_cap_exits_1(capsys):
    rc = main(["budget", "--tenant", "acme", "--cost", "999.0"])
    assert rc == 1


def test_quota_rail_over_hard_exits_1(capsys):
    # acme requests hardLimit 20000 (override); empty store -> 0 current,
    # so request 20001 calls to trip it.
    rc = main(["quota", "--tenant", "acme", "--resource", "requests",
               "--requested", "20001"])
    assert rc == 1


def test_demo_exit_0(capsys):
    rc = main(["demo"])
    assert rc == 0


def test_export_writes_json(tmp_path, capsys):
    out = tmp_path / "state.json"
    rc = main(["export", "--out", str(out)])
    assert rc == 0
    import json

    payload = json.loads(out.read_text(encoding="utf-8"))
    assert "killSwitch" in payload
    assert "acme" in payload["tenants"]


def test_unknown_command_exits_2():
    # argparse rejects unknown subcommands via SystemExit(2)
    try:
        main(["not-a-command"])
        rc = -1
    except SystemExit as exc:
        rc = exc.code
    assert rc == 2
