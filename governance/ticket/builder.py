"""The ticket projection builder (issue #401).

A projection is a *pure function of the committed ledgers*: two builds over one
revision are byte-identical, and the projected store can be deleted and rebuilt
to the same hash. That is what makes the ticket a **join**, not a second source
of truth (ADR-0014).

:func:`build` merges every producer's contributions per ticket and enforces the
three rules the contract's ``authority{}`` map implies:

* **one writer per field** — a populated authority-tracked field supplied by two
  producers fails, naming both;
* **the declared writer** — a populated authority-tracked field supplied by any
  other producer fails, naming both;
* **a known field only** — a populated field the frozen contract does not carry
  fails.

:func:`verify` is the rebuild proof: it re-derives the projection, compares it to
the store, deletes the store, rebuilds again and compares hashes. An unresolvable
reference is always a failure naming the file and line — never a skip.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from model import (
    CODE_BUDGET_RECEIPT_UNBACKED,
    CODE_NON_DETERMINISTIC,
    CODE_PRODUCER_MISMATCH,
    CODE_SCHEMA_ENUM,
    CODE_STORE_MISMATCH,
    CODE_TICKET_UNBACKED,
    CODE_TWO_WRITERS,
    CODE_UNKNOWN_FIELD,
    CODE_VALUE_CONFLICT,
    CONTRACT,
    STORE_RELPATH,
    Contribution,
    Contract,
    ProjectionWarning,
    Violation,
    canonical,
    digest,
    first_difference,
    load_contract,
    sorted_tickets,
)
from sources import (
    LessonIndex,
    board_contributions,
    read_attestations,
    read_board,
    read_budgets,
    read_claims,
    read_derived,
    read_lessons,
)


@dataclass
class Projection:
    """The built ticket graph plus every finding the build produced."""

    document: dict[str, Any]
    text: str
    tickets: dict[str, dict[str, Any]]
    contract: Contract
    violations: list[Violation] = field(default_factory=list)
    warnings: list[ProjectionWarning] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations

    @property
    def sha256(self) -> str:
        return digest(self.text)


@dataclass
class VerifyResult:
    """The verdict of ``verify`` (rebuild + determinism + store agreement)."""

    ok: bool
    violations: list[Violation] = field(default_factory=list)
    warnings: list[ProjectionWarning] = field(default_factory=list)
    sha256: str = ""
    ticket_count: int = 0
    store: str = ""


def _assign(document: dict[str, Any], path: str, value: Any) -> None:
    """Set a dotted contract path (``facets.lessons``) into the nested ticket."""
    parts = path.split(".")
    node = document
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value


def _value_key(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def _merge_values(
    contract_field: str, contributions: Sequence[Contribution]
) -> tuple[Any, bool]:
    """The one value a field carries, plus whether the producers disagreed.

    ``evidence`` is the only field that accumulates (it is appended, never
    authority-tracked); every other field must resolve to exactly one value.
    """
    if contract_field == "evidence":
        receipts = [item.value for item in contributions]
        unique: dict[str, Any] = {}
        for receipt in receipts:
            unique[_value_key(receipt)] = receipt
        return [unique[key] for key in sorted(unique)], False

    unique = {}
    for item in contributions:
        unique[_value_key(item.value)] = item.value
    keys = sorted(unique)
    return unique[keys[0]], len(keys) > 1


def _check_enums(ticket_id: str, document: dict[str, Any], contract: Contract) -> list[Violation]:
    """Every populated value must sit inside the contract's closed vocabulary."""
    violations: list[Violation] = []
    facets = document.get("facets") or {}
    checks = (
        ("status", document.get("status")),
        ("kind", document.get("kind")),
        ("facets.lessons.class", (facets.get("lessons") or {}).get("class")),
        ("facets.raid.risk", (facets.get("raid") or {}).get("risk")),
        ("facets.budget.scope.level", ((facets.get("budget") or {}).get("scope") or {}).get("level")),
    )
    for field_name, value in checks:
        allowed = contract.enums.get(field_name) or ()
        if value is None or not allowed:
            continue
        if value not in allowed:
            violations.append(
                Violation(
                    CODE_SCHEMA_ENUM,
                    f"{ticket_id}.{field_name}",
                    f"value {value!r} is outside the contract vocabulary {list(allowed)}",
                )
            )
    for index, receipt in enumerate(document.get("evidence") or []):
        if not isinstance(receipt, dict):
            continue
        allowed = contract.enums.get("evidence.result") or ()
        result = receipt.get("result")
        if allowed and result not in allowed:
            violations.append(
                Violation(
                    CODE_SCHEMA_ENUM,
                    f"{ticket_id}.evidence[{index}].result",
                    f"value {result!r} is outside the contract vocabulary {list(allowed)}",
                )
            )
    return violations


def _budget_backing(
    ticket_id: str, document: dict[str, Any], where: str
) -> list[Violation]:
    """A budget charge's receipt must be an evidence receipt on the same ticket.

    This is the contract's mismatch #10 closed: the receipt that backs the
    charge and the receipt that proves delivery are the same object.
    """
    facets = document.get("facets") or {}
    budget = facets.get("budget")
    if not isinstance(budget, dict) or not budget.get("receipt"):
        return []
    refs = {
        receipt.get("ref")
        for receipt in (document.get("evidence") or [])
        if isinstance(receipt, dict)
    }
    if budget["receipt"] in refs:
        return []
    return [
        Violation(
            CODE_BUDGET_RECEIPT_UNBACKED,
            ticket_id,
            f"budget receipt {budget['receipt']!r} backs no evidence receipt on this ticket",
            where,
        )
    ]


def build(
    root: Path | str = ".",
    *,
    stamp: str | None = None,
    extra: Iterable[Contribution] = (),
) -> Projection:
    """Build the ticket projection from the ledgers under ``root``.

    ``stamp`` is ``None`` in every real build: a timestamp would make two builds
    over one revision differ, which is exactly the non-determinism this
    projection refuses. It exists so the gate can *provoke* that refusal.

    ``extra`` contributes additional ``Contribution`` records — the documented
    negative-control seam. It is how the gate shows the authority and coverage
    rules can fail; no real call site passes it.
    """
    root = Path(root)
    contract = load_contract(root)
    board = read_board(root)

    contributions: list[Contribution] = list(board_contributions(board))
    violations: list[Violation] = []
    warnings: list[ProjectionWarning] = []

    gathered, found = read_claims(root, board)
    contributions.extend(gathered)
    violations.extend(found)

    gathered, found, noted, lesson_index = read_lessons(root, board)
    contributions.extend(gathered)
    violations.extend(found)
    warnings.extend(noted)

    contributions.extend(read_derived(board, lesson_index))

    for reader in (read_budgets, read_attestations):
        gathered, found = reader(root, board)
        contributions.extend(gathered)
        violations.extend(found)

    contributions.extend(extra)

    by_ticket: dict[str, list[Contribution]] = {}
    for contribution in contributions:
        by_ticket.setdefault(contribution.ticket, []).append(contribution)

    tickets: dict[str, dict[str, Any]] = {}
    for ticket_id in sorted_tickets(by_ticket):
        entries = by_ticket[ticket_id]
        by_field: dict[str, list[Contribution]] = {}
        for entry in entries:
            if entry.field is None:
                continue
            by_field.setdefault(entry.field, []).append(entry)

        if not by_field:
            violations.append(
                Violation(
                    CODE_TICKET_UNBACKED,
                    ticket_id,
                    "no ledger supplies any field for this ticket",
                    entries[0].where if entries else "",
                )
            )
            continue

        document: dict[str, Any] = {}
        authority: dict[str, str] = {}
        for contract_field in sorted(by_field):
            field_entries = by_field[contract_field]
            writers = sorted({entry.producer for entry in field_entries})
            where = sorted(entry.where for entry in field_entries)[0]
            if contract_field in contract.tracked:
                if len(writers) > 1:
                    violations.append(
                        Violation(
                            CODE_TWO_WRITERS,
                            f"{ticket_id}.{contract_field}",
                            "field is written by more than one producer: "
                            + ", ".join(writers),
                            where,
                        )
                    )
                elif writers[0] != contract.authority[contract_field]:
                    violations.append(
                        Violation(
                            CODE_PRODUCER_MISMATCH,
                            f"{ticket_id}.{contract_field}",
                            "field is written by "
                            f"'{writers[0]}' but its authority producer is "
                            f"'{contract.authority[contract_field]}'",
                            where,
                        )
                    )
                else:
                    authority[contract_field] = contract.authority[contract_field]
            elif contract_field not in contract.untracked:
                violations.append(
                    Violation(
                        CODE_UNKNOWN_FIELD,
                        f"{ticket_id}.{contract_field}",
                        "field is not part of the frozen contract",
                        where,
                    )
                )

            value, conflicted = _merge_values(contract_field, field_entries)
            if conflicted and len(writers) <= 1:
                violations.append(
                    Violation(
                        CODE_VALUE_CONFLICT,
                        f"{ticket_id}.{contract_field}",
                        f"producer '{writers[0]}' supplied conflicting values",
                        where,
                    )
                )
            _assign(document, contract_field, value)

        document["id"] = ticket_id
        if authority:
            document["authority"] = dict(sorted(authority.items()))
        violations.extend(_check_enums(ticket_id, document, contract))
        violations.extend(
            _budget_backing(
                ticket_id,
                document,
                next(
                    (
                        entry.where
                        for entry in by_field.get("facets.budget", [])
                    ),
                    "",
                ),
            )
        )
        tickets[ticket_id] = document

    ordered = {
        ticket_id: tickets[ticket_id]
        for ticket_id in sorted_tickets(tickets)
    }
    payload: dict[str, Any] = {"contract": CONTRACT, "tickets": list(ordered.values())}
    if stamp is not None:
        payload["generated_at"] = stamp
    text = canonical(payload)
    return Projection(
        document=payload,
        text=text,
        tickets=ordered,
        contract=contract,
        violations=violations,
        warnings=warnings,
    )


def verify(root: Path | str = ".", *, store: Path | str | None = None) -> VerifyResult:
    """Re-derive the projection, prove the store can be rebuilt, and compare.

    Steps, in order: build → compare against the stored projection → delete the
    store → rebuild from the ledgers → compare hashes. A mismatch, a
    non-deterministic rebuild or any authority finding is a failure.
    """
    root = Path(root)
    store_path = Path(store) if store is not None else root / STORE_RELPATH

    first = build(root)
    violations = list(first.violations)
    warnings = list(first.warnings)
    if violations:
        return VerifyResult(
            ok=False,
            violations=violations,
            warnings=warnings,
            sha256=first.sha256,
            ticket_count=len(first.tickets),
            store=str(store_path),
        )

    if store_path.is_file():
        stored_text = store_path.read_text(encoding="utf-8")
        try:
            stored = json.loads(stored_text)
        except ValueError as exc:
            violations.append(
                Violation(
                    CODE_STORE_MISMATCH,
                    str(store_path),
                    f"the projected store is not valid JSON ({exc})",
                )
            )
            stored = None
        if stored is not None and stored_text != first.text:
            where = first_difference(stored, first.document) or "canonical serialization"
            violations.append(
                Violation(
                    CODE_STORE_MISMATCH,
                    str(store_path),
                    f"the stored projection differs from the rebuild at {where}",
                )
            )

    # Rebuildability: the store is not a source of truth, so it must be
    # deletable and reproducible from the ledgers alone.
    store_path.unlink(missing_ok=True)
    second = build(root)
    violations.extend(second.violations)
    if not violations and digest(second.text) != first.sha256:
        violations.append(
            Violation(
                CODE_NON_DETERMINISTIC,
                str(store_path),
                "two builds over one revision differ",
            )
        )

    if not violations:
        store_path.parent.mkdir(parents=True, exist_ok=True)
        store_path.write_text(second.text, encoding="utf-8")

    return VerifyResult(
        ok=not violations,
        violations=violations,
        warnings=warnings + [note for note in second.warnings if note not in warnings],
        sha256=digest(second.text),
        ticket_count=len(second.tickets),
        store=str(store_path),
    )
