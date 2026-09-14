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
      auto-close, so it must never be written by accident).

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
    if len(derived) != 1:
        findings.append(_finding(
            "contract-invalid", "contract",
            f"expected exactly one '{DERIVED_ROLE}', found {len(derived)} ({sorted(derived)})"))
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
    derived = next((v for v in contract.values()
                    if isinstance(v, dict) and v.get("role") == DERIVED_ROLE), None)
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


def evaluate(contract: dict[str, Any], peer: dict[str, Any], hints_doc: Any,
             ledger_ids: set[str], *, commit_exists: Callable[[str], bool] | None = None,
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

    live = _gh_issue_exists if args.live else None
    try:
        contract = _load_json(contract_path)
        peer = _load_json(peer_path)
        hints_doc = _load_json(hints_path)
        ledger_ids = _load_ledger_ids(ledger_path)
        if not isinstance(contract, dict):
            raise InputError("contract input is not an object")
        if not isinstance(peer, dict):
            raise InputError("peer snapshot is not an object")
        findings = evaluate(contract, peer, hints_doc, ledger_ids,
                            commit_exists=_git_commit_exists, live=live)
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
            "live": bool(args.live),
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
        "authoritative": {"repo": OUR_REPO, "role": WRITER_ROLE, "ledger": "governance/lessons/ledger.jsonl"},
        "derived": {"repo": "kushin77/deepseek", "role": DERIVED_ROLE,
                    "ledger_ref": {"repo": "kushin77/deepseek", "issue": 84}},
        "sync": {"direction": "one-way:writer->derived", "symmetric": False},
    }
    base_peer = {"repo": "kushin77/deepseek", "issues": [{"number": 84, "state": "open"}],
                 "peer_lessons": []}
    base_hints = {"hints": [{"id": "hint-0001", "lesson_id": "L-1", "issue": "#402",
                             "commit": "b87cdf9"}]}
    ledger = {"L-1"}

    def rc_of(contract, peer, hints, ids=ledger):
        return evaluate(contract, peer, hints, set(ids), commit_exists=lambda _s: True)

    controls = []
    controls.append(("baseline clean", rc_of(base_contract, base_peer, base_hints) == []))

    import copy as _copy
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
    parser.add_argument("--ledger", default="governance/lessons/ledger.jsonl")
    parser.add_argument("--report", default=None, help="write the JSON report here")
    parser.add_argument("--live", action="store_true",
                        help="resolve the peer reference with live `gh` (needs network)")
    parser.add_argument("--self-test", action="store_true",
                        help="provoke every refusal on in-memory inputs")
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
