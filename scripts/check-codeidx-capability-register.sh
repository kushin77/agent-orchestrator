#!/usr/bin/env bash
# codeidx-capability-register gate (issue #480, parent EPIC #473; GR-29).
#
# Makes `docs/CODEIDX-CAPABILITY-REGISTER.md` BINDING rather than advisory: a
# rule that lives only in a doc is a suggestion, a gate that fails BY NAME is a
# policy. Every refusal below names the offending row.
#
# Six refusals (each rc 1, each naming the offending row):
#   1  owner        - blank or unknown; must be `kushin77/code-indexing`, `us` or `both`
#   2  acceptance   - missing; a row must carry a criterion that can fail
#   3  ref          - neither the literal `GAP` nor a real issue URL
#   4  shipped      - status `shipped` cites no completion signal (`closed=` + `signal=`)
#   5  gap          - status `gap` cites no filed direction issue (the ref is `GAP`)
#   6  #472 coverage- a capability EPIC #472 names is absent from the mapping, a
#                     mapping row names a register row that does not exist, or the
#                     mapping table is empty
#
# `UNVERIFIED` is an HONEST state, not a violation: the register deliberately
# holds a row we already consume whose provider has not published the contract.
# Only `shipped` must cite a completion signal and only `gap` must cite a filed
# direction - flagging `UNVERIFIED` would be this gate's false-positive failure
# mode, so the rule-4 test is `status == "shipped"` and nothing wider.
#
# The register table is found BY ITS HEADER (`capability | needed-for | owner |
# ref | status | acceptance | verify`), never as "the only table in the file":
# the document legitimately carries several other supporting tables (row-shape
# spec, status vocabulary, id ranges, the direction set, the #472 mapping,
# measured provenance), and the sibling reconciliation lane (#479) appends a
# further, differently-headed section. Only the table with this header is the
# register. A register table with ZERO rows is a violation, not a pass.
#
# Exit contract (tri-state, honest): 0 conformant / 1 NOT-OK / 2 CANNOT-ASSESS.
# CANNOT-ASSESS is never a pass. Offline and deterministic by default.
#
# `--epic-live` (opt-in, NETWORK) re-anchors the frozen EPIC #472 capability set
# against the live EPIC body, so the transcription below is re-verifiable rather
# than trusted. Without it the default path touches no network.
#
# Usage:
#   scripts/check-codeidx-capability-register.sh              # validate this repo
#   scripts/check-codeidx-capability-register.sh --self-test  # internal controls
#   scripts/check-codeidx-capability-register.sh --epic-live  # live re-anchor
#   scripts/check-codeidx-capability-register.sh --register PATH  # validate an
#       alternate copy of the register (e.g. the subject as the sibling
#       reconciliation lane #479 will merge it), never editing the tracked file
set -u

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-codeidx-capability-register: CANNOT-ASSESS - python3 unavailable" >&2
  exit 2
fi

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"
exec python3 - "$root" "$@" <<'PY'
import os
import re
import subprocess
import sys

REGISTER_REL = os.path.join("docs", "CODEIDX-CAPABILITY-REGISTER.md")
REGISTER_URL = REGISTER_REL.replace(os.sep, "/")

TABLE_HEADER = (
    "capability", "needed-for", "owner", "ref", "status", "acceptance", "verify",
)
MAPPING_HEADER = ("capability #472 relies on", "rows")

OWNERS = ("kushin77/code-indexing", "us", "both")
STATUSES = ("shipped", "in-flight", "gap", "UNVERIFIED")
PLACEHOLDERS = ("-", "--", "---", "\u2014", "\u2013", "n/a", "na", "none", "?", "...")

ROW_ID_RE = re.compile(r"\bC-\d{2,3}\b")
SEPARATOR_RE = re.compile(r"^:?-+:?$")
LINK_RE = re.compile(
    r"^\[[^\]\s]+\]\(https://github\.com/[^/\s]+/[^/\s]+/(?:issues|pull)/\d+\)$"
)

EPIC_ISSUE = 472
EPIC_REPO = "kushin77/agent-orchestrator"

# --- the capability set EPIC #472 itself names ------------------------------
#
# Transcribed from the live EPIC #472 body
# (https://github.com/kushin77/agent-orchestrator/issues/472, read 2026-09-14)
# by the enforce lane (#480). `reg` is the phrase set that identifies the
# register's #472 mapping row carrying the capability; `epic` is the (verbatim,
# post-normalisation) phrase set that proves EPIC #472 names it. The `epic`
# anchors are what `--epic-live` re-checks against the live body, so this table
# cannot silently drift into invention: every entry names where the EPIC says it.
EPIC_472_CAPABILITIES = (
    (  # Objective: "as the org-wide **code-location** SSOT on the agent surface"
        "the org-wide code-location SSOT on the agent surface",
        ("org-wide", "code-location"),
        ("org-wide", "code-location", "ssot"),
    ),
    (  # Objective: "the fleet's compiler-accurate, no-LLM symbol index"
        "compiler-accurate, no-LLM, reproducible answers",
        ("compiler-accurate", "no-llm"),
        ("compiler-accurate", "no-llm", "reproducible from compiler output"),
    ),
    (  # authority table: "one `index.db`, atomic symlink publish"
        "one index.db published by atomic symlink swap",
        ("index.db", "atomic"),
        ("index.db", "atomic symlink publish"),
    ),
    (  # measured position: "the MCP gateway re-implements codeidx tool shapes"
        "the MCP tool shapes as a contract",
        ("tool shapes", "contract"),
        ("mcp gateway", "tool shapes"),
    ),
    (  # authority table: "Where is symbol X, and who references it?"
        "reference lookup that cannot report a false absence",
        ("false absence",),
        ("who references it",),
    ),
    (  # measured position: "(definitions / references / search / query / freshness)"
        "per-repo index freshness",
        ("per-repo index freshness",),
        ("freshness",),
    ),
    (  # measured position: "publishing the `codeidx.context-pack/v1` contract"
        "the codeidx.context-pack/v1 consumption contract",
        ("context-pack/v1",),
        ("codeidx.context-pack/v1", "consumption contract"),
    ),
    (  # definition of done: "`assemble_prefix` ... consumes a pre-fetched pack"
        "a pre-fetched context pack consumed by assemble_prefix",
        ("pre-fetched context pack", "assemble_prefix"),
        ("pre-fetched", "assemble_prefix"),
    ),
    (  # measured position: "does not carry the GR-17 mandatory consumer surface"
        "the GR-17 mandatory consumer surface (.mcp.json, gdc-manifest.yaml pin)",
        ("mandatory consumer surface", ".mcp.json", "gdc-manifest.yaml"),
        ("gr-17 mandatory consumer surface", "mandatory_consumer_assets", "gdc-manifest.yaml"),
    ),
    (  # will-not-do: "The real-backend path ships **flag-gated OFF**"
        "the real backend behind a flag-gated OFF seam",
        ("flag-gated off",),
        ("real-backend path ships", "flag-gated off"),
    ),
    (  # children table: "the two-index authority split + the no-re-derivation rule"
        "the two-index authority split and the frozen no-re-derivation rule",
        ("authority split", "no-re-derivation"),
        ("authority split", "no-re-derivation rule"),
    ),
    (  # objective: "closing the two cross-repo lessons this repo was sent"
        "the two cross-repo lessons naming this repo (#129, #130)",
        ("#129", "#130"),
        ("two cross-repo lessons", "#129", "#130"),
    ),
    (  # definition of done: "wired into `make verify`"
        "the codeidx gates wired into the gate of record",
        ("gate of record",),
        ("mutation-proven", "wired into make verify"),
    ),
)


def norm(text):
    """Fold markdown emphasis, backticks and whitespace for phrase matching.

    Underscores are preserved: they are part of identifier-shaped tokens this
    register cites (`assemble_prefix`, `mandatory_consumer_assets`), not emphasis.
    """
    flat = text.replace("`", "").replace("*", "")
    return re.sub(r"\s+", " ", flat).strip().lower()


def split_row(line):
    """Return the cells of a markdown table line, or None if it is not one."""
    stripped = line.strip()
    if not stripped.startswith("|"):
        return None
    body = stripped[1:]
    if body.endswith("|"):
        body = body[:-1]
    return [cell.strip() for cell in body.split("|")]


def parse_tables(text):
    """Every markdown table in `text`, as (header_line, header_cells, rows).

    A table is a row followed by a separator row of the same arity; its body runs
    to the first blank or non-table line. This is how the register table is found
    by HEADER rather than by being the only table in the document.
    """
    lines = text.split("\n")
    tables = []
    i = 0
    while i < len(lines):
        header = split_row(lines[i])
        separator = split_row(lines[i + 1]) if i + 1 < len(lines) else None
        if (
            header
            and separator
            and len(separator) == len(header)
            and all(SEPARATOR_RE.match(cell) for cell in separator)
        ):
            rows = []
            j = i + 2
            while j < len(lines):
                cells = split_row(lines[j])
                if cells is None:
                    break
                rows.append((j + 1, cells))
                j += 1
            tables.append((i + 1, header, rows))
            i = j
            continue
        i += 1
    return tables


def find_table(tables, header):
    """The first table whose header cells match `header` (case-insensitive)."""
    wanted = tuple(cell.lower() for cell in header)
    for line, cells, rows in tables:
        if tuple(cell.lower() for cell in cells) == wanted:
            return {"line": line, "header": cells, "rows": rows}
    return None


def first_token(cell):
    parts = cell.split()
    return parts[0] if parts else ""


def ref_is_link(cell):
    return bool(LINK_RE.match(first_token(cell)))


def check_rows(rows, messages, subject):
    """Rules 1-3 on every register row; rules 4-5 keyed strictly on the status."""
    for line, cells in rows:
        if len(cells) != len(TABLE_HEADER):
            messages.append(
                "%s: line %d: register row has %d cell(s), expected %d - a row is "
                "%s"
                % (
                    subject,
                    line,
                    len(cells),
                    len(TABLE_HEADER),
                    " | ".join(TABLE_HEADER),
                )
            )
            continue
        capability, _needed_for, owner, ref, status, acceptance, verify = cells
        match = ROW_ID_RE.search(capability)
        row_id = match.group(0) if match else None
        row_label = row_id or "line %d" % line
        where = "%s: %s" % (subject, row_label)

        if row_id is None:
            messages.append(
                "%s: the capability cell names no row id (C-nn): %r"
                % (where, capability[:80])
            )
        if not verify:
            messages.append(
                "%s: the verify cell is empty - a row must carry the command that "
                "checks it" % where
            )
        if status not in STATUSES:
            messages.append(
                "%s: status %r is outside the vocabulary (%s)"
                % (where, status, " | ".join(STATUSES))
            )

        # rule 1 - owner
        if not owner:
            messages.append(
                "%s: rule 1 owner: MISSING - the owner cell is blank; it must be "
                "one of %s" % (where, " | ".join(OWNERS))
            )
        elif owner not in OWNERS:
            messages.append(
                "%s: rule 1 owner: UNKNOWN value %r; it must be one of %s"
                % (where, owner, " | ".join(OWNERS))
            )

        # rule 2 - acceptance
        if not acceptance or acceptance in PLACEHOLDERS:
            messages.append(
                "%s: rule 2 acceptance: MISSING - the row carries no criterion that "
                "can fail" % where
            )
        elif len(acceptance.split()) < 3:
            messages.append(
                "%s: rule 2 acceptance: %r is not an observable - the criterion must "
                "be a statement a reviewer can falsify" % (where, acceptance)
            )

        # rule 3 - ref
        if ref != "GAP" and not ref_is_link(ref):
            messages.append(
                "%s: rule 3 ref: %r is neither the literal GAP nor an issue URL"
                % (where, first_token(ref) or ref)
            )

        # rule 4 - a shipped row must cite its completion signal
        if status == "shipped":
            if "signal=" not in ref:
                messages.append(
                    "%s: rule 4: status shipped cites no completion signal - the ref "
                    "carries no signal= clause (a closed vendor issue is not itself a "
                    "completion signal)" % where
                )
            elif "closed=" not in ref:
                messages.append(
                    "%s: rule 4: status shipped cites no closed= date for its "
                    "completion signal" % where
                )

        # rule 5 - a gap must cite the direction issue that was filed
        if status == "gap":
            if ref == "GAP":
                messages.append(
                    "%s: rule 5: status gap carries the literal GAP - a gap row must "
                    "cite the filed direction issue" % where
                )
            elif not ref_is_link(ref):
                messages.append(
                    "%s: rule 5: status gap cites no filed direction issue" % where
                )


def check_mapping(mapping, register_ids, messages, subject):
    """Rule 6 - the consumption EPIC #472 cannot go uncovered.

    Three independent layers, so the check does not merely trust the register's
    own mapping table:
      (a) the mapping exists and is non-empty;
      (b) every row it names is a row that actually exists in the register table;
      (c) its capability set matches the set EPIC #472 itself names - both ways,
          so a capability the EPIC names cannot be dropped and a capability the
          EPIC does not name cannot be invented.
    """
    if mapping is None:
        messages.append(
            "%s: rule 6 #472 coverage: the %r mapping table is MISSING - EPIC #472 "
            "coverage cannot be shown"
            % (subject, " | ".join(MAPPING_HEADER))
        )
        return 0
    if not mapping["rows"]:
        messages.append(
            "%s: rule 6 #472 coverage: the 'Capability #472 relies on | Rows' mapping "
            "table is EMPTY - an emptied mapping is a violation, not a pass"
            % subject
        )
        return 0

    carried = []
    for line, cells in mapping["rows"]:
        if len(cells) != 2:
            messages.append(
                "%s: rule 6 #472 coverage: mapping row at line %d has %d cell(s), "
                "expected 2" % (subject, line, len(cells))
            )
            continue
        capability, rows_cell = cells
        if not capability or capability in PLACEHOLDERS:
            messages.append(
                "%s: rule 6 #472 coverage: mapping row at line %d names no capability"
                % (subject, line)
            )
            continue
        # (b) every named register row must exist
        named = ROW_ID_RE.findall(rows_cell)
        if not named:
            messages.append(
                "%s: rule 6 #472 coverage: mapping row %r names no register row"
                % (subject, capability)
            )
        for row_id in named:
            if row_id not in register_ids:
                messages.append(
                    "%s: rule 6 #472 coverage: mapping row %r names %s, which is not "
                    "a row in the register table"
                    % (subject, capability, row_id)
                )
        # (c) the mapping capability must be one EPIC #472 actually names
        flat = norm(capability)
        if not any(
            all(anchor in flat for anchor in anchors)
            for _title, anchors, _epic in EPIC_472_CAPABILITIES
        ):
            messages.append(
                "%s: rule 6 #472 coverage: the mapping table carries %r, which EPIC "
                "#%d does not name" % (subject, capability, EPIC_ISSUE)
            )
        carried.append((capability, flat))

    # (c) every capability EPIC #472 names must be carried by a mapping row
    for title, anchors, _epic in EPIC_472_CAPABILITIES:
        if not any(all(anchor in flat for anchor in anchors) for _cap, flat in carried):
            messages.append(
                "%s: rule 6 #472 coverage: EPIC #%d names the capability %r and the "
                "#472 mapping table carries no row for it"
                % (subject, EPIC_ISSUE, title)
            )
    return len(carried)


def evaluate_text(text, subject=REGISTER_URL):
    """(rc, messages, stats) for register text. rc: 0 conformant, 1 violation."""
    messages = []
    stats = {"rows": 0, "mapping": 0, "epic": len(EPIC_472_CAPABILITIES)}
    tables = parse_tables(text)

    register = find_table(tables, TABLE_HEADER)
    if register is None:
        messages.append(
            "%s: the register table (header %s) is MISSING - the register carries no "
            "rows"
            % (subject, " | ".join(TABLE_HEADER))
        )
        return 1, sorted(set(messages)), stats
    if not register["rows"]:
        messages.append(
            "%s: the register table (header %s) has ZERO rows - an emptied register "
            "is a violation, not a pass"
            % (subject, " | ".join(TABLE_HEADER))
        )
        return 1, sorted(set(messages)), stats

    stats["rows"] = len(register["rows"])
    register_ids = set()
    for _line, cells in register["rows"]:
        if cells:
            found = ROW_ID_RE.search(cells[0])
            if found:
                register_ids.add(found.group(0))

    check_rows(register["rows"], messages, subject)
    stats["mapping"] = check_mapping(
        find_table(tables, MAPPING_HEADER), register_ids, messages, subject
    )
    return (1 if messages else 0), sorted(set(messages)), stats


def evaluate_path(path, subject=None):
    """(rc, messages, stats); rc 2/CANNOT-ASSESS when the register is unreadable."""
    label = subject or REGISTER_URL
    empty = {"rows": 0, "mapping": 0, "epic": len(EPIC_472_CAPABILITIES)}
    if not os.path.isfile(path):
        return 2, [
            "check-codeidx-capability-register: CANNOT-ASSESS - the register %s is "
            "unreadable (missing or not a file)" % label
        ], empty
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
    except Exception as exc:
        return 2, [
            "check-codeidx-capability-register: CANNOT-ASSESS - the register %s "
            "could not be read: %r" % (label, exc)
        ], empty
    return evaluate_text(text, label)


# --- modes ------------------------------------------------------------------

def subject_of(root, register):
    """The register path under test; --register validates an alternate copy."""
    return register if register else os.path.join(root, REGISTER_REL)


def cmd_check(root, register=None):
    rc, messages, stats = evaluate_path(
        subject_of(root, register), register or REGISTER_URL
    )
    if rc == 0:
        print(
            "check-codeidx-capability-register: OK - %d register rows, %d #472 "
            "mapping rows; every owner is known, every acceptance criterion is "
            "present, every ref is GAP-or-link, every shipped row cites a completion "
            "signal, every gap row cites a filed direction, and all %d EPIC #%d "
            "capabilities resolve to rows that exist"
            % (stats["rows"], stats["mapping"], stats["epic"], EPIC_ISSUE)
        )
        return 0
    for message in messages:
        sys.stderr.write(message + "\n")
    if rc == 2:
        sys.stderr.write("check-codeidx-capability-register: CANNOT-ASSESS\n")
    else:
        sys.stderr.write(
            "check-codeidx-capability-register: FAIL - %d violation(s)\n"
            % len(messages)
        )
    return rc


# --- negative controls (in memory; the tracked register is never mutated) ----

def _register_row(text, row_id):
    lines = text.split("\n")
    for index, line in enumerate(lines):
        cells = split_row(line)
        if cells and len(cells) == len(TABLE_HEADER) and ROW_ID_RE.search(cells[0]):
            if ROW_ID_RE.search(cells[0]).group(0) == row_id:
                return lines, index, cells
    raise KeyError("register row %s not found" % row_id)


def set_cell(text, row_id, column, value):
    lines, index, cells = _register_row(text, row_id)
    cells[column] = value
    lines[index] = "| " + " | ".join(cells) + " |"
    return "\n".join(lines)


def drop_register_rows(text):
    kept = []
    for line in text.split("\n"):
        cells = split_row(line)
        if cells and len(cells) == len(TABLE_HEADER) and ROW_ID_RE.search(cells[0]):
            continue
        kept.append(line)
    return "\n".join(kept)


def _mapping_rows(text):
    mapping = find_table(parse_tables(text), MAPPING_HEADER)
    if mapping is None or not mapping["rows"]:
        raise KeyError("mapping table not found")
    return mapping["rows"]


def mapping_set_rows(text, anchor, rows_cell):
    lines = text.split("\n")
    for line, cells in _mapping_rows(text):
        if len(cells) == 2 and anchor.lower() in norm(cells[0]):
            lines[line - 1] = "| " + cells[0] + " | " + rows_cell + " |"
            return "\n".join(lines)
    raise KeyError("mapping row %r not found" % anchor)


def mapping_drop_row(text, anchor):
    lines = text.split("\n")
    for line, cells in _mapping_rows(text):
        if len(cells) == 2 and anchor.lower() in norm(cells[0]):
            del lines[line - 1]
            return "\n".join(lines)
    raise KeyError("mapping row %r not found" % anchor)


def _mapping_empty(text):
    lines = text.split("\n")
    for line, _cells in sorted(_mapping_rows(text), reverse=True):
        del lines[line - 1]
    return "\n".join(lines)


def mapping_append_row(text, capability, rows_cell):
    lines = text.split("\n")
    last = _mapping_rows(text)[-1][0]
    lines.insert(last, "| %s | %s |" % (capability, rows_cell))
    return "\n".join(lines)


def _shipped_without_signal(text, row_id):
    _lines, _index, cells = _register_row(text, row_id)
    if " \u00b7 signal=" not in cells[3]:
        raise KeyError("row %s cites no signal= clause to strip" % row_id)
    return set_cell(text, row_id, 3, cells[3].split(" \u00b7 signal=")[0])


def _cases():
    """(name, token, builder) - one control per refusal the gate makes.

    `token` must appear in a refusal message, i.e. the offending row is NAMED.
    """
    return [
        # rule 1 - owner
        ("owner-blank", "C-01", lambda t: set_cell(t, "C-01", 2, "")),
        ("owner-unknown", "C-01", lambda t: set_cell(t, "C-01", 2, "the-vendor")),
        # rule 2 - acceptance
        ("acceptance-blank", "C-02", lambda t: set_cell(t, "C-02", 5, "")),
        # rule 3 - ref
        ("ref-not-a-link", "C-03",
         lambda t: set_cell(t, "C-03", 3, "see the vendor board")),
        # rule 4 - shipped with no completion signal (closed= kept, signal= gone)
        ("shipped-no-signal", "C-05", lambda t: _shipped_without_signal(t, "C-05")),
        # rule 5 - gap with no filed direction
        ("gap-no-direction", "C-14", lambda t: set_cell(t, "C-14", 3, "GAP")),
        # rule 6 (a) - an emptied mapping
        ("mapping-empty", "mapping table is EMPTY", _mapping_empty),
        # rule 6 (b) - a mapping row naming a row that does not exist
        ("mapping-dangling-row-id", "C-77",
         lambda t: mapping_set_rows(t, "org-wide code-location", "`C-01`, `C-77`")),
        # rule 6 (c) - an EPIC #472 capability dropped from the mapping
        ("mapping-drops-epic-capability", "mandatory consumer surface",
         lambda t: mapping_drop_row(t, "mandatory consumer surface")),
        # rule 6 (c) - a capability the EPIC does not name, invented in the mapping
        ("mapping-invents-capability", "vendor's web UI",
         lambda t: mapping_append_row(
             t, "The vendor's web UI surfaces the index", "`C-01`")),
        # an emptied register is a violation, not a pass
        ("register-emptied", "ZERO rows", drop_register_rows),
    ]


def cmd_self_test(root):
    subject = os.path.join(root, REGISTER_REL)
    if not os.path.isfile(subject):
        sys.stderr.write(
            "check-codeidx-capability-register: CANNOT-ASSESS - self-test base "
            "subject %s is unreadable\n" % REGISTER_URL
        )
        return 2
    with open(subject, encoding="utf-8") as handle:
        base = handle.read()

    # A control proves nothing unless the un-mutated baseline is green.
    base_rc, base_messages, _stats = evaluate_text(base)
    if base_rc != 0:
        sys.stderr.write(
            "check-codeidx-capability-register: CANNOT-ASSESS - the register is "
            "already non-conformant (rc=%d); the controls would be meaningless\n"
            % base_rc
        )
        for message in base_messages:
            sys.stderr.write("    baseline: %s\n" % message)
        return 2

    print("== codeidx-capability-register self-test ==")
    failures = 0
    cases = _cases()

    # rc 2 reachability: an unreadable register is CANNOT-ASSESS, distinct from a
    # violation (rc 1) and never reported as a pass.
    missing = os.path.join(root, "docs", "no-such-codeidx-register.md")
    rc2, messages2, _s = evaluate_path(missing)
    ok2 = rc2 == 2
    print("  %s  %-30s rc=%d expects CANNOT-ASSESS" %
          ("OK  " if ok2 else "FAIL", "register-unreadable", rc2))
    if not ok2:
        failures += 1
        for message in messages2:
            sys.stderr.write("      got: %s\n" % message)

    for name, token, build in cases:
        try:
            mutated = build(base)
        except Exception as exc:
            print("  %s  %-30s control could not be constructed: %r" %
                  ("FAIL", name, exc))
            failures += 1
            continue
        if mutated == base:
            # a no-op mutation would prove nothing at all
            print("  %s  %-30s mutation changed nothing" % ("FAIL", name))
            failures += 1
            continue
        rc, messages, _s = evaluate_text(mutated)
        named = [message for message in messages if token in message]
        ok = rc == 1 and bool(named)
        print("  %s  %-30s rc=%d expects rc=1 naming %r" %
              ("OK  " if ok else "FAIL", name, rc, token))
        if not ok:
            failures += 1
            for message in messages:
                sys.stderr.write("      got: %s\n" % message)

    total = len(cases) + 1
    if failures:
        sys.stderr.write(
            "check-codeidx-capability-register self-test: FAIL - %d of %d control(s) "
            "not refused\n" % (failures, total)
        )
        return 1
    print("check-codeidx-capability-register self-test: OK - %d/%d controls proven "
          "(incl. rc 2 CANNOT-ASSESS)" % (total, total))
    return 0


def cmd_epic_live(root, register=None):
    """Re-anchor the frozen EPIC #472 capability set against the live EPIC body."""
    rc, messages, _stats = evaluate_path(
        subject_of(root, register), register or REGISTER_URL
    )
    for message in messages:
        sys.stderr.write(message + "\n")
    if rc != 0:
        sys.stderr.write(
            "check-codeidx-capability-register: FAIL - the register does not conform; "
            "the live re-anchor is moot\n"
        )
        return rc
    if not any(os.access(os.path.join(d, "gh"), os.X_OK)
               for d in os.environ.get("PATH", "").split(os.pathsep) if d):
        sys.stderr.write(
            "check-codeidx-capability-register: CANNOT-ASSESS - --epic-live needs the "
            "GitHub CLI (`gh`) on PATH\n"
        )
        return 2
    argv = ["gh", "api", "repos/%s/issues/%d" % (EPIC_REPO, EPIC_ISSUE),
            "--jq", '.title + "\\n" + .body']
    try:
        done = subprocess.run(argv, capture_output=True, text=True, check=False)
    except Exception as exc:
        sys.stderr.write(
            "check-codeidx-capability-register: CANNOT-ASSESS - --epic-live could not "
            "run `gh`: %r\n" % (exc,)
        )
        return 2
    if done.returncode != 0:
        sys.stderr.write(
            "check-codeidx-capability-register: CANNOT-ASSESS - --epic-live could not "
            "read EPIC #%d: %s\n"
            % (EPIC_ISSUE, (done.stderr or "").strip()[:400])
        )
        return 2
    body = norm(done.stdout)
    failures = 0
    for title, _anchors, epic_anchors in EPIC_472_CAPABILITIES:
        missing = [a for a in epic_anchors if a not in body]
        if missing:
            failures += 1
            sys.stderr.write(
                "  FAIL  EPIC #%d no longer names %r - missing anchor(s) %s\n"
                % (EPIC_ISSUE, title, ", ".join(repr(m) for m in missing))
            )
    if failures:
        sys.stderr.write(
            "check-codeidx-capability-register: FAIL - %d of %d transcribed "
            "capabilit(ies) are no longer anchored in EPIC #%d; re-transcribe the "
            "EPIC_472_CAPABILITIES table\n"
            % (failures, len(EPIC_472_CAPABILITIES), EPIC_ISSUE)
        )
        return 1
    print(
        "check-codeidx-capability-register: OK - all %d transcribed EPIC #%d "
        "capabilities are still anchored in the live EPIC body"
        % (len(EPIC_472_CAPABILITIES), EPIC_ISSUE)
    )
    return 0


def main():
    argv = sys.argv[1:]
    if not argv:
        sys.stderr.write(
            "usage: check-codeidx-capability-register.sh "
            "[--self-test | --epic-live] [--register PATH]\n"
        )
        return 2
    root = argv[0]
    rest = argv[1:]
    mode = "check"
    register = None
    index = 0
    while index < len(rest):
        arg = rest[index]
        if arg == "--self-test":
            mode = "self-test"
        elif arg == "--epic-live":
            mode = "epic-live"
        elif arg == "--register":
            index += 1
            if index >= len(rest):
                sys.stderr.write(
                    "check-codeidx-capability-register: --register needs a path\n"
                )
                return 2
            register = rest[index]
        elif arg in ("-h", "--help"):
            print(
                "usage: check-codeidx-capability-register.sh "
                "[--self-test | --epic-live] [--register PATH]"
            )
            return 0
        else:
            sys.stderr.write(
                "check-codeidx-capability-register: unknown argument %r\n" % (arg,)
            )
            return 2
        index += 1
    if mode == "self-test":
        return cmd_self_test(root)
    if mode == "epic-live":
        return cmd_epic_live(root, register)
    return cmd_check(root, register)


if __name__ == "__main__":
    sys.exit(main())
PY
