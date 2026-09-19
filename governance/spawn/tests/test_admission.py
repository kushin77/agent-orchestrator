"""Admission: the three judges, called at the spawn point (issue #1413).

#1377, #1372 and #1371 each landed a judge and each reported the same gap: the
Claude-subagent admission point is ``governance/spawn/model.py``, and nothing
called them there — so a lane could still be spawned at a forbidden tier or by an
actor nobody had declared, and all three judges were inert.

These tests are the negative controls for that, one per judge, each asserting the
refusal BY ITS OWN NAME (the vocabulary its owning module declared) and not merely
that something was refused. The last test drives the real CLI with minting
ENABLED and proves the ordering the acceptance asks for: a refused spawn leaves no
worktree behind, because admission runs before anything is created.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT / "governance" / "dispatch") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "governance" / "dispatch"))

from governance.spawn import admission, model, tiering  # noqa: E402

#: A spawn record that IS admissible, so every test below changes exactly one
#: thing about it and knows which judge the change is aimed at.
ADMISSIBLE = {
    "path": "local",
    "agent": "admission-fixture",
    "directive": "fixture-directive",
    "runtime": "claude-subagent",
    "role": "claude-subagent",
    "tier": "L0",
    "task_class": "code-author",
    "actor": "claude-subagent",
    "verbs": [],
    "skills": [],
    "secrets": [],
}


def refused_lines(record: dict) -> list[str]:
    return [refusal.line() for refusal in model.admission_refusals({"spawn": record})]


@pytest.fixture
def assemble_with(envelope_fields: dict):
    """Assemble the conftest envelope with the spawn block overridden — one edit, one judge."""

    def _assemble(overrides: dict) -> dict:
        fields = dict(envelope_fields)
        spawn = {**fields.pop("spawn"), **overrides}
        return model.assemble(fields, spawn=spawn)

    return _assemble


# --- judge 1: the actor (#1371) ----------------------------------------------- #


def test_an_unregistered_actor_cannot_open_a_lane() -> None:
    """The acceptance, judge 1: `resolve_actor` fail-closed, by its own name."""
    record = {**ADMISSIBLE, "actor": "definitely-not-declared-anywhere"}

    lines = refused_lines(record)

    assert lines == ["spawn.actor: actor-unresolved:definitely-not-declared-anywhere"], lines


def test_the_actor_judge_runs_at_admission_not_on_request(envelope_fields: dict) -> None:
    """`assemble` refuses an undeclared actor — the producer cannot skip the judge."""
    fields = dict(envelope_fields)
    spawn = {**fields.pop("spawn"), "actor": "nobody-has-ever-declared-this"}

    with pytest.raises(model.EnvelopeRefused) as refused:
        model.assemble(fields, spawn=spawn)

    assert "spawn.actor: actor-unresolved:nobody-has-ever-declared-this" in str(refused.value)


def test_a_declared_actor_is_admitted_and_named_in_the_envelope(envelope_fields: dict) -> None:
    fields = dict(envelope_fields)
    spawn = fields.pop("spawn")

    document = model.assemble(fields, spawn=spawn)

    assert document["spawn"]["actor"] == "claude-subagent"


# --- judge 2: the FinOps tier (#1372) ----------------------------------------- #


def test_a_spawn_at_the_opus_rung_for_an_l0_class_is_refused_by_name() -> None:
    """The acceptance, judge 2: opus is the L2 rung of the ladder tiers.yaml declares."""
    record = {**ADMISSIBLE, "tier": "", "model": "claude-opus-5"}

    lines = refused_lines(record)

    assert len(lines) == 1 and lines[0].startswith("spawn.tier: FINOPS-ROLE-NOT-ALLOWED:"), lines
    assert "L0..L1" in lines[0], lines


def test_a_declared_tier_above_the_class_window_is_refused_by_name() -> None:
    lines = refused_lines({**ADMISSIBLE, "tier": "L2"})

    assert len(lines) == 1 and "FINOPS-ROLE-NOT-ALLOWED" in lines[0], lines


def test_the_security_floor_is_judged_not_the_class_default() -> None:
    """A guarded class may not be spawned below the floor, even at its own default."""
    assert refused_lines({**ADMISSIBLE, "path": "fleet", "role": "fleet",
                          "task_class": "security-review", "tier": "L0"}) != []
    assert refused_lines({**ADMISSIBLE, "path": "fleet", "role": "fleet",
                          "task_class": "security-review", "tier": "L1"}) == []


def test_a_model_that_contradicts_the_tier_beside_it_is_refused_by_name() -> None:
    lines = refused_lines({**ADMISSIBLE, "tier": "L0", "model": "claude-opus-5"})

    assert len(lines) == 1 and lines[0].startswith("spawn.model: FINOPS-MODEL-TIER-CONFLICT:"), lines


def test_a_model_on_no_rung_of_the_ladder_is_refused_by_name() -> None:
    lines = refused_lines({**ADMISSIBLE, "tier": "", "model": "gpt-imaginary-9"})

    assert len(lines) == 1 and lines[0].startswith("spawn.model: FINOPS-UNKNOWN-MODEL:"), lines


def test_an_unreadable_tiers_table_is_a_named_refusal_never_a_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail closed: a judge that cannot read its table refuses, naming that."""

    def unavailable(*_args, **_kwargs):
        raise tiering.TieringUnavailable("gateway/finops/tiers.yaml: unreadable")

    monkeypatch.setattr(tiering, "load_table", unavailable)

    lines = refused_lines(ADMISSIBLE)

    assert len(lines) == 1 and lines[0].startswith("spawn.tier: FINOPS-TIERS-UNAVAILABLE:"), lines


# --- judge 3: the per-runtime allowlists (#1273 / #1377) ---------------------- #


@pytest.mark.parametrize(
    "key, item, expected",
    [
        ("verbs", "closure.close", "verb-not-allowed:deepseek-sister:closure.close"),
        ("skills", "sme-card-authoring", "skill-not-allowed:deepseek-sister:sme-card-authoring"),
        (
            "secrets",
            "projects/example/secrets/agent-x-signing-key",
            "secret-not-allowed:deepseek-sister:projects/example/secrets/agent-x-signing-key",
        ),
    ],
)
def test_a_request_the_runtime_may_not_make_is_refused_by_channel_declared_name(
    key: str, item: str, expected: str
) -> None:
    """One control per table: the refusal is the name `fleet/channel.py` declares."""
    record = {**ADMISSIBLE, "runtime": "deepseek-sister", "actor": "deepseek-sister", key: [item]}

    lines = refused_lines(record)

    assert lines == [f"spawn.{key}: {expected}"], lines


def test_a_runtime_outside_the_closed_set_is_refused_in_channels_own_words() -> None:
    lines = refused_lines({**ADMISSIBLE, "runtime": "rogue-runtime", "actor": "claude-subagent"})

    assert len(lines) == 1 and lines[0].startswith("spawn.runtime: runtime must be one of "), lines


def test_a_request_the_runtime_may_make_is_admitted() -> None:
    """The vacuity guard: the control above refuses the REQUEST, not the runtime."""
    assert refused_lines({**ADMISSIBLE, "verbs": ["closure.close"]}) == []


# --- the record the envelope stores (#1301's four values) --------------------- #


def test_the_admitted_envelope_stores_the_lane_binding_values(assemble_with) -> None:
    """`{runtime, role, tier, actor}` — the four `isolation open` is keyed by (#1301)."""
    document = assemble_with({})

    binding = document["spawn"]

    assert {key: binding[key] for key in ("runtime", "role", "tier", "actor")} == {
        "runtime": "claude-subagent",
        "role": "fleet",
        "tier": "L0",
        "actor": "claude-subagent",
    }
    assert model.validate(document) == []


def test_the_producer_materialises_a_tier_for_a_spawn_that_declares_none() -> None:
    """Silence is resolved to the table's own cheapest capable tier, never left empty."""
    record = admission.spawn_record(path="local", agent="me")
    guarded = admission.spawn_record(path="fleet", agent="me", task_class="security-review")
    by_model = admission.spawn_record(path="local", agent="me", model="claude-opus-5", task_class="research")

    assert record["tier"] == tiering.default_tier("code-author") == "L0"
    assert guarded["tier"] == tiering.default_tier("security-review") == "L1"
    assert by_model["tier"] == tiering.tier_for_model("claude-opus-5") == "L2"
    assert refused_lines(record) == []
    assert refused_lines(guarded) == []


def test_the_producer_reads_the_binding_from_the_environment_the_lane_ran_in() -> None:
    env = {
        "AO_RUNTIME": "hermes",
        "AO_ROLE": "hermes-persona",
        "AO_MODEL_TIER": "L1",
        "AO_TASK_CLASS": "code-review",
        "AO_ACTOR": "hermes",
        "AO_SPAWN_VERBS": "fleet.status",
    }

    record = admission.spawn_record(path="local", agent="me", env=env)

    assert record["runtime"] == "hermes"
    assert record["role"] == "hermes-persona"
    assert record["tier"] == "L1"
    assert record["task_class"] == "code-review"
    assert record["actor"] == "hermes"
    assert record["verbs"] == ["fleet.status"]
    assert refused_lines(record) == []


def test_the_rendered_block_names_the_admission_the_spawn_was_granted(assemble_with) -> None:
    from governance.spawn import render

    block = render.render(assemble_with({}))

    assert "admission" in block
    assert "runtime=claude-subagent" in block
    assert "tier=L0" in block


# --- the ordering the acceptance asks for: no worktree for a refused spawn ---- #


def now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def scratch_board(root: Path, issue: int = 1413, lane: str = "admission", agent: str = "admission-fixture") -> Path:
    """A root with its OWN board: focus, snapshot and a REAL claim event.

    Written with the claim ledger's own writer, so a claim attempted by the CLI
    would genuinely succeed here — which is what makes "the ledger did not move"
    an assertion about the ORDER admission runs in, not about the claim failing.
    """
    import claims as claim_ledger

    board = root / ".board"
    board.mkdir(parents=True, exist_ok=True)
    (board / "focus.json").write_text(
        json.dumps({"active_epic": 1268, "activated_at": now_stamp(), "wave_cap": 12, "max_agents": 0, "pooled": []}),
        encoding="utf-8",
    )
    (board / "snapshot.json").write_text(
        json.dumps(
            {
                "generated_at": now_stamp(),
                "source": "test-admission",
                "issues": [
                    {
                        "number": issue,
                        "title": "admission fixture",
                        "state": "OPEN",
                        "milestone": "",
                        "labels": [],
                        "parent": 1268,
                        "blocked_by": [],
                        "cross_refs": [],
                        "closed_at": None,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    claim_ledger.append_event(
        claim_ledger.ClaimEvent(event="claim", issue=issue, agent=agent, at=now_stamp(), lane=lane),
        board / "claims",
    )
    return board


def session_env(issue: int = 1413, lane: str = "admission", agent: str = "admission-fixture") -> dict[str, str]:
    result = subprocess.run(
        [
            sys.executable, str(REPO_ROOT / "governance" / "isolation" / "cli.py"), "env",
            "--issue", str(issue), "--agent", agent, "--lane", lane, "--json",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return dict(json.loads(result.stdout))


def run_cli(args: list[str], root: Path, env: dict[str, str]):
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "governance" / "spawn" / "cli.py"), *args],
        cwd=str(root),
        capture_output=True,
        text=True,
        env={**os.environ, **env},
    )


def ledger_events(root: Path) -> int:
    import claims as claim_ledger

    return len(claim_ledger.read_ledger(root / ".board" / "claims"))


def test_a_refused_spawn_leaves_no_worktree_and_no_claim(tmp_path: Path) -> None:
    """The acceptance's ordering: refused BEFORE any worktree exists, at the CLI.

    Minting is ENABLED here (no `--no-mint`) and pointed at an empty root, so if
    admission ran after the mint a lane directory would appear in it. The paired
    admitted run below shows the mint IS attempted when admission passes, which is
    what makes the absence here an ordering proof rather than a disabled feature.
    """
    root = tmp_path / "root"
    mint_root = tmp_path / "worktrees"
    mint_root.mkdir(parents=True)
    scratch_board(root)
    env = session_env()
    before = ledger_events(root)

    refused = run_cli(
        ["open", "--issue", "1413", "--lane", "admission", "--agent", "admission-fixture",
         "--root", str(root), "--worktree-root", str(mint_root),
         "--class", "code-author", "--model", "claude-opus-5"],
        root,
        env,
    )

    assert refused.returncode == model.EXIT_REFUSED, refused.stderr
    assert "FINOPS-ROLE-NOT-ALLOWED" in refused.stderr, refused.stderr
    assert "lane not minted" not in refused.stderr, refused.stderr
    assert "no lane was provisioned" in refused.stderr, refused.stderr
    assert list(mint_root.iterdir()) == [], list(mint_root.iterdir())
    assert ledger_events(root) == before, "admission did not run before the claim"


def test_the_same_spawn_is_admitted_so_the_ordering_control_is_not_vacuous(tmp_path: Path) -> None:
    """Admitted with the same flags minus the forbidden model — and the mint runs."""
    root = tmp_path / "root"
    mint_root = tmp_path / "worktrees"
    mint_root.mkdir(parents=True)
    scratch_board(root)
    env = session_env()

    admitted = run_cli(
        ["open", "--issue", "1413", "--lane", "admission", "--agent", "admission-fixture",
         "--root", str(root), "--worktree-root", str(mint_root),
         "--class", "code-author"],
        root,
        env,
    )

    assert "lane not minted" in admitted.stderr, admitted.stderr
    assert "spawn: refused at the admission point" not in admitted.stderr, admitted.stderr


def test_an_undeclared_actor_is_refused_at_the_cli_before_the_claim(tmp_path: Path) -> None:
    root = tmp_path / "root"
    mint_root = tmp_path / "worktrees"
    mint_root.mkdir(parents=True)
    scratch_board(root)
    env = session_env()
    before = ledger_events(root)

    refused = run_cli(
        ["open", "--issue", "1413", "--lane", "admission", "--agent", "admission-fixture",
         "--root", str(root), "--worktree-root", str(mint_root),
         "--actor", "definitely-not-declared-anywhere"],
        root,
        env,
    )

    assert refused.returncode == model.EXIT_REFUSED, refused.stderr
    assert "actor-unresolved:definitely-not-declared-anywhere" in refused.stderr, refused.stderr
    assert list(mint_root.iterdir()) == []
    assert ledger_events(root) == before


def test_a_verb_the_runtime_may_not_call_is_refused_at_the_cli(tmp_path: Path) -> None:
    root = tmp_path / "root"
    mint_root = tmp_path / "worktrees"
    mint_root.mkdir(parents=True)
    scratch_board(root)
    env = session_env()

    refused = run_cli(
        ["open", "--issue", "1413", "--lane", "admission", "--agent", "admission-fixture",
         "--root", str(root), "--worktree-root", str(mint_root),
         "--runtime", "deepseek-sister", "--actor", "deepseek-sister", "--verb", "closure.close"],
        root,
        env,
    )

    assert refused.returncode == model.EXIT_REFUSED, refused.stderr
    assert "verb-not-allowed:deepseek-sister:closure.close" in refused.stderr, refused.stderr
    assert list(mint_root.iterdir()) == []
