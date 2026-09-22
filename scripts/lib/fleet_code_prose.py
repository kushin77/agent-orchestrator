#!/usr/bin/env python3
"""fleet_code_prose.py — find retired role names used as NAMES in fleet/*.py prose.

Issue #923. Extracts exactly the two surfaces the issue names — comments and
module/class/function docstrings — using `tokenize` and `ast`, not a blanket
regex over the file text. Everything else (identifiers, string literals that are
mailbox paths, f-strings that print `[brain]` / `[sister]`, CLI flag values) is
out of scope BY CONSTRUCTION: it is never visited, so it can never be "fixed"
into the rename the issue forbids.

Within a comment or docstring, a retired word is still not a finding when it
names an ARTIFACT rather than a role: an inline code span (`` `brain-inbox` ``),
a path or filename, a `--rung brain` / `--rung sister` flag value, or a printed
tag like `[brain]` / `[sister]`. This mirrors the stripping
`scripts/check-fleet-vocabulary.sh`'s `surface_report` already does for the
Markdown normative surfaces (section 4), reused here rather than reinvented for
Python's syntax.

A retired word inside a marked legacy-gloss region (`# legacy-gloss:start` /
`# legacy-gloss:end`, the Python-comment sibling of the Markdown HTML-comment
markers declared in `governance/vocabulary/fleet.yaml`) is a declared gloss, not
a finding.

A STRUCTURAL IDENTIFIER is not prose either (issue #1974). A `---knowledge---`
block is machine-readable data (docs/CODE-HEADER-STANDARD.md), and its
`module_id:` field carries this file's stable, dotted identity — `fleet.brain`
names the module, it does not name a role. That field's value is therefore
skipped BY NAME, and ONLY that field: the free-text fields (`invariants:`,
`gotchas:`) stay in scope, so a retired term written as prose in the block is
still refused.

Usage:
    python3 scripts/lib/fleet_code_prose.py [FILE ...]

Exit code: 0 if no findings, 1 if any findings (each printed as
"path:line: retired term 'X': <line text>"), 2 on a usage/parse error.

---knowledge---
module_id: scripts.lib.fleet_code_prose
system: scripts
app: lib
solution_class: class
patterns: [tri-state-exit]
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [artifact_stripped, find_findings, main]
invariants: ""
gotchas: ""
related: ["#923"]
do_not_duplicate: null
---knowledge---
"""
from __future__ import annotations

import ast
import io
import re
import sys
import tokenize
from pathlib import Path

RETIRED = ("operator", "brain", "sister", "subagent")
WORD_RE = {term: re.compile(rf"\b{term}\b", re.IGNORECASE) for term in RETIRED}

LEGACY_START = "legacy-gloss:start"
LEGACY_END = "legacy-gloss:end"

# The knowledge block's delimiter (docs/CODE-HEADER-STANDARD.md) and its one
# structural `id` field. A `module_id:` value is this file's identity, not prose
# naming a role, so it is the single field skipped inside the block (#1974).
KNOWLEDGE_DELIM = "---knowledge---"
_KNOWLEDGE_ID_FIELD = re.compile(r"^\s*#?\s*module_id:\s*\S")

# Artifact-reference strippers, applied to a candidate prose line before it is
# checked for a retired word used as a NAME. Order matters: code spans and
# printed tags first (they can contain slashes/dots that the path stripper
# would otherwise eat asymmetrically), then paths/filenames, then flags.
_BACKTICK = re.compile(r"`[^`]*`")
_PRINTED_TAG = re.compile(r"\[(?:operator|brain|sister|subagent)(?:-[\w]+)?\]", re.IGNORECASE)
_FLAG_VALUE = re.compile(r"--?rung[= ]\s*[\w-]+", re.IGNORECASE)
_PATH = re.compile(r"\S*/\S*")
_FILENAME = re.compile(r"[\w.-]+\.(?:py|sh|json|md|ya?ml|txt|lock|log)\b", re.IGNORECASE)
_CODE_IDENT = re.compile(
    r"\b(?:brain|sister|subagent)-[\w-]+\b|\b[\w-]+-(?:brain|sister|subagent)\b",
    re.IGNORECASE,
)


def artifact_stripped(line: str) -> str:
    line = _BACKTICK.sub(" ", line)
    line = _PRINTED_TAG.sub(" ", line)
    line = _FLAG_VALUE.sub(" ", line)
    line = _PATH.sub(" ", line)
    line = _FILENAME.sub(" ", line)
    line = _CODE_IDENT.sub(" ", line)
    return line


def find_findings(path: Path) -> list[tuple[int, str, str]]:
    """Return [(lineno, term, source_line), ...] for retired names used as roles."""
    source = path.read_text(encoding="utf-8")
    lines = source.splitlines()
    findings: list[tuple[int, str, str]] = []

    # --- comments, via tokenize -------------------------------------------------
    in_knowledge = False
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except tokenize.TokenizeError as exc:  # pragma: no cover - defensive
        print(f"{path}: CANNOT-ASSESS — tokenize failed: {exc}", file=sys.stderr)
        raise SystemExit(2)

    # Track legacy-gloss regions by line number first, since a region can span
    # several comment lines and the marker lines themselves are never findings.
    legacy_lines: set[int] = set()
    open_region = False
    region_start = None
    for tok in tokens:
        if tok.type == tokenize.COMMENT:
            text = tok.string
            if LEGACY_START in text:
                open_region = True
                region_start = tok.start[0]
                continue
            if LEGACY_END in text:
                if open_region and region_start is not None:
                    for ln in range(region_start, tok.start[0] + 1):
                        legacy_lines.add(ln)
                open_region = False
                region_start = None
                continue
    if open_region and region_start is not None:
        # Unterminated region: treat the rest of the file as covered rather than
        # silently flagging lines a human marked as intentionally legacy.
        for ln in range(region_start, len(lines) + 1):
            legacy_lines.add(ln)

    for tok in tokens:
        if tok.type != tokenize.COMMENT:
            continue
        lineno = tok.start[0]
        text = tok.string
        if KNOWLEDGE_DELIM in text:
            in_knowledge = not in_knowledge
            continue
        if lineno in legacy_lines:
            continue
        if LEGACY_START in text or LEGACY_END in text:
            continue
        if in_knowledge and _KNOWLEDGE_ID_FIELD.match(text):
            continue
        stripped = artifact_stripped(text)
        for term in RETIRED:
            if WORD_RE[term].search(stripped):
                findings.append((lineno, term, lines[lineno - 1] if lineno <= len(lines) else text))

    # --- docstrings, via ast -----------------------------------------------------
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        print(f"{path}: CANNOT-ASSESS — could not parse: {exc}", file=sys.stderr)
        raise SystemExit(2)

    nodes: list[ast.AST] = [tree]
    for node in ast.walk(tree):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            nodes.append(node)

    for node in nodes:
        doc = ast.get_docstring(node, clean=False)
        if not doc:
            continue
        body = node.body if hasattr(node, "body") else []
        if not body or not isinstance(body[0], ast.Expr):
            continue
        doc_node = body[0]
        # doc_node.lineno is the line the string literal STARTS on (its opening
        # quotes). Docstring text line N (0-indexed within the string) lands at
        # source line doc_node.lineno + N.
        base_line = doc_node.lineno
        in_legacy_doc = False
        in_knowledge_doc = False
        for offset, doc_line in enumerate(doc.splitlines()):
            src_lineno = base_line + offset
            if KNOWLEDGE_DELIM in doc_line:
                in_knowledge_doc = not in_knowledge_doc
                continue
            if LEGACY_START in doc_line:
                in_legacy_doc = True
                continue
            if LEGACY_END in doc_line:
                in_legacy_doc = False
                continue
            if in_legacy_doc:
                continue
            if in_knowledge_doc and _KNOWLEDGE_ID_FIELD.match(doc_line):
                # The block's structural identity field, not prose: this file's
                # stable ``module_id`` names the module, never a role (#1974).
                continue
            stripped = artifact_stripped(doc_line)
            for term in RETIRED:
                if WORD_RE[term].search(stripped):
                    findings.append((src_lineno, term, doc_line.strip()))

    findings.sort(key=lambda item: item[0])
    return findings


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: fleet_code_prose.py FILE [FILE ...]", file=sys.stderr)
        return 2
    total = 0
    for arg in argv:
        path = Path(arg)
        if not path.is_file():
            print(f"{path}: CANNOT-ASSESS — not a file", file=sys.stderr)
            return 2
        findings = find_findings(path)
        for lineno, term, line_text in findings:
            print(f"{path}:{lineno}: retired term '{term}': {line_text[:120]}")
            total += 1
    return 1 if total else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
