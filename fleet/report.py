#!/usr/bin/env python3
"""Fleet status report — the planning-first surface paperclip owns (issue #302).

WHY it exists: the fleet already produces every fact a status report needs, but
they are spread across five surfaces — the wave plans, the claim ledger, the run
markers, the event log and the board snapshot — and each one answers a different
question. ADR-0012 gives the *reporting* half of the fleet a declared pattern
owner: the vendored ``paperclip`` module, whose ``roadmaps`` feature is
"plan-first artifacts and roadmap tracking for workstreams and milestones" and
whose ``status-reports`` feature is "project status and delivery reporting
patterns with crisp, human-readable output". This module is that report, derived
from state the fleet already writes — never a second copy of it.

Design rule (the same one `fleet/console.py` follows): every section is built by
a PURE function over a plain dict, and `read_state()` is the only part that
touches the filesystem. That is what lets the tests assert the whole report from
a fixture, with no fleet, no network and no tmux session — and what keeps the
report's own shape from depending on the machine that produced it.

The report is READ-ONLY. It reads `.fleet/`, the claim ledger and the committed
board snapshot; it never writes, claims, releases, reaps or dispatches anything.
No reporting change may alter a dispatch decision (ADR-0012 decision (d)).

Sections (paperclip's planning shape, in dependency order):

* **now**       — work in flight: a live claim, or a run marker a loop is tracking.
* **next**      — planned and unblocked: wave children whose dependencies are met,
                  plus the active milestone's frontier.
* **blocked**   — work that cannot proceed, named with what it waits on.
* **delivered** — terminal outcomes the fleet already recorded.

Every item carries its issue number, its lane, and an evidence pointer (the run,
the claim, the child's own ``Verify:`` command, or the closing record).

Exit-code contract (the repo's tri-state convention — `fleet/health.py`,
`fleet/cron.py`, `governance/dispatch/cli.py`):

* ``0`` OK            — the report was produced and nothing in it is blocked.
* ``1`` NOT-OK        — the report was produced and at least one item is blocked.
* ``2`` CANNOT-ASSESS — no plan source is present (neither a wave plan nor a
                        board snapshot): there is nothing to report on, and a
                        report is never fabricated to hide that.

Usage:
    python3 fleet/report.py            # the human-readable report
    python3 fleet/report.py --json     # the same report, typed and structured
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import runtime

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "governance" / "dispatch"))

import claims as claims_mod  # noqa: E402
import order  # noqa: E402
import snapshot as snapshot_mod  # noqa: E402

REPO = "kushin77/agent-orchestrator"
SCHEMA = "fleet.report/v1"

OK, NOT_OK, CANNOT_ASSESS = 0, 1, 2

WIDTH = 96
EVIDENCE_WIDTH = 62
# The audit log and the telemetry log are append-only and multi-megabyte; a report
# reads a bounded tail rather than the whole file, so the report is never the load
# it exists to explain.
OUTCOME_LIMIT = 40
TELEMETRY_LIMIT = 200
TAIL_BYTES = 262144

# GR-10 provenance: the declared pattern this surface implements, and the registry
# persona that owns it (ADR-0012 decision (c)). Recorded here as a data structure
# so the human output, the JSON output and the test that asserts the hand-off all
# read one source.
PROVENANCE: dict[str, Any] = {
    "pattern_source": "vendor/CMR/catalog/modules/paperclip",
    "pattern_features": ["roadmaps", "status-reports", "skills-and-governance"],
    "persona": "registry/personas/cards/paperclip.yaml",
    "persona_id": "paperclip",
    "persona_tier": "LOW",
    "persona_lanes": ["paperclip", "knowledge"],
    "decision": "docs/decision-records/ADR-0012-hermes-paperclip-boundary.md",
}

SECTIONS = ("now", "next", "blocked", "delivered")
ITEM_FIELDS = ("issue", "lane", "state", "title", "agent", "evidence", "source")


# --- paths (resolved per call, so a test can redirect the whole tree) --------


def repo_root(root: Path | str | None = None) -> Path:
    return Path(root) if root else ROOT


def fleet_dir(root: Path | str | None = None) -> Path:
    if root is None:
        return runtime.FLEET_DIR
    return repo_root(root) / ".fleet"


def waves_dir(root: Path | str | None = None) -> Path:
    return fleet_dir(root) / "waves"


def runs_dir(root: Path | str | None = None) -> Path:
    return fleet_dir(root) / "runs"


def runs_log(root: Path | str | None = None) -> Path:
    return fleet_dir(root) / "runs.jsonl"


def slog_path(root: Path | str | None = None) -> Path:
    return fleet_dir(root) / "slog.jsonl"


def board_snapshot_path(root: Path | str | None = None) -> Path:
    return repo_root(root) / ".board" / "snapshot.json"


def board_ledger_path(root: Path | str | None = None) -> Path:
    return repo_root(root) / ".board" / "claims.jsonl"


def attestation_path(root: Path | str | None = None) -> Path:
    return repo_root(root) / ".verify" / "attestation.json"


# --- readers (the only part that does I/O) ----------------------------------


def read_json_object(path: Path) -> dict | None:
    """A JSON object from `path`, or None — a missing file is not a crash."""
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def tail_records(path: Path, limit: int, *, max_bytes: int = TAIL_BYTES) -> list[dict]:
    """The last `limit` JSON objects in an append-only JSONL log, oldest first.

    The first line of a mid-file block is dropped: a bounded read from the end
    starts inside a record, and a torn fragment is not an outcome.
    """
    target = Path(path)
    try:
        size = target.stat().st_size
    except OSError:
        return []
    try:
        with target.open("rb") as handle:
            handle.seek(max(0, size - max_bytes))
            chunk = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    lines = chunk.splitlines()
    if size > max_bytes and lines:
        lines = lines[1:]
    records: list[dict] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            records.append(payload)
    return records[-limit:]


def read_wave_plans(directory: Path) -> list[dict]:
    """Every wave plan, lowest parent first — the plan half of the report."""
    plans: list[dict] = []
    if not directory.exists():
        return plans
    for path in sorted(directory.glob("*.json")):
        plan = read_json_object(path)
        if plan is not None and "parent" in plan and "children" in plan:
            plans.append(plan)
    plans.sort(key=lambda plan: int(plan.get("parent") or 0))
    return plans


def read_claims(ledger: Path, now: datetime | None = None) -> list[dict]:
    """Live claims — the same fold `governance/dispatch/cli.py status` prints.

    Read through `governance/dispatch/claims.py` rather than scraped from the
    command's text, so the values are typed and a formatting change in the CLI
    cannot silently empty this section. Expired claims are dropped exactly as the
    claim gate drops them; `now` is a seam for tests, never a caller's knob.
    """
    try:
        live = claims_mod.active_claims(claims_mod.read_ledger(ledger), now)
    except (OSError, ValueError, TypeError):
        return []
    return [
        {
            "issue": number,
            "agent": claim.agent,
            "lane": claim.lane,
            "at": claim.at,
            "reason": claim.reason,
        }
        for number, claim in sorted(live.items())
    ]


def read_runs(directory: Path) -> list[dict]:
    """In-flight run markers — one file per directive a loop is tracking.

    Written atomically by `fleet/terminal.mark_run`, and cleared when the run
    ends, so what is present here is work that has not finished.
    """
    runs: list[dict] = []
    if not directory.exists():
        return runs
    for path in sorted(directory.glob("*.json")):
        record = read_json_object(path)
        if record is None or "issue" not in record:
            continue
        try:
            issue = int(record["issue"])
        except (TypeError, ValueError):
            continue
        runs.append(
            {
                "directive_id": path.stem,
                "issue": issue,
                "agent": str(record.get("agent") or ""),
                "started_at": str(record.get("started_at") or ""),
            }
        )
    runs.sort(key=lambda run: (run["issue"], run["directive_id"]))
    return runs


def read_board(path: Path) -> dict:
    """The board snapshot plus the active milestone and its frontier.

    Milestone and frontier come from `governance/dispatch/order.py` — the rule the
    claim gate enforces — so the report cannot disagree with dispatch about what
    is next. An absent or unusable snapshot is reported as unreadable, never
    guessed at.
    """
    board: dict[str, Any] = {
        "readable": False,
        "generated_at": "",
        "source": "",
        "milestone": "",
        "frontier": None,
        "issues": {},
        "blocked": [],
    }
    try:
        snapshot = snapshot_mod.load(path)
    except (OSError, ValueError, json.JSONDecodeError):
        return board
    milestone = order.active_milestone(snapshot, frozenset())
    frontier = order.frontier(snapshot, milestone) if milestone else None
    board["readable"] = True
    board["generated_at"] = snapshot.generated_at
    board["source"] = snapshot.source
    board["milestone"] = milestone
    board["frontier"] = {"number": frontier.number, "title": frontier.title} if frontier else None
    board["issues"] = {
        number: {"state": issue.state, "title": issue.title, "milestone": issue.milestone}
        for number, issue in snapshot.issues.items()
    }
    if milestone:
        board["blocked"] = [
            {
                "issue": issue.number,
                "title": issue.title,
                "blocked_by": snapshot.blockers_open(issue),
            }
            for issue in sorted(snapshot.open_issues(), key=lambda item: item.number)
            if issue.milestone == milestone and snapshot.blockers_open(issue)
        ]
    return board


def read_verify(path: Path) -> dict | None:
    """The last `make verify` attestation, if the tree carries one.

    `.verify/` is a generated artifact and is gitignored, so this is best-effort:
    the report states the gate's own verdict when it is present and says so
    plainly when it is not, rather than implying a verification it cannot cite.
    """
    payload = read_json_object(path)
    if payload is None:
        return None
    return {
        "result": str(payload.get("result") or ""),
        "exit_code": payload.get("exit_code"),
        "git_sha": str(payload.get("git_sha") or ""),
        "check_count": payload.get("check_count"),
        "timestamp": str(payload.get("timestamp") or ""),
    }


def head_commit(root: Path | str | None = None) -> str:
    """The commit the report is stamped with — the tree the state was read from."""
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root(root)), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return result.stdout.strip() or "unknown"


def read_state(
    root: Path | str | None = None,
    *,
    now: datetime | None = None,
    generated_at: str | None = None,
) -> dict:
    """Every source the report is derived from, gathered once.

    `root` redirects the whole tree, which is what lets the tests build a
    synthetic fleet in a tmp directory and assert the report over it. `now`
    fixes the claim-expiry clock and defaults to the wall clock; `generated_at`
    stamps the report and defaults to `now`.
    """
    moment = now or datetime.now(timezone.utc)
    waves = read_wave_plans(waves_dir(root))
    claims = read_claims(board_ledger_path(root), moment)
    runs = read_runs(runs_dir(root))
    outcomes = tail_records(slog_path(root), OUTCOME_LIMIT)
    telemetry = tail_records(runs_log(root), TELEMETRY_LIMIT)
    board = read_board(board_snapshot_path(root))
    verify = read_verify(attestation_path(root))
    return {
        "root": str(repo_root(root)),
        "commit": head_commit(root),
        "generated_at": generated_at or moment.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "waves": waves,
        "claims": claims,
        "runs": runs,
        "outcomes": outcomes,
        "telemetry": telemetry,
        "board": board,
        "verify": verify,
        "sources": {
            "waves": {"path": str(waves_dir(root)), "records": len(waves), "present": waves_dir(root).exists()},
            "claims": {"path": str(board_ledger_path(root)), "records": len(claims), "present": board_ledger_path(root).exists()},
            "runs": {"path": str(runs_dir(root)), "records": len(runs), "present": runs_dir(root).exists()},
            "outcomes": {"path": str(slog_path(root)), "records": len(outcomes), "present": slog_path(root).exists()},
            "telemetry": {"path": str(runs_log(root)), "records": len(telemetry), "present": runs_log(root).exists()},
            "board": {
                "path": str(board_snapshot_path(root)),
                "records": len(board["issues"]),
                "present": board["readable"],
            },
            "verify": {
                "path": str(attestation_path(root)),
                "records": 1 if verify else 0,
                "present": verify is not None,
            },
        },
    }


# --- the typed report -------------------------------------------------------


@dataclass(frozen=True)
class Item:
    """One line of the report: what, where, and the evidence that says so."""

    issue: int
    lane: str = ""
    state: str = ""
    title: str = ""
    agent: str = ""
    evidence: str = ""
    source: str = ""

    def to_json(self) -> dict[str, Any]:
        return {field: getattr(self, field) for field in ITEM_FIELDS}


@dataclass(frozen=True)
class Source:
    """One state source the report was derived from, and what it yielded."""

    name: str
    path: str
    records: int
    present: bool

    def to_json(self) -> dict[str, Any]:
        return {"name": self.name, "path": self.path, "records": self.records, "present": self.present}


@dataclass(frozen=True)
class Basis:
    """What the report rests on — the evidence basis, not a summary claim.

    Issue #302: the status output states what it was derived from and at which
    commit, rather than asking the reader to trust a summary. `sources` names
    every source actually read; `verify` carries the gate's own verdict when the
    tree has one.
    """

    commit: str
    generated_at: str
    milestone: str
    frontier: dict | None
    sources: tuple[Source, ...]
    verify: dict | None

    def to_json(self) -> dict[str, Any]:
        return {
            "commit": self.commit,
            "generated_at": self.generated_at,
            "milestone": self.milestone,
            "frontier": self.frontier,
            "sources": [source.to_json() for source in self.sources],
            "verify": self.verify,
        }


@dataclass(frozen=True)
class Report:
    """The report itself: a basis and the four planning sections."""

    basis: Basis
    now: tuple[Item, ...] = ()
    next: tuple[Item, ...] = ()
    blocked: tuple[Item, ...] = ()
    delivered: tuple[Item, ...] = ()

    def section(self, name: str) -> tuple[Item, ...]:
        return getattr(self, name)

    def counts(self) -> dict[str, int]:
        return {name: len(self.section(name)) for name in SECTIONS}

    def to_json(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "repo": REPO,
            "provenance": PROVENANCE,
            "basis": self.basis.to_json(),
            "sections": {name: [item.to_json() for item in self.section(name)] for name in SECTIONS},
            "counts": self.counts(),
            "verdict": verdict(self),
        }


def validate_report(payload: Any, where: str = "report") -> list[str]:
    """Structural problems with a report payload; an empty list means it is valid.

    JSON is a first-class output of this module, so its shape is checked rather
    than assumed — a caller that consumes `--json` gets a guarantee, not a hope.
    """
    problems: list[str] = []
    if not isinstance(payload, dict):
        return [f"{where}: payload must be a JSON object"]
    if payload.get("schema") != SCHEMA:
        problems.append(f"{where}: schema must be {SCHEMA!r}, got {payload.get('schema')!r}")
    if not isinstance(payload.get("provenance"), dict):
        problems.append(f"{where}: provenance must be an object")
    basis = payload.get("basis")
    if not isinstance(basis, dict):
        problems.append(f"{where}: basis must be an object")
    elif not str(basis.get("commit") or "").strip():
        problems.append(f"{where}: basis.commit must name the commit the report was derived at")
    sections = payload.get("sections")
    if not isinstance(sections, dict):
        problems.append(f"{where}: sections must be an object")
        return problems
    for name in SECTIONS:
        items = sections.get(name)
        if not isinstance(items, list):
            problems.append(f"{where}: section {name!r} must be a list")
            continue
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                problems.append(f"{where}: {name}[{index}] must be an object")
                continue
            missing = [field for field in ITEM_FIELDS if field not in item]
            if missing:
                problems.append(f"{where}: {name}[{index}] is missing {missing}")
            elif not isinstance(item["issue"], int):
                problems.append(f"{where}: {name}[{index}].issue must be an integer")
    return problems


# --- derivation (pure: asserted by the tests, so it must not do I/O) --------


def closed_issues(state: dict) -> set[int]:
    """Board numbers the snapshot records as closed — the plan's own progress."""
    return {
        number
        for number, issue in state["board"]["issues"].items()
        if str(issue.get("state") or "").strip().lower() == "closed"
    }


def child_lane(state: dict, issue: int) -> str:
    """The lane a wave plan assigns to an issue, for an item that has no claim."""
    for plan in state["waves"]:
        for child in plan.get("children") or []:
            try:
                if int(child.get("issue")) == issue:
                    return str(child.get("lane") or "")
            except (TypeError, ValueError):
                continue
    return ""


def _child_waits(plan: dict, child: dict, closed: set[int]) -> list[int]:
    """Sibling issues this child's `depends_on` resolves to that are not closed yet."""
    by_index: dict[int, int] = {}
    for candidate in plan.get("children") or []:
        try:
            by_index[int(candidate["index"])] = int(candidate["issue"])
        except (KeyError, TypeError, ValueError):
            continue
    waits: list[int] = []
    for index in child.get("depends_on") or []:
        try:
            number = by_index.get(int(index))
        except (TypeError, ValueError):
            continue
        if number is not None and number not in closed:
            waits.append(number)
    return waits


def _wave_children(state: dict) -> list[tuple[str, dict, dict]]:
    """(source path, plan, child) for every child of every wave plan, in plan order."""
    out: list[tuple[str, dict, dict]] = []
    for plan in state["waves"]:
        source = f".fleet/waves/{plan.get('parent')}.json"
        for child in plan.get("children") or []:
            if isinstance(child, dict) and "issue" in child:
                out.append((source, plan, child))
    return out


def now_items(state: dict) -> tuple[Item, ...]:
    """Work in flight: a run marker a loop is tracking, or a live claim.

    A run marker is the stronger fact — it means a loop is executing the issue
    right now — so it wins the merge and the claim is kept as corroboration.
    """
    claims = {int(claim["issue"]): claim for claim in state["claims"]}
    runs: dict[int, dict] = {}
    for run in state["runs"]:
        runs.setdefault(int(run["issue"]), run)
    items: list[Item] = []
    for issue in sorted(set(claims) | set(runs)):
        claim = claims.get(issue)
        run = runs.get(issue)
        lane = str((run or {}).get("lane") or (claim or {}).get("lane") or child_lane(state, issue))
        if run is not None:
            evidence = f"run {run['directive_id']} started {run['started_at']}"
            if claim is not None:
                evidence += f"; claim since {claim['at']}"
            items.append(
                Item(
                    issue=issue,
                    lane=lane,
                    state="running",
                    agent=str(run.get("agent") or ""),
                    evidence=evidence,
                    source=".fleet/runs",
                )
            )
            continue
        items.append(
            Item(
                issue=issue,
                lane=lane,
                state="claimed",
                agent=str(claim.get("agent") or ""),
                evidence=f"claim held since {claim.get('at')} ({claim.get('reason') or 'n/a'})",
                source=".board/claims.jsonl",
            )
        )
    return tuple(items)


def next_items(state: dict, in_flight: tuple[Item, ...] = ()) -> tuple[Item, ...]:
    """Planned and unblocked: wave children with their dependencies met, then the frontier."""
    closed = closed_issues(state)
    busy = {item.issue for item in in_flight}
    items: list[Item] = []
    for source, plan, child in _wave_children(state):
        try:
            number = int(child["issue"])
        except (KeyError, TypeError, ValueError):
            continue
        if number in closed or number in busy:
            continue
        if number in {int(dispatched) for dispatched in plan.get("dispatched") or []}:
            continue
        if _child_waits(plan, child, closed):
            continue
        items.append(
            Item(
                issue=number,
                lane=str(child.get("lane") or ""),
                state="planned",
                evidence=str(child.get("verify") or "no Verify: declared"),
                source=source,
            )
        )
    frontier = state["board"].get("frontier")
    if frontier and int(frontier["number"]) not in {item.issue for item in items} | busy:
        items.insert(
            0,
            Item(
                issue=int(frontier["number"]),
                lane="",
                state="frontier",
                title=str(frontier.get("title") or ""),
                evidence=f"frontier of {state['board'].get('milestone') or '<no milestone>'}",
                source=".board/snapshot.json",
            ),
        )
    return tuple(items)


def blocked_items(state: dict) -> tuple[Item, ...]:
    """Work that cannot proceed, each item named with what it waits on.

    Four real sources, so a blocked report is a finding rather than a mood: a
    wave child whose dependencies are unresolved, a milestone issue with a
    declared open blocker, a run the telemetry recorded as failed, and a
    critical escalation a rung raised on the channel.
    """
    closed = closed_issues(state)
    items: list[Item] = []
    for source, plan, child in _wave_children(state):
        try:
            number = int(child["issue"])
        except (KeyError, TypeError, ValueError):
            continue
        if number in closed:
            continue
        waits = _child_waits(plan, child, closed)
        if not waits:
            continue
        listed = ", ".join(f"#{wait}" for wait in waits)
        items.append(
            Item(
                issue=number,
                lane=str(child.get("lane") or ""),
                state="waiting",
                evidence=f"depends on {listed}",
                source=source,
            )
        )
    for entry in state["board"].get("blocked") or []:
        listed = ", ".join(f"#{number}" for number in entry.get("blocked_by") or [])
        items.append(
            Item(
                issue=int(entry["issue"]),
                lane=child_lane(state, int(entry["issue"])),
                state="blocked",
                title=str(entry.get("title") or ""),
                evidence=f"blocked by {listed}",
                source=".board/snapshot.json",
            )
        )
    for record in state["telemetry"]:
        if str(record.get("status") or "") != "failed":
            continue
        try:
            number = int(record["issue"])
        except (KeyError, TypeError, ValueError):
            continue
        items.append(
            Item(
                issue=number,
                lane=child_lane(state, number),
                state="failed",
                agent=str(record.get("agent") or ""),
                evidence=f"run {record.get('run_id')} failed: {record.get('detail') or 'no detail'}",
                source=".fleet/runs.jsonl",
            )
        )
    for record in state["outcomes"]:
        if str(record.get("type") or "") != "escalate":
            continue
        if str(record.get("severity") or "") != "critical":
            continue
        try:
            number = int(record["issue"])
        except (KeyError, TypeError, ValueError):
            continue
        items.append(
            Item(
                issue=number,
                lane=child_lane(state, number),
                state="escalated",
                agent=str(record.get("from") or ""),
                evidence=f"critical escalation: {record.get('body') or ''}",
                source=".fleet/slog.jsonl",
            )
        )
    return tuple(items)


def delivered_items(state: dict) -> tuple[Item, ...]:
    """Terminal outcomes the fleet already recorded: closed children, done runs, results.

    One item per issue, strongest evidence first: a wave child the board records
    as closed outranks the run record that closed it, which outranks the result
    message reported on the channel. A section that repeated the same delivery
    once per source would be a log, not a report.
    """
    closed = closed_issues(state)
    items: list[Item] = []
    seen: set[int] = set()
    for source, plan, child in _wave_children(state):
        try:
            number = int(child["issue"])
        except (KeyError, TypeError, ValueError):
            continue
        if number not in closed or number in seen:
            continue
        seen.add(number)
        items.append(
            Item(
                issue=number,
                lane=str(child.get("lane") or ""),
                state="closed",
                evidence=f"wave child of #{plan.get('parent')} closed",
                source=source,
            )
        )
    for record in state["telemetry"]:
        if str(record.get("status") or "") != "done":
            continue
        try:
            number = int(record["issue"])
        except (KeyError, TypeError, ValueError):
            continue
        if number in seen:
            continue
        seen.add(number)
        items.append(
            Item(
                issue=number,
                lane=child_lane(state, number),
                state="done",
                agent=str(record.get("agent") or ""),
                evidence=f"run {record.get('run_id')} finished {record.get('finished_at')}",
                source=".fleet/runs.jsonl",
            )
        )
    for record in state["outcomes"]:
        if str(record.get("type") or "") != "result":
            continue
        try:
            number = int(record["issue"])
        except (KeyError, TypeError, ValueError):
            continue
        if number in seen:
            continue
        seen.add(number)
        items.append(
            Item(
                issue=number,
                lane=child_lane(state, number),
                state="reported",
                agent=str(record.get("from") or ""),
                evidence=f"result: {record.get('body') or ''}",
                source=".fleet/slog.jsonl",
            )
        )
    return tuple(items)


def build_report(state: dict) -> Report:
    """The whole report from an already-gathered state dict. Pure.

    Callers that must not fabricate a report ask `report_from` instead, which
    refuses when there is no plan to report on.
    """
    board = state["board"]
    frontier = board.get("frontier")
    basis = Basis(
        commit=str(state.get("commit") or "unknown"),
        generated_at=str(state.get("generated_at") or ""),
        milestone=str(board.get("milestone") or ""),
        frontier={"number": int(frontier["number"]), "title": str(frontier.get("title") or "")} if frontier else None,
        sources=tuple(Source(name=name, **fields) for name, fields in state["sources"].items()),
        verify=state.get("verify"),
    )
    in_flight = now_items(state)
    return Report(
        basis=basis,
        now=in_flight,
        next=next_items(state, in_flight),
        blocked=blocked_items(state),
        delivered=delivered_items(state),
    )


def report_from(state: dict) -> Report | None:
    """The report, or None when no plan source exists — never a fabricated report.

    "No plan" is exactly: no wave plan AND no board snapshot issue. The execution
    logs alone are not a plan, so they cannot carry a report on their own.
    """
    if not state["waves"] and not state["board"]["issues"]:
        return None
    return build_report(state)


def verdict(report: Report | None) -> int:
    """0 OK / 1 NOT-OK / 2 CANNOT-ASSESS — the repo's tri-state convention."""
    if report is None:
        return CANNOT_ASSESS
    return NOT_OK if report.blocked else OK


# --- rendering (pure: asserted by the tests, so it must not do I/O) ---------


def truncate(text: object, width: int = EVIDENCE_WIDTH) -> str:
    """One line, whitespace collapsed, ellipsised at `width`."""
    collapsed = " ".join(str(text or "").split())
    if len(collapsed) <= width:
        return collapsed
    return collapsed[: width - 1].rstrip() + "\u2026"


def section(title: str, items: tuple[Item, ...]) -> str:
    head = f"\u2500\u2500 {title} ({len(items)}) " + "\u2500" * max(0, WIDTH - len(title) - 8)
    body = [item_line(item) for item in items] or ["  (none)"]
    return "\n".join([head, *body])


def item_line(item: Item) -> str:
    who = truncate(item.title or item.agent or "-", 24)
    return (
        f"  #{item.issue:<5} {item.lane or '-':<16} {item.state:<9} {who:<24} "
        f"{truncate(item.evidence)}"
    )


def source_line(source: Source) -> str:
    mark = "read" if source.present else "absent"
    return f"  {source.name:<10} {mark:<7} {source.records:>4} record(s)  {source.path}"


def basis_lines(basis: Basis) -> list[str]:
    """What the report rests on: the commit, the scope, and the gate's verdict."""
    scope = f"milestone {basis.milestone or '<none>'}"
    if basis.frontier:
        scope += f" · frontier #{basis.frontier['number']} {truncate(basis.frontier['title'], 40)}"
    if basis.verify is None:
        verified = "no .verify/attestation.json in this tree — no gate verdict to cite"
    else:
        sha = basis.verify.get("git_sha") or "unknown"
        verified = (
            f"{basis.verify.get('result') or 'UNKNOWN'} "
            f"(exit {basis.verify.get('exit_code')}, {basis.verify.get('check_count')} checks) "
            f"at {str(sha)[:12]} · {basis.verify.get('timestamp')}"
        )
        if basis.verify.get("git_sha") and basis.commit not in str(basis.verify.get("git_sha")):
            verified += "  [does not match the report HEAD]"
    return [f"  basis:    scope {scope}", f"  verified: {verified}", *(
        f"  source:   {source_line(source).strip()}" for source in basis.sources
    )]


def render(report: Report) -> str:
    """The human-readable report. Pure: everything it needs is in `report`."""
    lines = [
        "\u2550" * WIDTH,
        f"  {REPO} \u2014 fleet status report \u00b7 HEAD {report.basis.commit} \u00b7 {report.basis.generated_at}",
        f"  pattern:  {PROVENANCE['pattern_source']} ({', '.join(PROVENANCE['pattern_features'])})",
        f"  persona:  {PROVENANCE['persona_id']} tier {PROVENANCE['persona_tier']} \u00b7 {PROVENANCE['persona']}",
        *basis_lines(report.basis),
        "\u2550" * WIDTH,
    ]
    for name, title in (("now", "NOW"), ("next", "NEXT"), ("blocked", "BLOCKED"), ("delivered", "DELIVERED")):
        lines.append(section(title, report.section(name)))
    code = verdict(report)
    label = {OK: "OK", NOT_OK: "NOT-OK", CANNOT_ASSESS: "CANNOT-ASSESS"}[code]
    lines.append(f"  verdict: {label} (exit {code})")
    return "\n".join(lines)


# --- the command ------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fleet-report", description=__doc__)
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit the typed report as JSON, with the exit code as the verdict",
    )
    parser.add_argument(
        "--root",
        default=None,
        help="repository root to read state from (default: this checkout)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    state = read_state(args.root)
    report = report_from(state)
    if report is None:
        print(
            "report: CANNOT-ASSESS \u2014 no wave plan and no board snapshot to report on "
            f"(looked in {state['sources']['waves']['path']} and {state['sources']['board']['path']})",
            file=sys.stderr,
        )
        return CANNOT_ASSESS
    if args.json:
        payload = report.to_json()
        problems = validate_report(payload)
        if problems:
            for problem in problems:
                print(f"report: CANNOT-ASSESS \u2014 {problem}", file=sys.stderr)
            return CANNOT_ASSESS
        print(json.dumps(payload, indent=2, sort_keys=True))
        return verdict(report)
    print(render(report))
    return verdict(report)


if __name__ == "__main__":
    raise SystemExit(main())
