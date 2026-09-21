"""Tagging CLI — the tag authority's verbs (issue #1175).

---knowledge---
module_id: governance.tagging.cli
system: governance
app: tagging
solution_class: enterprise
patterns: [provoked-negative-control, no-false-green, honesty-tri-state, injected-effects, bounded-work]
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [repo_root, cmd_lint, cmd_check, cmd_plan, cmd_matrix, cmd_schema, cmd_board, parse_classification, derive_pr_labels, cmd_pr_labels, (+3 more)]
invariants: ""
gotchas: ""
related: ["#322", "#1175", "#1179", "#1254", "#1328", "#1427"]
do_not_duplicate: null
---knowledge---

    lint     judge the authority itself: taxonomy shape, drift against every
             borrowed authority, rule/gate resolution, refusal-set completeness,
             the frozen shapes and the declared controls
    check    lint + prove the committed matrix is the generated one (no staleness)
    plan     derive the gates a tag set requires, by channel (pr/ci/cd/ops)
    matrix   render the tag -> gate matrix (stdout, or --write into docs/)
    board    judge the board's declared tags against the taxonomy (--live projects)
    labels   emit the `gh label create` commands that mint the new vocabulary
    pr-labels derive/apply the class:/posture:/lifecycle:/pillar: labels a
             PR's own `## Classification` block implies (issue #1254 step 5c
             / #1328)
    schema   print the frozen shapes of the authority's own artifacts

Exit codes follow the repository's tri-state convention (guardrails/honesty):
0 OK / 1 NOT-OK / 2 CANNOT-ASSESS. CANNOT-ASSESS is never reported as a pass.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ledger as L  # noqa: E402
import live as LV  # noqa: E402
import model as M  # noqa: E402
import policy as P  # noqa: E402
import schema as S  # noqa: E402

# NOTE: this is a DIFFERENT staleness policy from
# governance/dispatch/snapshot.py's DEFAULT_STALENESS_MINUTES (15m, from
# governance/policy/lease.SNAPSHOT_STALENESS_MINUTES). That one bounds a
# real-time dispatch CLAIM against the board; `.board/snapshot.json` is
# refreshed explicitly and by hand, riding whatever other PR happens to touch
# it (`git log -- .board/snapshot.json` shows real gaps over 48h and no
# committed cadence). The `ao-fleet-snapshot-refresh` cron liveness rung
# (fleet/cron.py, issue #1179) that WOULD auto-refresh it on a schedule is
# declared but deliberately OFF by default
# (infra/fleet/tests/test_healthz.py asserts it out of ENABLED_MARKERS), so
# nothing today refreshes this file automatically. Gating the tag-conformance
# projection at the dispatch path's 15m bar would make this check permanently
# red rather than catching real drift; issue #1427 names its own threshold —
# one week — instead of borrowing one built for a different cadence. Past
# this many minutes with no refresh, `check-tagging.sh` goes red until
# someone runs the one-line remedy `board --live` already prints:
# `python3 governance/dispatch/cli.py snapshot --from-github` (then commit
# the refreshed `.board/snapshot.json`).
TAGGING_BOARD_STALENESS_MINUTES = 7 * 24 * 60  # 1 week

TAXONOMY_REL = "governance/tagging/taxonomy.yaml"
RULES_REL = "governance/tagging/rules.yaml"
CONTROLS_REL = "governance/tagging/controls.yaml"
MATRIX_DOC_REL = "docs/TAGGING.md"

OK = 0
NOT_OK = 1
CANNOT_ASSESS = 2


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _load(root: Path) -> Tuple[M.Taxonomy, M.Rules]:
    taxonomy = M.load_taxonomy(root / TAXONOMY_REL)
    rules = M.load_rules(root / RULES_REL)
    return taxonomy, rules


def _authority_findings(root: Path, taxonomy: M.Taxonomy, rules: M.Rules) -> List[M.Finding]:
    """Every judgement about the authority itself, in one place."""
    findings: List[M.Finding] = []
    findings.extend(M.lint_taxonomy(taxonomy))
    findings.extend(M.drift(taxonomy, root))
    findings.extend(
        M.filing_drift(taxonomy, root / "governance/conformance/policy.yaml")
    )
    findings.extend(M.lint_rules(rules, taxonomy))
    findings.extend(M.lint_gates(rules, root))
    _, rank_findings = M.finops_rank_map(root / RULES_REL)
    findings.extend(rank_findings)

    # The frozen shapes. Each document is validated as `{shape: document}`
    # against the ROOT schema (see governance/tagging/schema.py) before anything
    # derives from it, so a shape violation is named here rather than surfacing
    # as a confusing KeyError three functions later.
    for shape, rel in (
        ("taxonomy", TAXONOMY_REL),
        ("rules", RULES_REL),
        ("controls", CONTROLS_REL),
    ):
        try:
            document = M._load_doc(root / rel)
        except M.TaggingUnavailable as exc:
            findings.append(
                M.Finding(
                    M.CODE_TAXONOMY_INVALID,
                    "%s is unreadable: %s" % (rel, exc),
                    subject=rel,
                )
            )
            continue
        for violation in S.problems(document, shape):
            findings.append(
                M.Finding(
                    M.CODE_TAXONOMY_INVALID,
                    "%s violates the frozen %s shape: %s" % (rel, shape, violation),
                    subject=rel,
                    remediation="fix the document, or amend tagging.schema.json",
                )
            )

    # The declared controls, held against the authority they govern.
    try:
        controls = P.load(root / CONTROLS_REL)
    except P.ControlsUnavailable as exc:
        findings.append(
            M.Finding(M.CODE_TAXONOMY_INVALID, str(exc), subject=CONTROLS_REL)
        )
    else:
        findings.extend(P.check(taxonomy, rules, controls, rules_path=root / RULES_REL))

    declared = set(taxonomy.refusal_ids)
    raised = set(M.REFUSAL_CODES)
    for missing in sorted(raised - declared):
        findings.append(
            M.Finding(
                M.CODE_TAXONOMY_INVALID,
                "finding code %r is raised but not declared in taxonomy.yaml refusals"
                % missing,
                subject="refusals",
                remediation="declare the refusal, or stop raising it",
            )
        )
    for extra in sorted(declared - raised):
        findings.append(
            M.Finding(
                M.CODE_TAXONOMY_INVALID,
                "refusal %r is declared but never raised — a formality (GR-12)"
                % extra,
                subject="refusals",
                remediation="provoke it, or delete the declaration",
            )
        )
    return findings


def _emit(findings: Sequence[M.Finding], stream: Any = sys.stdout) -> None:
    for finding in findings:
        print(
            "  %-6s %-24s %s%s"
            % (
                finding.severity.upper(),
                finding.code,
                finding.message,
                (" [%s]" % finding.subject) if finding.subject else "",
            ),
            file=stream,
        )
        if finding.remediation:
            print("         → %s" % finding.remediation, file=stream)


def _verdict(name: str, findings: Sequence[M.Finding], stream: Any = sys.stdout) -> int:
    errs = M.errors(findings)
    warns = M.warnings(findings)
    if errs:
        print(
            "%s: FAIL (%d error(s), %d deviation(s))" % (name, len(errs), len(warns)),
            file=stream,
        )
        return NOT_OK
    if warns:
        print("%s: PASS with %d deviation(s)" % (name, len(warns)), file=stream)
        return OK
    print("%s: PASS" % name, file=stream)
    return OK


# ---------------------------------------------------------------------------
# verbs
# ---------------------------------------------------------------------------
def cmd_lint(args: argparse.Namespace) -> int:
    root = repo_root()
    try:
        taxonomy, rules = _load(root)
    except M.TaggingUnavailable as exc:
        print("tagging: CANNOT-ASSESS — %s" % exc, file=sys.stderr)
        return CANNOT_ASSESS
    findings = _authority_findings(root, taxonomy, rules)
    print(
        "tagging-lint: %d dimension(s), %d rule(s), %d borrowed authority(ies)"
        % (
            len(taxonomy.dimensions),
            len(rules.rules),
            sum(1 for d in taxonomy.dimensions.values() if d.borrowed_from),
        )
    )
    _emit(findings)
    return _verdict("tagging-lint", findings)


def cmd_check(args: argparse.Namespace) -> int:
    root = repo_root()
    try:
        taxonomy, rules = _load(root)
    except M.TaggingUnavailable as exc:
        print("tagging-check: CANNOT-ASSESS — %s" % exc, file=sys.stderr)
        return CANNOT_ASSESS
    findings = list(_authority_findings(root, taxonomy, rules))

    doc = root / MATRIX_DOC_REL
    if not doc.is_file():
        findings.append(
            M.Finding(
                M.CODE_MATRIX_STALE,
                "%s is missing — the generated matrix is not committed" % MATRIX_DOC_REL,
                subject=MATRIX_DOC_REL,
                remediation="run: python3 governance/tagging/cli.py matrix --write",
            )
        )
    else:
        committed = M.extract_matrix(doc.read_text(encoding="utf-8"))
        generated = M.render_matrix(taxonomy, rules)
        if committed is None:
            findings.append(
                M.Finding(
                    M.CODE_MATRIX_STALE,
                    "%s carries no generated matrix markers" % MATRIX_DOC_REL,
                    subject=MATRIX_DOC_REL,
                    remediation="run: python3 governance/tagging/cli.py matrix --write",
                )
            )
        elif committed.strip() != generated.strip():
            findings.append(
                M.Finding(
                    M.CODE_MATRIX_STALE,
                    "the committed matrix in %s is not what the authority generates"
                    % MATRIX_DOC_REL,
                    subject=MATRIX_DOC_REL,
                    remediation="run: python3 governance/tagging/cli.py matrix --write",
                )
            )
    _emit(findings)
    return _verdict("tagging-check", findings)


def cmd_plan(args: argparse.Namespace) -> int:
    root = repo_root()
    try:
        taxonomy, rules = _load(root)
    except M.TaggingUnavailable as exc:
        print("tagging-plan: CANNOT-ASSESS — %s" % exc, file=sys.stderr)
        return CANNOT_ASSESS
    tag_set = M.parse_labels(args.tag or [])
    if not tag_set.labels:
        print("tagging-plan: CANNOT-ASSESS — no --tag given", file=sys.stderr)
        return CANNOT_ASSESS
    plan, findings = M.derive(
        tag_set, taxonomy, rules, target=args.target, strict=args.strict
    )
    if args.json:
        print(json.dumps(plan.as_dict(), indent=2, sort_keys=True))
    else:
        print("plan: %s" % plan.summary)
        print("  tags:   %s" % ", ".join(plan.tags.labels))
        print("  fired:  %s" % (", ".join(plan.rules_fired) or "(baseline only)"))
        for channel in M.CHANNELS:
            gates = plan.gates.get(channel)
            if gates:
                print("  %-3s:    %s" % (channel, " ".join(gates)))
        if plan.finops_floor:
            print("  floor:  %s" % plan.finops_floor)
        if plan.declarations:
            print("  needs:  %s" % ", ".join(plan.declarations))
        if plan.forbids:
            print("  forbids: %s" % ", ".join(plan.forbids))
    errs = M.errors(findings)
    print("  findings: %d error(s), %d deviation(s)" % (len(errs), len(M.warnings(findings))))
    _emit(findings)
    rc = NOT_OK if errs else OK
    if not args.no_ledger:
        L.record(
            "plan",
            ",".join(tag_set.labels) or "(untagged)",
            "ok" if rc == OK else "refused",
            root=root,
            exit_code=rc,
            tags=list(tag_set.labels),
            gates=sum(len(v) for v in plan.gates.values()),
            finops_floor=plan.finops_floor,
            rules_fired=list(plan.rules_fired),
            finding_codes=[f.code for f in errs],
        )
    return rc


def cmd_matrix(args: argparse.Namespace) -> int:
    root = repo_root()
    try:
        taxonomy, rules = _load(root)
    except M.TaggingUnavailable as exc:
        print("tagging-matrix: CANNOT-ASSESS — %s" % exc, file=sys.stderr)
        return CANNOT_ASSESS
    generated = M.render_matrix(taxonomy, rules)
    if args.write:
        doc = root / MATRIX_DOC_REL
        if not doc.is_file():
            print(
                "tagging-matrix: CANNOT-ASSESS — %s does not exist to update"
                % MATRIX_DOC_REL,
                file=sys.stderr,
            )
            return CANNOT_ASSESS
        text = doc.read_text(encoding="utf-8")
        committed = M.extract_matrix(text)
        if committed is None:
            print(
                "tagging-matrix: CANNOT-ASSESS — %s carries no matrix markers"
                % MATRIX_DOC_REL,
                file=sys.stderr,
            )
            return CANNOT_ASSESS
        doc.write_text(text.replace(committed, generated), encoding="utf-8")
        print("tagging-matrix: wrote %d row(s) into %s" % (len(rules.rules), MATRIX_DOC_REL))
        return OK
    print(generated)
    return OK


def cmd_schema(args: argparse.Namespace) -> int:
    """Print the frozen shapes of the authority's own artifacts."""
    if args.shapes:
        for name in S.shapes():
            print(name)
        return OK
    print(S.as_json())
    return OK


def cmd_board(args: argparse.Namespace) -> int:
    root = repo_root()
    snapshot = Path(args.snapshot) if args.snapshot else root / ".board/snapshot.json"
    if not snapshot.is_file():
        print(
            "tagging-board: CANNOT-ASSESS — no board snapshot at %s "
            "(run: python3 governance/dispatch/cli.py snapshot --from-github)" % snapshot,
            file=sys.stderr,
        )
        return CANNOT_ASSESS
    try:
        taxonomy, rules = _load(root)
    except M.TaggingUnavailable as exc:
        print("tagging-board: CANNOT-ASSESS — %s" % exc, file=sys.stderr)
        return CANNOT_ASSESS
    try:
        raw = json.loads(snapshot.read_text(encoding="utf-8"))
    except ValueError as exc:
        print("tagging-board: CANNOT-ASSESS — %s is not valid JSON: %s" % (snapshot, exc), file=sys.stderr)
        return CANNOT_ASSESS

    if args.live:
        try:
            projection = LV.project(
                root,
                taxonomy,
                snapshot_path=snapshot,
                strict=args.strict,
            )
        except LV.LiveUnavailable as exc:
            print("tagging-live: CANNOT-ASSESS — %s" % exc, file=sys.stderr)
            return CANNOT_ASSESS
        if args.json:
            print(json.dumps(projection, indent=2, sort_keys=True))
        else:
            LV.render(projection)
        age = projection.get("snapshot_age_minutes")
        # `args.max_stale_minutes > 0` — NOT bare truthiness: a threshold of
        # exactly 0.0 is falsy in Python, so the old `if args.max_stale_minutes
        # and ...` silently disabled the check whenever the (also 0.0) default
        # was in effect. Every default caller was drifting undetected; only a
        # caller who explicitly passed a positive value ever got refused.
        if (
            args.max_stale_minutes > 0
            and age is not None
            and age > args.max_stale_minutes
        ):
            print(
                "tagging-live: FAIL — the snapshot is %.1fm old (limit %.0fm); the "
                "projection describes a board that has moved"
                % (age, args.max_stale_minutes)
            )
            return NOT_OK
        if args.max_stale_minutes > 0 and age is None:
            print(
                "tagging-live: FAIL — the snapshot at %s has no readable "
                "generated_at, so its age cannot be bounded by "
                "--max-stale-minutes %.0f" % (projection.get("snapshot"), args.max_stale_minutes)
            )
            return NOT_OK
        return OK if not args.fail_on_refusal or projection.get("refusal_count", 0) == 0 else NOT_OK

    issues = raw if isinstance(raw, list) else raw.get("issues") or []
    findings: List[M.Finding] = []
    scanned = 0
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        if str(issue.get("state", "open")).lower() != "open":
            continue
        labels = []
        for label in issue.get("labels") or ():
            labels.append(label.get("name", "") if isinstance(label, dict) else str(label))
        tag_set = M.parse_labels(labels)
        if not tag_set.values:
            continue
        scanned += 1
        subject = "issue-%s" % issue.get("number", "?")
        for finding in M.validate_tags(tag_set, taxonomy, target="issue", strict=args.strict):
            findings.append(
                M.Finding(
                    finding.code,
                    finding.message,
                    severity=finding.severity,
                    subject=subject,
                    remediation=finding.remediation,
                )
            )
    print("tagging-board: scanned %d open issue(s) with tags" % scanned)
    _emit(findings)
    rc = _verdict("tagging-board", findings)
    if not args.no_ledger:
        L.record(
            "board",
            str(snapshot),
            "ok" if rc == OK else "refused",
            root=root,
            exit_code=rc,
            finding_codes=[f.code for f in M.errors(findings)],
        )
    return rc


GH_BIN_ENV = "AO_GH_BIN"
LABEL_FAMILY_PREFIXES = ("class:", "posture:", "lifecycle:", "pillar:")


def _gh_bin() -> str:
    """The `gh` binary, injected through a variable (SP-4): tests shadow it
    with a fake script rather than a function named after the real binary."""
    return os.environ.get(GH_BIN_ENV, "gh")


def _run_gh(args_list: Sequence[str], root: Path) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(
        [_gh_bin(), *args_list], cwd=str(root), capture_output=True, text=True
    )


def parse_classification(body: str) -> Dict[str, str]:
    """The `## Classification` block's `key: value` lines (issue #1328).

    Deliberately the same shape `scripts/check-pr-contract.sh`'s classification
    check parses — comments stripped, one field per `key: value` line, an
    inline `# comment` after the value trimmed. Kept in step with the shell
    parser by the shared fixtures in `governance/tagging/tests/test_pr_labels.py`.
    """
    text = re.sub(r"<!--.*?-->", "", body or "", flags=re.S)
    match = re.search(
        r"(?im)^##+[ \t]*Classification[ \t]*$(.*?)(?=^##+[ \t]|\Z)",
        text,
        flags=re.S | re.M,
    )
    fields: Dict[str, str] = {}
    if not match or not match.group(1).strip():
        return fields
    for raw_line in match.group(1).splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        kv = re.match(r"^([A-Za-z][A-Za-z0-9_-]*):\s*(.*)$", line)
        if not kv:
            continue
        key = kv.group(1).lower()
        value = re.split(r"\s+#", kv.group(2).strip(), maxsplit=1)[0].strip()
        fields[key] = value
    return fields


def _filled(value: str) -> bool:
    return bool(value) and "<" not in value


def derive_pr_labels(
    fields: Mapping[str, str], taxonomy: M.Taxonomy
) -> Tuple[List[str], List[Tuple[str, str]]]:
    """The class:/posture:/lifecycle:/pillar: labels a Classification block
    implies, plus the findings for whichever fields do not resolve.

    `class`'s vocabulary is `taxonomy.dimensions["class"].values` — the SAME
    mirrored-and-drift-checked set `governance/conformance/policy.yaml`'s
    ladder is proven to equal (`M.drift`), so this never re-declares the
    ladder; it borrows the borrow.
    """
    labels: List[str] = []
    findings: List[Tuple[str, str]] = []

    cls = fields.get("class", "")
    class_vocab = list(taxonomy.dimensions["class"].values)
    if _filled(cls) and cls in class_vocab:
        labels.append("class:%s" % cls)
    else:
        findings.append(("pr-class-unknown", cls or "(missing)"))

    posture_raw = fields.get("posture", "")
    posture_vocab = list(taxonomy.dimensions["posture"].values)
    if not _filled(posture_raw):
        findings.append(("pr-posture-unknown", "(missing)"))
    else:
        postures = [p.strip() for p in posture_raw.split(",") if p.strip()]
        bad = [p for p in postures if p not in posture_vocab]
        if bad or not postures:
            findings.append(("pr-posture-unknown", ",".join(bad) or posture_raw))
        for p in postures:
            if p in posture_vocab:
                labels.append("posture:%s" % p)

    lifecycle = fields.get("lifecycle", "")
    lifecycle_vocab = list(taxonomy.dimensions["lifecycle"].values)
    if _filled(lifecycle) and lifecycle in lifecycle_vocab:
        labels.append("lifecycle:%s" % lifecycle)
    else:
        findings.append(("pr-lifecycle-unknown", lifecycle or "(missing)"))

    pillar = fields.get("pillar", "")
    pillar_vocab = list(taxonomy.dimensions["pillar"].values)
    if _filled(pillar) and pillar in pillar_vocab:
        labels.append("pillar:%s" % pillar)
    else:
        findings.append(("pr-pillar-unknown", pillar or "(missing)"))

    return labels, findings


def cmd_pr_labels(args: argparse.Namespace) -> int:
    root = repo_root()
    try:
        taxonomy, _ = _load(root)
    except M.TaggingUnavailable as exc:
        print("tagging-pr-labels: CANNOT-ASSESS — %s" % exc, file=sys.stderr)
        return CANNOT_ASSESS

    subject = "PR #%s" % args.pr if args.pr else (args.body_file or "(body)")

    if args.body_file:
        try:
            body = Path(args.body_file).read_text(encoding="utf-8")
        except OSError as exc:
            print(
                "tagging-pr-labels: CANNOT-ASSESS — cannot read %s: %s"
                % (args.body_file, exc),
                file=sys.stderr,
            )
            return CANNOT_ASSESS
    else:
        if not args.pr:
            print(
                "tagging-pr-labels: CANNOT-ASSESS — pr-context-missing: no --pr "
                "and no --body-file",
                file=sys.stderr,
            )
            return CANNOT_ASSESS
        proc = _run_gh(["pr", "view", str(args.pr), "--json", "body", "--jq", ".body"], root)
        if proc.returncode != 0:
            print(
                "tagging-pr-labels: CANNOT-ASSESS — cannot read %s's body via gh: %s"
                % (subject, proc.stderr.strip()),
                file=sys.stderr,
            )
            return CANNOT_ASSESS
        body = proc.stdout

    fields = parse_classification(body)
    if not fields:
        print(
            "tagging-pr-labels: CANNOT-ASSESS — pr-classification-missing: %s "
            "carries no ## Classification block" % subject,
            file=sys.stderr,
        )
        return CANNOT_ASSESS

    labels, findings = derive_pr_labels(fields, taxonomy)
    for label in sorted(labels):
        print(label)
    for code, detail in findings:
        print("  %s: %s" % (code, detail), file=sys.stderr)
    rc = NOT_OK if findings else OK

    if args.apply:
        if not args.pr:
            print(
                "tagging-pr-labels: CANNOT-ASSESS — --apply requires --pr",
                file=sys.stderr,
            )
            return CANNOT_ASSESS
        proc = _run_gh(
            ["pr", "view", str(args.pr), "--json", "labels", "--jq",
             "[.labels[].name] | join(\",\")"],
            root,
        )
        if proc.returncode != 0:
            print(
                "tagging-pr-labels: CANNOT-ASSESS — cannot read %s's labels via "
                "gh: %s" % (subject, proc.stderr.strip()),
                file=sys.stderr,
            )
            return CANNOT_ASSESS
        existing = [l for l in (proc.stdout or "").strip().split(",") if l]
        existing_family = [l for l in existing if l.startswith(LABEL_FAMILY_PREFIXES)]
        derived_set = set(labels)
        drift = sorted(set(existing_family) - derived_set)
        for d in drift:
            print("  pr-label-drift: %s" % d, file=sys.stderr)
        to_add = sorted(derived_set - set(existing))
        for label in to_add:
            r = _run_gh(["pr", "edit", str(args.pr), "--add-label", label], root)
            if r.returncode != 0:
                print(
                    "tagging-pr-labels: CANNOT-ASSESS — gh could not add label "
                    "%s: %s" % (label, r.stderr.strip()),
                    file=sys.stderr,
                )
                return CANNOT_ASSESS
        for label in drift:
            r = _run_gh(["pr", "edit", str(args.pr), "--remove-label", label], root)
            if r.returncode != 0:
                print(
                    "tagging-pr-labels: CANNOT-ASSESS — gh could not remove "
                    "label %s: %s" % (label, r.stderr.strip()),
                    file=sys.stderr,
                )
                return CANNOT_ASSESS
        print(
            "tagging-pr-labels: applied %d label(s), removed %d drifted label(s)"
            % (len(to_add), len(drift))
        )
        rc = NOT_OK if findings or drift else OK

    return rc


def cmd_labels(args: argparse.Namespace) -> int:
    root = repo_root()
    try:
        taxonomy, _ = _load(root)
    except M.TaggingUnavailable as exc:
        print("tagging-labels: CANNOT-ASSESS — %s" % exc, file=sys.stderr)
        return CANNOT_ASSESS
    wanted = args.dimension or [
        name for name, dim in taxonomy.dimensions.items() if dim.kind != M.KIND_PATTERN
    ]
    repo = args.repo
    for name in wanted:
        dim = taxonomy.dimensions.get(name)
        if dim is None:
            print("tagging-labels: CANNOT-ASSESS — undeclared dimension %r" % name, file=sys.stderr)
            return CANNOT_ASSESS
        if dim.kind == M.KIND_PATTERN:
            print("# %s is slug-shaped (pattern %s) — no fixed labels to mint" % (name, dim.pattern))
            continue
        for value in dim.values:
            print(
                "gh label create '%s:%s' --repo %s --color 0e8a16 "
                "--description 'tagging: %s (%s)' --force"
                % (name, value, repo, name, dim.kind)
            )
    return OK


# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tagging",
        description="The tag authority's verbs (issue #1175).",
    )
    sub = parser.add_subparsers(dest="verb")

    sub.add_parser("lint", help="judge the authority itself")

    sub.add_parser("check", help="lint + prove the committed matrix is current")

    board = sub.add_parser("board", help="judge the board's tags against the taxonomy")
    board.add_argument("--snapshot", default="")
    board.add_argument("--strict", action="store_true")
    board.add_argument("--live", action="store_true", help="project the board's tag state")
    board.add_argument("--json", action="store_true")
    # Default comes from the single upstream staleness control (issue #322 /
    # #1427), the same one governance/dispatch/snapshot.py derives
    # DEFAULT_STALENESS_MINUTES from — a drifting board is caught by DEFAULT,
    # not only when a caller remembers to pass a threshold. 0 (or negative)
    # explicitly opts out of the check, matching the sentinel every caller of
    # this flag already read from --help.
    board.add_argument(
        "--max-stale-minutes", type=float, default=float(TAGGING_BOARD_STALENESS_MINUTES)
    )
    board.add_argument("--fail-on-refusal", action="store_true")
    board.add_argument("--no-ledger", action="store_true")

    plan = sub.add_parser("plan", help="derive the gates a tag set requires")
    plan.add_argument("--tag", action="append", default=[], metavar="name:value")
    plan.add_argument("--target", default="issue")
    plan.add_argument("--strict", action="store_true")
    plan.add_argument("--json", action="store_true")
    plan.add_argument("--no-ledger", action="store_true")

    matrix = sub.add_parser("matrix", help="render the tag -> gate matrix")
    matrix.add_argument("--write", action="store_true", help="update docs/TAGGING.md in place")

    labels = sub.add_parser("labels", help="emit the gh label create commands")
    labels.add_argument("--repo", default="kushin77/agent-orchestrator")
    labels.add_argument("--dimension", action="append", default=[])

    pr_labels = sub.add_parser(
        "pr-labels",
        help="derive/apply the labels a PR's Classification block implies",
    )
    pr_labels.add_argument("--pr", type=int, default=0)
    pr_labels.add_argument("--body-file", default="")
    pr_labels.add_argument("--apply", action="store_true")

    schema = sub.add_parser("schema", help="print the frozen shapes of the authority")
    schema.add_argument("--shapes", action="store_true", help="list the shape names")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    handlers = {
        "lint": cmd_lint,
        "check": cmd_check,
        "board": cmd_board,
        "plan": cmd_plan,
        "matrix": cmd_matrix,
        "labels": cmd_labels,
        "pr-labels": cmd_pr_labels,
        "schema": cmd_schema,
    }
    handler = handlers.get(args.verb or "")
    if handler is None:
        parser.print_help()
        return CANNOT_ASSESS
    return handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
