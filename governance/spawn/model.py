"""The spawn envelope — one versioned document, one producer (issue #793).

---knowledge---
module_id: governance.spawn.model
system: governance
app: spawn
solution_class: enterprise
patterns: [provoked-negative-control, no-false-green, honesty-tri-state, fail-closed, offline-hermetic]
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [Refusal, EnvelopeRefused, admission_refusals, validate, assemble, parse, dumps]
invariants: ""
gotchas: ""
related: ["#793", "#1371", "#1372", "#1377", "#1413"]
do_not_duplicate: null
---knowledge---

`governance/**` is a large, well-tested surface — claim ledger, lane isolation,
lifecycle close-out, reconcile, runaway guard, capacity, gate admission — and
**none of it was applied by the act of spawning**. The remote path inlined
governance as PROMPT PROSE inside `fleet/terminal.py::build_prompt`, where
nothing could check that it had been carried; the local path shared none of it.

So one lane ran **26 gates** in one worktree while every other lane ran exactly
one, and the fleet's own liveness evidence contradicted itself. The rule
(AO-GR-22: at most one composite gate per worktree) was DECLARED and enforced
nowhere, which is why it held for eight lanes and failed silently for the ninth.

This module is the single producer of the envelope: one document, versioned,
carrying every fact a spawn needs in order to be governed — and the ONLY
implementation of that shape. `fleet/terminal.py` consumes it instead of
inlining prose, and `governance/spawn/cli.py open` produces the same document
for a locally spawned subagent, so the two regimes converge by construction
rather than by convention.

**The envelope is a precondition, not a suggestion.** `assemble` refuses a
document it cannot validate, naming every field that is missing or malformed —
fail-closed, with its own exit code (`EXIT_REFUSED`), exactly as the gate's
tri-state already works. A spawn that cannot present a well-formed envelope is
not "spawned with a warning"; it is refused.

Stdlib only, offline, no network: the validation is a pure function of the
document, so a gate can provoke every refusal without a fleet.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

#: The document schema. Versioned deliberately: a consumer that reads an
#: envelope must be able to refuse one it does not understand rather than
#: silently reading a field that moved.
SCHEMA = "spawn-envelope/v1"
VERSION = 1
PRODUCER = "governance/spawn/model.py"

#: Exit codes. 0/1/2 are the repo's tri-state; 78 is the refusal, and it is a
#: DIFFERENT code on purpose — a refused spawn is a decision, not a crash and
#: not a failure of the work, so nothing may fold it into 1. It matches
#: `fleet/terminal.py::RC_REFUSED`, so the loop and the CLI refuse alike.
EXIT_OK = 0
EXIT_NOT_OK = 1
EXIT_CANNOT_ASSESS = 2
EXIT_REFUSED = 78

#: The fields every spawn must present, in the order they are reported. This is
#: the contract in one place: the remote path, the local path, the gate and the
#: rendered prompt block all read it from here.
REQUIRED_FIELDS: tuple[str, ...] = (
    "issue",
    "lane",
    "worktree",
    "session",
    "trailer",
    "claim",
    "focus",
    "capacity",
    "budget",
    "gate",
    "verify",
)

#: Fields the nested objects must present. A missing one is refused BY NAME
#: (`capacity.permit.store`), because "the envelope was malformed" is not a
#: finding anyone can act on.
NESTED_REQUIRED: Mapping[str, tuple[str, ...]] = {
    "spawn": ("path", "agent"),
    "session": (
        "id", "branch", "agent", "repo_slug", "author_name", "author_email",
        "committer_name", "committer_email",
    ),
    "claim": ("owner", "state"),
    "focus": ("epic", "source"),
    "capacity": ("effective", "binding", "permit", "assessed"),
    "capacity.permit": ("store", "worktree_key", "lock", "max_concurrent"),
    "budget": ("attempts", "cap", "state"),
    "gate": ("of_record", "bound", "entry", "max_concurrent"),
    "verify": ("command", "source"),
}

#: The spawn block's admission inputs (issue #1413). The three judges that
#: landed for them (#1377 allowlists, #1372 ``tiering.judge``, #1371
#: ``resolve_actor``) were called by nothing until this module called them HERE,
#: which is the admission point: `render.py` only renders an already-admitted
#: envelope. They are materialised by the producer (`sources.collect` →
#: `admission.spawn_record`) into every document, so the envelope an auditor
#: reads states the admission it was granted rather than leaving it to be
#: inferred from the environment the spawn happened to run in.
ADMISSION_FIELDS: tuple[str, ...] = ("runtime", "role", "tier", "task_class", "actor")

#: The spawn paths that exist. A path outside this set is refused rather than
#: treated as a third regime nobody governs.
SPAWN_PATHS: tuple[str, ...] = ("fleet", "local")


@dataclass(frozen=True)
class Refusal:
    """One reason a spawn cannot be admitted, naming the field it is about."""

    field: str
    reason: str

    def line(self) -> str:
        return f"{self.field}: {self.reason}"


class EnvelopeRefused(Exception):
    """A spawn could not present a well-formed envelope. Carries every refusal.

    Every refusal is carried, not just the first: a spawn that is missing three
    fields must say so once, so the fix is one edit rather than three spawns.
    """

    def __init__(self, refusals: Iterable[Refusal]) -> None:
        self.refusals: tuple[Refusal, ...] = tuple(refusals)
        detail = "; ".join(refusal.line() for refusal in self.refusals) or "malformed envelope"
        super().__init__(f"spawn envelope REFUSED — {detail}")

    def lines(self) -> list[str]:
        return [refusal.line() for refusal in self.refusals]


def _blank(value: Any) -> bool:
    """True for a value that is present but carries nothing.

    `0` and `False` are NOT blank: a budget of zero attempts is a measurement,
    and `epic: None` is the focus schema's own word for a pooled board.
    """
    return value is None or (isinstance(value, (str, bytes)) and not value) or (
        isinstance(value, (Mapping, Sequence)) and not isinstance(value, str) and len(value) == 0
    )


def _int_at_least(value: Any, minimum: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= minimum


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _absent(document: Mapping[str, Any]) -> list[Refusal]:
    """Every required field the document does not present at all."""
    refusals: list[Refusal] = []
    if document.get("schema") != SCHEMA:
        refusals.append(
            Refusal("schema", f"expected {SCHEMA!r}, got {document.get('schema')!r}")
        )
    for field in REQUIRED_FIELDS:
        if field not in document:
            refusals.append(Refusal(field, "missing"))
        elif _blank(document[field]):
            refusals.append(Refusal(field, "empty"))
    return refusals


def _nested_missing(document: Mapping[str, Any]) -> list[Refusal]:
    """Every required key a nested object does not present, refused by dotted name."""
    refusals: list[Refusal] = []
    for parent, keys in NESTED_REQUIRED.items():
        head, _, tail = parent.partition(".")
        obj: Any = document.get(head)
        if tail:
            if not isinstance(obj, Mapping):
                continue
            obj = obj.get(tail)
        if not isinstance(obj, Mapping):
            # The parent itself is already reported (missing/empty/not-a-map) by
            # its own validator below; do not double-report the whole subtree.
            continue
        for key in keys:
            if key not in obj:
                refusals.append(Refusal(f"{parent}.{key}", "missing"))
            elif _blank(obj[key]) and obj[key] != 0:
                refusals.append(Refusal(f"{parent}.{key}", "empty"))
    return refusals


def _shape_refusals(document: Mapping[str, Any]) -> list[Refusal]:
    """Structural checks: a present field whose VALUE cannot be a real spawn.

    Only fields that are present AND non-blank are checked, so one broken field
    produces one refusal rather than a cascade.
    """
    refusals: list[Refusal] = []

    def bad(field: str, reason: str) -> None:
        refusals.append(Refusal(field, reason))

    spawn = document.get("spawn")
    if isinstance(spawn, Mapping):
        path = spawn.get("path")
        if not _blank(path) and path not in SPAWN_PATHS:
            bad("spawn.path", f"{path!r} is not one of {SPAWN_PATHS}")

    issue = document.get("issue")
    if not _blank(issue) and not _int_at_least(issue, 1):
        bad("issue", f"must be an integer >= 1, got {issue!r}")

    lane = document.get("lane")
    if not _blank(lane) and not isinstance(lane, str):
        bad("lane", f"must be a string, got {type(lane).__name__}")

    worktree = document.get("worktree")
    if not _blank(worktree):
        if not isinstance(worktree, str) or not Path(worktree).is_absolute():
            bad("worktree", f"{worktree!r} is not an absolute path")

    session = document.get("session")
    if isinstance(session, Mapping) and _int_at_least(issue, 1):
        branch = session.get("branch")
        if not _blank(branch) and isinstance(branch, str) and not branch.startswith(f"issue-{issue}"):
            bad("session.branch", f"{branch!r} does not name issue #{issue}")
        slug = session.get("repo_slug")
        if not _blank(slug) and not isinstance(slug, str):
            bad("session.repo_slug", f"must be a string, got {type(slug).__name__}")

    trailer = document.get("trailer")
    if not _blank(trailer):
        slug = session.get("repo_slug") if isinstance(session, Mapping) else None
        expected = f"Refs {slug}#{issue}" if isinstance(slug, str) and slug else None
        if not isinstance(trailer, str) or trailer.count("#") != 1 or not trailer.startswith("Refs "):
            bad("trailer", f"{trailer!r} is not a 'Refs <owner>/<repo>#<issue>' trailer")
        elif expected is not None and trailer != expected:
            bad("trailer", f"{trailer!r} does not match the session's own {expected!r}")

    claim = document.get("claim")
    if isinstance(claim, Mapping):
        owner = claim.get("owner")
        if not _blank(owner) and not isinstance(owner, str):
            bad("claim.owner", f"must be a string, got {type(owner).__name__}")
        claim_lane = claim.get("lane")
        if not _blank(claim_lane) and not _blank(lane) and claim_lane != lane:
            bad("claim.lane", f"claim is held by {claim_lane!r} but the spawn is lane {lane!r}")

    focus = document.get("focus")
    if isinstance(focus, Mapping):
        epic = focus.get("epic")
        if not _blank(epic) and not _int_at_least(epic, 1):
            bad("focus.epic", f"must be an integer >= 1 or null (pooled), got {epic!r}")

    capacity = document.get("capacity")
    if isinstance(capacity, Mapping):
        if "effective" in capacity and not _blank(capacity["effective"]) and not _int_at_least(
            capacity["effective"], 0
        ):
            bad("capacity.effective", f"must be an integer >= 0, got {capacity['effective']!r}")
        permit = capacity.get("permit")
        if isinstance(permit, Mapping):
            if "max_concurrent" in permit and not _int_at_least(permit["max_concurrent"], 1):
                bad(
                    "capacity.permit.max_concurrent",
                    f"must be an integer >= 1, got {permit['max_concurrent']!r}",
                )
        if capacity.get("assessed") is False:
            # Fail-closed, exactly as `fleet/capacity.py::admit` holds every lane
            # when the ceiling cannot be measured: the box cannot be measured, so
            # nothing may be multiplied on it. "Probably fine" is not a permit.
            why = "; ".join(str(problem) for problem in capacity.get("problems") or [])
            bad("capacity", f"the ceiling could not be assessed ({why or 'no measurement'})")

    budget = document.get("budget")
    if isinstance(budget, Mapping):
        if "cap" in budget and not _blank(budget["cap"]) and not _int_at_least(budget["cap"], 1):
            bad("budget.cap", f"must be an integer >= 1, got {budget['cap']!r}")
        if "attempts" in budget and not _blank(budget["attempts"]) and not _int_at_least(
            budget["attempts"], 0
        ):
            bad("budget.attempts", f"must be an integer >= 0, got {budget['attempts']!r}")

    gate = document.get("gate")
    if isinstance(gate, Mapping):
        if "max_concurrent" in gate and not _int_at_least(gate["max_concurrent"], 1):
            bad("gate.max_concurrent", f"must be an integer >= 1, got {gate['max_concurrent']!r}")
        of_record = gate.get("of_record")
        if not _blank(of_record) and not isinstance(of_record, str):
            bad("gate.of_record", f"must be a string, got {type(of_record).__name__}")

    verify = document.get("verify")
    if isinstance(verify, Mapping):
        command = verify.get("command")
        if not _blank(command) and not isinstance(command, str):
            bad(
                "verify.command",
                f"must be the issue's Verify: clause as a string, got {type(command).__name__}",
            )
    elif not _blank(verify):
        bad("verify", f"must be a {{command, source}} object, got {type(verify).__name__}")

    return refusals


def admission_refusals(document: Mapping[str, Any]) -> list[Refusal]:
    """The three judges, called here — the spawn point — before anything exists.

    An envelope is a REQUEST to spawn, so this is where the request is judged:
    who is acting (``resolve_actor``, fail-closed on an undeclared actor), at
    which FinOps tier (``tiering.judge`` against ``gateway/finops/tiers.yaml``),
    with which capabilities (the per-runtime allowlists `fleet/channel.py` reads).
    Each verdict is named in the refusal list exactly as its owning module names
    it — ``actor-unresolved:<actor>``, ``FINOPS-ROLE-NOT-ALLOWED``,
    ``verb-not-allowed:<runtime>:<verb>`` and its siblings — so a refused spawn
    says which judge refused it and why.

    A document with no ``spawn`` block carries no request to judge; the shape
    validators already refuse the fields such a document is missing. A judge that
    cannot be reached is a refusal, never a pass.

    ``scripts/check-spawn-envelope.sh`` removes each of the three calls below, one
    at a time, and requires that judge's negative control to stop being refused —
    a control that cannot fail is not a control (GR-12).
    """
    record = document.get("spawn")
    if not isinstance(record, Mapping) or not record:
        return []
    try:
        from governance.spawn import admission  # noqa: PLC0415 - lazy, so this module stays importable alone
    except (ImportError, OSError) as exc:
        return [Refusal("spawn", f"admission-unavailable: {exc}")]
    findings: list[tuple[str, str]] = []
    findings += admission.actor_findings(record)  # ADMISSION-JUDGE-ACTOR
    findings += admission.tier_findings(record)  # ADMISSION-JUDGE-TIER
    findings += admission.allowlist_findings(record)  # ADMISSION-JUDGE-ALLOWLIST
    return [Refusal(field, reason) for field, reason in findings]


def validate(document: Any) -> list[Refusal]:
    """Every reason `document` is not a well-formed spawn envelope.

    Empty list == admitted. A non-object, a wrong schema, every missing field,
    every malformed value — all are reported, each naming its field, so one pass
    tells a caller everything it must fix.
    """
    if not isinstance(document, Mapping):
        return [
            Refusal(
                "envelope",
                f"must be a JSON object, got {type(document).__name__}",
            )
        ]
    return [
        *_absent(document),
        *_nested_missing(document),
        *_shape_refusals(document),
        *admission_refusals(document),
    ]


def assemble(
    fields: Mapping[str, Any],
    *,
    spawn: Mapping[str, Any] | None = None,
    produced_at: str | None = None,
) -> dict[str, Any]:
    """Build the canonical document from `fields`, or refuse it by name.

    This is the ONE place the document is shaped. Both spawn paths call it, so
    neither can invent a shape the other does not carry — which is what
    "converge by construction" means here.
    """
    document: dict[str, Any] = {
        "schema": SCHEMA,
        "version": VERSION,
        "producer": PRODUCER,
        "produced_at": produced_at or _iso_now(),
        "spawn": dict(spawn or {}),
    }
    for field in REQUIRED_FIELDS:
        document[field] = fields.get(field)
    refusals = validate(document)
    if refusals:
        raise EnvelopeRefused(refusals)
    return document


def parse(text: str) -> dict[str, Any]:
    """Read a document from its serialised form; an unreadable one is refused."""
    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        raise EnvelopeRefused([Refusal("envelope", f"not valid JSON ({exc.msg} at line {exc.lineno})")])
    if not isinstance(document, dict):
        raise EnvelopeRefused(
            [Refusal("envelope", f"must be a JSON object, got {type(document).__name__}")]
        )
    return document


def dumps(document: Mapping[str, Any]) -> str:
    """The canonical serialisation: stable key order, one trailing newline."""
    return json.dumps(document, indent=2, sort_keys=False) + "\n"
