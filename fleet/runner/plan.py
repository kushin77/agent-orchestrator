"""plan.py — the PR runner's planner: a pure function of its inputs (issue #1343).

    plan(open_prs, evidence, live_builds, holds, prune, master_tip, capacity)
        -> ordered list[Action]

No subprocess, no socket, no filesystem. Every test in
`fleet/runner/tests/test_plan.py` drives this function with fixtures; the real
inputs are gathered by `cli.py` and the actions are executed by `verify.py` /
`merge.py`.

THE RULES, EACH A MEASURED LESSON FROM THE 2026-09-18 PROTOTYPE
  1. Evidence is per (pr, head sha). A live build/verify whose sha is not the
     PR's CURRENT head is `cancel-stale`d; evidence for an old head is not read.
  2. EXPIRED / CANCELLED / PARKED / CANNOT-ASSESS are terminal-not-green: the
     head is re-queued (`verify`), never awaited. Only a run that is still
     `pending` for the current head is awaited.
  3. A merge needs green evidence AND that evidence must be about the CURRENT
     master tip: a merged-tree record that names an older `base_tip` is
     `merged-tree-stale` (set aside by evidence.py) and the head is re-queued;
     a head-level record (no `base_tip`: Cloud Build, a head verify) is passed
     to the merged-tree seam, which judges at merge time. No master tip known
     -> no merges at all (`master-tip-unknown`).
  4. The merge action's verb is `scripts/merge-pr.sh` — the squash guard runs
     first there, and the trailer / `Closes #<n>` are derived by it (#1266).
  5. CANNOT-ASSESS is never green (evidence.py) and a Cloud Build green
     outranks a local red (evidence.py's ranking); the planner reports both.
  7. A failed gate-lock prune (`PruneResult.ok == False`) plans NO new verify:
     `gatelock-prune-failed` is a refusal, not a warning.
"""

from __future__ import annotations

from fleet.runner.evidence import EvidenceTable
from fleet.runner.model import (
    ACTION_ORDER,
    AWAIT,
    CANCEL_STALE,
    DEFER,
    GREEN,
    HOLD,
    LIVE_STATES,
    MERGE,
    MERGE_VERB,
    MERGED_TREE_SEAM,
    RED,
    REFUSE,
    REQUEUE_STATES,
    SKIP,
    VERIFY,
    Action,
    LiveBuild,
    OpenPR,
    PruneResult,
)

DEFAULT_CAPACITY = 3


def _order(actions: list[Action]) -> list[Action]:
    rank = {kind: index for index, kind in enumerate(ACTION_ORDER)}
    return sorted(actions, key=lambda a: (rank.get(a.kind, len(rank)), a.pr, a.sha))


def plan(
    open_prs: list[OpenPR] | tuple[OpenPR, ...],
    evidence: EvidenceTable,
    live_builds: list[LiveBuild] | tuple[LiveBuild, ...] = (),
    holds: dict[int, str] | None = None,
    prune: PruneResult | None = None,
    master_tip: str | None = None,
    capacity: int = DEFAULT_CAPACITY,
) -> list[Action]:
    """The ordered actions for one cycle. Pure."""
    holds = dict(holds or {})
    prune = prune if prune is not None else PruneResult(ok=True, detail="not-run")
    heads = {pr.number: pr.head_sha for pr in open_prs if not pr.draft}
    actions: list[Action] = []

    # --- lesson 1: stale builds/verifies are cancelled before anything else ---
    live_for_head: dict[tuple[int, str], list[LiveBuild]] = {}
    for build in live_builds:
        current = heads.get(build.pr)
        if current is None or build.sha != current:
            if build.status in LIVE_STATES:
                actions.append(
                    Action(
                        CANCEL_STALE,
                        build.pr,
                        build.sha,
                        reason=f"stale-head:{build.pr}:{build.sha[:12]}->{(current or 'closed')[:12]}",
                        build_id=build.id,
                    )
                )
            continue
        live_for_head.setdefault((build.pr, build.sha), []).append(build)

    local_in_flight = sum(
        1 for builds in live_for_head.values() for b in builds if b.kind == "local" and b.status in LIVE_STATES
    )
    slots = max(0, capacity - local_in_flight)

    for pr in open_prs:
        number, sha = pr.number, pr.head_sha
        if pr.draft:
            actions.append(Action(SKIP, number, sha, reason=f"draft:{number}"))
            continue
        if number in holds:
            actions.append(Action(HOLD, number, sha, reason=f"held:{number}:{holds[number]}"))
            continue

        verdict = evidence.verdict(number, sha, master_tip)
        running = [b for b in live_for_head.get((number, sha), []) if b.status in LIVE_STATES]
        finished_not_green = [b for b in live_for_head.get((number, sha), []) if b.status in REQUEUE_STATES]

        if verdict.green:
            basis = verdict.basis
            assert basis is not None
            if master_tip is None:
                actions.append(Action(REFUSE, number, sha, reason=f"master-tip-unknown:{number}"))
                continue
            if pr.mergeable != "MERGEABLE":
                actions.append(Action(REFUSE, number, sha, reason=f"not-mergeable:{number}:{pr.mergeable}"))
                continue
            actions.append(
                Action(
                    MERGE,
                    number,
                    sha,
                    reason=f"green:{basis.source}:{number}",
                    via=MERGE_VERB,
                    extra={"merged_tree_seam": MERGED_TREE_SEAM, "master_tip": master_tip, "evidence": verdict.explain()},
                )
            )
            continue

        if verdict.state == RED:
            actions.append(Action(REFUSE, number, sha, reason=f"verify-red:{number}:{verdict.basis.source if verdict.basis else ''}"))
            continue

        # No verdict for this head. Running for the current head -> await.
        if running:
            actions.append(Action(AWAIT, number, sha, reason=f"verify-running:{number}", build_id=running[0].id))
            continue

        # lesson 2: a finished-not-green run is re-queued by name, never awaited.
        why = "no-evidence"
        if verdict.state == "none" and verdict.stale:
            # lesson 3: the only green named an older master tip.
            old = verdict.stale[0].base_tip or ""
            actions.append(Action(REFUSE, number, sha, reason=f"merged-tree-stale:{number}:{old[:12]}!={(master_tip or '')[:12]}"))
            why = "merged-tree-stale"
        elif finished_not_green:
            why = f"{finished_not_green[0].status}:{finished_not_green[0].id}"
        elif verdict.state in REQUEUE_STATES:
            why = f"{verdict.state}:{verdict.basis.source if verdict.basis else ''}"

        if not prune.ok:
            actions.append(Action(REFUSE, number, sha, reason=f"gatelock-prune-failed:{prune.detail or 'unknown'}"))
            continue
        if slots > 0:
            slots -= 1
            actions.append(Action(VERIFY, number, sha, reason=f"requeue:{why}:{number}"))
        else:
            actions.append(Action(DEFER, number, sha, reason=f"capacity:{number}:{why}"))

    return _order(actions)


def explain(actions: list[Action]) -> list[str]:
    """One line per action, for `plan` / `status` output."""
    lines = []
    for action in actions:
        bits = [f"{action.kind:<12}", f"#{action.pr}"]
        if action.sha:
            bits.append(action.sha[:12])
        if action.reason:
            bits.append(action.reason)
        if action.via:
            bits.append(f"via {action.via}")
        lines.append(" ".join(bits))
    return lines


__all__ = ["plan", "explain", "DEFAULT_CAPACITY", "GREEN"]
