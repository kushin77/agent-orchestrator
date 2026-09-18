"""The document store the surface serves — the ERP-02 model, behind four verbs.

**Every write goes through the model.** ``DocumentStore`` owns no schema, no
state machine and no rule of its own: a create, a replace and a transition each
call ``integrations/erp/core``'s :class:`~integrations.erp.core.validators.DocumentModel`,
and a document the model refuses never reaches the store. That is what makes the
offline CRUD path evidence about the *model* rather than about a second
implementation of it — and it is why the mutant control that neuters the model's
validator turns this store's driver red.

**Tenancy is a key, not a filter.** The store's keys are
``(tenant, kind, id)``, so a document of another tenant is not *hidden* from a
lookup — it is not in the lookup's address space at all. There is deliberately no
accessor that spans tenants and no argument that widens one, so the surface
cannot ask for another tenant's document even by mistake.

**A document that was never validated was never stored.** Reads return the
projection the ERP-08 layer permitted, not the stored record: see
``surface.py``, which asks for the projection and returns it, so a field a policy
withholds is *absent* from the response rather than blanked. The store itself
does not project — keeping the two responsibilities apart is what lets a reader
prove that a refused read is refused by policy and not by the store.

Nothing here opens a socket, reads the network or consults a clock: the store is
in-memory, seeded from :mod:`integrations.erp.api.fixtures` in every gate.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Tuple

from integrations.erp.core.errors import invalid_body

from . import errors as err

__all__ = ["DocumentStore"]

#: The key of one stored document. A tuple, not a string: two tenants with a
#: document of the same kind and id are two documents, and a joined key would
#: have to invent a separator an id might contain.
Key = Tuple[str, str, str]


def _require_name(value: Any, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise invalid_body(f"{what} must be a non-empty string, got {value!r}")
    return value


class DocumentStore:
    """An in-memory, tenant-scoped store of ERP documents, validated by ERP-02.

    ``model`` is the loaded :class:`~integrations.erp.core.validators.DocumentModel`.
    The store never loads it itself: one model instance serves the surface, the
    store and the health read, so "the schemas the API describes" and "the
    schemas the API enforces" cannot be two different loads.
    """

    def __init__(self, model: Any) -> None:
        self.model = model
        self._documents: Dict[Key, Dict[str, Any]] = {}

    # --- addressing -------------------------------------------------------

    def _key(self, tenant: str, kind: str, document_id: str) -> Key:
        return (
            _require_name(tenant, "a tenant"),
            self._kind(kind),
            _require_name(document_id, "a document id"),
        )

    def _kind(self, kind: str) -> str:
        """The kind, refused by the model's own vocabulary when unknown."""
        name = _require_name(kind, "a document kind")
        self.model.schema_for(name)  # raises unknown_document_kind, naming the known kinds
        return name

    # --- reads ------------------------------------------------------------

    def ids(self, tenant: str, kind: str) -> Tuple[str, ...]:
        """Every document id of ``kind`` in ``tenant``, sorted."""
        name = self._kind(kind)
        return tuple(
            sorted(key[2] for key in self._documents if key[0] == tenant and key[1] == name)
        )

    def peek(self, tenant: str, kind: str, document_id: str) -> Optional[Dict[str, Any]]:
        """The stored document, or ``None`` — **without refusing**.

        The surface needs this so it can decide *permission* before it reports
        *existence*: an absent document and a document in another tenant must be
        indistinguishable to a caller who may not read it, and a lookup that
        raised here would have answered the existence question first.
        """
        return self._documents.get((tenant, self._kind(kind), _require_name(document_id, "a document id")))

    def get(self, tenant: str, kind: str, document_id: str) -> Dict[str, Any]:
        """The stored document, or a ``document_not_found`` refusal."""
        found = self.peek(tenant, kind, document_id)
        if found is None:
            raise err.document_not_found(
                f"no {kind!r} document {document_id!r} in this tenant", kind=kind, id=document_id
            )
        return dict(found)

    def list(self, tenant: str, kind: str) -> Tuple[Dict[str, Any], ...]:
        """Every document of ``kind`` in ``tenant``, ordered by id (deterministic)."""
        return tuple(dict(self._documents[key]) for key in self._keys(tenant, kind))

    def _keys(self, tenant: str, kind: str) -> List[Key]:
        name = self._kind(kind)
        return sorted(key for key in self._documents if key[0] == tenant and key[1] == name)

    # --- writes -----------------------------------------------------------

    def create(self, tenant: str, kind: str, document: Mapping[str, Any]) -> Dict[str, Any]:
        """Validate and store a new document; a duplicate id is a conflict."""
        name = self._kind(kind)
        validated = self.model.validate_document(name, document)
        document_id = _require_name(validated.get("id"), "a document id")
        key = (tenant, name, document_id)
        if key in self._documents:
            raise err.conflict(
                f"{name!r} document {document_id!r} already exists in this tenant",
                kind=name,
                id=document_id,
            )
        self._documents[key] = dict(validated)
        return dict(self._documents[key])

    def replace(
        self, tenant: str, kind: str, document_id: str, document: Mapping[str, Any]
    ) -> Dict[str, Any]:
        """Replace a stored document, re-validated by the model.

        The body must describe the document the path addresses: a payload whose
        ``id`` is another document's would otherwise replace a document the
        caller did not name, so it is refused rather than silently re-keyed.
        """
        name = self._kind(kind)
        key = self._key(tenant, name, document_id)
        if key not in self._documents:
            raise err.document_not_found(
                f"no {name!r} document {document_id!r} in this tenant", kind=name, id=document_id
            )
        validated = self.model.validate_document(name, document)
        if _require_name(validated.get("id"), "a document id") != document_id:
            raise invalid_body(
                f"the body describes {validated.get('id')!r} but the request addresses "
                f"{document_id!r} — a replace may not re-key a document",
                kind=name,
                addressed=document_id,
            )
        self._documents[key] = dict(validated)
        return dict(self._documents[key])

    def delete(self, tenant: str, kind: str, document_id: str) -> Dict[str, Any]:
        """Remove a document; the removed document is returned, not a bare 200."""
        name = self._kind(kind)
        key = self._key(tenant, name, document_id)
        removed = self._documents.pop(key, None)
        if removed is None:
            raise err.document_not_found(
                f"no {name!r} document {document_id!r} in this tenant", kind=name, id=document_id
            )
        return dict(removed)

    def advance(self, tenant: str, kind: str, document_id: str, action: str) -> Dict[str, Any]:
        """Move a document through a transition **its workflow declares**.

        The move is the model's decision: ``DocumentModel.advance`` raises the
        model's own refusals (``unknown_action``/``state_jumped``/``unknown_state``,
        all 409) for a move the workflow does not declare. The document's
        ``docstatus`` is then re-derived from the target state — the parity
        ERP-02 pins in its schemas — and the whole document is re-validated, so a
        document in the store always satisfies the model including its rules.
        """
        name = self._kind(kind)
        key = self._key(tenant, name, document_id)
        current = self._documents.get(key)
        if current is None:
            raise err.document_not_found(
                f"no {name!r} document {document_id!r} in this tenant", kind=name, id=document_id
            )
        target = self.model.advance(current, action)
        workflow = self.model.workflow_for(name)
        state = workflow.assert_declared(target)
        moved = dict(current)
        moved["state"] = state.name
        moved["docstatus"] = state.docstatus
        validated = self.model.validate_document(name, moved)
        self._documents[key] = dict(validated)
        return dict(self._documents[key])

    # --- fixtures ---------------------------------------------------------

    def seed(self, tenant: str, documents: Mapping[str, Any]) -> Tuple[Dict[str, Any], ...]:
        """Store a corpus of ``kind -> document`` (or ``kind -> [documents]``).

        Every seeded document goes through :meth:`create`, so a fixture corpus the
        model refuses fails here — the offline corpus is held to the same model as
        a request body, which is what makes it evidence rather than decoration.
        """
        stored: List[Dict[str, Any]] = []
        for kind in sorted(documents):
            entries = documents[kind]
            if isinstance(entries, Mapping):
                entries = [entries]
            for document in entries:
                stored.append(self.create(tenant, kind, document))
        return tuple(stored)

    def __len__(self) -> int:
        return len(self._documents)
