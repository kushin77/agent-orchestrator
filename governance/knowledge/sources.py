"""The canonical knowledge source catalogue (issue #139).

Declarative, not hand-maintained: each entry names a kind, a glob, an owner and
whether the source is required. The indexer consumes this; nothing else lists
knowledge sources, so there is exactly one place to add a new one — which is what
"works from canonical standards and does not rely on duplicated knowledge stores"
asks for.

Required sources must resolve to at least one file, always. Optional sources may
be absent — the CMR hub assets live in the `vendor/CMR` submodule, which a clean
clone (or a CI checkout without submodules) simply does not have — and an absent
optional source becomes a *reported gap* rather than a failure.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

from model import (
    KIND_ADR,
    KIND_ARCHITECTURE,
    KIND_GOLDEN_RULES,
    KIND_GOVERNANCE,
    KIND_ISSUE_METADATA,
    KIND_LESSONS,
    KIND_PATTERN_TEMPLATE,
    KIND_POLICY,
    KIND_RCA,
)

RETRIEVAL_WORKING_TREE = "working-tree"
RETRIEVAL_SUBMODULE = "submodule"


@dataclass(frozen=True)
class SourceSpec:
    """One declarative source: a kind, a glob, an owner and its necessity."""

    kind: str
    pattern: str
    owner: str
    required: bool = True
    retrieval: str = RETRIEVAL_WORKING_TREE
    upstream: str = ""
    note: str = ""


# Owner labels are declarations of accountability, not guesses: they name the
# lane that owns the material in this repo.
_OWNER_GOVERNANCE = "platform-governance"
_OWNER_PMO = "governance-pmo"
_OWNER_ARCH = "architecture"
_OWNER_SECURITY = "security"
_OWNER_PLATFORM = "platform"

SOURCE_SPECS: Tuple[SourceSpec, ...] = (
    # -- golden rules -------------------------------------------------------
    SourceSpec(KIND_GOLDEN_RULES, "GOLDEN-RULES.md", _OWNER_GOVERNANCE),
    SourceSpec(KIND_GOLDEN_RULES, "docs/GOLDEN-RULES.md", _OWNER_GOVERNANCE),
    # -- governance ---------------------------------------------------------
    SourceSpec(KIND_GOVERNANCE, "AGENTS.md", _OWNER_GOVERNANCE),
    SourceSpec(KIND_GOVERNANCE, "CLAUDE.md", _OWNER_GOVERNANCE),
    SourceSpec(KIND_GOVERNANCE, "CONTRIBUTING.md", _OWNER_GOVERNANCE),
    SourceSpec(KIND_GOVERNANCE, "RELEASING.md", _OWNER_GOVERNANCE),
    SourceSpec(KIND_GOVERNANCE, "SECURITY.md", _OWNER_SECURITY),
    SourceSpec(KIND_GOVERNANCE, "VALIDATION.md", _OWNER_GOVERNANCE),
    SourceSpec(KIND_GOVERNANCE, "CHANGELOG.md", _OWNER_GOVERNANCE),
    SourceSpec(KIND_GOVERNANCE, "MIGRATION_NOTES.md", _OWNER_GOVERNANCE),
    SourceSpec(KIND_GOVERNANCE, "docs/GOVERNANCE.md", _OWNER_PMO),
    SourceSpec(KIND_GOVERNANCE, "docs/README.md", _OWNER_PMO),
    # -- architecture -------------------------------------------------------
    SourceSpec(KIND_ARCHITECTURE, "docs/ARCHITECTURE.md", _OWNER_ARCH),
    SourceSpec(KIND_ARCHITECTURE, "docs/QA-GATE.md", _OWNER_ARCH),
    SourceSpec(KIND_ARCHITECTURE, "docs/CANNIBALIZATION.md", _OWNER_ARCH),
    SourceSpec(KIND_ARCHITECTURE, "docs/spikes/*.md", _OWNER_ARCH),
    # -- ADRs ---------------------------------------------------------------
    SourceSpec(KIND_ADR, "docs/decision-records/ADR-*.md", _OWNER_ARCH),
    SourceSpec(KIND_ADR, "docs/decision-records/README.md", _OWNER_ARCH),
    # -- policy -------------------------------------------------------------
    SourceSpec(KIND_POLICY, "guardrails/policy/controls.yaml", _OWNER_SECURITY),
    SourceSpec(KIND_POLICY, "guardrails/policy/bundles/*/*.yaml", _OWNER_SECURITY),
    SourceSpec(KIND_POLICY, "guardrails/policy/schema/*.json", _OWNER_SECURITY),
    # -- pattern / template definitions -------------------------------------
    SourceSpec(KIND_PATTERN_TEMPLATE, "docs/decision-records/template.md", _OWNER_ARCH),
    SourceSpec(KIND_PATTERN_TEMPLATE, "registry/**/*.schema.json", _OWNER_PLATFORM),
    SourceSpec(KIND_PATTERN_TEMPLATE, "guardrails/sandbox/*.schema.json", _OWNER_PLATFORM),
    SourceSpec(KIND_PATTERN_TEMPLATE, "control-plane/**/*.schema.json", _OWNER_PLATFORM),
    SourceSpec(KIND_PATTERN_TEMPLATE, "gateway/**/*.schema.json", _OWNER_PLATFORM),
    SourceSpec(KIND_PATTERN_TEMPLATE, "telemetry/**/*.schema.json", _OWNER_PLATFORM),
    SourceSpec(
        KIND_PATTERN_TEMPLATE,
        "control-plane/sdk/template/**/*.md",
        _OWNER_PLATFORM,
        note="consumer-repo scaffold instructions",
    ),
    # -- issue metadata (from the committed board snapshot) -----------------
    SourceSpec(
        KIND_ISSUE_METADATA,
        ".board/snapshot.json",
        _OWNER_PMO,
        note="one item per issue, derived from the committed board snapshot",
    ),
    # -- lessons (CMR hub; absent unless the submodule is checked out) ------
    SourceSpec(
        KIND_LESSONS,
        "vendor/CMR/docs/LESSONS.md",
        _OWNER_PMO,
        required=False,
        retrieval=RETRIEVAL_SUBMODULE,
        upstream="kushin77/CMR",
    ),
    SourceSpec(
        KIND_LESSONS,
        "vendor/CMR/docs/LESSONS.tsv",
        _OWNER_PMO,
        required=False,
        retrieval=RETRIEVAL_SUBMODULE,
        upstream="kushin77/CMR",
    ),
    # -- RCA records (CMR hub; absent unless the submodule is checked out) --
    SourceSpec(
        KIND_RCA,
        "vendor/CMR/docs/RCA-TEMPLATE.md",
        _OWNER_PMO,
        required=False,
        retrieval=RETRIEVAL_SUBMODULE,
        upstream="kushin77/CMR",
    ),
    SourceSpec(
        KIND_RCA,
        "vendor/CMR/docs/rca/*.md",
        _OWNER_PMO,
        required=False,
        retrieval=RETRIEVAL_SUBMODULE,
        upstream="kushin77/CMR",
    ),
    SourceSpec(
        KIND_RCA,
        "vendor/CMR/docs/RETROSPECTIVE.md",
        _OWNER_PMO,
        required=False,
        retrieval=RETRIEVAL_SUBMODULE,
        upstream="kushin77/CMR",
    ),
)


def specs_for_kind(kind: str) -> Tuple[SourceSpec, ...]:
    return tuple(spec for spec in SOURCE_SPECS if spec.kind == kind)


def required_specs() -> Tuple[SourceSpec, ...]:
    return tuple(spec for spec in SOURCE_SPECS if spec.required)
