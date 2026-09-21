#!/usr/bin/env python3
"""fleet_prose_sweep.py — mechanically rename retired ROLE NAMES in fleet/*.py
comments and docstrings to the declared vocabulary (issue #923).

Reuses fleet_code_prose's protected-span strippers so a word inside an inline
code span, a printed tag (`[brain]`), a `--rung brain` flag value, a path/
filename, or a hyphenated code identifier (`brain-signed`, `subagent-<name>`)
is left untouched — only a retired word used as a ROLE NAME in prose is
rewritten. It never touches string literals, f-strings, or identifiers: only
`tokenize.COMMENT` tokens and `ast.get_docstring` text are visited.

Usage:
    python3 scripts/lib/fleet_prose_sweep.py FILE [FILE ...]

Rewrites files in place. Prints one line per line changed.

---knowledge---
module_id: scripts.lib.fleet_prose_sweep
system: scripts
app: lib
solution_class: class
patterns: [shared-helper-library]
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [protected_spans, in_any_span, rewrite_line, sweep_file, main]
invariants: ""
gotchas: ""
related: ["#923"]
do_not_duplicate: null
---knowledge---
"""
from __future__ import annotations

import re
import sys
import tokenize
import io
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from fleet_code_prose import (  # noqa: E402
    RETIRED,
    LEGACY_START,
    LEGACY_END,
    _BACKTICK,
    _PRINTED_TAG,
    _FLAG_VALUE,
    _PATH,
    _FILENAME,
    _CODE_IDENT,
)

REPLACEMENTS = {
    "operator": "principal",
    "brain": "director",
    "sister": "dispatcher",
    "subagent": "executor",
}

PROTECTORS = (_BACKTICK, _PRINTED_TAG, _FLAG_VALUE, _PATH, _FILENAME, _CODE_IDENT)


def protected_spans(line: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for pattern in PROTECTORS:
        for m in pattern.finditer(line):
            spans.append(m.span())
    return spans


def in_any_span(pos: int, spans: list[tuple[int, int]]) -> bool:
    return any(start <= pos < end for start, end in spans)


WORD_RE = re.compile(r"\b(operator|brain|sister|subagent)(s)?\b", re.IGNORECASE)


def rewrite_line(line: str) -> tuple[str, bool]:
    if LEGACY_START in line or LEGACY_END in line:
        return line, False
    spans = protected_spans(line)
    changed = False

    def repl(m: re.Match) -> str:
        nonlocal changed
        if in_any_span(m.start(), spans):
            return m.group(0)
        base = m.group(1)
        suffix = m.group(2) or ""
        new = REPLACEMENTS[base.lower()]
        if base.isupper():
            new = new.upper()
        elif base[0].isupper():
            new = new.capitalize()
        changed = True
        return new + suffix

    new_line = WORD_RE.sub(repl, line)
    return new_line, changed


def sweep_file(path: Path) -> int:
    source = path.read_text(encoding="utf-8")
    lines = source.splitlines(keepends=True)
    changed_count = 0

    # Find legacy-gloss region line ranges (1-indexed) to skip entirely.
    legacy_lines: set[int] = set()
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except tokenize.TokenizeError:
        return 0
    open_region = False
    region_start = None
    for tok in tokens:
        if tok.type == tokenize.COMMENT:
            if LEGACY_START in tok.string:
                open_region = True
                region_start = tok.start[0]
            elif LEGACY_END in tok.string:
                if open_region and region_start is not None:
                    for ln in range(region_start, tok.start[0] + 1):
                        legacy_lines.add(ln)
                open_region = False
                region_start = None
    if open_region and region_start is not None:
        for ln in range(region_start, len(lines) + 1):
            legacy_lines.add(ln)

    # Rewrite comment tokens.
    comment_linenos = {
        tok.start[0] for tok in tokens if tok.type == tokenize.COMMENT
    } - legacy_lines

    # Docstrings are inside triple-quoted STRING tokens that are the first
    # statement of a module/class/function. Simpler and robust: just apply the
    # same line-rewrite to every physical line that tokenize marks as (part of)
    # a COMMENT, and separately handle docstrings via a raw string-line scan
    # driven by ast in a second pass below.
    for lineno in sorted(comment_linenos):
        idx = lineno - 1
        new_line, did_change = rewrite_line(lines[idx])
        if did_change:
            lines[idx] = new_line
            changed_count += 1

    source_after_comments = "".join(lines)

    # Docstrings, via ast on the (possibly comment-updated) source.
    import ast

    tree = ast.parse(source_after_comments, filename=str(path))
    doc_nodes = [tree] + [
        n for n in ast.walk(tree)
        if isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    lines2 = source_after_comments.splitlines(keepends=True)
    for node in doc_nodes:
        doc = ast.get_docstring(node, clean=False)
        if not doc:
            continue
        body = getattr(node, "body", [])
        if not body or not isinstance(body[0], ast.Expr):
            continue
        base_line = body[0].lineno
        n_doc_lines = len(doc.splitlines())
        for offset in range(n_doc_lines):
            src_lineno = base_line + offset
            if src_lineno in legacy_lines:
                continue
            idx = src_lineno - 1
            if idx < 0 or idx >= len(lines2):
                continue
            new_line, did_change = rewrite_line(lines2[idx])
            if did_change:
                lines2[idx] = new_line
                changed_count += 1

    final_source = "".join(lines2)
    if final_source != source:
        path.write_text(final_source, encoding="utf-8")
    return changed_count


def main(argv: list[str]) -> int:
    total = 0
    for arg in argv:
        path = Path(arg)
        n = sweep_file(path)
        if n:
            print(f"{path}: {n} line(s) rewritten")
        total += n
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
