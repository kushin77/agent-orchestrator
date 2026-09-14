"""Purebliss-team pin reachability (issue #337).

Invariant: every agent pinned in a routing group is reachable end-to-end for at
least one routed task type whose capability the persona actually holds — the
provider is called, not denied at the capability boundary.

Before #337 the policy routed only ``classify-route`` -> ``orchestrate``,
``code-review-verdict`` -> ``code-review`` and ``summarize`` -> ``research``,
while the ``hermes`` persona holds code-author / test-author / test-run /
memory-ops and ``ollama`` holds code-author / test-run / memory-ops. No route
referenced a capability either persona held, so both pins were unreachable:
every dispatch was ``denied`` at the capability boundary and no provider was
ever called.

This suite proves BOTH directions through the real offline funnel
(``e2e.wiring.build_team_gateway``):

- ``hermes`` and ``ollama`` reach their pinned provider for the ``test-run`` /
  ``code-author`` capability routes #337 added, and
- the least-privilege boundary is NOT widened: both personas are still
  ``denied`` for the task types whose capabilities they do not hold.
"""

from __future__ import annotations

import json

import pytest

from e2e.wiring import (
    PROVIDER_HERMES,
    PROVIDER_OLLAMA,
    build_team_gateway,
)
from proxy import contract
from proxy.model import TaskRequest

TEST_RUN_OK = json.dumps(
    {
        "status": "pass",
        "checks": [{"name": "make verify", "result": "pass", "detail": "rc=0"}],
        "evidence": "make verify -> 25 checks PASS",
    }
)
CODE_AUTHOR_OK = json.dumps(
    {
        "files": [{"path": "gateway/proxy/config/routing.yaml", "purpose": "add route"}],
        "diffSummary": "Routes the code-author capability so hermes/ollama are reachable.",
        "testsAdded": ["e2e/tests/test_hermes_ollama_reachability.py"],
    }
)

# agent -> provider the routing group pins it to (routing.yaml purebliss-team).
PINNED = {"hermes": PROVIDER_HERMES, "ollama": PROVIDER_OLLAMA}

# The capability routes #337 added: a task type whose capability each persona holds.
REACHABLE = [
    ("hermes", "test-run", TEST_RUN_OK),
    ("hermes", "code-author", CODE_AUTHOR_OK),
    ("ollama", "test-run", TEST_RUN_OK),
    ("ollama", "code-author", CODE_AUTHOR_OK),
]

# Task types whose capabilities neither persona holds — must stay denied.
NOT_HELD = [
    ("hermes", "summarize"),          # requires research
    ("hermes", "code-review-verdict"),  # requires code-review
    ("ollama", "summarize"),          # requires research
    ("ollama", "classify-route"),     # requires orchestrate
]


def _dispatch(wired, agent_id, task_type):
    # The issue's repro supplies every rendered body's variables in one dict.
    return wired.gateway.dispatch(
        agent_id,
        TaskRequest(
            tenant_id="acme",
            task_type=task_type,
            input={
                "input": f"{task_type} for {agent_id}",
                "thread": "x",
                "diff": "d",
                "context": "c",
            },
        ),
    )


# --------------------------------------------------------------------------- #
# the fix: the two pins are reachable end-to-end (provider actually called)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("agent,task_type,canned", REACHABLE)
def test_pinned_agent_reaches_its_provider_for_a_held_capability(
    agent, task_type, canned
):
    """hermes/ollama dispatch a capability they hold -> routed, not denied."""
    wired = build_team_gateway()
    provider = PINNED[agent]
    wired.rig.script_success(provider, canned)

    result = _dispatch(wired, agent, task_type)

    assert result.outcome == contract.OUTCOME_SUCCESS
    assert result.served()
    assert result.provider == provider
    assert result.record.agent_id == agent
    assert result.record.capability == task_type
    assert result.record.task_class == task_type


# --------------------------------------------------------------------------- #
# no least-privilege widening: the pre-fix denials are still denials
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("agent,task_type", NOT_HELD)
def test_persona_boundary_is_not_widened(agent, task_type):
    """A capability the persona does not hold stays denied (no widening)."""
    wired = build_team_gateway()
    result = _dispatch(wired, agent, task_type)

    assert result.outcome == contract.OUTCOME_DENIED
    assert not result.served()
