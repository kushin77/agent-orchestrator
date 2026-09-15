"""The indexer seam: where this lane's definitions come from (ERP-03, issue #648).

Acceptance criterion 3 of #648 is that this lane duplicates no constant — every
definition it needs "resolves through the indexer query surface or ERP-02
schemas". This module is the first half of that promise: the query surface.

The index this reads is ``governance/knowledge/catalog.json``, the artefact
``governance/knowledge/cli.py query`` serves and that ``fleet/channel.py`` and
``gateway/mcp/sources.py`` already read as the in-repo knowledge source. The ERP
manifest (``integrations/erp/module.yaml``) registers the catalogue under the
indexer glob ``integrations/erp/catalog/**/*.json`` as kind ``pattern-template``,
so *this lane's document set is a query result, not a list written here*: the
catalogue entries whose ``owning_issue`` is this lane's issue are exactly the
documents ERP-01 declared for it, and a document added or re-owned upstream
changes this lane's set without a line of this file changing.

Two properties are deliberate:

* **the index is required, never guessed.** A missing or unreadable index is
  ``index-unavailable``, and a catalogue file the index names but that will not
  load is ``catalogue-invalid``. Falling back to a hardcoded list would turn a
  broken index into a green lane, which is precisely the false green this
  repository forbids.
* **the index decides *which* files, the file decides *what*.** The index gives
  the path; the catalogue document itself is read for the fields this lane uses
  (``id``, ``family``, ``owning_issue``). The index is not asked to duplicate a
  catalogue entry's body, and a mismatch between the two is reported rather than
  resolved in favour of one of them.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .model import Refused

#: The repository root, derived from this file's own location.
REPO_ROOT = Path(__file__).resolve().parents[3]

#: The built index — the same artefact ``governance/knowledge/cli.py query`` reads.
INDEX_RELPATH = "governance/knowledge/catalog.json"

#: The ERP catalogue directory. This is the manifest's ``catalogue.root``
#: (``integrations/erp/catalog``) narrowed to the document declarations.
CATALOGUE_RELPATH = "integrations/erp/catalog"
CATALOGUE_DOCUMENTS_RELPATH = "integrations/erp/catalog/documents"

#: The indexer kind the manifest registers the catalogue under.
CATALOGUE_KIND = "pattern-template"


@dataclass(frozen=True)
class IndexItem:
    """One item of the knowledge index, as much of it as a consumer needs."""

    id: str
    kind: str
    path: str
    title: str

    @classmethod
    def from_data(cls, data: Mapping[str, Any], *, where: str) -> "IndexItem":
        for field_name in ("id", "kind", "path"):
            value = data.get(field_name)
            if not isinstance(value, str) or not value.strip():
                raise Refused(
                    "index-unavailable",
                    f"{where}: index item is missing {field_name!r}",
                )
        return cls(
            id=data["id"],
            kind=data["kind"],
            path=data["path"],
            title=str(data.get("title", "")),
        )


@dataclass(frozen=True)
class LaneDocument:
    """One catalogue declaration, read through the index.

    ``owning_issue`` is what makes the lane's document set a *query* rather than a
    list: ERP-01 declares each document family and names the child lane that
    builds it, so this lane's set is ``owning_issue == <this issue>``.
    """

    id: str
    family: str
    owning_issue: int
    path: str
    upstream_doctype: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "family": self.family,
            "owning_issue": self.owning_issue,
            "path": self.path,
            "upstream_doctype": self.upstream_doctype,
        }


def index_path(root: Optional[Path] = None) -> Path:
    return (root or REPO_ROOT) / INDEX_RELPATH


def load_index(root: Optional[Path] = None) -> Tuple[Dict[str, Any], Tuple[IndexItem, ...]]:
    """Read the built index, refusing rather than guessing when it is not there."""
    path = index_path(root)
    if not path.is_file():
        raise Refused(
            "index-unavailable",
            f"{path} does not exist — build it with "
            "`python3 governance/knowledge/cli.py build`",
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Refused("index-unavailable", f"{path}: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise Refused("index-unavailable", f"{path}: the index must be an object")
    raw_items = payload.get("items")
    if not isinstance(raw_items, list):
        raise Refused("index-unavailable", f"{path}: the index declares no items")
    items = tuple(
        IndexItem.from_data(entry, where=f"{path}[items[{index}]]")
        for index, entry in enumerate(raw_items)
        if isinstance(entry, Mapping)
    )
    return dict(payload), items


def query(
    index_items: Sequence[IndexItem],
    *,
    kind: Optional[str] = None,
    path_prefix: Optional[str] = None,
) -> List[IndexItem]:
    """Select index items, mirroring ``governance/knowledge/cli.py query``.

    Supplied filters must all match (logical AND) and the result is sorted by
    ``(kind, id)``, which is the ordering the CLI's own query uses — so a caller
    that reads the CLI and a caller that reads this function see one set.
    """
    results = [
        item
        for item in index_items
        if (kind is None or item.kind == kind)
        and (path_prefix is None or item.path.startswith(path_prefix))
    ]
    results.sort(key=lambda item: (item.kind, item.id))
    return results


def read_lane_document(path: Path, *, where: str) -> LaneDocument:
    """Read one catalogue document, refusing rather than defaulting a field."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Refused("catalogue-invalid", f"{where}: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise Refused("catalogue-invalid", f"{where}: a catalogue document must be an object")

    document_id = payload.get("id")
    family = payload.get("family")
    owning_issue = payload.get("owning_issue")
    if not isinstance(document_id, str) or not document_id.strip():
        raise Refused("catalogue-invalid", f"{where}: missing 'id'")
    if not isinstance(family, str) or not family.strip():
        raise Refused("catalogue-invalid", f"{where}: missing 'family'")
    if isinstance(owning_issue, bool) or not isinstance(owning_issue, int):
        raise Refused("catalogue-invalid", f"{where}: missing integer 'owning_issue'")

    provenance = payload.get("provenance")
    upstream_doctype = ""
    upstream = payload.get("upstream")
    if isinstance(upstream, Mapping):
        upstream_doctype = str(upstream.get("doctype", ""))
    if isinstance(provenance, Mapping) and provenance.get("code_copied") is True:
        raise Refused(
            "catalogue-invalid",
            f"{where}: the harvest record claims copied code, which GR-10 forbids",
        )
    return LaneDocument(
        id=document_id,
        family=family,
        owning_issue=owning_issue,
        path=str(path),
        upstream_doctype=upstream_doctype,
    )


def catalogue_documents(
    root: Optional[Path] = None, *, issue: int
) -> Tuple[LaneDocument, ...]:
    """The document declarations this lane owns, resolved through the index.

    The index is read first (so a lane cannot be served a document the indexer
    does not carry), then each catalogue document it names is read. Documents the
    index names but whose declaration is not a *document* declaration (the
    catalogue also holds ``capabilities.json`` and ``module-map.json``) are
    filtered out by the ``documents/`` path, not by a name list.
    """
    base = root or REPO_ROOT
    _payload, items = load_index(base)

    declared = {
        Path(item.path).name: item
        for item in query(
            items,
            kind=CATALOGUE_KIND,
            path_prefix=f"{CATALOGUE_DOCUMENTS_RELPATH}/",
        )
    }
    if not declared:
        raise Refused(
            "index-unavailable",
            f"the index carries no {CATALOGUE_KIND} item under "
            f"{CATALOGUE_DOCUMENTS_RELPATH}/ — the ERP catalogue is not indexed",
        )

    found: List[LaneDocument] = []
    for name in sorted(declared):
        document = read_lane_document(
            base / CATALOGUE_DOCUMENTS_RELPATH / name,
            where=f"{CATALOGUE_DOCUMENTS_RELPATH}/{name}",
        )
        if document.owning_issue == issue:
            found.append(document)
    if not found:
        raise Refused(
            "undeclared-document",
            f"the indexer catalogue declares no document owned by issue #{issue}",
        )
    return tuple(found)


def catalogue_families(documents: Iterable[LaneDocument]) -> Tuple[str, ...]:
    """The families the lane's declarations span, sorted and de-duplicated."""
    return tuple(sorted({document.family for document in documents}))
