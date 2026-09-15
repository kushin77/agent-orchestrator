"""The envelope, rendered as the prompt block a subagent is spawned with.

This is the ONE copy of the spawn prose. Before #793 it lived inline in
`fleet/terminal.py::build_prompt`, where nothing could check that a spawn had
carried it: the governance guarantee was "somebody remembered to write it into
the prompt string", and the local path carried none of it. Now the prose is
produced from a validated document, both spawn paths render the SAME document
through this function, and `fleet/terminal.py` holds no second copy.

**Rendered deterministically, on purpose.** Everything here is a pure function
of the envelope: no timestamps, no measured headroom. Two identical spawns must
produce byte-identical blocks, or `scripts/check-spawn-envelope.sh` could not
prove that the prompt a spawn carries IS this document's rendering — it would
only be able to count matching lines. The measurement that justified the
admission (`capacity.effective`, its bounds) stays in the document for an
auditor; the block carries the permit the lane will actually take, which is what
the subagent must act on.
"""

from __future__ import annotations

from typing import Any, Mapping

#: The standing mandate, used when `fleet/directive.json` cannot be read. Kept
#: verbatim-compatible with the body `fleet/terminal.py` used to inline, so the
#: order a subagent reads does not change just because its source did.
DEFAULT_STANDING = (
    "LIVE CI/CD SDLC: run the issue's own `Verify:` command AND `make verify`; their REAL output "
    "is the only evidence; one issue = one lane = one self-contained green commit. "
    "REPLACEABILITY: the brain may replace you or frontload new instructions at any moment — "
    "execute ONLY this directive, keep every fact that matters in artifacts (issue, branch, "
    "claim ledger, board), and leave every artifact terminal."
)

#: The line a subagent (and a gate) can search for to know a prompt carries an
#: envelope rather than prose that merely looks like one.
MARKER = "SPAWN ENVELOPE"


def _text(document: Mapping[str, Any], *path: str, default: str = "") -> str:
    """Read a dotted path out of the document; '' when absent, never a crash."""
    value: Any = document
    for key in path:
        if not isinstance(value, Mapping):
            return default
        value = value.get(key)
    if value is None:
        return default
    return value if isinstance(value, str) else str(value)


def standing_block(document: Mapping[str, Any], standing: str = "") -> str:
    """The standing mandate, frontloaded ahead of the order itself.

    A subagent that reads only the first lines must still know that it is gated
    (real `Verify:` + `make verify` output is the evidence), that its commit must
    be atomic and green, and that it may be replaced at any moment — so it
    executes only this directive, keeps every fact it needs in artifacts, and
    leaves every artifact terminal.
    """
    issue = document.get("issue")
    verify = _text(document, "verify", "command") or _text(document, "gate", "of_record")
    return (
        "STANDING MANDATE — LIVE CI/CD SDLC + REPLACEABILITY (read this first; it is the operator's "
        "standing order as carried in fleet/directive.json):\n"
        f"{standing or DEFAULT_STANDING}\n"
        "What that means for THIS run, in order:\n"
        f"a. GATE OF RECORD: run issue #{issue}'s own `Verify:` clause ({verify}) AND `make verify`, "
        "and quote their REAL output. Unverified work is not done, and neither command may be "
        "skipped or reported from memory.\n"
        "b. ATOMIC + GREEN: one issue = one lane = one self-contained, green, reversible commit; a "
        "perfected (green-verified) commit reaches production on merge through the declared apply "
        "pipeline — never a hand-carried deploy and never a console click.\n"
        "c. NEVER MERGE FAILING WORK and never add a GitHub Actions workflow (GR-15 — automation is "
        "code-native `make` targets run by the ops runner and cron).\n"
        "d. REPLACEABLE, NOT AUTHORITATIVE: the brain may replace you or frontload new instructions "
        "at any moment. Execute ONLY this directive — do not self-escalate scope, pick your own "
        "issue, or re-plan the board.\n"
        "e. STATE LIVES IN ARTIFACTS: anything a replacement needs must live in the issue, the "
        "branch, the claim ledger or the board — never only in your context.\n"
        "f. LEAVE EVERY ARTIFACT TERMINAL: PR merged at green evidence, source branch deleted, claim "
        "released, directive consumed, issue closed with evidence. Strand nothing.\n\n"
    )


def envelope_block(document: Mapping[str, Any]) -> str:
    """The envelope as the subagent reads it: identity, ownership, bounds, evidence.

    Every line is derived from the document, so an envelope that would have been
    refused is never rendered — `governance/spawn/model.py` refuses it first, and
    a caller that reaches this function is holding an admitted envelope.
    """
    issue = document.get("issue")
    permit = document.get("capacity", {}).get("permit", {}) if isinstance(document.get("capacity"), Mapping) else {}
    budget = document.get("budget") if isinstance(document.get("budget"), Mapping) else {}
    focus = document.get("focus") if isinstance(document.get("focus"), Mapping) else {}
    claim = document.get("claim") if isinstance(document.get("claim"), Mapping) else {}
    rows = [
        ("issue", f"#{issue}"),
        ("lane", _text(document, "lane")),
        ("epic", f"#{focus.get('epic')} ({focus.get('source')}; pinned #{focus.get('pinned_epic')})"),
        ("session", _text(document, "session", "id")),
        ("agent", _text(document, "session", "agent")),
        ("branch", _text(document, "session", "branch")),
        ("worktree", _text(document, "worktree")),
        ("trailer", f"'{_text(document, 'trailer')}' on EVERY commit (the lane audit checks each one)"),
        ("claim", f"{claim.get('owner')} ({claim.get('state')}, lane {claim.get('lane') or 'n/a'}) — "
                  "the loop owns it: do NOT run claim or release"),
        ("gate", f"{_text(document, 'gate', 'of_record')} — {_text(document, 'gate', 'bound')}"),
        ("permit", f"store={permit.get('store')} worktree_key={permit.get('worktree_key')} "
                   f"lock={permit.get('lock')} cap={permit.get('max_concurrent')}"),
        ("budget", f"attempt {budget.get('attempts')}/{budget.get('cap')} ({budget.get('state')})"
                   + (f", next attempt at {budget.get('next_attempt_at')}" if budget.get("next_attempt_at") else "")),
        ("verify", _text(document, "verify", "command")),
    ]
    width = max(len(name) for name, _ in rows)
    body = "\n".join(f"  {name.ljust(width)}  {value}" for name, value in rows)
    return (
        f"{MARKER} {document.get('schema')} (produced by {document.get('producer')}; this same "
        "document governs a fleet spawn and a locally spawned subagent):\n"
        f"{body}\n"
    )


def render(document: Mapping[str, Any], standing: str = "") -> str:
    """The envelope block: the mandate, then the document as the order reads it."""
    return f"{standing_block(document, standing)}{envelope_block(document)}"


def positioning(document: Mapping[str, Any]) -> str:
    """Where to work, and under what identity — the two lines a spawn cannot infer.

    Kept separate from `render` so a caller composing a longer prompt (the fleet
    loop, which appends the directive body and its context pack) can place the
    order between the mandate and this positioning without duplicating prose.
    """
    worktree = _text(document, "worktree")
    branch = _text(document, "session", "branch") or worktree.rsplit("/", 1)[-1]
    session = _text(document, "session", "id")
    agent = _text(document, "session", "agent")
    issue = document.get("issue")
    where = (
        f"Your worktree is {worktree} (branch {branch}). Work ONLY there — never in the "
        "shared checkout, which other lanes are using.\n"
        if worktree
        else "Work in the repo checkout; disk worktrees under ~/ao-worktrees, never /tmp.\n"
    )
    who = (
        f"Your session identity is {session} (agent {agent}) on branch {branch}. Every commit you "
        f"author MUST carry '{_text(document, 'trailer')}' — the lane audit checks each "
        "commit, and one that omits it stays a violation even after a later good commit.\n"
        if session
        else ""
    )
    return f"{where}{who}"


def instructions(document: Mapping[str, Any]) -> str:
    """What the subagent does, in order — the tail of the prompt.

    Here rather than in the caller because it is the SAME order for a local spawn
    and a fleet spawn, and a second copy of it is exactly how the two regimes
    drifted apart (#793). The claim/close-out wording is the change that matters:
    the claim is owned by whoever spawned the agent, so the agent must not take or
    release one, in either regime.
    """
    issue = document.get("issue")
    agent = _text(document, "session", "agent")
    lane = _text(document, "lane")
    return (
        "Do exactly this, nothing else:\n"
        f"1. Issue #{issue} is ALREADY CLAIMED for you as `{agent}` (lane {lane or 'n/a'}) — do NOT "
        "run claim and do NOT run release; the spawner manages the claim around your run.\n"
        f"2. If a PR for issue #{issue} ALREADY exists, do NOT bail out: check out its branch, run "
        "`make verify`, and if it is green squash-merge the PR and close the issue. Only implement "
        "from scratch if no PR exists.\n"
        f"3. Implement issue #{issue} to completion; open a PR whose body carries 'Closes #{issue}' and "
        "the ACTUAL 'make verify' output as evidence.\n"
        "4. After `make verify` is green and the PR is open, squash-merge it with "
        "`gh pr merge <number> --squash --delete-branch`, then close the issue. "
        "NEVER leave a completed PR unmerged or the issue open.\n"
        "5. Leave every artifact terminal: the lifecycle close-out "
        "(`governance/lifecycle`) then runs and its verdict travels with your report, so a "
        "surviving branch, a wedged claim, an unconsumed directive or a lane left "
        "behind is reported as NOT-OK rather than passing as done.\n"
        "Return a short report: PR number, verify output summary, files touched, AND the merge result "
        "(PR number + merged/closed). If anything fails, report the exact error instead of improvising."
    )


def prompt(
    document: Mapping[str, Any],
    *,
    standing: str = "",
    directive_body: str = "",
    directive_id: str = "",
    tier: str = "flash",
    thinking: str = "none",
    context_block: str = "",
) -> str:
    """The complete subagent prompt for an envelope — the ONE copy of this prose.

    `fleet/terminal.py::build_prompt` passes its directive and context pack here
    instead of assembling governance sentences itself; a local spawn renders the
    same document the same way, so the two regimes cannot drift.
    """
    order = (
        f"BRAIN DIRECTIVE {directive_id} — model {tier}/{thinking}:\n{directive_body}\n\n"
        if directive_body or directive_id
        else ""
    )
    return (
        f"{render(document, standing)}"
        "You are an epic-focused subagent in the kushin77/agent-orchestrator fleet, "
        "steered by the brain through the sister session. "
        f"{positioning(document)}\n"
        f"{order}"
        f"{context_block}"
        f"{instructions(document)}"
    )
