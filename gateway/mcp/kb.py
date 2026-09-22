"""Tenant-scoped code/KB index backend (declared fixture + a real codeidx option).

The compiler-accurate indexing tool shapes (definitions / references / search
/ query / freshness) are the published contract of the code-indexing MCP
catalog (``.research/fleet/code-indexing/codeidx/mcp_server.py``) and the CMR
indexer (``.research/CMR/catalog/indexer/mcp_server.py``). Per
[``ADR-0018``](../../docs/decision-records/ADR-0018-codeidx-consumption-and-index-authority.md)
those shapes are **consumed, never mirrored**: this module keeps the declared,
in-memory graph (symbols per repo, modules, freshness) as the **offline
fixture** - legitimate in tests / gates / demo, never a silent production
answer - and adds a **real backend option** (:class:`CodeidxBackend`) that
proxies the same tool contract to the actual ``kushin77/code-indexing``
indexer. It is **opt-in** (:data:`DEFAULT_CODEIDX_ENABLED` is off, and a
surface left off carries its own recorded owner exception under AO-GR-6);
the declared fixture answers until an operator opts a tenant in.

Every answer envelope names its provenance (``source`` = ``codeidx`` or
``declared-fixture``) and carries a fidelity note **accurate for that path**,
so a caller can always tell the real index, the fixture, and an explicit
degradation apart (ADR-0018 decision 2; the no-false-green doctrine). Each
backend is bound to exactly one tenant - a repo name that exists only in
tenant B is simply absent from tenant A's index, so no tool result can ever
leak another tenant's data (the no-cross-tenant-fallback doctrine).

---knowledge---
module_id: gateway.mcp.kb
system: gateway
app: mcp
solution_class: enterprise
patterns: [consumed-shapes, declared-fixture, flag-gated-off]
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [CodeidxBackend, KbRegistry, TenantKb, MemoryKbBackend, IndexBackend, CodeidxClient, DEFAULT_CODEIDX_ENABLED]
invariants: "the declared in-memory index answers only until an operator opts a tenant into the real backend, which is opt-in (off until opted in, the cited owner exception AO-GR-6 requires for an OFF default)"
gotchas: "the tool shapes are consumed from the published codeidx contract, never mirrored (ADR-0018)"
related: ["#20"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Protocol

# ---- provenance + the flag-gated real-backend seam (ADR-0018) -------------- #

#: The two answer provenances an envelope can carry. Every envelope names one,
#: so a caller can always tell which index answered.
SOURCE_CODEIDX = "codeidx"
SOURCE_DECLARED = "declared-fixture"

#: The codeidx tool catalog this gateway CONSUMES (published by
#: ``kushin77/code-indexing``'s MCP server). Consumed, never re-defined here
#: (ADR-0018 decision 3); the gateway's declared tools map 1:1 onto these.
CODEIDX_TOOLS = ("definitions", "references", "search", "query", "freshness")

#: Environment flag opting a deployment into the real backend. Absent or empty
#: means the compiled default, which is OFF (GR-5 / the vendor's pickup).
CODEIDX_FLAG_ENV = "AO_MCP_CODEIDX_ENABLED"
DEFAULT_CODEIDX_ENABLED = False

# Fidelity note for an answer from the declared fixture (mirrors codeidx).
FIDELITY_NOTE = (
    "Results are compiler-derived declarations in a per-tenant fake index: "
    "`fidelity` is `semantic` for indexed definitions/references. No code is "
    "read; only the tenant's own index graph is queried."
)

# Fidelity note for an answer from the real codeidx indexer.
CODEIDX_FIDELITY_NOTE = (
    "Results are compiler-derived facts from the real kushin77/code-indexing "
    "symbol index: `fidelity` is `semantic` for indexed definitions/references. "
    "The code was read by the indexer, not by this gateway."
)

# Fidelity note for an explicit degradation (real index requested, unreachable).
DEGRADED_FIDELITY_NOTE = (
    "Results DEGRADED to the per-tenant declared (fake) index because the real "
    "codeidx indexer was unreachable. This answer is NOT from a live index: "
    "`fidelity` is `semantic` for declared definitions/references; no code was "
    "read by any indexer. Treat it as advisory evidence only."
)

FIDELITY_NOTES: Dict[str, str] = {
    SOURCE_CODEIDX: CODEIDX_FIDELITY_NOTE,
    SOURCE_DECLARED: FIDELITY_NOTE,
}


def fidelity_note_for(source: str, *, degraded: bool = False) -> str:
    """The fidelity label for an answer from ``source`` (accurate per path)."""
    if degraded:
        return DEGRADED_FIDELITY_NOTE
    return FIDELITY_NOTES.get(source, DEGRADED_FIDELITY_NOTE)


def env_codeidx_enabled(environ: Optional[Mapping[str, str]] = None) -> bool:
    """Whether the real codeidx backend is opted in through the environment.

    An absent or empty value returns the compiled default (:data:`False`); the
    truthy spellings ``1``/``true``/``yes``/``on`` enable it and anything else
    leaves it OFF.
    """
    env = os.environ if environ is None else environ
    raw = str(env.get(CODEIDX_FLAG_ENV, "")).strip().lower()
    if not raw:
        return DEFAULT_CODEIDX_ENABLED
    return raw in ("1", "true", "yes", "on")


class CodeidxUnavailableError(Exception):
    """The real codeidx indexer could not answer (unreachable or malformed).

    Raised by a :class:`CodeidxClient`; :class:`CodeidxBackend` turns it into an
    *explicit* degradation to the declared fixture - never an invented result.
    """


class CodeidxClient(Protocol):
    """The consumption seam onto ``kushin77/code-indexing``'s MCP server.

    ``call_tool`` invokes one tool of the published codeidx catalog and returns
    that server's result as-is (the tool shapes are consumed, not re-shaped).
    Raise :class:`CodeidxUnavailableError` when the indexer cannot answer.
    """

    def call_tool(self, name: str, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        ...


class RecordedCodeidxClient:
    """A :class:`CodeidxClient` replaying a recorded fixture (offline).

    ``records`` maps a codeidx tool name to its recorded result; a tool named in
    ``unavailable`` (or with no record) raises :class:`CodeidxUnavailableError`,
    so the unreachable-indexer path is exercisable with no network and no
    running indexer. ``malformed`` names tools whose result is deliberately not
    an object, so the malformed-envelope refusal is exercisable too.
    """

    def __init__(
        self,
        records: Mapping[str, Any],
        *,
        unavailable: Any = (),
        malformed: Any = (),
    ) -> None:
        self._records = {name: canonical(value) for name, value in records.items()}
        self._unavailable = frozenset(unavailable)
        self._malformed = frozenset(malformed)

    def call_tool(self, name: str, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        if name in self._unavailable or name not in self._records:
            raise CodeidxUnavailableError(
                f"codeidx indexer unreachable for tool {name!r}"
            )
        if name in self._malformed:
            raise CodeidxUnavailableError(
                f"malformed codeidx envelope for tool {name!r}: expected an object"
            )
        return copy.deepcopy(self._records[name])


class ProxyCodeidxClient:
    """A :class:`CodeidxClient` proxying the real indexer over a transport.

    ``transport(name, arguments) -> result`` is the deployment's wiring to
    ``kushin77/code-indexing`` (stdio, or the eventual gateway wiring); any
    failure it raises becomes :class:`CodeidxUnavailableError`, so the live path
    degrades explicitly instead of inventing a result. The live path is never
    exercised by ``make verify`` - offline tests inject a
    :class:`RecordedCodeidxClient` instead.
    """

    def __init__(
        self, transport: Callable[[str, Mapping[str, Any]], Mapping[str, Any]]
    ) -> None:
        if transport is None:
            raise ValueError("ProxyCodeidxClient requires a transport callable")
        self._transport = transport

    def call_tool(self, name: str, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        try:
            result = self._transport(name, dict(arguments))
        except Exception as exc:  # a transport failure is degradation, not a crash
            raise CodeidxUnavailableError(
                f"codeidx indexer unreachable for tool {name!r}: {exc}"
            ) from exc
        if not isinstance(result, Mapping):
            raise CodeidxUnavailableError(
                f"malformed codeidx envelope for tool {name!r}: "
                f"expected an object, got {type(result).__name__}"
            )
        return result


def canonical(value: Any) -> Any:
    """A JSON-equivalent value with every mapping's keys sorted at every depth.

    Makes wire bytes a pure function of the answer (codeidx issue #55): the
    same logical result renders identically on every call.
    """
    if isinstance(value, Mapping):
        return {key: canonical(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [canonical(item) for item in value]
    return value


@dataclass(frozen=True)
class Symbol:
    """One indexed symbol of a tenant's repo (declared, not parsed)."""

    name: str
    repo: str
    kind: str  # e.g. function / class / method
    path: str
    line: int
    references: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class RepoIndex:
    """One tenant repo's declared index graph."""

    repo: str
    modules: List[str] = field(default_factory=list)
    symbols: Dict[str, Symbol] = field(default_factory=dict)
    commit: str = ""
    indexed_at: str = ""
    status: str = "ok"

    def add_symbol(self, symbol: Symbol) -> None:
        self.symbols[symbol.name] = symbol

    def freshness(self) -> Dict[str, Any]:
        return {
            "repo": self.repo,
            "status": self.status,
            "commit": self.commit,
            "indexedAt": self.indexed_at,
        }


@dataclass
class TenantKb:
    """One tenant's code/KB graph: exactly its own repos and symbols."""

    tenant_id: str
    repos: Dict[str, RepoIndex] = field(default_factory=dict)

    def add_repo(self, repo: RepoIndex) -> None:
        self.repos[repo.repo] = repo


class IndexBackend(Protocol):
    """The per-tenant query surface the declared tools call."""

    def definitions(self, name: str, repo: Optional[str] = None,
                    limit: int = 100) -> Dict[str, Any]: ...

    def references(self, name: str, repo: Optional[str] = None,
                   limit: int = 500) -> Dict[str, Any]: ...

    def search(self, q: str, repo: Optional[str] = None,
               limit: int = 100) -> Dict[str, Any]: ...

    def query(self, module_id: Optional[str] = None,
              repo: Optional[str] = None) -> Dict[str, Any]: ...

    def freshness(self, repo: str) -> Dict[str, Any]: ...

    def summary(self) -> Dict[str, Any]: ...


class MemoryKbBackend:
    """An :class:`IndexBackend` over one tenant's :class:`TenantKb`.

    Every lookup is scoped to ``self._kb``; a repo not present in this tenant's
    graph yields an empty answer (count 0), never another tenant's rows.
    """

    def __init__(self, kb: TenantKb) -> None:
        self._kb = kb
        self._tenant_id = kb.tenant_id

    # ------------------------------------------------------------------ #
    # result helpers
    # ------------------------------------------------------------------ #
    def _repos(self, repo: Optional[str]) -> List[RepoIndex]:
        if repo is not None:
            found = self._kb.repos.get(repo)
            return [found] if found is not None else []
        return list(self._kb.repos.values())

    @staticmethod
    def _hit(symbol: Symbol, *, kind: str) -> Dict[str, Any]:
        return {
            "name": symbol.name,
            "repo": symbol.repo,
            "kind": symbol.kind,
            "fidelity": "semantic",
            "definition": {"path": symbol.path, "line": symbol.line},
            "hitKind": kind,
        }

    @staticmethod
    def _envelope(body: Dict[str, Any]) -> Dict[str, Any]:
        body["source"] = SOURCE_DECLARED
        body["fidelity_note"] = FIDELITY_NOTE
        return canonical(body)

    # ------------------------------------------------------------------ #
    # tool surface
    # ------------------------------------------------------------------ #
    def definitions(self, name: str, repo: Optional[str] = None,
                    limit: int = 100) -> Dict[str, Any]:
        hits = [
            self._hit(symbol, kind="definition")
            for index in self._repos(repo)
            for symbol in self._kb.repos[index.repo].symbols.values()
            if symbol.name == name
        ]
        return self._envelope(
            {"count": len(hits), "hits": hits[:limit], "name": name, "repo": repo}
        )

    def references(self, name: str, repo: Optional[str] = None,
                   limit: int = 500) -> Dict[str, Any]:
        hits = []
        for index in self._repos(repo):
            symbol = self._kb.repos[index.repo].symbols.get(name)
            if symbol is None:
                continue
            for ref in symbol.references:
                hits.append(
                    {
                        "name": name,
                        "repo": index.repo,
                        "fidelity": "semantic",
                        "reference": ref,
                    }
                )
        return self._envelope(
            {"count": len(hits), "hits": hits[:limit], "name": name, "repo": repo}
        )

    def search(self, q: str, repo: Optional[str] = None,
               limit: int = 100) -> Dict[str, Any]:
        hits = []
        for index in self._repos(repo):
            for name, symbol in self._kb.repos[index.repo].symbols.items():
                if q in name:
                    hits.append(self._hit(symbol, kind="search"))
        return self._envelope(
            {"count": len(hits), "hits": hits[:limit], "q": q, "repo": repo}
        )

    def query(self, module_id: Optional[str] = None,
              repo: Optional[str] = None) -> Dict[str, Any]:
        graph: Dict[str, Any] = {"tenantId": self._tenant_id, "repos": {}}
        for index in self._repos(repo):
            entry = {
                "modules": sorted(index.modules),
                "symbols": sorted(index.symbols),
            }
            if module_id is not None:
                if module_id in index.modules:
                    entry["modules"] = [module_id]
                    entry["matchedModule"] = module_id
                else:
                    continue
            graph["repos"][index.repo] = entry
        return self._envelope({"query": graph})

    def freshness(self, repo: str) -> Dict[str, Any]:
        index = self._kb.repos.get(repo)
        if index is None:
            return self._envelope(
                {
                    "repo": repo,
                    "notIndexed": True,
                    "message": f"repo {repo!r} is not in tenant {self._tenant_id!r}'s KB",
                }
            )
        return self._envelope(index.freshness())

    def summary(self) -> Dict[str, Any]:
        return self._envelope(
            {
                "tenantId": self._tenant_id,
                "repos": len(self._kb.repos),
                "modules": sum(len(r.modules) for r in self._kb.repos.values()),
                "symbols": sum(len(r.symbols) for r in self._kb.repos.values()),
            }
        )


class CodeidxBackend:
    """An :class:`IndexBackend` served by the real codeidx indexer (flag-gated).

    Every declared tool is answered from ``client`` - the real indexer - and its
    result is returned as-is, stamped with ``source`` = ``codeidx`` and the
    real-index fidelity note. When the indexer cannot answer, the same tool is
    answered from ``declared`` and the envelope *says so*: ``source`` =
    ``declared-fixture``, ``degraded`` = true and ``degradeReason`` naming the
    tool. A caller can therefore never mistake a degraded answer for a measured
    one (ADR-0018 decision 2; the no-false-green doctrine).

    ``kb.summary`` is this gateway's own shape (it has no codeidx counterpart),
    so it is always served from the declared graph and labelled
    ``declared-fixture`` - a declared-only answer, not a substitute for a
    measured one.
    """

    def __init__(
        self, client: CodeidxClient, *, declared: "MemoryKbBackend", tenant_id: str = ""
    ) -> None:
        self._client = client
        self._declared = declared
        self._tenant_id = tenant_id

    def _serve(
        self,
        tool: str,
        arguments: Mapping[str, Any],
        fallback: Callable[[], Dict[str, Any]],
    ) -> Dict[str, Any]:
        try:
            payload = self._client.call_tool(tool, arguments)
        except CodeidxUnavailableError as exc:
            body = dict(fallback())
            body["source"] = SOURCE_DECLARED
            body["fidelity_note"] = DEGRADED_FIDELITY_NOTE
            body["degraded"] = True
            body["degradeReason"] = str(exc)
            body["degradeTool"] = tool
            return canonical(body)
        body = dict(payload)
        body["source"] = SOURCE_CODEIDX
        body["fidelity_note"] = CODEIDX_FIDELITY_NOTE
        return canonical(body)

    def definitions(self, name: str, repo: Optional[str] = None,
                    limit: int = 100) -> Dict[str, Any]:
        return self._serve(
            "definitions",
            {"name": name, "repo": repo, "limit": limit},
            lambda: self._declared.definitions(name, repo=repo, limit=limit),
        )

    def references(self, name: str, repo: Optional[str] = None,
                   limit: int = 500) -> Dict[str, Any]:
        return self._serve(
            "references",
            {"name": name, "repo": repo, "limit": limit},
            lambda: self._declared.references(name, repo=repo, limit=limit),
        )

    def search(self, q: str, repo: Optional[str] = None,
               limit: int = 100) -> Dict[str, Any]:
        return self._serve(
            "search",
            {"q": q, "repo": repo, "limit": limit},
            lambda: self._declared.search(q, repo=repo, limit=limit),
        )

    def query(self, module_id: Optional[str] = None,
              repo: Optional[str] = None) -> Dict[str, Any]:
        return self._serve(
            "query",
            {"module_id": module_id, "repo": repo},
            lambda: self._declared.query(module_id=module_id, repo=repo),
        )

    def freshness(self, repo: str) -> Dict[str, Any]:
        return self._serve(
            "freshness",
            {"repo": repo},
            lambda: self._declared.freshness(repo),
        )

    def summary(self) -> Dict[str, Any]:
        # No codeidx counterpart: a gateway-local shape, always the declared
        # graph, and labelled as such by MemoryKbBackend._envelope.
        return self._declared.summary()


class KbRegistry:
    """Maps tenants to their :class:`TenantKb` and their real-backend opt-in.

    ``backend_for`` always returns an :class:`IndexBackend` - an unknown tenant
    gets an empty KB, never another tenant's graph (fail closed).

    The real codeidx backend is **opt-in** — off until a tenant is opted in,
    (:data:`DEFAULT_CODEIDX_ENABLED`, or ``AO_MCP_CODEIDX_ENABLED`` when
    ``codeidx_enabled`` is omitted). While it is OFF every tenant is answered by
    the declared fixture. When it is ON, a tenant that has been opted in with
    :meth:`opt_in` is answered by the real indexer (degrading explicitly to the
    fixture if it is unreachable); a tenant with no real index yet keeps the
    declared fixture. No caller argument can change which tenant's client is
    selected, so the flag can never widen a tenant's data reach.
    """

    def __init__(self, *, codeidx_enabled: Optional[bool] = None) -> None:
        self._kbs: Dict[str, TenantKb] = {}
        self._clients: Dict[str, CodeidxClient] = {}
        self._codeidx_enabled = (
            env_codeidx_enabled() if codeidx_enabled is None else bool(codeidx_enabled)
        )

    @property
    def codeidx_enabled(self) -> bool:
        """Whether the real backend is opted in for this registry (OFF default)."""
        return self._codeidx_enabled

    def put(self, kb: TenantKb) -> None:
        self._kbs[kb.tenant_id] = kb

    def opt_in(self, tenant_id: str, client: CodeidxClient) -> "KbRegistry":
        """Opt one tenant into the real codeidx backend (its own index only)."""
        self._clients[tenant_id] = client
        return self

    def backend_for(self, tenant_id: str) -> IndexBackend:
        kb = self._kbs.get(tenant_id)
        if kb is None:
            kb = TenantKb(tenant_id=tenant_id)
        declared = MemoryKbBackend(kb)
        if not self._codeidx_enabled:
            return declared
        client = self._clients.get(tenant_id)
        if client is None:
            return declared
        return CodeidxBackend(client, declared=declared, tenant_id=tenant_id)

    def tenants(self) -> List[str]:
        return sorted(set(self._kbs) | set(self._clients))
