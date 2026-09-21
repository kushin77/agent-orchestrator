"""Build the institutional knowledge index (issue #139 / M24).

---knowledge---
module_id: governance.knowledge.indexer
system: governance
app: knowledge
solution_class: enterprise
patterns: [deterministic]
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [now_iso, repo_name, sha256_file, iter_matches, extract_keywords, keywords_for_bytes, build_index, drift_findings, load_catalog, write_catalog, (+1 more)]
invariants: ""
gotchas: ""
related: ["#139"]
do_not_duplicate: null
---knowledge---

The indexer walks the declarative catalogue in `sources.py`, records provenance
for every asset it finds, refuses to accept credential-shaped content, and reports
coverage per kind. It is the single generator: nothing in this repository
hand-maintains a mirror of the catalogue.

Design notes that matter:

* **Deterministic.** Items are ordered by (kind, id) and provenance comes from git
  and the file bytes, so two runs over one revision produce identical output.
* **Honest about absence.** A required source that is missing is an error; a
  CMR-hub source that is unreachable because `vendor/CMR` is not checked out is a
  reported gap, not a silent pass.
* **No secrets in, no secrets out.** Suspected credentials are reported by rule
  and line number with the value redacted.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from model import (
    CODE_EXPECTED_KIND_UNAVAILABLE,
    CODE_INTEGRITY_DRIFT,
    CODE_MALFORMED_ISSUE_SNAPSHOT,
    CODE_PROVENANCE_INCOMPLETE,
    CODE_REQUIRED_KIND_EMPTY,
    CODE_REQUIRED_SOURCE_MISSING,
    CODE_SECRET_POLICY_VIOLATION,
    CODE_UNREADABLE_SOURCE,
    EXPECTED_KINDS,
    KIND_ISSUE_METADATA,
    KINDS,
    REQUIRED_KINDS,
    SEVERITY_WARNING,
    Coverage,
    Finding,
    Index,
    KnowledgeItem,
    Provenance,
)
from secretpolicy import scan_file
from sources import SOURCE_SPECS, SourceSpec

import crossref

CATALOG_FILENAME = "catalog.json"
REPORT_RELPATH = Path(".verify") / "knowledge-index-report.json"


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# -- primitives --------------------------------------------------------------


def repo_name(root: Path) -> str:
    """``owner/name`` from the git origin, else the directory name."""
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            check=False,
        )
        url = out.stdout.strip()
        if out.returncode == 0 and url:
            cleaned = url.removesuffix(".git")
            for marker in ("github.com/", "github.com:"):
                if marker in cleaned:
                    return cleaned.split(marker, 1)[1]
            return cleaned
    except OSError:
        pass
    return root.name


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _line_count(data: bytes) -> int:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return 0
    return text.count("\n") + (1 if text and not text.endswith("\n") else 0)


class _GitMeta:
    """Last-commit metadata per path, cached (at most one subprocess per path)."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._cache: Dict[str, Tuple[str, str]] = {}
        self._available: Optional[bool] = None

    def get(self, relpath: str) -> Tuple[str, str]:
        if relpath in self._cache:
            return self._cache[relpath]
        if self._available is False:
            return ("unversioned", "")
        try:
            out = subprocess.run(
                ["git", "-C", str(self._root), "log", "-1",
                 "--format=%H%x00%cI", "--", relpath],
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError:
            self._available = False
            return ("unversioned", "")
        if out.returncode != 0:
            self._available = False
            return ("unversioned", "")
        self._available = True
        line = out.stdout.strip()
        if not line:
            result: Tuple[str, str] = ("unversioned", "")  # tracked but uncommitted
        else:
            sha, _, stamp = line.partition("\x00")
            result = (sha, stamp)
        self._cache[relpath] = result
        return result


def iter_matches(root: Path, pattern: str) -> List[Path]:
    """Files matching a catalogue glob, sorted for deterministic output."""
    if any(char in pattern for char in "*?["):
        matches = [p for p in root.glob(pattern) if p.is_file()]
    else:
        candidate = root / pattern
        matches = [candidate] if candidate.is_file() else []
    return sorted(matches, key=lambda p: p.as_posix())


def _title_for(relpath: str) -> str:
    return Path(relpath).stem.replace("-", " ").replace("_", " ").title()


# Words carrying no discovery value; without this the keyword field fills with
# prose glue and search quality drops.
_STOPWORDS = frozenset(
    """
    the and for with that this from are not you all any can has have its may must
    out per run use used using via was were will your when what into than then
    they them their been each only over such more most other some these those
    upon whether which while being does done also both just like made make many
    need new one our own same set should since still take two very way well where
    who why without would about above after again against before below between
    during once under until rule rules item items note notes see also example
    examples doc docs file files section sections value values name names
    """.split()
)
KEYWORD_LIMIT = 40
MIN_KEYWORD_LENGTH = 4


def extract_keywords(text: str, limit: int = KEYWORD_LIMIT) -> Tuple[str, ...]:
    """Deterministic significant-term extraction for searchable metadata.

    Frequency first, then alphabetical, so two runs over one revision produce the
    same keyword list — a catalogue that reorders itself on every build is
    useless for drift review.
    """
    counts: Dict[str, int] = {}
    for raw_word in text.split():
        word = raw_word.strip(".,;:()[]{}*`'\"!?<>|#—-–/\\").lower()
        if len(word) < MIN_KEYWORD_LENGTH or word in _STOPWORDS:
            continue
        if not any(char.isalpha() for char in word):
            continue
        counts[word] = counts.get(word, 0) + 1
    ranked = sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))
    return tuple(word for word, _ in ranked[:limit])


def keywords_for_bytes(data: bytes) -> Tuple[str, ...]:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return ()
    return extract_keywords(text)


# -- item construction -------------------------------------------------------


def _file_items(
    root: Path,
    spec: SourceSpec,
    matches: Sequence[Path],
    git: _GitMeta,
    owner_repo: str,
    findings: List[Finding],
    secret_reader=None,
) -> List[KnowledgeItem]:
    items: List[KnowledgeItem] = []
    for match in matches:
        rel = match.relative_to(root).as_posix()

        for violation in scan_file(match, reader=secret_reader):
            findings.append(
                Finding(
                    code=CODE_SECRET_POLICY_VIOLATION,
                    message="indexed asset contains a suspected credential: %s"
                    % violation.describe(),
                    path=rel,
                )
            )

        try:
            data = match.read_bytes()
        except OSError as exc:
            findings.append(
                Finding(
                    code=CODE_UNREADABLE_SOURCE,
                    message="cannot read source: %s" % exc,
                    path=rel,
                )
            )
            continue

        version, timestamp = git.get(rel)
        provenance = Provenance(
            origin_repo=owner_repo,
            origin_path=rel,
            owner=spec.owner,
            version=version,
            sha256=hashlib.sha256(data).hexdigest(),
            bytes=len(data),
            lines=_line_count(data),
            timestamp=timestamp,
            retrieval=spec.retrieval,
            upstream=spec.upstream,
        )
        missing = provenance.missing_fields()
        if missing:
            findings.append(
                Finding(
                    code=CODE_PROVENANCE_INCOMPLETE,
                    message="provenance is missing %s" % ", ".join(missing),
                    path=rel,
                )
            )

        items.append(
            KnowledgeItem(
                id=rel,
                kind=spec.kind,
                path=rel,
                title=_title_for(rel),
                provenance=provenance,
                tags=tuple(tag for tag in (spec.upstream, spec.retrieval) if tag),
                keywords=keywords_for_bytes(data),
            )
        )
    return items


def _issue_items(
    root: Path, spec: SourceSpec, git: _GitMeta, owner_repo: str, findings: List[Finding]
) -> List[KnowledgeItem]:
    """One knowledge item per issue in the committed board snapshot."""
    path = root / spec.pattern
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        findings.append(
            Finding(
                code=CODE_MALFORMED_ISSUE_SNAPSHOT,
                message="board snapshot is not readable JSON: %s" % exc,
                path=spec.pattern,
            )
        )
        return []

    issues = raw.get("issues") if isinstance(raw, Mapping) else None
    if not isinstance(issues, list):
        findings.append(
            Finding(
                code=CODE_MALFORMED_ISSUE_SNAPSHOT,
                message="board snapshot has no 'issues' list",
                path=spec.pattern,
            )
        )
        return []

    try:
        digest = sha256_file(path)
    except OSError as exc:
        findings.append(
            Finding(
                code=CODE_UNREADABLE_SOURCE,
                message="cannot read board snapshot: %s" % exc,
                path=spec.pattern,
            )
        )
        return []

    version, timestamp = git.get(spec.pattern)
    items: List[KnowledgeItem] = []
    for issue in issues:
        if not isinstance(issue, Mapping) or "number" not in issue:
            continue
        number = issue.get("number")
        tags = [str(issue.get("state", ""))]
        if issue.get("milestone"):
            tags.append(str(issue["milestone"]))
        for label in issue.get("labels") or ():
            tags.append(str(label))
        if issue.get("parent") is not None:
            tags.append("parent:%s" % issue["parent"])
        for blocker in issue.get("blocked_by") or ():
            tags.append("blocked_by:%s" % blocker)
        items.append(
            KnowledgeItem(
                id="issue-%s" % number,
                kind=KIND_ISSUE_METADATA,
                path="%s#issue-%s" % (spec.pattern, number),
                title="#%s %s" % (number, issue.get("title", "")),
                provenance=Provenance(
                    origin_repo=owner_repo,
                    origin_path=spec.pattern,
                    owner=spec.owner,
                    version=version,
                    sha256=digest,
                    timestamp=timestamp,
                    retrieval=spec.retrieval,
                ),
                tags=tuple(tag for tag in tags if tag),
                keywords=extract_keywords(
                    "%s %s" % (issue.get("title", ""), " ".join(str(t) for t in tags))
                ),
            )
        )
    return items


# -- build -------------------------------------------------------------------


def build_index(
    repo_root: Path,
    *,
    owner_repo: Optional[str] = None,
    generated_at: Optional[str] = None,
    secret_reader=None,
) -> Index:
    """Walk the catalogue and produce the index (plus any findings)."""
    root = Path(repo_root).resolve()
    owner = owner_repo or repo_name(root)
    git = _GitMeta(root)
    findings: List[Finding] = []
    items: List[KnowledgeItem] = []
    unreachable: Dict[str, List[str]] = {}

    for spec in SOURCE_SPECS:
        matches = iter_matches(root, spec.pattern)
        if not matches:
            if spec.required:
                findings.append(
                    Finding(
                        code=CODE_REQUIRED_SOURCE_MISSING,
                        message="required source '%s' (%s) matched no files"
                        % (spec.pattern, spec.kind),
                        path=spec.pattern,
                    )
                )
            else:
                unreachable.setdefault(spec.kind, []).append(spec.pattern)
            continue

        if spec.kind == KIND_ISSUE_METADATA and spec.pattern.endswith(".json"):
            items.extend(_issue_items(root, spec, git, owner, findings))
            continue

        items.extend(_file_items(root, spec, matches, git, owner, findings, secret_reader))

    items.sort(key=lambda item: (item.kind, item.id))

    by_kind: Dict[str, int] = {}
    for item in items:
        by_kind[item.kind] = by_kind.get(item.kind, 0) + 1

    coverage: List[Coverage] = []
    for kind in KINDS:
        count = by_kind.get(kind, 0)
        required = kind in REQUIRED_KINDS
        if count:
            coverage.append(
                Coverage(kind=kind, count=count, required=required, status="present")
            )
            continue
        if required:
            coverage.append(
                Coverage(
                    kind=kind,
                    count=0,
                    required=True,
                    status="absent",
                    reason="no indexed source for a required kind",
                )
            )
            findings.append(
                Finding(
                    code=CODE_REQUIRED_KIND_EMPTY,
                    message="required kind '%s' yielded no items" % kind,
                )
            )
        else:
            patterns = sorted(unreachable.get(kind, ()))
            reason = (
                "source unreachable (CMR hub submodule absent?): %s"
                % ", ".join(patterns)
                if patterns
                else "no source declared"
            )
            coverage.append(
                Coverage(
                    kind=kind, count=0, required=False, status="unavailable", reason=reason
                )
            )
            findings.append(
                Finding(
                    code=CODE_EXPECTED_KIND_UNAVAILABLE,
                    message="expected kind '%s' is unavailable: %s" % (kind, reason),
                    severity=SEVERITY_WARNING,
                )
            )

    return Index(
        generated_at=generated_at or now_iso(),
        repo=owner,
        items=items,
        relationships=crossref.build_relationships(root),
        coverage=coverage,
        findings=findings,
    )


# -- validation / drift ------------------------------------------------------


def drift_findings(index: Index, baseline: Mapping) -> List[Finding]:
    """Compare a freshly built index against a recorded catalogue.

    Drift is **reported, not fatal**: sources legitimately change between
    refreshes, and a gate that blocked every doc edit would be turned off within a
    week. The finding names exactly which assets moved so a refresh is a decision
    rather than a discovery.
    """
    recorded: Dict[str, str] = {}
    for raw in (baseline or {}).get("items", []) or []:
        provenance = raw.get("provenance", {}) or {}
        recorded[str(raw.get("id", ""))] = str(provenance.get("sha256", ""))

    current = {item.id: item.provenance.sha256 for item in index.items}
    findings: List[Finding] = []

    for item_id in sorted(set(recorded) - set(current)):
        findings.append(
            Finding(
                code=CODE_INTEGRITY_DRIFT,
                message="recorded asset is no longer indexed",
                severity=SEVERITY_WARNING,
                path=item_id,
            )
        )
    for item_id in sorted(set(current) - set(recorded)):
        findings.append(
            Finding(
                code=CODE_INTEGRITY_DRIFT,
                message="asset is new since the last recorded index",
                severity=SEVERITY_WARNING,
                path=item_id,
            )
        )
    for item_id in sorted(set(recorded) & set(current)):
        if recorded[item_id] != current[item_id]:
            findings.append(
                Finding(
                    code=CODE_INTEGRITY_DRIFT,
                    message="asset changed since the last recorded index (sha256 mismatch)",
                    severity=SEVERITY_WARNING,
                    path=item_id,
                )
            )
    return findings


def load_catalog(path: Path) -> Optional[Mapping]:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_catalog(index: Index, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(index.as_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return path


def main() -> int:  # pragma: no cover - thin convenience wrapper
    root = Path(__file__).resolve().parents[2]
    index = build_index(root)
    catalog = write_catalog(index, root / "governance" / "knowledge" / CATALOG_FILENAME)
    print(json.dumps({"catalog": str(catalog), "items": len(index.items)}, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
