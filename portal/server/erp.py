"""portal.server.erp — the ERP module's portal surface (ERP-07, issue #652).

WHY this exists. EPIC #645's module is indexer-fed by construction: ERP-01
declares it, ERP-02 owns the document model, ERP-06 serves it as a REST
contract. A tenant operator still cannot *use* a library or read a JSON
document, so the module needs a serving half in the console — and the danger
that half creates is the one every other adapter here names: a projection that
keeps its own copy of the knowledge. A second family list, a second set of
lifecycle states, a second form's field names would each be a store that drifts
from the indexer the module is defined by.

**This adapter holds no ERP knowledge of its own.** Everything it serves is read
from the two declarations the module already has, at request time:

* the **declaration** — ``integrations/erp/module.yaml``, read through ERP-01's
  own loader (``integrations.erp.catalog.model.load_manifest``), so the module's
  identity, its flag, its catalogue pointers, its indexer globs and its GR-10
  provenance are the manifest's, never a paraphrase. Unreadable is
  CANNOT-ASSESS (503), never an empty module and never a fabricated one.
* the **contract** — ERP-06's own document, from
  ``GET /v1/erp/openapi.json``: the kind vocabulary (``x-erp-model``), each
  family's declared states and legal moves (``x-erp-transitions``), the refusal
  vocabulary (``x-erp-errors``), the authorization contract (``x-erp-identity``)
  and the schema sources (``x-erp-schema-sources``). The front end renders its
  filter lists, its state machine and its fields from *those*, which is what
  makes "no projection re-derives knowledge" a property of the code rather than
  a promise in a docstring.

**Every document verb is a proxy, not a second implementation.** Reads, writes
and transitions are handed to ERP-06's own transport-free ``Surface.handle``
with its own envelope, and that envelope is returned **verbatim** — the same
``ok``/``status``/``requestId``/``data``/``error`` object, the same code, the
same status. The console adds a session in front (the operator is
authenticated) and adds nothing else: the ERP *authorization* decision is
ERP-08's, taken inside the surface this module mounts, and a refusal a caller
receives here is the refusal ERP-08 made — one contract, not two dialects.

**The role is not this module's to choose, and it types none.** ERP-08 owns the
role vocabulary, and the declaration source a deployment authorizes against is
the indexer-fed catalogue that lands with ERP-08 — not this lane. Until then the
only assembly that exists is the one ERP-06 ships for its own offline
operation, so that is what the default wiring builds, and the role it runs as is
**ERP-06's own declared default**, read from that lane's signature rather than
typed here. The console session therefore authenticates the *operator*; it never
becomes an ERP role, because mapping a console role onto an ERP role would be
this module inventing an authorization rule (the acceptance criteria's third
clause). ``tests/test_erp_module_surface.py`` proves the absence mechanically.

The surface ships **feature-flag-gated OFF** (GR-5): the switch is declared in
the portal's own ``portal/config/feature-flags.yaml`` and read here through
``portal.server.config_flags``, and while it is off the app refuses every
``/api/erp/*`` route and the module's own documents **before authentication** —
an unpromoted surface is absent, not merely unauthorised (the ``chat`` /
``live_bridge`` / ``operator_terminal`` precedent).


---knowledge---
module_id: portal.server.erp
system: portal
app: server
solution_class: pattern
patterns: [delegate-never-re-derive, verbatim-proxy, no-second-declaration]
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [ErpModuleSurface, ErpApi, build_erp_api, ErpModuleError]
invariants: "every document verb proxies ERP-06's Surface.handle and returns its envelope verbatim; the adapter holds no ERP knowledge of its own"
gotchas: "the ERP authorization decision is ERP-08's, taken inside the mounted surface - one contract, not two dialects"
related: ["#652", "#645"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from portal.server.config_flags import ERP_MODULE_SURFACE, surface_enabled

__all__ = [
    "MODULE_MANIFEST_RELATIVE",
    "PROXIED_PREFIX",
    "REPORTS",
    "SCHEMA",
    "ErpApi",
    "ErpModuleError",
    "ErpModuleSurface",
    "build_erp_api",
]

#: The module's manifest — ERP-01's declaration, read through ERP-01's own loader.
MODULE_MANIFEST_RELATIVE = Path("integrations") / "erp" / "module.yaml"

#: The route prefix this module proxies to: ERP-06's own contract.
PROXIED_PREFIX = "/v1/erp"

#: The schema tag every document this module composes carries.
SCHEMA = "ao.portal-erp-module/v1"

#: The reports the module offers. A report is a *projection of the contract and
#: the API's own answers* — never a third store, and never a figure this module
#: computes from anything but a served document.
REPORTS: Tuple[str, ...] = ("inventory", "lifecycle", "contract")

#: The keys the module's own declaration surfaces, read from the manifest
#: verbatim. Listing them is not restating them: each value is copied through.
_MANIFEST_IDENTITY: Tuple[str, ...] = (
    "id",
    "name",
    "mandatory",
    "data_source",
    "epic",
    "issue",
)


class ErpModuleError(Exception):
    """An HTTP-addressable module error (envelope ``error.code``)."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def _api_role() -> str:
    """The ERP role the default wiring runs as: **ERP-06's own declared default**.

    Deliberately read out of the ERP-06 lane's signature rather than typed here.
    The role vocabulary is ERP-08's, and a copy of one of its names in this file
    would be exactly the kind of second declaration the module forbids — this
    module must be *able* to be checked for the absence of a role name.
    """
    from integrations.erp.api import fixtures

    default = inspect.signature(fixtures.declarations).parameters["role"].default
    if not isinstance(default, str) or not default:
        raise ErpModuleError(
            503,
            "role_unavailable",
            "ERP-06 declares no default role for its own declarations; the "
            "surface cannot be authorized without one",
        )
    return default


class ErpApi:
    """ERP-06's surface, ERP-08's principal, and the declarations between them.

    A thin holder, on purpose: the adapter must not be able to reach past
    ``handle`` into the store or the model, because every path around the
    surface's own ordering (route → kind → caller → authorization → body →
    model) is a path around the decision that refuses a request.
    """

    def __init__(self, *, surface: Any, principal: Any, role: str) -> None:
        self._surface = surface
        self._principal = principal
        self.role = role

    def handle(
        self, method: str, path: str, *, body: Any = None
    ) -> Dict[str, Any]:
        """One request through ERP-06's own entry point, principal supplied."""
        return self._surface.handle(
            method, path, principal=self._principal, body=body
        )


def build_erp_api(
    repo_root: Path | str,
    *,
    role: Optional[str] = None,
    api: Optional[Any] = None,
) -> ErpApi:
    """The ERP-06 surface this module mounts, assembled the way ERP-06 ships it.

    ERP-06 is transport-free by design — its own docstring names this adapter as
    the expected mounting point — so the proxy is in-process and needs no socket,
    no network and no deployment. The declarations come from ERP-06's own
    assembly (``integrations.erp.api.fixtures``), which derives them from the
    model and loads them through ERP-08's own loaders; the lane records that the
    indexer-fed ERP-08 catalogue replaces their *source* when it lands, with no
    change to this code.

    ``role`` and ``api`` are injectable so a caller (a test, or the ERP-08 lane
    once its catalogue lands) can supply a different authorization posture
    without editing this module.
    """
    if api is not None:
        return api

    from integrations.erp.api import fixtures
    from integrations.erp.api import surface as surface_module
    from integrations.erp.auth.model import Principal

    resolved_role = role if role is not None else _api_role()
    model = fixtures.model()
    declarations = fixtures.declarations(model, role=resolved_role)
    surface = surface_module.Surface(
        model=model,
        documents=fixtures.seeded_store(model),
        role_map=declarations.role_map,
        policy_set=declarations.policy_set,
        rbac_store=declarations.rbac_store,
        team=declarations.team,
        root=fixtures.repository_root(),
    )
    principal = Principal(
        tenant=declarations.tenant,
        subject=fixtures.PLATFORM_SUBJECT,
        roles=(resolved_role,),
    )
    return ErpApi(surface=surface, principal=principal, role=resolved_role)


class ErpModuleSurface:
    """Serves the ERP module's declaration, its contract and its documents.

    ``enabled`` is resolved from the portal's own flag declaration unless
    supplied explicitly (tests pass it; the server lets the declaration decide).
    ``api`` is the ERP-06 surface to mount; when it is absent the surface is
    still constructible — a route that needs it refuses with a named 503 — so a
    boot with an unreachable API serves the flag gate and an honest error rather
    than crashing.
    """

    def __init__(
        self,
        *,
        repo_root: Path | str,
        enabled: Optional[bool] = None,
        config_path: Optional[Path | str] = None,
        api: Optional[Any] = None,
    ) -> None:
        self.repo_root = Path(repo_root)
        self.config_path = Path(config_path) if config_path is not None else None
        if enabled is None:
            enabled = surface_enabled(
                self.repo_root,
                config_path=self.config_path,
                surface=ERP_MODULE_SURFACE,
            )
        self.enabled = bool(enabled)
        self._api = api

    # -- the two declarations ------------------------------------------------
    def declaration(self) -> Mapping[str, Any]:
        """ERP-01's manifest, read through ERP-01's own loader.

        A manifest that is missing or unreadable is CANNOT-ASSESS: the surface
        will not describe a module it cannot read, and it never invents one.
        """
        from integrations.erp.catalog.model import (
            MANIFEST_PATH,
            CannotAssess,
            load_manifest,
        )

        path = self.repo_root / MANIFEST_PATH
        try:
            return load_manifest(path)
        except CannotAssess as refusal:
            raise ErpModuleError(
                503,
                "declaration_unavailable",
                f"the module declaration cannot be read ({refusal}); no part of "
                "it is invented here",
            ) from None

    def contract(self) -> Mapping[str, Any]:
        """ERP-06's served document — the vocabulary the front end renders.

        Read from the API rather than from the schema directory on disk: the
        served contract is what the module *answers* with, and a projection that
        read the files directly could disagree with the surface it is projecting.
        """
        envelope = self._call("GET", f"{PROXIED_PREFIX}/openapi.json")
        data = envelope.get("data")
        if not isinstance(data, Mapping):
            raise ErpModuleError(
                503,
                "contract_unavailable",
                "the ERP-06 surface answered no contract document, so the module "
                "has no vocabulary to render",
            )
        return data

    # -- the module's own answers -------------------------------------------
    def module(self) -> dict:
        """``GET /api/erp/module`` — the module as its own declarations describe it."""
        manifest = self.declaration()
        contract = self.contract()
        return {
            "schema": SCHEMA,
            "module": {key: manifest.get(key) for key in _MANIFEST_IDENTITY},
            "declarationSource": str(MODULE_MANIFEST_RELATIVE),
            "flag": {
                "surface": ERP_MODULE_SURFACE,
                "declared": "on" if self.enabled else "off",
                "source": "portal/config/feature-flags.yaml",
            },
            "contract": {
                "path": f"{PROXIED_PREFIX}/openapi.json",
                "model": contract.get("x-erp-model"),
                "transitions": contract.get("x-erp-transitions"),
                "errors": contract.get("x-erp-errors"),
                "identity": contract.get("x-erp-identity"),
                "schemaSources": contract.get("x-erp-schema-sources"),
            },
            "indexer": {
                "dataSource": manifest.get("data_source"),
                "sources": manifest.get("indexer_sources", []),
            },
            "catalogue": manifest.get("catalogue"),
            "provenance": manifest.get("provenance"),
        }

    def dashboard(self) -> dict:
        """``GET /api/erp/dashboard`` — per-family tallies over the API's answers.

        Every figure is a count of what the API returned for this principal: the
        family vocabulary is the contract's, the counts are the collections the
        surface authorized and projected, and ``redactedFields`` is the decision's
        own omission list. Nothing here reads a second store or re-derives a
        fact the contract already states.
        """
        contract = self.contract()
        model = contract.get("x-erp-model") or {}
        transitions = contract.get("x-erp-transitions") or {}
        lifecycle = set(model.get("lifecycleKinds") or ())
        kinds = list(model.get("kinds") or ())

        families: List[dict] = []
        totals: Dict[str, int] = {}
        stateless_total = 0
        for kind in kinds:
            collection = self._collection(kind)
            items = collection.get("items") or []
            by_state: Dict[str, int] = {}
            stateless = 0
            for item in items:
                state = item.get("state")
                # A master family (a kind the contract does not list as having a
                # lifecycle) carries no `state` field at all: it is counted as
                # stateless rather than folded into an empty state name, because
                # "" is not a state the model declares.
                if isinstance(state, str) and state:
                    by_state[state] = by_state.get(state, 0) + 1
                    totals[state] = totals.get(state, 0) + 1
                else:
                    stateless += 1
                    stateless_total += 1
            moves = transitions.get(kind) or {}
            families.append(
                {
                    "kind": kind,
                    "lifecycle": kind in lifecycle,
                    "states": sorted(moves),
                    "transitions": moves,
                    "documents": len(items),
                    "byState": by_state,
                    "stateless": stateless,
                    "redactedFields": list(collection.get("redacted") or ()),
                }
            )

        return {
            "schema": SCHEMA,
            "generatedFrom": {
                "contract": f"{PROXIED_PREFIX}/openapi.json",
                "declaration": str(MODULE_MANIFEST_RELATIVE),
            },
            "families": families,
            "totals": {
                "families": len(families),
                "documents": sum(family["documents"] for family in families),
                "byState": totals,
                "stateless": stateless_total,
            },
        }

    def reports(self) -> dict:
        """``GET /api/erp/reports`` — the report catalogue, by name."""
        return {
            "schema": SCHEMA,
            "reports": [
                {"id": "inventory", "title": "Documents per family"},
                {"id": "lifecycle", "title": "State tally and declared moves"},
                {"id": "contract", "title": "Contract, refusals and schema sources"},
            ],
        }

    def report(self, name: str) -> dict:
        """``GET /api/erp/reports/<name>`` — one report, or a named 404."""
        if name not in REPORTS:
            raise ErpModuleError(
                404,
                "unknown_report",
                f"no report {name!r} (declared: {', '.join(REPORTS)})",
            )
        contract = self.contract()
        if name == "inventory":
            dashboard = self.dashboard()
            return {
                "schema": SCHEMA,
                "report": "inventory",
                "rows": [
                    {"kind": family["kind"], "documents": family["documents"]}
                    for family in dashboard["families"]
                ],
                "total": dashboard["totals"]["documents"],
            }
        if name == "lifecycle":
            dashboard = self.dashboard()
            return {
                "schema": SCHEMA,
                "report": "lifecycle",
                "rows": [
                    {
                        "kind": family["kind"],
                        "states": family["states"],
                        "transitions": family["transitions"],
                        "byState": family["byState"],
                    }
                    for family in dashboard["families"]
                ],
            }
        return {
            "schema": SCHEMA,
            "report": "contract",
            "model": contract.get("x-erp-model"),
            "errors": contract.get("x-erp-errors"),
            "schemaSources": contract.get("x-erp-schema-sources"),
        }

    # -- the proxy ----------------------------------------------------------
    def call(
        self, method: str, path: str, *, body: Any = None
    ) -> Dict[str, Any]:
        """One ERP-06 request, by path under :data:`PROXIED_PREFIX`.

        The console route hands ``/api/erp/documents/...`` here as
        ``/documents/...``; the prefix is added once and the target surface's own
        router sees exactly the path it declares. The ERP-06 *envelope* is
        returned as it is — the transport half of this adapter never rewrites a
        code, a status or a message.
        """
        target = f"{PROXIED_PREFIX}{path}"
        return self._call(method, target, body=body)

    # -- internals ----------------------------------------------------------
    def _require_api(self) -> Any:
        if self._api is None:
            raise ErpModuleError(
                503,
                "module_unavailable",
                "no ERP-06 surface is wired for this module "
                "(integrations/erp/api is not reachable)",
            )
        return self._api

    def _call(self, method: str, path: str, *, body: Any = None) -> Dict[str, Any]:
        """Call the mounted surface and let a transport failure be a named 503."""
        api = self._require_api()
        try:
            return api.handle(method, path, body=body)
        except Exception as failure:  # noqa: BLE001 - never leak a stack as a body
            raise ErpModuleError(
                503,
                "module_unavailable",
                f"the ERP-06 surface could not be reached for {method} {path}: "
                f"{type(failure).__name__}",
            ) from None

    def _collection(self, kind: str) -> Mapping[str, Any]:
        """One family's collection, through the API, or the API's own refusal.

        The refusal is *raised with the surface's own status and code* so the
        caller (and the operator reading the console) sees ERP-06's answer rather
        than a generic "the dashboard failed".
        """
        envelope = self._call("GET", f"{PROXIED_PREFIX}/documents/{kind}")
        if not envelope.get("ok"):
            error = envelope.get("error") or {}
            raise ErpModuleError(
                int(envelope.get("status") or 503),
                str(error.get("code") or "module_unavailable"),
                str(error.get("message") or f"the {kind!r} collection could not be read"),
            )
        data = envelope.get("data")
        return data if isinstance(data, Mapping) else {}
