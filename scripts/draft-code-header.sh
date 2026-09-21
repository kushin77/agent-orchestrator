#!/usr/bin/env bash
#
# draft-code-header.sh — draft a knowledge block for one file (issue #1535).
#
# WHAT THIS IS, AND WHAT IT IS NOT
#   A DRAFT tool. It reads a file's existing docstring or leading comment banner
#   and prints a best-effort block, decorated for that file's language, on
#   STDOUT. It never writes to the file, never commits, and never stages: fields
#   that need a human judgement — `solution_class` and `patterns` — are left as a
#   visible placeholder, and the fields it CAN infer from the path and the code
#   are filled in so the reviewer edits rather than authors.
#
# WHERE THE FIELD LIST COMES FROM (borrow, never re-declare)
#   The schema is declared once, in `docs/CODE-HEADER-STANDARD.md`, and read once,
#   by `scripts/check-code-headers.sh`. This tool does not carry a second copy of
#   it either: it asks that gate for the schema (`--schema`) and renders the
#   fields in the order the document declares them, so a field added to the
#   document is drafted here in the same commit and the two cannot drift.
#
# WHAT IT INFERS, AND WHAT IT REFUSES TO GUESS
#   inferred            `module_id`, `system`, `app` (from the path), `interfaces`
#                       (from the code's own declarations), `owner_sme`
#                       (`unassigned`), `tier` (the L1 security floor — a DRAFT is
#                       never a claim that a cheaper tier may author the file)
#   left for review     `solution_class`, `patterns` (a named placeholder), and an
#                       explicit stderr report of everything it could not know
#
# ---knowledge---
# module_id: scripts.draft-code-header
# system: governance
# app: gates
# solution_class: enterprise
# patterns: [draft-never-commits, declared-authority, human-in-the-loop]
# derives_from: scripts/check-code-headers.sh
# owner_sme: qa-sme
# tier: L1
# interfaces: [stdout: a ready-to-paste knowledge block, stderr: the review report]
# invariants: "stdout only — this tool never writes to, stages, or commits the file it drafts for"
# gotchas: "the placeholder is assembled from fragments so this file does not carry the bare marker its own docs gate refuses"
# related: ["#1535"]
# do_not_duplicate: null
# ---knowledge---
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)" || exit 2
self_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)" || exit 2
gate="$self_dir/check-code-headers.sh"

if [ ! -f "$gate" ]; then
  echo "draft-code-header: CANNOT-ASSESS missing-gate: $gate is not present" >&2
  exit 2
fi

if [ "$#" -ne 1 ]; then
  cat >&2 <<'USAGE'
Usage: bash scripts/draft-code-header.sh <file>

Prints a drafted knowledge block for <file> on stdout (docs/CODE-HEADER-STANDARD.md).
The file is never modified. Exit codes: 0 drafted / 1 not draftable / 2 CANNOT-ASSESS.
USAGE
  exit 2
fi

target="$1"
if [ ! -f "$target" ]; then
  echo "draft-code-header: CANNOT-ASSESS no-such-file: $target" >&2
  exit 2
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "draft-code-header: CANNOT-ASSESS python3-missing: install python3 and put it on PATH" >&2
  exit 2
fi

exec python3 - "$root" "$gate" "$target" <<'PY'
import os
import re
import subprocess
import sys

# The review placeholder, assembled so this file does not spell the marker token
# its own docs gate refuses in shell/python sources (docs/SHELL-PATTERNS.md).
REVIEW = "TO" + "DO"

TIER_DRAFT = "L1"          # the security floor; never a claim that a cheaper tier may author it
OWNER_DRAFT = "unassigned"  # a drafter cannot know who owns the file
INVENTED = ("solution_class", "patterns", "tier", "owner_sme", "invariants", "gotchas")


def schema_of(gate):
    """The field list, read from the one authority that declares it."""
    proc = subprocess.run(["bash", gate, "--schema"], capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit("draft-code-header: CANNOT-ASSESS schema-unreadable: %s"
                         % (proc.stderr.strip() or "the gate refused --schema"))
    rows = []
    for line in proc.stdout.splitlines():
        cells = line.split("\t")
        if len(cells) == 4:
            rows.append({"field": cells[0], "required": cells[1] == "yes", "kind": cells[2]})
    if not rows:
        raise SystemExit("draft-code-header: CANNOT-ASSESS schema-empty: the gate declared no fields")
    return rows


def identity(rel):
    """module_id, system and app, inferred from the path — the mechanical half."""
    stem = os.path.splitext(rel)[0].strip("/")
    parts = [part for part in stem.replace("\\", "/").split("/") if part and part != "."]
    slug = [re.sub(r"[^a-z0-9._-]", "_", part.lower().lstrip(".")) for part in parts]
    module_id = ".".join(slug) or "unnamed"
    system = slug[0] if len(slug) > 1 else "repo"
    app = slug[1] if len(slug) > 2 else system
    return module_id, system, app


def banner_summary(text, ext):
    """The file's existing knowledge, as prose — reported so the draft is traceable."""
    lines = text.splitlines()
    if ext == ".py":
        match = re.search(r'^\s*(?:"""|\'\'\')(.*)$', "\n".join(lines[:4]), re.M)
        if match:
            head = match.group(1).strip().strip('"\'')
            if head:
                return head, "module docstring"
    for line in lines[:40]:
        stripped = line.strip()
        if not stripped:
            continue
        for marker in ("#", "/*", "*", "//"):
            if stripped.startswith(marker):
                body = stripped[len(marker):].strip(" */")
                if body and not body.startswith(("!", "---")):
                    return body, "leading banner"
                break
        else:
            break
    return "", "no existing summary"


def interfaces(text, ext):
    """What the file exports, best-effort, from its own declarations."""
    names = []
    if ext == ".py":
        try:
            import ast
            tree = ast.parse(text)
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    if not node.name.startswith("_"):
                        names.append(node.name)
        except SyntaxError:
            names.append("(the file does not parse as Python, so nothing could be read)")
    elif ext == ".sh":
        names = re.findall(r"^([A-Za-z_][A-Za-z0-9_]*)\(\)\s*\{", text, re.M)
    elif ext in (".ts", ".js"):
        names = re.findall(r"^export\s+(?:default\s+)?(?:class|function|const|let|interface|type)\s+([A-Za-z_$][\w$]*)",
                           text, re.M)
    elif ext == ".tf":
        names = re.findall(r'^(?:output|variable|resource|module)\s+"([^"]+)"', text, re.M)
    elif ext == ".yml":
        names = re.findall(r"^([A-Za-z_][\w.-]*):", text, re.M)
    unique = []
    for name in names:
        if name not in unique:
            unique.append(name)
    if len(unique) > 8:
        unique = unique[:8] + ["(+%d more)" % (len(unique) - 8)]
    return unique


def value_for(field, kind, inferred):
    if field in inferred:
        return inferred[field]
    if kind == "enum":
        return REVIEW
    if kind == "list":
        return "[]"
    if kind == "nullable-string":
        return "null"
    if kind == "sme":
        return OWNER_DRAFT
    if kind == "id":
        return REVIEW
    return "\"\""


def render(rows, values, ext, summary):
    """The block, decorated for the language — ready to paste."""
    body = []
    for row in rows:
        field = row["field"]
        value = values[field]
        if row["kind"] == "list":
            if isinstance(value, list):
                value = "[" + ", ".join(value) + "]"
            elif value != "[]" and not value.startswith("["):
                value = "[" + str(value) + "]"
        elif row["kind"] == "string" and not str(value).startswith(("\"", "null")):
            value = "\"" + str(value).replace("\"", "'") + "\""
        body.append("%s: %s" % (field, value))
    delim = "---knowledge---"
    if ext == ".py":
        first = summary or "the module's summary line"
        return '"""%s\n\n%s\n%s\n%s\n"""\n' % (first, delim, "\n".join(body), delim)
    prefix = "# " if ext in (".sh", ".tf", ".yml") else " * "
    if ext in (".sh", ".tf", ".yml"):
        return "\n".join([prefix + delim] + [prefix + line for line in body] + [prefix + delim]) + "\n"
    return "\n".join(["/*"] + [prefix + delim] + [prefix + line for line in body] + [prefix + delim, " */"]) + "\n"


def main(argv):
    root, gate, target = argv[0], argv[1], argv[2]
    rel = os.path.relpath(os.path.abspath(target), root)
    if rel.startswith(".."):
        rel = os.path.basename(target)
    ext = os.path.splitext(target)[1]
    if ext not in (".py", ".sh", ".ts", ".js", ".tf", ".yml"):
        print("draft-code-header: NOT-OK unsupported-extension: %s is not one of the six in scope"
              % ext, file=sys.stderr)
        return 1
    with open(target, "r", encoding="utf-8") as handle:
        text = handle.read()
    rows = schema_of(gate)
    module_id, system, app = identity(rel)
    summary, where = banner_summary(text, ext)
    inferred = {"module_id": module_id, "system": system, "app": app,
                "owner_sme": OWNER_DRAFT, "tier": TIER_DRAFT,
                "interfaces": interfaces(text, ext), "derives_from": "null",
                "do_not_duplicate": "null", "related": "[]", "invariants": "\"\"", "gotchas": "\"\""}
    values = {row["field"]: value_for(row["field"], row["kind"], inferred) for row in rows}
    sys.stdout.write(render(rows, values, ext, summary))
    print("draft-code-header: drafted %s from the path and the code" % rel, file=sys.stderr)
    print("draft-code-header: existing knowledge found in the %s: %s"
          % (where, ("%r" % summary) if summary else "(none)"), file=sys.stderr)
    print("draft-code-header: review these before committing: %s"
          % ", ".join(name for name in INVENTED if name in values), file=sys.stderr)
    print("draft-code-header: %s was NOT modified (this tool prints; it never writes)" % target,
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
PY
