#!/usr/bin/env bash
#
# check-code-headers.sh — the code-header linter (issue #1535, EPIC #1510).
#
# WHAT IT REFUSES
#   Every in-scope source file must carry the machine-parseable knowledge block
#   defined by `docs/CODE-HEADER-STANDARD.md`, or be recorded in
#   `scripts/code-headers-baseline.tsv` as pre-standard debt. The block is what
#   the indexer (group H, #1527/#1532) reads, so "the file is documented in
#   prose" is not a passing state — the schema says what a declaration IS, and
#   this gate is what makes that a policy rather than a suggestion (GR-29).
#
# WHERE THE SCHEMA LIVES (borrow, never re-declare — ADR-0012)
#   The field table is parsed out of `docs/CODE-HEADER-STANDARD.md` BY ITS OWN
#   HEADER ROW, exactly as the capability-register gates find their register, so
#   this gate carries no copy of the field list: a field added to the document
#   binds the gate in the same commit, and a block declaring a field the
#   document does not is refused BY NAME (`payload-unknown-field`). The three
#   membership fields are validated against their own authorities —
#   `governance/conformance/policy.yaml` `ladder:`,
#   `gateway/finops/tiers.yaml` `ladder:` and `registry/personas/cards/*.yaml`
#   `id:` — and the provocation MUTATES each authority in a scratch tree and
#   requires the verdict to move, so "it reads the authority" is measured rather
#   than asserted (a borrow that reads its own values can never fail).
#
# THE VERDICT CLASSES (which one a file falls into is what makes it fail)
#   conformant   a block that parses and validates                     -> OK
#   unrecorded   no block, and the baseline does not record the file   -> REFUSED
#   invalid      a block is present and a named rule fails             -> REFUSED
#   recorded     no block, the baseline records this exact content     -> excused
#   drifted      no block, a row names the path, its key differs       -> tier >= 1: REFUSED, tier 0: NOTE
#   The baseline never excuses INVALIDITY — only absence.
#
# THE BASELINE IS KEYED BY CONTENT, NOT BY LINE
#   A row is `<path>\t<sha256:…>\t<tracked-by>\t<reason>`, the key being the hash
#   of the file's content with blank lines dropped and trailing whitespace
#   stripped — so inserting a blank line above a recorded file leaves the key
#   unchanged and does NOT re-flag it, while any real content change moves it.
#   That difference is provoked in BOTH directions below, because a
#   line-numbered ledger would excuse whatever happened to sit at the recorded
#   line after an unrelated edit.
#
# THE TIER SEAM (referenced, never reimplemented)
#   EPIC #1510 gates new checks with a tier switch (its group A `A2` design):
#   advisory below tier 1. That switch does not exist here yet, so this check
#   reads ONE declared seam — `AO_CODE_HEADERS_TIER` or `--tier N`, default 0 —
#   and implements no tier policy of its own. At tier 0 the legacy class
#   (`drifted`) is reported; at tier >= 1 it is refused. The new-file class
#   (`unrecorded`) is refused at EVERY tier: a file nobody ever recorded as debt
#   has no excuse. When the group A switch lands, this seam is deleted.
#
# THE PROVOCATION RUNS BY DEFAULT (GR-12: a gate that cannot fail is a formality)
#   `--self-test` builds a scratch tree, plants one violation per refusal class,
#   and requires each to be refused BY NAME through the same functions the
#   repository run uses — plus the vacuity half (a conformant file produces no
#   finding), the both-directions baseline half, and the authority-mutation half.
#   It runs before the tree scan on the default invocation (the point-query verbs
#   `--list`, `--schema`, `--files` and `--fingerprint` answer and exit before
#   it), so a rule added without a provoked refusal fails this gate itself.
#
# Exit contract (the repository's honesty tri-state):
#   0  OK              no refusal, and the provocation passed
#   1  NOT-OK          at least one refusal, or the provocation failed
#   2  CANNOT-ASSESS   unreadable schema/authority, bad invocation, no python3,
#                      no scratch directory — never a pass
#
# Usage:
#   bash scripts/check-code-headers.sh                     the gate: provocation + the tree
#   bash scripts/check-code-headers.sh --self-test         the provocation alone
#   bash scripts/check-code-headers.sh --files F...        the scope predicate, then exactly these files
#   bash scripts/check-code-headers.sh --list              the schema this document declares
#   bash scripts/check-code-headers.sh --schema            the same, machine-readable
#   bash scripts/check-code-headers.sh --fingerprint F...  the baseline key for these files
#   bash scripts/check-code-headers.sh --record P --tracked-by REF   add a debt row
#   bash scripts/check-code-headers.sh --prune-stale       drop stale rows (an explicit edit)
#   bash scripts/check-code-headers.sh --snapshot-baseline create the ledger, once
#
# ---knowledge---
# module_id: scripts.check-code-headers
# system: governance
# app: gates
# solution_class: enterprise
# patterns: [declared-authority, self-proving-gate, content-keyed-baseline, no-false-green]
# derives_from: scripts/check-diagrams-capability-register.sh
# owner_sme: qa-sme
# tier: L1
# interfaces: [exit 0 OK, exit 1 NOT-OK, exit 2 CANNOT-ASSESS, --list, --fingerprint]
# invariants: "the field list is read from the standard document and the three membership fields from their own authorities; this gate carries no copy of either"
# gotchas: "the baseline is keyed by content with blank lines dropped, so a pure line shift must not re-flag a recorded file"
# related: ["#1535", "#1510"]
# do_not_duplicate: docs/CODE-HEADER-STANDARD.md
# ---knowledge---
set -u

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)" || exit 2

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-code-headers: CANNOT-ASSESS python3-missing: install python3 and put it on PATH" >&2
  exit 2
fi

exec python3 - "$root" "$@" <<'PY'
import ast
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile

DOC_REL = "docs/CODE-HEADER-STANDARD.md"
BASELINE_REL = "scripts/code-headers-baseline.tsv"
SELF_REL = "scripts/check-code-headers.sh"
# The shared shell library the gate `source`s at startup (#1753). The scratch
# tree below must carry it too: without it the child cannot resolve its own root,
# the `|| exit 2` fires, and every `--files` arm collapses to CANNOT-ASSESS —
# measured as `2 of 47 arm(s) failed`, issue #1776.
LIB_REL = "scripts/lib/common.sh"
POLICY_REL = "governance/conformance/policy.yaml"
TIERS_REL = "gateway/finops/tiers.yaml"
CARDS_REL = "registry/personas/cards"

# The issue names exactly these six extensions; `.yaml` is a named gap, not an
# omission (docs/CODE-HEADER-STANDARD.md, "Scope").
EXT_KIND = {
    ".py": "py",
    ".sh": "hash",
    ".tf": "hash",
    ".yml": "hash",
    ".ts": "c",
    ".js": "c",
}
SKIP_DIRS = {".git", "vendor", "tests", "fixtures", "plants", "rendered",
             "__pycache__", "node_modules", ".venv", ".mypy_cache"}
KINDS = {"string", "id", "list", "nullable-string", "enum", "sme"}
ID_RE = re.compile(r"^[a-z][a-z0-9._-]*$")
DELIM = "---knowledge---"
DECOR = {"hash": re.compile(r"^(\s*)#\s?(.*)$"),
         "c": re.compile(r"^(\s*)(?:/\*\*?|/\*|//|\*/|\*)\s?(.*)$")}
# The placeholder the drafter leaves for a human decision, assembled here so this
# file never carries the bare marker token its own docs gate refuses (see
# docs/SHELL-PATTERNS.md: fragment the literal a rule would match).
REVIEW = "TO" + "DO"


class CannotAssess(Exception):
    pass


# --------------------------------------------------------------------------
# authorities: the standard document and the three membership vocabularies
# --------------------------------------------------------------------------

def read_text(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()
    except OSError as exc:
        raise CannotAssess("unreadable %s: %s" % (path, exc))


def load_schema(root):
    """The field table, found BY ITS HEADER ROW — the single declaration."""
    text = read_text(os.path.join(root, DOC_REL))
    lines = text.splitlines()
    header = "| field | required | kind | allowed | meaning |"
    start = None
    for index, line in enumerate(lines):
        if line.strip() == header:
            start = index + 2
            break
    if start is None:
        raise CannotAssess("schema-table-missing: %s carries no '%s' header row" % (DOC_REL, header))
    schema = []
    for line in lines[start:]:
        stripped = line.strip()
        if not stripped.startswith("|"):
            break
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if len(cells) < 4:
            raise CannotAssess("schema-table-malformed: %r" % stripped)
        field = cells[0].strip("`")
        required = cells[1].lower()
        kind = cells[2].lower()
        allowed = cells[3]
        if required not in ("yes", "no"):
            raise CannotAssess("schema-table-malformed: %s declares required=%r" % (field, required))
        if kind not in KINDS:
            raise CannotAssess("schema-table-malformed: %s declares kind=%r" % (field, kind))
        schema.append({"field": field, "required": required == "yes", "kind": kind, "allowed": allowed})
    if not schema:
        raise CannotAssess("schema-table-empty: %s declares no fields" % DOC_REL)
    seen = set()
    for row in schema:
        if row["field"] in seen:
            raise CannotAssess("schema-table-duplicate: %s declared twice" % row["field"])
        seen.add(row["field"])
    return schema


def read_ladder_list(path, key):
    """A top-level `<key>:` YAML block-sequence or flow-list of scalars."""
    values = []
    inside = False
    for line in read_text(path).splitlines():
        if not inside:
            match = re.match(r"^%s:\s*(.*)$" % re.escape(key), line)
            if not match:
                continue
            rest = match.group(1).strip()
            if rest.startswith("["):
                return [item.strip().strip("'\"") for item in rest.strip("[]").split(",") if item.strip()]
            inside = True
            continue
        match = re.match(r"^\s+-\s+(.+?)\s*$", line)
        if match:
            values.append(match.group(1).strip("'\""))
            continue
        if re.match(r"^\S", line) and line.strip():
            break
    return values


def read_ladder_keys(path, key):
    """The member keys of a top-level `<key>:` YAML mapping."""
    values = []
    inside = False
    for line in read_text(path).splitlines():
        if not inside:
            if re.match(r"^%s:\s*$" % re.escape(key), line):
                inside = True
            continue
        match = re.match(r"^(\s+)([A-Za-z0-9_.-]+):", line)
        if match and len(match.group(1)) == 2:
            values.append(match.group(2))
            continue
        if line.strip() and not line.startswith(" ") and not line.startswith("#"):
            break
    return values


def read_card_ids(root):
    directory = os.path.join(root, CARDS_REL)
    if not os.path.isdir(directory):
        raise CannotAssess("sme-card-registry-missing: %s" % CARDS_REL)
    ids = []
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".yaml"):
            continue
        match = re.search(r"^id:\s*(\S+)\s*$", read_text(os.path.join(directory, name)), re.M)
        if match:
            ids.append(match.group(1))
    if not ids:
        raise CannotAssess("sme-card-registry-empty: %s declares no id" % CARDS_REL)
    return ids


def load_authorities(root):
    classes = read_ladder_list(os.path.join(root, POLICY_REL), "ladder")
    if not classes:
        raise CannotAssess("class-ladder-empty: %s declares no ladder" % POLICY_REL)
    tiers = read_ladder_keys(os.path.join(root, TIERS_REL), "ladder")
    if not tiers:
        raise CannotAssess("tier-ladder-empty: %s declares no ladder" % TIERS_REL)
    cards = read_card_ids(root)
    return {"class": classes, "tier": tiers, "sme": sorted(set(cards) | {"unassigned"})}


def authority_for(allowed, authorities):
    """The membership set the `allowed` cell names, or None for a freeform field."""
    match = re.match(r"^ladder:([^#]+)#(\S+)$", allowed)
    if match:
        path, key = match.group(1), match.group(2)
        if key == "ladder" and path == POLICY_REL:
            return authorities["class"]
        if key == "ladder" and path == TIERS_REL:
            return authorities["tier"]
        raise CannotAssess("schema-unknown-authority: %s" % allowed)
    if allowed == "sme-card":
        return authorities["sme"]
    if allowed == "-":
        return None
    raise CannotAssess("schema-unknown-authority: %s" % allowed)


# --------------------------------------------------------------------------
# the block: extraction, payload parsing, validation
# --------------------------------------------------------------------------

def strip_decoration(line, kind):
    """The line's payload CONTENT: what follows the language's comment marker.

    The marker's own leading indentation is decoration, not YAML indentation —
    ` * module_id: x` inside a block comment and ` *   nested: y` two levels in
    must stay distinguishable, and returning the text after the marker keeps
    them so (the second one still starts with whitespace and is refused as a
    nested mapping).
    """
    if kind == "py":
        return line
    match = DECOR[kind].match(line)
    return match.group(2) if match else line


def comment_line(line, kind):
    """True when a line is inside the language's comment banner."""
    stripped = line.strip()
    if not stripped:
        return True
    if kind == "hash":
        return stripped.startswith("#")
    if kind == "c":
        return stripped.startswith(("/*", "*", "*/", "//"))
    return False


def parse_scalar(raw):
    text = raw.strip()
    if not text:
        return None
    if text[0] in "\"'":
        if len(text) < 2 or text[-1] != text[0]:
            raise ValueError("unterminated quoted scalar")
        return text[1:-1]
    # An unquoted scalar ends at a comment marker, as YAML itself reads it.
    marker = text.find(" #")
    if marker != -1:
        text = text[:marker].rstrip()
    if text in ("null", "~", "Null", "NULL", ""):
        return None
    return text


def split_flow(text):
    """Split a flow list's body on commas that are not inside quotes."""
    items, current, quote = [], "", ""
    for char in text:
        if quote:
            current += char
            if char == quote:
                quote = ""
            continue
        if char in "\"'":
            quote = char
            current += char
            continue
        if char == ",":
            items.append(current)
            current = ""
            continue
        current += char
    if quote:
        raise ValueError("unterminated quoted scalar")
    items.append(current)
    return [item for item in items if item.strip()]


def parse_payload(lines, start, end, offsets, kind):
    """The payload between the delimiters, as a flat mapping (stdlib only).

    The line decoration is stripped HERE as well as at delimiter detection: a
    shell block's lines arrive as `# module_id: x`, and the mapping entry is what
    remains after the language's comment marker comes off.
    """
    pairs, faults = {}, []
    for index in range(start + 1, end):
        raw = strip_decoration(lines[index], kind)
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if raw[:1].isspace():
            faults.append(("payload-unparsed", "line %d is indented: %r" % (offsets[index], raw.strip())))
            continue
        match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*):(.*)$", raw)
        if not match:
            faults.append(("payload-unparsed", "line %d is not a `key: value` mapping entry: %r"
                           % (offsets[index], raw.strip())))
            continue
        key, rest = match.group(1), match.group(2).strip()
        if key in pairs:
            faults.append(("payload-duplicate-field", "%s declared twice" % key))
            continue
        try:
            if rest.startswith("["):
                if not rest.endswith("]"):
                    raise ValueError("unterminated flow list")
                pairs[key] = [parse_scalar(item) for item in split_flow(rest[1:-1])]
            else:
                pairs[key] = parse_scalar(rest)
        except ValueError as exc:
            faults.append(("payload-unparsed", "%s: %s" % (key, exc)))
    return pairs, faults


def block_of(text, kind):
    """(pairs, faults) or ('missing', ...) — the block, or why there is none."""
    lines = text.splitlines()
    offsets = list(range(1, len(lines) + 1))
    if kind == "py":
        try:
            tree = ast.parse(text)
        except SyntaxError as exc:
            return None, [("python-unparsed", "the file is not parseable Python: %s (line %s)"
                           % (exc.msg, exc.lineno))]
        doc = None
        for node in tree.body:
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) \
                    and isinstance(node.value.value, str):
                doc = node
            break
        if doc is None:
            return "missing", []
        low, high = doc.lineno, doc.end_lineno
    else:
        low, high = 1, len(lines)
        banner_end = 0
        for index, line in enumerate(lines):
            if comment_line(line, kind):
                banner_end = index + 1
                continue
            break
        if banner_end == 0:
            return "missing", []
        high = banner_end
    marks = []
    for index in range(low - 1, high):
        if strip_decoration(lines[index], kind).strip() == DELIM:
            marks.append(index)
    if not marks:
        return "missing", []
    if len(marks) != 2:
        return None, [("block-delimiter-count",
                       "%s carries %d `%s` delimiter line(s); exactly two are required"
                       % (kind, len(marks), DELIM))]
    pairs, faults = parse_payload(lines, marks[0], marks[1], offsets, kind)
    return pairs, faults


def validate(pairs, schema, authorities):
    faults = []
    declared = {row["field"] for row in schema}
    for key in sorted(pairs):
        if key not in declared:
            faults.append(("payload-unknown-field",
                           "%s is not declared by %s" % (key, DOC_REL)))
    for row in schema:
        field, kind = row["field"], row["kind"]
        if field not in pairs:
            if row["required"]:
                faults.append(("field-missing", "%s is required and absent" % field))
            continue
        value = pairs[field]
        if value is None:
            if kind == "nullable-string":
                continue
            faults.append(("field-invalid", "%s is null; a %s is required" % (field, kind)))
            continue
        if kind == "list":
            if not isinstance(value, list):
                faults.append(("field-invalid", "%s is not a flow list" % field))
                continue
            if any(not isinstance(item, str) or not item.strip() for item in value):
                faults.append(("field-invalid", "%s carries an empty list item" % field))
            continue
        if not isinstance(value, str):
            faults.append(("field-invalid", "%s is not a scalar" % field))
            continue
        if not value.strip():
            # The document says an OPTIONAL freeform string may be empty (and may
            # never be null): `gotchas: ""` is the documented spelling, so the
            # empty string is accepted there and nowhere else.
            if kind == "string" and not row["required"]:
                continue
            faults.append(("field-invalid", "%s is empty; a %s is required" % (field, kind)))
            continue
        if kind == "id" and not ID_RE.match(value):
            faults.append(("field-invalid", "%s=%r is not a lowercase dotted slug" % (field, value)))
            continue
        if kind in ("enum", "sme"):
            members = authority_for(row["allowed"], authorities)
            if value not in members:
                faults.append(("field-not-in-authority",
                               "%s=%r is not one of %s" % (field, value, ", ".join(sorted(members)))))
    return faults


# --------------------------------------------------------------------------
# the baseline (content-keyed, never line-keyed)
# --------------------------------------------------------------------------

def fingerprint(text):
    kept = [line.rstrip() for line in text.splitlines() if line.strip()]
    return "sha256:" + hashlib.sha256("\n".join(kept).encode("utf-8")).hexdigest()


def load_baseline(path):
    rows, by_path, faults = [], {}, []
    if not os.path.exists(path):
        return rows, by_path, faults
    for number, line in enumerate(read_text(path).splitlines(), start=1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        cells = line.split("\t")
        if len(cells) != 4 or not all(cell.strip() for cell in cells):
            faults.append(("baseline-malformed",
                           "line %d is not `<path>\\t<sha256:…>\\t<tracked-by>\\t<reason>`" % number))
            continue
        rel, key, tracked_by, reason = (cell.strip() for cell in cells)
        if not key.startswith("sha256:"):
            faults.append(("baseline-malformed", "line %d does not carry a sha256 key" % number))
            continue
        if rel in by_path:
            faults.append(("baseline-duplicate", "%s is recorded twice" % rel))
            continue
        row = {"path": rel, "key": key, "tracked_by": tracked_by, "reason": reason}
        rows.append(row)
        by_path[rel] = row
    return rows, by_path, faults


def baseline_key(root, rel):
    return fingerprint(read_text(os.path.join(root, rel)))


# --------------------------------------------------------------------------
# the classification ONE file gets — the code path every arm drives
# --------------------------------------------------------------------------

def classify(root, rel, schema, authorities, baseline=None):
    """-> (class, faults, pairs). class is one of the five in the header."""
    path = os.path.join(root, rel)
    kind = EXT_KIND[os.path.splitext(rel)[1]]
    text = read_text(path)
    pairs, faults = block_of(text, kind)
    if faults:
        return "invalid", faults, None
    if pairs == "missing" or pairs is None:
        row = (baseline or {}).get(rel)
        if row is None:
            return "unrecorded", [], None
        if row["key"] == fingerprint(text):
            return "recorded", [], None
        return "drifted", [], None
    faults = validate(pairs, schema, authorities)
    if faults:
        return "invalid", faults, pairs
    return "conformant", [], pairs


# --------------------------------------------------------------------------
# the repository run
# --------------------------------------------------------------------------

def in_scope(rel):
    if not rel.endswith(tuple(EXT_KIND)):
        return False
    parts = rel.split("/")
    if parts[0] == "vendor":
        return False
    return not any(part in SKIP_DIRS for part in parts[:-1])


def why_out_of_scope(rel):
    """Name the reason `in_scope` said no, so a skipped path reads as a verdict."""
    if not rel.endswith(tuple(EXT_KIND)):
        return ("%s is not one of the extensions the standard covers (%s)"
                % (os.path.splitext(rel)[1] or "no extension",
                   ", ".join(sorted(EXT_KIND))))
    parts = rel.split("/")
    if parts[0] == "vendor":
        return "under vendor/, a pinned submodule"
    for part in parts[:-1]:
        if part in SKIP_DIRS:
            return "under %s/, which the tree scan skips" % part
    return "not in scope"


def scope_files(root):
    try:
        out = subprocess.run(["git", "-C", root, "ls-files", "--cached", "--others",
                              "--exclude-standard", "--"] + ["*" + ext for ext in EXT_KIND],
                             capture_output=True, text=True, check=True).stdout
        listed = [line for line in out.splitlines() if line.strip()]
    except (OSError, subprocess.CalledProcessError):
        listed = []
        for base, dirs, names in os.walk(root):
            dirs[:] = [name for name in dirs if name not in SKIP_DIRS]
            for name in names:
                listed.append(os.path.relpath(os.path.join(base, name), root))
    return sorted({rel for rel in listed if in_scope(rel)})


def report(path, message):
    print("  FAIL  %s: %s" % (path, message), file=sys.stderr)


def run_tree(root, schema, authorities, tier, files=None):
    rows, by_path, faults = load_baseline(os.path.join(root, BASELINE_REL))
    refusals, drifted = [], []
    for code, message in faults:
        refusals.append((BASELINE_REL, code, message))
    if files is None:
        targets, skipped = scope_files(root), []
    else:
        # An explicit list goes through the SAME predicate the tree scan uses (#1811).
        # Without this the two entry points disagreed, and a lane could not trust
        # `--files` as a scoped equivalent of the tree scan:
        #   * a non-source extension reached classify(), whose EXT_KIND lookup raised
        #     KeyError -- a traceback rather than a verdict; and
        #   * a file under a SKIP_DIR (tests/, fixtures/, ...) was refused as
        #     `unrecorded` although `scope_files` excludes it, so the naive list for a
        #     directory could never exit 0 and the failure it showed was noise.
        # Every lane in the header backfill hit one facet or the other.
        targets, skipped = [], []
        for rel in files:
            (targets if in_scope(rel) else skipped).append(rel)
    classes = {"conformant": 0, "unrecorded": 0, "invalid": 0, "recorded": 0, "drifted": 0}
    for rel in targets:
        if not os.path.isfile(os.path.join(root, rel)):
            raise CannotAssess("no such file: %s" % rel)
        result, found, _ = classify(root, rel, schema, authorities, by_path)
        classes[result] += 1
        if result == "drifted":
            drifted.append(rel)
            if tier >= 1:
                refusals.append((rel, "drifted", "recorded debt whose content has moved"))
        elif result == "unrecorded":
            refusals.append((rel, "unrecorded",
                             "carries no %s block and is not recorded in %s (draft one with "
                             "`bash scripts/draft-code-header.sh %s`)" % (DELIM, BASELINE_REL, rel)))
        elif result == "invalid":
            for code, message in found:
                refusals.append((rel, code, message))
    # Baseline hygiene, in BOTH directions: a row that has stopped being true is
    # reported and prunable (stale, deliberately NOT fatal — the measured #740
    # precedent: punishing the cleanup a ledger exists to prompt is worse), while
    # a row that is WRONG (malformed, duplicated) is refused.
    stale = stale_rows(root, rows, schema, authorities)
    for rel, code, message in refusals:
        report(rel, "%s — %s" % (code, message))
    for rel in skipped:
        print("    note  %s: out of scope — %s" % (rel, why_out_of_scope(rel)), file=sys.stderr)
    for row in stale:
        why = "the file is gone" if not os.path.isfile(os.path.join(root, row["path"])) \
            else "the file now carries a valid block"
        print("    note  %s: baseline row is stale (%s) — prune it with `--prune-stale`"
              % (row["path"], why), file=sys.stderr)
    for rel in drifted:
        print("  %s  %s: recorded debt edited without a block — the recorded content is gone"
              % ("FAIL" if tier >= 1 else "note", rel), file=sys.stderr)
    print("code-headers: %d in scope — %d conformant, %d recorded, %d drifted, %d stale row(s)"
          % (len(targets), classes["conformant"], classes["recorded"], len(drifted), len(stale)))
    if refusals:
        print("code-headers: NOT-OK — %d refusal(s) (tier %d)" % (len(refusals), tier), file=sys.stderr)
        return 1
    print("code-headers: OK — every in-scope file carries a valid block or is recorded debt")
    return 0


# --------------------------------------------------------------------------
# the writing verbs: an explicit, reviewed act, never an automatic one
# --------------------------------------------------------------------------

def baseline_header(path):
    if not os.path.exists(path):
        return []
    return [line for line in read_text(path).splitlines() if line.lstrip().startswith("#") or not line.strip()]


def write_baseline(path, header, rows):
    body = "".join("%s\t%s\t%s\t%s\n" % (row["path"], row["key"], row["tracked_by"], row["reason"])
                   for row in sorted(rows, key=lambda item: item["path"]))
    text = body if not header else "\n".join(header) + "\n" + body
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


BASELINE_NOTE = [
    "# scripts/code-headers-baseline.tsv — the recorded pre-standard debt (issue #1535).",
    "#",
    "# One row per file that carries no knowledge block yet, keyed by CONTENT and never",
    "# by line number:",
    "#",
    "#   <path>\t<sha256:…>\t<tracked-by>\t<reason>",
    "#",
    "# The key is sha256 over the file's content with blank lines dropped and trailing",
    "# whitespace stripped, so a pure LINE SHIFT (a blank line inserted above the content)",
    "# leaves the key unchanged and does not re-flag the file, while any real content change",
    "# moves it. That is the whole point of keying on content: a line-numbered ledger would",
    "# have to be re-recorded on every blank line and would then excuse whatever happened to",
    "# sit at the recorded line.",
    "#",
    "# The ledger is SHRINK-ONLY and self-cleaning:",
    "#   * add a block to a recorded file and its row goes STALE — reported and removable",
    "#     with `--prune-stale`, never silently kept;",
    "#   * edit a recorded file without adding a block and the row DRIFTS — advisory at",
    "#     tier 0, refused at tier >= 1 (EPIC #1510's group A tier switch);",
    "#   * `--record <path> --tracked-by <ref>` adds a row for a file the backfill cannot",
    "#     header, and REFUSES a path that did not exist at this ledger's own last commit —",
    "#     a lane can never grant its own new file an amnesty;",
    "#   * `--snapshot-baseline` created this file once, at the pre-standard measurement,",
    "#     and refuses to run again: every later addition goes through `--record`.",
]


def snapshot_baseline(root, schema, authorities):
    """Record the pre-standard tree, ONCE. Every later addition needs an anchor."""
    baseline_path = os.path.join(root, BASELINE_REL)
    if os.path.exists(baseline_path):
        raise CannotAssess("snapshot-refused: %s already exists — a ledger is created once and then "
                           "maintained with `--record` (anchored) and `--prune-stale`" % BASELINE_REL)
    rows, refused = [], []
    for rel in scope_files(root):
        result, found, _ = classify(root, rel, schema, authorities, None)
        if result == "unrecorded":
            rows.append({"path": rel, "key": baseline_key(root, rel), "tracked_by": "#1535",
                         "reason": "pre-standard snapshot (the backfill lane is #1538)"})
        elif result == "invalid":
            refused.append((rel, found))
    write_baseline(baseline_path, BASELINE_NOTE, rows)
    for rel, found in refused:
        report(rel, "%s — a snapshot cannot excuse an invalid block; fix it before recording"
               % found[0][0])
    print("recorded %d pre-standard file(s) as content-keyed debt" % len(rows))
    return 1 if refused else 0


def record(root, rel, tracked_by):
    baseline_path = os.path.join(root, BASELINE_REL)
    rows, by_path, _ = load_baseline(baseline_path)
    if not os.path.isfile(os.path.join(root, rel)):
        raise CannotAssess("record-no-such-file: %s" % rel)
    anchor = subprocess.run(["git", "-C", root, "log", "-1", "--format=%H", "--", BASELINE_REL],
                            capture_output=True, text=True)
    commit = anchor.stdout.strip() if anchor.returncode == 0 else ""
    if not commit:
        raise CannotAssess("record-no-anchor: %s is not tracked, so a debt row cannot be anchored"
                           % BASELINE_REL)
    present = subprocess.run(["git", "-C", root, "cat-file", "-e", "%s:%s" % (commit, rel)],
                             capture_output=True, text=True)
    if present.returncode != 0:
        raise CannotAssess("record-newly-delivered: %s did not exist at %s's own commit (%s), so a "
                           "lane cannot grant its own new file an amnesty" % (rel, BASELINE_REL, commit[:8]))
    row = {"path": rel, "key": baseline_key(root, rel), "tracked_by": tracked_by,
           "reason": "pre-standard snapshot"}
    rows = [existing for existing in rows if existing["path"] != rel] + [row]
    write_baseline(baseline_path, baseline_header(baseline_path), rows)
    print("recorded %s (%s) tracked by %s" % (rel, row["key"], tracked_by))
    return 0


def stale_rows(root, rows, schema, authorities):
    """Rows that have stopped being true: the file is gone, or it is conformant."""
    stale = []
    for row in rows:
        if not os.path.isfile(os.path.join(root, row["path"])):
            stale.append(row)
            continue
        result, _, _ = classify(root, row["path"], schema, authorities, None)
        if result == "conformant":
            stale.append(row)
    return stale


def prune_stale(root, schema, authorities):
    baseline_path = os.path.join(root, BASELINE_REL)
    rows, _, _ = load_baseline(baseline_path)
    stale = stale_rows(root, rows, schema, authorities)
    gone = {row["path"] for row in stale}
    kept = [row for row in rows if row["path"] not in gone]
    write_baseline(baseline_path, baseline_header(baseline_path), kept)
    print("pruned %d stale row(s): %s"
          % (len(stale), ", ".join(sorted(gone)) if gone else "none"))
    return 0


# --------------------------------------------------------------------------
# the provocation: every refusal class, both ways, through the SAME functions
# --------------------------------------------------------------------------

class Arms(object):
    def __init__(self):
        self.total = 0
        self.failed = 0

    def expect(self, label, got, want):
        self.total += 1
        if got == want:
            print("  OK    %s" % label)
            return True
        self.failed += 1
        print("check-code-headers: FAIL — %s (got %r, wanted %r)" % (label, got, want), file=sys.stderr)
        return False


def fixture(kind, pairs, summary="fixture"):
    """One fixture file's text, in that language's decoration."""
    body = "".join("%s: %s\n" % (key, value) for key, value in pairs)
    if kind == "py":
        return '"""%s.\n\n%s%s%s\n"""\n\n\nVALUE = 1\n' % (summary, DELIM, "\n", body.rstrip("\n") + "\n" + DELIM)
    prefix = "# " if kind == "hash" else " * "
    lines = [prefix + DELIM] + [prefix + line for line in body.rstrip("\n").splitlines()] + [prefix + DELIM]
    if kind == "hash":
        return "#!/usr/bin/env bash\n# banner\n" + "\n".join(lines) + "\nset -u\n"
    return "/*\n * banner\n" + "\n".join(lines) + "\n */\n"


VALID = [
    ("module_id", "probe.module"), ("system", "governance"), ("app", "gates"),
    ("solution_class", "enterprise"), ("patterns", "[a, b]"), ("derives_from", "null"),
    ("owner_sme", "qa-sme"), ("tier", "L1"), ("interfaces", "[one]"),
    ("invariants", "\"must not change\""), ("gotchas", "\"\""), ("related", "[\"#1535\"]"),
    ("do_not_duplicate", "null"),
]


def scratch_root(real_root, base):
    """A scratch tree carrying only the authorities the gate reads.

    The tuple below is the full set of files the gate resolves at startup: keep
    it in step with what the gate actually reads — it `source`s ``LIB_REL`` at
    line 95 and runs ``SELF_REL`` as the child. A dependency missing here does
    not fail loudly: the child's ``root`` resolves to nothing, its `|| exit 2`
    fires, and every ``--files`` arm reports ``(2, False)`` (#1776).
    """
    root = os.path.join(base, "root")
    for rel in (DOC_REL, POLICY_REL, TIERS_REL, SELF_REL, LIB_REL):
        target = os.path.join(root, rel)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copy2(os.path.join(real_root, rel), target)
    os.makedirs(os.path.join(root, CARDS_REL), exist_ok=True)
    cards = os.path.join(real_root, CARDS_REL)
    for name in sorted(os.listdir(cards)):
        if name.endswith(".yaml"):
            shutil.copy2(os.path.join(cards, name), os.path.join(root, CARDS_REL, name))
    os.makedirs(os.path.join(root, "probe"), exist_ok=True)
    return root


def place(root, rel, text):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    return rel


def self_test(real_root):
    arms = Arms()
    base = tempfile.mkdtemp(prefix="ao-code-headers.", dir="/tmp")
    try:
        root = scratch_root(real_root, base)
        schema = load_schema(root)
        authorities = load_authorities(root)
        arms.expect("the schema table is read from the document (%d fields)" % len(schema),
                    sorted(row["field"] for row in schema), sorted(dict(VALID)))
        arms.expect("the class ladder is read from its authority (6 rungs)",
                    authorities["class"], ["template", "class", "pattern", "enterprise", "faang", "elite"])
        arms.expect("the tier ladder is read from its authority (L0/L1/L2)",
                    authorities["tier"], ["L0", "L1", "L2"])
        arms.expect("the SME vocabulary is read from the card registry, plus `unassigned`",
                    (len(authorities["sme"]) >= 11, "unassigned" in authorities["sme"], "qa-sme" in authorities["sme"]),
                    (True, True, True))

        print("== each language's decoration parses ==")
        for ext, kind in sorted(EXT_KIND.items()):
            rel = place(root, "probe/valid" + ext, fixture(kind, VALID))
            result, faults, _ = classify(root, rel, schema, authorities, {})
            arms.expect("%s decodes as %s" % (ext, kind), (result, faults), ("conformant", []))

        print("== a conformant file produces no finding (vacuity) ==")
        rel = place(root, "probe/clean.py", fixture("py", VALID))
        arms.expect("a conformant file is OK", classify(root, rel, schema, authorities, {})[0], "conformant")

        print("== each refusal class, refused BY NAME ==")
        broken = [
            ("bad-class", [(k, "made-up-rung" if k == "solution_class" else v) for k, v in VALID],
             "solution_class", "invalid", "field-not-in-authority"),
            ("bad-sme", [(k, "nobody-sme" if k == "owner_sme" else v) for k, v in VALID],
             "owner_sme", "invalid", "field-not-in-authority"),
            ("bad-tier", [(k, "L9" if k == "tier" else v) for k, v in VALID],
             "tier", "invalid", "field-not-in-authority"),
            ("no-patterns", [(k, v) for k, v in VALID if k != "patterns"],
             "patterns", "invalid", "field-missing"),
            ("unknown-field", VALID + [("not_a_field", "1")],
             "not_a_field", "invalid", "payload-unknown-field"),
        ]
        for name, pairs, needle, want_class, want_code in broken:
            rel = place(root, "probe/%s.py" % name, fixture("py", pairs))
            result, faults, _ = classify(root, rel, schema, authorities, {})
            named = any(needle in message for _, message in faults)
            codes = sorted({code for code, _ in faults})
            arms.expect("a file with a bad %s is refused naming it" % needle,
                        (result, want_code in codes, named), (want_class, True, True))

        rel = place(root, "probe/no-block.py", '\"\"\"a module with no block at all.\"\"\"\n\n\nVALUE = 1\n')
        arms.expect("a file with no block and no row is unrecorded",
                    classify(root, rel, schema, authorities, {})[0], "unrecorded")

        rel = place(root, "probe/one-delim.sh", "#!/usr/bin/env bash\n# %s\n# module_id: x\nset -u\n" % DELIM)
        result, faults, _ = classify(root, rel, schema, authorities, {})
        arms.expect("one delimiter is refused by name", (result, faults[0][0] if faults else None),
                    ("invalid", "block-delimiter-count"))

        rel = place(root, "probe/unparsed.sh",
                    "#!/usr/bin/env bash\n# %s\n# module_id: x\n#   indented: y\n# %s\nset -u\n" % (DELIM, DELIM))
        result, faults, _ = classify(root, rel, schema, authorities, {})
        arms.expect("an indented payload line is refused by name",
                    (result, faults[0][0] if faults else None), ("invalid", "payload-unparsed"))

        rel = place(root, "probe/late-block.py", "import os\n\n" + fixture("py", VALID))
        result, faults, _ = classify(root, rel, schema, authorities, {})
        arms.expect("a python block outside the docstring is refused", result, "unrecorded")

        rel = place(root, "probe/late-banner.sh",
                    "#!/usr/bin/env bash\nset -u\n" + fixture("hash", VALID))
        result, faults, _ = classify(root, rel, schema, authorities, {})
        arms.expect("a shell block outside the leading banner is refused", result, "unrecorded")

        rel = place(root, "probe/prose.sh",
                    "#!/usr/bin/env bash\n# the block token is %s when it opens a line\nset -u\n" % DELIM)
        result, _, _ = classify(root, rel, schema, authorities, {})
        arms.expect("a prose mention of the token is not a block", result, "unrecorded")

        print("== the baseline is keyed by CONTENT, not by line ==")
        legacy = place(root, "probe/legacy.sh", "#!/usr/bin/env bash\n# an old file, no block\nset -u\n")
        baseline = {legacy: {"path": legacy, "key": fingerprint(read_text(os.path.join(root, legacy))),
                             "tracked_by": "#1535", "reason": "probe"}}
        arms.expect("recorded debt is excused", classify(root, legacy, schema, authorities, baseline)[0], "recorded")
        shifted = place(root, "probe/legacy.sh", "\n\n#!/usr/bin/env bash\n# an old file, no block\nset -u\n")
        arms.expect("a blank line above the content does NOT re-flag it (content-keyed)",
                    classify(root, shifted, schema, authorities, baseline)[0], "recorded")
        edited = place(root, "probe/legacy.sh", "#!/usr/bin/env bash\n# an old file, no block\nset -u\necho hi\n")
        arms.expect("a real content change DOES void the recorded key",
                    classify(root, edited, schema, authorities, baseline)[0], "drifted")
        arms.expect("an unrecorded file is never excused",
                    classify(root, legacy, schema, authorities, {})[0], "unrecorded")

        print("== the baseline's own hygiene, in both directions ==")
        baseline_path = os.path.join(root, BASELINE_REL)
        headered = place(root, "probe/now-headered.sh", fixture("hash", VALID))
        write_baseline(baseline_path, ["# probe ledger"],
                       [{"path": legacy, "key": fingerprint("stale"), "tracked_by": "#1535", "reason": "probe"},
                        {"path": "probe/gone.sh", "key": "sha256:0", "tracked_by": "#1535", "reason": "probe"},
                        {"path": headered, "key": fingerprint("stale"), "tracked_by": "#1535", "reason": "probe"}])
        rows, by_path, hygiene = load_baseline(baseline_path)
        arms.expect("three well-formed rows load with no fault", (len(rows), hygiene), (3, []))
        stale = sorted(row["path"] for row in stale_rows(root, rows, schema, authorities))
        arms.expect("staleness is the file that is gone or now conformant — never the drifted one",
                    stale, ["probe/gone.sh", "probe/now-headered.sh"])
        prune_stale(root, schema, authorities)
        kept, _, _ = load_baseline(baseline_path)
        arms.expect("`--prune-stale` removes exactly the stale rows",
                    [row["path"] for row in kept], [legacy])
        with open(baseline_path, "a", encoding="utf-8") as handle:
            handle.write("probe/broken-row\n")
        _, _, hygiene = load_baseline(baseline_path)
        arms.expect("a malformed row is refused by name",
                    hygiene[0][0] if hygiene else None, "baseline-malformed")
        write_baseline(baseline_path, [], [{"path": legacy, "key": "sha256:1", "tracked_by": "#1535",
                                           "reason": "probe"},
                                           {"path": legacy, "key": "sha256:2", "tracked_by": "#1535",
                                            "reason": "probe"}])
        _, _, hygiene = load_baseline(baseline_path)
        arms.expect("a duplicate row is refused by name",
                    hygiene[0][0] if hygiene else None, "baseline-duplicate")

        print("== the tier seam: the same file, two tiers (the legacy class only) ==")
        original = "#!/usr/bin/env bash\n# an old file, no block\nset -u\n"
        place(root, legacy, original)
        seam = {legacy: {"path": legacy, "key": fingerprint(original), "tracked_by": "#1535", "reason": "probe"}}
        arms.expect("tier 0: recorded debt is excused",
                    classify(root, legacy, schema, authorities, seam)[0], "recorded")
        place(root, legacy, original + "echo drifted\n")
        arms.expect("tier 0: drifted debt is classified as drifted, not refused",
                    classify(root, legacy, schema, authorities, seam)[0], "drifted")
        arms.expect("no tier excuses a file that was never recorded",
                    classify(root, legacy, schema, authorities, {})[0], "unrecorded")

        print("== a record entry can never grant a lane its own new file ==")
        git_root = os.path.join(base, "git")
        os.makedirs(os.path.join(git_root, os.path.dirname(BASELINE_REL)), exist_ok=True)
        write_baseline(os.path.join(git_root, BASELINE_REL), ["# probe ledger"],
                       [{"path": "probe/old.sh", "key": "sha256:0", "tracked_by": "#1535", "reason": "probe"}])
        place(git_root, "probe/old.sh", "#!/usr/bin/env bash\n")
        env = dict(os.environ, GIT_AUTHOR_NAME="probe", GIT_AUTHOR_EMAIL="probe@example.invalid",
                   GIT_COMMITTER_NAME="probe", GIT_COMMITTER_EMAIL="probe@example.invalid")
        for command in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "probe"]):
            subprocess.run(["git", "-C", git_root] + command, capture_output=True, text=True, env=env)
        place(git_root, "probe/new.sh", "#!/usr/bin/env bash\n")
        try:
            record(git_root, "probe/new.sh", "#1535")
            outcome = "accepted"
        except CannotAssess as exc:
            outcome = "refused" if "record-newly-delivered" in str(exc) else "other: %s" % exc
        arms.expect("`--record` refuses a path that did not exist at the ledger's own commit",
                    outcome, "refused")
        try:
            record(git_root, "probe/old.sh", "#1535")
            outcome = "accepted"
        except CannotAssess as exc:
            outcome = "refused: %s" % exc
        arms.expect("`--record` accepts a path that did", outcome, "accepted")
        _, by_path, _ = load_baseline(os.path.join(git_root, BASELINE_REL))
        arms.expect("the recorded row carries the file's current key",
                    by_path.get("probe/old.sh", {}).get("key"),
                    fingerprint(read_text(os.path.join(git_root, "probe/old.sh"))))

        print("== the gate READS each authority: mutate it and the verdict must move ==")
        probe = place(root, "probe/mutation.py", fixture("py", VALID))
        arms.expect("control: the unmutated authorities accept the declaration",
                    classify(root, probe, schema, authorities, {})[0], "conformant")
        mutations = ((POLICY_REL, "  - enterprise\n", "", "solution_class authority"),
                     (TIERS_REL, "\n  L1:", "\n  L1X:", "tier authority"),
                     (CARDS_REL + "/qa-sme.yaml", "id: qa-sme\n", "id: qa-sme-renamed\n",
                      "SME card registry"))
        for rel_path, old, new, label in mutations:
            victim = os.path.join(root, rel_path)
            original = read_text(victim)
            if old not in original:
                arms.expect("the mutation target for the %s is present" % label, old in original, True)
                continue
            with open(victim, "w", encoding="utf-8") as handle:
                handle.write(original.replace(old, new, 1))
            result, found, _ = classify(root, probe, load_schema(root), load_authorities(root), {})
            codes = sorted({code for code, _ in found})
            arms.expect("mutating the %s moves the verdict" % label,
                        (result, "field-not-in-authority" in codes), ("invalid", True))
            with open(victim, "w", encoding="utf-8") as handle:
                handle.write(original)
        changed, _, _ = classify(root, probe, load_schema(root), load_authorities(root), {})
        arms.expect("restoring the authorities restores the verdict", changed, "conformant")

        print("== the field list is READ from the document, not carried here ==")
        doc_path = os.path.join(root, DOC_REL)
        doc_text = read_text(doc_path)
        with open(doc_path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(line for line in doc_text.splitlines() if "`gotchas`" not in line) + "\n")
        trimmed = load_schema(root)
        result, found, _ = classify(root, probe, trimmed, authorities, {})
        codes = sorted({code for code, _ in found})
        arms.expect("a field the document no longer declares moves the verdict",
                    (len(trimmed), result, "payload-unknown-field" in codes), (12, "invalid", True))
        with open(doc_path, "w", encoding="utf-8") as handle:
            handle.write(doc_text)
        arms.expect("restoring the document restores the field list",
                    len(load_schema(root)), 13)

        print("== the CLI path, end to end (exit codes, not just functions) ==")
        # The scratch ledger is removed first: the CLI half must be measured with
        # a clean tree, or a leftover duplicate row would make its rc 1 for a
        # reason that is not the fixture's.
        os.remove(os.path.join(root, BASELINE_REL))
        script = os.path.join(root, SELF_REL)
        good = place(root, "probe/cli-good.sh", fixture("hash", VALID))
        bad = place(root, "probe/cli-bad.sh", "#!/usr/bin/env bash\nset -u\n")
        for rel_path, want_rc, want in ((good, 0, "code-headers: OK"), (bad, 1, "unrecorded")):
            # The child is MARKED as a provocation child. `--files` is what makes
            # it scan; the mark is what makes a regression of that dispatch refuse
            # by name instead of forking the gate until the box is out of
            # processes -- a runaway reports nothing, a refusal names itself.
            proc = subprocess.run(["bash", script, "--files", rel_path], capture_output=True,
                                  text=True, env=dict(os.environ, AO_CODE_HEADERS_IN_PROVOCATION="1"))
            arms.expect("`--files %s` exits %d naming %s" % (os.path.basename(rel_path), want_rc, want),
                        (proc.returncode, want in (proc.stdout + proc.stderr)), (want_rc, True))

        # The explicit list must pass the SAME predicate the tree scan uses (#1811).
        # Two shapes, because the two entry points disagreed in two ways: a
        # non-source extension crashed before it could be judged, and a file the
        # tree scan skips was refused as unrecorded debt. Both must read as a
        # VERDICT -- exit 0, say "out of scope", and owe no record.
        for rel_path, label in ((place(root, "probe/cli-note.md", "# not a source extension\n"),
                                 "a non-source extension"),
                                (place(root, "probe/tests/cli-skipped.sh", "#!/usr/bin/env bash\nset -u\n"),
                                 "a file under tests/")):
            proc = subprocess.run(["bash", script, "--files", rel_path], capture_output=True,
                                  text=True, env=dict(os.environ, AO_CODE_HEADERS_IN_PROVOCATION="1"))
            transcript = proc.stdout + proc.stderr
            arms.expect("`--files` on %s is out of scope, not a crash or a refusal" % label,
                        (proc.returncode, "Traceback" in transcript,
                         "out of scope" in transcript, "0 in scope" in transcript),
                        (0, False, True, True))

        print("== the payload is real YAML (an oracle, when it is importable) ==")
        try:
            import yaml  # noqa: F401  -- an oracle only; the shipping reader has no dependency
        except ImportError:
            yaml = None
        text = fixture("py", VALID)
        lines = text.splitlines()
        marks = [index for index, line in enumerate(lines) if line.strip() == DELIM]
        if yaml is None:
            print("    note  PyYAML is not importable here: the oracle half did not run "
                  "(the dependency-free reader is what ships; this is a note, never a pass)")
        else:
            ours, faults = parse_payload(lines, marks[0], marks[1],
                                         list(range(1, len(lines) + 1)), "py")
            oracle = yaml.safe_load("\n".join(lines[marks[0] + 1:marks[1]]))
            arms.expect("the dependency-free reader agrees with PyYAML on the documented payload",
                        (faults, ours == oracle), ([], True))

        if arms.failed:
            print("check-code-headers: self-test NOT-OK — %d of %d arm(s) failed"
                  % (arms.failed, arms.total), file=sys.stderr)
            return 1
        print("check-code-headers: self-test OK — %d arms, every refusal class provoked both ways"
              % arms.total)
        return 0
    finally:
        shutil.rmtree(base, ignore_errors=True)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def usage():
    print("""Usage:
  bash scripts/check-code-headers.sh                  the gate: provocation + the tree
  bash scripts/check-code-headers.sh --self-test      the provocation alone
  bash scripts/check-code-headers.sh --files F...     scan exactly these files
  bash scripts/check-code-headers.sh --list           the schema the document declares
  bash scripts/check-code-headers.sh --fingerprint F...  the baseline key for these files
  bash scripts/check-code-headers.sh --record P --tracked-by REF   add a debt row
  bash scripts/check-code-headers.sh --prune-stale    drop stale rows
Exit codes: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
Tier seam: AO_CODE_HEADERS_TIER (or --tier), default 0 — legacy debt is advisory below tier 1.""")


def tier_of(options, environ):
    """The declared tier seam: `--tier` wins over AO_CODE_HEADERS_TIER, default 0."""
    tier = options["tier"] if options["tier"] is not None else environ.get("AO_CODE_HEADERS_TIER", "0")
    if tier not in ("0", "1", "2"):
        raise CannotAssess("bad-invocation: tier %r is not 0, 1 or 2" % tier)
    return int(tier)


def parse_args(args):
    """One pass, so a flag's VALUE can never be read as a file path.

    The first version collected every token after `--files` that did not start
    with `--`, which quietly read `--tier 1`'s value as a filename and answered
    CANNOT-ASSESS `no such file: 1` — a plausible-looking refusal caused by a flag
    nobody had asked to be a file. Options that take a value consume it here.
    """
    options = {"files": [], "fingerprint": [], "flags": set(),
               "record": "", "tracked_by": "", "tier": None}
    index = 0
    while index < len(args):
        token = args[index]
        if token in ("--tier", "--record", "--tracked-by"):
            options["flags"].add(token)
            value = args[index + 1] if index + 1 < len(args) else ""
            if token == "--tier":
                options["tier"] = value
            elif token == "--record":
                options["record"] = value
            else:
                options["tracked_by"] = value
            index += 2
            continue
        if token in ("--files", "--fingerprint"):
            # The FLAG is recorded as well as its value: `main` dispatches on
            # membership in `flags`, so a verb that consumes its own value and is
            # never added to the set is absent from every `in flags` test and the
            # invocation falls through to the LAST branch -- the provocation, whose
            # CLI half re-invokes `--files`. Measured before this line existed:
            # the gate forked itself 733 processes deep and left 2256 scratch
            # trees, and `--files` answered nothing at all.
            options["flags"].add(token)
            index += 1
            while index < len(args) and not args[index].startswith("--"):
                options["files" if token == "--files" else "fingerprint"].append(args[index])
                index += 1
            continue
        if token.startswith("--"):
            options["flags"].add(token)
            index += 1
            continue
        options["files"].append(token)
        index += 1
    return options


def main(argv):
    root, args = argv[0], argv[1:]
    if "--help" in args or "-h" in args:
        usage()
        return 0
    if not os.path.isdir(root):
        raise CannotAssess("bad-invocation: %s is not a directory" % root)
    options = parse_args(args)
    flags = options["flags"]
    schema = load_schema(root)
    authorities = load_authorities(root)
    tier = tier_of(options, os.environ)

    if "--list" in flags:
        print("schema read from %s (%d fields):" % (DOC_REL, len(schema)))
        for row in schema:
            print("  %-18s required=%-3s kind=%-15s allowed=%s"
                  % (row["field"], "yes" if row["required"] else "no", row["kind"], row["allowed"]))
        for name in ("class", "tier", "sme"):
            print("%s authority (%d): %s" % (name, len(authorities[name]), ", ".join(sorted(authorities[name]))))
        return 0

    if "--schema" in flags:
        # One line per field, tab-separated: the machine-readable form of the SAME
        # declaration --list renders for a human, so a second tool (the drafter)
        # reads the field list here instead of carrying a copy of it.
        for row in schema:
            print("%s\t%s\t%s\t%s" % (row["field"], "yes" if row["required"] else "no",
                                      row["kind"], row["allowed"]))
        return 0

    if "--snapshot-baseline" in flags:
        return snapshot_baseline(root, schema, authorities)

    if "--fingerprint" in flags:
        if not options["fingerprint"]:
            raise CannotAssess("bad-invocation: --fingerprint needs at least one file")
        for rel in options["fingerprint"]:
            print("%s\t%s" % (rel, baseline_key(root, rel)))
        return 0

    if "--record" in flags:
        if not options["record"] or not options["tracked_by"]:
            raise CannotAssess("bad-invocation: --record needs a path and --tracked-by needs a ref")
        return record(root, options["record"], options["tracked_by"])

    if "--prune-stale" in flags:
        return prune_stale(root, schema, authorities)

    if "--self-test" in flags and not options["files"]:
        return self_test(root)

    if "--files" in flags:
        if not options["files"]:
            raise CannotAssess("bad-invocation: --files needs at least one file")
        return run_tree(root, schema, authorities, tier, files=sorted(options["files"]))

    # Reaching here as a provocation CHILD means the `--files` dispatch above let
    # the invocation through to the default branch: refuse by name rather than
    # provoke again, because the provocation is what spawns the child (the mark is
    # set only by its own CLI half). An unmarked cycle forks this gate until the
    # box is out of processes and reports nothing at all.
    if os.environ.get("AO_CODE_HEADERS_IN_PROVOCATION") == "1":
        raise CannotAssess("self-invocation-cycle: a provocation child must carry --files, "
                           "which scans instead of provoking")

    rc = self_test(root)
    if rc != 0:
        return rc
    return run_tree(root, schema, authorities, tier)


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except CannotAssess as exc:
        print("check-code-headers: CANNOT-ASSESS %s" % exc, file=sys.stderr)
        sys.exit(2)
PY
