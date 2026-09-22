"""The persona's declaration, bound to the tools it actually needs (issue #447).

The persona card is the declaration; this module is the **contract** that keeps
it honest. It names, for the ``module-brief`` capability:

* the capability id the card must declare in ``capabilitySet``;
* the tools that capability actually needs (``file_read`` for the three
  read-only sources, ``file_write`` to freeze the artifact) — a capability that
  requires a tool the allowlist does not grant is refused **by name**;
* the memory scope the artifact's canonical home needs (``repository``: the
  brief is a file in this repository, not a private agent scratchpad);
* the sources it reads, each of which must resolve to a real path.

The tools are *read-only towards everything the brief reads*: it reads the
module registry, the committed board snapshot and the hub catalog, and writes
only its own artifact. It cannot reach the board (``gh_issue``), the
repository's history (``shell_exec``) or a remote (only ``web_fetch`` for
research, as before) — the declaration grants what the capability uses and
nothing it does not.

---knowledge---
module_id: integrations.paperclip.reporting.capability
system: integrations
app: paperclip
solution_class: enterprise
patterns: []
derives_from: null
owner_sme: paperclip
tier: L1
interfaces: [Source, load_card, check, as_dict]
invariants: ""
gotchas: ""
related: ["#1910"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Tuple

from integrations.paperclip import mapping
from integrations.paperclip.reporting import policy as claim_policy
from integrations.paperclip.reporting.model import (
    CannotAssess,
    Refusal,
    resolves,
    sorted_refusals,
)

#: The capability this lane declares (the vocabulary id, issue #447).
CAPABILITY_ID = "module-brief"

#: Tools the capability needs. ``file_read`` reaches all three sources
#: (the registry package, the board snapshot, the hub catalog);
#: ``file_write`` freezes the artifact.
REQUIRED_TOOLS: Tuple[str, ...] = ("file_read", "file_write")

#: The capability set the card must declare: the pre-existing knowledge
#: capabilities plus the reporting one.
DECLARED_CAPABILITIES: Tuple[str, ...] = (
    "research",
    "docs-authoring",
    "memory-ops",
    CAPABILITY_ID,
)

#: Memory scope the artifact's canonical home needs.
ARTIFACT_HOME = "repository"

#: The persona card and the profile seed the declaration lives in.
CARD = "registry/personas/cards/paperclip.yaml"
SEED = "registry/profiles/seeds/paperclip.1.1.0.yaml"


@dataclass(frozen=True)
class Source:
    """A read-only input of the brief, and what it is for."""

    name: str
    path: str
    role: str


#: What the brief reads. Every one of these is read-only to the agent; the
#: board is the *committed snapshot*, never a live write surface.
SOURCES: Tuple[Source, ...] = (
    Source(
        name="module registry",
        path="governance/modules",
        role="the ecosystem module registry (issue #445): state, pin/rev, consumer assets, health, board ref",
    ),
    Source(
        name="board snapshot",
        path=".board/snapshot.json",
        role="the committed board snapshot of this repository — cited as the board evidence surface, never asserted as a live state",
    ),
    Source(
        name="hub catalog",
        path="vendor/CMR/catalog",
        role="the hub's mandatory registry and per-module manifests (the authority the registry reads)",
    ),
)


def load_card(repo_root: Path) -> Dict[str, Any]:
    """Read the persona card. Unreadable is CANNOT-ASSESS, never a pass."""
    path = Path(repo_root) / CARD
    try:
        data = mapping.load_yaml_file(path)
    except OSError as exc:
        raise CannotAssess("persona card unreadable: {} ({})".format(path, exc))
    except ValueError as exc:
        raise CannotAssess("persona card unparseable: {} ({})".format(path, exc))
    if not isinstance(data, dict):
        raise CannotAssess("persona card is not a mapping: {}".format(path))
    return data


def _as_strings(value: Any) -> List[str]:
    return [str(item) for item in value] if isinstance(value, list) else []


def check(repo_root: Path, hub_root: Path) -> Tuple[Refusal, ...]:
    """The capability contract: declared, granted, homed and resolvable."""
    repo_root = Path(repo_root)
    hub_root = Path(hub_root)
    card = load_card(repo_root)
    persona = str(card.get("id") or "paperclip")
    findings: List[Refusal] = []
    # The source-resolution rule is the declared policy's, the same one the
    # composer's claims are held to (issue #592).
    policy = claim_policy.load()

    declared = _as_strings(card.get("capabilitySet"))
    if CAPABILITY_ID not in declared:
        findings.append(
            Refusal(
                "BRIEF-CAPABILITY-UNDECLARED",
                CAPABILITY_ID,
                "the {!r} card does not declare the capability in capabilitySet — an agent "
                "that cannot be dispatched to compose the brief has no reporting duty".format(
                    persona
                ),
                "{} capabilitySet".format(CARD),
            )
        )

    granted = _as_strings(card.get("toolAllowlist"))
    for tool in REQUIRED_TOOLS:
        if tool not in granted:
            findings.append(
                Refusal(
                    "BRIEF-CAPABILITY-TOOL-UNGRANTED",
                    tool,
                    "capability {!r} requires the tool {!r}, which the {!r} card's "
                    "toolAllowlist does not grant — declared and usable must not "
                    "diverge".format(CAPABILITY_ID, tool, persona),
                    "{} toolAllowlist".format(CARD),
                )
            )

    scopes = _as_strings(card.get("memoryScope"))
    if ARTIFACT_HOME not in scopes:
        findings.append(
            Refusal(
                "BRIEF-ARTIFACT-HOME-UNGRANTED",
                ARTIFACT_HOME,
                "the brief's canonical home is this repository ({}), so the card must "
                "hold the {!r} memory scope".format(
                    "docs/MODULE-BRIEF.md", ARTIFACT_HOME
                ),
                "{} memoryScope".format(CARD),
            )
        )

    for source in SOURCES:
        if not resolves(
            source.path, repo_root=repo_root, hub_root=hub_root, ids=(), policy=policy
        ):
            findings.append(
                Refusal(
                    "BRIEF-SOURCE-MISSING",
                    source.name,
                    "the brief claims to read {} at {!r}, which does not resolve".format(
                        source.role, source.path
                    ),
                    source.path,
                )
            )

    findings.extend(_seed_drift(repo_root, card, persona))
    return sorted_refusals(iter(findings))


def _seed_drift(repo_root: Path, card: Dict[str, Any], persona: str) -> List[Refusal]:
    """The profile seed mirrors the card — a one-sided edit is a finding."""
    path = Path(repo_root) / SEED
    try:
        seed = mapping.load_yaml_file(path)
    except OSError:
        return [
            Refusal(
                "BRIEF-SEED-DRIFT",
                persona,
                "the published profile seed {!r} is missing, so the declaration exists "
                "in the card only".format(SEED),
                SEED,
            )
        ]
    except ValueError as exc:
        return [
            Refusal(
                "BRIEF-SEED-DRIFT",
                persona,
                "the published profile seed {!r} is unparseable: {}".format(SEED, exc),
                SEED,
            )
        ]
    if not isinstance(seed, dict):
        return [
            Refusal(
                "BRIEF-SEED-DRIFT",
                persona,
                "the published profile seed {!r} is not a mapping".format(SEED),
                SEED,
            )
        ]
    findings: List[Refusal] = []
    for field in ("capabilitySet", "toolAllowlist", "memoryScope", "constraintSet"):
        if sorted(_as_strings(seed.get(field))) != sorted(_as_strings(card.get(field))):
            findings.append(
                Refusal(
                    "BRIEF-SEED-DRIFT",
                    persona,
                    "{} disagrees between the card and the seed: card {!r} vs seed {!r}".format(
                        field, _as_strings(card.get(field)), _as_strings(seed.get(field))
                    ),
                    SEED,
                )
            )
    return findings


def as_dict() -> Dict[str, Any]:
    """The contract, for the artifact and the CLI to state without restating."""
    return {
        "capability": CAPABILITY_ID,
        "capabilities": list(DECLARED_CAPABILITIES),
        "required_tools": list(REQUIRED_TOOLS),
        "artifact_home": ARTIFACT_HOME,
        "card": CARD,
        "seed": SEED,
        "sources": [
            {"name": source.name, "path": source.path, "role": source.role}
            for source in SOURCES
        ],
    }


__all__ = [
    "ARTIFACT_HOME",
    "CAPABILITY_ID",
    "CARD",
    "DECLARED_CAPABILITIES",
    "REQUIRED_TOOLS",
    "SEED",
    "SOURCES",
    "Source",
    "as_dict",
    "check",
    "load_card",
]
