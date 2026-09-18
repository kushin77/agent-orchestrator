"""The identity projection: both halves of the minted signature reach the envelope (#918).

Issue #918 is the threading asymmetry that only ``GIT_AUTHOR_*`` reached the
envelope while ``governance/isolation`` had ALREADY exported ``GIT_COMMITTER_*``
too — so an envelope audit could prove authorship but not that the committer
half of the session identity ever reached the child. `session_field` is the ONE
reader of that identity; these tests pin that it projects all four variables,
that the committer half is read from the COMMITTER variables (never rounded to
the author), and that a missing half is reported empty — so the model refuses it
by name rather than `session_field` inventing a value.
"""

from __future__ import annotations

from governance.spawn import sources


def test_session_field_projects_all_four_git_identity_variables() -> None:
    env = {
        "AO_SESSION_ID": "s1",
        "AO_ISSUE": "918",
        "AO_AGENT_ID": "copilot-relentless",
        "AO_LANE": "spawn-envelope",
        "AO_BRANCH": "issue-918",
        "AO_WORKTREE": "/tmp/wt",
        "AO_REPO_SLUG": "kushin77/agent-orchestrator",
        "GIT_AUTHOR_NAME": "agent-copilot-relentless",
        "GIT_AUTHOR_EMAIL": "agent+copilot-relentless@agents.invalid",
        "GIT_COMMITTER_NAME": "agent-copilot-relentless",
        "GIT_COMMITTER_EMAIL": "agent+copilot-relentless@agents.invalid",
    }

    session = sources.session_field(env)

    assert session["author_name"] == "agent-copilot-relentless"
    assert session["author_email"] == "agent+copilot-relentless@agents.invalid"
    assert session["committer_name"] == "agent-copilot-relentless"
    assert session["committer_email"] == "agent+copilot-relentless@agents.invalid"
    assert session["committer_name"] == session["author_name"]
    assert session["committer_email"] == session["author_email"]


def test_session_field_reads_the_committer_from_the_committer_variables_not_the_author() -> None:
    """A mint that stamped a distinct committer must be projected, not rounded to the author."""
    env = {
        "GIT_AUTHOR_NAME": "agent-a",
        "GIT_AUTHOR_EMAIL": "agent+a@agents.invalid",
        "GIT_COMMITTER_NAME": "agent-b",
        "GIT_COMMITTER_EMAIL": "agent+b@agents.invalid",
    }

    session = sources.session_field(env)

    assert session["author_name"] == "agent-a"
    assert session["committer_name"] == "agent-b"
    assert session["committer_email"] == "agent+b@agents.invalid"


def test_an_absent_committer_variable_projects_an_empty_field_the_model_refuses() -> None:
    """Nothing is invented: a missing committer half is reported empty, not guessed."""
    session = sources.session_field({})

    assert session["committer_name"] == ""
    assert session["committer_email"] == ""
