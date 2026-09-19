"""ONE runtime list, proved by mutation (issue #1412, from #1385).

THE FAILURE THIS PINS
---------------------
`fleet/runtimes.yaml` declared SEVEN runtime ids, and `governance/notices/`
carried FIVE of its own (`claude`, `deepseek`, `hermes`, `ollama`, `paperclip`),
derived from a different pair of declarations. #1385 measured the mismatch and left
it to the owner: "settle the runtime vocabulary ... every lane record's `runtime`
validates against whichever wins". Two lists that AGREE today are not one list, and
these two did not even agree: `copilot-agent` owed no ack and `ollama`, which no
contract row registers, did.

HOW THIS PROVES ONE LIST RATHER THAN COMPARING TWO
--------------------------------------------------
Not by asserting that two literals are equal -- that passes for two lists that
happen to match, which is exactly the state that failed. The contract is MUTATED on
a fixture tree (an id is renamed, one added, one removed) and every consumer is
re-read on that tree: a consumer that carries its own list cannot follow, so the
mutation is what makes the assertion able to fail. `test_the_pin_can_fail` drives
the same comparison with a consumer that reports a stale list, so the comparison's
own failure path is shown to exist (GR-12: a check that cannot fail is a formality).

The consumers, and where each reads the vocabulary from now:

| consumer | before | now |
|---|---|---|
| `fleet/channel.py` | a literal seven-tuple | `fleet.runtimes.ids()` |
| `governance/notices/runtime_registry.py` | its own five-id derivation | the contract |
| `governance/isolation/runtimes.py` | the contract, with a literal fallback | the contract (unchanged) |
| `integrations/paperclip/adapters/heartbeat/beat.py` | the contract (unchanged) | the contract |
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

import channel
import runtimes

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

CONTRACT = REPO_ROOT / "fleet" / "runtimes.yaml"


def _mutated_contract(original: str, *, rename: str, add: str, drop: str) -> str:
    """The contract with one id renamed, one added and one dropped.

    A rename is the mutation an equality check cannot survive: the id list changes
    in every position, so a consumer holding its own copy reports the old name.
    """
    lines = original.splitlines()
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(f"- id: {drop}"):
            continue
        if stripped.startswith(f"- id: {rename}"):
            out.append(line.replace(f"- id: {rename}", f"- id: {renamed}"))
            continue
        out.append(line)
    out.append("")
    out.append(f"  - id: {add}")
    out.append("    kind: agent")
    out.append("    transport: cli")
    out.append("    identity: probe")
    out.append("    token_scope: probe")
    return "\n".join(out) + "\n"


renamed = "claude-session-renamed"


@pytest.fixture()
def mutated(tmp_path: Path) -> tuple[Path, tuple[str, ...]]:
    """A fixture tree whose contract differs from the real one, with its id list."""
    fx = tmp_path / "mutated-tree"
    (fx / "fleet").mkdir(parents=True)
    (fx / "fleet" / "runtimes.yaml").write_text(
        _mutated_contract(CONTRACT.read_text(encoding="utf-8"), rename="claude-session", add="probe-runtime", drop="paperclip"),
        encoding="utf-8",
    )
    # The consumers below reach for the tree's own inputs: the gateway catalog the
    # notices rule reads a provider from (empty here, so every row keeps its own
    # identity as the provider) and the mailbox its fanout names.
    (fx / "gateway" / "catalog" / "modules").mkdir(parents=True)
    (fx / "fleet" / "channel.py").write_text("# the mailbox\n", encoding="utf-8")
    ids = runtimes.ids(fx)
    assert ids != runtimes.ids(REPO_ROOT), "the fixture contract must differ from the real one"
    assert renamed in ids and "probe-runtime" in ids and "paperclip" not in ids
    return fx, ids


def consumers(fx: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, tuple[str, ...]]:
    """Every consumer's answer, read against `fx` rather than the real tree."""
    from governance.isolation import runtimes as isolation_runtimes
    from governance.notices import runtime_registry as notices_registry
    from integrations.paperclip.adapters.heartbeat import beat

    monkeypatch.setattr(channel, "ROOT", fx)
    monkeypatch.setattr(channel, "_RUNTIME_IDS", None)
    return {
        "fleet.runtimes.ids": runtimes.ids(fx),
        "fleet.channel.runtime_ids": tuple(channel.runtime_ids()),
        "governance.notices.registered_runtimes": tuple(
            runtime.id for runtime in notices_registry.registered_runtimes(fx)
        ),
        "governance.isolation.runtimes": tuple(isolation_runtimes.runtime_ids(fx)),
        "integrations.paperclip.adapters.heartbeat.load_registry": tuple(
            sorted(beat.load_registry(fx))
        ),
    }


def disagreements(observed: dict[str, tuple[str, ...]], expected: tuple[str, ...]) -> list[str]:
    """The consumers whose list is not the contract's, by name (empty is the pass)."""
    return [
        name
        for name, ids in observed.items()
        if tuple(ids) != expected and tuple(sorted(ids)) != tuple(sorted(expected))
    ]


def test_every_consumer_follows_the_mutated_contract(
    mutated: tuple[Path, tuple[str, ...]], monkeypatch: pytest.MonkeyPatch
) -> None:
    fx, ids = mutated
    observed = consumers(fx, monkeypatch)
    assert disagreements(observed, ids) == [], (
        "a consumer still carries its own runtime list, so the vocabulary is not one list: "
        f"{json.dumps({k: list(v) for k, v in observed.items()}, indent=2)}"
    )


def test_the_contract_is_validated_rather_than_trusted(tmp_path: Path) -> None:
    """A registry that cannot be READ is refused, never treated as empty."""
    bare = tmp_path / "bare"
    (bare / "fleet").mkdir(parents=True)
    with pytest.raises(runtimes.RegistryRefused):
        runtimes.ids(bare)
    (bare / "fleet" / "runtimes.yaml").write_text("runtimes: []\n", encoding="utf-8")
    with pytest.raises(runtimes.RegistryRefused, match="empty"):
        runtimes.ids(bare)
    (bare / "fleet" / "runtimes.yaml").write_text(
        "runtimes:\n  - id: a\n    kind: agent\n    transport: cli\n  - id: a\n    kind: agent\n    transport: cli\n",
        encoding="utf-8",
    )
    with pytest.raises(runtimes.RegistryRefused, match="more than once"):
        runtimes.ids(bare)
    (bare / "fleet" / "runtimes.yaml").write_text(
        "runtimes:\n  - id: a\n    kind: daemon\n    transport: cli\n", encoding="utf-8"
    )
    with pytest.raises(runtimes.RegistryRefused, match="closed vocabulary"):
        runtimes.ids(bare)


def test_the_pin_can_fail(
    mutated: tuple[Path, tuple[str, ...]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Negative control: a consumer reporting a STALE list must be named."""
    fx, ids = mutated
    observed = consumers(fx, monkeypatch)
    observed["fleet.channel.runtime_ids"] = runtimes.ids(REPO_ROOT)  # the pre-#1412 tuple
    assert disagreements(observed, ids) == ["fleet.channel.runtime_ids"], (
        "the comparison cannot detect a second list, so the pin above proves nothing"
    )


def test_the_real_tree_answers_with_the_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    """The live tree, unmutated: every consumer answers with the contract's seven."""
    ids = runtimes.ids(REPO_ROOT)
    assert len(ids) == 7
    assert disagreements(consumers(REPO_ROOT, monkeypatch), ids) == []
