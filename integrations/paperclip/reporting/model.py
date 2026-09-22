"""Vocabulary for the paperclip reporting half — the module brief (issue #447).

The brief is the organized, per-module statement of *what every repo must carry,
at which pin, and whether it is current*. It is composed from the ecosystem
module registry (``governance/modules/``, issue #445) and the hub files that
registry cites — never re-derived here: the **three states**
(``registered-mandatory`` / ``target-pending`` / ``catalog-module-not-mandatory``)
and the ``not-a-module`` **refusal** are imported from
:mod:`governance.modules.model`, and so is :class:`~governance.modules.model.Refusal`,
because a second vocabulary for the same thing is how a brief starts disagreeing
with the registry it claims to read.

What this module adds is the property the issue pins: **every claim resolves**.
A brief line is not prose — it is a :class:`Claim` that carries the registry row
or the cited path it came from. :func:`claim_findings` refuses, **naming the
line**, any claim that cites nothing or cites something that resolves to
nothing. That is the difference between a brief that is *runnable* and one that
is *truthful* (``kushin77/deepseek#117``).#:
#: Since issue #592 the resolution rule itself is **declared**, not written here:
#: ``claim-policy.json`` states which prefix names a registry row, which bases a
#: path may resolve against, which artifact the line is named in and which code a
#: non-resolving line produces. :func:`resolves` and :func:`claim_findings` take
#: that policy and read it — a rule restated here and a rule declared there would
#: eventually disagree, and the brief would be the one that lied.

---knowledge---
module_id: integrations.paperclip.reporting.model
system: integrations
app: paperclip
solution_class: enterprise
patterns: []
derives_from: null
owner_sme: paperclip
tier: L1
interfaces: [Claim, ClaimBook, resolves, claim_findings, state_of]
invariants: ""
gotchas: ""
related: ["#1910"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from governance.modules.model import (  # the authority's vocabulary, reused not redefined
    CATALOG_MODULE_NOT_MANDATORY,
    NOT_A_MODULE,
    REGISTERED_MANDATORY,
    STATES,
    TARGET_PENDING,
    CannotAssess,
    Refusal,
    sorted_refusals,
)

from integrations.paperclip.reporting.policy import ClaimPolicy

#: Canonical schema tag of the composed brief document.
SCHEMA = "ao.module-brief/v1"

#: The frozen artifact — the brief's canonical home in this repository.
ARTIFACT = "docs/MODULE-BRIEF.md"

#: The refusal vocabulary of this lane. Every code is produced by a check that
#: can genuinely fail, and every finding names its subject (GR-12 / AO-GR-4).
BRIEF_CODES: Tuple[str, ...] = (
    # -- the persona's declaration vs the tools it needs (acceptance 1) -----
    "BRIEF-CAPABILITY-UNDECLARED",
    "BRIEF-CAPABILITY-TOOL-UNGRANTED",
    "BRIEF-ARTIFACT-HOME-UNGRANTED",
    "BRIEF-SEED-DRIFT",
    # -- a source the brief claims to read is not there ---------------------
    "BRIEF-SOURCE-MISSING",
    # -- what a brief line must be (acceptance 3) ---------------------------
    "BRIEF-CLAIM-UNRESOLVED",
    # -- what a brief is composed from (acceptance 2) -----------------------
    "BRIEF-STATE-VOCABULARY-DRIFT",
    "BRIEF-SUMMARY-DRIFT",
    "BRIEF-MODULE-NO-PIN",
    "BRIEF-MODULE-NO-REV",
    "BRIEF-ASSET-NO-SEED",
    # -- the emitted machine document vs the frozen schema (issue #592) -----
    "BRIEF-SCHEMA-INVALID",
    # -- pending must never be rendered as shipped (acceptance 4) -----------
    "BRIEF-PENDING-RENDERED-SHIPPED",
    "BRIEF-PENDING-NO-BLOCKER",
    # -- the committed artifact vs a fresh composition ----------------------
    "BRIEF-STALE",
)

#: Citation prefix for a registry row: ``registry:<module-id>`` resolves against
#: the registry document this brief was composed from. The **declared** form of
#: this prefix is the policy's ``resolution.registry_prefix`` (issue #592); this
#: constant is only its name, so a reader has one word for the concept.
REGISTRY_PREFIX = "registry:"

#: Citation placed on a claim whose referenced subject is *counted*, not named
#: (the registry document as a whole).
REGISTRY_DOCUMENT = "governance/modules"


@dataclass(frozen=True)
class Claim:
    """One statement in the brief, with the evidence it resolves to."""

    subject: str
    fact: str
    value: str
    citations: Tuple[str, ...]
    line: int

    def as_dict(self) -> Dict[str, Any]:
        return {
            "subject": self.subject,
            "fact": self.fact,
            "value": self.value,
            "citations": list(self.citations),
            "line": self.line,
        }


class ClaimBook:
    """Line-accurate Markdown builder: every emitted line can carry a claim.

    Line numbers are the ones the reader sees, which is what makes
    ``BRIEF-CLAIM-UNRESOLVED`` able to name the line instead of the prose.
    """

    def __init__(self) -> None:
        self._lines: List[str] = []
        self._claims: List[Claim] = []

    @property
    def claims(self) -> Tuple[Claim, ...]:
        return tuple(self._claims)

    @property
    def line_count(self) -> int:
        return len(self._lines)

    def add(self, text: str) -> int:
        """Append one line; returns its 1-based number."""
        self._lines.append(text)
        return len(self._lines)

    def blank(self) -> int:
        return self.add("")

    def claim(
        self,
        text: str,
        *,
        subject: str,
        fact: str,
        value: str,
        citations: Sequence[str],
    ) -> int:
        """Append one line and register the claim it carries."""
        line = self.add(text)
        self._claims.append(
            Claim(
                subject=subject,
                fact=fact,
                value=value,
                citations=tuple(citations),
                line=line,
            )
        )
        return line

    def row(
        self,
        cells: Sequence[str],
        *,
        subject: str,
        fact: str,
        value: str,
        citations: Sequence[str],
    ) -> int:
        """Append a table row (cells escaped) carrying a claim."""
        return self.claim(
            "| " + " | ".join(_cell(cell) for cell in cells) + " |",
            subject=subject,
            fact=fact,
            value=value,
            citations=citations,
        )

    def render(self) -> str:
        return "\n".join(self._lines) + "\n"


def _cell(value: Any) -> str:
    """A table cell: no newlines, no unescaped pipe, no trailing space."""
    return str(value).replace("|", "\\|").replace("\n", " ").strip()


# --------------------------------------------------------------------------- #
# citation resolution
# --------------------------------------------------------------------------- #
def resolves(
    citation: str,
    *,
    repo_root: Path,
    hub_root: Path,
    ids: Iterable[str],
    policy: ClaimPolicy,
) -> bool:
    """Whether a citation resolves: a registry row, or a path that exists.

    A citation is one of the honest things the declared policy names
    (``claim-policy.json``, read through
    :class:`~integrations.paperclip.reporting.policy.ClaimPolicy`):

    * the policy's registry prefix followed by an id — a row of the registry
      document this brief was composed from (membership included:
      ``not-a-module`` rows are rows);
    * a path that exists under one of the policy's declared bases — the
      repository root, and the pinned read-only hub root.
    """
    prefix = policy.registry_prefix
    if citation.startswith(prefix):
        return citation[len(prefix) :] in set(ids)
    for base in policy.bases:
        if (policy.resolves_path(base, Path(repo_root), Path(hub_root)) / citation).exists():
            return True
    return False


def claim_findings(
    claims: Sequence[Claim],
    *,
    repo_root: Path,
    hub_root: Path,
    ids: Iterable[str],
    policy: ClaimPolicy,
    artifact: Optional[str] = None,
) -> Tuple[Refusal, ...]:
    """Every claim that cites nothing — or cites something that is not there.

    The finding names the **line**, because "which statement is unsupported" is
    the only question a reader of a brief can act on. Both the code it is refused
    under and the artifact the line is named in come from the declared policy,
    never from a literal here (issue #592).
    """
    code = policy.unresolved_code
    where_in = artifact or policy.artifact
    known = set(ids)
    findings: List[Refusal] = []
    for claim in claims:
        where = "{}:{:d}".format(where_in, claim.line)
        if not claim.citations:
            findings.append(
                Refusal(
                    code,
                    claim.subject,
                    "line {:d} ({}.{} = {!r}) cites no registry row and no cited path — "
                    "a statement that resolves to nothing is a finding, not prose".format(
                        claim.line, claim.subject, claim.fact, claim.value
                    ),
                    where,
                )
            )
            continue
        unresolved = [
            citation
            for citation in claim.citations
            if not resolves(
                citation,
                repo_root=repo_root,
                hub_root=hub_root,
                ids=known,
                policy=policy,
            )
        ]
        if unresolved:
            findings.append(
                Refusal(
                    code,
                    claim.subject,
                    "line {:d} ({}.{}) cites {!r}, which is neither a registry row nor "
                    "a path that exists".format(
                        claim.line, claim.subject, claim.fact, unresolved[0]
                    ),
                    where,
                )
            )
    return tuple(findings)


def state_of(entry: Dict[str, Any]) -> str:
    """The entry's state, validated against the authority's three states."""
    state = str(entry.get("state") or "")
    if state not in STATES:
        raise CannotAssess(
            "registry entry {!r} carries state {!r}, which is not one of the three "
            "states {} — the brief will not invent a fourth".format(
                entry.get("id"), state, list(STATES)
            )
        )
    return state


__all__ = [
    "ARTIFACT",
    "BRIEF_CODES",
    "CATALOG_MODULE_NOT_MANDATORY",
    "Claim",
    "ClaimBook",
    "ClaimPolicy",
    "NOT_A_MODULE",
    "REGISTERED_MANDATORY",
    "REGISTRY_DOCUMENT",
    "REGISTRY_PREFIX",
    "SCHEMA",
    "STATES",
    "TARGET_PENDING",
    "CannotAssess",
    "Refusal",
    "claim_findings",
    "resolves",
    "sorted_refusals",
    "state_of",
]
