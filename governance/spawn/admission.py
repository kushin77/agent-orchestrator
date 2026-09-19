#!/usr/bin/env python3
"""The three judges, called AT the spawn point (issue #1413).

#1377 (per-runtime allowlists), #1372 (``tiering.judge``) and #1371
(``resolve_actor``) each landed a judge, and each reported the same gap: the
Claude-subagent admission point is ``governance/spawn/model.py``
(``render.py`` only renders an already-admitted envelope), and nothing called
them there. So a lane could still be spawned at a forbidden tier or by an actor
nobody had ever declared, and the three judges were **inert** — a control nobody
runs is a formality (GR-12).

This module is the caller. It owns no verdict of its own: every judgement is the
one the owning module already makes, re-expressed as ``(field, reason)`` pairs so
``model.py`` can name it in the envelope's refusal list.

===========================  ==============================================
input                        judge (and where its vocabulary comes from)
===========================  ==============================================
``role`` + ``task_class`` +  ``governance.spawn.tiering.judge`` → the FinOps
``tier`` / ``model``          finding ``FINOPS-ROLE-NOT-ALLOWED``, against
                             ``gateway/finops/tiers.yaml`` alone
``actor``                    ``registry.service.identity.resolve_actor`` →
                             ``actor-unresolved:<actor>`` (fail closed)
``runtime`` + ``verbs`` /    ``fleet/channel.py``'s three tables (verbs:
``skills`` / ``secrets``     ``control-plane/control/verbs.yaml``; skills and
                             secrets: the paperclip adapters) → the declared
                             names ``verb-not-allowed:<runtime>:<verb>``,
                             ``skill-not-allowed:...``, ``secret-not-allowed:...``
===========================  ==============================================

**Borrowed, never re-declared.** The allowlist tables are read through
``fleet/channel.py``'s own readers (#1273) rather than re-parsed here, so there
is exactly one reader of each table; if that module stops exposing them this
module REFUSES the spawn naming the reader it could not reach, instead of
degrading to "no restriction" (the one failure mode an allowlist must not have).

**Absent is not wrong, declared is judged.** A spawn that declares no runtime,
role, task class or tier is resolved to the cheapest capable declaration the
tables already make — the runtime both paths run, the role its path speaks as,
and that class's OWN ``defaultTier`` (floored at the security floor) — which is
admissible by construction. A spawn that DECLARES any of them is judged for real.
That is the posture ``governance/isolation`` already takes for the lane record's
runtime (#1301): an absent binding is named, a wrong one is refused.

Fail-closed, offline, stdlib-only when the judges are reachable: a judge that
cannot be reached is a refusal naming that failure, never a pass.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

#: ``registry/`` goes on ``sys.path`` because that package is importable as
#: ``service`` from inside it (``registry/service/__init__.py`` imports
#: ``service.*``), and the identity resolver this module borrows lives there —
#: the same bootstrap ``registry/service/identity.py`` performs for
#: ``identity.sso``. ``fleet/`` is on it for ``fleet/channel.py``, the allowlist
#: tables' one reader. Both are resolved RELATIVE TO THIS FILE, so a scratch
#: checkout gets its own modules rather than whichever ones the cwd implies.
ROOT = Path(__file__).resolve().parents[2]
for _extra in (ROOT, ROOT / "registry", ROOT / "fleet"):
    if str(_extra) not in sys.path:
        sys.path.insert(0, str(_extra))

#: The runtime that runs the work when a spawn declares none. Both governed
#: spawn paths run ``claude`` — the fleet loop spawns ``claude -p`` and the local
#: path spawns a Claude subagent — so the default is that wire id, which is one
#: of the seven ``fleet/runtimes.yaml`` declares and the ids the allowlist tables
#: are keyed by. An explicit ``AO_RUNTIME`` (or ``--runtime``) overrides it: a
#: hermes-persona or copilot-agent spawn says so rather than being assumed.
DEFAULT_RUNTIME = "claude-subagent"

#: Which of ``tiering.KNOWN_ROLES`` each spawn path speaks as (the role
#: vocabulary itself is ``tiering``'s and is never re-declared here).
ROLE_BY_PATH: Mapping[str, str] = {"fleet": "fleet", "local": "claude-subagent"}

#: The task class a spawn that declares none is judged as. It is judged at that
#: class's OWN cheapest capable tier, so a spawn that asks for nothing is not
#: refused for its silence — while one that ASKS for a tier is judged for real.
DEFAULT_TASK_CLASS = "code-author"

#: The declared request lists a spawn may carry, and the field each is refused
#: under. The refusal PREFIXES are ``fleet/channel.py``'s own (#1273).
REQUEST_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("verbs", "spawn.verbs", "verb-not-allowed"),
    ("skills", "spawn.skills", "skill-not-allowed"),
    ("secrets", "spawn.secrets", "secret-not-allowed"),
)

#: The readers ``fleet/channel.py`` exposes for its three tables, and the label
#: used to name the one that could not be reached.
ALLOWLIST_READERS: tuple[tuple[str, str], ...] = (
    ("verbs", "_verb_allowlist"),
    ("skills", "_skill_allowlist"),
    ("secrets", "_secret_allowlist"),
)

#: The reader ``fleet/channel.py`` exposes for the closed runtime vocabulary.
#: #1412 replaced its module-level ``RUNTIME_IDS`` literal with this lazy
#: accessor, so the ids come from the ONE authority (``fleet/runtimes.yaml``,
#: read by ``fleet/runtimes.py``) and the vocabulary is never re-declared here.
RUNTIME_IDS_READER = "runtime_ids"

Finding = tuple[str, str]


class AdmissionUnavailable(Exception):
    """A judge could not be reached, so no verdict is possible (fail closed)."""


# --- declared values, with the declared defaults ----------------------------- #


def _text(value: Any) -> str:
    return str(value or "").strip()


def _items(record: Mapping[str, Any], key: str) -> tuple[str, ...]:
    """The declared request list under ``key`` — a list, a string, or nothing.

    A spawn record is JSON: a caller that writes one item as a bare string means
    one item, not one letter per entry.
    """
    value = record.get(key)
    if isinstance(value, str):
        return tuple(part.strip() for part in value.split(",") if part.strip())
    if isinstance(value, Iterable) and not isinstance(value, Mapping):
        return tuple(_text(part) for part in value if _text(part))
    return ()


def declared_runtime(record: Mapping[str, Any]) -> str:
    return _text(record.get("runtime")) or DEFAULT_RUNTIME


def declared_role(record: Mapping[str, Any]) -> str:
    """The tiering role: what the record says, else what its spawn path speaks as."""
    return _text(record.get("role")) or ROLE_BY_PATH.get(_text(record.get("path")), "")


def declared_task_class(record: Mapping[str, Any]) -> str:
    return _text(record.get("task_class")) or DEFAULT_TASK_CLASS


def declared_actor(record: Mapping[str, Any]) -> str:
    """The acting identity: what the record says, else the runtime it runs as."""
    return _text(record.get("actor")) or declared_runtime(record)


def declared_tier(record: Mapping[str, Any]) -> str:
    """The requested tier as declared ('' when the spawn asked for none)."""
    return _text(record.get("tier"))


def declared_model(record: Mapping[str, Any]) -> str:
    return _text(record.get("model"))


# --- the judges --------------------------------------------------------------- #


def actor_findings(record: Mapping[str, Any]) -> list[Finding]:
    """Judge whether ``actor`` is a declared identity (#1275).

    ``resolve_actor`` is the identity lane's ONE resolver; an actor string it does
    not know is ``actor-unresolved:<string>``, and a spawn that cannot say who is
    acting is refused rather than guessed at.
    """
    actor = declared_actor(record)
    try:
        from registry.service.errors import (  # noqa: PLC0415 - borrowed, resolved on demand
            ActorUnresolvedError,
            DelegationUndeclaredError,
        )
        from registry.service.identity import resolve_actor  # noqa: PLC0415
    except (ImportError, OSError) as exc:
        return [("spawn.actor", f"admission-unavailable: {exc}")]
    try:
        resolve_actor(actor)
    except (ActorUnresolvedError, DelegationUndeclaredError) as exc:
        return [("spawn.actor", str(exc))]
    except Exception as exc:  # noqa: BLE001 - fail CLOSED: a broken resolver refuses the spawn
        return [("spawn.actor", f"admission-unavailable: {type(exc).__name__}: {exc}")]
    return []


def tier_findings(record: Mapping[str, Any]) -> list[Finding]:
    """Judge ``(role, task_class, tier)`` against ``gateway/finops/tiers.yaml`` (#1272).

    The requested tier is the declared ``tier``; when the record names a MODEL
    instead (``claude-opus-5``), its rung is read off the ladder the table
    already declares — the table is the only place a model's tier is written
    down. A record that requests neither is judged at its class's own cheapest
    capable tier, so silence is never mistaken for a request.
    """
    role = declared_role(record)
    task_class = declared_task_class(record)
    model = declared_model(record)
    tier = declared_tier(record)
    tiering = _tiering_module()
    if tiering is None:
        return [
            ("spawn.tier", "admission-unavailable: governance.spawn.tiering is not importable here")
        ]
    try:
        table = tiering.load_table()
        if task_class not in table.task_classes:
            return [
                (
                    "spawn.task_class",
                    f"{tiering.FINDING_UNKNOWN_TASK_CLASS}: task class {task_class!r} "
                    "is not in tiers.yaml taskClasses",
                )
            ]
        if model:
            rung = tiering.tier_for_model(model, table=table)
            if rung is None:
                return [
                    (
                        "spawn.model",
                        f"{tiering.FINDING_UNKNOWN_MODEL}: {model!r} sits on no rung of the "
                        "tiers.yaml ladder",
                    )
                ]
            if tier and tier != rung:
                return [
                    (
                        "spawn.model",
                        f"{tiering.FINDING_MODEL_TIER_CONFLICT}: {model!r} is a {rung}-rung model "
                        f"but the spawn declares tier {tier!r}",
                    )
                ]
            tier = rung
        if not tier:
            tier = tiering.default_tier(task_class, table=table)
        verdict = tiering.judge(role, task_class, tier, table=table)
    except tiering.TieringUnavailable as exc:
        return [("spawn.tier", f"{tiering.FINDING_TIERS_UNAVAILABLE}: {exc}")]
    except Exception as exc:  # noqa: BLE001 - fail CLOSED: a broken judge refuses the spawn
        return [("spawn.tier", f"admission-unavailable: {type(exc).__name__}: {exc}")]
    if not verdict.allowed:
        return [("spawn.tier", str(verdict))]
    return []


def _allowlists() -> tuple[tuple[str, ...], dict[str, dict[str, tuple[str, ...]]]]:
    """The runtime ids and the three allowlists, read through ``fleet/channel.py``.

    Lazy on purpose, exactly as ``fleet/channel.py`` resolves its own optional
    siblings: ``governance/spawn`` must stay importable where ``fleet/`` is not a
    sibling (a scratch copy), and the failure is then a refusal this module names
    rather than an import-time crash.

    The vocabulary is read the same way as the tables — through the accessor the
    module exposes, never a copy of the ids held here.
    """
    try:
        import channel  # noqa: PLC0415 - fleet/channel.py, the tables' ONE reader (#1273)
    except ImportError as exc:
        raise AdmissionUnavailable(f"fleet/channel.py is not importable: {exc}") from exc
    tables: dict[str, dict[str, tuple[str, ...]]] = {}
    for label, reader_name in ALLOWLIST_READERS:
        reader = getattr(channel, reader_name, None)
        if not callable(reader):
            raise AdmissionUnavailable(
                f"fleet/channel.py no longer exposes {reader_name}(), so the {label} "
                "allowlist has no reader — refusing rather than assuming no restriction"
            )
        tables[label] = dict(reader())
    ids_reader = getattr(channel, RUNTIME_IDS_READER, None)
    if not callable(ids_reader):
        raise AdmissionUnavailable(
            f"fleet/channel.py no longer exposes {RUNTIME_IDS_READER}(), so the runtime "
            "vocabulary has no reader — refusing rather than assuming no restriction"
        )
    try:
        runtime_ids = tuple(ids_reader())
    except Exception as exc:  # noqa: BLE001 - fail CLOSED: an unreadable registry refuses
        # ``fleet/runtimes.py`` REFUSES an absent, unreadable or empty registry
        # rather than returning an empty vocabulary (an empty one would make every
        # runtime-bearing spawn valid by accident). That refusal is a NAMED verdict
        # here, never a traceback at the spawn point.
        raise AdmissionUnavailable(
            f"fleet/channel.py's {RUNTIME_IDS_READER}() could not read the runtime "
            f"registry: {type(exc).__name__}: {exc}"
        ) from exc
    return runtime_ids, tables


def allowlist_findings(record: Mapping[str, Any]) -> list[Finding]:
    """Judge the runtime and its declared verbs/skills/secrets (#1273).

    The refusal NAMES are ``fleet/channel.py``'s own, so a lane refused here is
    refused under the same name its message would have been refused under — one
    vocabulary, whichever door the request came through. A verb/skill/secret the
    table does not carry is simply not restricted (the allowlist is an ADDITIVE
    narrowing, never the only gate).
    """
    runtime = declared_runtime(record)
    requests = {key: _items(record, key) for key, _field, _prefix in REQUEST_FIELDS}
    try:
        runtime_ids, tables = _allowlists()
    except AdmissionUnavailable as exc:
        return [("spawn.runtime", f"admission-unavailable: {exc}")]
    unknown = _runtime_finding(runtime, runtime_ids)
    if unknown:
        return [unknown]
    findings: list[Finding] = []
    for key, field, prefix in REQUEST_FIELDS:
        allowed_for = tables[key]
        for item in requests[key]:
            allowed = allowed_for.get(item)
            if allowed is not None and runtime not in allowed:
                findings.append((field, f"{prefix}:{runtime}:{item}"))
    return findings


def _runtime_finding(runtime: str, runtime_ids: tuple[str, ...]) -> Finding | None:
    """Refuse a runtime outside the closed set the tables are keyed by.

    An id the tables do not know is not "unrestricted", it is unjudged — so it is
    refused in ``fleet/channel.py``'s own words, not silently passed.
    """
    if not runtime_ids or runtime in runtime_ids:
        return None
    return ("spawn.runtime", f"runtime must be one of {', '.join(runtime_ids)}, got {runtime!r}")


# --- what the producer writes into the envelope ------------------------------- #


def _tiering_module():
    """``governance.spawn.tiering``, or None when it cannot be imported here."""
    try:
        from governance.spawn import tiering  # noqa: PLC0415 - the FinOps ladder's owner

        return tiering
    except (ImportError, OSError):
        return None


def _tier_table_for_materialisation():
    """The loaded ``tiers.yaml`` table, or None when it cannot be read here.

    Only the PRODUCER uses this: a table that cannot be read leaves the tier
    empty, and ``tier_findings`` then refuses the spawn naming
    ``FINOPS-TIERS-UNAVAILABLE``. Nothing is invented to fill the gap.
    """
    tiering = _tiering_module()
    if tiering is None:
        return None
    try:
        return tiering.load_table()
    except tiering.TieringUnavailable:
        return None


def spawn_record(
    *,
    path: str,
    agent: str,
    directive: str = "",
    env: Mapping[str, str] | None = None,
    runtime: str = "",
    role: str = "",
    tier: str = "",
    model: str = "",
    task_class: str = "",
    actor: str = "",
    verbs: Sequence[str] = (),
    skills: Sequence[str] = (),
    secrets: Sequence[str] = (),
) -> dict[str, Any]:
    """The envelope's ``spawn`` block: who runs, as what, at which tier, under whose identity.

    This is the record the four lane-binding values are stored in — ``runtime``,
    ``role``, ``tier``, ``actor`` (issue #1301's `isolation open` vocabulary) —
    plus the class and the declared capability request the judges read. The four
    are always MATERIALISED here (never left empty) so the envelope an auditor
    reads states the admission it was judged by, rather than leaving it to be
    inferred from the environment the spawn happened to run in.

    The tier is materialised through the same tables the judge reads: the declared
    model's rung when one is named, else the class's own cheapest capable tier. A
    table that cannot be read leaves it empty on purpose — ``tier_findings`` then
    REFUSES the spawn naming ``FINOPS-TIERS-UNAVAILABLE`` instead of this
    function inventing one.
    """
    source = {str(key): _text(value) for key, value in (env or {}).items()}
    resolved_runtime = _text(runtime) or source.get("AO_RUNTIME", "") or DEFAULT_RUNTIME
    resolved_role = _text(role) or source.get("AO_ROLE", "") or ROLE_BY_PATH.get(_text(path), "")
    resolved_model = _text(model) or source.get("AO_MODEL", "")
    resolved_class = _text(task_class) or source.get("AO_TASK_CLASS", "") or DEFAULT_TASK_CLASS
    resolved_tier = _text(tier) or source.get("AO_MODEL_TIER", "")
    if not resolved_tier:
        table = _tier_table_for_materialisation()
        if table is not None:
            tiering = _tiering_module()
            if resolved_model and tiering is not None:
                resolved_tier = tiering.tier_for_model(resolved_model, table=table) or ""
            if not resolved_tier and tiering is not None and resolved_class in table.task_classes:
                resolved_tier = tiering.default_tier(resolved_class, table=table)
    record: dict[str, Any] = {
        "path": _text(path),
        "agent": _text(agent),
        "directive": _text(directive),
        "runtime": resolved_runtime,
        "role": resolved_role,
        "tier": resolved_tier,
        "task_class": resolved_class,
        "actor": _text(actor) or source.get("AO_ACTOR", "") or resolved_runtime,
        "verbs": list(verbs) or list(_csv(source.get("AO_SPAWN_VERBS", ""))),
        "skills": list(skills) or list(_csv(source.get("AO_SPAWN_SKILLS", ""))),
        "secrets": list(secrets) or list(_csv(source.get("AO_SPAWN_SECRETS", ""))),
    }
    if resolved_model:
        record["model"] = resolved_model
    return record


def _csv(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.split(",") if part.strip())
