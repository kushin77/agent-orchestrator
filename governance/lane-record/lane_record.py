"""The lane record -- ONE schema for the lane brief and the lane result

---knowledge---
module_id: governance.lane-record.lane_record
system: governance
app: lane-record
solution_class: enterprise
patterns: [provoked-negative-control, honesty-tri-state, lane-isolation]
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [SchemaUnavailable, RecordsUnreadable, RegistryUnavailable, Finding, load_schema, schema_for, mirror_problems, runtime_ids, records_dir, read_one, (+7 more)]
invariants: ""
gotchas: ""
related: ["#1268", "#1270"]
do_not_duplicate: null
---knowledge---

(issue #1270, EPIC #1268).

THE RULE
    A lane is given a brief and returns a result, and both are the SAME record.
    The brief carries what the lane owns, the gates it must run, the verbs it may
    not use and the worktree it was assigned; the result carries the commit, the
    files it touched, the tail of every gate, whether it is mergeable, the squash
    exit code and the worktree it actually ran in. Nothing about a lane's
    assignment or its hand-back is prose any more, so the merge loop reads the
    record instead of a chat message, and every runtime (Claude, DeepSeek,
    Copilot, paperclip, hermes) writes one shape.

WHAT MAKES IT MECHANICAL
    * the shape is frozen in ``schema/lane-record.schema.json`` and validated by
      the repository's stdlib-only subset validator
      (``governance/modules/schema.py``), which REFUSES a schema keyword it does
      not implement rather than under-enforcing it -- so a requirement nobody
      measures cannot be added here by writing ``oneOf`` and hoping;
    * the vocabulary is DERIVED, never typed: which runtimes exist comes from the
      registry the repository already owns (``governance/notices``'s
      ``runtime_registry``, the single authority for "what is a runtime"), so a
      runtime registered next week is writable without editing this module;
    * a brief and a result are held to EACH OTHER, not only to the schema -- the
      result's runtime is the brief's, its worktree is the assigned one, its
      files are inside the owned set, every scoped gate carries a tail, and
      ``squash_rc`` is 0 before anything may claim it is mergeable.

REFUSALS, BY NAME (each is provoked by ``scripts/check-lane-record.sh``)
    ``lane-record-unreadable:<path>``        a record file cannot be read
    ``lane-record-not-json:<path>``          a record file is not JSON
    ``lane-record-naming:<path>``            a file in the records tree is not
                                             <issue>/<lane>.{brief,result}.json
    ``lane-record-filename-mismatch:<path>`` the name and the record disagree
    ``lane-record-schema-version:<value>``   not lane-record/v1
    ``lane-record-kind-unknown:<kind>``      a shape the schema does not allow
    ``lane-record-malformed:<json-path>``    a schema violation, by JSON path
    ``lane-record-duplicate:<issue>/<lane>`` two halves of one name, one kind
    ``lane-result-no-brief:<issue>/<lane>``  a result whose brief does not exist
    ``lane-runtime-unregistered:<runtime>``  a runtime the registry does not carry
    ``lane-runtime-mismatch:<issue>/<lane>`` the result's runtime is not the brief's
    ``lane-file-outside-scope:<path>``       a touched file outside the owned set
    ``lane-file-path-invalid:<path>``        an owned/touched path escapes the repo
    ``lane-worktree-mismatch:<worktree>``    the result ran elsewhere than assigned
    ``lane-worktree-shared:<worktree>``      the lane ran in the shared checkout
    ``lane-gate-tail-missing:<gate>``        a scoped gate carries no tail
    ``lane-gate-tail-unscoped:<gate>``       a tail for an unscoped gate
    ``lane-gate-tail-empty:<gate>``          an empty tail is not evidence
    ``lane-report-shape-unallowed:<field>``  a brief demands a field the schema
                                             does not allow a result to carry
    ``lane-report-field-missing:<field>``    a result omits a field its own brief
                                             demanded
    ``lane-squash-not-green:<rc>``           mergeable while the squash was not 0
    ``lane-sha-malformed:<sha>``             a sha that is not 40 lowercase hex
    ``lane-timestamp-malformed:<ts>``        a timestamp with no UTC zone
    ``lane-schema-mirror-drift:<field>``     the schema and this module disagree

ONE SHAPE, MIRRORED
    ``Finding`` is the sibling notice rule's shape (``code:subject``, with the
    prose in ``reason`` so a gate's needle cannot depend on prose). It is
    mirrored here rather than imported -- it is a shape, not an authority -- and
    ``governance/lane-record/tests`` asserts the two are equal, so the mirror
    cannot drift in silence.

EXIT CONTRACT (guardrails/honesty tri-state)
    0 OK / 1 NOT-OK / 2 CANNOT-ASSESS. An unreadable schema, an unreadable
    runtime registry or a records directory that cannot be read is
    CANNOT-ASSESS, never a pass.
"""

from __future__ import annotations

import json
import posixpath
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

_PKG_DIR = Path(__file__).resolve().parent
ROOT = _PKG_DIR.parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from governance.modules import schema as _subset  # noqa: E402
from governance.modules.model import CannotAssess  # noqa: E402

# --------------------------------------------------------------------------- #
# the declarations this module is held to
# --------------------------------------------------------------------------- #
DEFAULT_SCHEMA = _PKG_DIR / "schema" / "lane-record.schema.json"

SCHEMA_VERSION = "lane-record/v1"
KIND_BRIEF = "brief"
KIND_RESULT = "result"
KINDS: Tuple[str, ...] = (KIND_BRIEF, KIND_RESULT)

#: The fields BOTH halves carry. This is what makes the brief and the result one
#: record rather than two shapes that resemble each other.
RECORD_REQUIRED: Tuple[str, ...] = ("schema", "kind", "issue", "lane", "runtime", "ts")
RECORD_OPTIONAL: Tuple[str, ...] = ("refs",)

#: The dispatch half, and the report half.
BRIEF_REQUIRED: Tuple[str, ...] = (
    "owned_files",
    "scoped_gates",
    "forbidden_verbs",
    "assigned_worktree",
    "report_shape",
)
RESULT_REQUIRED: Tuple[str, ...] = (
    "sha",
    "files_touched",
    "gate_tails",
    "mergeable",
    "squash_rc",
    "worktree",
)

HALF_REQUIRED: Dict[str, Tuple[str, ...]] = {
    KIND_BRIEF: BRIEF_REQUIRED,
    KIND_RESULT: RESULT_REQUIRED,
}

#: Where a lane record lives, under the fleet's runtime state. Records are
#: written by the runtimes and are never committed; what ships is the schema,
#: this module, the declaration and the gate.
RECORDS_SUBDIR = "lane-records"

#: Every refusal this module can report, in one place. The declaration
#: (``controls.yaml``) is held to this tuple in BOTH directions by
#: ``controls.problems``: a refusal declared and never reportable, and a
#: refusal reportable and never declared, are each refused by name -- so the
#: vocabulary cannot be minted by editing one side.
REFUSAL_CODES: Tuple[str, ...] = (
    "lane-record-set-unreadable",
    "lane-record-unreadable",
    "lane-record-not-json",
    "lane-record-naming",
    "lane-record-filename-mismatch",
    "lane-record-schema-version",
    "lane-record-kind-unknown",
    "lane-record-malformed",
    "lane-result-no-brief",
    "lane-runtime-unregistered",
    "lane-runtime-mismatch",
    "lane-file-outside-scope",
    "lane-file-path-invalid",
    "lane-worktree-mismatch",
    "lane-worktree-shared",
    "lane-gate-tail-missing",
    "lane-gate-tail-unscoped",
    "lane-gate-tail-empty",
    "lane-report-shape-unallowed",
    "lane-report-field-missing",
    "lane-squash-not-green",
    "lane-sha-malformed",
    "lane-timestamp-malformed",
    "lane-schema-mirror-drift",
)

_TS_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")

#: The only keywords the schema DOCUMENT may carry outside ``$defs``. Anything
#: else is a validating keyword for a document nobody validates against.
_TOP_LEVEL_ALLOWED = frozenset(
    {"$schema", "$id", "$comment", "title", "description", "$defs"}
)


class SchemaUnavailable(CannotAssess):
    """The frozen schema is missing, malformed, or uses a keyword nobody measures."""


class RecordsUnreadable(CannotAssess):
    """The records tree named cannot be read, so nothing can be said about it."""


class RegistryUnavailable(CannotAssess):
    """The derived runtime set cannot be built -- the authority is unreadable."""


@dataclass(frozen=True)
class Finding:
    """One refusal, by name: ``code:subject``, with the prose kept beside it.

    Mirrored from ``governance/notices/finding.py`` (the sibling rule's shape);
    the tests assert the two are equal so the mirror cannot drift unnoticed.
    """

    code: str
    subject: str
    reason: str = ""

    def __str__(self) -> str:
        return "%s:%s" % (self.code, self.subject)

    def line(self) -> str:
        """The report line: the refusal by name, then why it is refused."""
        return "%s -- %s" % (self, self.reason) if self.reason else str(self)


# --------------------------------------------------------------------------- #
# the frozen schema, and the mirror that keeps it honest
# --------------------------------------------------------------------------- #
def load_schema(path: Path | str | None = None) -> Mapping[str, Any]:
    """Read, parse and CHECK the schema. Never cached: a stale copy of a schema
    is how a gate certifies a shape that is no longer on disk.

    The check is the subset validator's, so a schema keyword this repository
    cannot enforce is refused here rather than silently ignored.
    """
    target = Path(path) if path else DEFAULT_SCHEMA
    try:
        document = _subset.load(target)
    except _subset.SchemaUnavailable as exc:
        raise SchemaUnavailable(str(exc)) from exc
    if not isinstance(document.get("$defs"), Mapping):
        raise SchemaUnavailable(
            "the frozen schema %s declares no $defs, so there is no half to validate"
            % target
        )
    return document


def _schema_required(schema: Mapping[str, Any], kind: str) -> Tuple[str, ...]:
    half = (schema.get("$defs") or {}).get(kind)
    if not isinstance(half, Mapping):
        raise SchemaUnavailable("the frozen schema declares no $defs/%s" % kind)
    return tuple(half.get("required") or ())


def _schema_properties(schema: Mapping[str, Any], kind: str) -> Tuple[str, ...]:
    half = (schema.get("$defs") or {}).get(kind)
    if not isinstance(half, Mapping):
        raise SchemaUnavailable("the frozen schema declares no $defs/%s" % kind)
    return tuple((half.get("properties") or {}).keys())


def schema_for(document: Mapping[str, Any], schema: Mapping[str, Any]) -> Mapping[str, Any]:
    """The half-schema a record is validated against, selected by its own ``kind``."""
    kind = document.get("kind")
    if kind not in KINDS:
        raise SchemaUnavailable(
            "the frozen schema declares no $defs/%s" % (kind,)
        )
    half = (schema.get("$defs") or {}).get(kind)
    if not isinstance(half, Mapping):  # pragma: no cover - guarded by KINDS above
        raise SchemaUnavailable("the frozen schema declares no $defs/%s" % kind)
    return half


def mirror_problems(schema: Mapping[str, Any]) -> Tuple[Finding, ...]:
    """Every place the frozen schema and this module have stopped agreeing.

    The schema lists the shared skeleton three times (once for the record, once
    for each half) because the subset validator implements no ``allOf`` and no
    ``unevaluatedProperties``. That duplication is only safe while it is
    CHECKED, so this is the check: the halves must carry exactly the shared
    fields plus their own, the version and the kind set must match the module's
    constants, and a field belonging to one half must not be declared on the
    other.

    It also refuses a validating keyword at the top level. A document is
    validated against ``$defs/<kind>`` and nothing else, so a top-level
    ``required`` or ``$ref`` would be a shape that looks authoritative and is
    never run -- exactly the formality this EPIC exists to refuse.
    """
    found: List[Finding] = []
    for keyword in schema:
        if keyword not in _TOP_LEVEL_ALLOWED:
            found.append(
                Finding(
                    "lane-schema-mirror-drift",
                    "(document).%s" % keyword,
                    "the top level carries metadata and $defs only, so there is "
                    "exactly one validation path; %r here is a shape that looks "
                    "authoritative and is never run" % keyword,
                )
            )
    record_props = _schema_properties(schema, "record")
    shared = set(RECORD_REQUIRED) | set(RECORD_OPTIONAL)

    declared_version = (
        ((schema.get("$defs") or {}).get("record") or {}).get("properties") or {}
    ).get("schema", {}).get("const")
    if declared_version != SCHEMA_VERSION:
        found.append(
            Finding(
                "lane-schema-mirror-drift",
                "$defs.record.properties.schema.const",
                "the schema declares version %r, this module declares %r"
                % (declared_version, SCHEMA_VERSION),
            )
        )

    declared_kinds = tuple(
        (((schema.get("$defs") or {}).get("record") or {}).get("properties") or {})
        .get("kind", {})
        .get("enum", ())
    )
    if set(declared_kinds) != set(KINDS):
        found.append(
            Finding(
                "lane-schema-mirror-drift",
                "$defs.record.properties.kind.enum",
                "the schema admits %r, this module admits %r"
                % (sorted(declared_kinds), sorted(KINDS)),
            )
        )

    if set(record_props) != shared:
        found.append(
            Finding(
                "lane-schema-mirror-drift",
                "$defs.record.properties",
                "the shared skeleton is %r in the schema and %r here"
                % (sorted(record_props), sorted(shared)),
            )
        )

    for kind in KINDS:
        wanted = set(RECORD_REQUIRED) | set(HALF_REQUIRED[kind])
        declared = set(_schema_required(schema, kind))
        if declared != wanted:
            found.append(
                Finding(
                    "lane-schema-mirror-drift",
                    "$defs.%s.required" % kind,
                    "the schema requires %r, this module requires %r"
                    % (sorted(declared), sorted(wanted)),
                )
            )
        props = set(_schema_properties(schema, kind))
        if props != wanted | set(RECORD_OPTIONAL):
            found.append(
                Finding(
                    "lane-schema-mirror-drift",
                    "$defs.%s.properties" % kind,
                    "the schema declares %r, this module declares %r"
                    % (sorted(props), sorted(wanted | set(RECORD_OPTIONAL))),
                )
            )
        for other in KINDS:
            if other == kind:
                continue
            for field in HALF_REQUIRED[other]:
                if field in props:
                    found.append(
                        Finding(
                            "lane-schema-mirror-drift",
                            "$defs.%s.properties/%s" % (kind, field),
                            "the %s half declares the %s field %r, so the two "
                            "halves are no longer distinguishable" % (kind, other, field),
                        )
                    )
    return tuple(found)


# --------------------------------------------------------------------------- #
# the derived runtime set
# --------------------------------------------------------------------------- #
def runtime_ids(root: Path | str | None = None) -> Tuple[str, ...]:
    """The registered runtime set, DERIVED -- never a list typed here.

    A runtime is an identity bundled by a live AgentPack that the gateway
    catalog carries a transport for. That derivation is the sibling notice
    rule's (``governance/notices/runtime_registry``) and is imported rather than
    re-implemented: a second derivation is exactly the second vocabulary this
    EPIC exists to refuse.
    """
    here = Path(root) if root else ROOT
    try:
        from governance.notices import runtime_registry as registry
    except Exception as exc:  # pragma: no cover - a missing peer package is loud
        raise RegistryUnavailable(
            "the runtime registry (governance/notices/runtime_registry.py) is not "
            "importable: %s" % exc
        ) from exc
    try:
        runtimes = registry.registered_runtimes(here)
    except registry.RegistryUnavailable as exc:
        raise RegistryUnavailable(str(exc)) from exc
    except Exception as exc:  # PyYAML absent, a pack unreadable, and so on
        raise RegistryUnavailable("the runtime registry could not be built: %s" % exc) from exc
    return tuple(item.id for item in runtimes)


# --------------------------------------------------------------------------- #
# reading the records tree
# --------------------------------------------------------------------------- #
def records_dir(fleet: Path | str | None = None, root: Path | str | None = None) -> Path:
    """Where lane records live: ``<fleet>/lane-records`` (``$AO_FLEET_DIR`` honoured
    by the CLI, else ``<root>/.fleet``)."""
    if fleet:
        return Path(fleet) / RECORDS_SUBDIR
    return (Path(root) if root else ROOT) / ".fleet" / RECORDS_SUBDIR


def _read_json(path: Path) -> Mapping[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RecordsUnreadable("lane-record-unreadable:%s" % path) from exc
    try:
        document = json.loads(text)
    except ValueError as exc:
        raise _NotJson(path, str(exc)) from exc
    if not isinstance(document, Mapping):
        raise _NotJson(path, "the document is not an object")
    return document


class _NotJson(CannotAssess):
    """A record file that is not a JSON object. Reported as a refusal, not as an
    inability to assess: the file exists, and what it holds is wrong."""

    def __init__(self, path: Path, detail: str) -> None:
        super().__init__("lane-record-not-json:%s" % path)
        self.path = path
        self.detail = detail


def read_one(
    path: Path | str, subject: str | None = None
) -> Tuple[Mapping[str, Any] | None, Tuple[Finding, ...]]:
    """Read exactly one record file, turning a bad file into a NAMED refusal.

    A file that exists and holds the wrong thing is NOT-OK, not CANNOT-ASSESS:
    the question was answerable and the answer is no.
    """
    name = subject if subject is not None else str(path)
    try:
        return _read_json(Path(path)), ()
    except RecordsUnreadable:
        return None, (
            Finding("lane-record-unreadable", name, "the record file cannot be read"),
        )
    except _NotJson as exc:
        return None, (Finding("lane-record-not-json", name, exc.detail),)


@dataclass(frozen=True)
class Located:
    """One record file: where it is, what it claims to be, and the document."""

    path: Path
    relpath: str
    issue: int
    lane: str
    kind: str
    document: Mapping[str, Any]


def locate(path: Path, records: Path) -> Tuple[Located | None, Tuple[Finding, ...]]:
    """Read one record file and check its NAME against its own content.

    The name is not decoration: ``<records>/<issue>/<lane>.<kind>.json`` is how
    the tree is walked, so a file whose name and record disagree would be paired
    with the wrong partner -- a mis-pairing that reads as a satisfied rule.
    """
    rel = path.relative_to(records).as_posix()
    parts = Path(rel).parts
    if len(parts) != 2 or not parts[0].isdigit():
        return None, (
            Finding(
                "lane-record-naming",
                rel,
                "a lane record is <issue>/<lane>.brief.json or "
                "<issue>/<lane>.result.json, so the tree can be walked",
            ),
        )
    name = parts[1]
    kind = ""
    for candidate in KINDS:
        if name.endswith(".%s.json" % candidate):
            kind = candidate
    if not kind:
        return None, (
            Finding(
                "lane-record-naming",
                rel,
                "the file name does not end in .brief.json or .result.json",
            ),
        )
    lane = name[: -(len(kind) + 6)]
    if not lane:
        return None, (Finding("lane-record-naming", rel, "the file name carries no lane"),)

    document, read_problems = read_one(path, rel)
    if document is None:
        return None, read_problems

    found: List[Finding] = []
    if document.get("kind") != kind:
        found.append(
            Finding(
                "lane-record-filename-mismatch",
                rel,
                "the file is named %s but the record says kind=%r"
                % (kind, document.get("kind")),
            )
        )
    if str(document.get("lane")) != lane:
        found.append(
            Finding(
                "lane-record-filename-mismatch",
                rel,
                "the file is named %r but the record says lane=%r"
                % (lane, document.get("lane")),
            )
        )
    if document.get("issue") != int(parts[0]):
        found.append(
            Finding(
                "lane-record-filename-mismatch",
                rel,
                "the file is filed under %s but the record says issue=%r"
                % (parts[0], document.get("issue")),
            )
        )
    return Located(path, rel, int(parts[0]), lane, kind, document), tuple(found)


def load_records(records: Path | str) -> Tuple[Tuple[Located, ...], Tuple[Finding, ...]]:
    """Every record under a records directory, with the refusals found while reading.

    An absent or unreadable records directory is CANNOT-ASSESS, never an empty
    set: "there are no records" and "the records could not be read" must never
    read the same, because the second one is a pass that means nothing.
    """
    directory = Path(records)
    if not directory.is_dir():
        raise RecordsUnreadable(
            "lane-record-set-unreadable:%s -- the records directory is not readable, "
            "so nothing can be said about any lane" % directory
        )
    located: List[Located] = []
    found: List[Finding] = []
    for path in sorted(directory.rglob("*.json")):
        item, problems = locate(path, directory)
        found.extend(problems)
        if item is not None:
            located.append(item)
    return tuple(located), tuple(found)


# --------------------------------------------------------------------------- #
# one record
# --------------------------------------------------------------------------- #
def _path_invalid(raw: str) -> bool:
    return raw.startswith("/") or bool(posixpath.normpath(raw).startswith(".."))


def validate_record(
    document: Mapping[str, Any],
    schema: Mapping[str, Any],
    runtimes: Sequence[str],
    root: Path | str | None = None,
) -> Tuple[Finding, ...]:
    """One record, against the schema and against the vocabulary.

    The shape half and the vocabulary half are deliberately separate: a document
    whose ``runtime`` is a well-formed string the registry does not carry is a
    DIFFERENT refusal from a document whose ``runtime`` is a number, and naming
    only one of them is how a runtime gets in unregistered.
    """
    found: List[Finding] = []
    base = Path(root) if root else ROOT

    kind = document.get("kind")
    if kind not in KINDS:
        return (
            Finding(
                "lane-record-kind-unknown",
                repr(kind),
                "this schema admits %s, so a runtime writing any other shape is "
                "refused rather than quietly admitted" % " and ".join(KINDS),
            ),
        )

    version = document.get("schema")
    if version != SCHEMA_VERSION:
        return (
            Finding(
                "lane-record-schema-version",
                repr(version),
                "the frozen schema is %r; a record at another version cannot be "
                "assessed against it" % SCHEMA_VERSION,
            ),
        )

    for problem in _subset.problems(document, schema_for(document, schema)):
        where, _, reason = problem.partition(": ")
        # A fault about a FIELD is named by that field, never by the document: a
        # reader told "lane-record-malformed:(document)" has to go and read the
        # schema to find out what the lane got wrong. Both fault shapes the
        # validator reports for a property are mapped to the field's own path.
        field = re.match(r"the required property '([^']+)' is missing", reason)
        if not field:
            field = re.match(r"the property '([^']+)' is not declared", reason)
        if field:
            where = "/%s" % field.group(1)
        found.append(Finding("lane-record-malformed", where, reason or problem))
    if found:
        return tuple(found)

    runtime = str(document["runtime"])
    if runtime not in runtimes:
        found.append(
            Finding(
                "lane-runtime-unregistered",
                runtime,
                "the derived registry carries %s; an unregistered runtime owes no "
                "record and its record proves nothing" % ", ".join(sorted(runtimes)),
            )
        )

    stamp = str(document["ts"])
    if not _TS_RE.match(stamp):
        found.append(
            Finding(
                "lane-timestamp-malformed",
                stamp,
                "a record is stamped in UTC (YYYY-MM-DDTHH:MM:SSZ); a stamp with no "
                "zone cannot be re-checked",
            )
        )

    if kind == KIND_BRIEF:
        for raw in document["owned_files"]:
            if _path_invalid(str(raw)):
                found.append(
                    Finding(
                        "lane-file-path-invalid",
                        str(raw),
                        "an owned file is repo-relative",
                    )
                )
        assigned = str(document["assigned_worktree"])
        if assigned and posixpath.normpath(assigned) == posixpath.normpath(str(base)):
            found.append(
                Finding(
                    "lane-worktree-shared",
                    assigned,
                    "the shared checkout is not a lane's worktree (AGENTS.md rule 15), "
                    "so a brief assigning one is refused before the lane runs",
                )
            )
        allowed = set(_schema_properties(schema, KIND_RESULT))
        for field in document["report_shape"]["fields"]:
            if field not in allowed:
                found.append(
                    Finding(
                        "lane-report-shape-unallowed",
                        str(field),
                        "a brief demands its result in the SCHEMA's vocabulary; %r is "
                        "not a declared property of $defs.result" % (field,),
                    )
                )
    else:
        sha = str(document["sha"])
        if not _SHA_RE.match(sha):
            found.append(
                Finding(
                    "lane-sha-malformed",
                    sha,
                    "a result names the commit it produced, 40 lowercase hex",
                )
            )
        if document["mergeable"] is True and int(document["squash_rc"]) != 0:
            found.append(
                Finding(
                    "lane-squash-not-green",
                    str(document["squash_rc"]),
                    "a lane may claim it is mergeable only on green evidence; the "
                    "squash exited %s" % document["squash_rc"],
                )
            )
        for raw in document["files_touched"]:
            if _path_invalid(str(raw)):
                found.append(
                    Finding(
                        "lane-file-path-invalid",
                        str(raw),
                        "a touched file is repo-relative",
                    )
                )
        for gate, tail in document["gate_tails"].items():
            if not str(tail).strip():
                found.append(
                    Finding(
                        "lane-gate-tail-empty",
                        str(gate),
                        "a tail with nothing in it is not evidence of a gate run",
                    )
                )
    return tuple(found)


# --------------------------------------------------------------------------- #
# a brief and its result
# --------------------------------------------------------------------------- #
def pair_problems(
    brief: Mapping[str, Any],
    result: Mapping[str, Any],
    root: Path | str | None = None,
) -> Tuple[Finding, ...]:
    """The two halves held to EACH OTHER -- the half of the rule a schema cannot state."""
    found: List[Finding] = []
    base = Path(root) if root else ROOT
    subject = "%s/%s" % (brief.get("issue"), brief.get("lane"))

    if str(result.get("runtime")) != str(brief.get("runtime")):
        found.append(
            Finding(
                "lane-runtime-mismatch",
                subject,
                "the brief names runtime %r and the result names %r, so the record "
                "returned is not the one dispatched"
                % (brief.get("runtime"), result.get("runtime")),
            )
        )

    assigned = str(brief.get("assigned_worktree") or "")
    ran_in = str(result.get("worktree") or "")
    if ran_in and posixpath.normpath(ran_in) == posixpath.normpath(str(base)):
        found.append(
            Finding(
                "lane-worktree-shared",
                ran_in,
                "the lane ran in the shared checkout, not in the worktree it was "
                "assigned (AGENTS.md rule 15)",
            )
        )
    elif posixpath.normpath(ran_in) != posixpath.normpath(assigned):
        found.append(
            Finding(
                "lane-worktree-mismatch",
                ran_in,
                "the brief assigned %r and the result ran in %r" % (assigned, ran_in),
            )
        )

    owned = {str(item) for item in brief.get("owned_files") or ()}
    for touched in result.get("files_touched") or ():
        if str(touched) not in owned:
            found.append(
                Finding(
                    "lane-file-outside-scope",
                    str(touched),
                    "the brief owns %s; a lane is not free to widen its own scope"
                    % (", ".join(sorted(owned)) or "nothing"),
                )
            )

    scoped = [str(gate) for gate in brief.get("scoped_gates") or ()]
    tails = result.get("gate_tails") or {}
    for gate in scoped:
        if gate not in tails:
            found.append(
                Finding(
                    "lane-gate-tail-missing",
                    gate,
                    "every gate the brief scoped carries its tail, so the verdict "
                    "travels with the record",
                )
            )
    for gate in tails:
        if str(gate) not in scoped:
            found.append(
                Finding(
                    "lane-gate-tail-unscoped",
                    str(gate),
                    "the brief scoped %s, so a tail for an unscoped gate is a gate "
                    "nobody asked this lane to run" % (", ".join(scoped) or "nothing"),
                )
            )

    for field in (brief.get("report_shape") or {}).get("fields") or ():
        if str(field) not in result:
            found.append(
                Finding(
                    "lane-report-field-missing",
                    str(field),
                    "the brief demanded this field of the result, and a result that "
                    "omits what its own brief asked for is not a result",
                )
            )
    return tuple(found)


# --------------------------------------------------------------------------- #
# the whole records tree
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Report:
    """What the rule found, and the counts it found it over."""

    findings: Tuple[Finding, ...]
    counts: Mapping[str, int]

    @property
    def rc(self) -> int:
        return 1 if self.findings else 0

    def summary(self) -> str:
        return "lane-record: records=%d briefs=%d results=%d pairs=%d runtimes=%d" % (
            self.counts["records"],
            self.counts["briefs"],
            self.counts["results"],
            self.counts["pairs"],
            self.counts["runtimes"],
        )


def evaluate(
    records: Path | str,
    schema: Mapping[str, Any],
    runtimes: Sequence[str],
    root: Path | str | None = None,
) -> Report:
    """Every record in a tree: shape, vocabulary, then the pair relation.

    A quiet successful read is still reported with its counts, so an empty
    records tree cannot look like a tree that was never read.
    """
    located, read_problems = load_records(records)
    found: List[Finding] = list(mirror_problems(schema))
    found.extend(read_problems)

    briefs: Dict[Tuple[int, str], Mapping[str, Any]] = {}
    results: Dict[Tuple[int, str], Mapping[str, Any]] = {}
    for item in located:
        found.extend(validate_record(item.document, schema, runtimes, root))
        bucket = briefs if item.kind == KIND_BRIEF else results
        bucket[(item.issue, item.lane)] = item.document

    for key in sorted(results):
        if key not in briefs:
            found.append(
                Finding(
                    "lane-result-no-brief",
                    "%s/%s" % key,
                    "a lane reports a result for a brief that does not exist; the "
                    "result cannot be inside a scope nobody declared",
                )
            )
    for key in sorted(set(briefs) & set(results)):
        found.extend(pair_problems(briefs[key], results[key], root))

    counts = {
        "records": len(located),
        "briefs": len(briefs),
        "results": len(results),
        "pairs": len(set(briefs) & set(results)),
        "runtimes": len(runtimes),
    }
    # One refusal per distinct name: a malformed record reported once per rule
    # reads as many records and buries the one that has to be fixed.
    unique: Dict[str, Finding] = {}
    for finding in found:
        unique.setdefault(str(finding), finding)
    return Report(tuple(unique[key] for key in sorted(unique)), counts)
