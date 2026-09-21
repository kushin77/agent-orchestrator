#!/usr/bin/env python3
"""Regenerate the "## Canonical docs" table in docs/README.md (issue #1672).

---knowledge---
module_id: scripts.docs-index-fix
system: scripts
app: scripts
solution_class: class
patterns: [union-then-regenerate]
derives_from: docs/README.md
owner_sme: docs-sme
tier: L1
interfaces: [first_h1, main]
invariants: "every lane runs this rather than hand-editing the table directly"
gotchas: ""
related: ["#1672"]
do_not_duplicate: null
---knowledge---

Union-then-regenerate: every lane that adds a docs/*.md file runs this
(`make docs-index` / `check-docs.sh --fix`) instead of hand-editing the
index, so two lanes adding different files never conflict on the same
insertion point — a merge conflict resolves to running this again.

Existing rows (any file already linked anywhere in docs/README.md) are kept
verbatim, in whatever section they live in, so curated purpose text is never
clobbered. Only files NOT yet linked anywhere in the file are added, as new
rows in the "## Canonical docs" table, with purpose text pulled from the
file's first H1. The table is then re-sorted by link target so the same
file set always produces the same byte-for-byte table, regardless of the
order lanes added rows in.
"""
import glob
import re
import sys

README = "docs/README.md"
EXCLUDED_DIRS = ("docs/decision-records/", "docs/spikes/", "docs/contracts/",
                  "docs/rca/", "docs/rollback/")
SECTION_HEADER = "## Canonical docs"


def first_h1(path):
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("# "):
                    return line[2:].strip()
    except OSError:
        pass
    return "purpose not yet written up; fill this in (scripts/docs-index-fix.py)"


def main():
    with open(README, encoding="utf-8") as fh:
        text = fh.read()
    lines = text.splitlines(keepends=True)

    indexed = set(re.findall(r"\]\(([^)]+)\)", text))

    all_docs = sorted(set(glob.glob("docs/*.md")) | set(glob.glob("docs/**/*.md", recursive=True)))
    missing = []
    for f in all_docs:
        if f == README:
            continue
        if any(f.startswith(d) for d in EXCLUDED_DIRS):
            continue
        rel = f[len("docs/"):]
        if rel in indexed or f in indexed:
            continue
        missing.append(f)

    if not missing:
        print("docs-index-fix: nothing to add, index already covers every tracked doc")

    # Locate the "## Canonical docs" table: header line, "| Doc | Purpose |",
    # separator, then rows until the first non-"| " line.
    try:
        hidx = next(i for i, l in enumerate(lines) if l.strip() == SECTION_HEADER)
    except StopIteration:
        print(f"docs-index-fix: FAIL — {SECTION_HEADER!r} not found in {README}", file=sys.stderr)
        return 2
    row_start = hidx + 3  # header, "| Doc | Purpose |", "|---|---|"
    row_end = row_start
    while row_end < len(lines) and lines[row_end].startswith("|"):
        row_end += 1

    existing_rows = [l for l in lines[row_start:row_end] if l.strip()]

    new_rows = []
    for f in missing:
        rel = f[len("docs/"):]
        purpose = first_h1(f)
        new_rows.append(f"| [`{rel}`]({rel}) | {purpose} |\n")

    def sort_key(row):
        m = re.search(r"\]\(([^)]+)\)", row)
        return m.group(1) if m else row

    all_rows = sorted(existing_rows + new_rows, key=sort_key)

    lines[row_start:row_end] = all_rows
    with open(README, "w", encoding="utf-8") as fh:
        fh.writelines(lines)

    for f in missing:
        print(f"docs-index-fix: added {f} to {SECTION_HEADER}")
    print(f"docs-index-fix: {README} regenerated ({len(all_rows)} rows in {SECTION_HEADER})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
