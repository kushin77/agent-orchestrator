"""The ERP module's paths, findings and loaders (EPIC #645, issue #646).

One module states where the declaration lives and what a refusal looks like, so
the validator, the CLI, the gate and the suite read the same names instead of
each carrying its own copy — the same discipline the module itself demands of
its domain facts.

Two conventions are load-bearing here:

* **A refusal names its subject.** :class:`Finding` carries a stable code, the
  path it is about, and a message that says what is wrong with *that* file. A
  gate that reports "the module is invalid" cannot be acted on, and one that
  copies the wording it greps for cannot be trusted to mean what it greps.
* **An unreadable input is CANNOT-ASSESS, never NOT-OK.** A manifest that does
  not parse has not been judged; reporting it as a failure would be a claim the
  check cannot support. :class:`CannotAssess` is that honest third state, and it
  leaves on exit code 2.

---knowledge---
module_id: integrations.erp.catalog.model
system: integrations
app: erp
solution_class: enterprise
patterns: []
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [CannotAssess, Finding, Report, load_manifest, load_json, document_files, vocabulary]
invariants: ""
gotchas: ""
related: ["#1910"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Tuple

# -- where the module's declaration lives ------------------------------------
MODULE_ROOT = Path("integrations") / "erp"
MANIFEST_PATH = MODULE_ROOT / "module.yaml"
README_PATH = MODULE_ROOT / "README.md"

#: The catalogue's own location. The manifest declares these as pointers, but
#: the *schemas* are needed to judge the manifest that declares them, so the
#: directory is a constant and the manifest's pointer must agree with it.
CATALOGUE_ROOT = MODULE_ROOT / "catalog"
SCHEMA_DIR = CATALOGUE_ROOT / "schema"

MANIFEST_SCHEMA = SCHEMA_DIR / "integration-module.schema.json"
MODULE_MAP_SCHEMA = SCHEMA_DIR / "module-map.schema.json"
CAPABILITY_SCHEMA = SCHEMA_DIR / "capability.schema.json"
DOCUMENT_SCHEMA = SCHEMA_DIR / "document.schema.json"

#: The indexer's one declarative source catalogue — what "indexer-fed" means.
SOURCES_PATH = Path("governance") / "knowledge" / "sources.py"
#: The epic's gap analysis, and the index that must reference it.
GAPS_PATH = Path("docs") / "ERP-MODULE-GAP-ANALYSIS.md"
DOCS_INDEX_PATH = Path("docs") / "README.md"

EXIT_OK = 0
EXIT_NOT_OK = 1
EXIT_CANNOT_ASSESS = 2

#: The GR-10 provenance declaration every catalogue file must carry, verbatim.
PROVENANCE_FIELDS: Tuple[str, ...] = (
    "upstream",
    "license",
    "pattern_source_only",
    "code_copied",
    "retrieved",
    "source_doc",
)

# -- finding codes (stable, greppable, one per refusal) -----------------------
CODE_MANIFEST_MISSING = "manifest-missing"
CODE_MANIFEST_INVALID = "manifest-invalid"
CODE_CATALOGUE_MISSING = "catalogue-missing"
CODE_CATALOGUE_INVALID = "catalogue-invalid"
CODE_CATALOGUE_DUPLICATE = "catalogue-duplicate"
CODE_PROVENANCE_MISMATCH = "provenance-mismatch"
CODE_CROSS_REFERENCE = "cross-reference"
CODE_SOURCE_REGISTRY_MISSING = "source-registry-missing"
CODE_SOURCE_UNREGISTERED = "source-unregistered"
CODE_CATALOGUE_UNINDEXED = "catalogue-unindexed"
CODE_DUPLICATE_FACT = "duplicate-fact"
CODE_CATALOGUE_POINTER = "catalogue-pointer"
CODE_README_MISSING = "readme-missing"
CODE_GAPS_MISSING = "gaps-missing"
CODE_GAPS_UNREFERENCED = "gaps-unreferenced"


class CannotAssess(Exception):
    """The check cannot answer its question: an input is unreadable or unenforceable."""


@dataclass(frozen=True)
class Finding:
    """One refusal: a stable code, the path it is about, and what is wrong."""

    code: str
    message: str
    path: str = ""

    def line(self) -> str:
        return "  FAIL  %s  %s  %s" % (self.code, self.path or "(module)", self.message)


@dataclass(frozen=True)
class Report:
    """The full verdict: findings, the reasons it could not be assessed, and the tally."""

    findings: Tuple[Finding, ...]
    cannot_assess: Tuple[str, ...] = ()
    catalogue_files: int = 0
    documents: Tuple[str, ...] = ()

    @property
    def exit_code(self) -> int:
        if self.cannot_assess:
            return EXIT_CANNOT_ASSESS
        return EXIT_NOT_OK if self.findings else EXIT_OK


def load_manifest(path: Path) -> Mapping[str, Any]:
    """Read ``module.yaml`` as a mapping. Unreadable or malformed is CANNOT-ASSESS."""
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - PyYAML is a gate dependency
        raise CannotAssess("PyYAML is not installed, so the manifest cannot be read (%s)" % exc)

    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise CannotAssess("the manifest %s is unreadable: %s" % (path, exc))
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise CannotAssess("the manifest %s is not valid YAML: %s" % (path, exc))
    if not isinstance(data, Mapping):
        raise CannotAssess("the manifest %s is not a mapping of keys to values" % path)
    return data


def load_json(path: Path) -> Any:
    """Read one JSON document. Unreadable or malformed is CANNOT-ASSESS."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise CannotAssess("%s is unreadable: %s" % (path, exc))
    try:
        return json.loads(text)
    except ValueError as exc:
        raise CannotAssess("%s is not valid JSON: %s" % (path, exc))


def document_files(directory: Path) -> Tuple[Path, ...]:
    """Every declared document type, in a stable order."""
    try:
        return tuple(sorted((p for p in directory.glob("*.json") if p.is_file()), key=lambda p: p.name))
    except OSError:
        return ()


def vocabulary(document: Any) -> Tuple[Tuple[str, str], ...]:
    """The ``(field, value)`` domain facts one declared document carries.

    Only the declared document types are scanned for a second store. A
    capability or family name ("Selling", "Support") is ordinary prose that a
    document may legitimately use, whereas a document type is the module's
    load-bearing domain vocabulary — the thing ERP-02 fills with parameters and
    every later child consumes. That distinction is deliberate; it is what keeps
    the rule about knowledge rather than about wording.
    """
    if not isinstance(document, Mapping):
        return ()
    found = []
    for field in ("name", "id"):
        value = document.get(field)
        if isinstance(value, str) and value:
            found.append((field, value))
    return tuple(found)
