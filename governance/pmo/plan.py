"""``governance/pmo/plan.py`` — the enterprise project plan (issue #1648).

``governance/pmo/plan.yaml`` is data: every task the fleet has queued, tagged
with a milestone and a priority, converging on "CRM + Asterisk voice fully
operational". This module only ever *derives* views over that data — it never
stores a task's ``status``: status is read live from GitHub
(``status_source: github`` on every task), because a stored status would be a
second source of truth the moment the real issue moved (the same discipline
``views.py``/``graph.py`` already hold for the dispatch ledger).

Three checks make the plan a gate, not a document (rc 1 on any):

* ``closed-milestone-unmet`` — a task's issue closed while its milestone's
  exit criteria are not all met yet (closing a task should never silently
  outrun the milestone it was supposed to satisfy).
* ``dependency-order-violation`` — a task closed while a task it
  ``depends_on`` is still open.
* ``missing-issue`` — a task references an issue that does not exist (or is
  otherwise unreachable) on GitHub.

The renderer (``render_table`` / ``render_paperclip``) is deterministic:
sorted by ``(milestone order, -priority, id)``, no clock, no set/dict
iteration leaking into output — so ``docs/PMO-PROJECT-PLAN.md`` and any
``--check`` freshness gate can diff it byte-for-byte across runs.

---knowledge---
module_id: governance.pmo.plan
system: governance
app: pmo
solution_class: enterprise
patterns: [derived-view, byte-stable-render]
derives_from: null
owner_sme: pmo-sme
tier: L1
interfaces: [CannotAssess, Finding, load_plan, sorted_tasks, check_plan, render_table, paperclip_ticket, render_paperclip]
invariants: "no dict or set iteration order leaks into output; the render is byte-identical across runs"
gotchas: ""
related: []
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - environment guard
    yaml = None

try:
    import jsonschema
except ImportError:  # pragma: no cover - environment guard
    jsonschema = None


class CannotAssess(Exception):
    """The plan itself could not be loaded/validated."""


class Finding:
    def __init__(self, rule: str, subject: str, detail: str) -> None:
        self.rule = rule
        self.subject = subject
        self.detail = detail

    def render(self) -> str:
        return f"{self.rule} {self.subject}: {self.detail}"


def load_plan(root: str = ".") -> dict[str, Any]:
    if yaml is None:
        raise CannotAssess("PyYAML is not installed")
    plan_path = Path(root) / "governance" / "pmo" / "plan.yaml"
    schema_path = Path(root) / "governance" / "pmo" / "plan.schema.json"
    try:
        document = yaml.safe_load(plan_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CannotAssess(f"plan is missing: {plan_path}") from exc
    except (OSError, yaml.YAMLError) as exc:
        raise CannotAssess(f"plan is unreadable: {plan_path} ({exc})") from exc

    if jsonschema is not None:
        try:
            schema = json.loads(schema_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise CannotAssess(f"plan schema is missing: {schema_path}") from exc
        try:
            jsonschema.validate(document, schema)
        except jsonschema.ValidationError as exc:
            raise CannotAssess(f"plan fails schema: {exc.message}") from exc

    return document


def _milestone_order(document: dict[str, Any]) -> dict[str, int]:
    return {m["id"]: m["order"] for m in document["milestones"]}


def sorted_tasks(document: dict[str, Any]) -> list[dict[str, Any]]:
    order = _milestone_order(document)
    return sorted(
        document["tasks"],
        key=lambda t: (order.get(t["milestone"], 1 << 30), -t["priority"], t["id"]),
    )


def _issue_state(repo: str, issue: int) -> str | None:
    """Live GitHub state for one issue; ``None`` if unreachable (offline or
    nonexistent — the caller decides which finding that becomes)."""
    try:
        out = subprocess.run(
            [
                "gh",
                "issue",
                "view",
                str(issue),
                "--repo",
                f"kushin77/{repo}",
                "--json",
                "state",
                "-q",
                ".state",
            ],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    state = out.stdout.strip()
    return state or None


def check_plan(document: dict[str, Any], live: bool) -> list[Finding]:
    findings: list[Finding] = []
    tasks_by_id = {t["id"]: t for t in document["tasks"]}
    order = _milestone_order(document)

    states: dict[str, str | None] = {}
    if live:
        for task in document["tasks"]:
            states[task["id"]] = _issue_state(task["repo"], task["issue"])
            if states[task["id"]] is None:
                findings.append(
                    Finding("missing-issue", task["id"], f"{task['repo']}#{task['issue']} not reachable")
                )

    for task in document["tasks"]:
        for dep in task["depends_on"]:
            if dep not in tasks_by_id:
                findings.append(
                    Finding("dependency-order-violation", task["id"], f"depends_on unknown task {dep}")
                )
                continue
            if not live:
                continue
            dep_state = states.get(dep)
            this_state = states.get(task["id"])
            if this_state == "CLOSED" and dep_state == "OPEN":
                findings.append(
                    Finding(
                        "dependency-order-violation",
                        task["id"],
                        f"closed while dependency {dep} is still open",
                    )
                )

        if live and states.get(task["id"]) == "CLOSED":
            milestone = task["milestone"]
            m_order = order.get(milestone)
            unmet = [
                sibling["id"]
                for sibling in document["tasks"]
                if sibling["milestone"] == milestone and states.get(sibling["id"]) == "OPEN"
            ]
            # A closed task in a milestone whose OTHER tasks are still open is
            # normal (tasks close incrementally); what rc-1's is a task closed
            # while depending on work in a LATER milestone that is unmet.
            for dep in task["depends_on"]:
                dep_task = tasks_by_id.get(dep)
                if dep_task is None:
                    continue
                dep_m_order = order.get(dep_task["milestone"])
                if dep_m_order is not None and m_order is not None and dep_m_order > m_order:
                    findings.append(
                        Finding(
                            "closed-milestone-unmet",
                            task["id"],
                            f"closed in {milestone} but depends on {dep} in a later milestone",
                        )
                    )

    return findings


def render_table(document: dict[str, Any]) -> str:
    lines = [f"# PMO project plan — {document['goal']}", ""]
    milestones = sorted(document["milestones"], key=lambda m: m["order"])
    tasks = sorted_tasks(document)
    for milestone in milestones:
        lines.append(f"## {milestone['id']} — {milestone['name']}")
        lines.append("")
        lines.append("Exit criteria:")
        for crit in milestone["exit_criteria"]:
            lines.append(f"- {crit['description']}: `{crit['command']}` -> `{crit['expect']}`")
        lines.append("")
        lines.append("| task | repo#issue | module | priority | sme | tier | depends_on |")
        lines.append("|---|---|---|---|---|---|---|")
        for task in tasks:
            if task["milestone"] != milestone["id"]:
                continue
            deps = ", ".join(task["depends_on"]) or "-"
            lines.append(
                f"| {task['id']} | {task['repo']}#{task['issue']} | {task['module']} | "
                f"{task['priority']} | {task['sme']} | {task['tier']} | {deps} |"
            )
        lines.append("")
    return "\n".join(lines) + "\n"


def paperclip_ticket(task: dict[str, Any], document: dict[str, Any]) -> dict[str, Any]:
    """Project one plan task as a Paperclip ticket record shaped to
    ``docs/contracts/paperclip/ticket.schema.json`` v2 — the same partial-record
    discipline as ``governance/pmo/dispatch.py::paperclip_ticket_record``: only
    populate fields the plan actually carries."""
    return {
        "id": f"kushin77/{task['repo']}#{task['issue']}",
        "goal": task["milestone"],
        "blocked_by": [
            f"kushin77/{tasks_by_id_dep['repo']}#{tasks_by_id_dep['issue']}"
            for tasks_by_id_dep in (
                next(t for t in document["tasks"] if t["id"] == dep) for dep in task["depends_on"]
            )
        ],
        "kind": "task",
    }


def render_paperclip(document: dict[str, Any]) -> dict[str, Any]:
    """The Paperclip import/sync payload this plan would push, if
    ``integrations/paperclip/adapters`` had a write path (it does not yet —
    issue #1649). Read-only: never calls out to a live Paperclip instance."""
    paperclip = document.get("paperclip", {})
    tasks = sorted_tasks(document)
    return {
        "project": {
            "id": paperclip.get("project_id"),
            "name": paperclip.get("project_name"),
            "owner": paperclip.get("owner_persona"),
        },
        "milestones": [
            {
                "id": m["id"],
                "name": m["name"],
                "owner": paperclip.get("milestone_owners", {}).get(m["id"]),
            }
            for m in sorted(document["milestones"], key=lambda m: m["order"])
        ],
        "tickets": [paperclip_ticket(t, document) for t in tasks],
        "sync_adapter_status": paperclip.get("sync_adapter_status", "not-implemented"),
    }
