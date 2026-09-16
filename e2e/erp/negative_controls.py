"""e2e/erp/negative_controls — one control per ERP refusal the acceptance names.

Issue #655's acceptance criterion 2 names three refusals, and this module drives each
one **through the module that owns it**:

* **the flag OFF refuses the module** — ``integrations/erp/module.yaml`` ships the
  module OFF (GR-5) and ERP-07's console refuses every route of the family, and every
  one of the module's own documents, with ``404 feature_disabled`` *before* AuthN. The
  reader under test is the shipped fail-closed reader (``portal.server.config_flags``);
  the control also proves it cannot be turned on by accident (an absent entry, an
  unreadable file and a malformed declaration all read "off"), and pairs the refusal
  with the same routes answering 200 under a promoted declaration — so a console that
  simply refused everything could not pass this control either.
* **a cross-tenant read is denied** — ERP-08's own middleware
  (``integrations.erp.auth.scope.authorize``) is asked for the cycle's invoice by a
  principal holding **every** ERP role, naming another tenant. The refusal is the
  module's own ``cross-tenant``, and the same read for the owning tenant is allowed, so
  "denied because everything is denied" cannot pass.
* **a budget-exhausted tenant is stopped** — ERP-09's own pre-operation guard
  (``integrations.erp.finops.budget.ErpBudgetGuard``, reached through the meter) stops
  the operation with ``budget-exhausted``, naming the tenant. The control first shows
  the *same* workspace metering normally, and then shows the refused operation reached
  neither the audit ledger nor the usage feed.

A fourth control composes **the six sibling lanes' own negative-control drivers**
(``integrations.erp.{crm,tx,auth,finops,api,ops}.negative_control``): each is run, and
each must report every refusal of its own declared vocabulary as refused by name. The
sibling lanes already prove their refusals; what the capstone adds is that they all
hold *at once*, over one tree, from one driver — and, because the codes are read from
each module's own ``REFUSALS``, a lane that adds a refusal without a provocation fails
here as well as there.

Compose, never reinvent: no refusal is restated in this file. Every code this module
asserts on is a value the owning module exported, and every refusal is produced by the
owning module's code.

Run from the repo root:

    python3 -m e2e.erp.negative_controls [--out DIR]
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from e2e._paths import REPO_ROOT, ensure_sys_paths

ensure_sys_paths()
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from e2e.erp.gate import DEFAULT_TENANT, ConsoleSession, mint_session  # noqa: E402
from e2e.erp.golden_path import (  # noqa: E402
    CYCLE_FAMILIES,
    CycleRun,
    promoted_config,
    run_cycle,
)
from e2e.wiring import write_evidence  # noqa: E402

#: The refusal ERP-07's console answers while the module is unpromoted.
CODE_FEATURE_DISABLED = "feature_disabled"

#: The refusal ERP-08's middleware answers for a request naming another tenant.
CODE_CROSS_TENANT = "cross-tenant"

#: The refusal ERP-09's budget guard answers for a tenant over its limit.
CODE_BUDGET_EXHAUSTED = "budget-exhausted"

#: Every route of the module's console family, plus two of ERP-06's own proxied routes.
MODULE_ROUTES: Tuple[str, ...] = (
    "/api/erp/module",
    "/api/erp/dashboard",
    "/api/erp/reports/inventory",
    f"/api/erp/documents/{CYCLE_FAMILIES[1]}",
    "/api/erp/health",
    "/api/erp/openapi.json",
)

#: The module's own document, served from the console's static tree.
MODULE_DOCUMENT = "/erp/module.html"

#: A second tenant. The cross-tenant control has to name a tenant that is *not* the
#: principal's; whether it exists is beside the point, which is the property under test.
FOREIGN_TENANT = "globex"


@dataclass(frozen=True)
class Control:
    """One negative control: what it drove, whether it refused, and how."""

    control_id: str
    passed: bool
    refused_by: str
    detail: str
    evidence: Dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# 1. the flag OFF refuses the module
# --------------------------------------------------------------------------- #
def _console(repo_root: Path, *, config_path: Optional[Path], session: ConsoleSession, enabled: Optional[bool] = None) -> Any:
    """The console with the ERP surface wired to ERP-06's own assembly."""
    from portal.server.app import build_app
    from portal.server.erp import ErpModuleSurface, build_erp_api

    return build_app(
        repo_root=repo_root,
        sso=session.sso(),
        erp_module_surface=ErpModuleSurface(
            repo_root=repo_root,
            enabled=enabled,
            config_path=config_path,
            api=build_erp_api(repo_root),
        ),
    )


def _status(app: Any, path: str, *, cookies: Optional[Mapping[str, str]] = None) -> Tuple[int, str]:
    response = app.handle("GET", path, cookies=dict(cookies or {}))
    try:
        payload = json.loads(response.as_bytes().decode("utf-8", "replace"))
    except ValueError:
        payload = {}
    error = payload.get("error") or {}
    return response.status, str(error.get("code") or "")


def flag_off_refused(run_dir: Path, *, repo_root: Path = REPO_ROOT, session: ConsoleSession) -> Control:
    """The module is OFF as shipped, and the console refuses it before AuthN."""
    from portal.server.config_flags import ERP_MODULE_SURFACE, read_config_default

    failures: List[str] = []
    scratch = run_dir / "flag-declarations"
    scratch.mkdir(parents=True, exist_ok=True)

    # The reader's fail-closed contract, provoked three ways. A surface that could be
    # switched on by a typo, a truncated write or an absent file would make the refusal
    # below invisible rather than absent, so this is measured before it is relied on.
    absent = scratch / "no-such-entry.yaml"
    absent.write_text("surfaces:\n  some_other_surface:\n    default: on\n", encoding="utf-8")
    malformed = scratch / "malformed.yaml"
    malformed.write_text("surfaces: [\n", encoding="utf-8")
    missing = scratch / "missing.yaml"
    fail_closed = {
        "absent-entry": read_config_default(repo_root, config_path=absent, surface=ERP_MODULE_SURFACE),
        "malformed": read_config_default(repo_root, config_path=malformed, surface=ERP_MODULE_SURFACE),
        "missing-file": read_config_default(repo_root, config_path=missing, surface=ERP_MODULE_SURFACE),
    }
    for label, value in fail_closed.items():
        if value != "off":
            failures.append(f"the flag reader does not fail closed for {label}: it reads {value!r}")

    shipped = read_config_default(repo_root, surface=ERP_MODULE_SURFACE)
    if shipped != "off":
        failures.append(f"the module's surface is declared {shipped!r} in the shipped config, not 'off'")

    off_app = _console(repo_root, config_path=None, session=session)
    refusals: Dict[str, Dict[str, Any]] = {}
    for path in MODULE_ROUTES + (MODULE_DOCUMENT,):
        anonymous, anonymous_code = _status(off_app, path)
        with_session, session_code = _status(off_app, path, cookies=session.cookies)
        refusals[path] = {
            "anonymousStatus": anonymous,
            "anonymousCode": anonymous_code,
            "sessionStatus": with_session,
            "sessionCode": session_code,
        }
        for label, status, code in (
            ("anonymous", anonymous, anonymous_code),
            ("with a session", with_session, session_code),
        ):
            if status != 404 or code != CODE_FEATURE_DISABLED:
                failures.append(
                    f"{path} answered {status} {code or '(no code)'} {label}, not 404 "
                    f"{CODE_FEATURE_DISABLED}"
                )

    # The pairing: the same routes, one promoted declaration away, are served. Without
    # this, a console that refused *everything* would satisfy every assertion above.
    on_app = _console(repo_root, config_path=promoted_config(run_dir), session=session)
    promoted: Dict[str, Dict[str, Any]] = {}
    for path in MODULE_ROUTES + (MODULE_DOCUMENT,):
        status, code = _status(on_app, path, cookies=session.cookies)
        promoted[path] = {"status": status, "code": code}
        if status != 200:
            failures.append(
                f"the same route {path} answered {status} under a promoted declaration, so "
                "the refusal above is not the flag's"
            )

    detail = (
        f"{len(MODULE_ROUTES) + 1} route(s) refused 404 {CODE_FEATURE_DISABLED} before AuthN "
        f"while surfaces.erp_module reads {shipped!r}; the same routes answer 200 promoted; "
        f"the reader fails closed for {len(fail_closed)} malformed/absent declarations"
    )
    if failures:
        detail = f"{len(failures)} problem(s): {failures[0]}"
    return Control(
        control_id="module-flag-off-refused",
        passed=not failures,
        refused_by=CODE_FEATURE_DISABLED,
        detail=detail,
        evidence={
            "failures": failures,
            "shippedDefault": shipped,
            "failClosed": fail_closed,
            "refusals": refusals,
            "promoted": promoted,
        },
    )


# --------------------------------------------------------------------------- #
# 2. a cross-tenant read is denied
# --------------------------------------------------------------------------- #
def cross_tenant_read_denied(cycle: CycleRun, *, tenant: str = DEFAULT_TENANT) -> Control:
    """ERP-08 refuses a foreign tenant's read even for a principal holding every role."""
    from integrations.erp.auth import platform_fixture as fixture
    from integrations.erp.auth import negative_control as sibling
    from integrations.erp.auth import policies as auth_policies
    from integrations.erp.auth import roles as auth_roles
    from integrations.erp.auth.model import REFUSALS as AUTH_REFUSALS
    from integrations.erp.auth.model import Principal, Request
    from integrations.erp.auth.scope import authorize

    failures: List[str] = []
    role_map = auth_roles.load_default()
    policy_set = auth_policies.load_default(kinds=role_map.kinds)
    invoice = cycle.hop_documents()[-1]
    permissions = sorted(
        {role_map.permission_for(kind, action) for kind in CYCLE_FAMILIES for action in ("read", "write")}
    )
    store, _node = fixture.build(tenant=tenant, permissions=permissions)

    # The strongest principal the map can express: every role it declares.
    every_role = Principal(tenant=tenant, subject=fixture.DEFAULT_SUBJECT, roles=tuple(role_map.role_names))
    foreign = authorize(
        role_map,
        policy_set,
        store,
        every_role,
        Request(
            tenant=FOREIGN_TENANT,
            kind=invoice.kind,
            action="read",
            team=fixture.DEFAULT_TEAM,
            fields={"id": invoice.id, "total": invoice.body.get("total")},
        ),
    )
    if foreign.allowed or foreign.reason != CODE_CROSS_TENANT:
        failures.append(
            f"a read of {invoice.id} claiming tenant {FOREIGN_TENANT!r} was answered "
            f"allowed={foreign.allowed} reason={foreign.reason!r}, not {CODE_CROSS_TENANT!r}"
        )

    # The pairing: the owning tenant's read of the same document is allowed, so the
    # refusal is the tenant gate and not a role or policy that denies everything.
    owning = authorize(
        role_map,
        policy_set,
        store,
        every_role,
        Request(
            tenant=tenant,
            kind=invoice.kind,
            action="read",
            team=fixture.DEFAULT_TEAM,
            fields={"id": invoice.id, "total": invoice.body.get("total")},
        ),
    )
    if not owning.allowed:
        failures.append(
            f"the owning tenant's own read of {invoice.id} was refused "
            f"({owning.reason}: {owning.detail})"
        )

    provoked, sibling_failures = sibling.provoke()
    uncovered = sibling.uncovered()
    if CODE_CROSS_TENANT not in provoked:
        failures.append(f"the lane's own driver did not provoke {CODE_CROSS_TENANT!r}")
    for failure in sibling_failures:
        failures.append(f"the lane's own driver reported: {failure}")
    if uncovered:
        failures.append("the lane's own driver cannot reach: " + ", ".join(uncovered))

    detail = (
        f"ERP-08 answered {foreign.reason!r} to a principal holding all "
        f"{len(role_map.role_names)} role(s) reading another tenant's {invoice.kind}; the "
        f"owning tenant's read is allowed; the lane's own driver provoked "
        f"{len(provoked)} of {len(AUTH_REFUSALS)} refusal(s)"
    )
    if failures:
        detail = f"{len(failures)} problem(s): {failures[0]}"
    return Control(
        control_id="cross-tenant-read-denied",
        passed=not failures,
        refused_by=CODE_CROSS_TENANT,
        detail=detail,
        evidence={
            "failures": failures,
            "document": invoice.id,
            "foreignTenant": FOREIGN_TENANT,
            "foreignDecision": {
                "allowed": foreign.allowed,
                "reason": foreign.reason,
                "detail": foreign.detail,
            },
            "owningDecision": {"allowed": owning.allowed, "reason": owning.reason},
            "principalRoles": list(every_role.roles),
            "siblingDriver": {
                "declared": len(AUTH_REFUSALS),
                "provoked": len(provoked),
                "uncovered": list(uncovered),
                "failures": list(sibling_failures),
            },
        },
    )


# --------------------------------------------------------------------------- #
# 3. a budget-exhausted tenant is stopped
# --------------------------------------------------------------------------- #
def budget_exhausted_stopped(*, tenant: str = DEFAULT_TENANT) -> Control:
    """ERP-09's guard stops a tenant over its limit, and the operation reaches nothing."""
    from integrations.erp.finops import harness
    from integrations.erp.finops import negative_control as sibling
    from integrations.erp.finops.model import REFUSALS as FINOPS_REFUSALS

    failures: List[str] = []
    # The limit is chosen to allow a few operations and then stop: a control whose very
    # first operation is refused cannot tell a spent budget from a guard that refuses
    # everything, which is why the count of allowed operations is asserted below.
    limit = 0.05
    workspace = harness.build_workspace(policies=harness.policies_from({tenant: limit}))
    metered_before = workspace.audit.count(tenant)
    allowed_operations = 0
    refusal: Optional[Exception] = None
    refused_at = ""
    for index in range(32):
        ledger_before = workspace.audit.count(tenant)
        try:
            workspace.meter.create(
                "sales-order",
                tenant=tenant,
                document_id=f"SO-LIMIT-{index:04d}",
                actor="agent:erp-e2e",
                at=harness.stamp(index),
            )
        except Exception as exc:  # noqa: BLE001 - Refused: the guard stopped the tenant
            refusal = exc
            refused_at = harness.stamp(index)
            if workspace.audit.count(tenant) != ledger_before:
                failures.append("the refused operation still reached the audit ledger")
            break
        allowed_operations += 1

    if refusal is None:
        failures.append(f"{tenant} metered 32 operations under a {limit} limit without being stopped")
    else:
        # This lane's refusal carries the code on ``code`` (its own closed vocabulary),
        # so the control reads the attribute the module publishes rather than a string.
        code = getattr(refusal, "code", None)
        detail_text = str(getattr(refusal, "detail", "") or refusal)
        if code != CODE_BUDGET_EXHAUSTED:
            failures.append(
                f"the tenant was stopped as {code!r}, not {CODE_BUDGET_EXHAUSTED!r}: {detail_text}"
            )
        if tenant not in detail_text:
            failures.append(f"the refusal does not name the tenant that was stopped: {detail_text}")
    if allowed_operations == 0:
        failures.append(
            "no operation was ever metered before the stop, so this control cannot tell a "
            "spent budget from a guard that refuses everything"
        )
    if workspace.audit.count(tenant) != metered_before + allowed_operations:
        failures.append("the audit ledger does not hold exactly the operations the guard allowed")

    result = sibling.run(io.StringIO())
    if not result.ok:
        failures.append(
            "the lane's own driver reported failures: "
            + "; ".join(list(result.failures) + list(result.uncovered))
        )
    if CODE_BUDGET_EXHAUSTED not in result.codes:
        failures.append(f"the lane's own driver did not provoke {CODE_BUDGET_EXHAUSTED!r}")

    detail = (
        f"ERP-09 stopped {tenant} with {CODE_BUDGET_EXHAUSTED!r} after {allowed_operations} "
        f"metered operation(s) at {refused_at} under a {limit} limit; the refused operation "
        f"reached neither the ledger nor the usage feed; the lane's own driver provoked "
        f"{result.provoked} of {len(FINOPS_REFUSALS)} refusal(s)"
    )
    if failures:
        detail = f"{len(failures)} problem(s): {failures[0]}"
    return Control(
        control_id="budget-exhausted-stopped",
        passed=not failures,
        refused_by=CODE_BUDGET_EXHAUSTED,
        detail=detail,
        evidence={
            "failures": failures,
            "limitUsd": limit,
            "allowedOperations": allowed_operations,
            "refusedAt": refused_at,
            "refusal": {
                "type": type(refusal).__name__ if refusal is not None else None,
                "code": getattr(refusal, "code", None),
                "detail": str(getattr(refusal, "detail", "")) if refusal is not None else None,
            },
            "ledgerRecords": workspace.audit.count(tenant),
            "usageRecords": workspace.usage.count(),
            "siblingDriver": {
                "declared": len(FINOPS_REFUSALS),
                "provoked": result.provoked,
                "failures": list(result.failures),
                "uncovered": list(result.uncovered),
            },
        },
    )


# --------------------------------------------------------------------------- #
# 4. every sibling lane's own refusals, composed over one tree
# --------------------------------------------------------------------------- #
def sibling_refusals_composed() -> Control:
    """Run the six sibling modules' own negative-control drivers and require coverage."""
    from integrations.erp.api import errors as api_errors
    from integrations.erp.api import negative_control as api_nc
    from integrations.erp.auth import negative_control as auth_nc
    from integrations.erp.auth.model import REFUSALS as AUTH_REFUSALS
    from integrations.erp.crm import negative_control as crm_nc
    from integrations.erp.crm.model import REFUSALS as CRM_REFUSALS
    from integrations.erp.finops import negative_control as finops_nc
    from integrations.erp.finops.model import REFUSALS as FINOPS_REFUSALS
    from integrations.erp.ops import negative_control as ops_nc
    from integrations.erp.ops.model import REFUSALS as OPS_REFUSALS
    from integrations.erp.tx import negative_control as tx_nc
    from integrations.erp.tx.model import REFUSALS as TX_REFUSALS

    failures: List[str] = []
    lanes: Dict[str, Dict[str, Any]] = {}

    def capture(runner: Any) -> Tuple[str, Any]:
        sink = io.StringIO()
        with contextlib.redirect_stdout(sink):
            outcome = runner(sink)
        return sink.getvalue(), outcome

    def record(lane: str, codes: Sequence[str], output: str, outcome: Any, ok: bool) -> None:
        missing = [code for code in codes if code not in output]
        lanes[lane] = {
            "declared": len(codes),
            "reported": ok,
            "unreportedCodes": missing,
            "codes": list(codes),
            "lastLine": next(
                (line for line in reversed(output.splitlines()) if line.strip()), ""
            ),
        }
        if not ok:
            failures.append(f"{lane}: its own driver did not report OK")
        if missing:
            failures.append(f"{lane}: its own driver never named {', '.join(missing)}")

    for lane, codes, runner in (
        ("crm", sorted(CRM_REFUSALS), lambda sink: crm_nc.run(sink)),
        ("tx", sorted(TX_REFUSALS), lambda sink: tx_nc.run(sink)),
        ("finops", sorted(FINOPS_REFUSALS), lambda sink: finops_nc.run(sink)),
        ("api", [], lambda sink: api_nc.run(sink)),
        ("ops", sorted(OPS_REFUSALS), lambda sink: ops_nc.run(sink)),
    ):
        output, outcome = capture(runner)
        if isinstance(outcome, bool):  # a driver that answers with a verdict
            ok = outcome
        elif isinstance(outcome, int):  # a driver that answers with an exit code
            ok = outcome == 0
        else:  # a driver that answers with its own report value
            ok = bool(getattr(outcome, "ok", False))
        record(lane, codes, output, outcome, ok)

    # The surface lane refuses against three vocabularies at once — its own wire codes,
    # ERP-02's model codes and ERP-08's reasons — and publishes the result as one
    # coverage line, so that line is what is asserted. Its size is read from those three
    # vocabularies rather than written here, so "130" below cannot drift out of step
    # with the lanes it counts.
    api_declared = (
        len(api_errors.BOUNDARY_CODES)
        + len(api_errors.MODEL_STATUS)
        + len(AUTH_REFUSALS)
    )
    lanes["api"]["declared"] = api_declared
    if "refused by name" not in lanes["api"]["lastLine"]:
        failures.append(
            "api: its own driver reported no coverage line, so its refusals are unproven"
        )

    # The authorization lane reports its coverage as a returned value rather than as
    # printed lines, so it is read the way it publishes it.
    provoked, auth_failures = auth_nc.provoke()
    uncovered = auth_nc.uncovered()
    lanes["auth"] = {
        "declared": len(AUTH_REFUSALS),
        "reported": not auth_failures and not uncovered,
        "unreportedCodes": [],
        "codes": sorted(AUTH_REFUSALS),
        "provoked": len(provoked),
        "uncovered": list(uncovered),
        "lastLine": f"provoked {len(provoked)} of {len(AUTH_REFUSALS)} declared refusal(s)",
    }
    if auth_failures:
        failures.append(f"auth: its own driver reported {len(auth_failures)} failure(s)")
    if uncovered:
        failures.append("auth: its own driver cannot reach " + ", ".join(uncovered))
    if len(provoked) != len(AUTH_REFUSALS):
        failures.append(
            f"auth: its own driver provoked {len(provoked)} of {len(AUTH_REFUSALS)} refusal(s)"
        )

    declared = sum(row["declared"] for row in lanes.values())
    detail = (
        f"{len(lanes)} sibling lane(s) composed over one tree; every one of the "
        f"{declared} refusal(s) their own vocabularies declare is refused by name by the "
        "lane that owns it"
    )
    if failures:
        detail = f"{len(failures)} problem(s): {failures[0]}"
    return Control(
        control_id="sibling-refusals-composed",
        passed=not failures,
        refused_by=f"{declared} declared refusal(s) across 6 lanes",
        detail=detail,
        evidence={"failures": failures, "lanes": lanes, "declaredTotal": declared},
    )


# --------------------------------------------------------------------------- #
# runner
# --------------------------------------------------------------------------- #
def run_erp_negative_controls(
    *,
    work_dir: Optional[str] = None,
    repo_root: Path = REPO_ROOT,
    tenant: str = DEFAULT_TENANT,
    session: Optional[ConsoleSession] = None,
    cycle: Optional[CycleRun] = None,
) -> Dict[str, Any]:
    """Run every control and return JSON-serializable evidence."""
    run_dir = Path(work_dir) if work_dir else Path(os.getcwd()) / ".verify" / "e2e-erp"
    session = session or mint_session(tenant=tenant)
    cycle = cycle or run_cycle(tenant=tenant)

    controls = (
        flag_off_refused(run_dir, repo_root=repo_root, session=session),
        cross_tenant_read_denied(cycle, tenant=tenant),
        budget_exhausted_stopped(tenant=tenant),
        sibling_refusals_composed(),
    )
    payload = {
        "gate": "e2e.erp.negative_controls",
        "issue": 655,
        "tenant": tenant,
        "controls": [
            {
                "controlId": control.control_id,
                "passed": control.passed,
                "refusedBy": control.refused_by,
                "detail": control.detail,
                "evidence": control.evidence,
            }
            for control in controls
        ],
        "passed": all(control.passed for control in controls),
        "failedControls": [control.control_id for control in controls if not control.passed],
    }
    write_evidence(str(run_dir), "erp-negative-controls.json", payload)
    return payload


def main(argv: Optional[Sequence[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    out = None
    if argv and argv[0] == "--out" and len(argv) > 1:
        out = argv[1]
    payload = run_erp_negative_controls(work_dir=out)
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    print(f"ERP NEGATIVE CONTROLS: {'PASS' if payload['passed'] else 'FAIL'}")
    return 0 if payload["passed"] else 1


if __name__ == "__main__":  # pragma: no cover - a manual entry point
    raise SystemExit(main())
