#!/usr/bin/env python3
"""Independent SME reviewer assignment for merge governance (issue #43).

Every PR under merge governance gets an **independent SME reviewer persona**
(assigned, not the author), and the merge decision's evidence is open to an
independent auditor. This module is the governance-layer consumer of the
issue #11 SME reviewer doctrine (``registry/personas/mapping.py``):

* **Posture classes are rigid.** ``executor`` executes primary work;
  ``reviewer`` independently reviews an executor's PR; ``auditor``
  adversarially audits claims/evidence. A persona may only be dispatched to
  the duty matching its posture class.
* **A reviewer is never the author/executor of the work it reviews**
  (separation of duties, AO-GR-14) — ``assign_reviewer`` only ever returns a
  reviewer distinct from the executor.
* **An auditor persona can never be the executing persona of the task it
  audits** — dispatching an auditor-posture persona as the executor, or as the
  reviewer of its own audit subject, is rejected.
* **Assignment is domain-scored**: the best-fit persona matches the PR subject
  against the persona id, owned lanes and expertise (leaderboard
  lenses-as-discriminating-questions model) — the assigned persona is the one
  whose questions change the output.

When the real issue #11 module is importable it is delegated to directly
(genuine consumption); otherwise an identical built-in mirror runs, so the
governance layer stays importable offline without ``jsonschema``. The persona
cards themselves are read from the committed platform library
(``registry/personas/cards``) — read-only consumption.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

PKG_DIR = Path(__file__).resolve().parent  # .../governance/merge
REPO_ROOT = PKG_DIR.parent.parent  # repo root (two levels up from governance/merge)
PLATFORM_CARDS_DIR = REPO_ROOT / "registry" / "personas" / "cards"
PLATFORM_MAPPING_PATH = REPO_ROOT / "registry" / "personas" / "mapping.py"
_PLATFORM_TENANT = "platform"
_REVIEWER_POSTURES = ("reviewer",)


class ReviewerError(Exception):
    """Base error for reviewer assignment."""


class NoReviewerAvailableError(ReviewerError):
    """Raised when no reviewer persona distinct from the executor is available."""


class NoAuditorAvailableError(ReviewerError):
    """Raised when no auditor persona distinct from the executor is available."""


class SeparationViolationError(ReviewerError):
    """Raised on a separation-of-duties violation."""


class SelfReviewError(SeparationViolationError):
    """Raised when the reviewer of a PR would be its author/executor."""


class SelfAuditError(SeparationViolationError):
    """Raised when the auditor of a task would be its executor."""


class AuditorCannotExecuteError(SeparationViolationError):
    """Raised when an auditor-posture persona is dispatched as the executor."""


@dataclass(frozen=True)
class ReviewerPersona:
    """A lightweight persona descriptor returned by assignment."""

    id: str
    name: str
    posture: str
    tenant: str = _PLATFORM_TENANT
    summary: str = ""


# --------------------------------------------------------------------------- #
# Persona-card loading (read-only consumption of the platform library)
# --------------------------------------------------------------------------- #


def load_platform_personas() -> Dict[Tuple[str, str], dict]:
    """Load every committed platform persona card as ``{(tenant, id): card}``.

    Mirrors ``registry/personas/registry.py discover()``'s shape so the cards
    can be handed straight to the issue #11 assignment functions. PyYAML is
    imported lazily so importing this module never requires it.
    """
    import yaml  # type: ignore  # noqa: PLC0415

    if not PLATFORM_CARDS_DIR.is_dir():
        raise ReviewerError(f"platform persona cards not found at {PLATFORM_CARDS_DIR}")
    cards: Dict[Tuple[str, str], dict] = {}
    for path in sorted(PLATFORM_CARDS_DIR.glob("*.yaml")):
        with path.open(encoding="utf-8") as fh:
            card = yaml.safe_load(fh) or {}
        if "id" not in card:
            raise ReviewerError(f"persona card {path.name} has no id")
        tenant = card.get("tenant") or _PLATFORM_TENANT
        cards[(tenant, card["id"])] = card
    if not cards:
        raise ReviewerError("platform persona library is empty (no cards discovered)")
    return cards


# --------------------------------------------------------------------------- #
# Domain-scored assignment — the built-in mirror of issue #11 semantics
# --------------------------------------------------------------------------- #


def _domain_score(card: dict, subject: str) -> int:
    """Score a persona's fit to a PR subject (id + owned lanes + expertise)."""
    hay = subject.lower()
    tokens = [str(card.get("id", ""))] + [
        str(t) for t in card.get("ownedLanes", [])
    ] + [str(t) for t in card.get("expertise", [])]
    return sum(1 for token in tokens if token.lower() and token.lower() in hay)


def _visible_cards(
    cards: Dict[Tuple[str, str], dict], tenant: str
) -> List[dict]:
    return [
        card
        for (_t, _p), card in cards.items()
        if _t == tenant or _t == _PLATFORM_TENANT
    ]


def _best_match(candidates: List[dict], subject: str) -> Optional[dict]:
    if not candidates:
        return None
    scored = sorted(
        ((_domain_score(c, subject), c["id"], c) for c in candidates),
        key=lambda t: (t[0], t[1]),
        reverse=True,
    )
    return scored[0][2]


def _guard_dispatch(card: dict, role: str) -> None:
    posture = card.get("posture")
    if role == "executor":
        if posture == "auditor":
            raise AuditorCannotExecuteError(
                f"persona {card['id']!r} has posture 'auditor' and can never be "
                "the executing persona (separation of duties)"
            )
        if posture != "executor":
            raise SeparationViolationError(
                f"persona {card['id']!r} has posture {posture!r}; only "
                "executor-posture personas execute primary work"
            )
        return
    if role == "reviewer":
        if posture != "reviewer":
            raise SeparationViolationError(
                f"persona {card['id']!r} has posture {posture!r}; only "
                "reviewer-posture personas may act as reviewer"
            )
        return
    if role == "auditor":
        if posture != "auditor":
            raise SeparationViolationError(
                f"persona {card['id']!r} has posture {posture!r}; only "
                "auditor-posture personas may act as auditor"
            )
        return
    raise ReviewerError(f"unknown dispatch role {role!r}")


def mirror_assign_reviewer(
    cards: Dict[Tuple[str, str], dict],
    executor_tenant: str,
    executor_id: str,
    subject: str,
    valid: Optional[Callable[[dict], bool]] = None,
) -> dict:
    """Assign an independent reviewer persona (issue #11 semantics, offline).

    Reviewer posture, visible to the executor's tenant, distinct from the
    executor, domain-scored; falls back to the general ``reviewer`` persona
    when no domain signal matches. ``valid`` (optional) is the
    measured-before-trust gate: only personas that pass the validation corpus
    are assignable.
    """
    visible = _visible_cards(cards, executor_tenant)
    candidates = [
        c
        for c in visible
        if c.get("posture") in _REVIEWER_POSTURES
        and not (c.get("tenant") == executor_tenant and c["id"] == executor_id)
    ]
    if valid is not None:
        candidates = [c for c in candidates if valid(c)]
    if not candidates:
        raise NoReviewerAvailableError(
            "no reviewer persona distinct from the executor is available "
            "(separation of duties)"
        )
    match = _best_match(candidates, subject)
    if match is not None and _domain_score(match, subject) == 0:
        default = next((c for c in candidates if c["id"] == "reviewer"), None)
        if default is not None:
            match = default
    return match


def mirror_assign_auditor(
    cards: Dict[Tuple[str, str], dict],
    executor_tenant: str,
    executor_id: str,
    subject: str,
) -> dict:
    """Assign an auditor persona to audit an executor's merge evidence.

    Only auditor-posture personas audit; the auditor is never the executor of
    the task it audits (``AuditorCannotExecuteError`` / ``SelfAuditError``).
    """
    visible = _visible_cards(cards, executor_tenant)
    auditors = [
        c
        for c in visible
        if c.get("posture") == "auditor"
        and not (c.get("tenant") == executor_tenant and c["id"] == executor_id)
    ]
    if not auditors:
        raise NoAuditorAvailableError("no auditor persona is available for assignment")
    match = _best_match(auditors, subject)
    return match


# --------------------------------------------------------------------------- #
# The assigner — delegates to the real issue #11 module when importable
# --------------------------------------------------------------------------- #


class PersonaAssigner:
    """Offline reviewer/auditor assigner over a persona-card library.

    ``cards`` defaults to the committed platform library. When
    ``use_real_mapping`` is true (default) and the issue #11 module
    (``registry/personas/mapping.py``) is importable, assignment delegates to
    its ``assign_reviewer`` / ``assign_auditor`` — genuine consumption; the
    built-in mirror is the offline fallback.
    """

    def __init__(
        self,
        cards: Optional[Dict[Tuple[str, str], dict]] = None,
        use_real_mapping: bool = True,
        valid: Optional[Callable[[dict], bool]] = None,
    ) -> None:
        if cards is None:
            cards = load_platform_personas()
        self.cards = cards
        self.valid = valid
        self._mapping = None
        if use_real_mapping and PLATFORM_MAPPING_PATH.is_file():
            try:
                self._mapping = _import_mapping(PLATFORM_MAPPING_PATH)
            except Exception:  # pragma: no cover - offline fallback path
                self._mapping = None

    # -- reviewer -------------------------------------------------------------

    def assign_reviewer(
        self, executor_tenant: str, executor_id: str, subject: str
    ) -> ReviewerPersona:
        card = self._pick_reviewer_card(executor_tenant, executor_id, subject)
        return ReviewerPersona(
            id=card["id"],
            name=card.get("name", card["id"]),
            posture=card["posture"],
            tenant=card.get("tenant", _PLATFORM_TENANT),
            summary=card.get("summary", ""),
        )

    def _pick_reviewer_card(
        self, executor_tenant: str, executor_id: str, subject: str
    ) -> dict:
        if self._mapping is not None:
            return self._mapping.assign_reviewer(
                self.cards, executor_tenant, executor_id, subject
            )
        return mirror_assign_reviewer(
            self.cards,
            executor_tenant,
            executor_id,
            subject,
            valid=self.valid,
        )

    # -- auditor --------------------------------------------------------------

    def assign_auditor(
        self, executor_tenant: str, executor_id: str, subject: str
    ) -> ReviewerPersona:
        card = self._pick_auditor_card(executor_tenant, executor_id, subject)
        return ReviewerPersona(
            id=card["id"],
            name=card.get("name", card["id"]),
            posture=card["posture"],
            tenant=card.get("tenant", _PLATFORM_TENANT),
            summary=card.get("summary", ""),
        )

    def _pick_auditor_card(
        self, executor_tenant: str, executor_id: str, subject: str
    ) -> dict:
        if self._mapping is not None:
            return self._mapping.assign_auditor(
                self.cards, executor_tenant, executor_id, subject
            )
        return mirror_assign_auditor(
            self.cards, executor_tenant, executor_id, subject
        )

    # -- separation guards ------------------------------------------------------

    def guard_dispatch(self, persona: ReviewerPersona, role: str) -> None:
        """Refuse to dispatch a persona outside its posture class."""
        card = self.cards.get((persona.tenant, persona.id))
        if card is None:
            raise ReviewerError(
                f"persona {persona.tenant}/{persona.id} not in the assigner's library"
            )
        _guard_dispatch(card, role)

    def assert_reviewer_distinct(
        self, executor_tenant: str, executor_id: str, reviewer: ReviewerPersona
    ) -> None:
        if reviewer.tenant == executor_tenant and reviewer.id == executor_id:
            raise SelfReviewError(
                f"reviewer persona {reviewer.id!r} is the executor of the same "
                "PR (separation of duties)"
            )

    def assert_auditor_not_executor(
        self, auditor: ReviewerPersona, executor_tenant: str, executor_id: str
    ) -> None:
        """The auditor is never the executing persona of the task it audits.

        Mirrors issue #11 ``assert_auditor_not_executor``: the same identity as
        both auditor and executor is a SelfAuditError, and an auditor-posture
        persona cannot be the executor (guard_dispatch refuses it).
        """
        if auditor.tenant == executor_tenant and auditor.id == executor_id:
            raise SelfAuditError(
                f"auditor persona {auditor.id!r} cannot be the executing persona "
                "of the task it audits (separation of duties)"
            )
        executor_card = self.cards.get((executor_tenant, executor_id))
        if executor_card is not None:
            # an auditor-posture "executor" would be refused here too
            _guard_dispatch(executor_card, "executor")


def _import_mapping(path: Path):
    """Import the issue #11 mapping module from its absolute path."""
    import importlib.util  # noqa: PLC0415

    spec = importlib.util.spec_from_file_location("_persona_mapping", path)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise ReviewerError(f"cannot load persona mapping module at {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
