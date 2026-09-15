"""The authorisation directive, and the mailbox move that ends it (#821).

A brain-minted directive is the chain edge that authorises a lane: it is written
to ``.fleet/sent/<id>.json`` and ``claim --directive <id>`` validates it
(``AGENTS.md`` golden rule 14). *Consumed* means the record has reached
``.fleet/done/``. There is no third state, so "is this order finished?" is a
question about where the record is, and it is answered here rather than inferred
from a CLI's mailboxes.

The defect this module removes was measured, not imagined. On 2026-09-15
``.fleet/sent/`` held 118 directives that no documented command could retire: the
remedy ``DIRECTIVE_NOT_CONSUMED`` named — ``fleet/channel.py consume --id <id>`` —
reads only the *inbox*, and a brain-minted directive never enters it, so the
command answered ``nothing to consume`` (rc 1) for the very artifact it was given.
Two lanes in one wave (#693, #695) reported the identical failure, and the only
way past it was an undocumented hand-move of another lane's runtime state. That is
an invariant the documented path cannot satisfy (AO-GR-21/GR-29), and the close-out
is where it is satisfiable: the close-out already knows whether the ordered change
landed, which is exactly the fact the move needs.

**The gate, and why it is "landed" rather than "closed".** A directive is retired
only when the change its issue ordered has **landed** — the issue is closed, or its
pull request is merged — reusing the model's own ``owes_closure`` definition so the
two cannot drift apart. It deliberately is *not* "the issue is closed": close-out
consumes the directive at step 4 and closes the issue at step 7, so that criterion
would make the documented path unsatisfiable for every ordered lane — the very
defect class this module exists to remove. Everything else is refused **by name**:
a directive that names no issue, an issue the lifecycle record cannot see, and an
order whose change has not landed (live work) all stay in ``.fleet/sent/``. The
move can therefore never become a "consume anything" verb that retires work still
in flight, and an undecidable subject is refused rather than defaulted to allowed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from governance.lifecycle.model import owes_closure

#: The two mailboxes a directive can occupy. ``sent`` is where the brain mints an
#: order; ``done`` is terminal. Nothing else in this repository may move a record
#: between them, so this module is the single owner of the transition.
SENT_DIR = ".fleet/sent"
DONE_DIR = ".fleet/done"

STATE_SENT = "sent"
STATE_DONE = "done"


class DirectiveRefused(RuntimeError):
    """The terminal move was refused by name. Nothing was moved."""


@dataclass(frozen=True)
class Directive:
    """One authorisation directive, and the mailbox it currently sits in."""

    id: str
    issue: int | None
    state: str
    path: Path

    @property
    def consumed(self) -> bool:
        """Whether the order has reached its terminal mailbox."""
        return self.state == STATE_DONE


def mailboxes(root: Path) -> tuple[Path, Path]:
    """The ``(sent, done)`` directories under a repository root."""
    return root / SENT_DIR, root / DONE_DIR


def _read(path: Path) -> dict | None:
    """A directive record, or ``None`` when the file cannot be read as one.

    Tolerance is deliberate here and strictness is applied by :func:`resolve`: a
    single unreadable file must not blind the lifecycle to every other directive,
    but a file the close-out is about to act on must be legible or the move is
    refused.
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _issue_of(payload: dict) -> int | None:
    """The issue an order names, or ``None`` when it names none we can act on."""
    try:
        number = int((payload.get("task") or {}).get("issue") or 0)
    except (TypeError, ValueError):
        return None
    return number or None


def records(root: Path) -> list[Directive]:
    """Every directive in both mailboxes — ``sent`` first, then the terminal ones.

    Ordering is part of the contract, not an accident: the callers that report an
    item's directive state want the *stranded* record to win, because a terminal
    sibling must never mask an authorisation that is still pending (#821).
    """
    sent, done = mailboxes(root)
    found: list[Directive] = []
    for state, directory in ((STATE_SENT, sent), (STATE_DONE, done)):
        if not directory.exists():
            continue
        for path in sorted(directory.glob("*.json")):
            payload = _read(path)
            if payload is None:
                continue
            found.append(Directive(str(payload.get("id") or path.stem), _issue_of(payload), state, path))
    return found


def resolve(root: Path, directive_id: str) -> Directive | None:
    """The directive named by ``directive_id``, matched on its id or its filename.

    A file that carries the name we were asked about but cannot be parsed is a
    **refusal**, not a miss: the move would have to guess the order's subject, and
    guessing an authorisation's subject is how a "consume anything" verb starts.
    """
    sent, done = mailboxes(root)
    for state, directory in ((STATE_SENT, sent), (STATE_DONE, done)):
        if not directory.exists():
            continue
        for path in sorted(directory.glob("*.json")):
            payload = _read(path)
            if payload is None:
                if path.stem == directive_id:
                    raise DirectiveRefused(
                        f"{SENT_DIR if state == STATE_SENT else DONE_DIR}/{path.name} is unreadable; "
                        "refusing to guess the issue it orders"
                    )
                continue
            record_id = str(payload.get("id") or path.stem)
            if record_id == directive_id or path.stem == directive_id:
                return Directive(record_id, _issue_of(payload), state, path)
    return None


def stranded(root: Path, issue: int) -> list[Directive]:
    """A strand: the directives for ``issue`` still sitting in ``sent``."""
    return [record for record in records(root) if record.issue == issue and record.state == STATE_SENT]


def landed_issues(record: Mapping | None) -> dict[int, bool]:
    """Which issues' ordered work has landed, read from a lifecycle record.

    "Landed" is the model's own :func:`owes_closure` — the issue is closed, or its
    pull request is merged — rather than a second rule invented here, so the
    directive's gate and the closure invariants cannot drift apart. An issue the
    record does not mention is simply absent from the map, which the move reads as
    *unknown* and refuses: absence is not permission.
    """
    items = (record or {}).get("items") or []
    return {int(item.get("issue") or 0): owes_closure(item) for item in items}


def _collision(record: Directive, done: Path) -> Path | None:
    """The terminal file that already holds this name, when it holds one."""
    target = done / record.path.name
    return target if target.exists() else None


def consume(root: Path, directive_id: str, landed: Mapping[int, bool] | None = None) -> str:
    """Retire the order: move its stranded record(s) from ``sent`` to ``done``.

    Terminal, idempotent, and refused **by name** rather than defaulted, in every
    case where the order cannot be shown to be finished:

    * no record carries the id — refuse;
    * the record names no issue — refuse (an authorisation whose subject cannot be
      established is not an authorisation that may be retired);
    * the issue is outside the lifecycle record — refuse;
    * the issue's change has not landed — refuse, and the record stays in ``sent``,
      so the move can never retire work that is still in flight;
    * an identical file already sits in ``done`` — the sent copy is a duplicate of a
      terminal record and is removed; a *differing* file is refused rather than
      overwritten, because which edition is authoritative is not a fact this module
      has.

    The whole stranded set for the order's issue moves in one go: the invariant is
    about the order being retired, and leaving a sibling pending would reproduce the
    same finding while ``sent`` kept growing. Every collision is checked before the
    first move, so a refusal never leaves the mailboxes half-moved.
    """
    landed = landed or {}
    record = resolve(root, directive_id)
    if record is None:
        raise DirectiveRefused(f"unknown directive {directive_id!r}: no record in {SENT_DIR}/ or {DONE_DIR}/")
    if record.consumed:
        return f"{record.id} was already consumed"

    issue = record.issue
    if issue is None:
        raise DirectiveRefused(
            f"directive {record.id} names no issue; refusing to retire an order whose subject cannot be established"
        )
    state = landed.get(issue)
    if state is None:
        raise DirectiveRefused(
            f"directive {record.id} orders #{issue}, which is outside the lifecycle record; refusing rather than "
            "defaulting to allow"
        )
    if not state:
        raise DirectiveRefused(
            f"directive {record.id} orders #{issue}, whose change has not landed; the order is live and is not "
            f"consumed (it stays in {SENT_DIR}/)"
        )

    pending = stranded(root, issue)
    if not pending:  # pragma: no cover - resolve() already saw a sent record for this issue
        return f"{record.id} was already consumed"

    sent, done = mailboxes(root)
    collisions: list[tuple[Directive, Path]] = []
    for candidate in pending:
        target = _collision(candidate, done)
        if target is not None and target.read_bytes() != candidate.path.read_bytes():
            raise DirectiveRefused(
                f"{DONE_DIR}/{target.name} already holds different content; refusing to overwrite the terminal record"
            )
        if target is not None:
            collisions.append((candidate, target))

    done.mkdir(parents=True, exist_ok=True)
    moved: list[str] = []
    for candidate in pending:
        target = done / candidate.path.name
        if target.exists():
            candidate.path.unlink()  # byte-identical duplicate of a terminal record
            moved.append(f"{candidate.id} (duplicate of a terminal record, removed)")
            continue
        candidate.path.replace(target)
        moved.append(candidate.id)
    return f"consumed {len(moved)} directive(s) for #{issue}: {', '.join(moved)}"
