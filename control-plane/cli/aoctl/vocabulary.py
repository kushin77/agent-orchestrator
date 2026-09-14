"""The CLI's verbs — one declared command id each, consumed from RC-2's registry.

The CLI owns **no** vocabulary. ``control-plane/control/verbs.yaml`` (issue #553,
RC-2) is the one declaration of every control verb, and this module reads it at
run time. The CLI's own surface is a table of eight ``<command id>`` references
into that registry:

======================  ==========================  ==========================
``ao-control`` verb     declared command id         what it is
======================  ==========================  ==========================
``status``              ``fleet.status``            the fleet's standing
``verbs``               ``fleet.verbs``             the closed vocabulary itself
``pause``               ``fleet.pause``             hold
``resume``              ``fleet.resume``            hold
``stop``                ``fleet.stop``              stop
``kill``                ``fleet.kill``              stop
``override``            ``fleet.override``          irreversible
``audit``               ``board.audit``             the fleet's ledger audit
======================  ==========================  ==========================

**Why the table exists at all.** The issue's acceptance is *"every verb maps to
exactly one declared command id"*. A table makes that checkable rather than
asserted: the mapping is data, and ``check`` (below) proves every row resolves to
a verb the registry declares — so a registry that renames or withholds a verb
makes the CLI **refuse**, never silently drift into asking the plane for a verb
it does not have.

**Why ``audit`` maps to ``board.audit``.** The landed vocabulary has no
``fleet.audit``: the only declared, exposed verb whose local name is ``audit`` and
whose subject is the fleet's own ledger is ``board.audit``
(``governance/dispatch/cli.py audit``). The CLI does not invent a verb to fit the
brief — RC-2 forbids exactly that — so it names the declared id and the choice is
recorded here and in the README.

The registry's **closed sets** — effect classes and refusal codes — are read here
too, so the CLI's refusal matrix can be checked against the same declaration the
server enforces instead of a remembered copy.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parents[3]

#: The committed vocabulary — RC-2's one declaration of a control verb.
REGISTRY_RELATIVE = Path("control-plane") / "control" / "verbs.yaml"
#: The only registry schema this client will speak.
REGISTRY_SCHEMA = "cmr.control-verbs/v1"

#: The CLI's surface: verb -> the declared command id it speaks. Exactly one
#: id per verb, and the id is RC-2's own — never a CLI-local spelling.
SURFACE: tuple[tuple[str, str], ...] = (
    ("status", "fleet.status"),
    ("verbs", "fleet.verbs"),
    ("pause", "fleet.pause"),
    ("resume", "fleet.resume"),
    ("stop", "fleet.stop"),
    ("kill", "fleet.kill"),
    ("override", "fleet.override"),
    ("audit", "board.audit"),
)

#: The effects the CLI asks for a confirmation before sending. Read from the
#: registry's own ``effect_class``, never inferred from a verb name (the
#: gap-analysis §8.4 rule: irreversibility is declared, not inferred).
IRREVERSIBLE_CLASS = "irreversible"


class VocabularyError(RuntimeError):
    """The vocabulary could not be trusted (the CLI fails closed, never guesses)."""


class VocabularyUnreadable(VocabularyError):
    """The registry is missing, unreadable or not the schema this client speaks."""


class UnknownCliVerb(VocabularyError):
    """The CLI has no such verb (the table is the surface)."""


class SurfaceDrift(VocabularyError):
    """The CLI's table names a command id the registry does not declare."""


@dataclass(frozen=True)
class Row:
    """One declared control verb, named as the registry names its fields."""

    id: str
    family: str
    action: str
    source: str
    local: str
    effect_class: str
    capability: str
    audit_action: Optional[str]
    idempotent: bool
    exposed: bool
    why_not_exposed: Optional[str]
    refusals: tuple[int, ...]

    @property
    def mutates(self) -> bool:
        """True for every effect class but ``read`` (the registry's own word)."""
        return self.effect_class != "read"

    @property
    def irreversible(self) -> bool:
        return self.effect_class == IRREVERSIBLE_CLASS


class Registry:
    """RC-2's registry, read once and used as the CLI's whole vocabulary.

    Only what a *client* needs is checked here: the schema id, a non-empty
    ``verbs`` list, the per-row fields the CLI reads, and the two closed sets.
    The full contract — the cross-reference against the five lever files, the
    audit rule in both directions — is the registry's own gate
    (``control-plane/control/cli.py validate``, run by
    ``scripts/check-control-verbs.sh``). Re-deriving it here would be a second
    authority that could disagree with the first.
    """

    def __init__(self, *, document: Mapping[str, Any], path: Optional[Path] = None) -> None:
        self.path = Path(path) if path is not None else None
        schema = document.get("schema")
        if schema != REGISTRY_SCHEMA:
            raise VocabularyUnreadable(
                f"{self.path or 'the registry'} declares schema {schema!r}, "
                f"expected {REGISTRY_SCHEMA!r}"
            )
        entries = document.get("verbs")
        if not isinstance(entries, list) or not entries:
            raise VocabularyUnreadable(f"{self.path or 'the registry'} declares no verbs[] list")
        classes = document.get("effect_classes")
        self.effect_classes: tuple[str, ...] = (
            tuple(str(key) for key in classes) if isinstance(classes, Mapping) else ()
        )
        refusals = document.get("refusals")
        self.refusals: dict[int, str] = (
            {int(code): str(text) for code, text in refusals.items()}
            if isinstance(refusals, Mapping)
            else {}
        )
        self.verbs: dict[str, Row] = {}
        for index, entry in enumerate(entries):
            row = self._row(entry, index)
            self.verbs[row.id] = row

    def _row(self, entry: Any, index: int) -> Row:
        where = f"{self.path or 'the registry'} verbs[{index}]"
        if not isinstance(entry, Mapping):
            raise VocabularyUnreadable(f"{where} is not a mapping")
        verb_id = entry.get("id")
        if not isinstance(verb_id, str) or "." not in verb_id:
            raise VocabularyUnreadable(f"{where} has no `<family>.<action>` id")
        family, _, action = verb_id.partition(".")
        for field in ("source", "local", "effect_class", "capability"):
            value = entry.get(field)
            if not isinstance(value, str) or not value:
                raise VocabularyUnreadable(f"{where} ({verb_id}) has no {field}")
        exposed = entry.get("exposed")
        if not isinstance(exposed, bool):
            raise VocabularyUnreadable(f"{where} ({verb_id}) has no boolean exposed")
        declared_refusals = entry.get("refusals")
        codes: tuple[int, ...] = ()
        if isinstance(declared_refusals, Sequence):
            codes = tuple(int(code) for code in declared_refusals)
        why = entry.get("why_not_exposed")
        return Row(
            id=verb_id,
            family=family,
            action=action,
            source=str(entry["source"]),
            local=str(entry["local"]),
            effect_class=str(entry["effect_class"]),
            capability=str(entry["capability"]),
            audit_action=entry.get("audit") if isinstance(entry.get("audit"), str) else None,
            idempotent=bool(entry.get("idempotent")),
            exposed=exposed,
            why_not_exposed=str(why) if why else None,
            refusals=codes,
        )

    @classmethod
    def load(cls, path: Optional[Path | str] = None) -> "Registry":
        """Read the registry at ``path`` (or the committed one); fail closed."""
        target = Path(path) if path is not None else REPO_ROOT / REGISTRY_RELATIVE
        try:
            raw = target.read_text(encoding="utf-8")
        except OSError as exc:
            raise VocabularyUnreadable(f"{target} is unreadable: {exc}") from exc
        try:
            import yaml
        except ImportError as exc:  # pragma: no cover - PyYAML is a repo dependency
            raise VocabularyUnreadable(f"the YAML reader is unavailable: {exc}") from exc
        try:
            document = yaml.safe_load(raw)
        except yaml.YAMLError as exc:
            raise VocabularyUnreadable(f"{target} is not valid YAML: {exc}") from exc
        if not isinstance(document, Mapping):
            raise VocabularyUnreadable(f"{target} is not a mapping")
        return cls(document=document, path=target)

    # -- the CLI's surface --------------------------------------------------
    def declared_id(self, verb: str) -> str:
        """The declared command id the CLI's ``verb`` speaks."""
        for name, verb_id in SURFACE:
            if name == verb:
                return verb_id
        raise UnknownCliVerb(f"{verb!r} is not one of the CLI's verbs: {self.verb_names()}")

    def verb_names(self) -> tuple[str, ...]:
        """The CLI's verbs, in the order the surface declares them."""
        return tuple(name for name, _ in SURFACE)

    def row_for_verb(self, verb: str) -> Row:
        """The registry row the CLI's ``verb`` speaks — or a named drift.

        A table row whose id the registry does not declare is **not** a verb the
        CLI can send: it would be asking the plane for a word the plane does not
        have. That is the drift this method exists to refuse.
        """
        verb_id = self.declared_id(verb)
        try:
            return self.verbs[verb_id]
        except KeyError as exc:
            raise SurfaceDrift(
                f"the CLI's {verb!r} speaks {verb_id!r}, which "
                f"{self.path or 'the registry'} does not declare"
            ) from exc

    def surface(self) -> tuple[tuple[str, Row], ...]:
        """The whole surface, resolved (raises on the first drifted row)."""
        return tuple((name, self.row_for_verb(name)) for name in self.verb_names())

    def check(self) -> tuple[str, ...]:
        """Every finding the surface and the registry disagree on (empty == OK).

        Called by the CLI's own suite and by ``--help``'s table builder, so the
        mapping cannot rot silently between a registry edit and a CLI release.
        """
        findings: list[str] = []
        seen: dict[str, str] = {}
        for name, verb_id in SURFACE:
            if verb_id in seen:
                findings.append(f"{name!r} and {seen[verb_id]!r} both speak {verb_id!r}")
            seen[verb_id] = name
            row = self.verbs.get(verb_id)
            if row is None:
                findings.append(f"surface: {name!r} speaks {verb_id!r}, which is not declared")
                continue
            if not row.exposed:
                findings.append(
                    f"surface: {name!r} speaks {verb_id!r}, which the registry withholds "
                    f"({row.why_not_exposed})"
                )
            if self.effect_classes and row.effect_class not in self.effect_classes:
                findings.append(
                    f"surface: {verb_id!r} has effect_class {row.effect_class!r}, outside "
                    f"{sorted(self.effect_classes)}"
                )
        return tuple(findings)

    def effect_class_of(self, verb: str) -> str:
        """The declared effect class of the CLI's ``verb`` (never inferred)."""
        return self.row_for_verb(verb).effect_class
