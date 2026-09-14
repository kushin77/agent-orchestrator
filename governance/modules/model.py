"""Vocabulary and value objects for the ecosystem module registry (issue #445).

The registry gives the ecosystem **one honest view of every module** —
mandatory status, consumer assets, pin/rev, owning repo, board ref and a health
probe — assembled by **reference** from the hub's own registry
(``vendor/CMR/catalog/mandatory.tsv`` + ``catalog/modules/*/module.json``),
never by copying module source into this repository.

The vocabulary is deliberately small, and it is the thing the issue pins:

* **three states, never two** — ``registered-mandatory``,
  ``target-pending`` (with the blocking hub issue named) and
  ``catalog-module-not-mandatory`` (acceptance 2);
* ``not-a-module`` is **not** a state — it is the refusal returned when a name
  the hub catalog does not carry is asked for membership (acceptance 3). A
  finding about a non-module is not membership;
* anything else that says *no* is a :class:`Refusal`: a finding that names its
  subject, so a check whose pass and fail paths would otherwise collapse into
  the same exit code cannot (AO-GR-4 / GR-12).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterator, Tuple

#: Canonical schema tag of the generated registry document.
SCHEMA = "ao.module-registry/v1"

#: The three states an ecosystem module can be in. Never two.
REGISTERED_MANDATORY = "registered-mandatory"
TARGET_PENDING = "target-pending"
CATALOG_MODULE_NOT_MANDATORY = "catalog-module-not-mandatory"
STATES: Tuple[str, ...] = (
    REGISTERED_MANDATORY,
    TARGET_PENDING,
    CATALOG_MODULE_NOT_MANDATORY,
)

#: Membership is refused, not inferred. Deliberately **not** a member of
#: :data:`STATES` — a name the hub catalog does not carry is not a module in
#: some other state; it is not a module at all (the CMR#952 drift guard).
NOT_A_MODULE = "not-a-module"

#: Health-probe statuses. ``not-run`` is the honest default of the
#: deterministic offline build: a probe that never ran never reports ``ok``.
PROBE_OK = "ok"
PROBE_NOT_RUN = "not-run"
PROBE_UNREACHABLE = "unreachable"
PROBE_NO_TAG = "no-tag"
PROBE_ABSENT = "absent"
PROBE_UNKNOWN = "unknown"
PROBE_STATUSES: Tuple[str, ...] = (
    PROBE_OK,
    PROBE_NOT_RUN,
    PROBE_UNREACHABLE,
    PROBE_NO_TAG,
    PROBE_ABSENT,
    PROBE_UNKNOWN,
)

#: The frozen refusal vocabulary. Every code is produced by a check that can
#: genuinely fail, and every finding names its subject.
REFUSAL_CODES: Tuple[str, ...] = (
    # -- the hub's own registry, mirrored from catalog/validate.py ----------
    "MODULE-DUPLICATE-ID",
    "MODULE-MANIFEST-INVALID",
    "MODULE-MANDATORY-FLAG-INVALID",
    "MODULE-NONMANDATORY-DECLARES-ASSETS",
    "MODULE-ASSET-NO-LIST",
    "MODULE-ASSET-UNSAFE",
    "MODULE-ASSET-NO-SEED",
    "MODULE-ASSET-DRIFT",
    "MODULE-NAME-DRIFT",
    "MODULE-UNREGISTERED-MANDATORY",
    "MODULE-UNREGISTERED-FLAG",
    # -- the declared target set -------------------------------------------
    "TARGET-WITHOUT-ID",
    "TARGET-WITHOUT-BLOCKING",
    "MODULE-TARGET-LANDED-NOT-MANDATORY",
    # -- no vendoring: references, never in-tree source --------------------
    "VENDOR-SOURCE-IN-TREE",
    "VENDOR-IN-TREE-PACKAGE",
    "VENDOR-PATH-OUTSIDE-HUB",
    "VENDOR-EXTRA-SUBMODULE",
)


class CannotAssess(Exception):
    """The registry cannot be built — the authority is absent or unreadable.

    Raised when the hub catalog (or the declared target set) is missing or
    malformed. It is never a pass: the CLI maps it to exit 2.
    """


@dataclass(frozen=True)
class Refusal:
    """A finding that refuses something, naming the thing it refuses."""

    code: str
    subject: str
    detail: str
    source: str = ""

    def render(self) -> str:
        """``CODE: subject — detail [source]`` — the one line a gate greps for."""
        where = " [" + self.source + "]" if self.source else ""
        return "{}: {} — {}{}".format(self.code, self.subject, self.detail, where)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "subject": self.subject,
            "detail": self.detail,
            "source": self.source,
        }


def sorted_refusals(refusals: Iterator[Refusal]) -> Tuple[Refusal, ...]:
    """A deterministic ordering, so two builds over one revision agree."""
    return tuple(sorted(refusals, key=lambda r: (r.code, r.subject, r.detail, r.source)))
