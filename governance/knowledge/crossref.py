"""Cross-reference spine: typed relationship edges between knowledge nodes

---knowledge---
module_id: governance.knowledge.crossref
system: governance
app: knowledge
solution_class: enterprise
patterns: [deterministic]
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [board_issue_numbers, ledger_records, adr_ids, gr_id_present, load_catalog, resolve_target, classify_marker_target, parse_markers, marker_findings, iter_tracked_markdown, (+1 more)]
invariants: ""
gotchas: ""
related: ["#138", "#384", "#402"]
do_not_duplicate: null
---knowledge---

(EPIC #138, issue #384).

The index catalogue carries *items* (nodes); this module carries the *edges*.
It is a deterministic builder: it walks the sources of truth — ADR front-matter,
the committed board snapshot, the ticket graph (the lessons register emits typed
ticket edges, issue #402) and ``cmr-refs:`` markers in tracked markdown — and
emits a sorted, deduplicated list of :class:`~model.Relationship` edges. Two runs
over one revision produce identical bytes, because the edges carry no timestamps
and are ordered by a stable key.

The same module owns the *resolution* rules the gate uses: a target resolves
when it is a file that exists, an entity id present in the catalogue / board
snapshot / ledger, or a golden-rule id declared in the golden-rules documents.
A target that cannot be validated is a failure, never a skip.
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from model import (
    RELATIONSHIP_BLOCKED_BY,
    RELATIONSHIP_CAUSED_BY,
    RELATIONSHIP_MITIGATES,
    RELATIONSHIP_ORIGIN,
    RELATIONSHIP_PARENT_OF,
    RELATIONSHIP_REFS,
    RELATIONSHIP_SUPERSEDES,
    Relationship,
)

ADR_GLOB = "docs/decision-records/ADR-*.md"
SNAPSHOT_RELPATH = ".board/snapshot.json"
LEDGER_RELPATH = "governance/lessons/ledger.jsonl"
CATALOG_RELPATH = "governance/knowledge/catalog.json"
GOLDEN_RULES_PATHS = ("GOLDEN-RULES.md", "docs/GOLDEN-RULES.md")

# A marker line: optional indentation, then ``cmr-refs:`` and the target list.
RE_CMREFS_LINE = re.compile(r"^[ \t]*cmr-refs:[ \t]*(.*?)[ \t]*$")

RE_ADR_ID = re.compile(r"^ADR-\d+$")
RE_GR_ID = re.compile(r"^GR-\d+$")
RE_ISSUE_REF = re.compile(r"^#(\d+)$")
RE_ISSUE_NODE = re.compile(r"^issue-\d+$")
RE_LEDGER_ID = re.compile(r"^(RCA|INC|CA|LESSON|SUGGEST)-\d+$")
RE_PR_NODE = re.compile(r"^pr-\d+$")
RE_COMMIT_NODE = re.compile(r"^commit-[0-9a-fA-F]{7,40}$")
RE_EVENT_NODE = re.compile(r"^event-.+$")

# Marker target forms (the closed marker vocabulary). ``CA-`` stays out: a
# corrective action is a sub-part of an RCA, not an addressable node. ``SUGGEST-``
# is in (issue #402): an open suggestion is a ticket of kind ``suggestion``, so
# it is addressable like a closed ``LESSON-``.
RE_MARKER_RCA = re.compile(r"^RCA-\d+$")
RE_MARKER_LESSON = re.compile(r"^LESSON-\d+$")
RE_MARKER_SUGGEST = re.compile(r"^SUGGEST-\d+$")
RE_MARKER_INC = re.compile(r"^INC-\d+$")

# -- ticket-graph edge sources (issue #402) -----------------------------------
#
# The lessons register is an ordinary edge source: it declares typed ticket
# edges (``governance/lessons/edges.py``) and this module consumes them exactly
# like the ADR front-matter, the board snapshot and the ``cmr-refs:`` markers.
# The list is data, not a branch — a new source is one entry, not new code here.
TICKET_EDGE_SOURCES = ("governance/lessons/edges.py",)

#: Ticket edge type -> spine relationship type. The spine's vocabulary is closed
#: at nine types (:data:`model.RELATIONSHIP_TYPES`); a ticket edge type the spine
#: does not carry (``remediation-of``) stays a ticket-graph edge and is not
#: emitted into the spine, so no ungoverned type is ever invented.
TICKET_TO_SPINE: Dict[str, str] = {
    "caused-by": RELATIONSHIP_CAUSED_BY,
    "origin": RELATIONSHIP_ORIGIN,
    "mitigates": RELATIONSHIP_MITIGATES,
}

_EXCLUDED_PREFIXES = ("vendor/", ".research/", ".git/")


# -- source readers ----------------------------------------------------------


def _read_json(path: Path) -> Optional[Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _read_ledger_lines(path: Path) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return records
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def board_issue_numbers(root: Path) -> Set[int]:
    payload = _read_json(root / SNAPSHOT_RELPATH)
    issues = payload.get("issues") if isinstance(payload, dict) else None
    if not isinstance(issues, list):
        return set()
    numbers: Set[int] = set()
    for issue in issues:
        if isinstance(issue, dict) and isinstance(issue.get("number"), int):
            numbers.add(issue["number"])
    return numbers


def ledger_records(root: Path) -> Dict[str, Dict[str, Any]]:
    by_id: Dict[str, Dict[str, Any]] = {}
    for record in _read_ledger_lines(root / LEDGER_RELPATH):
        record_id = record.get("id")
        if isinstance(record_id, str) and record_id:
            by_id[record_id] = record
    return by_id


def adr_ids(root: Path) -> Set[str]:
    """ADR ids, derived from filenames (reserved ADRs carry no front-matter)."""
    ids: Set[str] = set()
    for path in sorted(Path(root).glob(ADR_GLOB)):
        if not path.is_file():
            continue
        match = re.match(r"^(ADR-\d+)", path.name)
        if match:
            ids.add(match.group(1))
    return ids


def gr_id_present(root: Path, gr_id: str) -> bool:
    """True when ``gr_id`` is declared in the golden-rules documents.

    The repo names its rules ``AO-GR-<n>``; the hub names them ``GR-<n>``. Both
    are accepted so a ``GR-<n>`` target resolves against either spelling without
    being able to match a longer rule number (``GR-1`` must not match ``GR-11``).
    """
    number = gr_id[len("GR-"):]
    for rel in GOLDEN_RULES_PATHS:
        path = root / rel
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for candidate in ("GR-" + number, "AO-" + "GR-" + number):
            pattern = r"(?<![A-Za-z0-9])" + re.escape(candidate) + r"(?![0-9])"
            if re.search(pattern, text):
                return True
    return False


def _origin_present(root: Path, kind: str, ref: str) -> bool:
    for record in ledger_records(root).values():
        origin = record.get("origin")
        if (
            isinstance(origin, dict)
            and origin.get("kind") == kind
            and str(origin.get("ref", "")) == ref
        ):
            return True
    return False


def load_catalog(root: Path) -> Optional[Dict[str, Any]]:
    payload = _read_json(root / CATALOG_RELPATH)
    return payload if isinstance(payload, dict) else None


# -- resolution --------------------------------------------------------------


def resolve_target(root: Path, target: str) -> Tuple[bool, str]:
    """Resolve a node id / target to something real: ``(ok, description)``.

    A target is a file (in backticks, or a bare repo-relative path used as an
    edge endpoint), an entity id present in the board snapshot / ledger, an ADR
    id, a golden-rule id, or a normalized origin reference.
    """
    t = target.strip()
    if t.startswith("`") and t.endswith("`") and len(t) >= 2:
        rel = t[1:-1].strip()
        return (bool(rel) and (root / rel).is_file(), "file %s" % rel)
    if RE_ISSUE_REF.match(t):
        return (int(t[1:]) in board_issue_numbers(root), "issue %s" % t)
    if RE_ISSUE_NODE.match(t):
        return (int(t.split("-", 1)[1]) in board_issue_numbers(root), "issue %s" % t)
    if RE_ADR_ID.match(t):
        return (t in adr_ids(root), "ADR %s" % t)
    if RE_GR_ID.match(t):
        return (gr_id_present(root, t), "golden rule %s" % t)
    if RE_LEDGER_ID.match(t):
        return (t in ledger_records(root), "ledger record %s" % t)
    if RE_PR_NODE.match(t):
        return (
            _origin_present(root, "pr", "#" + t[3:]),
            "pull request %s" % t[3:],
        )
    if RE_COMMIT_NODE.match(t):
        return (
            _origin_present(root, "commit", t[len("commit-"):]),
            "commit %s" % t[len("commit-"):],
        )
    if RE_EVENT_NODE.match(t):
        return (
            _origin_present(root, "event", t[len("event-"):]),
            "event %s" % t[len("event-"):],
        )
    if "/" in t or t.endswith((".md", ".yaml", ".yml", ".json", ".py", ".sh", ".txt")):
        return ((root / t).is_file(), "file %s" % t)
    return (False, "unrecognised target %s" % t)


# -- cmr-refs: markers --------------------------------------------------------


def classify_marker_target(token: str) -> Optional[str]:
    """``None`` when the token is a well-formed marker target, else the reason.

    The marker vocabulary is closed: ``ADR-<n>``, ``GR-<n>``, ``#<n>``,
    ``RCA-<n>``, ``LESSON-<n>``, ``SUGGEST-<n>``, ``INC-<n>``, or a
    repo-relative path in backticks. Anything else — including ``CA-<n>`` — is
    malformed, because a target form that was never declared cannot be silently
    accepted. An open ``SUGGEST-<n>`` is a ticket of kind ``suggestion``
    (ADR-0014), so it is addressable exactly like a closed ``LESSON-<n>``
    (issue #402).
    """
    if not token:
        return "empty target"
    if token.startswith("`") and token.endswith("`") and len(token) >= 2:
        return None if token[1:-1].strip() else "empty path target"
    if RE_ADR_ID.match(token):
        return None
    if RE_GR_ID.match(token):
        return None
    if RE_ISSUE_REF.match(token):
        return None
    if RE_MARKER_RCA.match(token):
        return None
    if RE_MARKER_LESSON.match(token):
        return None
    if RE_MARKER_SUGGEST.match(token):
        return None
    if RE_MARKER_INC.match(token):
        return None
    return "target %r is not a valid form" % token


def parse_markers(text: str) -> List[Tuple[int, List[str]]]:
    """``(line number, targets)`` for every ``cmr-refs:`` line.

    A bare ``cmr-refs:`` with no targets is reported as an empty list so the
    gate can name it a malformed marker.
    """
    out: List[Tuple[int, List[str]]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        match = RE_CMREFS_LINE.match(line)
        if not match:
            continue
        content = match.group(1).strip()
        if not content:
            out.append((lineno, []))
            continue
        out.append((lineno, [token.strip() for token in content.split(",")]))
    return out


def marker_findings(root: Path, relpath: str, text: str) -> List[str]:
    """One finding per malformed or unresolvable marker target in ``text``."""
    findings: List[str] = []
    for lineno, targets in parse_markers(text):
        if not targets:
            findings.append(
                "%s:%d: cmr-refs: marker names no targets" % (relpath, lineno)
            )
            continue
        for token in targets:
            reason = classify_marker_target(token)
            if reason:
                findings.append(
                    "%s:%d: malformed cmr-refs target %r (%s)"
                    % (relpath, lineno, token, reason)
                )
                continue
            ok, what = resolve_target(root, token)
            if not ok:
                findings.append(
                    "%s:%d: cmr-refs target %r does not resolve (%s)"
                    % (relpath, lineno, token, what)
                )
    return findings


def _excluded(relpath: str) -> bool:
    return any(relpath.startswith(prefix) for prefix in _EXCLUDED_PREFIXES)


def iter_tracked_markdown(root: Path) -> List[str]:
    """Repo-relative paths of tracked markdown, outside vendor/.research/.git."""
    root = Path(root).resolve()
    rels: List[str] = []
    try:
        out = subprocess.run(
            [
                "git", "-C", str(root), "ls-files", "-z",
                "--cached", "--others", "--exclude-standard", "--", "*.md",
            ],
            capture_output=True,
            check=False,
        )
        if out.returncode == 0:
            for raw in out.stdout.split(b"\0"):
                if not raw:
                    continue
                rel = raw.decode("utf-8", "replace")
                if rel and not _excluded(rel):
                    rels.append(rel)
            return sorted(set(rels))
    except OSError:
        pass
    # Fallback for non-git trees (test fixtures): walk and exclude manually.
    for path in sorted(root.rglob("*.md")):
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            continue
        if not _excluded(rel):
            rels.append(rel)
    return sorted(set(rels))


# -- edge construction --------------------------------------------------------


def _frontmatter(text: str) -> Dict[str, str]:
    """Parse the leading ``---``-fenced YAML block into a flat field map."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    fields: Dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        match = re.match(r"^([A-Za-z_][A-Za-z0-9_-]*):\s*(.*?)\s*$", line)
        if match:
            fields[match.group(1)] = match.group(2)
    return fields


def _frontmatter_list(fields: Dict[str, str], key: str) -> List[str]:
    raw = fields.get(key, "").strip()
    if not raw or raw == "[]":
        return []
    if raw.startswith("["):
        raw = raw[1:]
    if raw.endswith("]"):
        raw = raw[:-1]
    return [piece.strip() for piece in raw.split(",") if piece.strip()]


def _adr_id_from_filename(rel: str) -> str:
    match = re.search(r"(ADR-\d+)", Path(rel).name)
    return match.group(1) if match else ""


def _adr_edges(root: Path) -> List[Tuple[str, str, str, str]]:
    edges: List[Tuple[str, str, str, str]] = []
    for path in sorted(Path(root).glob(ADR_GLOB)):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        fields = _frontmatter(text)
        adr_id = fields.get("id", "").strip() or _adr_id_from_filename(rel)
        if not RE_ADR_ID.match(adr_id):
            continue
        for superseded in _frontmatter_list(fields, "supersedes"):
            if RE_ADR_ID.match(superseded):
                edges.append((adr_id, RELATIONSHIP_SUPERSEDES, superseded, rel))
    return edges


def _board_edges(root: Path) -> List[Tuple[str, str, str, str]]:
    payload = _read_json(root / SNAPSHOT_RELPATH)
    issues = payload.get("issues") if isinstance(payload, dict) else None
    if not isinstance(issues, list):
        return []
    edges: List[Tuple[str, str, str, str]] = []
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        number = issue.get("number")
        if not isinstance(number, int):
            continue
        parent = issue.get("parent")
        if isinstance(parent, int):
            edges.append(
                ("issue-%d" % parent, RELATIONSHIP_PARENT_OF, "issue-%d" % number,
                 SNAPSHOT_RELPATH)
            )
        for blocker in issue.get("blocked_by") or []:
            if isinstance(blocker, int):
                edges.append(
                    ("issue-%d" % number, RELATIONSHIP_BLOCKED_BY, "issue-%d" % blocker,
                     SNAPSHOT_RELPATH)
                )
    return edges


def _load_edge_source(path: Path):
    """Load a declared ticket-graph edge source module by file path.

    The source is loaded under a private name rather than as a bare module, so
    it cannot collide with a same-named module the host process already holds.
    An absent source is skipped (it is legitimately optional, like an absent
    ledger); a source that exists but cannot be loaded is a hard error, never a
    silent skip — an edge source that vanishes would be a false green.
    """
    spec = importlib.util.spec_from_file_location(
        "ao_ticket_source_" + path.stem, path
    )
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise RuntimeError("cannot load ticket edge source %s" % path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _ticket_edges(root: Path) -> List[Tuple[str, str, str, str]]:
    """Typed edges declared by the ticket-graph sources, mapped to the spine.

    This replaces the lessons-specific branch this module used to carry: the
    knowledge of what a lessons reference *means* now lives in the lessons lane
    (``governance/lessons/edges.py``), and the spine only maps the ticket edge
    types it can carry.
    """
    edges: List[Tuple[str, str, str, str]] = []
    for rel in TICKET_EDGE_SOURCES:
        path = Path(root) / rel
        if not path.is_file():
            continue
        module = _load_edge_source(path)
        emit = getattr(module, "ticket_edges", None)
        if emit is None:
            raise RuntimeError("%s declares no ticket_edges() function" % rel)
        for edge in emit(root):
            spine_type = TICKET_TO_SPINE.get(str(edge.type))
            if spine_type is None:
                continue
            edges.append(
                (str(edge.from_id), spine_type, str(edge.to_id), str(edge.from_id))
            )
    return edges


def _refs_edges(root: Path) -> List[Tuple[str, str, str, str]]:
    edges: List[Tuple[str, str, str, str]] = []
    for rel in iter_tracked_markdown(root):
        path = root / rel
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for _lineno, targets in parse_markers(text):
            for token in targets:
                if classify_marker_target(token) is not None:
                    continue  # malformed — the gate reports it, the builder skips
                normalized = token
                if token.startswith("`") and token.endswith("`"):
                    normalized = token[1:-1].strip()
                edges.append((rel, RELATIONSHIP_REFS, normalized, rel))
    return edges


def build_relationships(root: Path) -> List[Relationship]:
    """The deterministic, sorted, deduplicated cross-reference edge list."""
    root = Path(root).resolve()
    edges: List[Tuple[str, str, str, str]] = []
    edges.extend(_adr_edges(root))
    edges.extend(_board_edges(root))
    edges.extend(_ticket_edges(root))
    edges.extend(_refs_edges(root))
    unique = sorted(set(edges), key=lambda edge: (edge[1], edge[0], edge[2], edge[3]))
    return [
        Relationship(from_id=from_id, type=rel_type, to_id=to_id, via=via)
        for from_id, rel_type, to_id, via in unique
    ]
