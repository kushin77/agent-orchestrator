#!/usr/bin/env python3
"""Scheduled peer-board triage for the cross-repo sync boundary (issue #427).

Issue #427 gives cross-repo sync an *owner and a machine surface*: a
code-native, cron-scheduled pass that reviews the peer repositories' boards and
produces a provenance-carrying report — instead of "the peers' boards point at
us" being something rediscovered when a lane happens to look.

The pass obeys the NG4 boundary (`docs/CROSS-REPO-SYNC-OWNER.md` §1–§2): this
repository never edits a peer's files. The only sanctioned way work crosses the
boundary is a **direction issue on the peer's board**, and this pass only
*reports* the direction issues it would file. It is dry-run by default, exactly
like this repository's standards-push tooling.

Design (from the issue's acceptance criteria):

- **Offline + deterministic by default.** The pass reads a pinned, committed
  board snapshot (``peer-board/snapshot.json``) plus the previous pass's
  snapshot (``peer-board/baseline.json``) and the authored dispositions
  (``peer-board/triages.json``). It never calls the network, never runs ``gh``
  and never uses the wall clock, so two runs against the same inputs produce
  byte-identical reports. A live-fetch mode exists behind ``--live`` and is
  never used by the gate.
- **Three explicit dispositions.** Every item the pass surfaces is either
  ``track-here`` (we own the half; records the local issue that consumes it),
  ``direction`` (they own it — a direction issue on their board, NG4) or
  ``no-action`` (with a reason). An item with **no disposition** is a finding:
  the pass never defaults to silence.
- **Provenance is mandatory.** Anything adopted from a peer carries repo, issue
  and SHA; an adopting disposition without a complete provenance record fails.
- **No silent adoption, no silent close.** A ``track-here`` disposition with no
  link back to the local issue that consumes the peer reference fails, and the
  pass **refuses** any run that would close a peer issue from here (cross-repo
  ``Closes`` is not used — the boundary hands off, it does not resolve).
- **Tri-state exit, honest.** ``0`` OK / ``1`` NOT-OK (findings) / ``2``
  CANNOT-ASSESS (snapshot/baseline/ownership unreadable, empty, or
  schema-mismatched). CANNOT-ASSESS is never ``0``.

The vocabulary (a report, per-item findings, an honest tri-state, dry-run
report-only posture) is the same one ``drift.py`` and ``sync_plan.py`` carry, so
this module reads as a sibling rather than a parallel invention. Those sibling
modules are consumed, never modified.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

SNAPSHOT_SCHEMA = "ao.sync/peer-board-snapshot-v1"
TRIAGE_SCHEMA = "ao.sync/peer-triage-v1"
OWNERSHIP_SCHEMA = "ao.sync/sync-ownership-v1"
REPORT_SCHEMA = "ao.sync/peer-triage-report-v1"

# The closed disposition vocabulary (issue #427 acceptance).
DISPOSITIONS = ("track-here", "direction", "no-action")

# Dispositions that consume a peer artifact, so provenance is mandatory.
ADOPTING = ("track-here", "direction")

# A content pin is a 64-hex sha256; a commit pin is a 40- or 64-hex sha.
HEX = set("0123456789abcdef")
SHA_LENGTHS = (40, 64)

# A `Closes`/`Fixes`/`Resolves <owner>/<repo>#<n>` reference naming a foreign
# repository. The boundary refuses to close a peer issue from here.
_PEER_CLOSE_RE = re.compile(
    r"\b(?:closes|fixes|resolves)\s+([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)#(\d+)",
    re.IGNORECASE)


class CannotAssess(Exception):
    """The pass cannot assess its inputs; the CLI maps this to exit code 2."""


def _is_hex(text, lengths):
    return (isinstance(text, str) and len(text) in lengths
            and all(c in HEX for c in text.lower()))


def _load_json(path, label):
    """Load one pinned JSON input, or raise ``CannotAssess`` (never a pass)."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
    except FileNotFoundError:
        raise CannotAssess("%s %s is missing" % (label, path))
    except OSError as exc:
        raise CannotAssess("%s %s is unreadable: %s" % (label, path, exc))
    except ValueError as exc:
        raise CannotAssess("%s %s is not valid JSON: %s" % (label, path, exc))
    if not isinstance(doc, dict):
        raise CannotAssess("%s %s is not a JSON object" % (label, path))
    return doc


def _labels(raw):
    """Normalise a peer issue's labels to a sorted list of names."""
    out = []
    for item in raw or []:
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, dict) and item.get("name"):
            out.append(str(item["name"]))
    return sorted(set(out))


def index_issues(doc, label):
    """Index a board snapshot to ``{'owner/repo#n': issue_record}``.

    The record carries only what the pass reasons over (repo, number, title,
    state, body, labels) so the report is a pure function of the pinned input.
    """
    if doc.get("schema") != SNAPSHOT_SCHEMA:
        raise CannotAssess(
            "%s has schema '%s' (expected '%s')"
            % (label, doc.get("schema"), SNAPSHOT_SCHEMA))
    peers = doc.get("peers")
    if not isinstance(peers, list) or not peers:
        raise CannotAssess("%s declares no peers (an empty board cannot be "
                           "assessed)" % label)
    index = {}
    for peer in peers:
        if not isinstance(peer, dict):
            raise CannotAssess("%s has a peer entry that is not an object"
                               % label)
        repo = peer.get("repo")
        if not isinstance(repo, str) or "/" not in repo:
            raise CannotAssess("%s has a peer with no owner/repo 'repo' field"
                               % label)
        issues = peer.get("issues")
        if not isinstance(issues, list):
            raise CannotAssess("%s peer %s has no 'issues' list"
                               % (label, repo))
        for issue in issues:
            if not isinstance(issue, dict) or not isinstance(
                    issue.get("number"), int):
                raise CannotAssess(
                    "%s peer %s has an issue with no integer 'number'"
                    % (label, repo))
            ref = "%s#%d" % (repo, issue["number"])
            index[ref] = {
                "repo": repo,
                "number": issue["number"],
                "title": issue.get("title") or "",
                "state": str(issue.get("state") or "unknown").lower(),
                "body": issue.get("body") or "",
                "labels": _labels(issue.get("labels")),
            }
    if not index:
        raise CannotAssess("%s contains no issues (nothing to assess)" % label)
    return index


def load_ownership(doc):
    """Validate the ownership map; raise ``CannotAssess`` on a bad shape."""
    if doc.get("schema") != OWNERSHIP_SCHEMA:
        raise CannotAssess(
            "ownership schema is '%s' (expected '%s')"
            % (doc.get("schema"), OWNERSHIP_SCHEMA))
    owner_repo = doc.get("owner_repo")
    if not isinstance(owner_repo, str) or "/" not in owner_repo:
        raise CannotAssess("ownership.owner_repo is not owner/repo form")
    tokens = doc.get("name_tokens")
    if not isinstance(tokens, list) or not tokens:
        raise CannotAssess("ownership.name_tokens must be a non-empty list")
    lanes = doc.get("lanes")
    if not isinstance(lanes, dict) or not lanes:
        raise CannotAssess("ownership.lanes must be a non-empty mapping")
    peers = doc.get("peers")
    if not isinstance(peers, list) or not peers:
        raise CannotAssess("ownership.peers must be a non-empty list")
    return {
        "owner_repo": owner_repo,
        "name_tokens": [str(t) for t in tokens],
        "lanes": {str(k): str(v) for k, v in lanes.items()},
        "peers": [str(p) for p in peers],
    }


def load_triages(doc):
    """Validate the triage ledger; return the ``ref -> item`` mapping."""
    if doc.get("schema") != TRIAGE_SCHEMA:
        raise CannotAssess(
            "triages schema is '%s' (expected '%s')"
            % (doc.get("schema"), TRIAGE_SCHEMA))
    items = doc.get("items")
    if not isinstance(items, dict):
        raise CannotAssess("triages.items must be a mapping")
    return items


def classify(issue, ownership):
    """Return ``(named_us, lanes)`` for one peer issue.

    An issue is surfaced as a candidate when it either *names us* (a name token
    appears in its title/body) or *maps onto a lane we own* (it carries a label
    that ownership.lanes maps to one of our lanes).
    """
    blob = ("%s\n%s" % (issue["title"], issue["body"])).lower()
    named = any(token.lower() in blob for token in ownership["name_tokens"])
    lanes = sorted({ownership["lanes"][label]
                    for label in issue["labels"]
                    if label in ownership["lanes"]})
    return named, lanes


def _provenance_finding(ref, provenance, issue):
    """Return a finding when an adopting disposition's provenance is bad."""
    if not isinstance(provenance, dict):
        return ("missing-provenance", ref,
                "peer artifact '%s' is adopted with no provenance record "
                "(repo + issue + sha are mandatory)" % ref)
    repo = provenance.get("repo")
    number = provenance.get("issue")
    sha = provenance.get("sha")
    if not isinstance(repo, str) or "/" not in repo:
        return ("missing-provenance", ref,
                "peer artifact '%s' records no source repo (owner/repo)"
                % ref)
    if not isinstance(number, int):
        return ("missing-provenance", ref,
                "peer artifact '%s' records no source issue number" % ref)
    if not _is_hex(sha, SHA_LENGTHS):
        return ("missing-provenance", ref,
                "peer artifact '%s' records no 40/64-hex sha pin" % ref)
    if repo != issue["repo"] or number != issue["number"]:
        return ("bad-provenance", ref,
                "peer artifact '%s' provenance names %s#%s, not the adopted "
                "item" % (ref, repo, number))
    return None


def _close_refusal(ref, item):
    """Return a finding when a disposition would close a peer issue here."""
    if item.get("closes_peer") or item.get("closes") is not None:
        return ("peer-close-refused", ref,
                "refused: a report may not close a peer issue from here "
                "(cross-repo Closes is not used)")
    direction = item.get("direction") or {}
    haystack = " ".join(str(part) for part in (
        item.get("reason"), direction.get("title"), direction.get("body"),
        direction.get("ref")) if part)
    match = _PEER_CLOSE_RE.search(haystack)
    if match:
        return ("peer-close-refused", ref,
                "refused: disposition for '%s' would close %s#%s from here "
                "(cross-repo Closes is not used)"
                % (ref, match.group(1), match.group(2)))
    return None


def _assess_candidate(ref, issue, triage, owner_repo, findings, directions):
    """Validate one candidate's disposition; append findings + directions."""
    if triage is None or not isinstance(triage, dict):
        findings.append(("no-disposition", ref,
                         "peer item '%s' has no disposition (track-here / "
                         "direction / no-action) — silence is not a "
                         "disposition" % ref))
        return None

    disposition = triage.get("disposition")
    if disposition not in DISPOSITIONS:
        findings.append(("bad-disposition", ref,
                         "peer item '%s' has disposition %r, not one of %s"
                         % (ref, disposition, list(DISPOSITIONS))))
        return None

    reason = triage.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        findings.append(("no-reason", ref,
                         "disposition '%s' for peer item '%s' carries no reason"
                         % (disposition, ref)))

    refusal = _close_refusal(ref, triage)
    if refusal:
        findings.append(refusal)
        return disposition

    if disposition in ADOPTING:
        finding = _provenance_finding(ref, triage.get("provenance"), issue)
        if finding:
            findings.append(finding)

    if disposition == "track-here":
        local_issue = triage.get("local_issue")
        if not isinstance(local_issue, int):
            findings.append((
                "no-link-back", ref,
                "peer item '%s' is tracked here but references no local issue "
                "that consumes it (silent adoption)" % ref))

    if disposition == "direction":
        direction = triage.get("direction")
        if not isinstance(direction, dict):
            findings.append(("no-direction-record", ref,
                             "peer item '%s' is a direction hand-off but "
                             "records no direction issue" % ref))
        else:
            target = direction.get("target_repo")
            title = direction.get("title")
            if not isinstance(target, str) or "/" not in target:
                findings.append(("bad-direction", ref,
                                 "direction for '%s' names no target repo"
                                 % ref))
            elif target == owner_repo:
                findings.append(("bad-direction", ref,
                                 "direction for '%s' targets our own repo; a "
                                 "direction issue belongs on the peer's board"
                                 % ref))
            if not isinstance(title, str) or not title.strip():
                findings.append(("bad-direction", ref,
                                 "direction for '%s' carries no title" % ref))
            directions.append({
                "ref": ref,
                "target_repo": target,
                "title": title,
                "labels": sorted(str(x) for x in
                                 (direction.get("labels") or [])),
                "ref_id": direction.get("ref"),
                "status": direction.get("status"),
                "filed": False,
            })
    return disposition


def run_pass(snapshot, baseline, triages, ownership, generated=None):
    """Produce the deterministic peer-triage report (never raises).

    ``snapshot``/``baseline`` are ``ref -> issue`` index maps (from
    ``index_issues``); ``generated`` carries each board's own ``generated``
    stamp so the report never reads the wall clock.
    """
    generated = generated or {}
    owner_repo = ownership["owner_repo"]
    findings = []
    directions = []

    baseline_refs = set(baseline)
    peers = {}
    for ref in sorted(set(snapshot) | baseline_refs):
        issue = snapshot.get(ref)
        repo = (issue or baseline[ref])["repo"]
        peers.setdefault(repo, {
            "repo": repo,
            "opened_since_last_pass": [],
            "closed_since_last_pass": [],
            "candidates": [],
        })

    for ref, issue in sorted(snapshot.items()):
        peer = peers[issue["repo"]]
        if ref not in baseline_refs:
            peer["opened_since_last_pass"].append(issue["number"])
        elif issue["state"] == "closed" and baseline[ref]["state"] != "closed":
            peer["closed_since_last_pass"].append(issue["number"])

    counts = {disposition: 0 for disposition in DISPOSITIONS}
    candidates_total = 0
    for ref, issue in sorted(snapshot.items()):
        if issue["state"] != "open":
            continue
        named, lanes = classify(issue, ownership)
        if not (named or lanes):
            continue
        candidates_total += 1
        triage = triages.get(ref)
        disposition = _assess_candidate(
            ref, issue, triage, owner_repo, findings, directions)
        if disposition:
            counts[disposition] += 1
        peer = peers[issue["repo"]]
        peer["candidates"].append({
            "ref": ref,
            "repo": issue["repo"],
            "number": issue["number"],
            "named_us": named,
            "lanes": lanes,
            "disposition": disposition,
            "local_issue": (triage or {}).get("local_issue"),
            "provenance": (triage or {}).get("provenance"),
        })

    orphan = sorted(ref for ref in triages if ref not in snapshot)
    findings = [{"code": code, "ref": ref, "message": message}
                for code, ref, message in findings]

    return {
        "schema": REPORT_SCHEMA,
        "generated": generated.get("snapshot"),
        "owner_repo": owner_repo,
        "pass": {
            "snapshot_generated": generated.get("snapshot"),
            "baseline_generated": generated.get("baseline"),
        },
        "peers": [peers[repo] for repo in sorted(peers)],
        "summary": {
            "candidates": candidates_total,
            "track-here": counts["track-here"],
            "direction": counts["direction"],
            "no-action": counts["no-action"],
            "direction_issues_to_file": len(directions),
            "findings": len(findings),
        },
        "direction_issues_to_file": directions,
        "orphan_dispositions": orphan,
        "findings": findings,
        "verdict": "not-ok" if findings else "ok",
    }


def _report_exit(report):
    """The honest exit code for a report: 0 OK / 1 NOT-OK."""
    return 1 if report["findings"] else 0


def _default_dir():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "peer-board")


def _default_paths():
    base = _default_dir()
    return {
        "snapshot": os.path.join(base, "snapshot.json"),
        "baseline": os.path.join(base, "baseline.json"),
        "triages": os.path.join(base, "triages.json"),
        "ownership": os.path.join(base, "ownership.json"),
    }


def load_inputs(snapshot_path, baseline_path, triages_path, ownership_path):
    """Load + validate every pinned input.

    Returns ``(snapshot, baseline, triages, ownership, generated)`` where the
    first two are index maps and ``generated`` carries each board's own stamp.
    Raises ``CannotAssess`` on any gap — never a pass.
    """
    ownership = load_ownership(_load_json(ownership_path, "ownership"))
    snapshot_doc = _load_json(snapshot_path, "snapshot")
    snapshot = index_issues(snapshot_doc, "snapshot")
    baseline_doc = _load_json(baseline_path, "baseline")
    baseline = index_issues(baseline_doc, "baseline")
    triages = load_triages(_load_json(triages_path, "triages"))
    generated = {"snapshot": snapshot_doc.get("generated"),
                 "baseline": baseline_doc.get("generated")}
    return snapshot, baseline, triages, ownership, generated


# --- live fetch (explicit flag only; never used by the gate) ----------------
def _gh_runner(repo):
    """Fetch one peer's issues via ``gh`` (one API read per peer)."""
    proc = subprocess.run(
        ["gh", "issue", "list", "--repo", repo, "--state", "all",
         "--limit", "1000",
         "--json", "number,title,state,body,updatedAt,labels"],
        capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise CannotAssess("gh issue list failed for %s: %s"
                           % (repo, proc.stderr.strip()))
    return proc.stdout


def fetch_snapshot(repos, generated, runner=None):
    """Build a snapshot from live peer boards (injectable runner for tests)."""
    runner = runner or _gh_runner
    peers = []
    for repo in repos:
        try:
            issues = json.loads(runner(repo))
        except ValueError as exc:
            raise CannotAssess("peer %s returned unparseable JSON: %s"
                               % (repo, exc))
        if not isinstance(issues, list):
            raise CannotAssess("peer %s returned no issue list" % repo)
        peers.append({"repo": repo, "issues": issues})
    return {"schema": SNAPSHOT_SCHEMA, "generated": generated,
            "source": "live", "peers": peers}


def _dumps(obj):
    return json.dumps(obj, indent=2, sort_keys=True) + "\n"


def _print_human(report):
    summary = report["summary"]
    print("peer-triage: %s — %d candidate(s) (%d track-here / %d direction / "
          "%d no-action), %d direction issue(s) to file (dry-run), %d finding(s)"
          % (report["verdict"].upper(), summary["candidates"],
             summary["track-here"], summary["direction"], summary["no-action"],
             summary["direction_issues_to_file"], summary["findings"]))
    for peer in report["peers"]:
        print("  %s: +%d opened / -%d closed since last pass, %d candidate(s)"
              % (peer["repo"], len(peer["opened_since_last_pass"]),
                 len(peer["closed_since_last_pass"]), len(peer["candidates"])))
    for finding in report["findings"]:
        print("  FAIL %-20s %s: %s"
              % (finding["code"], finding["ref"], finding["message"]))
    for direction in report["direction_issues_to_file"]:
        print("  DIRECTION (dry-run, filed=%s) %s -> %s: %s"
              % (direction["filed"], direction["ref"],
                 direction["target_repo"], direction["title"]))


def main(argv=None):
    paths = _default_paths()
    parser = argparse.ArgumentParser(
        description="Scheduled peer-board triage for the cross-repo sync "
                    "boundary (issue #427). Offline + deterministic by default.")
    parser.add_argument("--snapshot", default=paths["snapshot"])
    parser.add_argument("--baseline", default=paths["baseline"])
    parser.add_argument("--triages", default=paths["triages"])
    parser.add_argument("--ownership", default=paths["ownership"])
    parser.add_argument("--report", help="write the report JSON to this path")
    parser.add_argument("--json", action="store_true",
                        help="print the report JSON to stdout")
    parser.add_argument("--quiet", action="store_true",
                        help="suppress the human summary (exit code only)")
    parser.add_argument("--live", action="store_true",
                        help="fetch peer boards live via gh (NOT the gated "
                             "mode: the default is pinned + offline)")
    parser.add_argument("--live-generated", default="live",
                        help="stamp for a --live snapshot's generated field")
    args = parser.parse_args(argv)

    try:
        ownership = load_ownership(_load_json(args.ownership, "ownership"))
        if args.live:
            snapshot_doc = fetch_snapshot(ownership["peers"],
                                          args.live_generated)
        else:
            snapshot_doc = _load_json(args.snapshot, "snapshot")
        snapshot = index_issues(snapshot_doc, "snapshot")
        baseline_doc = _load_json(args.baseline, "baseline")
        baseline = index_issues(baseline_doc, "baseline")
        triages = load_triages(_load_json(args.triages, "triages"))
    except CannotAssess as exc:
        print("peer-triage: CANNOT-ASSESS — %s" % exc, file=sys.stderr)
        return 2

    generated = {"snapshot": snapshot_doc.get("generated"),
                 "baseline": baseline_doc.get("generated")}
    report = run_pass(snapshot, baseline, triages, ownership,
                      generated=generated)
    payload = _dumps(report)
    if args.report:
        with open(args.report, "w", encoding="utf-8") as fh:
            fh.write(payload)
    if args.json:
        sys.stdout.write(payload)
    if not args.quiet and not args.json:
        _print_human(report)
    return _report_exit(report)


if __name__ == "__main__":
    sys.exit(main())
