"""Provocation driver — the tag authority's negative control (issue #1175).

A refusal declared in `taxonomy.yaml` that no one can provoke is a formality
(GR-12), and this driver is what makes the declaration falsifiable. For every
refusal the taxonomy declares it:

1. plants a REAL mutant in a scratch tree (never in the repository, so a
   provocation can never be mistaken for a corpus fixture);
2. asserts the mutant actually differs from the original (sha256), because a
   mutation that never landed must not be reported as a passing control;
3. runs the checker and requires the refusal **by code AND by the named token**
   the taxonomy promises it names in its finding;
4. runs the CLEAN twin of that input and requires it to be refused nothing —
   a rule that fires on everything is not a rule.

It also asserts the two halves agree: the set of refusal ids in `taxonomy.yaml`
equals the set of codes the model can raise, so a refusal added without a
provocation (or a code raised without a declaration) fails here by name.

    python3 governance/tagging/provoke.py            # run every provocation
    python3 governance/tagging/provoke.py --list     # list them

Exit codes: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS — the repository's tri-state.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

import mandate as MD  # noqa: E402
import model as M  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
TAXONOMY = ROOT / "governance" / "tagging" / "taxonomy.yaml"
RULES = ROOT / "governance" / "tagging" / "rules.yaml"

OK = 0
NOT_OK = 1
CANNOT_ASSESS = 2

CLEAN_ISSUE_TAGS = [
    "class:enterprise",
    "type:feature",
    "priority:P1",
    "area:standards",
]


@dataclass(frozen=True)
class Provocation:
    """One declared refusal and the real mutant that must provoke it.

    ``expect_token`` is the value the taxonomy PROMISES the finding names: the
    assertion is on the code *and* on the token, so a refusal that fires with a
    different (or empty) explanation does not count as provoked.
    """

    refusal: str
    label: str
    expect_token: str
    run: Callable


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _copy_without(source: Path, target: Path, old: str, new: str) -> Path:
    """Copy ``source`` to ``target`` with one substitution; assert it landed."""
    text = source.read_text(encoding="utf-8")
    mutant = text.replace(old, new, 1)
    if mutant == text:
        raise AssertionError(
            "the mutation did not land: %r is absent from %s" % (old, source.name)
        )
    target.write_text(mutant, encoding="utf-8")
    if _digest(target) == _digest(source):
        raise AssertionError("the mutated file is byte-identical to the original")
    return target


# ---------------------------------------------------------------------------
# the provocations
# ---------------------------------------------------------------------------
def _scratch(scratch: Path, name: str) -> Path:
    path = scratch / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def provoke_unknown_dimension(scratch: Path, taxonomy: M.Taxonomy, rules: M.Rules):
    tags = M.parse_labels(CLEAN_ISSUE_TAGS + ["vibe:elite"])
    clean = M.parse_labels(CLEAN_ISSUE_TAGS)
    return M.validate_tags(tags, taxonomy), M.validate_tags(clean, taxonomy)


def provoke_unknown_value(scratch: Path, taxonomy: M.Taxonomy, rules: M.Rules):
    tags = M.parse_labels(CLEAN_ISSUE_TAGS + ["posture:magic"])
    clean = M.parse_labels(CLEAN_ISSUE_TAGS + ["posture:iac"])
    return M.validate_tags(tags, taxonomy), M.validate_tags(clean, taxonomy)


def provoke_value_pattern(scratch: Path, taxonomy: M.Taxonomy, rules: M.Rules):
    off = [t for t in CLEAN_ISSUE_TAGS if not t.startswith("area:")]
    tags = M.parse_labels(off + ["area:Bad_Area"])
    clean = M.parse_labels(off + ["area:erp-module"])
    return M.validate_tags(tags, taxonomy), M.validate_tags(clean, taxonomy)


def provoke_vocabulary_drift(scratch: Path, taxonomy: M.Taxonomy, rules: M.Rules):
    work = _scratch(scratch, "drift")
    mutant = _copy_without(
        TAXONOMY,
        work / "taxonomy.yaml",
        "values: [template, class, pattern, enterprise, faang, elite]",
        "values: [template, class, pattern, enterprise, faang, elite, mythic]",
    )
    findings = M.drift(M.load_taxonomy(mutant), ROOT)
    clean = M.drift(taxonomy, ROOT)
    return findings, clean


def provoke_name_authority_drift(scratch: Path, taxonomy: M.Taxonomy, rules: M.Rules):
    work = _scratch(scratch, "name-authority")
    mutant = _copy_without(
        TAXONOMY,
        work / "taxonomy.yaml",
        "  gdc:\n    kind: closed\n    multi: false\n    applies_to: [issue]\n    values: [enterprise]",
        "  gdc:\n    kind: closed\n    multi: false\n    applies_to: [issue]\n    values: [enterprise]\n    name_authority:\n      path: governance/conformance/policy.yaml\n      pointer: ladder",
    )
    findings = M.drift(M.load_taxonomy(mutant), ROOT)
    clean = M.drift(taxonomy, ROOT)
    return findings, clean


def provoke_posture_contradiction(scratch: Path, taxonomy: M.Taxonomy, rules: M.Rules):
    tags = M.parse_labels(
        CLEAN_ISSUE_TAGS + ["posture:no-human-needed", "posture:human-gated"]
    )
    clean = M.parse_labels(CLEAN_ISSUE_TAGS + ["posture:no-human-needed"])
    return M.validate_tags(tags, taxonomy), M.validate_tags(clean, taxonomy)


def provoke_required_missing(scratch: Path, taxonomy: M.Taxonomy, rules: M.Rules):
    tags = M.parse_labels(["class:elite"])
    return M.validate_tags(tags, taxonomy, target="issue"), M.validate_tags(
        M.parse_labels(CLEAN_ISSUE_TAGS), taxonomy, target="issue"
    )


def provoke_unknown_target(scratch: Path, taxonomy: M.Taxonomy, rules: M.Rules):
    tags = M.parse_labels(["posture:iac"])
    return M.validate_tags(tags, taxonomy, target="surface"), M.validate_tags(
        M.parse_labels(["class:enterprise"]), taxonomy, target="surface"
    )


def provoke_unknown_gate(scratch: Path, taxonomy: M.Taxonomy, rules: M.Rules):
    work = _scratch(scratch, "unknown-gate")
    mutant = _copy_without(
        RULES, work / "rules.yaml", "make:tf-fmt", "make:tf-fmt-renamed"
    )
    return M.lint_gates(M.load_rules(mutant), ROOT), M.lint_gates(rules, ROOT)


def provoke_rule_unknown_dimension(scratch: Path, taxonomy: M.Taxonomy, rules: M.Rules):
    work = _scratch(scratch, "rule-dimension")
    mutant = _copy_without(RULES, work / "rules.yaml", "dimension: posture", "dimension: mood")
    return (
        M.lint_rules(M.load_rules(mutant), taxonomy),
        M.lint_rules(rules, taxonomy),
    )


def provoke_finops_floor_unmet(scratch: Path, taxonomy: M.Taxonomy, rules: M.Rules):
    tags = M.parse_labels(CLEAN_ISSUE_TAGS + ["posture:iac", "finops:flash"])
    clean = M.parse_labels(CLEAN_ISSUE_TAGS + ["posture:iac", "finops:pro"])
    _, findings = M.derive(tags, taxonomy, rules)
    _, clean_findings = M.derive(clean, taxonomy, rules)
    return findings, clean_findings


def _mandate_root(scratch: Path, name: str, mutate=None) -> Path:
    """A scratch tree holding the contract documents, optionally mutated.

    The mandate reads repository-relative paths, so a scratch root containing
    just those documents is a faithful fixture: it lets the provocation strip one
    marker, or drop one document, WITHOUT touching the repository — a
    provocation that edited the real docs would be a hazard, not a control.
    """
    root = _scratch(scratch, name)
    for doc in MD.DOCS:
        source = ROOT / doc
        target = root / doc
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    if mutate is not None:
        mutate(root)
    return root


def provoke_mandate_missing_marker(scratch: Path, taxonomy: M.Taxonomy, rules: M.Rules):
    """A contract doc that stops declaring a marker is refused, by name.

    The edit is IN PLACE in the scratch copy, so `_copy_without`'s
    copy-then-hash guard does not apply; the assertion here is stronger for this
    case — the marker must be gone from the text after the substitution, which
    the substitution itself could silently fail to achieve.
    """

    def strip(root: Path) -> None:
        doc = root / "docs" / "QA-GATE.md"
        original = doc.read_text(encoding="utf-8")
        mutated = original.replace("lifecycle", "SDLC-stage")
        if mutated == original:
            raise AssertionError("the marker 'lifecycle' was not present to strip")
        if "lifecycle" in mutated:
            raise AssertionError("the marker 'lifecycle' survived the substitution")
        doc.write_text(mutated, encoding="utf-8")

    return MD.check(_mandate_root(scratch, "mandate-marker", strip)), MD.check(
        _mandate_root(scratch, "mandate-marker-clean")
    )


def provoke_mandate_missing_doc(scratch: Path, taxonomy: M.Taxonomy, rules: M.Rules):
    """A contract document that has gone missing is refused, by name."""

    def drop(root: Path) -> None:
        (root / "docs" / "GOVERNANCE.md").unlink()

    return MD.check(_mandate_root(scratch, "mandate-doc", drop)), MD.check(
        _mandate_root(scratch, "mandate-doc-clean")
    )


PROVOCATIONS: Tuple[Tuple[str, str, str, Callable], ...] = (
    ("unknown-dimension", "a tag on an undeclared dimension", "vibe", provoke_unknown_dimension),
    ("unknown-value", "a value the dimension does not declare", "magic", provoke_unknown_value),
    ("value-pattern", "a pattern dimension's value that cannot match", "Bad_Area", provoke_value_pattern),
    ("vocabulary-drift", "a rung minted that the authority does not carry", "mythic", provoke_vocabulary_drift),
    ("name-authority-drift", "a name anchor the authority no longer declares", "gdc", provoke_name_authority_drift),
    ("posture-contradiction", "two mutually exclusive postures at once", "no-human-needed", provoke_posture_contradiction),
    ("required-missing", "a tag set missing a required dimension", "type", provoke_required_missing),
    ("unknown-target", "a tag on a target the dimension does not cover", "surface", provoke_unknown_target),
    ("unknown-gate", "a rule naming a gate that does not exist", "tf-fmt-renamed", provoke_unknown_gate),
    ("rule-unknown-dimension", "a rule keyed on an undeclared dimension", "mood", provoke_rule_unknown_dimension),
    ("finops-floor-unmet", "a tier below the floor its tags require", "flash", provoke_finops_floor_unmet),
    ("mandate-missing-marker", "a contract doc that stopped declaring a marker", "lifecycle", provoke_mandate_missing_marker),
    ("mandate-missing-doc", "a contract document that went missing", "GOVERNANCE.md", provoke_mandate_missing_doc),
)


#: The declared refusals this driver provokes, by id — so a caller (the suite,
#: or a reader) can assert the provocation set without unpacking tuples.
PROVOCATIONS_BY_REFUSAL = frozenset(refusal for refusal, _, _, _ in PROVOCATIONS)


def run(verbose: bool = True) -> int:
    try:
        taxonomy = M.load_taxonomy(TAXONOMY)
        rules = M.load_rules(RULES)
    except M.TaggingUnavailable as exc:
        print("check-tagging: CANNOT-ASSESS — %s" % exc, file=sys.stderr)
        return CANNOT_ASSESS

    failures: List[str] = []
    provoked: set[str] = set()

    # The clean control: the unmutated authority must be refused nothing at all.
    # Without this half, every provocation below could be passing for the wrong
    # reason — a checker that refuses everything refuses the mutants too.
    baseline = (
        list(M.lint_taxonomy(taxonomy))
        + list(M.drift(taxonomy, ROOT))
        + list(M.lint_rules(rules, taxonomy))
        + list(M.lint_gates(rules, ROOT))
        + list(M.validate_tags(M.parse_labels(CLEAN_ISSUE_TAGS + ["posture:iac", "lifecycle:build"]), taxonomy))
    )
    if M.errors(baseline):
        for finding in M.errors(baseline):
            print("  FAIL  clean-control refused: %s [%s]" % (finding.message, finding.code))
        failures.append("clean-control")
    elif verbose:
        print("  PASS  clean-control          the unmutated authority is refused nothing")

    scratch_root = Path(tempfile.mkdtemp(prefix="ao1175-provoke."))
    try:
        for refusal, label, token, fn in PROVOCATIONS:
            try:
                findings, clean = fn(scratch_root, taxonomy, rules)
            except AssertionError as exc:
                print("  FAIL  %-22s mutant never landed: %s" % (refusal, exc))
                failures.append(refusal)
                continue

            errs = M.errors(findings)
            matching = [f for f in errs if f.code == refusal and token in f.message]
            if not matching:
                printed = ", ".join(sorted({f.code for f in errs})) or "(nothing)"
                print(
                    "  FAIL  %-22s not refused by name (%s) — the plan says it names %r, it raised %s"
                    % (refusal, label, token, printed)
                )
                failures.append(refusal)
                continue
            provoked.add(refusal)

            clean_errs = M.errors(clean)
            if clean_errs:
                print(
                    "  FAIL  %-22s the clean twin was refused too (%s) — the rule fires on everything"
                    % (refusal, ", ".join(sorted({f.code for f in clean_errs})))
                )
                failures.append(refusal)
                continue
            print("  PASS  %-22s refused by name, clean twin accepted" % refusal)
    finally:
        shutil.rmtree(scratch_root, ignore_errors=True)

    # The two halves must agree: a refusal declared with no provocation is a
    # formality, and a code raised that is not declared is undocumented behaviour.
    declared = set(taxonomy.refusal_ids)
    for missing in sorted(declared - provoked):
        print("  FAIL  %-22s declared as a refusal but NO provocation raises it" % missing)
        failures.append(missing)
    for extra in sorted(provoked - declared):
        print("  FAIL  %-22s is provoked but not declared in taxonomy.yaml" % extra)
        failures.append(extra)

    if failures:
        print("check-tagging: FAIL — %d provocation(s) did not hold" % len(failures))
        return NOT_OK
    print(
        "check-tagging: PASS — %d refusal(s) provoked by name, clean control accepted"
        % len(provoked)
    )
    return OK


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="provoke", description=__doc__)
    parser.add_argument("--list", action="store_true", help="list the provocations")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    if args.list:
        for refusal, label, token, _ in PROVOCATIONS:
            print("%-24s %-50s expects %r" % (refusal, label, token))
        return OK
    return run(verbose=not args.quiet)


if __name__ == "__main__":
    raise SystemExit(main())
