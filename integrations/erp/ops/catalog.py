"""The lane's declaration set, loaded through one seam, and its frozen schema.

``catalog/ops-catalog.json`` is the ERP-04 lane's single declaration set: which
families it declares and which it consumes, the closed vocabularies it speaks,
the stock effect of each purpose, and the posting rules the buying and
production cycles post under. Nothing else in this package hard-codes a family
list, a purpose or an account, so a change of *source* — the indexer-fed
catalogue of EPIC #645 — is a change of what this module reads rather than a
change of code, which is the same seam discipline the CRM lane's definition set
follows.

**The catalogue is validated, not merely parsed.** ``schema/catalog.schema.json``
is the frozen shape, and it is enforced with ERP-02's own stdlib JSON-Schema
subset (``integrations.erp.core.schema``) rather than a second answer to "is this
declaration well-formed". ``assert_supported_schema`` is the part that keeps the
schema honest: this validator asserts only a fixed keyword set, so a frozen
schema that reaches for a keyword the validator cannot enforce would validate
*less* than it reads — a formality. Rather than let that happen silently, the
load refuses to run such a schema at all (``unsupported-schema-keyword``).

**Structural and semantic checks are separate, and neither is redundant.** The
schema fixes the shape (a posting rule has an account, a side and a basis);
:meth:`Catalog.term` refuses the contradictions a schema cannot express (a
purpose outside the declared stock vocabulary, a voucher the ledger cannot
cite). Both run, and each names itself when it fails, so a reader can tell a
malformed declaration from a contradictory one.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from integrations.erp.core import schema as core_schema

from . import provenance
from .model import MODEL_ROOT, Refused

__all__ = [
    "ANNOTATION_KEYWORDS",
    "CATALOG_FILE",
    "CATALOG_ROOT",
    "SCHEMA_FILE",
    "SUPPORTED_KEYWORDS",
    "Catalog",
    "Kind",
    "Posting",
    "assert_supported_schema",
    "load",
]

CATALOG_ROOT = MODEL_ROOT / "catalog"
CATALOG_FILE = "ops-catalog.json"
SCHEMA_FILE = "schema/catalog.schema.json"

#: The annotation keywords this validator ignores by design (JSON Schema makes
#: them non-verdicts). They may appear anywhere in a schema.
ANNOTATION_KEYWORDS = frozenset(
    {
        "$schema",
        "$id",
        "$comment",
        "title",
        "description",
        "default",
        "examples",
        "deprecated",
        "readOnly",
        "writeOnly",
    }
)

#: Every keyword this validator enforces. A frozen schema that uses a keyword
#: outside this set plus the annotation set is refused rather than run, because a
#: keyword the validator ignores is a constraint nobody applies.
SUPPORTED_KEYWORDS = frozenset(
    {
        "$ref",
        "$defs",
        "definitions",
        "type",
        "enum",
        "const",
        "required",
        "properties",
        "additionalProperties",
        "items",
        "minItems",
        "maxItems",
        "uniqueItems",
        "minLength",
        "maxLength",
        "pattern",
        "format",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
        "allOf",
        "anyOf",
        "oneOf",
        "not",
        "if",
        "then",
        "else",
    }
)

_SUBSCHEMA_KEYS = ("items", "additionalProperties", "not", "if", "then", "else")
_SUBSCHEMA_LIST_KEYS = ("allOf", "anyOf", "oneOf")
_SUBSCHEMA_MAP_KEYS = ("$defs", "definitions", "properties")


def assert_supported_schema(schema: Any, *, where: str = SCHEMA_FILE) -> None:
    """Refuse a schema that uses a keyword this validator cannot enforce.

    Walks the *schema* position of every node — the values of ``properties`` are
    schemas, but the keys of ``properties`` are property names and are not
    keywords — so a property called ``enum`` or ``format`` is not mistaken for a
    keyword, and a real keyword hidden inside a property is still found.
    """
    problems: List[str] = []

    def walk(node: Any, at: str) -> None:
        if isinstance(node, list):
            for index, entry in enumerate(node):
                walk(entry, f"{at}[{index}]")
            return
        if not isinstance(node, dict):
            return
        for key, value in node.items():
            if key.startswith("x-") or key in ANNOTATION_KEYWORDS or key in SUPPORTED_KEYWORDS:
                continue
            problems.append(f"{at}.{key}")
        for key in _SUBSCHEMA_KEYS:
            if key in node:
                walk(node[key], f"{at}.{key}")
        for key in _SUBSCHEMA_LIST_KEYS:
            if key in node:
                walk(node[key], f"{at}.{key}")
        for key in _SUBSCHEMA_MAP_KEYS:
            value = node.get(key)
            if isinstance(value, Mapping):
                for name, subschema in value.items():
                    walk(subschema, f"{at}.{key}.{name}")

    walk(schema, where)
    if problems:
        raise Refused(
            "unsupported-schema-keyword",
            f"{where}: {', '.join(sorted(problems))} — this validator does not "
            "enforce that keyword, so running this schema would be a formality",
        )


@dataclass(frozen=True)
class Kind:
    """One declared family: where it comes from and what drives it."""

    id: str
    family: str
    source: str
    lifecycle: Optional[str]
    role: str

    @classmethod
    def from_data(cls, data: Mapping[str, Any]) -> "Kind":
        return cls(
            id=data["id"],
            family=data["family"],
            source=data["source"],
            lifecycle=data["lifecycle"],
            role=data["role"],
        )


@dataclass(frozen=True)
class Posting:
    """One posting rule: what a family posts, under which purpose and voucher."""

    key: str
    document: str
    purpose: Optional[str]
    voucher_type: str
    gl: Tuple[Mapping[str, str], ...]

    @classmethod
    def from_data(cls, data: Mapping[str, Any]) -> "Posting":
        return cls(
            key=data["key"],
            document=data["document"],
            purpose=data.get("purpose"),
            voucher_type=data["voucher_type"],
            gl=tuple(data["gl"]),
        )


@dataclass(frozen=True)
class Catalog:
    """The loaded declaration set."""

    lane: str
    owning_issue: int
    epic: int
    module: str
    flag: Mapping[str, Any]
    currency: str
    kinds: Tuple[Kind, ...]
    vocabularies: Mapping[str, Tuple[str, ...]]
    stock_effects: Mapping[str, Mapping[str, int]]
    stock_warehouses: Mapping[str, Tuple[str, ...]]
    postings: Tuple[Posting, ...]
    provenance: Mapping[str, Any]
    raw: Mapping[str, Any]

    # --- lookups ----------------------------------------------------------

    def kind(self, name: str) -> Kind:
        for entry in self.kinds:
            if entry.id == name:
                return entry
        raise Refused(
            "unknown-kind",
            f"the catalogue declares no kind {name!r}; known: "
            f"{[entry.id for entry in self.kinds]}",
        )

    def vocabulary(self, name: str) -> Tuple[str, ...]:
        terms = self.vocabularies.get(name)
        if terms is None:
            raise Refused(
                "unknown-vocabulary",
                f"the catalogue declares no vocabulary {name!r}; known: "
                f"{sorted(self.vocabularies)}",
            )
        return terms

    def term(self, vocabulary: str, value: str) -> str:
        terms = self.vocabulary(vocabulary)
        if value not in terms:
            raise Refused(
                "unknown-vocabulary-term",
                f"{value!r} is not a term of {vocabulary!r}; known: {list(terms)}",
            )
        return value

    def posting(self, key: str) -> Posting:
        for rule in self.postings:
            if rule.key == key:
                return rule
        raise Refused(
            "unknown-posting-rule",
            f"no posting rule is declared for {key!r}; known: "
            f"{[rule.key for rule in self.postings]}",
        )

    def stock_warehouses_for(self, purpose: str) -> Tuple[str, ...]:
        """Which warehouse fields a stock movement of ``purpose`` must name."""
        self.term("stock-purposes", purpose)
        return self.stock_warehouses[purpose]

    def stock_effect(self, purpose: str) -> Mapping[str, int]:
        """Which warehouses a movement of ``purpose`` moves, and which way."""
        self.term("stock-purposes", purpose)
        return self.stock_effects[purpose]

    def to_dict(self) -> Dict[str, Any]:
        return json.loads(json.dumps(self.raw))


def _read_json(path: Path) -> Any:
    if not path.is_file():
        raise Refused("declarations-invalid", f"no declaration file at {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Refused(
            "declarations-invalid", f"{path.name} is unreadable ({exc})"
        ) from exc


def load(root: Optional[Path | str] = None) -> Catalog:
    """Load, structurally validate and semantically check the declaration set.

    Raises :class:`Refused` with ``declarations-invalid`` for a malformed
    catalogue, ``unsupported-schema-keyword`` when the frozen schema asks for a
    keyword the validator does not enforce, and the provenance codes when the
    harvest record is missing or claims copied code.
    """
    base = Path(root) if root is not None else CATALOG_ROOT
    schema = _read_json(base / SCHEMA_FILE)
    assert_supported_schema(schema)

    raw = _read_json(base / CATALOG_FILE)
    if not isinstance(raw, Mapping):
        raise Refused(
            "declarations-invalid", f"{CATALOG_FILE} must be a JSON object"
        )
    violations = core_schema.validate(raw, schema, base_dir=base, where=CATALOG_FILE)
    if violations:
        raise Refused(
            "declarations-invalid",
            f"{CATALOG_FILE} does not match {SCHEMA_FILE}: "
            + "; ".join(violations[:4]),
        )

    provenance.enforce_catalogue(raw["provenance"], f"{CATALOG_FILE}")

    kinds = tuple(Kind.from_data(entry) for entry in raw["kinds"])
    seen: Dict[str, int] = {}
    for position, entry in enumerate(kinds):
        if entry.id in seen:
            raise Refused(
                "declarations-invalid",
                f"kinds[{position}] declares {entry.id!r} a second time "
                f"(kinds[{seen[entry.id]}] already does)",
            )
        seen[entry.id] = position

    postings = tuple(Posting.from_data(entry) for entry in raw["postings"])
    keys: Dict[str, int] = {}
    for position, entry in enumerate(postings):
        if entry.key in keys:
            raise Refused(
                "declarations-invalid",
                f"postings[{position}] declares key {entry.key!r} a second time",
            )
        keys[entry.key] = position
        sides = {line["side"] for line in entry.gl}
        if sides != {"debit", "credit"}:
            raise Refused(
                "declarations-invalid",
                f"posting rule {entry.key!r} declares only {sorted(sides)} lines; a "
                "double entry needs both sides",
            )

    vocabularies = {
        name: tuple(terms) for name, terms in raw["vocabularies"].items()
    }
    stock_effects = {
        purpose: dict(effects) for purpose, effects in raw["stock_effects"].items()
    }
    stock_warehouses = {
        purpose: tuple(fields) for purpose, fields in raw["stock_warehouses"].items()
    }
    for purpose in stock_effects:
        if purpose not in vocabularies.get("stock-purposes", ()):
            raise Refused(
                "declarations-invalid",
                f"stock_effects declares {purpose!r}, which the stock-purposes "
                "vocabulary does not contain",
            )
    for purpose in vocabularies.get("stock-purposes", ()):
        if purpose not in stock_effects:
            raise Refused(
                "declarations-invalid",
                f"the stock-purposes vocabulary declares {purpose!r} but "
                "stock_effects does not say what it moves",
            )
        if purpose not in stock_warehouses:
            raise Refused(
                "declarations-invalid",
                f"the stock-purposes vocabulary declares {purpose!r} but "
                "stock_warehouses does not say which warehouses it must name",
            )
        # Where a purpose moves a quantity, it must name that warehouse; the
        # converse does not hold (a manufacture names a source it does not debit
        # on this entry), which is why the two tables are separate.
        for field in stock_effects[purpose]:
            if field not in stock_warehouses[purpose]:
                raise Refused(
                    "declarations-invalid",
                    f"{purpose!r} moves {field} but does not declare naming it",
                )
    for purpose in stock_warehouses:
        if purpose not in stock_effects:
            raise Refused(
                "declarations-invalid",
                f"stock_warehouses declares {purpose!r}, which stock_effects does "
                "not declare",
            )

    return Catalog(
        lane=raw["lane"],
        owning_issue=raw["owning_issue"],
        epic=raw["epic"],
        module=raw["module"],
        flag=dict(raw["flag"]),
        currency=raw["currency"],
        kinds=kinds,
        vocabularies=vocabularies,
        stock_effects=stock_effects,
        stock_warehouses=stock_warehouses,
        postings=postings,
        provenance=dict(raw["provenance"]),
        raw=raw,
    )
