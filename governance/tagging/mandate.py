"""The tag authority's mandate — the constitution must declare it (issue #1183).

---knowledge---
module_id: governance.tagging.mandate
system: governance
app: tagging
solution_class: pattern
patterns: [provoked-negative-control]
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [contract, check, main]
invariants: ""
gotchas: ""
related: ["#1175", "#1183"]
do_not_duplicate: null
---knowledge---

A rule that only prose carries is advice (GR-29 / AO-GR-4: *a rule in a document
is advisory until its gate ships*). The tag authority has had its behavioural
half since #1175 — five checks, eleven provoked refusals, artifact round trips —
and nothing in the constitution said a governed artifact must be tagged, and
nothing would have noticed if a doc stopped saying so.

This module is the declarative half. It asserts that the repository's **contract
documents** declare the tag authority with a fixed marker vocabulary, and it
FAILS naming the document and the marker that went missing. The shape is copied
deliberately from `scripts/check-chronological-dispatch.sh`, which does exactly
this for the chronological-dispatch rule: a list of docs, a required vocabulary,
extra markers demanded of `AGENTS.md`, and a behavioural half proved by
provocation. Reusing the shape is the point — a second mechanism for making rules
canonical would be a second thing to keep honest.

The markers are chosen to be the things a reader must be able to find, not a
fingerprint of any particular wording:

* every contract doc must name the **tag authority** and both new dimensions,
  **posture** and **lifecycle**;
* `AGENTS.md` must additionally name the **authority file** and the **gate**, so
  the constitution points at the mechanism rather than gesturing at a principle;
* `docs/EXECUTION-PLAN.md` must declare the per-lane **Tag declaration**;
* `docs/QA-GATE.md` must name the **tagging** check it carries;
* `docs/GOLDEN-RULES.md` must carry the numbered spine entry, **AO-GR-28**.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

import model as M  # noqa: E402

CODE_MISSING_DOC = M.CODE_MANDATE_MISSING_DOC
CODE_MISSING_MARKER = M.CODE_MANDATE_MISSING_MARKER

#: The marker vocabulary EVERY contract document must declare, case-sensitively.
SHARED_MARKERS: Tuple[str, ...] = ("tag authority", "posture", "lifecycle")

#: Per-document extra markers, keyed by the doc's repository-relative path.
EXTRA_MARKERS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("AGENTS.md", ("governance/tagging/taxonomy.yaml", "scripts/check-tagging.sh")),
    ("docs/GOLDEN-RULES.md", ("AO-GR-28",)),
    ("docs/EXECUTION-PLAN.md", ("Tag declaration",)),
    ("docs/QA-GATE.md", ("tagging",)),
)

#: The contract documents. Order is the order they are reported in.
DOCS: Tuple[str, ...] = (
    "AGENTS.md",
    "docs/GOLDEN-RULES.md",
    "docs/GOVERNANCE.md",
    "docs/EXECUTION-PLAN.md",
    "docs/QA-GATE.md",
)


def contract() -> Tuple[Tuple[str, Tuple[str, ...]], ...]:
    """Each contract doc with the full marker set it must declare."""
    extras = dict(EXTRA_MARKERS)
    return tuple((doc, SHARED_MARKERS + extras.get(doc, ())) for doc in DOCS)


def check(root: Path) -> Tuple[M.Finding, ...]:
    """Every contract doc and every missing marker, as findings.

    ``root`` is the tree to read, so a provocation can point this at a scratch
    copy and require the refusal by name without touching the repository.
    """
    findings = []
    for doc, markers in contract():
        path = Path(root) / doc
        if not path.is_file():
            findings.append(
                M.Finding(
                    CODE_MISSING_DOC,
                    "the contract document %s is missing" % doc,
                    subject=doc,
                    remediation=(
                        "restore it — the tag authority is declared there, and a "
                        "rule no document carries is advice (GR-29)"
                    ),
                )
            )
            continue
        text = path.read_text(encoding="utf-8")
        for marker in markers:
            if marker not in text:
                findings.append(
                    M.Finding(
                        CODE_MISSING_MARKER,
                        "%s no longer declares %r" % (doc, marker),
                        subject=doc,
                        remediation=(
                            "declare the tag authority and its dimensions in this "
                            "document — the mandate is what keeps the rule "
                            "constitutional rather than advisory"
                        ),
                    )
                )
    return tuple(findings)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="mandate",
        description="Assert the contract documents declare the tag authority.",
    )
    parser.add_argument("--root", default="", help="the tree to read (default: the repo)")
    parser.add_argument("--list", action="store_true", help="list the required markers")
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    if args.list:
        for doc, markers in contract():
            print("%s" % doc)
            for marker in markers:
                print("    %s" % marker)
        return 0

    root = Path(args.root) if args.root else Path(__file__).resolve().parents[2]
    findings = check(root)
    for finding in findings:
        print("  FAIL  %-20s %s" % (finding.code, finding.message))
        if finding.remediation:
            print("        -> %s" % finding.remediation)
    if findings:
        print(
            "tagging-mandate: FAIL — %d marker(s) missing across %d contract doc(s)"
            % (len(findings), len({f.subject for f in findings}))
        )
        return 1
    total = sum(len(markers) for _, markers in contract())
    print(
        "tagging-mandate: OK — %d marker(s) declared across %d contract doc(s)"
        % (total, len(DOCS))
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
