"""Tenant-scoped code/KB index backend (offline, fake-data exercisable).

The compiler-accurate indexing tool shapes (definitions / references / search
/ query / freshness) are re-used from the code-indexing MCP catalog
(``.research/fleet/code-indexing/codeidx/mcp_server.py``) and the CMR indexer
(``.research/CMR/catalog/indexer/mcp_server.py``), but this gateway does NOT
implement a real indexer: each tenant's code/KB is represented as a declared,
in-memory graph (symbols per repo, modules, freshness) that the declared tools
query. Every backend is bound to exactly one tenant - a repo name that exists
only in tenant B is simply absent from tenant A's index, so no tool result can
ever leak another tenant's data (the no-cross-tenant-fallback doctrine).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Protocol

# Fidelity note surfaced on every hit envelope (mirrors codeidx).
FIDELITY_NOTE = (
    "Results are compiler-derived declarations in a per-tenant fake index: "
    "`fidelity` is `semantic` for indexed definitions/references. No code is "
    "read; only the tenant's own index graph is queried."
)


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


class KbRegistry:
    """Maps tenants to their :class:`TenantKb`.

    ``backend_for`` always returns a backend - an unknown tenant gets an empty
    KB, never another tenant's graph (fail closed).
    """

    def __init__(self) -> None:
        self._kbs: Dict[str, TenantKb] = {}

    def put(self, kb: TenantKb) -> None:
        self._kbs[kb.tenant_id] = kb

    def backend_for(self, tenant_id: str) -> MemoryKbBackend:
        kb = self._kbs.get(tenant_id)
        if kb is None:
            kb = TenantKb(tenant_id=tenant_id)
        return MemoryKbBackend(kb)

    def tenants(self) -> List[str]:
        return sorted(self._kbs)
