"""Tiered try-loop dispatcher (L0 -> L1 -> L2 escalation), issue #1524.

Reads an issue's ``tier:L0``/``tier:L1``/``tier:L2`` label, dispatches the work
to the matching model, runs the issue's own ``## Acceptance`` commands, and on
repeated failure applies ``escalate:L1``/``escalate:L2`` and re-dispatches one
tier up — every attempt recorded in the dispatch audit trail
(``.board/dispatch-audit.jsonl``, the ``ao.dispatch/audit-v1`` ledger the
``audit`` module already owns — no third ledger format is created).

The tier -> model mapping is a config value (``tier-policy.json``), not a
hardcoded choice: the provider (claude vs deepseek) and the model ids can change
without a code edit. The tier *definitions* stay in
``vendor/CMR/docs/MODEL-PROFILES.md``; this module only maps a tier label to the
concrete invocation model id.

Dispatch invocation reuses the fleet's own mechanism (``claude -p --model <id>``
for both Claude aliases and the DeepSeek BYOK ids — see ``fleet/terminal.py`` /
``fleet/runners.py``), never a new runner. The model invocation is injectable so
the deterministic fixture (and the dry-run) can drive the loop without a live
model.

The model invocation, the acceptance-runner, the label applier and the commenter
are all injectable hooks: the CLI wires the real ``gh``/subprocess
implementations, the test suite injects fakes. Everything that talks to the
network or the host is a hook; everything that decides (tier reading, model
resolution, the escalation loop, the ledger write) is a pure function.

---knowledge---
module_id: governance.dispatch.tiered
system: governance
app: dispatch
solution_class: enterprise
patterns: [dependency-injection, pure-function-core]
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [TieredRefusal, load_policy, read_tier, resolve_model, parse_acceptance_commands, run_commands, max_attempts_from, escalate_label_for]
invariants: "everything that talks to the network or host is an injected hook; everything that decides is a pure function"
gotchas: ""
related: ["#1524"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Iterable

_PKG_DIR = Path(__file__).resolve().parent
if str(_PKG_DIR) not in sys.path:
    sys.path.insert(0, str(_PKG_DIR))

import audit  # noqa: E402
import claims as claims_mod  # noqa: E402
import peers  # noqa: E402
import snapshot as snapshot_mod  # noqa: E402
from model import FileClaim  # noqa: E402

DEFAULT_POLICY_PATH = _PKG_DIR / "tier-policy.json"

TIERS = ("L0", "L1", "L2")
TIER_LABEL_PREFIX = "tier:"
DEFAULT_TIER = "L0"


class TieredRefusal(Exception):
    """The dispatcher refuses to run — reason is the machine-readable code."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


def load_policy(path: Path | str | None = None) -> dict[str, Any]:
    """Load the tier-policy mapping (tiers, default provider, max attempts)."""
    target = Path(path) if path else DEFAULT_POLICY_PATH
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TieredRefusal("tier-policy-unreadable", f"{target}: {exc}") from exc
    for field in ("tiers", "default_provider", "max_attempts_per_tier"):
        if field not in data:
            raise TieredRefusal("tier-policy-incomplete", f"{target}: missing {field!r}")
    tiers = data["tiers"]
    for tier in TIERS:
        if tier not in tiers or "claude" not in tiers[tier] or "deepseek" not in tiers[tier]:
            raise TieredRefusal("tier-policy-incomplete", f"{target}: tier {tier!r} has no model mapping")
    return data


def read_tier(labels: Iterable[str]) -> tuple[str, str | None]:
    """Extract the ``tier:L0/L1/L2`` label; default ``L0`` with a warning when absent.

    Returns ``(tier, warning)`` where ``warning`` is ``None`` when a tier label
    is present, else a human-readable string naming the default that was applied.
    A ``tier:*`` label carrying an unknown tier is not silently ignored — it is
    refused, so a typo cannot downgrade a task to L0.
    """
    seen: str | None = None
    for label in labels:
        if label.startswith(TIER_LABEL_PREFIX):
            candidate = label[len(TIER_LABEL_PREFIX):]
            if candidate in TIERS:
                seen = candidate
            else:
                raise TieredRefusal("tier-label-unknown", f"label {label!r} is not one of {TIERS}")
    if seen is not None:
        return seen, None
    return DEFAULT_TIER, "no tier:* label present — defaulting to tier:L0"


def resolve_model(tier: str, provider: str | None = None, policy: dict[str, Any] | None = None) -> tuple[str, str]:
    """Resolve ``(provider, model_id)`` for a tier from the policy.

    Returns ``(provider, model_id)``. The provider defaults to the policy's
    ``default_provider`` (``deepseek``) and must be one the policy declares.
    """
    data = policy if policy is not None else load_policy()
    chosen = provider or data["default_provider"]
    tiers = data["tiers"]
    if tier not in tiers:
        raise TieredRefusal("tier-unknown", f"{tier!r} is not one of {TIERS}")
    mapping = tiers[tier]
    if chosen not in mapping:
        raise TieredRefusal(
            "provider-unknown", f"provider {chosen!r} is not declared for {tier!r} ({sorted(mapping)})"
        )
    return chosen, mapping[chosen]


def _acceptance_section(body: str) -> str:
    """The text of the ``## Acceptance`` section, or ``""`` when absent."""
    match = re.search(r"^##\s*Acceptance\b(.*?)(?=^##\s|\Z)", body, flags=re.MULTILINE | re.DOTALL)
    return match.group(1).strip() if match else ""


#: A backtick span is treated as a shell command only when it leads with a known
#: shell keyword — ``[``/``test`` conditionals, common tool names, runtimes — so
#: prose that happens to backtick a label (``escalate:L1``) or a path being
#: *described* (``governance/dispatch/cli.py --dry-run <fixture-issue>``) is not
#: mistaken for a command to execute. Fenced ```bash blocks are always commands.
_SHELL_KEYWORD = re.compile(
    r"^\s*(?:test\b|\[|\[\[|grep\b|echo\b|cd\b|python3?\b|bash\b|sh\b|gh\b|"
    r"make\b|ls\b|cat\b|curl\b|wget\b|sed\b|awk\b|node\b|npm\b|pytest\b|"
    r"git\b|find\b|rm\b|cp\b|mv\b|mkdir\b|docker\b|gcloud\b|terraform\b|claude\b|env\b)"
)


def _looks_like_command(text: str) -> bool:
    """True when ``text`` leads with a known shell keyword (a command, not prose)."""
    return bool(_SHELL_KEYWORD.match(text))


def parse_acceptance_commands(body: str) -> list[str]:
    """Extract the shell commands from the ``## Acceptance`` section, in source order.

    Two shapes are recognised (the epic's L0-completable convention):
    a ```bash fenced block (every non-empty line is one command — always a
    command), and a backtick-wrapped `` `cmd` `` span that leads with a known
    shell keyword. Commands are returned in source order.
    """
    section = _acceptance_section(body)
    commands: list[str] = []
    if not section:
        return commands
    fence = re.compile(r"```(?:bash|sh)?\s*\n(.*?)```", flags=re.DOTALL)
    token = re.compile(r"```(?:bash|sh)?\s*\n.*?```|`[^`]+`", flags=re.DOTALL)
    for match in token.finditer(section):
        span = match.group(0)
        if span.startswith("```"):
            block = fence.match(span)
            if block is None:
                continue
            for line in block.group(1).splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    commands.append(line)
        else:
            command = span[1:-1].strip()
            if _looks_like_command(command):
                commands.append(command)
    return commands


def run_commands(commands: list[str], tier: str) -> tuple[bool, str, int]:
    """Run every acceptance command; return ``(passed, combined_output, duration_ms)``.

    ``AO_TIER`` is exported to the commands so a deterministic fixture can fail
    at one tier and pass at the next (the escalation proof). A command fails the
    run on a non-zero exit; the combined output captures stdout+stderr of every
    command so the escalation comment can quote the exact failing output.
    """
    env = dict(os.environ)
    env["AO_TIER"] = tier
    start = time.monotonic()
    passed = True
    output_parts: list[str] = []
    for command in commands:
        proc = subprocess.run(command, shell=True, capture_output=True, text=True, env=env)
        output_parts.append(f"$ {command}\n{proc.stdout}{proc.stderr}".rstrip())
        if proc.returncode != 0:
            passed = False
    duration_ms = int((time.monotonic() - start) * 1000)
    return passed, "\n".join(output_parts), duration_ms


def max_attempts_from(body: str, policy: dict[str, Any] | None = None, override: int | None = None) -> int:
    """The per-tier attempt budget: ``--max-attempts`` > body's ``N = <n>`` > policy default."""
    if override is not None:
        return override
    data = policy if policy is not None else load_policy()
    match = re.search(r"\bN\s*=\s*(\d+)\b", body)
    if match:
        return int(match.group(1))
    return int(data["max_attempts_per_tier"])


def escalate_label_for(tier: str, policy: dict[str, Any] | None = None) -> str | None:
    """The ``escalate:*`` label applied to climb past ``tier`` (``None`` above L2)."""
    data = policy if policy is not None else load_policy()
    return data["escalate_label"].get(tier)


def _invoke_model(tier: str, provider: str, model: str, body: str) -> int:
    """Run the model via the fleet's own mechanism (``claude -p --model <id>``).

    Both Claude aliases (haiku/sonnet/opus) and the DeepSeek BYOK ids
    (deepseek-v4-flash/pro) run through ``claude -p --model``, matching
    ``fleet/terminal.py``'s argv shape. Returns the subprocess exit code; the
    model's stdout/stderr are not captured here — the acceptance commands are
    the pass/fail oracle, not the model's own output.
    """
    _ = tier
    proc = subprocess.run(["claude", "-p", "--model", model, body], capture_output=True, text=True)
    return proc.returncode


def _gh_labels(issue_number: int) -> list[str]:
    """Read an issue's labels via ``gh issue view <n> --json labels``."""
    proc = subprocess.run(
        ["gh", "issue", "view", str(issue_number), "--json", "labels"],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise TieredRefusal("gh-labels-failed", proc.stderr.strip() or f"gh issue view {issue_number} exited {proc.returncode}")
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise TieredRefusal("gh-labels-unreadable", str(exc)) from exc
    return [label["name"] for label in payload.get("labels", [])]


def _gh_body(issue_number: int) -> str:
    """Read an issue's body via ``gh issue view <n> --json body``."""
    proc = subprocess.run(
        ["gh", "issue", "view", str(issue_number), "--json", "body"],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise TieredRefusal("gh-body-failed", proc.stderr.strip() or f"gh issue view {issue_number} exited {proc.returncode}")
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise TieredRefusal("gh-body-unreadable", str(exc)) from exc
    return payload.get("body", "")


def _gh_apply_label(issue_number: int, label: str) -> None:
    subprocess.run(["gh", "issue", "edit", str(issue_number), "--add-label", label], check=False)


def _gh_comment(issue_number: int, text: str) -> None:
    subprocess.run(["gh", "issue", "comment", str(issue_number), "--body", text], check=False)


def _main_worktree(root: Path) -> Path | None:
    """The main worktree path from ``git worktree list``, or ``None``.

    ``.board/claims/`` is untracked, so a lane worktree does not carry the live
    ledger while the shared (main) worktree does — the fallback
    :func:`peers.resolve_ledger` needs. Read-only; no fetch, no network.
    """
    try:
        listing = subprocess.run(
            ["git", "-C", str(root), "worktree", "list", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=20.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    for line in listing.stdout.splitlines():
        if line.startswith("worktree "):
            return Path(line[len("worktree ") :].strip())
    return None


def _default_peer_gate(
    issue_number: int,
    body: str,
    agent: str,
    *,
    root: Path | str | None = None,
    main_worktree: Path | str | None = None,
    ledger: Path | str | None = None,
) -> str | None:
    """A2A peer gate: refuse a dispatch whose files a live sibling already holds.

    The peer-check standard of issue #1549 (EPIC #1510) wired into the tiered
    loop: read the live claim ledger (with the main-worktree fallback — a lane
    worktree has no ``.board/claims/``), build the caller's files from the
    issue's own ``Files:`` line, and intersect them with every live sibling via
    ``peers.peer_check``. Returns the refusal string (naming the sibling and the
    specific overlapping path) or ``None`` when disjoint.

    FAIL-CLOSED: an unresolvable or unreadable ledger raises ``TieredRefusal``,
    never a silent ``None`` — ``None`` would report "no siblings" while a
    hundred lanes are live, the exact false green ``peers.peer_check`` warns
    about. ``report.refusal()`` is used, not ``report.verdict``: a sibling off
    which no file evidence could be read reads ``unverifiable`` through the
    verdict, and only the refusal names a real overlap.
    """
    repo_root = Path(root) if root is not None else _PKG_DIR.parents[1]
    resolved = Path(ledger) if ledger is not None else None
    if resolved is None:
        fallback = Path(main_worktree) if main_worktree is not None else _main_worktree(repo_root)
        resolved, note = peers.resolve_ledger(repo_root, fallback)
        if resolved is None:
            # No ledger is NOT "no siblings": refuse rather than report disjoint.
            raise TieredRefusal("peer-ledger-unreadable", note)
    elif not resolved.exists():
        # A ledger NAMED but absent is CANNOT-ASSESS, not empty (peers.main's
        # precedent): read_ledger answers [] for a missing path, and [] renders
        # as a clean "disjoint".
        raise TieredRefusal(
            "peer-ledger-unreadable",
            f"claim ledger {resolved} does not exist; an absent ledger is not an empty one",
        )
    try:
        events = claims_mod.read_ledger(resolved)
    except ValueError as exc:
        raise TieredRefusal("peer-ledger-unreadable", f"{resolved}: {exc}") from exc
    live = claims_mod.active_claims(events)
    mine = tuple(FileClaim(path=path) for path in snapshot_mod.parse_files(body))
    report = peers.peer_check(mine, live, caller_agent=agent, caller_issue=issue_number)
    return report.refusal()


def run(
    issue_number: int,
    body: str,
    labels: Iterable[str],
    *,
    audit_path: Path | str = audit.DEFAULT_AUDIT_PATH,
    max_attempts: int | None = None,
    provider: str | None = None,
    agent: str = "brain",
    at: str | None = None,
    policy: dict[str, Any] | None = None,
    invoke: Callable[[str, str, str], int] | None = None,
    run_acceptance: Callable[[list[str], str], tuple[bool, str, int]] | None = None,
    apply_label: Callable[[int, str], None] | None = None,
    post_comment: Callable[[int, str], None] | None = None,
    peer_gate: Callable[[int, str, str], str | None] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Run the tiered try-loop for one issue; return a summary dict.

    Every attempt — tier, model, timestamp, pass/fail, duration — is recorded in
    the audit trail. On the final failure at a tier the loop applies the
    ``escalate:*`` label and re-dispatches one tier up. Above L2 there is no
    further tier: the loop records the failure and stops.

    All side effects are hooks: ``invoke`` runs the model, ``run_acceptance``
    runs the acceptance commands, ``apply_label``/``post_comment`` touch GitHub.
    Passing ``dry_run=True`` resolves the mapping and records nothing.
    """
    data = policy if policy is not None else load_policy()
    chosen_provider = provider or data["default_provider"]
    tier, warning = read_tier(labels)
    budget = max_attempts_from(body, policy=data, override=max_attempts)
    timestamp = at or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    run_acceptance = run_acceptance or (lambda cmds, t: run_commands(cmds, t))
    invoke = invoke or _invoke_model
    apply_label = apply_label or _gh_apply_label
    post_comment = post_comment or _gh_comment

    commands = parse_acceptance_commands(body)
    attempts: list[dict[str, Any]] = []
    outcome: dict[str, Any] = {
        "issue": issue_number,
        "tier": tier,
        "warning": warning,
        "provider": chosen_provider,
        "max_attempts": budget,
        "commands": commands,
        "attempts": attempts,
        "final_status": "fail",
        "escalated_to": None,
    }

    if dry_run:
        provider_resolved, model = resolve_model(tier, chosen_provider, data)
        outcome["dry_run"] = True
        outcome["provider"] = provider_resolved
        outcome["model"] = model
        return outcome

    # The A2A peer gate runs AFTER the dry-run early return (a dry-run resolves
    # the mapping and touches nothing, so it must not be gated) and BEFORE any
    # dispatch: an overlap with a live sibling aborts the whole try-loop, so no
    # model is invoked and no attempt is recorded. It is an injected hook like
    # the others, and it is deliberately NOT swapped for a no-op by --no-gh /
    # --no-dispatch — the gate reads its own ledger, not GitHub or a model.
    if peer_gate is None:
        peer_gate = _default_peer_gate
    refusal = peer_gate(issue_number, body, agent)
    if refusal:
        post_comment(issue_number, f"tiered dispatch aborted before dispatch: {refusal}")
        raise TieredRefusal("peer-overlap", refusal)

    current = tier
    while current in TIERS:
        provider_resolved, model = resolve_model(current, chosen_provider, data)
        failed = False
        for attempt_number in range(1, budget + 1):
            _ = invoke(current, provider_resolved, model)  # model produces the work
            passed, output, duration_ms = run_acceptance(commands, current)
            # The "no tier:* label" warning is a one-time signal, recorded once
            # (on the first attempt) so the ledger itself shows the default was
            # applied rather than the tier being explicit.
            detail = warning if (warning and attempt_number == 1) else ""
            record = audit.record_tiered_attempt(
                issue=issue_number,
                agent=agent,
                at=timestamp,
                tier=current,
                provider=provider_resolved,
                model=model,
                status="pass" if passed else "fail",
                attempt=attempt_number,
                duration_ms=duration_ms,
                output=output,
                detail=detail,
            )
            attempts.append(record)
            audit.append(record, path=audit_path)
            if passed:
                outcome["final_status"] = "pass"
                outcome["final_tier"] = current
                return outcome
            failed = True
        # All budget attempts at this tier failed: escalate.
        next_label = escalate_label_for(current, data)
        outcome["escalated_to"] = next_label
        if next_label is None:
            # Above L2: no further tier. Record the terminal failure and stop.
            outcome["final_status"] = "fail"
            outcome["final_tier"] = current
            return outcome
        apply_label(issue_number, next_label)
        last_failure = attempts[-1]["output"] if attempts else ""
        post_comment(
            issue_number,
            (
                f"tiered dispatch: all {budget} attempt(s) at `tier:{current}` failed. "
                f"Escalating to `{next_label}`.\n\n"
                f"Last failing command output:\n```\n{last_failure}\n```"
            ),
        )
        current = {"L0": "L1", "L1": "L2"}[current]
    # Exhausted the tier ladder without a pass (L2 failed all budget attempts).
    outcome["final_status"] = "fail"
    outcome["final_tier"] = "L2"
    return outcome
