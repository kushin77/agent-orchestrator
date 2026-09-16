"""The workspace: the masters, the documents, the stock ledger and the rail.

One value carries everything a flow mutates, so a flow is a function from a
workspace to a workspace and there is no hidden module state to reset between
runs. That is what makes the golden path reproducible: two runs of the same
seed produce the same documents, the same stock, the same ledger and the same
audit head — and ``cli.py check`` measures exactly that rather than assuming it.

Three seams this type owns:

* **identity is spent, never guessed.** :meth:`Workspace.next_id` hands out a
  monotonically increasing id per prefix, so a document's id is a function of
  what was created before it rather than of a clock or a random source.
* **a duplicate id is refused, not overwritten.** A second document with an id
  already in the store is ``duplicate-id`` naming the family that holds it —
  silently replacing one document with another is how a ledger loses a row.
* **posting is the only thing that moves stock.** :meth:`Workspace.post` adopts
  the ledger the posting layer returns, so a stock movement and its ledger row
  are written together or not at all.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from . import documents, ledger, workflow
from .audit import Rail
from .catalog import Catalog
from .model import (
    ACTION_CREATE,
    ACTION_POST,
    KIND_ITEM,
    KIND_PARTY,
    Model,
    Refused,
)

__all__ = ["DEFAULT_COMPANY", "Workspace"]

#: The internal company every document of a scenario belongs to. A constant
#: rather than configuration: the lane is offline and single-company, and a
#: company that could vary per run would make the transcript irreproducible.
DEFAULT_COMPANY = "COMPANY-1"


class Workspace:
    """The working set: a model, a catalogue, and everything a flow touches."""

    def __init__(
        self, model: Model, catalog: Catalog, *, company: str = DEFAULT_COMPANY
    ) -> None:
        self.model = model
        self.catalog = catalog
        self.company = company
        self.documents: Dict[str, Dict[str, Any]] = {}
        self.stock: Dict[str, Dict[str, float]] = {}
        self.gl: List[Dict[str, Any]] = []
        self.rail: Rail = Rail()
        self._counters: Dict[str, int] = {}

    # --- identity ---------------------------------------------------------

    def next_id(self, prefix: str) -> str:
        """The next id for ``prefix``: ``PREFIX-0001``, ``PREFIX-0002``, …."""
        count = self._counters.get(prefix, 0) + 1
        self._counters[prefix] = count
        return f"{prefix}-{count:04d}"

    # --- the store --------------------------------------------------------

    def find(self, document_id: Any) -> Optional[Dict[str, Any]]:
        """The document with this id, or ``None`` — never a guess."""
        if not isinstance(document_id, str):
            return None
        return self.documents.get(document_id)

    def get(self, document_id: Any) -> Dict[str, Any]:
        """The document with this id, or a refusal naming what is missing."""
        found = self.find(document_id)
        if found is None:
            raise Refused(
                "unknown-document",
                f"no document {document_id!r} exists; known: {sorted(self.documents)}",
            )
        return found

    def create(
        self,
        document: Any,
        *,
        at: str,
        actor: str,
        where: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Validate and store a new document, refusing a duplicate id."""
        parsed = documents.parse(self.model, document, where=where)
        existing = self.find(parsed["id"])
        if existing is not None:
            raise Refused(
                "duplicate-id",
                f"{parsed['id']} already exists as a {existing.get('doctype')}",
            )
        self.documents[parsed["id"]] = parsed
        self.rail = self.rail.append(
            at=at,
            actor=actor,
            action=ACTION_CREATE,
            document=parsed["id"],
            detail=(
                f"{parsed.get('doctype')} created in state "
                f"{parsed.get('state', '<master>')}"
            ),
        )
        return parsed

    def of_kind(self, kind: str) -> Tuple[Dict[str, Any], ...]:
        """Every stored document of ``kind``, in id order."""
        return tuple(
            self.documents[key]
            for key in sorted(self.documents)
            if self.documents[key].get("doctype") == kind
        )

    def replace(self, document: Any, *, where: Optional[str] = None) -> Dict[str, Any]:
        """Re-validate and store a document that already exists.

        Used where a completion writes a field onto a document it has already
        validated (a work order records what it produced). The replacement goes
        through the same schema check as the original, so a rewritten document
        cannot become one the model would refuse to have created.
        """
        parsed = documents.parse(self.model, document, where=where)
        if self.find(parsed["id"]) is None:
            raise Refused(
                "unknown-document", f"{parsed['id']!r} does not exist, so it cannot be replaced"
            )
        self.documents[parsed["id"]] = parsed
        return parsed

    def advance(
        self,
        document: Mapping[str, Any],
        action: str,
        *,
        at: str,
        actor: str,
        target: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Move a document by ``action``, storing it and recording the move.

        The state change and its audit entry are written together, so a
        document cannot be advanced without the move being on the rail.
        """
        advanced, rail = workflow.advance(
            self.model,
            document,
            action,
            actor=actor,
            at=at,
            rail=self.rail,
            target=target,
        )
        self.rail = rail
        self.documents[advanced["id"]] = advanced
        return advanced

    # --- masters ----------------------------------------------------------

    def items(self) -> Mapping[str, Mapping[str, Any]]:
        """The item master, keyed by item code."""
        return {
            key: value
            for key, value in sorted(self.documents.items())
            if value.get("doctype") == KIND_ITEM
        }

    def item(self, item_code: Any) -> Mapping[str, Any]:
        """One item, or a refusal naming what the master does not hold."""
        found = self.find(item_code)
        if found is None or found.get("doctype") != KIND_ITEM:
            raise Refused(
                "unknown-item",
                f"{item_code!r} is not in the item master; known: {sorted(self.items())}",
            )
        return found

    def party(self, party_id: Any, *, expect: Optional[str] = None) -> Mapping[str, Any]:
        """One party, checked against the side of the trade it is used as."""
        found = self.find(party_id)
        if found is None or found.get("doctype") != KIND_PARTY:
            raise Refused(
                "unknown-supplier",
                f"{party_id!r} is not a known party; known: {sorted(self.documents)}",
            )
        if expect is not None and found.get("party_type") != expect:
            raise Refused(
                "unknown-supplier",
                f"{party_id!r} is a {found.get('party_type')!r}, not a {expect!r}",
            )
        return found

    # --- stock ------------------------------------------------------------

    def stock_qty(self, warehouse: str, item_code: str) -> float:
        return float(self.stock.get(warehouse, {}).get(item_code, 0.0))

    def receive_into(self, warehouse: str, item_code: str, qty: float) -> None:
        """Seed stock without a document — used to set up a scenario's opening balance."""
        bucket = self.stock.setdefault(warehouse, {})
        bucket[item_code] = round(bucket.get(item_code, 0.0) + float(qty), 6)

    # --- posting ----------------------------------------------------------

    def post(
        self,
        key: str,
        document: Mapping[str, Any],
        *,
        at: str,
        actor: str,
        lines: Optional[Sequence[Mapping[str, Any]]] = None,
        currency: Optional[str] = None,
        where: Optional[str] = None,
        action: str = ACTION_POST,
    ) -> ledger.Posted:
        """Post a document's stock and ledger effects, and record the posting.

        The ledger returned by the posting layer is *adopted*, never merged in
        place, so the stock a refusal would have moved stays exactly where it
        was. ``action`` names the audited event on the rail — ``receive`` for a
        goods arrival, ``issue`` for components leaving, and so on — so the rail
        reads as the sequence of events rather than as a list of writes.
        """
        posted = ledger.post(
            self.model,
            self.catalog,
            key,
            document,
            entry_id=self.next_id("STE"),
            posting_id=self.next_id("GL"),
            lines=lines,
            stock=self.stock,
            items=self.items(),
            currency=currency,
            where=where or f"{key} from {document.get('id')}",
        )
        self.stock = {warehouse: dict(bucket) for warehouse, bucket in posted.stock.items()}
        if posted.stock_entry is not None:
            self.documents[posted.stock_entry["id"]] = posted.stock_entry
        self.documents[posted.gl_posting["id"]] = posted.gl_posting
        self.gl.append(posted.gl_posting)
        moved = (
            f" and stock {posted.stock_entry['id']} ({posted.stock_entry['purpose']})"
            if posted.stock_entry is not None
            else ""
        )
        self.rail = self.rail.append(
            at=at,
            actor=actor,
            action=action,
            document=str(document.get("id")),
            detail=f"{key}: ledger {posted.gl_posting['id']}{moved}",
        )
        return posted

    # --- reads ------------------------------------------------------------

    def gl_balance(self) -> Dict[str, float]:
        """Each account's debit-minus-credit across every posted ledger row."""
        balance: Dict[str, float] = {}
        for posting in self.gl:
            for line in posting.get("lines", []):
                account = line["account"]
                balance[account] = round(
                    balance.get(account, 0.0)
                    + float(line.get("debit", 0) or 0)
                    - float(line.get("credit", 0) or 0),
                    2,
                )
        return {account: balance[account] for account in sorted(balance) if balance[account]}

    def gl_imbalance(self) -> float:
        """The sum of every account's balance: zero when the books tie out."""
        return round(sum(self.gl_balance().values()), 2)

    def snapshot(self) -> Dict[str, Any]:
        """A canonical rendering of everything the workspace holds."""
        return {
            "documents": {key: self.documents[key] for key in sorted(self.documents)},
            "stock": {
                warehouse: {item: self.stock[warehouse][item] for item in sorted(self.stock[warehouse])}
                for warehouse in sorted(self.stock)
                if self.stock[warehouse]
            },
            "ledger": list(self.gl),
            "accounts": self.gl_balance(),
            "audit": self.rail.to_data(),
            "audit_head": self.rail.head(),
        }
