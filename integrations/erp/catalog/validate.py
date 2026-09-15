"""The ERP module's rules, and the evidence each one measures (issue #646).

Eight rules, each of which can genuinely fail, each of which names its subject:

1. **the manifest is the frozen shape.** ``module.yaml`` is validated against
   ``catalog/schema/integration-module.schema.json``. The three facts that make
   the module a module — ``mandatory``, the feature flag's ``off`` default, and
   ``data_source: indexer`` — are constrained *by the schema*, so a manifest that
   stops declaring them stops validating. The schema is loaded through
   ``governance/modules/schema.py``, which refuses to run a schema that uses a
   keyword it cannot enforce: an ignored keyword would be a requirement nobody
   measures.
2. **the pointers resolve.** Every path the manifest declares exists, and the
   declared schema directory is the module's own.
3. **the catalogue is valid.** Every catalogue file is validated against its
   schema, and a refusal quotes the ``<json path>: <what is wrong>`` the
   validator produced.
4. **the provenance is complete and singular.** Every catalogue file carries the
   full GR-10 declaration, and it is the manifest's — one declaration, agreed,
   never a second copy to drift.
5. **the cross-references resolve.** A document's family, a capability's pillar
   and every ``child_issues`` entry are declared in the module map, and every
   pillar the map names is a real directory in this repository.
6. **the indexer really serves it.** Every glob the manifest declares is
   registered verbatim in ``governance/knowledge/sources.py``, and at least one
   of them is a glob under the catalogue — so "the module is indexer-fed" cannot
   outrun the indexer.
7. **there is no second store.** No declared document fact is restated on the
   module's declaration surface (the manifest, the README, the source registry),
   the README names the catalogue, and no two files declare the same document id.
8. **the gap analysis is reachable.** ``docs/README.md`` references it.

The rules are functions over a *root*: the gate and the suite both drive them
against a scratch copy of the tree, where a planted violation must be refused
*by name* — a check whose pass and fail paths collapse is a formality.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from governance.modules import schema as subset

from integrations.erp.catalog import model
from integrations.erp.catalog.model import CannotAssess, Finding, Report


def _where(path: Path) -> str:
    return path.as_posix()


def _load_schema(root: Path, relative: Path) -> Mapping[str, Any]:
    """Load one of the module's frozen schemas, through the repository's validator."""
    try:
        return subset.load(root / relative)
    except subset.CannotAssess as exc:
        raise CannotAssess(str(exc))


def _catalogue_schema(root: Path, relative: Path, cannot: List[str]) -> Optional[Mapping[str, Any]]:
    """Load a catalogue schema, recording an unenforceable one as CANNOT-ASSESS.

    A schema the repository's validator refuses to enforce cannot judge anything,
    so the files it would have judged are left *unjudged* rather than passed —
    and the reason names the schema and the keyword.
    """
    try:
        return _load_schema(root, relative)
    except CannotAssess as exc:
        cannot.append(str(exc))
        return None


def _judge(document: Any, schema: Mapping[str, Any], relative: Path, code: str) -> Tuple[Finding, ...]:
    """Validate one document, turning each violation into a named refusal."""
    return tuple(
        Finding(code, problem, _where(relative))
        for problem in subset.problems(document, schema)
    )


def _declares(text: str, field: str, value: str) -> bool:
    """Whether ``text`` restates the fact ``value``.

    A declared *name* is matched case-sensitively as a whole word: a restatement
    reproduces the catalogue's own spelling, and the case-sensitivity is what
    keeps ordinary lowercase prose from being reported as a restatement. A
    declared *id* is matched case-insensitively as a whole token, so its slug
    form counts wherever it appears.
    """
    if field == "name":
        pattern = r"(?<![0-9A-Za-z])%s(?![0-9A-Za-z])" % re.escape(value)
        return re.search(pattern, text) is not None
    pattern = r"(?<![0-9a-z])%s(?![0-9a-z])" % re.escape(value.lower())
    return re.search(pattern, text.lower()) is not None


def _read(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _string(value: Any) -> str:
    return value if isinstance(value, str) else ""


def verify(root: Path) -> Report:
    """Judge the ERP module's declaration rooted at ``root``."""
    root = Path(root)
    findings: List[Finding] = []
    cannot: List[str] = []
    documents: List[str] = []

    manifest_rel = model.MANIFEST_PATH
    manifest_path = root / manifest_rel
    if not manifest_path.is_file():
        return Report(
            (
                Finding(
                    model.CODE_MANIFEST_MISSING,
                    "the module manifest is missing",
                    _where(manifest_rel),
                ),
            )
        )

    try:
        manifest = model.load_manifest(manifest_path)
    except CannotAssess as exc:
        return Report((), (str(exc),))
    try:
        manifest_schema = _load_schema(root, model.MANIFEST_SCHEMA)
    except CannotAssess as exc:
        return Report((), (str(exc),))

    # --- 1. the manifest is the frozen shape --------------------------------
    findings.extend(_judge(manifest, manifest_schema, manifest_rel, model.CODE_MANIFEST_INVALID))
    manifest_provenance = manifest.get("provenance")
    manifest_provenance = manifest_provenance if isinstance(manifest_provenance, Mapping) else {}

    catalogue = manifest.get("catalogue")
    catalogue = catalogue if isinstance(catalogue, Mapping) else {}
    catalogue_root_rel = _string(catalogue.get("root"))
    catalogue_root = (root / catalogue_root_rel) if catalogue_root_rel else None

    # --- 2. the declared pointers resolve -----------------------------------
    if catalogue_root is not None and not catalogue_root.is_dir():
        findings.append(
            Finding(
                model.CODE_CATALOGUE_MISSING,
                "the declared catalogue root is not a directory",
                catalogue_root_rel,
            )
        )
        catalogue_root = None

    resolved: Dict[str, Path] = {}
    if catalogue_root is not None:
        for key in ("module_map", "capabilities", "documents", "schema"):
            value = _string(catalogue.get(key))
            if not value:
                continue
            target = catalogue_root / value
            resolved[key] = target
            ok = target.is_dir() if key in ("documents", "schema") else target.is_file()
            if not ok:
                findings.append(
                    Finding(
                        model.CODE_CATALOGUE_MISSING,
                        "the declared catalogue %s does not resolve" % key,
                        "%s/%s" % (catalogue_root_rel, value),
                    )
                )

        declared_schema = resolved.get("schema")
        if declared_schema is not None and declared_schema.resolve() != (root / model.SCHEMA_DIR).resolve():
            findings.append(
                Finding(
                    model.CODE_CATALOGUE_POINTER,
                    "the declared schema directory is not the module's own (%s)" % _where(model.SCHEMA_DIR),
                    catalogue_root_rel,
                )
            )

    # --- 3./4./5. the catalogue itself --------------------------------------
    module_map: Mapping[str, Any] = {}
    capabilities: Mapping[str, Any] = {}
    catalogue_files = 0

    module_map_path = resolved.get("module_map")
    module_map_schema = _catalogue_schema(root, model.MODULE_MAP_SCHEMA, cannot)
    if module_map_path is not None and module_map_path.is_file() and module_map_schema is not None:
        catalogue_files += 1
        relative = module_map_path.relative_to(root)
        try:
            document = model.load_json(module_map_path)
        except CannotAssess as exc:
            cannot.append(str(exc))
        else:
            findings.extend(
                _judge(document, module_map_schema, relative, model.CODE_CATALOGUE_INVALID)
            )
            findings.extend(_provenance_findings(document, manifest_provenance, relative))
            module_map = document if isinstance(document, Mapping) else {}

    capabilities_path = resolved.get("capabilities")
    capability_schema = _catalogue_schema(root, model.CAPABILITY_SCHEMA, cannot)
    if capabilities_path is not None and capabilities_path.is_file() and capability_schema is not None:
        catalogue_files += 1
        relative = capabilities_path.relative_to(root)
        try:
            document = model.load_json(capabilities_path)
        except CannotAssess as exc:
            cannot.append(str(exc))
        else:
            findings.extend(
                _judge(document, capability_schema, relative, model.CODE_CATALOGUE_INVALID)
            )
            findings.extend(_provenance_findings(document, manifest_provenance, relative))
            capabilities = document if isinstance(document, Mapping) else {}

    declared_documents: List[Mapping[str, Any]] = []
    documents_dir = resolved.get("documents")
    document_schema = _catalogue_schema(root, model.DOCUMENT_SCHEMA, cannot)
    if documents_dir is not None and documents_dir.is_dir() and document_schema is not None:
        for path in model.document_files(documents_dir):
            catalogue_files += 1
            relative = path.relative_to(root)
            try:
                document = model.load_json(path)
            except CannotAssess as exc:
                cannot.append(str(exc))
                continue
            findings.extend(_judge(document, document_schema, relative, model.CODE_CATALOGUE_INVALID))
            findings.extend(_provenance_findings(document, manifest_provenance, relative))
            if isinstance(document, Mapping):
                declared_documents.append(document)
                ident = document.get("id")
                if isinstance(ident, str):
                    documents.append(ident)

    findings.extend(_cross_reference_findings(root, module_map, capabilities, declared_documents))
    findings.extend(_duplicate_id_findings(declared_documents))

    # --- 6. the indexer really serves the module ----------------------------
    sources_rel = model.SOURCES_PATH
    sources_text = _read(root / sources_rel)
    if sources_text is None:
        findings.append(
            Finding(
                model.CODE_SOURCE_REGISTRY_MISSING,
                "the indexer source catalogue is missing",
                _where(sources_rel),
            )
        )
    else:
        declared_globs = [
            _string(entry.get("glob"))
            for entry in (manifest.get("indexer_sources") or [])
            if isinstance(entry, Mapping)
        ]
        declared_globs = [glob for glob in declared_globs if glob]
        for glob in declared_globs:
            if glob not in sources_text:
                findings.append(
                    Finding(
                        model.CODE_SOURCE_UNREGISTERED,
                        "the indexer does not carry this source: register the glob in %s" % _where(sources_rel),
                        glob,
                    )
                )
        if not any(catalogue_root_rel and glob.startswith(catalogue_root_rel) for glob in declared_globs):
            findings.append(
                Finding(
                    model.CODE_CATALOGUE_UNINDEXED,
                    "no declared indexer source is under the catalogue (%s), so the module's"
                    " own facts are not indexed" % (catalogue_root_rel or "unset"),
                    manifest_rel.as_posix(),
                )
            )

    # --- 7. no second store -------------------------------------------------
    readme_rel = model.README_PATH
    readme_text = _read(root / readme_rel)
    if readme_text is None:
        findings.append(
            Finding(model.CODE_README_MISSING, "the module README is missing", _where(readme_rel))
        )
    elif catalogue_root_rel and catalogue_root_rel not in readme_text:
        findings.append(
            Finding(
                model.CODE_CATALOGUE_POINTER,
                "the README does not name the catalogue (%s)" % catalogue_root_rel,
                _where(readme_rel),
            )
        )

    surface: List[Tuple[str, str]] = [(manifest_rel.as_posix(), _read(manifest_path) or "")]
    if readme_text is not None:
        surface.append((readme_rel.as_posix(), readme_text))
    if sources_text is not None:
        surface.append((sources_rel.as_posix(), sources_text))

    for document in declared_documents:
        for field, value in model.vocabulary(document):
            for surface_rel, text in surface:
                if _declares(text, field, value):
                    findings.append(
                        Finding(
                            model.CODE_DUPLICATE_FACT,
                            "restates the catalogue fact %s %r; domain facts live only under %s"
                            % (field, value, catalogue_root_rel or model.CATALOGUE_ROOT.as_posix()),
                            surface_rel,
                        )
                    )

    # --- 8. the gap analysis is reachable -----------------------------------
    gaps_rel = model.GAPS_PATH
    if not (root / gaps_rel).is_file():
        findings.append(
            Finding(model.CODE_GAPS_MISSING, "the epic's gap analysis is missing", _where(gaps_rel))
        )
    else:
        index_text = _read(root / model.DOCS_INDEX_PATH)
        if index_text is None:
            findings.append(
                Finding(
                    model.CODE_GAPS_UNREFERENCED,
                    "the docs index is missing, so nothing references the gap analysis",
                    _where(model.DOCS_INDEX_PATH),
                )
            )
        elif gaps_rel.name not in index_text:
            findings.append(
                Finding(
                    model.CODE_GAPS_UNREFERENCED,
                    "the docs index does not reference %s" % gaps_rel.name,
                    _where(model.DOCS_INDEX_PATH),
                )
            )

    return Report(tuple(findings), tuple(cannot), catalogue_files, tuple(documents))


def _provenance_findings(
    document: Any, manifest_provenance: Mapping[str, Any], relative: Path
) -> Tuple[Finding, ...]:
    """Every catalogue file carries the manifest's GR-10 declaration, verbatim."""
    if not isinstance(document, Mapping):
        return ()
    declared = document.get("provenance")
    if not isinstance(declared, Mapping):
        return ()
    findings = []
    for field in model.PROVENANCE_FIELDS:
        if field not in manifest_provenance:
            continue
        expected = manifest_provenance[field]
        if declared.get(field, object()) != expected:
            findings.append(
                Finding(
                    model.CODE_PROVENANCE_MISMATCH,
                    "provenance %s is %r but the manifest declares %r"
                    % (field, declared.get(field), expected),
                    _where(relative),
                )
            )
    return tuple(findings)


def _cross_reference_findings(
    root: Path,
    module_map: Mapping[str, Any],
    capabilities: Mapping[str, Any],
    documents: List[Mapping[str, Any]],
) -> Tuple[Finding, ...]:
    """Every reference a catalogue file makes must resolve to a declaration."""
    findings: List[Finding] = []
    map_rel = (model.CATALOGUE_ROOT / "module-map.json").as_posix()
    caps_rel = (model.CATALOGUE_ROOT / "capabilities.json").as_posix()

    pillars = [entry for entry in (module_map.get("pillars") or []) if isinstance(entry, Mapping)]
    pillar_ids = {_string(entry.get("id")) for entry in pillars}
    children = [entry for entry in (module_map.get("children") or []) if isinstance(entry, Mapping)]
    child_issues = {entry.get("issue") for entry in children}

    for entry in pillars:
        path = _string(entry.get("path"))
        if path and not (root / path / "README.md").is_file():
            findings.append(
                Finding(
                    model.CODE_CROSS_REFERENCE,
                    "the map names pillar %r, which is not a pillar of this repository" % _string(entry.get("id")),
                    map_rel,
                )
            )

    for entry in capabilities.get("capabilities") or []:
        if not isinstance(entry, Mapping):
            continue
        pillar = _string(entry.get("pillar"))
        if pillar and pillar not in pillar_ids:
            findings.append(
                Finding(
                    model.CODE_CROSS_REFERENCE,
                    "capability %r names pillar %r, which the module map does not declare" % (_string(entry.get("id")), pillar),
                    caps_rel,
                )
            )
        for issue in entry.get("child_issues") or []:
            if issue not in child_issues:
                findings.append(
                    Finding(
                        model.CODE_CROSS_REFERENCE,
                        "capability %r names child issue %s, which the module map does not declare" % (_string(entry.get("id")), issue),
                        caps_rel,
                    )
                )

    family_ids = {
        _string(entry.get("id"))
        for entry in (capabilities.get("capabilities") or [])
        if isinstance(entry, Mapping)
    }
    documents_dir = model.CATALOGUE_ROOT / "documents"
    for document in documents:
        relative = documents_dir / ("%s.json" % _string(document.get("id")))
        family = _string(document.get("family"))
        if family and family not in family_ids:
            findings.append(
                Finding(
                    model.CODE_CROSS_REFERENCE,
                    "family %r is not a declared capability" % family,
                    relative.as_posix(),
                )
            )
        issue = document.get("owning_issue")
        if issue not in child_issues:
            findings.append(
                Finding(
                    model.CODE_CROSS_REFERENCE,
                    "owning issue %s is not a declared child of the epic" % issue,
                    relative.as_posix(),
                )
            )
    return tuple(findings)


def declared_vocabulary(root: Path) -> Tuple[Tuple[str, str], ...]:
    """Every ``(field, value)`` domain fact the catalogue declares, in file order.

    Reads the manifest to find the catalogue, then every declared document. An
    unreadable input yields nothing rather than a guess: this feeds the gate's
    provocations, and a provocation built on an invented fact would prove
    nothing about the catalogue it claims to test.
    """
    root = Path(root)
    try:
        manifest = model.load_manifest(root / model.MANIFEST_PATH)
    except CannotAssess:
        return ()
    catalogue = manifest.get("catalogue")
    catalogue = catalogue if isinstance(catalogue, Mapping) else {}
    root_rel = _string(catalogue.get("root"))
    documents_rel = _string(catalogue.get("documents"))
    if not root_rel or not documents_rel:
        return ()
    directory = root / root_rel / documents_rel
    if not directory.is_dir():
        return ()
    facts: List[Tuple[str, str]] = []
    for path in model.document_files(directory):
        try:
            document = model.load_json(path)
        except CannotAssess:
            continue
        facts.extend(model.vocabulary(document))
    return tuple(facts)


def _duplicate_id_findings(documents: List[Mapping[str, Any]]) -> Tuple[Finding, ...]:
    """Two files declaring one id is two stores of one fact."""
    seen: Dict[str, int] = {}
    findings: List[Finding] = []
    for document in documents:
        ident = _string(document.get("id"))
        if not ident:
            continue
        seen[ident] = seen.get(ident, 0) + 1
    for ident, count in sorted(seen.items()):
        if count > 1:
            findings.append(
                Finding(
                    model.CODE_CATALOGUE_DUPLICATE,
                    "document id %r is declared by %d files" % (ident, count),
                    (model.CATALOGUE_ROOT / "documents").as_posix(),
                )
            )
    return tuple(findings)
