"""Intake adapter for registry/events-shaped audit records (telemetry/ledger,
issue #31).

The agent registry (``registry/events``, issue #10) maintains its own
append-only lifecycle event log with records of the shape::

    {"seq": n, "ts": "...", "event": "register", "status": "registered",
     "tenantId": "acme", "agentId": "worker-1", "actor": "admin",
     "detail": {...} | null, "prevHash": "...", "hash": "..."}

This ledger's evidence layer must reflect those lifecycle transitions on the
tenant's *audit* chain. The adapter below consumes that shape and maps it into
an audit record - it is READ-ONLY over the registry event (the input dict is
never mutated) and never imports ``registry/events`` (a separate lane's
package): it operates on the documented wire contract only, so either the
registry service pushes its event to the audit ledger, or an ETL replay reads
the registry log and feeds this adapter.

Mapping contract (owned by this lane, telemetry/ledger):

* ``tenantId``  -> the audit chain tenant (per-tenant isolation preserved).
* ``event``     -> ``action`` as ``registry.<event>`` (e.g. ``registry.register``)
  so registry lifecycle actions are namespaced apart from control-plane/model
  actions.
* ``agentId``   -> ``resource`` ``registry/agents/<agentId>`` (``registry`` when
  null) so the audited resource is queryable.
* ``hash``      -> ``evidence`` ``registry-event:<hash>`` (the registry record's
  own chain hash), giving a verifiable pointer from the audit ledger to the
  source log.
* ``actor``     -> canonicalised to ``user:``/``agent:``/``system:``; the
  registry's ``actor`` is a bare id, so ``system:registry`` is the default when
  it is absent.
* ``detail``    -> the sensitive ``payload`` (encrypted at rest by the ledger).
  Producers that consider registry ``detail`` non-sensitive may opt out with
  ``with_detail=False``.
"""

from __future__ import annotations

from typing import Any, Dict

from .store import LedgerStore


def _canonical_actor(actor: Any) -> str:
    if actor is None:
        return "system:registry"
    text = str(actor)
    if ":" in text:
        return text
    # Registry actors are bare ids (admin, worker-1, system). Default kind is
    # agent for ids that look machine-ish, otherwise user; explicit is better,
    # so callers can pass a canonical string directly.
    if text == "system":
        return "system:registry"
    return f"system:{text}"


def ingest_registry_event(
    store: LedgerStore,
    event: Dict[str, Any],
    *,
    with_detail: bool = True,
) -> Dict[str, Any]:
    """Append one registry/events-shaped record to the tenant's audit chain.

    Returns the newly stored audit record. Raises the ledger's standard
    validation / key errors when the mapping cannot be recorded (fail closed):
    in particular appending ``detail`` requires the tenant's encryption key.
    The input ``event`` is never mutated (read-only consumption).
    """
    if not isinstance(event, dict):
        raise TypeError("registry event must be a dict-shaped record")
    tenant_id = event.get("tenantId")
    if not isinstance(tenant_id, str) or not tenant_id:
        raise ValueError("registry event has no tenantId")

    action = event.get("event")
    if not isinstance(action, str) or not action:
        raise ValueError("registry event has no 'event' kind")

    agent_id = event.get("agentId")
    resource = f"registry/agents/{agent_id}" if agent_id else "registry"
    evidence = f"registry-event:{event['hash']}" if event.get("hash") else None

    payload: Any = None
    if with_detail and event.get("detail") is not None:
        payload = event["detail"]

    return store.append(
        tenant_id,
        actor=_canonical_actor(event.get("actor")),
        action=f"registry.{action}",
        resource=resource,
        evidence=evidence,
        payload=payload,
        ts=event.get("ts"),
    )
