"""The futureproof capstone — every classification mechanism's chain, end to end.

The repository has ten classification mechanisms (the operator's list): `class`,
`pattern`, `template`, `rca`, `system`, `app`, `env-var`, `gov`, `issues`,
`index`. Each already has a declared authority and a gate with its own negative
control. What was NOT proven anywhere is the CHAIN per mechanism, and the
measured failure class this proves against is #1164 — a control can exist and be
invoked by NOTHING while the board reads green.

For every mechanism this module proves four links, in order:

  1. `authority-declared`   every declared authority path exists on disk;
  2. `gate-wired`           every declared gate is a real, non-empty
                            `scripts/check-<gate>.sh` that the repository's OWN
                            discovery layer wires into `make verify`
                            (`scripts/discover-checks.sh`) and that
                            `scripts/check-denylist.txt` does not disable;
  3. `gate-falsifiable`     the gate DECLARES a provocation (a negative control,
                            a `--self-test`, a mutation, a declared behavioral
                            control) AND carries a verdict vocabulary — a gate
                            that cannot fail is a formality (GR-12);
  4. `assesses-real-tree`   the gate, RUN on this tree, reaches a VERDICT
                            (rc 0 or 1) rather than CANNOT-ASSESS (rc 2).

Link 4 is the one this capstone exists for, and it is the same doctrine the issue
quotes applied one level deeper. `scripts/verify.sh` maps rc 2 to SKIP and still
prints `verify: PASS (… N skipped)`, so a gate that is PERMANENTLY
CANNOT-ASSESS is invisible: discovered, executable, carrying a negative control,
and assessing nothing. Two live witnesses were measured on pristine
`origin/master` (`04ad55a`):

    bash scripts/check-paperclip-routines.sh  -> rc 2  could not build the dropped-entry tree
    bash scripts/check-dispatch-queue.sh      -> rc 2  could not assess against the current board

Both pass links 1-3 and fail link 4. A gate that never *assesses* is a formality
that hides in the `skipped` bucket.

A mechanism's verdict is one of the four words this repository already uses for
that judgement — `docs/SYSTEM-APP-GOVERNANCE-E2E-GAP-ANALYSIS.md` (issue #1156):

    ENFORCED             every link held, INCLUDING the gate assessing this tree
    DECLARED-ONLY        the authority is declared but the gate cannot fail, or
                         cannot assess (rc 2) — a gate that never assesses
    IMPLEMENTED-UNGATED  the gate exists but the discovery layer does not wire it
    ABSENT               an authority path, or the gate script itself, is missing

Usage:

    python3 governance/futureproof/e2e.py             # the capstone (rc 0/1/2)
    python3 governance/futureproof/e2e.py --list      # the mechanism table
    python3 governance/futureproof/e2e.py --root DIR  # assess ANOTHER tree
    python3 governance/futureproof/e2e.py --json      # the report as JSON

Exit codes: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS — the repository's tri-state, and
CANNOT-ASSESS is never a pass.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

DEFAULT_ROOT = Path(__file__).resolve().parents[2]

OK = 0
NOT_OK = 1
CANNOT_ASSESS = 2

# The four verdict words, borrowed — never re-declared — from the measured
# vocabulary of docs/SYSTEM-APP-GOVERNANCE-E2E-GAP-ANALYSIS.md (issue #1156) so
# two documents cannot disagree about what "enforced" means.
ENFORCED = "ENFORCED"
DECLARED_ONLY = "DECLARED-ONLY"
IMPLEMENTED_UNGATED = "IMPLEMENTED-UNGATED"
ABSENT = "ABSENT"

VERDICTS = (ENFORCED, DECLARED_ONLY, IMPLEMENTED_UNGATED, ABSENT)

# The links, in the order they must hold. `mechanisms-complete` and
# `mechanisms-disjoint` are the repository-wide fourth link of the issue brief;
# they are reported once, not per mechanism, and their two halves are named
# separately so a failure says which half broke.
LINK_AUTHORITY = "authority-declared"
LINK_WIRED = "gate-wired"
LINK_FALSIFIABLE = "gate-falsifiable"
LINK_ASSESSES = "assesses-real-tree"
LINK_COMPLETE = "mechanisms-complete"
LINK_DISJOINT = "mechanisms-disjoint"
PER_MECHANISM_LINKS = (LINK_AUTHORITY, LINK_WIRED, LINK_FALSIFIABLE, LINK_ASSESSES)
REPO_WIDE_LINKS = (LINK_COMPLETE, LINK_DISJOINT)

# The gate source must DECLARE a provocation and carry a verdict vocabulary.
# The provocation vocabulary is deliberately specific — a phrase a gate uses when
# it states that its OWN failure path is exercised — because a token like
# "control" appears in nearly every gate and would make this link vacuous. Two
# measured shapes must be covered: the mutant/self-test family, and the
# "declared behavioral control that must return the verdict it declares" family
# `scripts/check-authority.sh` uses (a narrower list reported it as a false red).
PROVOCATION_TOKENS = (
    "negative control",
    "negative-control",
    "self-test",
    "selftest",
    "selfcheck",
    "self check",
    "provoke",
    "provok",
    "mutation",
    "mutant",
    "clean twin",
    "both ways",
    "declared behavioral control",
    "declared control",
    "must return the verdict it declares",
)
VERDICT_TOKENS = ("FAIL", "REFUSED", "NOT-OK", "CANNOT-ASSESS")

# A per-gate bound. Measured on this tree the whole table runs in ~20 seconds, so
# the default is a safety net for a gate that hangs, not a budget any real gate
# needs. A gate that does not finish is BLIND (it never reached a verdict), never
# a pass.
DEFAULT_GATE_TIMEOUT = 900

# Named, shrinking exemptions for gaps this capstone MEASURED. A gap is honoured
# only while it is listed, and the entry is matched EXACTLY (mechanism + link +
# gate), so it cannot widen to cover a different breakage. Two halves make it a
# ratchet rather than a hiding place:
#   * a broken link with NO entry FAILS by name — a NEW blind or non-falsifiable
#     gate can never be waved through;
#   * an entry that matches nothing FAILS as a stale exemption and must be
#     removed — the list can only shrink, and cannot outlive its fix.
KNOWN_GAPS_FILE = "governance/futureproof/known-gaps.json"

# The futureproof classification surface (the operator's list). For each
# mechanism: the declared AUTHORITY (a path that must exist and be tracked) and
# the GATE(s) that enforce it — named exactly as `scripts/check-<gate>.sh`, which
# is the name `scripts/discover-checks.sh` derives and wires into `make verify`.
MECHANISMS: List[dict] = [
    {
        "id": "class",
        "why": "the quality ladder a surface must reach",
        "authorities": [
            "governance/conformance/policy.yaml",
            "governance/conformance/surfaces.yaml",
        ],
        "gates": ["surface-class", "conformance"],
    },
    {
        "id": "pattern",
        "why": "the pattern canon every shell script is judged against",
        "authorities": ["docs/SHELL-PATTERNS.md"],
        "gates": ["shell-patterns"],
    },
    {
        "id": "template",
        "why": "the issue-form contracts a filed task must satisfy",
        "authorities": [".github/ISSUE_TEMPLATE"],
        "gates": ["issue-template"],
    },
    {
        "id": "rca",
        "why": "the lessons/RCA ledger and the rule that makes a record land",
        "authorities": [
            "governance/lessons/policy.yaml",
            "governance/lessons/ledger.jsonl",
        ],
        "gates": ["lessons"],
    },
    {
        "id": "system",
        "why": "the system-app exemption and the tree it claims",
        "authorities": ["module.json", "docs/MODULE-ADMISSION.md"],
        "gates": ["system-app-declaration"],
    },
    {
        "id": "app",
        "why": "the app-model admission contract",
        "authorities": ["docs/MODULE-ADMISSION.md"],
        "gates": ["module-admission"],
    },
    {
        "id": "env-var",
        "why": "the declared environment-variable surface",
        "authorities": [
            "docs/GIT-ENV-VARIABLES.md",
            "infra/terraform/modules/web-surface/auth-env.json",
        ],
        "gates": ["portal-auth-env"],
    },
    {
        "id": "gov",
        "why": "the golden rules and the authority matrix that scopes them",
        "authorities": ["docs/GOLDEN-RULES.md", "AGENTS.md"],
        "gates": ["authority"],
    },
    {
        "id": "issues",
        "why": "the board: the dispatch contract and the committed snapshot",
        "authorities": ["governance/dispatch/README.md", ".board/snapshot.json"],
        "gates": ["issue-claims"],
    },
    {
        "id": "index",
        "why": "the capability registers and the knowledge catalog",
        "authorities": [
            "docs/CODEIDX-CAPABILITY-REGISTER.md",
            "docs/DIAGRAMS-CAPABILITY-REGISTER.md",
            "governance/knowledge/catalog.json",
        ],
        "gates": ["knowledge-index", "diagrams-capability-register"],
    },
]

# The ten mechanism ids, declared ONCE. A table that silently drops one is the
# defect this link exists to refuse, so the expected set is asserted equal to the
# table's, and a name that is not expected is a drift too.
EXPECTED_MECHANISMS = (
    "class",
    "pattern",
    "template",
    "rca",
    "system",
    "app",
    "env-var",
    "gov",
    "issues",
    "index",
)


class CannotAssess(Exception):
    """The capstone could not form a judgement — reported as rc 2, never a pass."""


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:  # unreadable is CANNOT-ASSESS, never "clean"
        raise CannotAssess("cannot read %s (%s)" % (path, exc))


def discovered_gates(root: Path) -> Set[str]:
    """The gate names the repository's OWN discovery layer wires into make verify.

    This runs `scripts/discover-checks.sh`'s `discover_check_scripts` rather than
    re-deriving the naming convention, so the capstone asserts against the layer
    that actually decides what runs. A tree without that layer is CANNOT-ASSESS:
    the wiring meant to be checked cannot be observed there.
    """
    script = root / "scripts" / "discover-checks.sh"
    if not script.exists():
        raise CannotAssess(
            "scripts/discover-checks.sh is missing — gate-wired cannot be assessed"
        )
    proc = subprocess.run(
        ["bash", "-c", 'source "$1"; discover_check_scripts', "bash", str(script)],
        cwd=str(root),
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise CannotAssess(
            "scripts/discover-checks.sh failed (rc %d): %s"
            % (proc.returncode, (proc.stderr or proc.stdout).strip()[:200])
        )
    names: Set[str] = set()
    for line in proc.stdout.splitlines():
        if "|" in line:
            names.add(line.split("|", 1)[0].strip())
    return names


def denylisted_gates(root: Path) -> Set[str]:
    """The names `scripts/check-denylist.txt` disables (disabled BY NAME)."""
    path = root / "scripts" / "check-denylist.txt"
    if not path.exists():
        return set()
    blocked: Set[str] = set()
    for line in _read(path).splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            blocked.add(line)
    return blocked


def gate_source(root: Path, gate: str) -> Optional[Path]:
    path = root / "scripts" / ("check-%s.sh" % gate)
    return path if path.exists() else None


def wired_problem(
    root: Path, gate: str, discovered: Set[str], blocked: Set[str]
) -> Optional[str]:
    path = gate_source(root, gate)
    if path is None:
        return "gate check-%s.sh does not exist" % gate
    if path.stat().st_size == 0:
        return "gate check-%s.sh is empty (a dead gate)" % gate
    if gate in blocked or path.name in blocked:
        return "gate %s is denylisted (disabled by name, never wired)" % gate
    if gate not in discovered:
        return "gate %s is not discovered into make verify" % gate
    return None


def falsifiable_problem(root: Path, gate: str) -> Optional[str]:
    """The gate must DECLARE a provocation and carry a verdict vocabulary."""
    path = gate_source(root, gate)
    if path is None:
        return "gate check-%s.sh does not exist" % gate
    text = _read(path)
    provocation = sorted({t for t in PROVOCATION_TOKENS if t.lower() in text.lower()})
    verdict = sorted({t for t in VERDICT_TOKENS if t in text})
    if not provocation:
        return (
            "gate check-%s.sh declares no provocation (no negative control, "
            "self-test or mutation) — a gate that cannot fail is a formality" % gate
        )
    if not verdict:
        return "gate check-%s.sh carries no FAIL/REFUSED verdict vocabulary" % gate
    return None


def probe_gate(root: Path, gate: str, timeout: int) -> Tuple[int, str]:
    """RUN the gate on this tree. Returns (rc, first verdict line).

    rc is the gate's own exit code, with 124 standing for "did not finish inside
    `timeout`". Anything that is not 0 or 1 is a gate that reached NO verdict.
    """
    script = root / "scripts" / ("check-%s.sh" % gate)
    try:
        proc = subprocess.run(
            ["bash", str(script)],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return 124, "did not finish inside %ds" % timeout
    except OSError as exc:
        return 127, "could not be run: %s" % exc
    text = (proc.stdout or "") + (proc.stderr or "")
    verdict = ""
    for line in text.splitlines():
        if re.match(r"^\s*(check-)?%s(:| )" % re.escape(gate), line):
            verdict = line.strip()
            break
    if not verdict:
        for line in text.splitlines():
            if "CANNOT-ASSESS" in line or re.search(r":\s*(OK|FAIL)\b", line):
                verdict = line.strip()
                break
    return proc.returncode, verdict[:200]


def assess_mechanism(
    root: Path, mech: dict, discovered: Set[str], blocked: Set[str], timeout: int
) -> dict:
    """One mechanism's chain. Returns the report row for it."""
    broken: List[dict] = []

    for rel in mech["authorities"]:
        if not (root / rel).exists():
            broken.append(
                {"link": LINK_AUTHORITY, "gate": None,
                 "detail": "authority %s does not exist" % rel}
            )

    unwired = False
    for gate in mech["gates"]:
        problem = wired_problem(root, gate, discovered, blocked)
        if problem:
            if "does not exist" in problem or "is empty" in problem:
                unwired = True
            broken.append({"link": LINK_WIRED, "gate": gate, "detail": problem})

    for gate in mech["gates"]:
        if gate_source(root, gate) is None:
            continue  # already named by gate-wired; never reported twice
        problem = falsifiable_problem(root, gate)
        if problem:
            broken.append({"link": LINK_FALSIFIABLE, "gate": gate, "detail": problem})

    probed: List[dict] = []
    if not any(h["link"] in (LINK_WIRED, LINK_FALSIFIABLE) for h in broken):
        for gate in mech["gates"]:
            rc, verdict = probe_gate(root, gate, timeout)
            probed.append({"gate": gate, "rc": rc, "verdict": verdict})
            if rc not in (0, 1):
                broken.append(
                    {
                        "link": LINK_ASSESSES,
                        "gate": gate,
                        "detail": "gate %s is BLIND — rc %d CANNOT-ASSESS, never a "
                                  "pass (%s)" % (gate, rc, verdict or "no verdict line"),
                    }
                )

    if any(h["link"] == LINK_AUTHORITY for h in broken):
        verdict_word = ABSENT
    elif unwired:
        verdict_word = ABSENT
    elif any(h["link"] == LINK_WIRED for h in broken):
        verdict_word = IMPLEMENTED_UNGATED
    elif broken:
        verdict_word = DECLARED_ONLY
    else:
        verdict_word = ENFORCED

    return {
        "mechanism": mech["id"],
        "why": mech["why"],
        "verdict": verdict_word,
        "authorities": list(mech["authorities"]),
        "gates": list(mech["gates"]),
        "broken": broken,
        "probed": probed,
    }


def completeness_problem(mechanisms: Sequence[dict]) -> Optional[str]:
    got = [m["id"] for m in mechanisms]
    missing = [e for e in EXPECTED_MECHANISMS if e not in got]
    extra = [g for g in got if g not in EXPECTED_MECHANISMS]
    if missing or extra:
        return "mechanism set drift — missing %s, extra %s" % (missing, extra)
    return None


def disjointness_problem(mechanisms: Sequence[dict]) -> Optional[str]:
    """No two mechanisms may claim the same gate (a hidden authority conflict)."""
    seen: Dict[str, str] = {}
    clashes: List[str] = []
    for mech in mechanisms:
        for gate in mech["gates"]:
            owner = seen.get(gate)
            if owner is not None and owner != mech["id"]:
                clashes.append("%s (claimed by %s and %s)" % (gate, owner, mech["id"]))
            seen[gate] = mech["id"]
    if clashes:
        return "a gate is claimed by two mechanisms: %s" % ", ".join(sorted(clashes))
    return None


def load_known_gaps(root: Path) -> List[dict]:
    """The named exemptions. A MISSING file is an empty list, never a bypass.

    Missing means "no exemptions", so every measured breakage is a failure — the
    fail-closed direction. A malformed file is CANNOT-ASSESS: an exemption list
    that cannot be read must never be read as "no exemptions needed" silently.
    """
    path = root / KNOWN_GAPS_FILE
    if not path.exists():
        return []
    try:
        doc = json.loads(_read(path))
    except json.JSONDecodeError as exc:
        raise CannotAssess("%s is not valid JSON (%s)" % (KNOWN_GAPS_FILE, exc))
    entries = doc.get("entries")
    if not isinstance(entries, list):
        raise CannotAssess("%s has no 'entries' list" % KNOWN_GAPS_FILE)
    out: List[dict] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise CannotAssess("%s has a non-object entry" % KNOWN_GAPS_FILE)
        for key in ("mechanism", "link", "gate", "issue", "reason"):
            if key not in entry:
                raise CannotAssess(
                    "%s entry is missing '%s': %r" % (KNOWN_GAPS_FILE, key, entry)
                )
        out.append(entry)
    return out


def validate_known_gaps(gaps: Sequence[dict]) -> List[dict]:
    """An exemption may name only a real mechanism and a real per-mechanism link."""
    problems: List[dict] = []
    for entry in gaps:
        if entry["mechanism"] not in EXPECTED_MECHANISMS:
            problems.append(
                {"link": "known-gap-invalid",
                 "detail": "exemption names unknown mechanism %r" % entry["mechanism"]}
            )
        if entry["link"] not in PER_MECHANISM_LINKS:
            problems.append(
                {"link": "known-gap-invalid",
                 "detail": "exemption names unknown link %r" % entry["link"]}
            )
    return problems


def report(root: Path, mechanisms: Sequence[dict], timeout: int) -> dict:
    discovered = discovered_gates(root)
    blocked = denylisted_gates(root)
    rows = [assess_mechanism(root, m, discovered, blocked, timeout) for m in mechanisms]

    gaps = load_known_gaps(root)
    used: Set[int] = set()
    known: List[dict] = []
    for row in rows:
        for hit in row["broken"]:
            for i, entry in enumerate(gaps):
                if (entry["mechanism"] == row["mechanism"]
                        and entry["link"] == hit["link"]
                        and entry["gate"] == hit["gate"]):
                    used.add(i)
                    known.append(
                        {"mechanism": row["mechanism"], "link": hit["link"],
                         "gate": hit["gate"], "issue": entry["issue"],
                         "reason": entry["reason"], "detail": hit["detail"]}
                    )
                    row.setdefault("known_gaps", []).append(entry)

    repo_wide: List[dict] = []
    problem = completeness_problem(mechanisms)
    if problem:
        repo_wide.append({"link": LINK_COMPLETE, "detail": problem})
    problem = disjointness_problem(mechanisms)
    if problem:
        repo_wide.append({"link": LINK_DISJOINT, "detail": problem})
    repo_wide.extend(validate_known_gaps(gaps))
    for i, entry in enumerate(gaps):
        if i not in used:
            repo_wide.append(
                {"link": "known-gap-stale",
                 "detail": "exemption %s/%s gate %s matches nothing — the gap has "
                           "closed (or moved); remove the entry from %s"
                           % (entry["mechanism"], entry["link"], entry["gate"],
                              KNOWN_GAPS_FILE)}
            )

    return {"root": str(root), "mechanisms": rows, "known_gaps": known,
            "repo_wide": repo_wide}


def _failure_hits(rep: dict) -> List[Tuple[str, str, dict]]:
    """Every breakage that is NOT a listed, matched exemption."""
    out: List[Tuple[str, str, dict]] = []
    for row in rep["mechanisms"]:
        exempt = {(e["link"], e["gate"]) for e in row.get("known_gaps", [])}
        for hit in row["broken"]:
            if (hit["link"], hit["gate"]) in exempt:
                continue
            out.append((row["mechanism"], hit["link"], hit))
    return out


def render(rep: dict) -> Tuple[int, List[str]]:
    lines: List[str] = []
    rows = rep["mechanisms"]
    lines.append("futureproof-e2e: the classification mechanisms, end to end")
    lines.append("  root: %s" % rep["root"])
    lines.append("")
    lines.append("  %-12s %-20s %s" % ("MECHANISM", "VERDICT", "GATES"))
    for row in rows:
        lines.append(
            "  %-12s %-20s %s"
            % (row["mechanism"], row["verdict"], ", ".join(row["gates"]))
        )
    lines.append("")

    for hit in rep["known_gaps"]:
        lines.append(
            "  KNOWN  mechanism '%s' fails %s (gate %s) — tracked by #%s"
            % (hit["mechanism"], hit["link"], hit["gate"], hit["issue"])
        )

    failures = _failure_hits(rep)
    for mech, link, hit in failures:
        lines.append(
            "  FAIL  mechanism '%s' fails %s — %s" % (mech, link, hit["detail"])
        )
    for hit in rep["repo_wide"]:
        lines.append("  FAIL  %s — %s" % (hit["link"], hit["detail"]))

    counts = {v: 0 for v in VERDICTS}
    for row in rows:
        counts[row["verdict"]] = counts.get(row["verdict"], 0) + 1
    lines.append("")
    lines.append(
        "  verdicts: " + " · ".join("%s=%d" % (v, counts.get(v, 0)) for v in VERDICTS)
    )
    for link in PER_MECHANISM_LINKS:
        held = 0
        for row in rows:
            exempt = {(e["link"], e["gate"]) for e in row.get("known_gaps", [])}
            if all(h["link"] != link or (h["link"], h["gate"]) in exempt
                   for h in row["broken"]):
                held += 1
        lines.append("  link %-20s %d/%d mechanism(s)" % (link, held, len(rows)))
    lines.append("")

    total = len(failures) + len(rep["repo_wide"])
    if total:
        lines.append(
            "futureproof-e2e: FAIL — %d broken link(s) across %d mechanism(s)"
            % (total, len(rows))
        )
        return NOT_OK, lines
    exempt_note = ""
    if rep["known_gaps"]:
        names = sorted({str(h["issue"]) for h in rep["known_gaps"]})
        exempt_note = " · %d named exemption(s) tracked by #%s" % (
            len(rep["known_gaps"]), ", #".join(names))
    lines.append(
        "futureproof-e2e: PASS — %d mechanism(s) [%s]; every gate assessed this "
        "tree%s"
        % (
            len(rows),
            " · ".join("%s=%d" % (v, counts.get(v, 0)) for v in VERDICTS),
            exempt_note,
        )
    )
    return OK, lines


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="futureproof-e2e",
        description="Prove every classification mechanism's chain end to end.",
    )
    parser.add_argument("--root", default=str(DEFAULT_ROOT),
                        help="the tree to assess (default: this repository)")
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    parser.add_argument("--list", action="store_true",
                        help="print the mechanism table and exit")
    parser.add_argument("--mechanism", action="append", default=None,
                        help="assess only this mechanism (repeatable)")
    parser.add_argument("--timeout", type=int, default=DEFAULT_GATE_TIMEOUT,
                        help="per-gate bound in seconds (default: %d)"
                             % DEFAULT_GATE_TIMEOUT)
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    if not root.is_dir():
        print("futureproof-e2e: CANNOT-ASSESS — %s is not a directory" % root,
              file=sys.stderr)
        return CANNOT_ASSESS

    table = MECHANISMS
    if args.mechanism:
        wanted = set(args.mechanism)
        unknown = sorted(wanted - {m["id"] for m in MECHANISMS})
        if unknown:
            print("futureproof-e2e: CANNOT-ASSESS — unknown mechanism(s): %s"
                  % ", ".join(unknown), file=sys.stderr)
            return CANNOT_ASSESS
        table = [m for m in MECHANISMS if m["id"] in wanted]

    if args.list:
        for mech in table:
            print("%-10s %-28s %s" % (mech["id"], " ".join(mech["gates"]), mech["why"]))
        return OK

    try:
        rep = report(root, table, args.timeout)
    except CannotAssess as exc:
        print("futureproof-e2e: CANNOT-ASSESS — %s" % exc, file=sys.stderr)
        return CANNOT_ASSESS

    rc, lines = render(rep)
    if args.json:
        print(json.dumps(rep, indent=2, sort_keys=True))
    else:
        for line in lines:
            print(line)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
