#!/usr/bin/env python3
"""engine/memory - offline operator CLI for the scoped memory store.

Issue kushin77/agent-orchestrator#25. Runs fully OFFLINE (no HTTP), against
an in-memory store or a JSON ``--store FILE`` (``FileStore``). Every command
prints deterministic JSON to stdout and exits 0; failures exit 2.

Run from the repo root (so ``engine.memory`` resolves via the PEP-420
namespace):

    python3 -m engine.memory.cli store --tenant acme --scope agent \\
        --agent coder --key deploy-style --text "..."
    python3 -m engine.memory.cli search --store /tmp/mem.json \\
        --tenant acme --agent coder --session s1 --query "deploy style"
    python3 -m engine.memory.cli export --store /tmp/mem.json --tenant acme
    python3 -m engine.memory.cli forget --store /tmp/mem.json --tenant acme \\
        --scope session --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import sys

# Allow running directly (python engine/memory/cli.py) from anywhere: reach
# the repo root through the PEP-420 namespace (engine/ has no __init__.py).
_HERE = os.path.dirname(os.path.abspath(__file__))  # .../engine/memory
_REPO_ROOT = os.path.dirname(os.path.dirname(_HERE))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from engine.memory.enrich import ContextEnricher  # noqa: E402
from engine.memory.gdpr import export_memory, forget  # noqa: E402
from engine.memory.model import MemoryKind, MemoryScope  # noqa: E402
from engine.memory.retrieval import Retriever  # noqa: E402
from engine.memory.store import FileStore, MemoryStore  # noqa: E402


def _emit(payload: dict) -> None:
    print(json.dumps(payload, sort_keys=True, indent=2, default=str))


def _store(args: argparse.Namespace) -> MemoryStore:
    if args.store:
        return FileStore(args.store)
    return MemoryStore()


def _scope(value: str) -> MemoryScope:
    return MemoryScope(value)


def _kind(value: str) -> MemoryKind:
    return MemoryKind(value)


def _scope_list(values) -> tuple:
    return tuple(_scope(v.strip()) for v in values)


def cmd_store(args: argparse.Namespace) -> int:
    store = _store(args)
    entry = store.put(
        tenant_id=args.tenant,
        scope=_scope(args.scope),
        agent_id=args.agent,
        session_id=args.session,
        key=args.key,
        text=args.text,
        kind=_kind(args.kind),
        ttl_seconds=args.ttl,
    )
    _emit({"stored": True, "memory_id": entry.memory_id,
           "scope": entry.scope.value, "key": entry.key,
           "ttl_seconds": entry.ttl_seconds, "tenant_id": args.tenant})
    return 0


def cmd_get(args: argparse.Namespace) -> int:
    store = _store(args)
    entry = store.get(args.id, tenant_id=args.tenant,
                      agent_id=args.agent, session_id=args.session)
    if entry is None:
        _emit({"found": False, "memory_id": args.id, "tenant_id": args.tenant})
        return 0
    _emit({"found": True, "entry": entry.to_dict()})
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    store = _store(args)
    retriever = Retriever(store)
    result = retriever.search(
        args.query,
        tenant_id=args.tenant,
        agent_id=args.agent,
        session_id=args.session,
        scopes=_scope_list(args.scope) if args.scope else None,
        limit=args.limit,
        min_score=args.min_score,
    )
    _emit(result.to_dict())
    return 0


def cmd_enrich(args: argparse.Namespace) -> int:
    store = _store(args)
    enricher = ContextEnricher(store)
    report = enricher.enrich(
        args.query,
        tenant_id=args.tenant,
        agent_id=args.agent,
        session_id=args.session,
        profile_scopes=args.profile_scope,
        max_tokens=args.max_tokens,
        min_relevance=args.min_relevance,
    )
    payload = report.to_dict()
    payload["block_text"] = report.block_text
    _emit(payload)
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    store = _store(args)
    scope = _scope(args.scope) if args.scope else None
    _emit(export_memory(store, tenant_id=args.tenant, agent_id=args.agent,
                        session_id=args.session, scope=scope,
                        kinds=(_kind(args.kind),) if args.kind else None))
    return 0


def cmd_forget(args: argparse.Namespace) -> int:
    store = _store(args)
    scope = _scope(args.scope) if args.scope else None
    report = forget(store, tenant_id=args.tenant, agent_id=args.agent,
                    session_id=args.session, scope=scope,
                    kinds=(_kind(args.kind),) if args.kind else None,
                    dry_run=args.dry_run)
    _emit(report.to_dict())
    return 0


def cmd_prune(args: argparse.Namespace) -> int:
    store = _store(args)
    _emit({"pruned_expired": store.prune_expired()})
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    store = _store(args)
    _emit(store.stats(tenant_id=args.tenant))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="engine.memory.cli",
        description="Offline operator CLI for the scoped memory store "
                    "(tenant/agent/session + TTL).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p) -> None:
        p.add_argument("--store", default=None,
                       help="JSON persistence file (default: transient "
                            "in-memory store)")
        p.add_argument("--tenant", required=True, help="tenant id")

    p_store = sub.add_parser("store", help="store a memory")
    common(p_store)
    p_store.add_argument("--scope", required=True,
                         choices=[s.value for s in MemoryScope])
    p_store.add_argument("--agent", default=None)
    p_store.add_argument("--session", default=None)
    p_store.add_argument("--key", required=True)
    p_store.add_argument("--text", required=True)
    p_store.add_argument("--kind", default="semantic",
                         choices=[k.value for k in MemoryKind])
    p_store.add_argument("--ttl", type=int, default=None)
    p_store.set_defaults(func=cmd_store)

    p_get = sub.add_parser("get", help="read one memory by id")
    common(p_get)
    p_get.add_argument("--agent", default=None)
    p_get.add_argument("--session", default=None)
    p_get.add_argument("--id", required=True)
    p_get.set_defaults(func=cmd_get)

    p_search = sub.add_parser("search", help="semantic search (scoped)")
    common(p_search)
    p_search.add_argument("--agent", default=None)
    p_search.add_argument("--session", default=None)
    p_search.add_argument("--query", required=True)
    p_search.add_argument("--scope", default=None, nargs="*",
                          choices=[s.value for s in MemoryScope])
    p_search.add_argument("--limit", type=int, default=8)
    p_search.add_argument("--min-score", type=float, default=None)
    p_search.set_defaults(func=cmd_search)

    p_enrich = sub.add_parser("enrich", help="build a bounded memory block")
    common(p_enrich)
    p_enrich.add_argument("--agent", default=None)
    p_enrich.add_argument("--session", default=None)
    p_enrich.add_argument("--query", required=True)
    p_enrich.add_argument("--profile-scope", default=None, nargs="*",
                          choices=["user", "session", "repository"])
    p_enrich.add_argument("--max-tokens", type=int, default=None)
    p_enrich.add_argument("--min-relevance", type=float, default=None)
    p_enrich.set_defaults(func=cmd_enrich)

    p_export = sub.add_parser("export", help="export memories (GDPR)")
    common(p_export)
    p_export.add_argument("--agent", default=None)
    p_export.add_argument("--session", default=None)
    p_export.add_argument("--scope", default=None,
                          choices=[s.value for s in MemoryScope])
    p_export.add_argument("--kind", default=None,
                          choices=[k.value for k in MemoryKind])
    p_export.set_defaults(func=cmd_export)

    p_forget = sub.add_parser("forget", help="erase memories (GDPR)")
    common(p_forget)
    p_forget.add_argument("--agent", default=None)
    p_forget.add_argument("--session", default=None)
    p_forget.add_argument("--scope", default=None,
                          choices=[s.value for s in MemoryScope])
    p_forget.add_argument("--kind", default=None,
                          choices=[k.value for k in MemoryKind])
    p_forget.add_argument("--dry-run", action="store_true")
    p_forget.set_defaults(func=cmd_forget)

    p_prune = sub.add_parser("prune", help="delete expired memories")
    common(p_prune)
    p_prune.set_defaults(func=cmd_prune)

    p_stats = sub.add_parser("stats", help="store statistics")
    common(p_stats)
    p_stats.set_defaults(func=cmd_stats)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except Exception as exc:  # surface cleanly to stderr, exit 2
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
