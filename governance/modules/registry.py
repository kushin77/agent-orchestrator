"""Assemble the federated registry — and answer membership honestly.

---knowledge---
module_id: governance.modules.registry
system: governance
app: modules
solution_class: enterprise
patterns: [deterministic]
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [load_targets, load_register, build, render, findings, by_disposition, membership]
invariants: ""
gotchas: ""
related: ["#591", "#952"]
do_not_duplicate: null
---knowledge---

The registry is assembled from four surfaces, in this precedence:

1. **the hub catalog** (``vendor/CMR/catalog``) — the authority on what a module
   is and what is mandatory;
2. **the declared target set** (:mod:`governance.modules.targets`, a package
   data file) — what the mandatory set *should* become, each target naming the
   blocking hub issue(s) that keep it pending;
3. **this repo's sub-module admission register** (root ``module.json``
   ``submodules``) — a set of *claims*, which never confer membership;
4. **the in-tree vendoring scan** (:mod:`governance.modules.vendoring`) — proof
   that the first three are references, not copies.

From those, every name resolves to exactly one of the three states or to the
``not-a-module`` refusal. The catalog always wins: a declared target that lands
in the hub catalog is **derived** as registered, and a register entry the
catalog does not carry is **refused** however it describes itself.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from governance.modules import audit, health, hub, schema, vendoring
from governance.modules import policy as acceptance
from governance.modules.model import (
    CATALOG_MODULE_NOT_MANDATORY,
    NOT_A_MODULE,
    REGISTERED_MANDATORY,
    SCHEMA,
    STATES,
    TARGET_PENDING,
    CannotAssess,
    Refusal,
    sorted_refusals,
)

#: The declared target set, read from the package so the registry does not
#: depend on the caller's working directory.
DEFAULT_TARGETS = Path(__file__).resolve().parent / "targets.json"

ROOT_MANIFEST = "module.json"
REGISTER_KEY = "submodules"

#: The drift the registry exists to report (kushin77/CMR#952).
MEMBERSHIP_NOTE = (
    "the hub catalog carries no module with this id — portfolio membership is "
    "not module membership (kushin77/CMR#952)"
)


def _rel(path: Path, base: Path) -> str:
    try:
        return os.path.relpath(str(path), str(base))
    except ValueError:  # pragma: no cover - different drives (Windows only)
        return str(path)


def _as_list(value: Any) -> List[Any]:
    return list(value) if isinstance(value, list) else []


# --------------------------------------------------------------------------- #
# declared inputs
# --------------------------------------------------------------------------- #
def load_targets(path: Path, repo_root: Path) -> Tuple[Dict[str, Any], List[Refusal]]:
    """Read the declared target/watch sets; unreadable is CANNOT-ASSESS."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CannotAssess("declared target set unreadable: {} ({})".format(path, exc))
    reference_path = _rel(Path(path), Path(repo_root))
    refusals: List[Refusal] = []
    targets: List[Dict[str, Any]] = []
    for item in _as_list(raw.get("targets")):
        if not isinstance(item, dict) or not item.get("id"):
            refusals.append(
                Refusal("TARGET-WITHOUT-ID", str(item), "a declared target carries no id", reference_path)
            )
            continue
        module_id = str(item["id"])
        blocking = [str(ref) for ref in _as_list(item.get("blocking"))]
        if not blocking:
            refusals.append(
                Refusal(
                    "TARGET-WITHOUT-BLOCKING",
                    module_id,
                    "a declared target must name the blocking hub issue(s) that keep "
                    "it pending — an unnamed blocker is a wish, not a target",
                    reference_path,
                )
            )
        targets.append(
            {
                "id": module_id,
                "repo": item.get("repo"),
                "blocking": blocking,
                "onboarding": item.get("onboarding"),
                "note": str(item.get("note") or ""),
            }
        )
    watch: List[Dict[str, Any]] = []
    for item in _as_list(raw.get("watch")):
        if not isinstance(item, dict) or not item.get("id"):
            continue
        watch.append(
            {
                "id": str(item["id"]),
                "repo": item.get("repo"),
                "hub_issue": item.get("hub_issue"),
                "disposition": item.get("disposition"),
                "note": str(item.get("note") or ""),
            }
        )
    return {"reference_path": reference_path, "targets": targets, "watch": watch}, refusals


def load_register(repo_root: Path) -> Tuple[Dict[str, Dict[str, Any]], str]:
    """This repo's sub-module admission register — a claim, never the authority."""
    path = Path(repo_root) / ROOT_MANIFEST
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}, "{0} {1} (absent or unreadable)".format(ROOT_MANIFEST, REGISTER_KEY)
    register: Dict[str, Dict[str, Any]] = {}
    for item in _as_list(data.get(REGISTER_KEY)):
        if isinstance(item, dict) and item.get("id"):
            register[str(item["id"])] = {
                "repo": item.get("repo"),
                "admission": item.get("admission"),
                "request": item.get("request"),
                "rationale": item.get("rationale"),
            }
    return register, "{0} {1} register".format(ROOT_MANIFEST, REGISTER_KEY)


# --------------------------------------------------------------------------- #
# entries
# --------------------------------------------------------------------------- #
def _assets(repo_root: Path, assets: Iterable[str], seeds: Iterable[hub.Seed]) -> List[Dict[str, Any]]:
    by_asset = {seed.asset: seed for seed in seeds}
    rows = []
    for asset in sorted(assets):
        seed = by_asset.get(asset)
        present = bool(seed and seed.present)
        rows.append(
            {
                "asset": asset,
                "seed": seed.path if present else None,
                "seed_present": present,
                "present_in_repo": (Path(repo_root) / asset).is_file(),
            }
        )
    return rows


def _catalog_entry(
    repo_root: Path, module: hub.HubModule, hub_root: str, revision: Optional[str], live: bool
) -> Dict[str, Any]:
    registered = bool(module.mandatory)
    return {
        "id": module.id,
        "state": REGISTERED_MANDATORY if registered else CATALOG_MODULE_NOT_MANDATORY,
        "shipped": True,
        "mandatory": registered,
        "consumer_assets": list(module.consumer_assets),
        "owning_repo": module.repo,
        "pin": module.pin,
        "rev": revision,
        "board_ref": module.board_ref,
        "blocking": [],
        "claim": None,
        "reference": {
            "kind": "hub-catalog-entry",
            "hub": hub_root,
            "path": module.manifest_path,
            "repo": module.repo,
            "pin": module.pin,
        },
        "assets": _assets(repo_root, module.consumer_assets, module.seeds),
        "health": health.catalog_spec(module.repo, module.pin, live=live),
        "note": "",
    }


def _pending_entry(
    target: Dict[str, Any], hub_root: str, reference_path: str, catalog: hub.HubCatalog
) -> Dict[str, Any]:
    blocking = list(target["blocking"])
    hub_side = [ref for ref in blocking if ref.startswith("kushin77/CMR#")]
    return {
        "id": target["id"],
        "state": TARGET_PENDING,
        "shipped": False,
        "mandatory": None,
        "consumer_assets": [],
        "owning_repo": target["repo"],
        "pin": None,
        "rev": None,
        "board_ref": hub_side[0] if hub_side else (blocking[0] if blocking else None),
        "blocking": blocking,
        "onboarding": target["onboarding"],
        "claim": None,
        "reference": {
            "kind": "declared-target",
            "hub": hub_root,
            "path": reference_path,
            "repo": target["repo"],
            "pin": None,
        },
        "assets": [],
        "health": health.pending_spec(catalog.entry_path(target["id"])),
        "note": target["note"],
    }


def _refused_entry(
    name: str,
    *,
    repo: Optional[str],
    kind: str,
    hub_issue: Optional[str],
    disposition: Optional[str],
    note: str,
    claim: Optional[Dict[str, Any]],
    catalog: hub.HubCatalog,
    hub_root: str,
) -> Dict[str, Any]:
    detail = MEMBERSHIP_NOTE
    if claim:
        detail += "; the local admission register records admission={!r}, which does not confer membership".format(
            claim.get("admission")
        )
    elif disposition:
        detail += "; recorded as {!r} at the hub".format(disposition)
    return {
        "id": name,
        "state": NOT_A_MODULE,
        "membership": "refused",
        "shipped": False,
        "mandatory": None,
        "consumer_assets": [],
        "owning_repo": repo,
        "pin": None,
        "rev": None,
        "board_ref": hub_issue,
        "blocking": [hub_issue] if hub_issue else [],
        "claim": claim,
        "reference": {
            "kind": kind,
            "hub": hub_root,
            "path": catalog.entry_path(name),
            "repo": repo,
            "pin": None,
        },
        "assets": [],
        "health": health.pending_spec(catalog.entry_path(name)),
        "detail": detail,
        "note": note,
    }


# --------------------------------------------------------------------------- #
# the registry
# --------------------------------------------------------------------------- #
def build(
    repo_root: Path,
    hub_root: Path = hub.DEFAULT_HUB,
    targets_path: Optional[Path] = None,
    live: bool = False,
    include_vendoring: bool = True,
    controls_path: Optional[Path] = None,
    schema_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Build the registry. Raises :class:`CannotAssess` when the hub is unreadable.

    Three artifacts judge the result before it leaves this function (issue #591):
    the declared acceptance policy (``controls.yaml``, via :mod:`policy`) stamps
    every refusal and supplies the dispositions the audit trail records; the
    frozen schema (``module-registry.schema.json``, via :mod:`schema`) is
    enforced **on the document this function is about to return** rather than
    asserted later by a test; and :mod:`audit` projects the judged document into
    the deterministic record set the trail is appended from. Any of the three
    failing is CANNOT-ASSESS — never a pass.
    """
    repo_root = Path(repo_root)
    hub_root = Path(hub_root)
    targets_path = Path(targets_path) if targets_path else DEFAULT_TARGETS
    controls = acceptance.load(controls_path)

    catalog = hub.load(hub_root, repo_root, recorded_root=str(hub_root))
    hub_root_str = catalog.root
    declared, refusals = load_targets(targets_path, repo_root)
    register, register_source = load_register(repo_root)
    refusals = list(catalog.refusals) + list(refusals)

    entries: List[Dict[str, Any]] = [
        _catalog_entry(repo_root, module, hub_root_str, catalog.revision, live)
        for module in catalog.modules
    ]

    target_ids = {target["id"] for target in declared["targets"]}
    for target in declared["targets"]:
        module = catalog.by_id(target["id"])
        if module is None:
            entries.append(
                _pending_entry(target, hub_root_str, declared["reference_path"], catalog)
            )
            continue
        if not module.mandatory:
            refusals.append(
                Refusal(
                    "MODULE-TARGET-LANDED-NOT-MANDATORY",
                    module.id,
                    "the declared target is now a catalog module but is not registered "
                    "mandatory — a target that has not landed must never be reported "
                    "as shipped",
                    module.manifest_path,
                )
            )

    refused: List[Dict[str, Any]] = []
    watch_by_id = {item["id"]: item for item in declared["watch"]}
    for name in sorted(set(register) | set(watch_by_id)):
        if name in catalog.ids or name in target_ids:
            continue
        watch = watch_by_id.get(name)
        claim = register.get(name)
        refused.append(
            _refused_entry(
                name,
                repo=(watch or {}).get("repo") or (claim or {}).get("repo"),
                kind="register-claim" if claim else "watch-question",
                hub_issue=(watch or {}).get("hub_issue"),
                disposition=(watch or {}).get("disposition"),
                note=(watch or {}).get("note", ""),
                claim=claim,
                catalog=catalog,
                hub_root=hub_root_str,
            )
        )

    if include_vendoring:
        refusals.extend(vendoring.scan(repo_root, catalog, entries))

    entries.sort(key=lambda entry: entry["id"])
    refused.sort(key=lambda entry: entry["id"])
    # The declared acceptance policy judges every refusal the registry emits. A
    # code it does not declare raises here (CANNOT-ASSESS): a refusal nobody
    # declared is a refusal nobody reviewed, and recording it would look like
    # evidence.
    findings = tuple(controls.judge(finding) for finding in sorted_refusals(refusals))

    summary: Dict[str, int] = {state: 0 for state in STATES}
    for entry in entries:
        summary[entry["state"]] = summary.get(entry["state"], 0) + 1
    summary[NOT_A_MODULE] = len(refused)

    document: Dict[str, Any] = {
        "schema": SCHEMA,
        "states": list(STATES),
        "membership_refusal": NOT_A_MODULE,
        "hub": {
            "root": catalog.root,
            "revision": catalog.revision,
            "revision_source": catalog.revision_source,
            "mandatory_registry": "{}/{}".format(hub.CATALOG, hub.MANDATORY_TSV),
            "module_count": len(catalog.modules),
            "mandatory_count": len(catalog.mandatory_rows),
        },
        "declared": {
            "targets": declared["reference_path"],
            "register": register_source,
            "target_count": len(declared["targets"]),
            "watch_count": len(declared["watch"]),
        },
        "summary": summary,
        "modules": entries,
        "not_modules": refused,
        "refusals": [finding.as_dict() for finding in findings],
        "policy": controls.as_document(_rel(controls.path, repo_root)),
    }

    records = audit.records(document, controls)
    document["audit"] = {
        "schema": audit.SCHEMA,
        "summary": audit.summary(records),
        "records": records,
    }
    # The generator validates what it emits: the row shape is enforced on the
    # document about to leave this function, not asserted later by a test.
    schema.validate(document, schema_path)
    return document


def render(doc: Dict[str, Any]) -> str:
    """The canonical serialisation: sorted keys, two-space indent, one newline.

    Two builds over one revision are byte-identical because nothing in the
    document is time-, order- or path-incidental: every list is sorted and no
    field records when it was read.
    """
    return json.dumps(doc, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def findings(doc: Dict[str, Any]) -> Tuple[Refusal, ...]:
    """The document's refusals, back as value objects, with the declared judgment."""
    return tuple(
        Refusal(
            code=finding["code"],
            subject=finding["subject"],
            detail=finding["detail"],
            source=finding.get("source", ""),
            disposition=finding.get("disposition", ""),
            condition=finding.get("condition", ""),
        )
        for finding in doc.get("refusals") or []
    )


def by_disposition(doc: Dict[str, Any], disposition: str) -> Tuple[Refusal, ...]:
    """The refusals the declared policy judged with one disposition.

    ``fatal`` and ``recorded`` are read from the document, so the exit code a
    caller derives follows the *declaration that judged the build* rather than a
    second copy of the rule.
    """
    undeclared = [
        finding
        for finding in doc.get("refusals") or []
        if not finding.get("disposition")
    ]
    if undeclared:
        raise acceptance.PolicyUnavailable(
            "the document carries {} unjudged refusal(s) ({}): a refusal without a "
            "declared disposition cannot be weighed".format(
                len(undeclared), ", ".join(str(item.get("code")) for item in undeclared)
            )
        )
    return tuple(finding for finding in findings(doc) if finding.disposition == disposition)


def membership(doc: Dict[str, Any], name: str) -> Tuple[str, Dict[str, Any]]:
    """Resolve one name. Returns a state, or the ``not-a-module`` refusal."""
    for entry in doc.get("modules") or []:
        if entry["id"] == name:
            return entry["state"], entry
    for entry in doc.get("not_modules") or []:
        if entry["id"] == name:
            return NOT_A_MODULE, entry
    return NOT_A_MODULE, {
        "id": name,
        "state": NOT_A_MODULE,
        "membership": "refused",
        "owning_repo": None,
        "claim": None,
        "blocking": [],
        "detail": MEMBERSHIP_NOTE
        + "; no declared target and no register entry names it either",
    }
