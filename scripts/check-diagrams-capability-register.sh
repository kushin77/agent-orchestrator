#!/usr/bin/env bash
# diagrams-capability-register gate (issue #469, EPIC #462 / #461; GR-29).
#
# Makes docs/DIAGRAMS-CAPABILITY-REGISTER.md binding instead of advisory: a rule
# that lives only in a doc is a suggestion, a gate that fails BY NAME is a
# policy. The gate refuses, naming the offending row, when the register carries
#   1. a row with no owner (owner must be kushin77/diagrams, us or both);
#   2. a row with no acceptance criterion;
#   3. a row whose ref is neither the literal GAP nor a canonical issue URL;
#   4. a row marked `shipped` that cites no completion signal;
#   5. a row marked `gap` with no filed direction issue recorded;
#   6. a capability named by the integration EPIC (#461) that is absent from the
#      register, or pinned to a row id the register does not carry.
#
# The register table is found BY ITS HEADER -
#   `capability | needed-for | owner | ref | status | acceptance | verify`
# - never as "the only table in the file", so a sibling lane may append a
# differently-headed table (issue #468) without this gate reading it.
#
# Tri-state, honest (GR-12, no false green):
#   0  conformant - every row parses and every rule holds
#   1  NOT-OK - at least one violation, every offending row named on stderr
#   2  CANNOT-ASSESS - the register or its table is unreadable (never a pass)
#
# Offline (no network: "a real issue URL" is checked structurally, as the
# canonical github.com/<owner>/<repo>/issues/<n> shape) and deterministic (two
# runs over the same input are byte-identical). Internal negative controls
# construct each of the six violation classes in a scratch register text and
# require rc 1 naming the row; they run by default, so a rule that was added
# without a provoked refusal fails this gate itself.
#
# Usage:
#   scripts/check-diagrams-capability-register.sh                  # check + controls
#   scripts/check-diagrams-capability-register.sh --self-test      # controls only
#   scripts/check-diagrams-capability-register.sh --register FILE  # alternate subject
#
# ---knowledge---
# module_id: scripts.check-diagrams-capability-register
# system: governance
# app: gates
# solution_class: enterprise
# patterns: [tri-state-exit, provoked-negative-control, self-proving-gate, no-false-green, offline-hermetic, named-refusal, deterministic]
# derives_from: null
# owner_sme: qa-sme
# tier: L1
# interfaces: [exit 0 OK, exit 1 NOT-OK, exit 2 CANNOT-ASSESS]
# invariants: ""
# gotchas: ""
# related: ["#461", "#462", "#468", "#469"]
# do_not_duplicate: null
# ---knowledge---
set -u

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"
exec python3 - "$root" "$@" <<'PY'
import os
import re
import sys

REGISTER_REL = "docs/DIAGRAMS-CAPABILITY-REGISTER.md"

REGISTER_HEADER = (
    "capability",
    "needed-for",
    "owner",
    "ref",
    "status",
    "acceptance",
    "verify",
)

# Register convention 4: the owner cell is never blank and names one of these.
OWNERS = ("kushin77/diagrams", "us", "both")

# Register convention 3: status is one of these four values.
STATUSES = ("shipped", "in-flight", "gap", "UNVERIFIED")

# Register convention 2: the literal token used when no direction is filed yet.
GAP_LITERAL = "GAP"

# Values that are not acceptance criteria.
ACCEPTANCE_PLACEHOLDERS = ("", "-", "--", "\u2014", "n/a", "na", "none", "tbd", "?")

# A canonical GitHub issue URL (no pull URL, no other host).
ISSUE_URL_RE = re.compile(
    r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/issues/[1-9][0-9]*"
)

# Register convention 2: the clause a `shipped` row's ref must carry.
COMPLETION_CLAUSE = "completion signal:"

# A re-fetchable signal inside that clause: a comment id, a commit sha or a tag.
SIGNAL_TOKEN_RE = re.compile(r"\b[0-9]{6,}\b|\b[0-9a-f]{7,40}\b|refs/tags/\S+")

# Register convention 2: a `gap` row records the direction it was filed as.
DIRECTION_FILED_RE = re.compile(
    r"direction[\s-]+filed|filed\s+(?:a\s+|the\s+|this\s+|by\s+this\s+|by\s+the\s+)?direction",
    re.IGNORECASE,
)

ROW_ID_RE = re.compile(r"^(C[0-9]+)\b")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")

# The capabilities EPIC #461 (the diagrams integration) names, recorded by
# reading that issue's body (kushin77/agent-orchestrator#461, read 2026-09-14),
# each pinned to the register row that must answer it. The three feature tokens
# are quoted in #461's authority table; the two assets are quoted in its
# definition of done; `blueprint-engine` is named by #461 as explicitly out of
# scope but is still named, so it must stay accounted for. Rule 6 refuses when
# one of these is absent from the register, or when the row that claims to
# answer it does not carry it.
AO461_CAPABILITIES = (
    ("ssot-extract", "C2"),
    ("blueprint-gen", "C4"),
    ("drift-detect", "C5"),
    ("architecture.yaml", "C1"),
    ("gdc-manifest.yaml", "C1"),
    ("blueprint-engine", "C23"),
)


# --- parsing ----------------------------------------------------------------

def split_cells(line):
    """The cells of a markdown table line, or None when the line is not one."""
    s = line.strip()
    if not s.startswith("|"):
        return None
    body = s[1:]
    if body.endswith("|"):
        body = body[:-1]
    return [c.strip() for c in body.split("|")]


def _is_separator(cells):
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", c) for c in cells)


def find_tables(text):
    """Every markdown table, keyed by its exact (lowercased) header tuple."""
    lines = text.splitlines()
    tables = []
    i = 0
    while i < len(lines):
        head = split_cells(lines[i])
        if head is not None and i + 1 < len(lines):
            sep = split_cells(lines[i + 1])
            if sep is not None and len(sep) == len(head) and _is_separator(sep):
                rows = []
                j = i + 2
                while j < len(lines):
                    cells = split_cells(lines[j])
                    if cells is None:
                        break
                    if _is_separator(cells):
                        j += 1
                        continue
                    rows.append((j + 1, cells))
                    j += 1
                tables.append(
                    {
                        "header": tuple(c.lower() for c in head),
                        "header_line": i + 1,
                        "rows": rows,
                    }
                )
                i = j
                continue
        i += 1
    return tables


def register_table(tables):
    for table in tables:
        if table["header"] == REGISTER_HEADER:
            return table
    return None


def row_label(cells, lineno):
    match = ROW_ID_RE.match(cells[0]) if cells else None
    return match.group(1) if match else "line %d" % lineno


def sections(text):
    """Heading-delimited sections, with 0-based [start, end) line ranges."""
    lines = text.splitlines()
    heads = []
    for idx, line in enumerate(lines):
        match = HEADING_RE.match(line)
        if match:
            heads.append((idx, len(match.group(1)), match.group(2)))
    out = []
    for pos, (idx, level, title) in enumerate(heads):
        end = len(lines)
        for jdx, jlevel, _ in heads[pos + 1:]:
            if jlevel <= level:
                end = jdx
                break
        out.append({"start": idx, "end": end, "level": level, "title": title})
    return out


# --- the six rules ----------------------------------------------------------

def shipped_signal_gap(ref):
    """Rule 4 anchor: a `shipped` ref must cite a re-fetchable signal.

    Returns None when the anchor holds, else the reason it does not.
    """
    idx = ref.find(COMPLETION_CLAUSE)
    if idx < 0:
        return "carries no '%s' clause" % COMPLETION_CLAUSE
    if not SIGNAL_TOKEN_RE.search(ref[idx + len(COMPLETION_CLAUSE):]):
        return "the '%s' clause cites no re-fetchable signal" % COMPLETION_CLAUSE
    return None


def check_461_mapping(text, tables, row_ids, label):
    """Rule 6 cross-check: the register's own `#461 relies on` mapping section.

    Inert when the register carries no such section (this revision does not);
    when one is present it must be non-empty, name only row ids the register
    carries, and cover every capability #461 names.
    """
    viol = []
    found = []
    for sec in sections(text):
        if not re.search(r"461", sec["title"]):
            continue
        if not re.search(r"reli|mapp|capabilit", sec["title"], re.IGNORECASE):
            continue
        for table in tables:
            if sec["start"] < table["header_line"] - 1 < sec["end"]:
                found.append(table)
    if not found:
        return viol, False

    data_rows = 0
    ids_seen = set()
    tokens_seen = set()
    for table in found:
        for _lineno, cells in table["rows"]:
            data_rows += 1
            head = cells[0] if cells else ""
            pinned = [c for c in cells if re.fullmatch(r"C[0-9]+", c)]
            ids_seen.update(pinned)
            for token, expected in AO461_CAPABILITIES:
                if token not in head:
                    continue
                tokens_seen.add(token)
                if pinned and expected not in pinned:
                    viol.append(
                        "%s: #461 mapping: %r is pinned to %s, but EPIC #461 "
                        "resolves it to %s (rule 6c)"
                        % (label, token, ", ".join(pinned), expected)
                    )

    if data_rows == 0:
        viol.append("%s: #461 mapping: the mapping section is empty (rule 6a)" % label)
    for rid in sorted(ids_seen):
        if rid not in row_ids:
            viol.append(
                "%s: #461 mapping: %s - the mapping names a row id the register "
                "does not carry (rule 6b)" % (label, rid)
            )
    for token, _expected in AO461_CAPABILITIES:
        if token not in tokens_seen:
            viol.append(
                "%s: #461 mapping: %r - EPIC #461 names it, the mapping omits it "
                "(rule 6c)" % (label, token)
            )
    return viol, True


def check_register(text, label):
    """Return (rc, messages, facts). rc: 0 conformant, 1 violation, 2 cannot-assess."""
    tables = find_tables(text)
    reg = register_table(tables)
    if reg is None:
        return (
            2,
            [
                "%s: no table whose header is '%s' (cannot assess the register)"
                % (label, " | ".join(REGISTER_HEADER))
            ],
            {"rows": 0, "mapping": False},
        )

    viol = []
    rows = []
    for lineno, cells in reg["rows"]:
        rid = row_label(cells, lineno)
        if len(cells) != len(REGISTER_HEADER):
            viol.append(
                "%s: %s: row shape: %d column(s), expected %d (%s)"
                % (
                    label,
                    rid,
                    len(cells),
                    len(REGISTER_HEADER),
                    " | ".join(REGISTER_HEADER),
                )
            )
            continue
        rows.append((rid, dict(zip(REGISTER_HEADER, cells))))

    row_ids = {rid for rid, _row in rows}
    cap_text = {rid: row["capability"] for rid, row in rows}

    for rid, row in rows:
        # rule 1 - owner
        owner = row["owner"]
        if owner not in OWNERS:
            viol.append(
                "%s: %s: owner: %r is not one of %s (convention 4: the owner cell "
                "is never blank)" % (label, rid, owner, " | ".join(OWNERS))
            )

        # rule 2 - acceptance criterion
        acceptance = row["acceptance"].strip()
        if acceptance.lower() in ACCEPTANCE_PLACEHOLDERS:
            viol.append(
                "%s: %s: acceptance: no acceptance criterion (convention 5: "
                "acceptance must be something observed)" % (label, rid)
            )

        status = row["status"]
        if status not in STATUSES:
            viol.append(
                "%s: %s: status: %r is not one of %s"
                % (label, rid, status, " | ".join(STATUSES))
            )

        # rule 3 - ref is the literal GAP or a canonical issue URL
        ref = row["ref"]
        first = (ref.split()[0] if ref.split() else "").strip("`")
        if first == GAP_LITERAL or ISSUE_URL_RE.fullmatch(first):
            pass
        else:
            viol.append(
                "%s: %s: ref: %r is neither the literal %s nor a canonical "
                "issue URL (convention 2: nothing leads the ref)"
                % (label, rid, first or ref, GAP_LITERAL)
            )

        # rule 4 - a shipped row cites a completion signal
        if status == "shipped":
            reason = shipped_signal_gap(ref)
            if reason is not None:
                viol.append(
                    "%s: %s: ref: status is 'shipped' but the ref %s (a closed "
                    "issue is not acceptance evidence)" % (label, rid, reason)
                )

        # rule 5 - a gap row records the filed direction
        if status == "gap":
            if not ISSUE_URL_RE.search(ref):
                viol.append(
                    "%s: %s: ref: status is 'gap' but no filed direction-issue "
                    "URL is recorded in the cell" % (label, rid)
                )
            elif not DIRECTION_FILED_RE.search(ref):
                viol.append(
                    "%s: %s: ref: status is 'gap' but the cell does not record "
                    "the filed direction (convention 2: a %s row records the "
                    "direction it was filed as)" % (label, rid, GAP_LITERAL)
                )

    # rule 6 - every capability EPIC #461 names resolves to a real row
    if not AO461_CAPABILITIES:
        return (
            2,
            [
                "%s: the recorded #461 capability set is empty, so rule 6 would "
                "be vacuous (cannot assess)" % label
            ],
            {"rows": len(rows), "mapping": False},
        )
    for token, expected in AO461_CAPABILITIES:
        if expected not in row_ids:
            viol.append(
                "%s: %s: capability: EPIC #461 names %r but the register "
                "carries no row %s - the capability is absent from the register "
                "(rule 6)" % (label, expected, token, expected)
            )
            continue
        if token not in cap_text[expected]:
            carriers = sorted(rid for rid, cap in cap_text.items() if token in cap)
            if not carriers:
                viol.append(
                    "%s: %s: capability: %r - named by EPIC #461, absent from "
                    "the register (rule 6)" % (label, expected, token)
                )
            else:
                viol.append(
                    "%s: %s: capability: %r is carried by row(s) %s, not by %s "
                    "as recorded (rule 6)"
                    % (label, expected, token, ", ".join(carriers), expected)
                )

    mapping_viol, mapping_present = check_461_mapping(text, tables, row_ids, label)
    viol.extend(mapping_viol)

    return (1 if viol else 0), viol, {"rows": len(rows), "mapping": mapping_present}


def read_text(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return None


def evaluate(path, label):
    text = read_text(path)
    if text is None:
        return (
            2,
            ["%s: register unreadable or absent (cannot assess)" % label],
            {"rows": 0, "mapping": False},
        )
    return check_register(text, label)


# --- internal negative controls ---------------------------------------------

def _row_index(lines, rid):
    for idx, line in enumerate(lines):
        cells = split_cells(line)
        if cells and ROW_ID_RE.match(cells[0]) and cells[0].startswith(rid + " "):
            return idx
    return None


def _set_cell(lines, rid, column, value):
    idx = _row_index(lines, rid)
    if idx is None:
        raise KeyError(rid)
    cells = split_cells(lines[idx])
    cells[REGISTER_HEADER.index(column)] = value
    lines[idx] = "| " + " | ".join(cells) + " |"


def _drop_row(lines, rid):
    idx = _row_index(lines, rid)
    if idx is None:
        raise KeyError(rid)
    del lines[idx]


MAPPING_PROBE = (
    "",
    "## Capabilities #461 relies on",
    "",
    "| capability | row |",
    "|---|---|",
    "| extraction | C99 |",
)


def control_cases():
    """(name, mutator, expected row label, expected message token)."""

    def unowned(lines):
        _set_cell(lines, "C1", "owner", "")

    def no_acceptance(lines):
        _set_cell(lines, "C3", "acceptance", "")

    def ref_not_url(lines):
        _set_cell(lines, "C5", "ref", "see the vendor board")

    def shipped_without_signal(lines):
        _set_cell(
            lines,
            "C2",
            "ref",
            "https://github.com/kushin77/diagrams/issues/100 \u2014 delivered 2026-09-14",
        )

    def gap_without_direction(lines):
        _set_cell(lines, "C13", "status", "gap")
        _set_cell(lines, "C13", "ref", "GAP")

    def capability_absent(lines):
        _drop_row(lines, "C5")

    def mapping_names_missing_row(lines):
        lines.extend(MAPPING_PROBE)

    return (
        ("unowned-row", unowned, "C1", "owner"),
        ("row-without-acceptance", no_acceptance, "C3", "acceptance"),
        ("ref-neither-gap-nor-url", ref_not_url, "C5", "ref"),
        (
            "shipped-without-completion-signal",
            shipped_without_signal,
            "C2",
            COMPLETION_CLAUSE,
        ),
        ("gap-without-filed-direction", gap_without_direction, "C13", "gap"),
        ("capability-absent-from-register", capability_absent, "drift-detect", "drift-detect"),
        ("461-mapping-names-missing-row", mapping_names_missing_row, "C99", "C99"),
    )


def run_controls(base_text):
    print("== diagrams-capability-register: internal negative controls ==")
    base_rc, base_msgs, _facts = check_register(base_text, REGISTER_REL)
    if base_rc != 0:
        sys.stderr.write(
            "check-diagrams-capability-register: CANNOT-ASSESS - the baseline "
            "register is not conformant (rc=%d), so the controls would be "
            "vacuous\n" % base_rc
        )
        for msg in base_msgs:
            sys.stderr.write("    baseline: %s\n" % msg)
        return 2

    cases = control_cases()
    failures = 0
    for name, mutate, expect_row, expect_token in cases:
        lines = base_text.splitlines()
        mutate(lines)
        text = "\n".join(lines) + "\n"
        rc, msgs, _facts = check_register(text, REGISTER_REL)
        named = [m for m in msgs if expect_row in m and expect_token in m]
        ok = rc == 1 and bool(named)
        print(
            "  %s  %-34s rc=%d names %r + %r"
            % ("OK  " if ok else "FAIL", name, rc, expect_row, expect_token)
        )
        if not ok:
            failures += 1
            for msg in msgs:
                sys.stderr.write("      got: %s\n" % msg)

    first = check_register(base_text, REGISTER_REL)
    second = check_register(base_text, REGISTER_REL)
    det_ok = first == second
    print(
        "  %s  %-34s %s"
        % (
            "OK  " if det_ok else "FAIL",
            "deterministic",
            "two consecutive runs identical" if det_ok else "two runs differ",
        )
    )
    if not det_ok:
        failures += 1

    if failures:
        sys.stderr.write(
            "check-diagrams-capability-register: FAIL - %d of %d negative "
            "control(s) not refused by name\n" % (failures, len(cases))
        )
        return 1
    print(
        "check-diagrams-capability-register: OK - %d/%d negative control(s) "
        "refused by name; determinism holds" % (len(cases), len(cases))
    )
    return 0


# --- modes ------------------------------------------------------------------

def main():
    argv = sys.argv[1:]
    if not argv:
        sys.stderr.write(
            "usage: check-diagrams-capability-register.sh [--self-test] "
            "[--register FILE]\n"
        )
        return 2

    root = argv[0]
    rest = argv[1:]
    register = os.path.join(root, REGISTER_REL)
    mode = "all"

    i = 0
    while i < len(rest):
        arg = rest[i]
        if arg == "--self-test":
            mode = "controls"
        elif arg == "--register":
            i += 1
            if i >= len(rest):
                sys.stderr.write(
                    "check-diagrams-capability-register: --register needs an "
                    "argument\n"
                )
                return 2
            register = rest[i]
        elif arg in ("-h", "--help"):
            print(
                "usage: check-diagrams-capability-register.sh [--self-test] "
                "[--register FILE]"
            )
            return 0
        else:
            sys.stderr.write(
                "check-diagrams-capability-register: unknown argument %r\n" % (arg,)
            )
            return 2
        i += 1

    label = REGISTER_REL
    if os.path.abspath(register) != os.path.abspath(os.path.join(root, REGISTER_REL)):
        label = register

    if mode == "controls":
        text = read_text(register)
        if text is None:
            sys.stderr.write(
                "check-diagrams-capability-register: CANNOT-ASSESS - register "
                "unreadable: %s\n" % label
            )
            return 2
        return run_controls(text)

    rc, msgs, facts = evaluate(register, label)
    if rc == 0:
        print(
            "check-diagrams-capability-register: OK - %d register row(s); rules "
            "1-6 enforced; %d #461 capability token(s) resolved"
            % (facts["rows"], len(AO461_CAPABILITIES))
        )
        if not facts.get("mapping"):
            print(
                "check-diagrams-capability-register: NOTE - the register carries "
                "no '#461 relies on' mapping section, so the rule-6 mapping "
                "cross-check (6a/6b/6c) is inert; rule 6 coverage is enforced"
            )
    else:
        for msg in msgs:
            sys.stderr.write(msg + "\n")
        sys.stderr.write(
            "check-diagrams-capability-register: %s - %d finding(s)\n"
            % ("CANNOT-ASSESS" if rc == 2 else "FAIL", len(msgs))
        )
    if rc != 0:
        return rc

    text = read_text(register)
    if text is None:
        sys.stderr.write(
            "check-diagrams-capability-register: CANNOT-ASSESS - register "
            "unreadable: %s\n" % label
        )
        return 2
    return run_controls(text)


if __name__ == "__main__":
    sys.exit(main())
PY
