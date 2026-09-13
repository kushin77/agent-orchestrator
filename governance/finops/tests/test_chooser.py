"""The FinOps chooser enforces the brain's tier/thinking choice (issue #164).

Three properties are proven here, and none of them is a formality:

1. an unknown tier or thinking level is refused with a named finding;
2. a subagent cannot choose its own tier — a divergent request is refused in
   both directions, an echo is not an override, and a recorded spawn that no
   longer reproduces the directive is refused;
3. the vocabulary is the harvested one, and the CLI's exit codes are honest
   (0 OK / 1 NOT-OK / 2 CANNOT-ASSESS).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import chooser

ROOT = Path(__file__).resolve().parents[3]
CHOOSER_PY = ROOT / "governance" / "finops" / "chooser.py"

HARVESTED_TIERS = ("flash", "pro", "auditor")
HARVESTED_THINKING = ("none", "low", "medium", "high")


@pytest.fixture
def policy() -> dict:
    return chooser.load_policy()


def directive(tier: str, thinking: str, issue: int = 164, message_id: str = "d-1") -> dict:
    return {
        "id": message_id,
        "from": "brain",
        "to": "sister",
        "type": "directive",
        "model": {"tier": tier, "thinking": thinking, "budget_hint": "cheapest capable tier"},
        "task": {"issue": issue},
    }


def codes(findings) -> list[str]:
    return [finding.code for finding in findings]


def run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(CHOOSER_PY), *args],
        capture_output=True,
        text=True,
        check=False,
    )


# --- the vocabulary is harvested, not invented ------------------------------


def test_vocabulary_is_the_harvested_set(policy: dict) -> None:
    tiers, thinking = chooser.vocabulary(policy)
    assert tiers == HARVESTED_TIERS
    assert thinking == HARVESTED_THINKING


def test_tier_models_and_ranks_cover_the_vocabulary(policy: dict) -> None:
    tiers, thinking = chooser.vocabulary(policy)
    assert set(policy["tier_models"]) == set(tiers)
    assert set(policy["tier_rank"]) == set(tiers)
    assert set(policy["thinking_rank"]) == set(thinking)
    assert list(policy["tier_rank"].values()) == sorted(policy["tier_rank"].values())
    assert list(policy["thinking_rank"].values()) == sorted(policy["thinking_rank"].values())


def test_render_vocabulary_is_the_two_pinned_lines(policy: dict) -> None:
    assert chooser.render_vocabulary(policy) == (
        "tiers: flash, pro, auditor\nthinking: none, low, medium, high\n"
    )


def test_finding_codes_are_unique() -> None:
    names = [name for name in dir(chooser) if name.startswith("FINDING_")]
    assert len(names) >= 8
    assert len({getattr(chooser, name) for name in names}) == len(names)


# --- accept: the standing directive puts the sister on the DSv4FNone seat ----


def test_standing_directive_puts_the_sister_on_the_dsv4fnone_seat(policy: dict) -> None:
    record, findings = chooser.choose(policy, directive("flash", "none"), "sister")
    assert findings == []
    assert record is not None
    assert (record.tier, record.thinking) == ("flash", "none")
    assert record.model == "deepseek-v4-flash"


def test_a_subagent_runs_the_auditor_tier_when_the_brain_says_so(policy: dict) -> None:
    record, findings = chooser.choose(policy, directive("auditor", "high"), "subagent-a1")
    assert findings == []
    assert record is not None
    assert (record.tier, record.thinking) == ("auditor", "high")
    assert record.model == "deepseek-v4-pro"


def test_echoing_the_directive_is_not_an_override(policy: dict) -> None:
    record, findings = chooser.choose(
        policy,
        directive("pro", "medium"),
        "subagent-a2",
        requested_tier="pro",
        requested_thinking="medium",
    )
    assert findings == []
    assert record is not None


# --- refuse: unknown vocabulary ---------------------------------------------


def test_unknown_tier_is_refused_with_a_named_finding(policy: dict) -> None:
    record, findings = chooser.choose(policy, directive("ultra", "none"), "sister")
    assert record is None
    assert codes(findings) == [chooser.FINDING_UNKNOWN_TIER]


def test_unknown_thinking_is_refused_with_a_named_finding(policy: dict) -> None:
    record, findings = chooser.choose(policy, directive("flash", "max"), "sister")
    assert record is None
    assert codes(findings) == [chooser.FINDING_UNKNOWN_THINKING]


def test_missing_model_block_is_refused(policy: dict) -> None:
    bare = {"from": "brain", "to": "sister", "type": "directive", "task": {"issue": 164}}
    record, findings = chooser.choose(policy, bare, "sister")
    assert record is None
    assert codes(findings) == [chooser.FINDING_MISSING_MODEL_BLOCK]


def test_a_non_directive_input_is_refused(policy: dict) -> None:
    result = {"from": "sister", "to": "brain", "type": "result", "model": {"tier": "flash", "thinking": "none"}}
    record, findings = chooser.choose(policy, result, "sister")
    assert record is None
    assert codes(findings) == [chooser.FINDING_BAD_DIRECTIVE]
    assert chooser.choose(policy, "not-an-object", "sister")[1][0].code == chooser.FINDING_BAD_DIRECTIVE


def test_a_role_outside_the_fleet_is_refused(policy: dict) -> None:
    record, findings = chooser.choose(policy, directive("flash", "none"), "contractor")
    assert record is None
    assert codes(findings) == [chooser.FINDING_ROLE_NOT_ALLOWED]


# --- refuse: a subagent may not choose its own tier -------------------------


def test_the_sister_cannot_be_dispatched_above_flash(policy: dict) -> None:
    record, findings = chooser.choose(policy, directive("pro", "none"), "sister")
    assert record is None
    assert codes(findings) == [chooser.FINDING_ROLE_NOT_ALLOWED]


def test_the_sister_cannot_be_dispatched_with_thinking(policy: dict) -> None:
    record, findings = chooser.choose(policy, directive("flash", "low"), "sister")
    assert record is None
    assert codes(findings) == [chooser.FINDING_ROLE_NOT_ALLOWED]


def test_a_subagent_cannot_escalate_its_own_tier(policy: dict) -> None:
    record, findings = chooser.choose(
        policy, directive("flash", "low"), "subagent-a1", requested_tier="auditor"
    )
    assert record is None
    assert codes(findings) == [chooser.FINDING_SELF_ESCALATION]
    assert "escalation is a self-override" in findings[0].detail


def test_a_subagent_cannot_downgrade_its_own_tier(policy: dict) -> None:
    record, findings = chooser.choose(
        policy, directive("pro", "low"), "subagent-a1", requested_tier="flash"
    )
    assert record is None
    assert codes(findings) == [chooser.FINDING_SELF_ESCALATION]
    assert "downgrade is a self-override" in findings[0].detail


def test_a_subagent_cannot_raise_its_own_thinking_effort(policy: dict) -> None:
    record, findings = chooser.choose(
        policy, directive("pro", "low"), "subagent-a1", requested_thinking="high"
    )
    assert record is None
    assert codes(findings) == [chooser.FINDING_SELF_ESCALATION]


def test_an_unknown_requested_tier_is_refused_by_the_vocabulary(policy: dict) -> None:
    record, findings = chooser.choose(
        policy, directive("flash", "none"), "subagent-a1", requested_tier="turbo"
    )
    assert record is None
    assert codes(findings) == [chooser.FINDING_UNKNOWN_TIER]


def test_an_unknown_requested_thinking_is_refused_by_the_vocabulary(policy: dict) -> None:
    record, findings = chooser.choose(
        policy, directive("flash", "none"), "subagent-a1", requested_thinking="max"
    )
    assert record is None
    assert codes(findings) == [chooser.FINDING_UNKNOWN_THINKING]


# --- the spawn record carries the brain's choice ----------------------------


def test_the_spawn_record_carries_the_brain_choice(policy: dict) -> None:
    authorising = directive("pro", "high", issue=164, message_id="directive-0164")
    record, findings = chooser.choose(policy, authorising, "subagent-a3", issue=999)
    assert findings == []
    assert record is not None
    assert record.chosen_by == "brain"
    assert record.directive_id == "directive-0164"
    assert record.issue == 999
    assert record.directive_fingerprint == chooser.model_fingerprint(authorising["model"])
    assert record.vocabulary_version == policy["policy_version"]


def test_the_issue_falls_back_to_the_directive_task(policy: dict) -> None:
    record, _ = chooser.choose(policy, directive("flash", "none", issue=164), "subagent-a4")
    assert record is not None
    assert record.issue == 164


def test_verify_spawn_accepts_its_own_record(policy: dict) -> None:
    authorising = directive("pro", "medium")
    record, _ = chooser.choose(policy, authorising, "subagent-a5")
    assert chooser.verify_spawn(policy, authorising, record.to_dict()) == []


def test_verify_spawn_refuses_a_raised_tier(policy: dict) -> None:
    authorising = directive("flash", "none")
    record, _ = chooser.choose(policy, authorising, "subagent-a6")
    tampered = record.to_dict()
    tampered["tier"] = "auditor"
    findings = chooser.verify_spawn(policy, authorising, tampered)
    assert codes(findings) == [chooser.FINDING_SPAWN_TAMPERED]
    assert "does not match the directive" in findings[0].detail


def test_verify_spawn_refuses_a_lowered_thinking_effort(policy: dict) -> None:
    authorising = directive("pro", "high")
    record, _ = chooser.choose(policy, authorising, "subagent-a7")
    tampered = record.to_dict()
    tampered["thinking"] = "none"
    findings = chooser.verify_spawn(policy, authorising, tampered)
    assert chooser.FINDING_SPAWN_TAMPERED in codes(findings)


def test_verify_spawn_refuses_a_self_authored_choice(policy: dict) -> None:
    authorising = directive("flash", "none")
    record, _ = chooser.choose(policy, authorising, "subagent-a8")
    tampered = record.to_dict()
    tampered["chosen_by"] = "subagent-a8"
    findings = chooser.verify_spawn(policy, authorising, tampered)
    assert codes(findings) == [chooser.FINDING_SPAWN_TAMPERED]
    assert "only the brain may choose a tier" in findings[0].detail


def test_verify_spawn_refuses_a_rewritten_directive_block(policy: dict) -> None:
    authorising = directive("flash", "none")
    record, _ = chooser.choose(policy, authorising, "subagent-a9")
    rewritten = json.loads(json.dumps(authorising))
    rewritten["model"]["budget_hint"] = "spend freely"
    findings = chooser.verify_spawn(policy, rewritten, record.to_dict())
    assert chooser.FINDING_SPAWN_TAMPERED in codes(findings)
    assert any("fingerprint" in finding.detail for finding in findings)


def test_verify_spawn_refuses_a_wrong_model_for_the_tier(policy: dict) -> None:
    authorising = directive("pro", "none")
    record, _ = chooser.choose(policy, authorising, "subagent-a10")
    tampered = record.to_dict()
    tampered["model"] = "deepseek-v4-flash"
    findings = chooser.verify_spawn(policy, authorising, tampered)
    assert chooser.FINDING_SPAWN_TAMPERED in codes(findings)


def test_verify_spawn_refuses_a_malformed_record(policy: dict) -> None:
    authorising = directive("pro", "none")
    assert codes(chooser.verify_spawn(policy, authorising, "nope")) == [chooser.FINDING_SPAWN_MALFORMED]
    assert codes(chooser.verify_spawn(policy, authorising, {"role": "subagent-a1"})) == [
        chooser.FINDING_SPAWN_MALFORMED
    ]


def test_policy_unavailable_is_not_a_pass(tmp_path: Path) -> None:
    with pytest.raises(chooser.PolicyUnavailable):
        chooser.load_policy(tmp_path / "absent.json")
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    with pytest.raises(chooser.PolicyUnavailable):
        chooser.load_policy(broken)
    empty = tmp_path / "empty.json"
    empty.write_text('{"policy_version": "1"}', encoding="utf-8")
    with pytest.raises(chooser.PolicyUnavailable):
        chooser.load_policy(empty)


# --- the CLI is what the gate drives ----------------------------------------


def test_cli_exit_codes_are_tri_state(tmp_path: Path) -> None:
    good = tmp_path / "good.json"
    good.write_text(json.dumps(directive("pro", "low")), encoding="utf-8")
    spawn = tmp_path / "spawn.json"

    ok = run_cli("choose", "--directive", str(good), "--role", "subagent-a1", "--write", str(spawn))
    assert ok.returncode == 0, ok.stderr
    assert "chosen by brain" in ok.stdout
    assert json.loads(spawn.read_text(encoding="utf-8"))["tier"] == "pro"

    refused = run_cli(
        "choose", "--directive", str(good), "--role", "subagent-a1", "--request-tier", "auditor"
    )
    assert refused.returncode == 1
    assert chooser.FINDING_SELF_ESCALATION in refused.stderr

    unassessable = run_cli("choose", "--directive", str(tmp_path / "absent.json"), "--role", "sister")
    assert unassessable.returncode == 2
    assert "CANNOT-ASSESS" in unassessable.stderr


def test_cli_verify_spawn_and_vocabulary(tmp_path: Path) -> None:
    good = tmp_path / "good.json"
    good.write_text(json.dumps(directive("flash", "none")), encoding="utf-8")
    spawn = tmp_path / "spawn.json"
    record = tmp_path / "record.json"
    assert run_cli("choose", "--directive", str(good), "--role", "sister", "--write", str(record)).returncode == 0

    spawn.write_text(record.read_text(encoding="utf-8"), encoding="utf-8")
    accepted = run_cli("verify-spawn", "--directive", str(good), "--spawn", str(spawn))
    assert accepted.returncode == 0, accepted.stderr

    payload = json.loads(spawn.read_text(encoding="utf-8"))
    payload["tier"] = "auditor"
    spawn.write_text(json.dumps(payload), encoding="utf-8")
    refused = run_cli("verify-spawn", "--directive", str(good), "--spawn", str(spawn))
    assert refused.returncode == 1
    assert chooser.FINDING_SPAWN_TAMPERED in refused.stderr

    vocab = run_cli("vocabulary")
    assert vocab.returncode == 0
    assert vocab.stdout == "tiers: flash, pro, auditor\nthinking: none, low, medium, high\n"
