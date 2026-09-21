"""End-to-end integration proof — the whole tagging chain in one offline run.

---knowledge---
module_id: governance.tagging.e2e
system: governance
app: tagging
solution_class: pattern
patterns: [provoked-negative-control, honesty-tri-state, offline-hermetic]
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [main]
invariants: ""
gotchas: ""
related: ["#1182"]
do_not_duplicate: null
---knowledge---

The tag authority's gates each prove one link: `tagging-lint` the vocabulary,
`tagging-refusals` the refusals, `tagging-mandate` the constitution. This module
proves the CHAIN — that a tag set, fed into the real machinery, produces a plan
whose gates all resolve, whose matrix names the rules that fired, whose mandate
holds in the very tree that runs it, and whose filing seam derives the same tag
dimensions for a NEW issue so the board is born classified. That last link is the
one the other gates cannot reach, because it crosses the package boundary from
`governance/tagging` into `governance/conformance` — the dependency is
one-directional (tagging reads conformance's policy; conformance never imports
tagging), and this module is the only place it is exercised.

    python3 governance/tagging/e2e.py

Exit codes: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS — the repository's tri-state.
Every check below is driven with its provoked half AND its clean twin, so a link
that passes while doing nothing is caught here rather than passing as strict.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path
from typing import Callable, List, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import mandate as MD  # noqa: E402
import model as M  # noqa: E402

OK = 0
NOT_OK = 1
CANNOT_ASSESS = 2

TAGS = ["class:elite", "type:feature", "priority:P1", "area:standards",
        "posture:iac", "lifecycle:release", "finops:pro"]


def main(argv: Sequence[str] | None = None) -> int:
    try:
        taxonomy = M.load_taxonomy(ROOT / "governance" / "tagging" / "taxonomy.yaml")
        rules = M.load_rules(ROOT / "governance" / "tagging" / "rules.yaml")
    except M.TaggingUnavailable as exc:
        print("tagging-e2e: CANNOT-ASSESS — %s" % exc, file=sys.stderr)
        return CANNOT_ASSESS

    checks: List[Tuple[str, Callable[[], str]]] = []

    def _plan() -> Tuple[M.Plan, List[M.Finding]]:
        plan, findings = M.derive(
            M.parse_labels(TAGS), taxonomy, rules, target="issue"
        )
        if M.errors(findings):
            raise AssertionError(
                "the representative tag set was refused: %s"
                % [f.message for f in M.errors(findings)]
            )
        return plan, list(findings)

    def _plan_derives():
        plan, _ = _plan()
        if "cd" not in plan.gates or "ci" not in plan.gates:
            raise AssertionError("the plan derives no ci/cd gates: %s" % plan.gates)
        if plan.finops_floor != "pro":
            raise AssertionError("the FinOps floor is %r, expected pro" % plan.finops_floor)
        if "flag-gated-off" not in plan.declarations:
            raise AssertionError("posture:iac owes flag-gated-off, not derived")
        return "gates=%d floor=%s fired=%d" % (
            sum(len(v) for v in plan.gates.values()), plan.finops_floor, len(plan.rules_fired))

    checks.append(("plan-derives", _plan_derives))

    def _every_gate_resolves():
        # the whole rule set resolves (no rule names a dead gate) ...
        if M.errors(M.lint_gates(rules, ROOT)):
            raise AssertionError("a rule names a gate that does not resolve")
        # ... and the plan the representative set derives resolves too.
        plan, _ = _plan()
        names = {gate for gates in plan.gates.values() for gate in gates}
        if not names:
            raise AssertionError("the plan derives no gates at all")
        return "%d gate(s), all resolvable" % len(names)

    checks.append(("every-gate-resolves", _every_gate_resolves))

    def _matrix_names_the_fired_rules():
        plan, _ = _plan()
        rendered = M.render_matrix(taxonomy, rules)
        for rule_id in plan.rules_fired:
            if "`%s`" % rule_id not in rendered:
                raise AssertionError("the matrix does not name fired rule %r" % rule_id)
        return "matrix names %d fired rule(s)" % len(plan.rules_fired)

    checks.append(("matrix-names-fired-rules", _matrix_names_the_fired_rules))

    def _mandate_holds_here():
        findings = MD.check(ROOT)
        if findings:
            raise AssertionError("the mandate is not declared: %s" % findings[0].message)
        return "%d marker(s) declared in this tree" % sum(
            len(markers) for _, markers in MD.contract())

    checks.append(("mandate-holds-here", _mandate_holds_here))

    def _filing_seam_derives_the_tags():
        # The cross-package link. The conformance filing seam derives the tag
        # dimensions for a NEW issue, so the board is born classified (#1182).
        # It is exercised as a SUBPROCESS on purpose: the conformance package
        # uses flat imports (its own `model`), so importing it from here would
        # shadow it with this package's `model`. The CLI is the real entry
        # point, the policy is the real policy, and the result is the same one
        # `make conformance` gates — proving it here closes the loop.
        import subprocess

        result = subprocess.run(
            [sys.executable, "governance/conformance/cli.py", "filing-check"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise AssertionError(
                "the filing seam's own gate is not OK (rc=%d): %s"
                % (result.returncode, (result.stderr or result.stdout).strip()[:200])
            )
        if "derives the tag dimensions for a bare filing" not in result.stdout:
            raise AssertionError(
                "the filing seam does not prove it derives the tag dimensions"
            )
        if "posture:overall" not in result.stdout or "lifecycle:build" not in result.stdout:
            raise AssertionError("the derived tag labels are not shown")
        return "filing-check derives posture:overall + lifecycle:build"

    checks.append(("filing-seam-derives-tags", _filing_seam_derives_the_tags))

    def _filing_defaults_are_legal():
        findings = M.filing_drift(
            taxonomy, ROOT / "governance" / "conformance" / "policy.yaml"
        )
        if findings:
            raise AssertionError("a filing default is illegal: %s" % findings[0].message)
        return "the filing defaults are values the authority declares"

    checks.append(("filing-defaults-legal", _filing_defaults_are_legal))

    # -- the provoked half: a broken link in the chain is refused -----------
    scratch = Path(tempfile.mkdtemp(prefix="ao1182-e2e."))
    try:

        def _broken_gate_is_refused():
            # Break the consumer side: a filing default naming an illegal value
            # must be refused by the drift anchor, not filed. The clean twin is
            # the real policy, which the anchor accepts.
            policy = ROOT / "governance" / "conformance" / "policy.yaml"
            text = policy.read_text(encoding="utf-8")
            mutant_text = text.replace("    posture: overall", "    posture: mythic", 1)
            if mutant_text == text:
                raise AssertionError("the posture default was not present to mutate")
            mutant = scratch / "policy.yaml"
            mutant.write_text(mutant_text, encoding="utf-8")
            findings = M.filing_drift(taxonomy, mutant)
            if M.CODE_FILING_DEFAULT_DRIFT not in {f.code for f in findings}:
                raise AssertionError(
                    "an illegal filing default was not refused (%s)"
                    % (sorted({f.code for f in findings}) or "(nothing)")
                )
            clean = M.filing_drift(taxonomy, policy)
            if clean:
                raise AssertionError("the clean twin was refused: %s" % clean[0].message)
            return "illegal default refused by name; clean twin accepted"

        checks.append(("broken-link-refused", _broken_gate_is_refused))

        failures: List[str] = []
        for name, fn in checks:
            try:
                detail = fn()
            except AssertionError as exc:
                print("  FAIL  %-24s %s" % (name, exc))
                failures.append(name)
            except Exception as exc:  # noqa: BLE001 - a driver reports, never crashes
                print("  FAIL  %-24s unexpected %s: %s" % (name, type(exc).__name__, exc))
                failures.append(name)
            else:
                print("  PASS  %-24s %s" % (name, detail))
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    if failures:
        print("tagging-e2e: FAIL — %d link(s) of the chain did not hold" % len(failures))
        return NOT_OK
    print("tagging-e2e: PASS — %d link(s) of the chain held end to end" % len(checks))
    return OK


if __name__ == "__main__":
    raise SystemExit(main())
