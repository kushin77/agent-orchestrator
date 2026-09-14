#!/usr/bin/env python3
"""lessons_sync.py — the cross-repo lessons-sync CONTRACT gate (issue #424).

There is a lessons loop on each side of the repo boundary: `kushin77/deepseek`
builds one (`kushin77/deepseek#84`, `kushin77/deepseek#79`) and this repository
builds one (`#402`/`#403`, on top of `governance/lessons/`). `#181`'s gap
register named the missing relationship as **gap 6** and filed it against `#141`,
which closed while the peer items stayed open.

This module gates the *contract* between the two loops, never their prose. It is
a **pure function of pinned, committed inputs** plus this repository's own
authoritative ledger, so it is offline and deterministic:

    * one authoritative ledger, one derived view — two symmetric stores are
      refused by construction (a second `writer` is a finding);
    * the peer-side ledger reference **resolves** (against the pinned peer
      snapshot offline, or live `gh` behind `--live`);
    * a lesson recorded on one side is **discoverable from the other** (its local
      id resolves in this repository's ledger, and its declared peer counterpart
      resolves on the peer side);
    * a lesson whose peer-side counterpart is **missing is reported by id** —
      never passed over in silence;
    * a dispatch hint carries **provenance** (an issue reference *and* a commit),
      not a bare string — the local equivalent of `kushin77/deepseek#79`'s "feed
      lessons back as dispatch hints" is the brain-directive / claim chain;
    * a reference that would **close a peer issue from here** is refused
      (cross-repo `Closes` is not used — same-owner cross-repo `Closes` *does*
      auto-close, so it must never be written by accident);
    * the **second lessons ledger** — the `kushin77/CMR` `docs/LESSONS.md`
      consolidated index — is **declared** in the contract, its reference
      **resolves** against a frozen baseline, and **every CMR index record
      carries an explicit disposition** (`hub-only`, or `mirrors` naming a local
      counterpart that must resolve); a record with no disposition, or a mapping
      whose counterpart is missing, is reported **by id**, never silently
      dropped.

The CMR index is frozen as a committed input (`cmr-ledger.json`) because
`vendor/CMR` is an **unpopulated submodule in a fresh worktree**, so the default
mode can never read the live source there. The default pass therefore reconciles
against the freeze and is fully offline; `--verify-cmr-source` re-resolves the
freeze against the live `vendor/CMR/docs/LESSONS.md` when it is populated, and a
missing or empty live source is CANNOT-ASSESS (`2`), never a pass.

Exit-code contract (the repo tri-state, `guardrails/honesty`): `0` OK / `1`
NOT-OK (findings) / `2` CANNOT-ASSESS. A required input that is missing,
unreadable, empty, or malformed exits `2` and **never** `0`.

No network, no `gh`, no wall clock in the default mode; the report is written to
a file, never streamed into the shared shell.

Usage:
    python3 governance/lessons-sync/lessons_sync.py --report FILE
    python3 governance/lessons-sync/lessons_sync.py --live --report FILE
    python3 governance/lessons-sync/lessons_sync.py --self-test
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Any, Callable, Iterable

OUR_REPO = "kushin77/agent-orchestrator"
WRITER_ROLE = "writer"
DERIVED_ROLE = "derived-view"

# The second lessons ledger — the CMR hub's consolidated index. It lives in the
# pinned `vendor/CMR` submodule, which is UNPOPULATED in a fresh git worktree, so
# the live read is behind an explicit flag and the default pass reconciles against
# the frozen contract input `governance/lessons-sync/cmr-ledger.json`.
CMR_VENDOR_DIR = "vendor/CMR"
CMR_LESSONS_REL = "docs/LESSONS.md"
CMR_DISPOSITIONS = frozenset({"hub-only", "mirrors"})
CMR_ROW_ID_RE = re.compile(r"^(?:LESSON|SUGGEST)-\d+$")
LEDGER_REL = "governance/lessons/ledger.jsonl"

# A foreign-repo close reference: `Closes owner/repo#N` (and Fixes/Resolves).
FOREIGN_CLOSE_RE = re.compile(
    r"\b(?:Closes|Fixes|Resolves)\s+([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)#(\d+)",
    re.IGNORECASE,
)
# An issue provenance reference: `#N` (same repo) or `owner/repo#N`.
ISSUE_REF_RE = re.compile(r"^(?:[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)?#\d+$")
COMMIT_RE = re.compile(r"^[0-9a-f]{7,40}$")

OK = 0
NOT_OK = 1
CANNOT_ASSESS = 2


class InputError(Exception):
    """A required pinned input is missing, unreadable, empty, or malformed."""


def _load_json(path: str) -> Any:
    if not os.path.isfile(path):
        raise InputError(f"input not found: {path}")
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError as exc:  # unreadable
        raise InputError(f"input unreadable: {path} ({exc})") from exc
    if not text.strip():
        raise InputError(f"input empty: {path}")
    try:
        return json.loads(text)
    except ValueError as exc:
        raise InputError(f"input malformed JSON: {path} ({exc})") from exc


def _load_ledger_ids(path: str) -> set[str]:
    """The set of lesson-record ids in this repository's authoritative ledger."""
    if not os.path.isfile(path):
        raise InputError(f"ledger not found: {path}")
    ids: set[str] = set()
    with open(path, encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, 1):
            line = raw.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except ValueError as exc:
                raise InputError(f"ledger malformed JSONL at line {lineno} ({exc})") from exc
            if isinstance(record, dict) and isinstance(record.get("id"), str):
                ids.add(record["id"])
    if not ids:
        raise InputError(f"ledger declares no records: {path}")
    return ids


def _finding(code: str, item_id: str, message: str) -> dict[str, str]:
    return {"code": code, "id": item_id, "message": message}


def _iter_texts(value: Any) -> Iterable[str]:
    """Yield every string reachable in a nested structure (for the close scan)."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key in sorted(value):
            yield from _iter_texts(key)
            yield from _iter_texts(value[key])
    elif isinstance(value, list):
        for item in value:
            yield from _iter_texts(item)


def check_contract(contract: dict[str, Any]) -> list[dict[str, str]]:
    """C1 — one authoritative ledger, one derived view, declared direction."""
    findings: list[dict[str, str]] = []
    writers = [k for k, v in contract.items()
               if isinstance(v, dict) and v.get("role") == WRITER_ROLE]
    derived = [k for k, v in contract.items()
               if isinstance(v, dict) and v.get("role") == DERIVED_ROLE]
    if len(writers) != 1:
        findings.append(_finding(
            "symmetric-stores", "contract",
            f"expected exactly one '{WRITER_ROLE}', found {len(writers)} ({sorted(writers)}) "
            f"— two symmetric stores are refused"))
    # One writer, and at least one derived view. More than one *derived* view is
    # read-only fan-out of the single writer (the CMR hub is the second one); it is
    # not a symmetric store — only a second *writer* is.
    if len(derived) < 1:
        findings.append(_finding(
            "contract-invalid", "contract",
            f"expected at least one '{DERIVED_ROLE}', found {len(derived)} ({sorted(derived)})"))
    sync = contract.get("sync")
    if not isinstance(sync, dict) or not str(sync.get("direction", "")).strip():
        findings.append(_finding(
            "contract-invalid", "contract", "sync.direction is not declared"))
    elif sync.get("symmetric") is not False:
        findings.append(_finding(
            "symmetric-stores", "sync",
            "sync.symmetric must be false — the relationship is never two-way"))
    return findings


def check_ledger_ref(contract: dict[str, Any], peer: dict[str, Any],
                     live: Callable[[str, int], bool] | None = None) -> list[dict[str, str]]:
    """C2 — the deepseek ledger reference resolves."""
    # The deepseek relationship is the `derived` key explicitly; the CMR-hub
    # relationship is `cmr_hub` (checked by check_cmr_ledger).
    derived = contract.get("derived") if isinstance(contract.get("derived"), dict) else None
    ref = (derived or {}).get("ledger_ref")
    if not isinstance(ref, dict):
        return [_finding("ledger-ref-unresolved", "contract",
                         "derived view declares no ledger_ref to resolve")]
    repo = str(ref.get("repo", ""))
    issue = ref.get("issue")
    if not repo or not isinstance(issue, int):
        return [_finding("ledger-ref-unresolved", repo or "contract",
                         "ledger_ref must name a repo and an integer issue")]
    if live is not None:
        if not live(repo, issue):
            raise InputError(
                f"live peer reference unavailable: {repo}#{issue} "
                f"(CANNOT-ASSESS, never a pass)")
        return []
    peers = peer.get("issues")
    if not isinstance(peers, list):
        raise InputError("peer snapshot has no 'issues' list")
    for entry in peers:
        if isinstance(entry, dict) and entry.get("number") == issue:
            return []
    return [_finding("ledger-ref-unresolved", f"{repo}#{issue}",
                     f"the peer ledger reference {repo}#{issue} does not resolve "
                     f"in the pinned peer snapshot")]


def check_discoverable(ledger_ids: set[str], hints: list[Any], peer: dict[str, Any],
                       commit_exists: Callable[[str], bool] | None = None) -> list[dict[str, str]]:
    """C3/C4/C5 — discoverability, peer counterpart by id, hint provenance."""
    findings: list[dict[str, str]] = []
    peer_numbers = {e.get("number") for e in peer.get("issues", []) if isinstance(e, dict)}
    peer_lessons = peer.get("peer_lessons", [])
    if not isinstance(peer_lessons, list):
        raise InputError("peer snapshot 'peer_lessons' is not a list")

    for idx, hint in enumerate(hints):
        if not isinstance(hint, dict):
            findings.append(_finding(
                "hint-without-provenance", f"hint[{idx}]",
                "a dispatch hint must be an object carrying lesson_id + issue + commit, "
                "not a bare string"))
            continue
        hint_id = str(hint.get("id", f"hint[{idx}]"))
        lesson_id = hint.get("lesson_id")
        # C3 — the lesson is recorded on our side (discoverable from the ledger).
        if not isinstance(lesson_id, str) or lesson_id not in ledger_ids:
            findings.append(_finding(
                "lesson-not-discoverable", str(lesson_id or hint_id),
                f"hint '{hint_id}' names lesson '{lesson_id}' which is not recorded "
                f"in the authoritative ledger"))
        # C4 — the declared peer counterpart resolves.
        peer_ref = hint.get("peer_ref")
        if isinstance(peer_ref, dict):
            pnum = peer_ref.get("issue")
            if not isinstance(pnum, int) or pnum not in peer_numbers:
                findings.append(_finding(
                    "peer-counterpart-missing", str(lesson_id or hint_id),
                    f"lesson '{lesson_id}' declares peer counterpart "
                    f"{peer_ref.get('repo', '?')}#{pnum} which is missing from the "
                    f"peer snapshot"))
        # C5 — provenance is an issue ref + a commit, not a bare string.
        issue = hint.get("issue")
        commit = hint.get("commit")
        if not isinstance(issue, str) or not ISSUE_REF_RE.match(issue):
            findings.append(_finding(
                "hint-without-provenance", str(lesson_id or hint_id),
                f"hint '{hint_id}' has no issue provenance (issue ref missing or "
                f"malformed)"))
        if not isinstance(commit, str) or not COMMIT_RE.match(commit):
            findings.append(_finding(
                "hint-without-provenance", str(lesson_id or hint_id),
                f"hint '{hint_id}' has no commit provenance (commit sha missing or "
                f"malformed)"))
        elif commit_exists is not None and not commit_exists(commit):
            findings.append(_finding(
                "hint-provenance-unresolvable", str(lesson_id or hint_id),
                f"hint '{hint_id}' cites commit '{commit}' which is not present "
                f"in this repository"))

    # Reverse direction — a peer lesson must be discoverable locally.
    for idx, lesson in enumerate(peer_lessons):
        if not isinstance(lesson, dict):
            raise InputError("peer snapshot 'peer_lessons' entry is not an object")
        mirrors = lesson.get("mirrors")
        if isinstance(mirrors, str) and mirrors not in ledger_ids:
            findings.append(_finding(
                "local-counterpart-missing", str(lesson.get("id", f"peer[{idx}]")),
                f"peer lesson '{lesson.get('id')}' mirrors '{mirrors}' which is not "
                f"recorded in the authoritative ledger"))
    return findings


def check_peer_close(*documents: Any) -> list[dict[str, str]]:
    """C6 — a reference that would close a peer issue from here is refused."""
    findings: list[dict[str, str]] = []
    seen: set[str] = set()
    for document in documents:
        for text in _iter_texts(document):
            for match in FOREIGN_CLOSE_RE.finditer(text):
                repo, number = match.group(1), match.group(2)
                if repo.lower() == OUR_REPO.lower():
                    continue
                key = f"{repo}#{number}"
                if key in seen:
                    continue
                seen.add(key)
                findings.append(_finding(
                    "peer-close-refused", key,
                    f"'{match.group(0)}' would close a peer issue from here; "
                    f"cross-repo Closes is not used"))
            if isinstance(document, dict) and document.get("closes_peer") is True:
                if "closes_peer" not in seen:
                    seen.add("closes_peer")
                    findings.append(_finding(
                        "peer-close-refused", "closes_peer",
                        "a closes_peer flag is set; the boundary hands off, never "
                        "closes"))
    return findings


def _sha256_file(path: str) -> str:
    import hashlib
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_cmr_index(text: str) -> list[dict[str, str]]:
    """Parse the CMR consolidated-index rows into {id, kind, status} records.

    Pure and deterministic: the id is the first table cell and must match the
    `LESSON-NNN` / `SUGGEST-NNN` vocabulary; the kind and status are the next two
    cells. A `|` inside the free-text lesson column cannot affect the first four
    cells.
    """
    records: list[dict[str, str]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) < 4 or not CMR_ROW_ID_RE.match(cells[0]):
            continue
        records.append({"id": cells[0], "kind": cells[2], "status": cells[3]})
    return records


def _load_cmr_baseline(path: str) -> dict[str, Any]:
    doc = _load_json(path)
    if not isinstance(doc, dict):
        raise InputError("cmr baseline is not an object")
    records = doc.get("records")
    if not isinstance(records, list) or not records:
        raise InputError("cmr baseline declares no records")
    if not isinstance(doc.get("local_only"), list):
        raise InputError("cmr baseline has no 'local_only' list")
    if not isinstance(doc.get("ledger_ref"), dict):
        raise InputError("cmr baseline declares no ledger_ref")
    if not isinstance(doc.get("_provenance"), dict):
        raise InputError("cmr baseline declares no _provenance block")
    return doc


def _cmr_source_path(root: str, override: str | None) -> str:
    if override:
        return override if os.path.isabs(override) else os.path.join(root, override)
    return os.path.join(root, CMR_VENDOR_DIR, CMR_LESSONS_REL)


def check_cmr_ledger(contract: dict[str, Any], cmr_doc: dict[str, Any],
                     ledger_ids: set[str]) -> list[dict[str, str]]:
    """C7/C8 — the second lessons ledger: declared, ref resolves, no silent drop.

    Every CMR index record must carry an explicit disposition. `hub-only` says it
    is org-scoped and deliberately not mirrored here; `mirrors` names the local
    counterpart it maps to, which must resolve in the authoritative ledger. A
    record with neither is reported **by id**; so is a mapping whose counterpart is
    missing. An unconfirmed relationship (`confirmed: false`) may assert no
    `mirrors` mapping at all.
    """
    findings: list[dict[str, str]] = []
    hub = contract.get("cmr_hub")
    if not isinstance(hub, dict):
        return [_finding("ledger-undeclared", "contract",
                         "the CMR-hub lessons ledger is not declared in the contract")]
    if not str(hub.get("role", "")).strip() or not str(hub.get("direction", "")).strip():
        findings.append(_finding(
            "contract-invalid", "cmr_hub",
            "cmr_hub must declare both a role and a direction of flow"))

    ref = hub.get("ledger_ref")
    base_ref = cmr_doc.get("ledger_ref")
    if not isinstance(ref, dict):
        findings.append(_finding(
            "ledger-ref-unresolved", "cmr_hub",
            "cmr_hub declares no ledger_ref to resolve"))
    elif not (isinstance(base_ref, dict)
              and base_ref.get("repo") == ref.get("repo")
              and base_ref.get("path") == ref.get("path")):
        findings.append(_finding(
            "ledger-ref-unresolved", f"{ref.get('repo')} {ref.get('path')}",
            "the CMR ledger reference does not resolve in the frozen baseline"))

    confirmed = hub.get("confirmed") is True
    cmr_ids: dict[str, dict[str, Any]] = {}
    for rec in cmr_doc["records"]:
        if not isinstance(rec, dict) or not isinstance(rec.get("id"), str):
            raise InputError("cmr baseline record is not an object carrying an id")
        cmr_ids[rec["id"]] = rec

    mirrored_locals: set[str] = set()
    for rid in sorted(cmr_ids):
        rec = cmr_ids[rid]
        disposition = rec.get("disposition")
        if disposition == "mirrors":
            if not confirmed:
                findings.append(_finding(
                    "mapping-unconfirmed", rid,
                    f"CMR record {rid} asserts a counterpart while the relationship "
                    f"is a declared intent (confirmed=false)"))
            local_id = rec.get("mirrors")
            if not isinstance(local_id, str) or local_id not in ledger_ids:
                findings.append(_finding(
                    "local-counterpart-missing", rid,
                    f"CMR record {rid} mirrors {local_id!r} which is not recorded in "
                    f"the authoritative ledger"))
            else:
                mirrored_locals.add(local_id)
        elif disposition != "hub-only":
            findings.append(_finding(
                "cmr-record-undisclosed", rid,
                f"CMR index record {rid} carries no disposition (hub-only, or mirrors "
                f"naming a local counterpart) — reported by id, never dropped"))

    local_only = cmr_doc["local_only"]
    for lid in sorted(str(x) for x in local_only):
        if lid not in ledger_ids:
            findings.append(_finding(
                "local-record-unknown", lid,
                "declared local-only id is not recorded in the authoritative ledger"))
    accounted = set(mirrored_locals) | {str(x) for x in local_only}
    for lid in sorted(ledger_ids - accounted):
        findings.append(_finding(
            "local-record-undisclosed", lid,
            f"authoritative ledger record {lid} has no counterpart declaration and is "
            f"not listed local-only — reported by id, never dropped"))
    return findings


def check_cmr_source(cmr_doc: dict[str, Any], source_path: str) -> list[dict[str, str]]:
    """--verify-cmr-source — re-resolve the freeze against the live vendor file.

    A missing, unreadable, or empty live source is CANNOT-ASSESS (raised as
    InputError → rc 2), never a pass.
    """
    prov = cmr_doc["_provenance"]
    if not source_path or not os.path.isfile(source_path):
        raise InputError(
            f"live CMR lessons source unavailable: {source_path} "
            f"(CANNOT-ASSESS, never a pass)")
    if os.path.getsize(source_path) == 0:
        raise InputError(f"live CMR lessons source is empty: {source_path}")
    findings: list[dict[str, str]] = []
    actual = _sha256_file(source_path)
    expected = prov.get("source_sha256")
    if actual != expected:
        findings.append(_finding(
            "cmr-baseline-stale", os.path.basename(source_path),
            f"frozen source_sha256 {expected} != live {actual}"))
    with open(source_path, encoding="utf-8") as fh:
        live = parse_cmr_index(fh.read())
    live_ids = {r["id"] for r in live}
    frozen_ids = {r["id"] for r in cmr_doc["records"]}
    for rid in sorted(live_ids - frozen_ids):
        findings.append(_finding(
            "cmr-record-undisclosed", rid,
            f"CMR index record {rid} is present live but has no disposition in the "
            f"frozen baseline — reported by id"))
    for rid in sorted(frozen_ids - live_ids):
        findings.append(_finding(
            "cmr-baseline-stale", rid,
            f"frozen baseline record {rid} is absent from the live CMR index"))
    return findings


def refresh_cmr_baseline(root: str, source_path: str, out_path: str,
                         vendor_commit: str | None = None,
                         extracted: str | None = None) -> int:
    """Regenerate the frozen CMR baseline from the live index (populated vendor).

    Preserves each existing record's declared disposition by id, updates the
    provenance `source_sha256`, and recomputes `local_only` from the authoritative
    ledger minus mirror targets. A record that is new in the live index is written
    with no disposition on purpose, so it surfaces as `cmr-record-undisclosed`
    until it is dispositioned by name.
    """
    if not source_path or not os.path.isfile(source_path):
        print(f"lessons-sync: CANNOT-ASSESS — live CMR source unavailable: {source_path}",
              file=sys.stderr)
        return CANNOT_ASSESS
    with open(source_path, encoding="utf-8") as fh:
        live = parse_cmr_index(fh.read())
    if not live:
        print("lessons-sync: CANNOT-ASSESS — live CMR index parsed zero records",
              file=sys.stderr)
        return CANNOT_ASSESS
    previous: dict[str, dict[str, Any]] = {}
    if os.path.isfile(out_path):
        try:
            with open(out_path, encoding="utf-8") as fh:
                for rec in json.load(fh).get("records", []):
                    previous[rec["id"]] = rec
        except (ValueError, OSError, TypeError):
            previous = {}

    records: list[dict[str, str]] = []
    for entry in live:
        old = previous.get(entry["id"], {})
        record = dict(entry)
        if old.get("disposition") in CMR_DISPOSITIONS:
            record["disposition"] = old["disposition"]
            if old.get("disposition") == "mirrors" and isinstance(old.get("mirrors"), str):
                record["mirrors"] = old["mirrors"]
        records.append(record)

    ledger_ids = sorted(_load_ledger_ids(os.path.join(root, LEDGER_REL)))
    mirrored = {r["mirrors"] for r in records
                if r.get("disposition") == "mirrors" and isinstance(r.get("mirrors"), str)}
    prev_prov = {}
    if os.path.isfile(out_path):
        try:
            with open(out_path, encoding="utf-8") as fh:
                prev_prov = json.load(fh).get("_provenance", {})
        except (ValueError, OSError):
            prev_prov = {}
    documented = {
        "_provenance": {
            "vendor_repo": "kushin77/CMR",
            "source_path": CMR_LESSONS_REL,
            "vendor_commit": vendor_commit or prev_prov.get("vendor_commit", "unrecorded"),
            "source_sha256": _sha256_file(source_path),
            "extracted": extracted or prev_prov.get("extracted", "unrecorded"),
            "note": "Durable contract input: the kushin77/CMR consolidated lessons index, "
                    "frozen so the default lessons-sync gate is deterministic and offline.",
            "frozen_because": "vendor/CMR is a git submodule that is UNPOPULATED in a fresh "
                              "git worktree, so the default gate cannot read the live index "
                              "there; --verify-cmr-source re-resolves the freeze when the "
                              "submodule is populated.",
            "refresh_command": "python3 governance/lessons-sync/lessons_sync.py "
                               "--refresh-cmr-baseline    # run where vendor/CMR is populated",
            "verify_command": "bash scripts/check-cross-repo-lessons.sh --verify-cmr-source",
        },
        "ledger_ref": {"repo": "kushin77/CMR", "path": CMR_LESSONS_REL,
                       "kind": "org-consolidated-index"},
        "records": records,
        "local_only": [lid for lid in ledger_ids if lid not in mirrored],
    }
    directory = os.path.dirname(out_path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(documented, fh, indent=2, sort_keys=False)
        fh.write("\n")
    print(f"lessons-sync: refreshed {os.path.relpath(out_path, root)} "
          f"({len(records)} CMR record(s), {len(documented['local_only'])} local-only)")
    return OK


def evaluate(contract: dict[str, Any], peer: dict[str, Any], hints_doc: Any,
             ledger_ids: set[str], cmr_doc: dict[str, Any],
             *, commit_exists: Callable[[str], bool] | None = None,
             live: Callable[[str, int], bool] | None = None) -> list[dict[str, str]]:
    """Pure evaluation: the ordered, deterministic finding list."""
    if isinstance(hints_doc, dict):
        hints = hints_doc.get("hints")
        if not isinstance(hints, list):
            raise InputError("hints input has no 'hints' list")
    elif isinstance(hints_doc, list):
        hints = hints_doc
    else:
        raise InputError("hints input is neither an object nor a list")

    findings: list[dict[str, str]] = []
    findings += check_contract(contract)
    findings += check_ledger_ref(contract, peer, live=live)
    findings += check_discoverable(ledger_ids, hints, peer, commit_exists=commit_exists)
    findings += check_cmr_ledger(contract, cmr_doc, ledger_ids)
    findings += check_peer_close(contract, peer, hints_doc)
    # Deterministic ordering: sort by (code, id, message).
    return sorted(findings, key=lambda f: (f["code"], f["id"], f["message"]))


def _git_commit_exists(sha: str) -> bool:
    import subprocess
    try:
        proc = subprocess.run(
            ["git", "cat-file", "-e", f"{sha}^{{commit}}"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    except OSError:
        return False
    return proc.returncode == 0


def _gh_issue_exists(repo: str, number: int) -> bool:
    import subprocess
    try:
        proc = subprocess.run(
            ["gh", "api", f"repos/{repo}/issues/{number}", "--jq", ".number"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False)
    except OSError:
        return False
    return proc.returncode == 0 and proc.stdout.strip().isdigit()


def _resolve(root: str, name: str) -> str:
    return name if os.path.isabs(name) else os.path.join(root, name)


def run(args: argparse.Namespace) -> int:
    root = args.root
    contract_path = _resolve(root, args.contract)
    peer_path = _resolve(root, args.peer)
    hints_path = _resolve(root, args.hints)
    ledger_path = _resolve(root, args.ledger)
    cmr_path = _resolve(root, args.cmr)

    live = _gh_issue_exists if args.live else None
    try:
        contract = _load_json(contract_path)
        peer = _load_json(peer_path)
        hints_doc = _load_json(hints_path)
        ledger_ids = _load_ledger_ids(ledger_path)
        cmr_doc = _load_cmr_baseline(cmr_path)
        if not isinstance(contract, dict):
            raise InputError("contract input is not an object")
        if not isinstance(peer, dict):
            raise InputError("peer snapshot is not an object")
        findings = evaluate(contract, peer, hints_doc, ledger_ids, cmr_doc,
                            commit_exists=_git_commit_exists, live=live)
        if args.verify_cmr_source:
            findings += check_cmr_source(cmr_doc, _cmr_source_path(root, args.cmr_source))
            findings = sorted(findings, key=lambda f: (f["code"], f["id"], f["message"]))
    except InputError as exc:
        report = {"ok": False, "rc": CANNOT_ASSESS, "findings": [],
                  "cannot_assess": str(exc)}
        _write_report(args.report, report)
        print(f"lessons-sync: CANNOT-ASSESS — {exc}", file=sys.stderr)
        return CANNOT_ASSESS

    rc = OK if not findings else NOT_OK
    report = {
        "ok": rc == OK,
        "rc": rc,
        "findings": findings,
        "inputs": {
            "contract": os.path.relpath(contract_path, root) if contract_path.startswith(root) else contract_path,
            "peer": os.path.relpath(peer_path, root) if peer_path.startswith(root) else peer_path,
            "hints": os.path.relpath(hints_path, root) if hints_path.startswith(root) else hints_path,
            "ledger": os.path.relpath(ledger_path, root) if ledger_path.startswith(root) else ledger_path,
            "cmr": os.path.relpath(cmr_path, root) if cmr_path.startswith(root) else cmr_path,
            "live": bool(args.live),
            "cmr_source_verified": bool(args.verify_cmr_source),
        },
        "finding_count": len(findings),
    }
    _write_report(args.report, report)
    if rc == OK:
        print(f"lessons-sync: OK — one authoritative ledger, {len(findings)} findings")
    else:
        print(f"lessons-sync: FAIL — {len(findings)} finding(s):", file=sys.stderr)
        for finding in findings:
            print(f"  {finding['code']} {finding['id']}: {finding['message']}",
                  file=sys.stderr)
    return rc


def _write_report(path: str | None, report: dict[str, Any]) -> None:
    if not path:
        return
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, sort_keys=True)
        fh.write("\n")


def self_test() -> int:
    """Provoke every refusal on in-memory inputs so the gate cannot pass vacuously."""
    base_contract = {
        "authoritative": {"repo": OUR_REPO, "role": WRITER_ROLE, "ledger": LEDGER_REL},
        "derived": {"repo": "kushin77/deepseek", "role": DERIVED_ROLE,
                    "ledger_ref": {"repo": "kushin77/deepseek", "issue": 84}},
        "cmr_hub": {"repo": "kushin77/CMR", "role": DERIVED_ROLE, "confirmed": False,
                    "direction": "one-way:agent-orchestrator->CMR",
                    "ledger_ref": {"repo": "kushin77/CMR", "path": CMR_LESSONS_REL,
                                   "kind": "org-consolidated-index"}},
        "sync": {"direction": "one-way:writer->derived", "symmetric": False},
    }
    base_peer = {"repo": "kushin77/deepseek", "issues": [{"number": 84, "state": "open"}],
                 "peer_lessons": []}
    base_hints = {"hints": [{"id": "hint-0001", "lesson_id": "L-1", "issue": "#402",
                             "commit": "b87cdf9"}]}
    base_cmr = {
        "_provenance": {"vendor_repo": "kushin77/CMR", "source_path": CMR_LESSONS_REL,
                        "source_sha256": "0" * 64},
        "ledger_ref": {"repo": "kushin77/CMR", "path": CMR_LESSONS_REL,
                       "kind": "org-consolidated-index"},
        "records": [{"id": "LESSON-001", "kind": "lesson", "status": "closed",
                     "disposition": "hub-only"}],
        "local_only": ["L-1"],
    }
    ledger = {"L-1"}

    def rc_of(contract, peer, hints, ids=ledger, cmr=None):
        doc = base_cmr if cmr is None else cmr
        return evaluate(contract, peer, hints, set(ids), _copy_doc(doc),
                        commit_exists=lambda _s: True)

    import copy as _copy

    def _copy_doc(value):
        return _copy.deepcopy(value)

    controls = []
    controls.append(("baseline clean", rc_of(base_contract, base_peer, base_hints) == []))

    sym = _copy.deepcopy(base_contract)
    sym["derived"]["role"] = WRITER_ROLE
    codes = {f["code"] for f in rc_of(sym, base_peer, base_hints)}
    controls.append(("symmetric stores refused", "symmetric-stores" in codes))

    bad_ref = _copy.deepcopy(base_peer)
    bad_ref["issues"] = []
    codes = {f["code"] for f in rc_of(base_contract, bad_ref, base_hints)}
    controls.append(("ledger ref unresolved", "ledger-ref-unresolved" in codes))

    missing_peer = _copy.deepcopy(base_hints)
    missing_peer["hints"][0]["peer_ref"] = {"repo": "kushin77/deepseek", "issue": 9999}
    findings = rc_of(base_contract, base_peer, missing_peer)
    controls.append(("peer counterpart missing reported by lesson id",
                     any(f["code"] == "peer-counterpart-missing" and f["id"] == "L-1"
                         for f in findings)))

    bare = {"hints": ["LESSON-0001 should be fed back as a hint"]}
    codes = {f["code"] for f in rc_of(base_contract, base_peer, bare)}
    controls.append(("bare-string hint refused", "hint-without-provenance" in codes))

    no_commit = _copy.deepcopy(base_hints)
    del no_commit["hints"][0]["commit"]
    codes = {f["code"] for f in rc_of(base_contract, base_peer, no_commit)}
    controls.append(("hint missing commit refused", "hint-without-provenance" in codes))

    close = {"hints": [{"id": "h", "lesson_id": "L-1", "issue": "#402",
                        "commit": "b87cdf9", "note": "Closes kushin77/deepseek#79"}]}
    codes = {f["code"] for f in rc_of(base_contract, base_peer, close)}
    controls.append(("peer-close reference refused", "peer-close-refused" in codes))

    # --- the second ledger (CMR hub) --------------------------------------
    no_hub = _copy.deepcopy(base_contract)
    del no_hub["cmr_hub"]
    codes = {f["code"] for f in rc_of(no_hub, base_peer, base_hints)}
    controls.append(("undeclared CMR ledger refused", "ledger-undeclared" in codes))

    bad_cmr_ref = _copy.deepcopy(base_contract)
    bad_cmr_ref["cmr_hub"]["ledger_ref"]["path"] = "docs/OTHER.md"
    codes = {f["code"] for f in rc_of(bad_cmr_ref, base_peer, base_hints)}
    controls.append(("CMR ledger reference unresolved refused",
                     "ledger-ref-unresolved" in codes))

    undisposed = _copy.deepcopy(base_cmr)
    del undisposed["records"][0]["disposition"]
    findings = rc_of(base_contract, base_peer, base_hints, cmr=undisposed)
    controls.append(("CMR record with no disposition reported by id",
                     any(f["code"] == "cmr-record-undisclosed" and f["id"] == "LESSON-001"
                         for f in findings)))

    bad_mirror = _copy.deepcopy(base_cmr)
    bad_mirror["records"][0] = {"id": "LESSON-001", "kind": "lesson", "status": "closed",
                                "disposition": "mirrors", "mirrors": "L-404"}
    findings = rc_of(base_contract, base_peer, base_hints, cmr=bad_mirror)
    controls.append(("CMR mirror with a missing local counterpart reported by id",
                     any(f["code"] == "local-counterpart-missing" and f["id"] == "LESSON-001"
                         for f in findings)))
    controls.append(("unconfirmed relationship may not assert a mapping",
                     any(f["code"] == "mapping-unconfirmed" for f in findings)))

    unaccounted = _copy.deepcopy(base_cmr)
    unaccounted["local_only"] = []
    findings = rc_of(base_contract, base_peer, base_hints, cmr=unaccounted)
    controls.append(("local record with no counterpart declaration reported by id",
                     any(f["code"] == "local-record-undisclosed" and f["id"] == "L-1"
                         for f in findings)))

    ok = True
    for name, passed in controls:
        print(f"  [{'PASS' if passed else 'FAIL'}] {name}")
        ok = ok and passed
    print("self-test: " + ("OK" if ok else "FAIL"))
    return OK if ok else NOT_OK


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="cross-repo lessons-sync contract gate (#424)")
    parser.add_argument("--root", default=os.getcwd(), help="repository root")
    parser.add_argument("--contract", default="governance/lessons-sync/contract.json")
    parser.add_argument("--peer", default="governance/lessons-sync/peer-issues.json")
    parser.add_argument("--hints", default="governance/lessons-sync/hints.json")
    parser.add_argument("--ledger", default=LEDGER_REL)
    parser.add_argument("--cmr", default="governance/lessons-sync/cmr-ledger.json",
                        help="the frozen CMR-hub ledger baseline (committed input)")
    parser.add_argument("--cmr-source", default=None,
                        help="override the live CMR index path (default "
                             "vendor/CMR/docs/LESSONS.md under --root)")
    parser.add_argument("--report", default=None, help="write the JSON report here")
    parser.add_argument("--live", action="store_true",
                        help="resolve the peer reference with live `gh` (needs network)")
    parser.add_argument("--verify-cmr-source", action="store_true",
                        help="re-resolve the frozen CMR baseline against the live "
                             "vendor/CMR/docs/LESSONS.md (CANNOT-ASSESS if absent)")
    parser.add_argument("--refresh-cmr-baseline", action="store_true",
                        help="regenerate the frozen CMR baseline from the live source "
                             "(run where vendor/CMR is populated)")
    parser.add_argument("--vendor-commit", default=None,
                        help="the vendor/CMR pin to record in the refreshed provenance")
    parser.add_argument("--extracted", default=None,
                        help="the extraction date to record in the refreshed provenance")
    parser.add_argument("--self-test", action="store_true",
                        help="provoke every refusal on in-memory inputs")
    args = parser.parse_args(argv)
    if args.refresh_cmr_baseline:
        return refresh_cmr_baseline(args.root, _cmr_source_path(args.root, args.cmr_source),
                                    _resolve(args.root, args.cmr),
                                    vendor_commit=args.vendor_commit,
                                    extracted=args.extracted)
    if args.self_test:
        return self_test()
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
