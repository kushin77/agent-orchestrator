"""Compose each mandatory module's distributable brief (issue #447).

The brief is composed **only** from the registry document (issue #445) and the
hub files that document cites. Nothing here re-reads the hub catalog, re-derives
mandatory status, or keeps a second copy of the three states: the vocabulary and
the refusal type come from :mod:`governance.modules.model`, and the entry fields
come from the registry document as handed in.

Three properties the issue pins, and how they are held:

* every rendered statement is a :class:`~integrations.paperclip.reporting.model.Claim`
  carrying the registry row or cited path it resolves to — :func:`compose`
  returns the claims so a caller can require them to resolve, and
  ``BRIEF-CLAIM-UNRESOLVED`` names the line when one does not (acceptance 3);
* **pending is never rendered as shipped**: a ``target-pending`` entry whose
  ``shipped`` is anything but ``False`` is refused by name, and a pending entry
  without a blocking hub issue is refused too (acceptance 4);
* the composition is **deterministic** — no time, no ordering incidentals, every
  list sorted — so two runs over one revision are byte-identical (acceptance 5).

Refusals do not make the brief lie: the text is still produced (honestly
reporting what it found), but it is not *frozen* — the CLI refuses to write the
artifact while a finding stands.

Three artifacts landed with issue #592 (parent #590) are read on every
composition, never restated:

* ``claim-policy.json`` — the **declared claim-resolution policy**: which
  prefix names a registry row, which bases a citation path may resolve against,
  which code a non-resolving line is refused under, and that a target-set module
  with no vendor ``module.json`` renders ``target-pending`` while *pending is
  never rendered as shipped*;
* ``brief.schema.json`` — the **frozen schema** of the machine document this
  module emits; :func:`compose` validates what it emits against it on every run
  and refuses ``BRIEF-SCHEMA-INVALID`` naming the JSON path otherwise;
* ``audit.py`` — the **append-only trail** every composed run appends one
  record to (the resolved / unresolved counts and the finding lines).

One rule, one home: none of the three rules above is written out below, and the
gate proves each artifact is really read by doctoring it and requiring the
refusal to change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from governance.modules.model import (
    CATALOG_MODULE_NOT_MANDATORY,
    NOT_A_MODULE,
    REGISTERED_MANDATORY,
    STATES,
    TARGET_PENDING,
    CannotAssess,
    Refusal,
    sorted_refusals,
)

from integrations.paperclip.reporting import audit, brief_schema, capability, policy as claim_policy
from integrations.paperclip.reporting.model import (
    ARTIFACT,
    REGISTRY_DOCUMENT,
    REGISTRY_PREFIX,
    SCHEMA,
    Claim,
    ClaimBook,
    claim_findings,
    state_of,
)

#: Default hub root, relative to the repository root (the pinned submodule).
DEFAULT_HUB = "vendor/CMR"

#: Where the registry document's own declared inputs live (cited, never re-read).
TARGETS = "governance/modules/targets.json"
MANDATORY_TSV = "catalog/mandatory.tsv"
REGISTRY_PACKAGE = "governance/modules"
HEALTH_MODULE = "governance/modules/health.py"
HUB_MODULE = "governance/modules/hub.py"
MODEL_MODULE = "governance/modules/model.py"
GATE = "scripts/check-module-brief.sh"

#: What the rev row says when the hub is not a git checkout (an extracted tree):
#: the registry reports `revision_source: unavailable`, and the brief states the
#: limitation instead of inventing a revision.
REV_UNAVAILABLE = "unavailable at this revision (the hub is not a git checkout)"


@dataclass(frozen=True)
class Composition:
    """The rendered brief, the claims it makes, and every refusal it found.

    ``document`` is the same composition as a **machine document** — the shape
    ``brief.schema.json`` freezes, validated against it before this value is
    returned. The rendered text and the document come from one pass, so they
    cannot describe different compositions.
    """

    text: str
    claims: Tuple[Claim, ...]
    findings: Tuple[Refusal, ...]
    ids: Tuple[str, ...]
    document: Dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def _row(book: ClaimBook, cells: Sequence[str]) -> None:
    book.add("| " + " | ".join(str(cell) for cell in cells) + " |")


def _table(book: ClaimBook, header: Sequence[str]) -> None:
    _row(book, header)
    _row(book, ["---"] * len(header))


def _registry_row(module_id: str, prefix: str = REGISTRY_PREFIX) -> str:
    """A citation of the registry row for a module id.

    The prefix is the policy's, not a literal: a citation form the policy does
    not resolve would make every claim in the brief a finding.
    """
    return prefix + module_id


def _reference_citation(reference: Dict[str, Any], hub_root_rel: str) -> str:
    """The path a registry entry's reference points at, in citation form.

    A registry entry cites two different kinds of place: a hub catalog entry
    (hub-relative, ``catalog/modules/<id>/module.json``) and a declared target
    (repository-relative, ``governance/modules/targets.json``). Prefixing the
    wrong one produces a citation that resolves nowhere — which is what
    ``BRIEF-CLAIM-UNRESOLVED`` exists to catch, so the form is decided by the
    reference's own ``kind`` and never guessed.
    """
    path = str(reference.get("path") or "")
    if not path:
        return ""
    if reference.get("kind") == "declared-target":
        return path
    return "{}/{}".format(hub_root_rel, path)


def _asset_rows(book: ClaimBook, entry: Dict[str, Any], prefix: str) -> None:
    """One row per consumer asset, naming the seed it comes from."""
    module_id = str(entry["id"])
    assets = entry.get("assets") or []
    if not assets:
        book.row(
            ["(none)", "—", "—", _registry_row(module_id, prefix)],
            subject=module_id,
            fact="asset_inventory",
            value="none",
            citations=[_registry_row(module_id, prefix)],
        )
        return
    for asset in assets:
        seed = asset.get("seed")
        present = bool(asset.get("seed_present"))
        if present and seed:
            citations = [str(seed), _registry_row(module_id, prefix)]
            value = "`{}`".format(seed)
        else:
            # The seed is missing: the only thing this line can honestly cite is
            # the registry row that reports it missing.
            citations = [_registry_row(module_id, prefix)]
            value = "NO SEED"
        book.row(
            [
                str(asset.get("asset")),
                value,
                "yes" if present else "NO",
                ", ".join(citations),
            ],
            subject=module_id,
            fact="asset_seed:{}".format(asset.get("asset")),
            value=value,
            citations=citations,
        )


def _drift_findings(entry: Dict[str, Any], findings: Sequence[Refusal]) -> List[Refusal]:
    """The findings that belong to this module — one rule, used twice."""
    return [finding for finding in findings if finding.subject == str(entry["id"])]


def _drift(entry: Dict[str, Any], findings: Sequence[Refusal]) -> str:
    mine = _drift_findings(entry, findings)
    if not mine:
        return "none"
    return "; ".join("{}: {}".format(f.code, f.detail) for f in mine)


def _module_findings(
    entry: Dict[str, Any], policy: claim_policy.ClaimPolicy, revision_known: bool = True
) -> List[Refusal]:
    """What a module entry must not be, checked before it is rendered.

    The pending rule is the policy's: which state counts as pending, that it may
    only be rendered with ``shipped: false``, that it must name its blocker, and
    which refusals say so. A rule restated here would be a second policy.
    """
    module_id = str(entry["id"])
    state = state_of(entry)
    findings: List[Refusal] = []
    if state == policy.pending_state:
        if entry.get("shipped") is not policy.pending_shipped_must_be:
            findings.append(
                Refusal(
                    policy.pending_shipped_code,
                    module_id,
                    "the registry says {}, but this entry reports shipped={!r} — "
                    "pending is never rendered as shipped".format(
                        policy.pending_renders, entry.get("shipped")
                    ),
                    "{} (state={}, shipped={!r})".format(
                        entry.get("reference", {}).get("path") or REGISTRY_DOCUMENT,
                        state,
                        entry.get("shipped"),
                    ),
                )
            )
        if policy.pending_blocker_required and not entry.get("blocking"):
            findings.append(
                Refusal(
                    policy.pending_blocker_code,
                    module_id,
                    "a pending module must name the blocking hub issue(s) that keep it "
                    "pending — an unnamed blocker is indistinguishable from a wish",
                    TARGETS,
                )
            )
        return findings

    if state != REGISTERED_MANDATORY:
        # A catalog module outside the mandatory set is briefed for context —
        # this repo does not have to carry it, so a missing pin is the hub's
        # fact to report (as drift), not a defect in the brief.
        return findings

    if not entry.get("pin"):
        findings.append(
            Refusal(
                "BRIEF-MODULE-NO-PIN",
                module_id,
                "a {!r} module with no pin cannot be briefed: every repo would carry a "
                "different revision of it".format(state),
                entry.get("reference", {}).get("path") or REGISTRY_DOCUMENT,
            )
        )
    if not entry.get("rev") and revision_known:
        findings.append(
            Refusal(
                "BRIEF-MODULE-NO-REV",
                module_id,
                "a {!r} module with no revision cannot be briefed: the brief would not "
                "name the revision it describes".format(state),
                entry.get("reference", {}).get("path") or REGISTRY_DOCUMENT,
            )
        )
    for asset in entry.get("consumer_assets") or []:
        if not asset:
            continue
        rows = {row.get("asset"): row for row in entry.get("assets") or []}
        row = rows.get(asset) or {}
        if not row.get("seed_present"):
            findings.append(
                Refusal(
                    "BRIEF-ASSET-NO-SEED",
                    module_id,
                    "consumer asset {!r} resolves to no seed under templates/module/ or "
                    "guardrails/ — the repo is told to carry an asset nobody ships".format(
                        asset
                    ),
                    entry.get("reference", {}).get("path") or REGISTRY_DOCUMENT,
                )
            )
    return findings


# --------------------------------------------------------------------------- #
# composition
# --------------------------------------------------------------------------- #
def compose(
    document: Dict[str, Any],
    repo_root: Path,
    hub_root_rel: str = DEFAULT_HUB,
    *,
    policy: Optional[claim_policy.ClaimPolicy] = None,
    audit_trail: Optional[audit.Trail] = None,
) -> Composition:
    """Compose the brief from a registry document. Deterministic over one revision.

    ``policy`` is the declared claim-resolution policy; it is read from this
    package's ``claim-policy.json`` unless a caller hands in its own (which is how
    the suite proves the composer reads it rather than restating it).

    ``audit_trail`` is where this run's single audit record is appended **after**
    the findings are known — the trail records a run, it never influences one.
    """
    policy = policy or claim_policy.load()
    prefix = policy.registry_prefix
    schema = brief_schema.load()
    repo_root = Path(repo_root)
    hub_root = repo_root / hub_root_rel

    modules = list(document.get("modules") or [])
    refused = list(document.get("not_modules") or [])
    registry_ids = sorted({str(entry["id"]) for entry in modules + refused})
    refusals = tuple(
        Refusal(**finding) for finding in document.get("refusals") or []
    )

    findings: List[Refusal] = []
    if list(document.get("states") or []) != list(STATES):
        findings.append(
            Refusal(
                "BRIEF-STATE-VOCABULARY-DRIFT",
                "states",
                "the registry document declares states {!r}; the authority's three are "
                "{!r}".format(document.get("states"), list(STATES)),
                REGISTRY_PACKAGE,
            )
        )

    recomputed = {state: 0 for state in STATES}
    hub_revision_known = bool((document.get("hub") or {}).get("revision"))
    for entry in modules:
        recomputed[state_of(entry)] = recomputed.get(state_of(entry), 0) + 1
    declared_counts = document.get("summary") or {}
    for state in STATES:
        if declared_counts.get(state) != recomputed.get(state, 0):
            findings.append(
                Refusal(
                    "BRIEF-SUMMARY-DRIFT",
                    state,
                    "the registry document counts {!r} of this state; its entries carry "
                    "{!r}".format(declared_counts.get(state), recomputed.get(state, 0)),
                    REGISTRY_PACKAGE,
                )
            )

    for entry in sorted(modules, key=lambda item: str(item["id"])):
        findings.extend(
            _module_findings(
                entry, policy, revision_known=bool(hub_revision_known)
            )
        )
    module_findings = sorted_refusals(iter(findings))

    book = ClaimBook()
    hub_revision = (document.get("hub") or {}).get("revision") or "unavailable"
    hub_revision_source = (document.get("hub") or {}).get("revision_source") or "unavailable"

    # -- title + provenance -------------------------------------------------
    book.add("# Module brief — what every repo must carry, at which pin, and whether it is current")
    book.blank()
    book.claim(
        "Composed by `integrations/paperclip/reporting/` (issue #447 — the paperclip reporting "
        "half, ADR-0012) from the module registry (issue #445) and the hub files that registry "
        "cites. Nothing in this document is re-derived: every line below cites the registry row "
        "or the cited path it resolves to.",
        subject="brief",
        fact="provenance",
        value=SCHEMA,
        citations=[REGISTRY_DOCUMENT, "{}/{}".format(hub_root_rel, MANDATORY_TSV)],
    )
    book.claim(
        "Composition is deterministic — two runs over one revision are byte-identical — and "
        "`{}` regenerates this document and refuses a stale one by name.".format(GATE),
        subject="brief",
        fact="determinism",
        value="byte-identical over one revision",
        citations=[GATE],
    )
    book.blank()

    # -- sources ------------------------------------------------------------
    book.add("## 1. Sources — every claim below resolves to a registry row or one of these")
    book.blank()
    _table(book, ["Source", "Path", "What it is", "Cited as"])
    for source in capability.SOURCES:
        book.row(
            [source.name, "`{}`".format(source.path), source.role, "`{}`".format(source.path)],
            subject=source.name,
            fact="source",
            value=source.path,
            citations=[source.path],
        )
    book.row(
        [
            "hub revision",
            "`{}`".format(hub_root_rel),
            "the pinned, read-only hub checkout the registry was built from",
            "`{}`".format(hub_root_rel),
        ],
        subject="hub",
        fact="revision",
        value=str(hub_revision),
        citations=[hub_root_rel],
    )
    book.claim(
        "The hub revision is {} ({}); the board snapshot carries this repository's own board "
        "only, so a hub issue named below is **cited, never asserted** open or closed.".format(
            hub_revision, hub_revision_source
        ),
        subject="hub",
        fact="revision_source",
        value=str(hub_revision_source),
        citations=[hub_root_rel, ".board/snapshot.json"],
    )
    book.blank()

    # -- vocabulary ---------------------------------------------------------
    book.add("## 2. Three states, never two — plus a refusal that is not a state")
    book.blank()
    _table(book, ["State", "Count", "Meaning", "Source"])
    meanings = {
        REGISTERED_MANDATORY: "in the hub catalog with the mandatory flag and a mandatory-registry row in lockstep",
        TARGET_PENDING: "declared as a target, not yet in the hub catalog: `shipped: false`, blocking hub issue named",
        CATALOG_MODULE_NOT_MANDATORY: "in the hub catalog, outside the mandatory set",
    }
    for state in STATES:
        book.row(
            [
                "`{}`".format(state),
                str(recomputed.get(state, 0)),
                meanings[state],
                "`{}`".format(MODEL_MODULE),
            ],
            subject="states",
            fact="count:{}".format(state),
            value=str(recomputed.get(state, 0)),
            citations=[MODEL_MODULE, REGISTRY_DOCUMENT],
        )
    book.row(
        [
            "`{}` (refused, not a state)".format(NOT_A_MODULE),
            str(len(refused)),
            "a name the hub catalog does not carry: membership is refused, never inferred",
            "`{}`".format(MODEL_MODULE),
        ],
        subject="states",
        fact="count:refused",
        value=str(len(refused)),
        citations=[MODEL_MODULE, REGISTRY_DOCUMENT],
    )
    book.blank()

    # -- module sections ----------------------------------------------------
    book.add("## 3. The module set")
    book.blank()
    book.claim(
        "{} names resolve: {} registered mandatory, {} target-pending, {} catalog modules outside "
        "the mandatory set, and {} refused.".format(
            len(registry_ids),
            recomputed.get(REGISTERED_MANDATORY, 0),
            recomputed.get(TARGET_PENDING, 0),
            recomputed.get(CATALOG_MODULE_NOT_MANDATORY, 0),
            len(refused),
        ),
        subject="inventory",
        fact="total",
        value=str(len(registry_ids)),
        citations=[REGISTRY_DOCUMENT],
    )
    book.blank()

    for state, title in (
        (REGISTERED_MANDATORY, "### 3.1 Registered mandatory — shipped to every repo"),
        (TARGET_PENDING, "### 3.2 Declared targets — pending, never rendered as shipped"),
        (CATALOG_MODULE_NOT_MANDATORY, "### 3.3 Catalog modules outside the mandatory set"),
    ):
        subset = [entry for entry in modules if entry.get("state") == state]
        book.add(title)
        book.blank()
        if not subset:
            book.claim(
                "none.",
                subject=state,
                fact="membership",
                value="none",
                citations=[REGISTRY_DOCUMENT],
            )
            book.blank()
            continue
        for entry in sorted(subset, key=lambda item: str(item["id"])):
            _module(
                book,
                entry,
                module_findings,
                hub_root_rel,
                refusals,
                prefix,
                revision_known=hub_revision_known,
            )
    book.blank()

    # -- refused names ------------------------------------------------------
    book.add("## 4. Membership refused — not a state, and not a module")
    book.blank()
    if not refused:
        book.claim(
            "none.",
            subject=NOT_A_MODULE,
            fact="membership",
            value="none",
            citations=[REGISTRY_DOCUMENT],
        )
    else:
        _table(book, ["Name", "Recorded as", "Why", "Source"])
        for entry in sorted(refused, key=lambda item: str(item["id"])):
            module_id = str(entry["id"])
            book.row(
                [
                    module_id,
                    str(entry.get("reference", {}).get("kind") or ""),
                    str(entry.get("detail") or entry.get("note") or ""),
                    "`{}`".format(
                        _reference_citation(entry.get("reference") or {}, hub_root_rel)
                        or REGISTRY_DOCUMENT
                    ),
                ],
                subject=module_id,
                fact="membership",
                value=NOT_A_MODULE,
                citations=[_registry_row(module_id, prefix)],
            )
    book.blank()

    # -- distribution -------------------------------------------------------
    book.add("## 5. Distribution — the existing channel, unchanged")
    book.blank()
    book.claim(
        "The brief, not a copy, travels with the assets: distribution stays with the hub's "
        "`controller/standards-sync.sh` and `controller/standards-manifest.txt`, whose seeds live "
        "under `templates/module/`. This lane produces the organized statement; it adds no second "
        "push mechanism and vendors nothing (GR-10 / ADR-0013 / NG4).",
        subject="distribution",
        fact="channel",
        value="controller/standards-sync.sh",
        citations=[
            "{}/controller/standards-sync.sh".format(hub_root_rel),
            "{}/controller/standards-manifest.txt".format(hub_root_rel),
            "{}/templates/module".format(hub_root_rel),
        ],
    )
    book.blank()

    # -- findings -----------------------------------------------------------
    book.add("## 6. Findings")
    book.blank()
    if module_findings:
        for finding in module_findings:
            line = book.add("- `{}`".format(finding.render()))
            del line
    else:
        book.claim(
            "none — every claim above resolves to a registry row or a cited hub path.",
            subject="findings",
            fact="count",
            value="0",
            citations=[REGISTRY_DOCUMENT, GATE],
        )
    book.blank()

    claims = book.claims
    findings = list(module_findings) + list(
        claim_findings(
            claims,
            repo_root=repo_root,
            hub_root=hub_root,
            ids=registry_ids,
            policy=policy,
        )
    )

    # The machine document, and the composer's duty to validate what it emits:
    # a document that stops being the frozen shape is a finding, not a silence.
    document_of_run = _document(
        registry=document,
        modules=modules,
        refused=refused,
        recomputed=recomputed,
        claims=claims,
        findings=sorted_refusals(iter(findings)),
        hub_revision=str(hub_revision),
        hub_revision_source=str(hub_revision_source),
        prefix=prefix,
    )
    findings.extend(brief_schema.validate(document_of_run, schema))

    composition = Composition(
        text=book.render(),
        claims=claims,
        findings=sorted_refusals(iter(findings)),
        ids=tuple(registry_ids),
        document=document_of_run,
    )
    if audit_trail is not None:
        # One record per composed brief run — appended here, never anywhere else,
        # so the trail cannot record something that did not happen.
        audit_trail.record(composition, policy)
    return composition


def _document(
    *,
    registry: Dict[str, Any],
    modules: Sequence[Dict[str, Any]],
    refused: Sequence[Dict[str, Any]],
    recomputed: Mapping[str, int],
    claims: Sequence[Claim],
    findings: Sequence[Refusal],
    hub_revision: str,
    hub_revision_source: str,
    prefix: str,
) -> Dict[str, Any]:
    """The composition as the machine document ``brief.schema.json`` freezes.

    The same data the rendered text is built from, so the two cannot describe
    different compositions. Absent values are the empty string with an explicit
    presence flag rather than ``null``: the frozen schema is restricted to the
    validation subset the repository's own validator implements, which has no
    nullable type, and an explicit flag is the honest form anyway — "the registry
    reports no pin at this revision" is a fact, not a missing value.
    """
    records: List[Dict[str, Any]] = []
    for entry in sorted(modules, key=lambda item: str(item["id"])):
        module_id = str(entry["id"])
        health = entry.get("health") or {}
        mandatory = entry.get("mandatory")
        records.append(
            {
                "id": module_id,
                "state": state_of(entry),
                "owning_repo": str(entry.get("owning_repo") or ""),
                "mandatory": mandatory if isinstance(mandatory, bool) else None,
                "shipped": entry.get("shipped") is True,
                "pin": str(entry.get("pin") or ""),
                "pin_present": bool(entry.get("pin")),
                "rev": str(entry.get("rev") or ""),
                "rev_present": bool(entry.get("rev")),
                "consumer_assets": [str(a) for a in entry.get("consumer_assets") or []],
                "assets": [
                    {
                        "asset": str(asset.get("asset") or ""),
                        "seed": str(asset.get("seed") or ""),
                        "seed_present": bool(asset.get("seed_present")),
                    }
                    for asset in entry.get("assets") or []
                ],
                "health": {
                    "kind": str(health.get("kind") or ""),
                    "status": str(health.get("status") or "unknown"),
                    "reason": str(health.get("reason") or ""),
                },
                "board_ref": str(entry.get("board_ref") or ""),
                "blocking": [str(item) for item in entry.get("blocking") or []],
                "drift": [
                    finding.as_dict() for finding in _drift_findings(entry, findings)
                ],
            }
        )
    return {
        "schema": SCHEMA,
        "hub": {
            "revision": hub_revision,
            "revision_source": hub_revision_source,
        },
        "states": list(STATES),
        "summary": {state: int(recomputed.get(state, 0)) for state in STATES},
        "modules": records,
        "refused": [
            {
                "id": str(entry["id"]),
                "kind": str(entry.get("reference", {}).get("kind") or ""),
                "detail": str(entry.get("detail") or entry.get("note") or ""),
            }
            for entry in sorted(refused, key=lambda item: str(item["id"]))
        ],
        "claims": [claim.as_dict() for claim in claims],
        "findings": [finding.as_dict() for finding in findings],
    }


def _module(
    book: ClaimBook,
    entry: Dict[str, Any],
    findings: Sequence[Refusal],
    hub_root_rel: str,
    refusals: Sequence[Refusal],
    prefix: str,
    revision_known: bool = True,
) -> None:
    """One module: the fields acceptance 2 freezes, each with its citation."""
    module_id = str(entry["id"])
    state = str(entry["state"])
    reference = entry.get("reference") or {}
    reference_path = _reference_citation(reference, hub_root_rel)
    row_citations = [_registry_row(module_id, prefix)]
    if reference_path:
        row_citations.append(reference_path)

    book.claim(
        "#### `{}` — {}".format(module_id, state),
        subject=module_id,
        fact="state",
        value=state,
        citations=[_registry_row(module_id, prefix)],
    )
    book.blank()

    shipped = entry.get("shipped")
    mandatory = entry.get("mandatory")
    if state == TARGET_PENDING:
        status = "`target-pending` — declared target, **not shipped** (`shipped: false`)"
    elif state == REGISTERED_MANDATORY:
        status = "`registered-mandatory` — in the hub catalog with the mandatory flag (shipped)"
    else:
        status = "`catalog-module-not-mandatory` — in the hub catalog, outside the mandatory set (shipped)"

    _table(book, ["Field", "Value", "Source"])
    book.row(
        ["owning repo", str(entry.get("owning_repo") or "—"), ", ".join("`{}`".format(c) for c in row_citations)],
        subject=module_id,
        fact="owning_repo",
        value=str(entry.get("owning_repo") or "—"),
        citations=row_citations,
    )
    book.row(
        [
            "mandatory status",
            "{} (`mandatory: {}`, `shipped: {}`)".format(status, mandatory, shipped),
            ", ".join("`{}`".format(c) for c in row_citations),
        ],
        subject=module_id,
        fact="mandatory_status",
        value=state,
        citations=row_citations,
    )
    pin = entry.get("pin")
    rev = entry.get("rev")
    rev_citations = list(row_citations)
    if rev:
        rev_value = str(rev)
    else:
        # The hub is not a git checkout at this tree: the registry records
        # `revision_source: unavailable`, and the brief states the limitation
        # rather than inventing a revision or silently dropping the field.
        rev_value = REV_UNAVAILABLE
        rev_citations.append(HUB_MODULE)
    book.row(
        ["pin", str(pin) if pin else "—", ", ".join("`{}`".format(c) for c in row_citations)],
        subject=module_id,
        fact="pin",
        value=str(pin) if pin else "—",
        citations=row_citations,
    )
    book.row(
        ["rev", str(rev) if rev else REV_UNAVAILABLE, ", ".join("`{}`".format(c) for c in rev_citations)],
        subject=module_id,
        fact="rev",
        value=rev_value,
        citations=rev_citations,
    )
    book.row(
        [
            "consumer assets",
            ", ".join("`{}`".format(a) for a in entry.get("consumer_assets") or []) or "—",
            ", ".join("`{}`".format(c) for c in row_citations),
        ],
        subject=module_id,
        fact="consumer_assets",
        value=",".join(str(a) for a in entry.get("consumer_assets") or []),
        citations=row_citations,
    )
    health = entry.get("health") or {}
    book.row(
        [
            "health",
            "{} — {}".format(health.get("status") or "unknown", health.get("reason") or ""),
            "`{}`".format(HEALTH_MODULE),
        ],
        subject=module_id,
        fact="health",
        value=str(health.get("status") or "unknown"),
        citations=[HEALTH_MODULE, _registry_row(module_id, prefix)],
    )
    board_ref = entry.get("board_ref")
    book.row(
        [
            "board ref",
            "{} (cited, not resolved here)".format(board_ref or "—"),
            ", ".join("`{}`".format(c) for c in row_citations),
        ],
        subject=module_id,
        fact="board_ref",
        value=str(board_ref or "—"),
        citations=row_citations,
    )
    if state == TARGET_PENDING:
        blocking = entry.get("blocking") or []
        book.row(
            [
                "blocking hub issue(s)",
                ", ".join("`{}`".format(item) for item in blocking) or "—",
                "`{}`".format(TARGETS),
            ],
            subject=module_id,
            fact="blocking",
            value=",".join(str(item) for item in blocking),
            citations=[TARGETS, _registry_row(module_id, prefix)],
        )
        if entry.get("onboarding"):
            book.row(
                [
                    "onboarding",
                    str(entry.get("onboarding")),
                    "`{}`".format(TARGETS),
                ],
                subject=module_id,
                fact="onboarding",
                value=str(entry.get("onboarding")),
                citations=[TARGETS, _registry_row(module_id, prefix)],
            )
    book.row(
        [
            "drift",
            _drift(entry, refusals + findings),
            ", ".join("`{}`".format(c) for c in row_citations),
        ],
        subject=module_id,
        fact="drift",
        value="reported",
        citations=row_citations,
    )
    book.blank()

    _table(book, ["Consumer asset", "Seed it comes from", "Seed present", "Source"])
    _asset_rows(book, entry, prefix)
    book.blank()


__all__ = ["DEFAULT_HUB", "Composition", "compose"]
