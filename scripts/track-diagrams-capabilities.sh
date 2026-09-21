#!/usr/bin/env bash
# diagrams capability tracker (issue #468, EPIC #462, sibling gate #469).
#
# Keeps `docs/DIAGRAMS-CAPABILITY-REGISTER.md` from going stale: it reads every
# vendor issue URL out of the register's table, reconciles each row's DECLARED
# status against the vendor issue's LIVE state in a recorded snapshot, and names
# every disagreement. A capability must not silently slide from `in-flight` to
# `shipped`, and it must not be called `shipped` because the issue closed.
#
# Tri-state, honest (GR-12, no false green):
#   0  OK            — every row's declared status survives the reconciliation
#                      (a row already declared `UNVERIFIED` on a closed issue
#                      with no cited completion signal is CORRECT, and is
#                      surfaced as a FINDING rather than hidden or failed)
#   1  NOT-OK        — at least one NAMED mismatch (see the classes below), or
#                      the register's generated reconciliation table is stale
#   2  CANNOT-ASSESS — no/unreadable register, no/unreadable or incomplete
#                      snapshot, an unparsable row, an unknown status, a `GAP`
#                      row with no filed direction, or `--live` without a
#                      working `gh`. Fails CLOSED: an absent register or an
#                      unparsable row is never reported as OK.
#
# Mismatch classes (each name is the tracker's, not the register's):
#   shipped-while-open        declared `shipped`, vendor issue still open
#   shipped-without-signal    declared `shipped`, no `completion signal:` cited
#   closed-without-signal     declared `in-flight`/`gap`, issue closed and no
#                             cited completion signal (the row must become
#                             `UNVERIFIED` — closure is not delivery)
#   closed-with-signal        declared `in-flight`/`gap`/`UNVERIFIED`, issue
#                             closed WITH a cited signal (the row is stale-low
#                             and has to be re-read, then flipped by a human)
#   unverified-while-open     declared `UNVERIFIED`, issue still open (the work
#                             is in flight, not unverifiable)
#   cited-signal-absent       the ref cites a comment id or a tag ref the
#                             snapshot does not record, so the citation cannot
#                             be re-read
#   register-section-stale    the register's committed reconciliation table
#                             differs from a fresh generation
#
# Offline and deterministic BY DEFAULT: the reconciliation runs entirely off the
# committed snapshot `scripts/fixtures/diagrams-vendor-board.snapshot.json`, with
# no network call, and two consecutive runs are byte-identical. The live read is
# opt-in (`--live`, or `AO_TRACK_DIAGRAMS_LIVE=1`) and is never required by a
# test or by `make verify`.
#
# The snapshot is a recording, not a second source of truth. Regenerating it:
#   scripts/track-diagrams-capabilities.sh --live --out <path>
# Regenerating the register's table after the board moves:
#   scripts/track-diagrams-capabilities.sh --emit-reconciliation-table
#
# `--self-test` runs internal negative controls: for each mismatch class it
# builds a single violation in a scratch directory (the real register and
# snapshot are never mutated) and asserts the tracker refuses it BY NAME, with
# every class backed by a control — an unbacked class is a formality, not a
# gate. It also asserts the controls' own baseline is rc 0, so a harness that
# simply always fails cannot pass the self-test.
#
# ---knowledge---
# module_id: scripts.track-diagrams-capabilities
# system: scripts
# app: scripts
# solution_class: pattern
# patterns: [tri-state-exit, provoked-negative-control, self-proving-gate, no-false-green, offline-hermetic, declared-authority, named-refusal, deterministic, schema-validation]
# derives_from: null
# owner_sme: platform-sme
# tier: L0
# interfaces: [exec python3 -]
# invariants: ""
# gotchas: ""
# related: ["#462", "#468", "#469"]
# do_not_duplicate: null
# ---knowledge---
set -u

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"
self="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"
exec python3 - "$root" "$self" "$@" <<'PY'
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

REGISTER_REL = "docs/DIAGRAMS-CAPABILITY-REGISTER.md"
SNAPSHOT_REL = "scripts/fixtures/diagrams-vendor-board.snapshot.json"
SNAPSHOT_SCHEMA = "ao.diagrams.vendor-board-snapshot/v1"
VENDOR_REPO = "kushin77/diagrams"
VENDOR_URL = "https://github.com/kushin77/diagrams/issues/"
STATUSES = ("shipped", "in-flight", "gap", "UNVERIFIED")
OWNERS = ("kushin77/diagrams", "us", "both")
TABLE_HEAD = "## The register"
SECTION_HEAD = "## Reconciliation against the vendor board"
LEAD_ISSUE = re.compile(r"^<?https://github\.com/kushin77/diagrams/issues/(\d+)>?")
ANY_ISSUE = re.compile(r"https://github\.com/kushin77/diagrams/issues/(\d+)")
HEADING = re.compile(r"^##\s")
USAGE = ("usage: track-diagrams-capabilities.sh [--register FILE] [--snapshot FILE]\n"
         "                                      [--live [--out FILE]]\n"
         "                                      [--emit-reconciliation-table] [--self-test]\n")


class CannotAssess(Exception):
    """Raised for every rc-2 condition; never downgraded to a pass."""


# --- register ---------------------------------------------------------------

def section_slice(lines, head):
    start = None
    for i, line in enumerate(lines):
        if line.strip() == head or line.startswith(head + " "):
            start = i
            break
    if start is None:
        return None
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if HEADING.match(lines[j]):
            end = j
            break
    return start, end


def split_cells(line):
    stripped = line.strip()
    if not stripped.startswith("|"):
        return None
    body = stripped[1:]
    if body.endswith("|"):
        body = body[:-1]
    return [c.strip() for c in body.split("|")]


def parse_ref(cell, row_id):
    """Return (vendor issue number, cited signal text, cited comment ids, cited tag refs)."""
    lead = LEAD_ISSUE.match(cell)
    if lead:
        number = int(lead.group(1))
    elif cell.startswith("GAP"):
        # Convention: a `GAP` row is only legal with a filed direction, and the
        # direction is recorded in the same cell. With no direction there is no
        # board object to reconcile, so the row cannot be assessed at all.
        later = ANY_ISSUE.search(cell)
        if not later:
            raise CannotAssess("row %s declares GAP with no filed direction URL in its "
                               "ref cell, so there is nothing to reconcile" % row_id)
        number = int(later.group(1))
    else:
        raise CannotAssess("row %s has an unparsable ref cell: %r" % (row_id, cell[:80]))

    signal = None
    hit = re.search(r"completion signal:", cell)
    if hit is not None:
        prefix = cell[max(0, hit.start() - 3):hit.start()].lower()
        if not prefix.endswith("no "):
            signal = cell[hit.end():].strip()
    comment_ids = [int(x) for x in re.findall(r"comment (\d+)", cell)]
    tag_refs = re.findall(r"refs/tags/[A-Za-z0-9._/-]+", cell)
    return number, signal, comment_ids, tag_refs


def parse_register(path):
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError as exc:
        raise CannotAssess("cannot read the register %s: %s" % (path, exc))
    lines = text.splitlines()
    span = section_slice(lines, TABLE_HEAD)
    if span is None:
        raise CannotAssess("the register %s has no %r section" % (path, TABLE_HEAD))
    start, end = span

    rows = []
    seen_header = False
    seen_ids = {}
    for offset, line in enumerate(lines[start + 1:end], start=start + 2):
        if not line.strip():
            continue
        cells = split_cells(line)
        if cells is None:
            continue
        if not seen_header:
            seen_header = True
            continue
        if all(set(c) <= set("-: ") for c in cells):
            continue
        if cells[0].lower() == "capability":
            continue
        if len(cells) != 7:
            raise CannotAssess("register row at line %d has %d cell(s), the seven-column "
                               "shape is expected" % (offset, len(cells)))
        row_id = cells[0].split(" ")[0]
        if not re.match(r"^C\d+$", row_id):
            raise CannotAssess("register row at line %d does not start with a C<number> "
                               "id: %r" % (offset, cells[0][:60]))
        if row_id in seen_ids:
            raise CannotAssess("rows %s and %s share the id %s" % (seen_ids[row_id], offset, row_id))
        seen_ids[row_id] = offset
        status = cells[4]
        if status not in STATUSES:
            raise CannotAssess("register row %s declares %r, not one of %s"
                               % (row_id, status, "/".join(STATUSES)))
        if cells[2] not in OWNERS:
            raise CannotAssess("register row %s declares owner %r, not one of %s"
                               % (row_id, cells[2], "/".join(OWNERS)))
        number, signal, comment_ids, tag_refs = parse_ref(cells[3], row_id)
        rows.append(dict(row=row_id, status=status, owner=cells[2], ref=cells[3],
                         number=number, signal=signal, comment_ids=comment_ids,
                         tag_refs=tag_refs, line=offset))
    if not rows:
        raise CannotAssess("the register %s yielded no capability row" % path)
    return text, rows


# --- snapshot ---------------------------------------------------------------

def load_snapshot(path):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except OSError as exc:
        raise CannotAssess("cannot read the snapshot %s: %s" % (path, exc))
    except ValueError as exc:
        raise CannotAssess("the snapshot %s is not readable JSON: %s" % (path, exc))
    if data.get("schema") != SNAPSHOT_SCHEMA:
        raise CannotAssess("the snapshot %s declares schema %r, not %r"
                           % (path, data.get("schema"), SNAPSHOT_SCHEMA))
    issues = data.get("issues")
    if not isinstance(issues, dict) or not issues:
        raise CannotAssess("the snapshot %s records no issue" % path)
    for key, entry in issues.items():
        if entry.get("state") not in ("open", "closed"):
            raise CannotAssess("the snapshot records issue %s with state %r"
                               % (key, entry.get("state")))
        ids = entry.get("comment_ids")
        if not isinstance(ids, list):
            raise CannotAssess("the snapshot records issue %s without a comment id list" % key)
        if entry.get("comment_count") != len(ids):
            raise CannotAssess("the snapshot records issue %s with %s comment(s) but lists %d "
                               "id(s), so a cited id cannot be checked"
                               % (key, entry.get("comment_count"), len(ids)))
    tags = data.get("tags") or {}
    if not isinstance(tags, dict):
        raise CannotAssess("the snapshot %s has a non-object tags map" % path)
    return data, issues, tags


def record_live(numbers, tag_refs):
    """Opt-in: read the vendor board through `gh` and return a snapshot object."""
    import datetime
    import urllib.parse

    if shutil.which("gh") is None:
        raise CannotAssess("--live needs the gh CLI on PATH; nothing was read")

    def api(path):
        proc = subprocess.run(["gh", "api", path], capture_output=True, text=True)
        if proc.returncode != 0:
            raise CannotAssess("--live read of %s failed (rc %d): %s"
                               % (path, proc.returncode, (proc.stderr or "").strip()[:200]))
        try:
            return json.loads(proc.stdout)
        except ValueError as exc:
            raise CannotAssess("--live read of %s returned unreadable JSON: %s" % (path, exc))

    issues = {}
    for number in sorted(set(numbers)):
        issue = api("repos/%s/issues/%d" % (VENDOR_REPO, number))
        comments = api("repos/%s/issues/%d/comments?per_page=100" % (VENDOR_REPO, number))
        issues[str(number)] = {
            "title": issue.get("title", ""),
            "state": issue.get("state"),
            "created_at": issue.get("created_at"),
            "closed_at": issue.get("closed_at"),
            "comment_count": issue.get("comments"),
            "comment_ids": sorted(c["id"] for c in comments),
        }
    tags = {}
    for ref in sorted(set(tag_refs)):
        encoded = urllib.parse.quote(ref, safe="")
        tags[ref] = api("repos/%s/git/ref/%s" % (VENDOR_REPO, encoded))["object"]["sha"]
    return {
        "schema": SNAPSHOT_SCHEMA,
        "vendor_repo": VENDOR_REPO,
        "recorded_at": datetime.date.today().isoformat(),
        "recorded_by": "scripts/track-diagrams-capabilities.sh --live (issue #468)",
        "recorded_with": ["gh api repos/%s/issues/<n>" % VENDOR_REPO,
                          "gh api 'repos/%s/issues/<n>/comments?per_page=100'" % VENDOR_REPO,
                          "gh api 'repos/%s/git/ref/<ref>'" % VENDOR_REPO],
        "note": ("A recorded read of the vendor board. The live path is opt-in; the committed "
                 "snapshot is what the offline reconciliation reads."),
        "tags": tags,
        "issues": issues,
    }


# --- reconciliation ---------------------------------------------------------

def reconcile(row, issues, tags):
    """Return (verdict_token, class_name, detail)."""
    entry = issues.get(str(row["number"]))
    if entry is None:
        raise CannotAssess("row %s cites %s#%d, which the snapshot does not record"
                           % (row["row"], VENDOR_REPO, row["number"]))
    closed = entry["state"] == "closed"
    absent = [c for c in row["comment_ids"] if c not in entry["comment_ids"]]
    absent += [t for t in row["tag_refs"] if t not in tags]
    if absent:
        return ("mismatch", "cited-signal-absent",
                "the ref cites %s, which the snapshot does not record on %s#%d"
                % (", ".join(str(a) for a in absent), VENDOR_REPO, row["number"]))
    if not closed:
        if row["status"] == "shipped":
            return ("mismatch", "shipped-while-open",
                    "declared shipped, %s#%d is open in the snapshot" % (VENDOR_REPO, row["number"]))
        if row["status"] == "UNVERIFIED":
            return ("mismatch", "unverified-while-open",
                    "declared UNVERIFIED, %s#%d is open in the snapshot, so the work is in "
                    "flight rather than unevidenced" % (VENDOR_REPO, row["number"]))
        return ("consistent", None, "%s and open: the declaration holds"
                % row["status"])
    if row["signal"]:
        if row["status"] == "shipped":
            return ("consistent", None, "shipped and closed with a cited completion signal")
        return ("mismatch", "closed-with-signal",
                "declared %s, but %s#%d is closed with a cited completion signal: re-read the "
                "signal and flip the row" % (row["status"], VENDOR_REPO, row["number"]))
    if row["status"] == "shipped":
        return ("mismatch", "shipped-without-signal",
                "declared shipped, but the ref cites no completion signal and %s#%d is closed"
                % (VENDOR_REPO, row["number"]))
    if row["status"] == "UNVERIFIED":
        return ("finding", "closed-without-signal",
                "declared UNVERIFIED and %s#%d is closed with no cited completion signal: the "
                "close is not evidence, and this row must NOT be flipped to shipped until a "
                "signal lands" % (VENDOR_REPO, row["number"]))
    return ("mismatch", "closed-without-signal",
            "declared %s, but %s#%d is closed with no cited completion signal: the row must "
            "become UNVERIFIED" % (row["status"], VENDOR_REPO, row["number"]))


def render_table(verdicts, rows, issues):
    out = ["| row | vendor issue | register status | vendor state | verdict |",
           "|---|---|---|---|---|"]
    for row in rows:
        state = issues[str(row["number"])]["state"]
        token, name, _ = verdicts[row["row"]]
        cell = token if name is None else "%s %s" % (token, name)
        out.append("| %s | [%s#%d](%s%d) | %s | %s | %s |"
                   % (row["row"], VENDOR_REPO, row["number"], VENDOR_URL, row["number"],
                      row["status"], state, cell))
    return out


def committed_table(text):
    """The reconciliation table as committed in the register, or None when absent."""
    lines = text.splitlines()
    span = section_slice(lines, SECTION_HEAD)
    if span is None:
        return None
    start, end = span
    table = []
    for line in lines[start + 1:end]:
        if line.strip().startswith("|"):
            table.append(line.strip())
        elif table:
            break
    return table


def evaluate(register_path, snapshot_path):
    text, rows = parse_register(register_path)
    data, issues, tags = load_snapshot(snapshot_path)
    verdicts = {}
    for row in rows:
        verdicts[row["row"]] = reconcile(row, issues, tags)
    table = render_table(verdicts, rows, issues)
    findings = [(r, verdicts[r["row"]]) for r in rows if verdicts[r["row"]][0] == "finding"]
    mismatches = [(r, verdicts[r["row"]]) for r in rows if verdicts[r["row"]][0] == "mismatch"]
    return dict(text=text, rows=rows, snapshot=data, issues=issues, tags=tags,
                verdicts=verdicts, table=table, findings=findings, mismatches=mismatches)


def describe(result, register_path, snapshot_path):
    data = result["snapshot"]
    numbers = sorted({r["number"] for r in result["rows"]})
    lines = ["diagrams capability tracker (issue #468)",
             "register: %s" % register_path,
             "snapshot: %s (schema %s, recorded %s from %s, %d issue(s))"
             % (snapshot_path, data.get("schema"), data.get("recorded_at"),
                data.get("vendor_repo"), len(result["issues"])),
             "rows: %d | vendor issues cited: %d | open: %d | closed: %d"
             % (len(result["rows"]), len(numbers),
                sum(1 for n in numbers if result["issues"][str(n)]["state"] == "open"),
                sum(1 for n in numbers if result["issues"][str(n)]["state"] == "closed")),
             ""]
    lines += result["table"]
    lines.append("")
    for row, (token, name, detail) in result["findings"]:
        lines.append("FINDING %s: %s (%s#%d) %s"
                     % (name, row["row"], VENDOR_REPO, row["number"], detail))
    for row, (token, name, detail) in result["mismatches"]:
        lines.append("MISMATCH %s: %s (%s#%d) %s"
                     % (name, row["row"], VENDOR_REPO, row["number"], detail))
    return lines


def run(register_path, snapshot_path):
    result = evaluate(register_path, snapshot_path)
    lines = describe(result, register_path, snapshot_path)

    committed = committed_table(result["text"])
    stale = None
    if committed is None:
        stale = "the register carries no %r section" % SECTION_HEAD
    elif committed != result["table"]:
        stale = ("the committed reconciliation table differs from a fresh generation "
                 "(run --emit-reconciliation-table and paste the result under %r)" % SECTION_HEAD)
    if stale:
        lines.append("MISMATCH register-section-stale: %s: %s" % (register_path, stale))

    for line in lines:
        print(line)
    classes = []
    for _row, (_token, name, _detail) in result["mismatches"]:
        if name not in classes:
            classes.append(name)
    if stale:
        classes.append("register-section-stale")
    if classes:
        sys.stderr.write("track-diagrams-capabilities: NOT-OK - %d mismatch(es): %s\n"
                         % (len(classes), ", ".join(classes)))
        return 1
    sys.stderr.write("track-diagrams-capabilities: OK - %d row(s) reconciled against %d vendor "
                     "issue(s); %d finding(s) surfaced, 0 mismatch(es)\n"
                     % (len(result["rows"]), len(result["issues"]), len(result["findings"])))
    return 0


# --- self-test --------------------------------------------------------------

C1_PREFIX = "| C1 · a per-repo declaration"
C2_PREFIX = "| C2 · extract a governed repo's real resource inventory"


def _reg_replace(old, new):
    def mutate(text, snap):
        if old not in text:
            raise AssertionError("control mutation text not found: %r" % old)
        return text.replace(old, new, 1), snap
    return mutate


def _flip_status(prefix, old_status, new_status):
    def mutate(text, snap):
        head, sep, tail = text.partition(prefix)
        if not sep:
            raise AssertionError("control mutation row not found: %r" % prefix)
        head2, sep2, tail2 = tail.partition("| %s |" % old_status)
        if not sep2:
            raise AssertionError("control mutation status %r not found after %r"
                                 % (old_status, prefix))
        return head + sep + head2 + "| %s |" % new_status + tail2, snap
    return mutate


def _snap_edit(fn):
    def mutate(text, snap):
        mutated = json.loads(json.dumps(snap))
        fn(mutated["issues"])
        return text, mutated
    return mutate


def _stale_section():
    """Make the committed reconciliation table differ, inside the section only.

    The anchor matters: `|---|---|---|---|---|` is also a substring of the
    register table's own seven-column separator, so an unanchored replacement
    edits the wrong table and the control passes vacuously.
    """
    def mutate(text, snap):
        head, sep, tail = text.partition(SECTION_HEAD)
        if not sep:
            raise AssertionError("control mutation: the reconciliation section is absent")
        old = "|---|---|---|---|---|"
        if old not in tail:
            raise AssertionError("control mutation: the section table separator is absent")
        return head + sep + tail.replace(old, "| --- | --- | --- | --- | --- |", 1), snap
    return mutate


def _control_cases():
    """(name, expected rc, expected named class, mutate(text, snapshot))."""

    def close_373(issues):
        issues["373"]["state"] = "closed"
        issues["373"]["closed_at"] = "2026-09-14T00:00:00Z"

    def drop_cited_comment(issues):
        issues["100"]["comment_ids"] = [i for i in issues["100"]["comment_ids"] if i != 5529493429]
        issues["100"]["comment_count"] = len(issues["100"]["comment_ids"])

    return [
        # The baseline is what stops the rest of this table from passing
        # vacuously: a harness that always returned 1 would fail it.
        ("baseline (the controls are calibrated on it)", 0, None, lambda t, s: (t, s)),
        ("shipped-while-open", 1, "shipped-while-open",
         _flip_status(C1_PREFIX, "in-flight", "shipped")),
        ("shipped-without-signal", 1, "shipped-without-signal",
         _reg_replace("completion signal: comment 5529493429",
                      "delivery observed: comment 5529493429")),
        ("closed-without-signal", 1, "closed-without-signal", _snap_edit(close_373)),
        ("closed-with-signal", 1, "closed-with-signal",
         _flip_status(C2_PREFIX, "shipped", "in-flight")),
        ("unverified-while-open", 1, "unverified-while-open",
         _flip_status(C1_PREFIX, "in-flight", "UNVERIFIED")),
        ("cited-signal-absent", 1, "cited-signal-absent", _snap_edit(drop_cited_comment)),
        ("register-section-stale", 1, "register-section-stale", _stale_section()),
    ]


def cmd_self_test(root, script, register_path, snapshot_path):
    with open(register_path, encoding="utf-8") as fh:
        register_text = fh.read()
    with open(snapshot_path, encoding="utf-8") as fh:
        snapshot = json.load(fh)

    failures = 0
    cases = _control_cases()
    for name, expected_rc, expected_class, mutate in cases:
        work = tempfile.mkdtemp(prefix="ao-diagrams-tracker-selftest.")
        try:
            try:
                text, snap = mutate(register_text, snapshot)
            except AssertionError as exc:
                print("  FAIL  %-42s mutation did not apply: %s" % (name, exc))
                failures += 1
                continue
            reg = os.path.join(work, "register.md")
            snp = os.path.join(work, "snapshot.json")
            with open(reg, "w", encoding="utf-8") as fh:
                fh.write(text)
            with open(snp, "w", encoding="utf-8") as fh:
                json.dump(snap, fh, indent=2, sort_keys=True)
                fh.write("\n")
            proc = subprocess.run(["/usr/bin/env", "bash", script, "--register", reg,
                                   "--snapshot", snp], capture_output=True, text=True)
            named = expected_class is None or expected_class in proc.stderr
            ok = proc.returncode == expected_rc and named
            print("  %s  %-42s rc=%d expects rc=%d%s"
                  % ("OK  " if ok else "FAIL", name, proc.returncode, expected_rc,
                     "" if named else " (class %r not named)" % expected_class))
            if not ok:
                failures += 1
                sys.stderr.write("      stderr: %s\n" % proc.stderr.strip())
        finally:
            shutil.rmtree(work, ignore_errors=True)

    # Fail-closed controls: nothing to reconcile must never read as OK.
    for name, args in (
        ("no snapshot -> CANNOT-ASSESS", ["--snapshot", "/nonexistent/snapshot.json"]),
        ("no register -> CANNOT-ASSESS", ["--register", "/nonexistent/register.md"]),
    ):
        proc = subprocess.run(["/usr/bin/env", "bash", script] + args,
                              capture_output=True, text=True)
        ok = proc.returncode == 2
        print("  %s  %-42s rc=%d expects rc=2" % ("OK  " if ok else "FAIL", name,
                                                  proc.returncode))
        if not ok:
            failures += 1
            sys.stderr.write("      stderr: %s\n" % proc.stderr.strip())

    total = len(cases) + 2
    if failures:
        sys.stderr.write("track-diagrams-capabilities self-test: FAIL - %d of %d control(s) not "
                         "refused\n" % (failures, total))
        return 1
    print("track-diagrams-capabilities self-test: OK - %d/%d control(s) refused by name"
          % (total, total))
    return 0


# --- main -------------------------------------------------------------------

def main():
    argv = sys.argv[1:]
    root = argv[0]
    script = argv[1]
    rest = argv[2:]
    register = os.path.join(root, REGISTER_REL)
    snapshot = os.path.join(root, SNAPSHOT_REL)
    mode = "check"
    live = os.environ.get("AO_TRACK_DIAGRAMS_LIVE", "").lower() in ("1", "true", "yes", "on")
    out = None
    i = 0
    while i < len(rest):
        arg = rest[i]
        if arg in ("-h", "--help"):
            sys.stdout.write(USAGE)
            return 0
        elif arg == "--register":
            i += 1
            if i >= len(rest):
                raise CannotAssess("--register needs a path")
            register = os.path.abspath(rest[i])
        elif arg == "--snapshot":
            i += 1
            if i >= len(rest):
                raise CannotAssess("--snapshot needs a path")
            snapshot = os.path.abspath(rest[i])
        elif arg == "--live":
            live = True
        elif arg == "--out":
            i += 1
            if i >= len(rest):
                raise CannotAssess("--out needs a path")
            out = os.path.abspath(rest[i])
        elif arg == "--emit-reconciliation-table":
            mode = "emit"
        elif arg == "--self-test":
            mode = "self-test"
        else:
            sys.stderr.write("track-diagrams-capabilities: unknown argument %r\n%s"
                             % (arg, USAGE))
            return 2
        i += 1

    if mode == "self-test":
        return cmd_self_test(root, script, register, snapshot)

    if live:
        _, rows = parse_register(register)
        recorded = record_live([r["number"] for r in rows],
                               [t for r in rows for t in r["tag_refs"]])
        if out:
            with open(out, "w", encoding="utf-8") as fh:
                json.dump(recorded, fh, indent=2, sort_keys=True, ensure_ascii=False)
                fh.write("\n")
            snapshot = out
            sys.stderr.write("track-diagrams-capabilities: recorded %d vendor issue(s) to %s\n"
                             % (len(recorded["issues"]), out))
        else:
            snapshot = os.path.join(tempfile.mkdtemp(prefix="ao-diagrams-tracker-live."),
                                    "snapshot.json")
            with open(snapshot, "w", encoding="utf-8") as fh:
                json.dump(recorded, fh, indent=2, sort_keys=True, ensure_ascii=False)
                fh.write("\n")

    if mode == "emit":
        result = evaluate(register, snapshot)
        print("\n".join(result["table"]))
        return 0

    return run(register, snapshot)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except CannotAssess as exc:
        sys.stderr.write("track-diagrams-capabilities: CANNOT-ASSESS - %s\n" % exc)
        sys.exit(2)
PY
