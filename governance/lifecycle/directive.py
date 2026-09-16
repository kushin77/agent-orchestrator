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

**The one case the gate cannot serve, and its terminal mode (#861).** That gate
presumes the order's issue has a change of its own to have landed. A **superseded**
issue has none: it was closed because other work replaced it, so it is closed on the
board, absent from the lifecycle record, and — with no change of its own — it can
never satisfy the landed predicate. Measured on the live mailbox: #692 is closed
(2026-09-16), ``.fleet/lifecycle/692.json`` does not exist, its authorisation
``brain-directive-a2a-692-6b3951b31bb3db7d.json`` has sat in ``.fleet/sent/`` since
2026-09-15, and no documented command could retire it. :func:`retire_superseded` is
that path, and it is deliberately *narrower* than the landed gate rather than wider:

* it demands **positive evidence of closure** — the issue is closed in the board
  snapshot, which must be readable and fresh, and it refuses on a board it cannot
  read, does not carry the issue in, or is older than the dispatch layer's own
  snapshot bar (the bar is *read* from that layer, never re-invented);
* it demands a **named successor** that is a real issue and not the issue itself: a
  supersession with no successor is an assertion, not evidence;
* it refuses when the **close-out can see the issue** — the landed gate applies
  there and the ordinary, better-evidenced move must be used, so the new mode can
  never become the easy way past the old one;
* it refuses an **open** issue, which is the #821 invariant, preserved: the negative
  control that stops this mode becoming a bypass.

The move records *why* it was made. A directive retired by supersession carries a
``retired`` block naming the reason, the successor and the board it was read from,
so the terminal record is evidence rather than an unexplained disappearance.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from governance.lifecycle.model import owes_closure
from governance.policy import lease

#: The two mailboxes a directive can occupy. ``sent`` is where the brain mints an
#: order; ``done`` is terminal. Nothing else in this repository may move a record
#: between them, so this module is the single owner of the transition.
SENT_DIR = ".fleet/sent"
DONE_DIR = ".fleet/done"

STATE_SENT = "sent"
STATE_DONE = "done"

#: The reasons a directive may be retired BY. ``landed`` is the ordinary path
#: (#821): the change the order asked for landed. ``superseded`` is the terminal
#: mode above (#861), for a closed issue with no change of its own.
REASON_LANDED = "landed"
REASON_SUPERSEDED = "superseded"

#: The reasons :func:`retire_superseded` may be called with. A reason outside this
#: set is refused by name, because a terminal move whose reason is unrecognised is
#: not a move anybody can audit afterwards.
SUPERSESSION_REASONS = (REASON_SUPERSEDED,)

#: The board snapshot the closure oracle is read from. The same committed file the
#: brain's own closure guard reads (#693), so the two cannot disagree about what
#: the board said.
BOARD_PATH = Path(".board/snapshot.json")

#: How stale the board may be for a retirement to be decided from it. READ from the
#: dispatch layer that owns the rule — a second, parallel freshness bar declared
#: here would be exactly the kind of drifted duplicate this repo's gates exist to
#: catch (GR-10: harvest the contract, never re-invent it).
BOARD_MAX_AGE_MINUTES = lease.SNAPSHOT_STALENESS_MINUTES

#: Tri-state exit codes (repo convention, GR-12 / guardrails/honesty).
EXIT_OK = 0
EXIT_NOT_OK = 1
EXIT_CANNOT_ASSESS = 2


class DirectiveRefused(RuntimeError):
    """The terminal move was refused by name. Nothing was moved."""


@dataclass(frozen=True)
class Retirement:
    """Why a directive is retiring, and the evidence for that why.

    ``reason`` is one of the declared ``REASON_*`` values. ``superseded_by`` is the
    issue that superseded this one — required for :data:`REASON_SUPERSEDED`, whose
    whole content is that another issue replaced this one. ``detail`` is free text
    the operator's record keeps; it is never the evidence, only the explanation.
    """

    reason: str
    superseded_by: int | None = None
    detail: str = ""

    def as_record(self, *, by: str, at: str, board: str = "") -> dict:
        """The stamp written into the record as it enters the terminal mailbox.

        A retirement is a state change to a governed store, so it is recorded as
        one: what the reason was, on whose authority, when, and — for a
        supersession — the successor and the board edition the closure was read
        from. Without the board edition, "it was closed" would be unfalsifiable
        later, because the snapshot is refreshed continuously.
        """
        record: dict = {"reason": self.reason, "at": at, "by": by}
        if self.superseded_by is not None:
            record["superseded_by"] = self.superseded_by
        if board:
            record["board"] = board
        if self.detail:
            record["detail"] = self.detail
        return record


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


@dataclass(frozen=True)
class Board:
    """The closure oracle: which issues the board snapshot says are closed."""

    closed: dict[int, bool]
    generated_at: str
    age_minutes: float


def board_closure(
    path: Path | str = BOARD_PATH,
    *,
    now: datetime | None = None,
    max_age_minutes: int | None = None,
) -> Board:
    """Read closed-ness out of the committed board snapshot, or refuse by name.

    The three ways this refuses are the three ways the oracle would otherwise fail
    **open**, and a closure oracle that fails open is worse than no oracle at all:

    * the file cannot be read or parsed — CANNOT-ASSESS is never "closed";
    * the file is older than the dispatch layer's own staleness bar
      (:data:`BOARD_MAX_AGE_MINUTES`, read from ``governance.policy.lease``): a
      snapshot is refreshed continuously, so "the board said closed" is only
      evidence while the board is the board;
    * it is readable and fresh but does not carry the issue at all — absence is not
      permission, so the caller refuses rather than defaulting.

    The returned :class:`Board` carries the edition's ``generated_at``, because a
    snapshot is a mutable file: a retirement recorded without the edition it was
    decided from could not be checked afterwards.
    """
    limit = BOARD_MAX_AGE_MINUTES if max_age_minutes is None else int(max_age_minutes)
    target = Path(path)
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DirectiveRefused(
            f"the board snapshot {target} cannot be read ({exc}); a closure oracle that cannot be "
            "read is CANNOT-ASSESS, never a closed issue: refresh it with "
            "`python3 governance/dispatch/cli.py snapshot --from-github`"
        ) from exc
    generated_at = str(payload.get("generated_at") or "")
    try:
        generated = datetime.strptime(generated_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        raise DirectiveRefused(
            f"the board snapshot {target} carries no readable generated_at ({generated_at!r}); "
            "the edition a retirement was decided from cannot be established, so nothing is retired"
        ) from None
    moment = now or datetime.now(timezone.utc)
    age = max(0.0, (moment - generated).total_seconds() / 60.0)
    if age > limit:
        raise DirectiveRefused(
            f"the board snapshot {target} is {age:.1f}m old (bar {limit}m, from "
            "governance.policy.lease.SNAPSHOT_STALENESS_MINUTES); a stale board is not evidence of "
            "closure — refresh it with `python3 governance/dispatch/cli.py snapshot --from-github`"
        )
    issues = payload.get("issues")
    closed: dict[int, bool] = {}
    for item in issues if isinstance(issues, list) else []:
        if not isinstance(item, dict):
            continue
        try:
            number = int(item.get("number") or 0)
        except (TypeError, ValueError):
            continue
        if number:
            closed[number] = str(item.get("state") or "").strip().lower() == "closed"
    return Board(closed=closed, generated_at=generated_at, age_minutes=age)


def _supersession_refusal(
    issue: int,
    landed: Mapping[int, bool],
    retirement: Retirement,
    closed: Mapping[int, bool] | None,
) -> str | None:
    """Why this supersession may NOT be retired, or None when it may.

    Every return is a refusal **by name**; None is the only permit, and it is
    granted only when all of the following hold. The order below is the order of
    the checks, because the first refusal an operator reads should be the one that
    would have to be fixed first:

    1. the reason is one of the declared :data:`SUPERSESSION_REASONS`;
    2. a successor issue is named, it is a real issue number, and it is not the
       issue itself — a supersession with no successor is an assertion, not
       evidence, and #861 is a task to make an authorisation retirable, not to make
       it disappearable;
    3. the closure oracle was supplied and CARRIES the issue — an oracle that cannot
       be read, or does not know the issue, is CANNOT-ASSESS, and not knowing is
       never a licence to retire;
    4. the issue is CLOSED in it. An OPEN issue is refused here: this is the #821
       invariant, and the negative control that stops the new mode becoming a
       bypass — the same directive that stays in ``sent/`` today stays in ``sent/``;
    5. the close-out cannot see the issue. If it can, the ordinary landed gate
       applies and it is a *better* evidenced move, so this mode refuses rather than
       becoming the easier road past it — including when the close-out shows the
       work has NOT landed, which would be live work.
    """
    if retirement.reason not in SUPERSESSION_REASONS:
        return (
            f"unknown retirement reason {retirement.reason!r}; the declared reasons are "
            f"{', '.join(SUPERSESSION_REASONS)}"
        )
    successor = retirement.superseded_by
    if isinstance(successor, bool) or not isinstance(successor, int) or successor < 1:
        return (
            f"reason {retirement.reason!r} names no successor issue; a supersession with no "
            "successor is an assertion, not evidence"
        )
    if successor == issue:
        return f"#{issue} cannot have superseded itself"
    if closed is None:
        return (
            "the closure oracle was not supplied, so whether the issue is closed cannot be "
            "assessed; refusing rather than defaulting to allow"
        )
    state = closed.get(issue)
    if state is None:
        return (
            f"the board snapshot does not carry #{issue}; refusing rather than defaulting to allow — "
            "refresh it with `python3 governance/dispatch/cli.py snapshot --from-github`"
        )
    if not state:
        return (
            f"#{issue} is OPEN on the board; a directive for live work is not retired by "
            f"supersession, so it stays in {SENT_DIR}/ and the #821 gate is untouched"
        )
    known = landed.get(issue)
    if known is False:
        return (
            f"the lifecycle record shows #{issue}'s change has NOT landed; the order is live, not "
            f"superseded, so it stays in {SENT_DIR}/"
        )
    if known is not None:
        return (
            f"the lifecycle record can see #{issue}, so the ordinary landed gate applies and is the "
            "better-evidenced move; refusing to retire by supersession what the close-out can retire itself"
        )
    return None


def consume(
    root: Path,
    directive_id: str,
    landed: Mapping[int, bool] | None = None,
    *,
    retirement: Retirement | None = None,
    closed: Mapping[int, bool] | None = None,
    board: str = "",
    retired_by: str = "governance/lifecycle/directive.py",
) -> str:
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

    ``retirement`` selects the ONE terminal mode that gate cannot serve (#861): an
    issue closed *without* a change of its own, i.e. superseded. It is narrower, not
    wider — :func:`_supersession_refusal` is the whole contract, and the negative
    control that keeps #821 intact is that an **open** issue with an unlanded change
    is still refused. A retirement made that way is *stamped into the record* on the
    way through (reason, successor, board edition, who) so the terminal copy is
    evidence rather than an unexplained disappearance.

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

    stamp: dict | None = None
    if retirement is None:
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
    else:
        refusal = _supersession_refusal(issue, landed, retirement, closed)
        if refusal is not None:
            raise DirectiveRefused(
                f"directive {record.id} orders #{issue}, which is not retirable by "
                f"{retirement.reason!r}: {refusal}"
            )
        stamp = retirement.as_record(
            by=retired_by,
            at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            board=board,
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
        if stamp is None:
            candidate.path.replace(target)
        else:
            # The retirement is written INTO the record as it enters the terminal
            # mailbox, so the move is auditable from the artifact alone. The
            # collision check above compared the ORIGINAL bytes, so stamping here
            # cannot make an idempotent re-run look like a differing edition.
            payload = _read(candidate.path) or {}
            payload["retired"] = stamp
            target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            candidate.path.unlink()
        moved.append(candidate.id)
    if stamp is not None:
        return (
            f"retired {len(moved)} directive(s) for #{issue} by {stamp['reason']} "
            f"(superseded by #{retirement.superseded_by}): {', '.join(moved)}"
        )
    return f"consumed {len(moved)} directive(s) for #{issue}: {', '.join(moved)}"


def retire_superseded(
    root: Path,
    directive_id: str,
    superseded_by: int,
    *,
    landed: Mapping[int, bool] | None = None,
    closed: Mapping[int, bool] | None = None,
    board: str = "",
    detail: str = "",
) -> str:
    """Retire a stranded authorisation whose issue was closed *without* a change.

    The named entry point for the terminal mode above, so the operator (and the
    gate) call the same thing: build the :class:`Retirement` and hand it to
    :func:`consume`. ``closed`` is the closure oracle — either a mapping, or the
    product of :func:`board_closure`; the CLI takes care of that. Nothing is
    defaulted: a missing oracle is a refusal, not an assumption.
    """
    return consume(
        root,
        directive_id,
        landed,
        retirement=Retirement(reason=REASON_SUPERSEDED, superseded_by=superseded_by, detail=detail),
        closed=closed,
        board=board,
    )


# --- operator CLI ------------------------------------------------------------
#
# The close-out drives `consume` as one of its invariants (`governance/lifecycle/
# cli.py`, which is not this module's file). The supersession mode needs an
# operator-driven entry as well, because the case it serves is by definition one
# the close-out never collected: the issue is absent from the lifecycle record, so
# no close-out run will ever reach the stranded authorisation. That entry is here,
# beside the move it performs, so the mailbox's owner remains the mailbox's only
# writer.


def _closeout_seen(root: Path) -> dict[int, bool]:
    """Which issues the close-out has collected — the mode's last check, answered honestly.

    The question the last check asks is *can the close-out see this issue?*, because
    if it can, the ordinary landed gate is available and is the better-evidenced
    move. The close-out's own mark that it collected an item is its journal
    (``.fleet/lifecycle/<issue>.json``), so that is what is read — the journal
    path and the directory are taken from the module that owns them rather than
    restated here, so the two cannot drift apart.

    A journal is deliberately NOT read as evidence that the issue is closed. Closure
    comes from the board (the oracle); this map only says *seen*, and every entry is
    the same value because "seen" is the only fact being claimed. The consequence is
    the safe one: an issue the close-out collected is refused this mode and sent to
    the ordinary gate, which for a board-closed issue succeeds — so routing here
    cannot create a strand, it can only decline to.

    Reading no journals (an empty directory) is NOT "the close-out sees nothing is
    closed": it is simply the absence of that mark, and the closure oracle still
    decides.
    """
    from governance.lifecycle import cli as lifecycle_cli

    directory = Path(root) / lifecycle_cli.JOURNAL_DIR
    seen: dict[int, bool] = {}
    if not directory.exists():
        return seen
    for path in sorted(directory.glob("*.json")):
        try:
            seen[int(path.stem)] = True
        except ValueError:
            continue
    return seen


def cmd_status(args: argparse.Namespace) -> int:
    root = Path(args.root)
    found = records(root)
    sent = [record for record in found if record.state == STATE_SENT]
    print(
        f"directive mailbox: {len(sent)} pending in {SENT_DIR}/, "
        f"{len(found) - len(sent)} terminal in {DONE_DIR}/"
    )
    for record in sent:
        print(f"  pending  {record.id}  issue={record.issue}")
    return EXIT_OK


def cmd_retire(args: argparse.Namespace) -> int:
    root = Path(args.root)
    try:
        oracle = board_closure(args.board, max_age_minutes=args.max_age_minutes)
    except DirectiveRefused as exc:
        print(f"directive retire: CANNOT-ASSESS — {exc}", file=sys.stderr)
        return EXIT_CANNOT_ASSESS
    try:
        detail = retire_superseded(
            root,
            args.directive,
            args.superseded_by,
            landed=_closeout_seen(root),
            closed=oracle.closed,
            board=oracle.generated_at,
            detail=args.detail or "",
        )
    except DirectiveRefused as exc:
        print(f"directive retire: REFUSED — {exc}", file=sys.stderr)
        return EXIT_NOT_OK
    print(
        f"directive retire: {detail} [board {args.board} {oracle.generated_at}, "
        f"{oracle.age_minutes:.1f}m old, #{args.superseded_by} successor]"
    )
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="governance-lifecycle-directive", description=__doc__)
    parser.add_argument("--root", default=".", help="the repository root holding .fleet/ (default: cwd)")
    sub = parser.add_subparsers(dest="command", required=True)
    status = sub.add_parser("status", help="list the directives the mailboxes hold")
    status.set_defaults(func=cmd_status)
    retire = sub.add_parser(
        "retire",
        help="retire a stranded authorisation for an issue closed without a change of its own",
    )
    retire.add_argument("--directive", required=True, help="the directive id, or its filename stem")
    retire.add_argument("--superseded-by", required=True, type=int, help="the issue that superseded it")
    retire.add_argument("--board", default=str(BOARD_PATH), help="the board snapshot the closure is read from")
    retire.add_argument("--max-age-minutes", type=int, default=None, help="override the board freshness bar")
    retire.add_argument("--detail", default="", help="free text recorded with the retirement")
    retire.set_defaults(func=cmd_retire)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
