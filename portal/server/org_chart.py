"""portal.server.org_chart — the org-chart view adapter (issue #642, workbook-11).

WHY this exists: the workbook-1 declaration (``registry/personas/org-chart.yaml``
plus the bound ``cards/*.yaml``) says *who exists, who reports to whom, and what
each seat may spend and when it must beat*, and the workbook-6 role-health feed
(``telemetry/role_health.RoleHealthReport``) says *what each seat has actually
spent and when it last checked in*. Both are offline libraries. A tenant admin
who needs to answer "is my C-suite alive and inside budget?" cannot open one, and
the two together answer the question only if someone joins them — which is this
adapter's entire job.

Cannibalize, do not duplicate. Every field this surface serves is read through
the module that owns it:

* the nodes, their titles, the reporting edges and the single root —
  ``registry.personas.registry.load_org_chart`` (+ ``validate_org_chart``, which
  refuses a chart whose edges do not resolve or whose root is doubled);
* the declared per-node governance (``defaultModelTier``, ``monthlyBudgetCapUsd``,
  ``heartbeatSchedule``) — the chart nodes themselves, verified equal to the
  bound card's values by the registry validator, so this adapter compares
  nothing and restates no number;
* the per-role **burn** and **heartbeat status**, the cap source and the alert
  feed — ``telemetry.role_health.RoleHealthReport`` (workbook-6), which is itself
  a re-read of the same declaration plus the durable metering feed.

The adapter owns transport shape and the honesty rules of the view:

* **an undeclared budget is ``null``, never ``0``.** A role the workbook-6 feed
  does not carry reports ``null`` burn fields — a fabricated zero would read as
  "no spend, all good" for a seat that may simply never have been metered.
* **an unknown cadence is stated, not assumed.** A role with no declared
  ``heartbeatSchedule`` reports ``"unknown"`` with its source named, never a
  default interval invented here.
* **the chart is served whole or refused.** If the declaration cannot be read or
  fails its own validator, the answer is an explicitly ``unresolved`` document
  naming the defect with an empty ``nodes`` list — the view is allowed to be
  empty, it is never allowed to be invented.

The surface ships **feature-flag-gated OFF** (GR-5): the flag is declared in the
portal's own ``portal/config/feature-flags.yaml`` and read here through
``portal.server.config_flags``; while it is off the app refuses every
``/api/orgchart/*`` route before authentication.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, List, Mapping, Optional

from portal.server.config_flags import ORG_CHART_SURFACE, surface_enabled

#: The schema tag this view emits (its own, so a consumer can refuse a stranger).
SCHEMA = "ao.portal-org-chart/v1"

#: Where the workbook-1 declaration lives (repo-root relative).
ORG_CHART_RELATIVE = Path("registry") / "personas" / "org-chart.yaml"

#: The heartbeat status the view reports for a role with no declared cadence.
CADENCE_UNKNOWN = "unknown"


def _load_registry_module():
    """Import the persona registry (``registry/`` on ``sys.path`` convention).

    ``registry/personas`` imports as ``personas.registry`` when ``registry/`` is
    on ``sys.path`` (its own lane convention) or as ``registry.personas.registry``
    through the repo-root namespace. Try the namespaced path first and fall back,
    so the adapter works under either bootstrap without the caller caring.
    """
    try:
        from registry.personas import registry as module  # type: ignore
    except ImportError:  # pragma: no cover - depends on the caller's bootstrap
        from personas import registry as module  # type: ignore

    return module


class OrgChartView:
    """Joins the workbook-1 org chart to the workbook-6 role-health feed.

    ``enabled`` is resolved from the portal's own flag file unless supplied
    explicitly (tests pass it; the server lets the config decide). Both inputs
    are read lazily on first use, so a flag-OFF surface costs the process
    nothing but the config read.
    """

    def __init__(
        self,
        *,
        repo_root: Path | str,
        enabled: Optional[bool] = None,
        config_path: Optional[Path | str] = None,
        chart_path: Optional[Path | str] = None,
        health_report: Optional[Any] = None,
        health_factory: Optional[Callable[[], Any]] = None,
    ) -> None:
        self.repo_root = Path(repo_root)
        self.config_path = (
            Path(config_path) if config_path is not None else None
        )
        if enabled is None:
            enabled = surface_enabled(
                self.repo_root,
                config_path=self.config_path,
                surface=ORG_CHART_SURFACE,
            )
        self.enabled = bool(enabled)
        self.chart_path = (
            Path(chart_path)
            if chart_path is not None
            else self.repo_root / ORG_CHART_RELATIVE
        )
        self._health_report = health_report
        self._health_factory = health_factory

    # -- reads ---------------------------------------------------------------
    def chart(self) -> dict:
        """The declaration as served: ``(document, problem)`` collapsed to one.

        The chart is served verbatim (every node field is the declaration's own)
        plus the derived ``reportsTo`` edges. When the declaration cannot be read
        or fails its own validator, an explicitly ``unresolved`` document with an
        empty ``nodes`` list and a ``note`` naming the defect is served.
        """
        module = _load_registry_module()
        try:
            declaration = module.load_org_chart(self.chart_path)
        except Exception as exc:  # the registry's own error taxonomy
            return self._unresolved(f"the org chart is unreadable: {exc}")
        try:
            declaration = module.validate_org_chart(declaration)
        except Exception as exc:
            return self._unresolved(f"the org chart failed validation: {exc}")

        roles = declaration.get("roles") or []
        nodes: List[dict] = []
        for role in roles:
            if not isinstance(role, Mapping):
                continue
            nodes.append(
                {
                    "id": role.get("id"),
                    "title": role.get("title"),
                    "reportsTo": role.get("reportsTo"),
                    "defaultModelTier": role.get("defaultModelTier"),
                    "monthlyBudgetCapUsd": role.get("monthlyBudgetCapUsd"),
                    "heartbeatSchedule": role.get("heartbeatSchedule"),
                    "isRoot": role.get("id") == declaration.get("root"),
                }
            )
        return {
            "schema": SCHEMA,
            "state": "resolved",
            "id": declaration.get("id"),
            "tenant": declaration.get("tenant"),
            "version": declaration.get("version"),
            "principal": declaration.get("principal"),
            "root": declaration.get("root"),
            "nodes": nodes,
            "edges": [
                {"from": node["id"], "to": node["reportsTo"]}
                for node in nodes
                if node["reportsTo"]
            ],
            "note": "",
        }

    def health(self) -> dict:
        """The workbook-6 role-health feed, joined to the chart's node ids.

        Every figure is the ``RoleHealthReport``'s own — this method restates no
        cap, tier or cadence. The join exists so the view can render one row per
        *declared node*: a node the health feed does not cover reports a null
        burn and a ``fallback`` source, and the node still appears (a missing
        seat is visible, not dropped).
        """
        document = self.chart()
        declaration, problem = self._declaration()
        if declaration is None:
            return {
                "schema": SCHEMA,
                "state": "unresolved",
                "note": problem,
                "cadenceSource": "",
                "rows": [],
                "alerts": [],
            }
        report = self._report()
        if report is None:
            return {
                "schema": SCHEMA,
                "state": "unresolved",
                "note": (
                    "the role-health feed is unavailable — no burn or heartbeat "
                    "figure is invented here"
                ),
                "cadenceSource": "",
                "rows": [],
                "alerts": [],
            }
        snapshot = report.snapshot()
        burn_rows = {
            row.get("roleId"): row
            for row in (snapshot.get("roleBudgetBurn") or {}).get("rows") or []
        }
        beats = {
            status.get("roleId"): status
            for status in (snapshot.get("roleHeartbeat") or {}).get("statuses")
            or []
        }
        rows: List[dict] = []
        for node in document["nodes"]:
            role_id = node["id"]
            burn = burn_rows.get(role_id)
            beat = beats.get(role_id)
            # ``null`` (not 0) when the feed never metered the seat: an unmetered
            # role is unknown, not free. ``hasMeteredCalls`` is the lane's own
            # "no data" flag and rides along so a consumer can tell the two apart.
            metered = bool(burn.get("hasMeteredCalls")) if burn else False
            rows.append(
                {
                    "roleId": role_id,
                    "title": node["title"],
                    "reportsTo": node["reportsTo"],
                    "spentUsd": (
                        None if not metered else burn.get("costUsd")
                    ),
                    "monthlyCapUsd": node["monthlyBudgetCapUsd"],
                    "burnPct": None if not metered else burn.get("burnPct"),
                    "position": "unknown" if not metered else burn.get("position"),
                    "hasMeteredCalls": metered,
                    "heartbeatSchedule": node["heartbeatSchedule"],
                    "heartbeatStatus": (
                        CADENCE_UNKNOWN
                        if beat is None
                        else beat.get("status", CADENCE_UNKNOWN)
                    ),
                }
            )
        return {
            "schema": SCHEMA,
            "state": "resolved",
            "note": "",
            "cadenceSource": snapshot.get("capSource", ""),
            "month": snapshot.get("month"),
            "warnAtPct": snapshot.get("warnAtPct"),
            "rows": rows,
            "alerts": list(snapshot.get("roleAlerts") or []),
        }

    # -- internals -----------------------------------------------------------
    def _declaration(self) -> tuple[Optional[Mapping[str, Any]], str]:
        """The validated declaration, or ``(None, problem)``."""
        module = _load_registry_module()
        try:
            declaration = module.load_org_chart(self.chart_path)
            declaration = module.validate_org_chart(declaration)
        except Exception as exc:
            return None, f"the org chart is unavailable: {exc}"
        if not isinstance(declaration, Mapping):
            return None, "the org chart is not a mapping"
        return declaration, ""

    def _report(self) -> Optional[Any]:
        """The workbook-6 report, built once and cached on the view."""
        if self._health_report is not None:
            return self._health_report
        if self._health_factory is None:
            return None
        try:
            self._health_report = self._health_factory()
        except Exception:
            # A feed that cannot be built is *unavailable*, never zero spend.
            return None
        return self._health_report

    @staticmethod
    def _unresolved(problem: str) -> dict:
        return {
            "schema": SCHEMA,
            "state": "unresolved",
            "id": None,
            "tenant": None,
            "version": None,
            "principal": None,
            "root": None,
            "nodes": [],
            "edges": [],
            "note": f"{problem} — no node is invented here",
        }
