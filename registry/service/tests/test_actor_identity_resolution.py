"""One identity per actor across GitHub, the mailbox, paperclip and hermes
(issue #1275).

``resolve_actor`` resolves any actor string (a PR author, a directive sender,
an approval signer, a heartbeat source) to exactly one declared identity
record in ``registry/service/actors.yaml``, or fails closed.
"""

from __future__ import annotations

import os

import pytest
import yaml

from service import ActorUnresolvedError, DelegationUndeclaredError, resolve_actor
from service.identity import _ACTORS_YAML


def test_declared_actor_resolves():
    record = resolve_actor("kushin77")
    assert record["id"] == "kushin77"
    assert record["kind"] == "human"


def test_declared_runtime_ids_resolve():
    for actor in [
        "claude-session",
        "claude-subagent",
        "deepseek-sister",
        "deepseek-executor",
        "copilot-agent",
        "hermes",
        "paperclip",
        "github-actions[bot]",
    ]:
        record = resolve_actor(actor)
        assert record["id"] == actor


def test_unknown_actor_string_is_unresolved():
    with pytest.raises(ActorUnresolvedError) as exc:
        resolve_actor("totally-unknown-actor")
    assert str(exc.value) == "actor-unresolved:totally-unknown-actor"


def test_declared_delegation_chain_resolves():
    record = resolve_actor(
        "cloud-build-purebliss-api@example.iam.gserviceaccount.com"
    )
    assert record["delegatesTo"] == "tf-runner-purebliss-api"
    # the delegate target must itself resolve
    target = resolve_actor(record["delegatesTo"])
    assert target["id"] == "tf-runner-purebliss-api"


def test_undeclared_delegation_is_refused(tmp_path):
    """Strip the delegate target from a scratch copy -> delegation-undeclared."""
    with open(_ACTORS_YAML, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    del data["identities"]["tf-runner-purebliss-api"]
    scratch = tmp_path / "actors.yaml"
    scratch.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    with pytest.raises(DelegationUndeclaredError) as exc:
        resolve_actor(
            "cloud-build-purebliss-api@example.iam.gserviceaccount.com",
            actors_path=str(scratch),
        )
    assert str(exc.value) == (
        "delegation-undeclared:"
        "cloud-build-purebliss-api@example.iam.gserviceaccount.com"
        "->tf-runner-purebliss-api"
    )


def test_real_actors_file_is_used_by_default():
    assert os.path.basename(_ACTORS_YAML) == "actors.yaml"
    assert os.path.isfile(_ACTORS_YAML)
