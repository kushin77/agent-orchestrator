"""The general ledger, derived from an invoice (ERP-03, issue #648).

Acceptance criterion 1 of #648 names the accounting half: "GL postings on
invoice", and criterion 2 requires that a cancellation "reverses prior effects".
Both live here, and both are *derived* rather than declared:

* the **voucher** a posting is derived from is checked against ERP-02's closed
  ``voucher_type`` enum, so a family the model cannot resolve is refused rather
  than posted (this is what makes "GL postings on invoice" a property of the
  schemas and not a claim in a docstring);
* the **receivable and income accounts** come from a :class:`PostingPolicy` the
  caller supplies — the spine declares the *roles* (``receivable``, ``income``)
  and nothing else, so no chart of accounts is baked in;
* the **tax accounts** are read off the invoice's own tax lines, which ERP-02
  requires to carry an ``account``; the spine guesses nothing;
* the **balance** is proven here *and* re-checked by ERP-02, whose
  ``gl-posting`` family rule refuses an unbalanced posting as
  ``unbalanced_posting``.

A reversal is a *new* entry with its sides swapped, never a deletion: ERP-02
carries money as a non-negative number, so "undo this debit" can only mean
"credit the same account by the same amount". :meth:`GeneralLedger.verify`
therefore checks each voucher's net to zero, which is what makes a cancelled
invoice demonstrably reversed rather than merely marked.

---knowledge---
module_id: integrations.erp.tx.ledger
system: integrations
app: erp
solution_class: enterprise
patterns: []
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [PostingPolicy, Entry, GeneralLedger, line_totals, posting_entries, posting_lines, invert, reversal_is_exact]
invariants: ""
gotchas: ""
related: ["#1910"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .model import POSTING_ROLES, Finding, Refused, ROLE_INCOME, ROLE_RECEIVABLE, TxDocument
from .stock import LINE_QTY, LINE_RATE, money

#: The invoice fields this derivation reads. Named once, so the rules below read
#: as rules.
FIELD_LINES = "lines"
FIELD_TAXES = "taxes"
FIELD_TOTAL = "total"
FIELD_ACCOUNT = "account"
FIELD_AMOUNT = "amount"

#: The tolerance a money comparison allows, in minor units. ERP-02's own balance
#: rule uses the same figure; two implementations that disagree about "equal"
#: would make the ledger's verdict depend on which one ran first.
TOLERANCE = 0.005


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


@dataclass(frozen=True)
class PostingPolicy:
    """Which account each posting role is written against.

    Supplied by the caller because it is scenario data: ERP-02 tells the spine
    that a posting line names an ``account``, and nothing tells it *which*
    account a receivable belongs to. A role the policy does not declare is
    ``unknown-account-role`` naming the roles it does declare, so a missing
    account is never silently an empty string.
    """

    accounts: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "accounts", {str(k): str(v) for k, v in self.accounts.items()})

    def resolve(self, role: str) -> str:
        account = self.accounts.get(role)
        if not isinstance(account, str) or not account.strip():
            raise Refused(
                "unknown-account-role",
                f"the posting policy declares no account for role {role!r} "
                f"(declared: {', '.join(sorted(self.accounts)) or 'none'})",
            )
        return account

    def missing(self) -> Tuple[str, ...]:
        """The roles every posting needs but this policy does not declare."""
        return tuple(role for role in POSTING_ROLES if not self.accounts.get(role))

    def to_dict(self) -> Dict[str, Any]:
        return {"accounts": dict(sorted(self.accounts.items()))}


@dataclass(frozen=True)
class Entry:
    """One ledger row: exactly one of a debit or a credit, never both.

    The shape mirrors ERP-02's ``gl-posting`` line, so a posting document can be
    built from these rows without a translation layer — and so a row that carries
    both sides (or neither) is refusable here for the same reason the schema
    refuses it there.
    """

    voucher_type: str
    voucher_id: str
    account: str
    debit: float = 0.0
    credit: float = 0.0
    at: str = ""
    reversal: bool = False

    @property
    def signed(self) -> float:
        """The row's effect: a debit increases, a credit decreases."""
        return money(self.debit - self.credit)

    def inverted(self, *, at: str) -> "Entry":
        """The row that exactly undoes this one: same account, sides swapped."""
        return Entry(
            voucher_type=self.voucher_type,
            voucher_id=self.voucher_id,
            account=self.account,
            debit=self.credit,
            credit=self.debit,
            at=at,
            reversal=not self.reversal,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "voucherType": self.voucher_type,
            "voucherId": self.voucher_id,
            "account": self.account,
            "debit": self.debit,
            "credit": self.credit,
            "at": self.at,
            "reversal": self.reversal,
        }


@dataclass(frozen=True)
class GeneralLedger:
    """An immutable general ledger: the entries posted so far."""

    entries: Tuple[Entry, ...] = field(default_factory=tuple)

    def __len__(self) -> int:
        return len(self.entries)

    def __iter__(self) -> Iterable[Entry]:
        return iter(self.entries)

    def apply(self, entries: Iterable[Entry]) -> "GeneralLedger":
        """Append entries and return the extended ledger."""
        return GeneralLedger(entries=self.entries + tuple(entries))

    def for_document(self, voucher_id: str) -> Tuple[Entry, ...]:
        return tuple(entry for entry in self.entries if entry.voucher_id == voucher_id)

    def net_for_document(self, voucher_id: str) -> float:
        """The net effect a document has left on the ledger (0 when reversed)."""
        return money(sum(entry.signed for entry in self.for_document(voucher_id)))

    def totals(self) -> Tuple[float, float]:
        """The ledger's debits and credits, in that order."""
        debits = money(sum(entry.debit for entry in self.entries))
        credits = money(sum(entry.credit for entry in self.entries))
        return debits, credits

    def balances(self) -> Dict[str, float]:
        """Every account's signed balance, sorted by account."""
        balances: Dict[str, float] = {}
        for entry in self.entries:
            balances[entry.account] = money(balances.get(entry.account, 0.0) + entry.signed)
        return {account: balances[account] for account in sorted(balances)}

    def to_list(self) -> List[Dict[str, Any]]:
        return [entry.to_dict() for entry in self.entries]

    def verify(self) -> List[Finding]:
        """Every way this ledger contradicts itself (empty is OK).

        Three whole-ledger properties, each reported for *every* offender rather
        than only the first: a row is exactly one of a debit or a credit, every
        voucher nets to zero, and the ledger as a whole balances.
        """
        findings: List[Finding] = []
        for entry in self.entries:
            if not entry.account:
                findings.append(
                    Finding("missing-field", "a ledger row names no account", ref=entry.voucher_id)
                )
            debit_side = entry.debit > 0
            credit_side = entry.credit > 0
            if debit_side == credit_side:
                findings.append(
                    Finding(
                        "unbalanced-posting",
                        f"{entry.voucher_id}: a row carries "
                        f"{'both a debit and a credit' if debit_side else 'neither a debit nor a credit'} "
                        f"on {entry.account!r}",
                        ref=entry.voucher_id,
                    )
                )

        vouchers = sorted({entry.voucher_id for entry in self.entries})
        for voucher_id in vouchers:
            net = self.net_for_document(voucher_id)
            if net != 0:
                findings.append(
                    Finding(
                        "unbalanced-posting",
                        f"{voucher_id}: the ledger nets to {net:.2f} rather than zero",
                        ref=voucher_id,
                    )
                )

        debits, credits = self.totals()
        if abs(debits - credits) > TOLERANCE:
            findings.append(
                Finding(
                    "unbalanced-posting",
                    f"the ledger as a whole posts {debits:.2f} of debits against "
                    f"{credits:.2f} of credits",
                )
            )
        return findings


def line_totals(invoice: TxDocument) -> Tuple[float, List[Tuple[str, float]], float]:
    """The invoice's net, its tax rows and its gross — read off the document.

    ``net`` comes from the lines' quantity × rate, each tax row from the tax
    line's own ``account`` and ``amount``, and ``gross`` from the document's
    ``total`` when it declares one. A document that disagrees with itself — a
    total that is not net plus tax — is ``unbalanced-posting`` naming both
    figures, rather than a posting derived from whichever of them was read first.
    """
    lines = invoice.body.get(FIELD_LINES)
    if not isinstance(lines, list) or not lines:
        raise Refused("missing-field", f"{invoice.id}: the invoice declares no lines")
    net = money(0.0)
    for index, line in enumerate(lines):
        if not isinstance(line, Mapping):
            raise Refused("invalid-value", f"{invoice.id}: lines[{index}] must be an object")
        qty = _number(line.get(LINE_QTY))
        rate = _number(line.get(LINE_RATE))
        if qty is None or rate is None:
            raise Refused(
                "invalid-value",
                f"{invoice.id}: lines[{index}] carries no usable quantity and rate",
            )
        net = money(net + money(qty * rate))
    if net <= 0:
        raise Refused("invalid-value", f"{invoice.id}: the invoice nets to {net:.2f}")

    taxes: List[Tuple[str, float]] = []
    raw_taxes = invoice.body.get(FIELD_TAXES) or []
    if not isinstance(raw_taxes, list):
        raise Refused("invalid-value", f"{invoice.id}: taxes must be a list")
    for index, tax in enumerate(raw_taxes):
        if not isinstance(tax, Mapping):
            raise Refused("invalid-value", f"{invoice.id}: taxes[{index}] must be an object")
        account = tax.get(FIELD_ACCOUNT)
        amount = _number(tax.get(FIELD_AMOUNT))
        if not isinstance(account, str) or not account.strip():
            raise Refused(
                "missing-field",
                f"{invoice.id}: taxes[{index}] names no account, so the posting "
                "would have to guess the tax account",
            )
        if amount is None:
            raise Refused(
                "invalid-value", f"{invoice.id}: taxes[{index}] carries no amount"
            )
        if amount > 0:
            taxes.append((account, money(amount)))

    tax_total = money(sum(amount for _account, amount in taxes))
    declared = _number(invoice.body.get(FIELD_TOTAL))
    gross = money(net + tax_total) if declared is None else money(declared)
    if abs(gross - money(net + tax_total)) > TOLERANCE:
        raise Refused(
            "unbalanced-posting",
            f"{invoice.id}: the declared total {gross:.2f} is not the net "
            f"{net:.2f} plus tax {tax_total:.2f}",
        )
    return net, taxes, gross


def posting_entries(
    invoice: TxDocument, policy: PostingPolicy, *, at: str
) -> Tuple[Entry, ...]:
    """The balanced ledger rows a sales invoice posts, or the refusal.

    Double entry, derived: the receivable is debited by the gross, income is
    credited by the net, and each tax row is credited to the account the invoice
    itself named. The rows are proven to balance *here* so a caller of this
    function cannot receive an unbalanced set, and ERP-02 then proves it again
    when the posting document is validated.
    """
    net, taxes, gross = line_totals(invoice)
    entries: List[Entry] = [
        Entry(
            voucher_type=invoice.kind,
            voucher_id=invoice.id,
            account=policy.resolve(ROLE_RECEIVABLE),
            debit=gross,
            at=at,
        ),
        Entry(
            voucher_type=invoice.kind,
            voucher_id=invoice.id,
            account=policy.resolve(ROLE_INCOME),
            credit=net,
            at=at,
        ),
    ]
    for account, amount in taxes:
        entries.append(
            Entry(
                voucher_type=invoice.kind,
                voucher_id=invoice.id,
                account=account,
                credit=amount,
                at=at,
            )
        )

    debits = money(sum(entry.debit for entry in entries))
    credits = money(sum(entry.credit for entry in entries))
    if abs(debits - credits) > TOLERANCE:
        raise Refused(
            "unbalanced-posting",
            f"{invoice.id}: the derived posting debits {debits:.2f} against "
            f"{credits:.2f} of credits",
        )
    return tuple(entries)


def posting_lines(entries: Sequence[Entry]) -> List[Dict[str, Any]]:
    """The ``gl-posting`` document lines for a set of ledger rows.

    A row whose side is zero is omitted rather than written as ``0.00``: ERP-02's
    posting schema requires each line to carry exactly one *non-zero* side, so
    emitting a zero would turn a valid posting into a schema violation.
    """
    lines: List[Dict[str, Any]] = []
    for entry in entries:
        line: Dict[str, Any] = {"account": entry.account}
        if entry.debit > 0:
            line["debit"] = entry.debit
        elif entry.credit > 0:
            line["credit"] = entry.credit
        else:
            raise Refused(
                "unbalanced-posting",
                f"{entry.voucher_id}: a posting line for {entry.account!r} carries "
                "neither a debit nor a credit",
            )
        lines.append(line)
    return lines


def invert(entries: Iterable[Entry], *, at: str) -> Tuple[Entry, ...]:
    """The exact reversal of a set of ledger rows, at ``at``."""
    return tuple(entry.inverted(at=at) for entry in entries)


def reversal_is_exact(ledger: GeneralLedger, voucher_id: str) -> bool:
    """Whether ``voucher_id``'s net effect on the ledger is exactly nothing."""
    return ledger.net_for_document(voucher_id) == 0
