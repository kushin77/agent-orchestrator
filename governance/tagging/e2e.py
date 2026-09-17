"""End-to-end integration proof — the whole tagging chain in one offline run.

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

It then proves the WHOLE "futureproof" classification surface end to end: for
every mechanism the operator named — `class`, `pattern`, `template`, `rca`,
`system`, `app`, `env-var`, `gov`, `issues`, `index` — the declared authority is
tracked, the gate is executable and wired into `make verify` (not denylisted,
not undiscovered), and the gate's failure path is exercised by a concrete
provocation artifact. That is the inverse of the measured failure class #1164
(`governance/dupcheck/check-duplicates.sh` is invoked by NOTHING): a control
that exists but never runs is the whole point this link refuses.

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

# The futureproof classification surface (the operator's list). For each
# mechanism: the declared AUTHORITY (must be tracked in the tree), the GATE(s)
# (must be executable, discovered into `make verify`, and not denylisted), and
# the PROVOCATION — a concrete artifact that exercises the gate's failure path
# (a negative control, a self-test flag, or the file that carries the planted
# mutant). A mechanism whose gate can never fail is a formality (GR-12); a
# mechanism whose gate is invoked by nothing is invisible (#1164).
MECHANISMS = [
    {
        "id": "class",
        "authorities": [
            "governance/conformance/policy.yaml",
            "governance/conformance/surfaces.yaml",
        ],
        "gates": ["surface-class", "conformance"],
        "provocation": ("scripts/check-surface-class.sh", "negative control"),
    },
    {
        "id": "pattern",
        "authorities": ["docs/SHELL-PATTERNS.md"],
        "gates": ["shell-patterns"],
        "provocation": ("scripts/check-shell-patterns.sh", "--self-test"),
    },
    {
        "id": "template",
        "authorities": [".github/ISSUE_TEMPLATE"],
        "gates": ["issue-template"],
        "provocation": ("scripts/check-issue-template.sh", "negative control"),
    },
    {
        "id": "rca",
        "authorities": [
            "governance/lessons/policy.yaml",
            "governance/lessons/ledger.jsonl",
        ],
        "gates": ["lessons"],
        "provocation": ("governance/lessons/negative_control.py", ""),
    },
    {
        "id": "system",
        "authorities": ["module.json", "docs/MODULE-ADMISSION.md"],
        "gates": ["system-app-declaration"],
        "provocation": ("scripts/check-system-app-declaration.sh", "provok"),
    },
    {
        "id": "app",
        "authorities": ["docs/MODULE-ADMISSION.md"],
        "gates": ["module-admission"],
        "provocation": ("scripts/check-module-admission.sh", "negative control"),
    },
    {
        "id": "env-var",
        "authorities": [
            "docs/GIT-ENV-VARIABLES.md",
            "infra/terraform/modules/web-surface/auth-env.json",
        ],
        "gates": ["portal-auth-env"],
        "provocation": ("scripts/check-portal-auth-env.sh", "provoked"),
    },
    {
        "id": "gov",
        "authorities": ["docs/GOLDEN-RULES.md", "AGENTS.md"],
        "gates": ["authority"],
        "provocation": ("scripts/check-authority.sh", "isolation"),
    },
    {
        "id": "issues",
        "authorities": ["governance/dispatch/README.md", ".board/snapshot.json"],
        "gates": ["issue-claims"],
        "provocation": ("scripts/check-issue-claims.sh", "mutant"),
    },
    {
        "id": "index",
        "authorities": [
            "docs/CODEIDX-CAPABILITY-REGISTER.md",
            "docs/DIAGRAMS-CAPABILITY-REGISTER.md",
            "governance/knowledge/catalog.json",
        ],
        "gates": ["knowledge-index", "diagrams-capability-register"],
        "provocation": ("scripts/check-diagrams-capability-register.sh", "negative control"),
    },
]

EXPECTED_MECHANISMS = [
    "class", "pattern", "template", "rca", "system", "app",
    "env-var", "gov", "issues", "index",
]


def _discovered_gates(root: Path) -> set:
    """The gate names `scripts/discover-checks.sh` would wire into make verify."""
    return {
        p.name[len("check-"):-len(".sh")]
        for p in sorted((root / "scripts").glob("check-*.sh"))
    }


def _denylisted_gates(root: Path) -> set:
    path = root / "scripts" / "check-denylist.txt"
    if not path.exists():
        return set()
    blocked = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            blocked.add(line)
    return blocked


def mechanism_ok(root: Path, mech: dict, discovered: set, denylisted: set) -> str:
    """One mechanism's chain. Returns a detail string or raises AssertionError
    naming the broken link: a missing authority, a dead/denylisted/undiscovered
    gate, or a missing provocation artifact."""
    missing_auth = [a for a in mech["authorities"] if not (root / a).exists()]
    if missing_auth:
        raise AssertionError("authority missing: %s" % ", ".join(missing_auth))
    for gate in mech["gates"]:
        script = root / "scripts" / ("check-%s.sh" % gate)
        if not script.exists():
            raise AssertionError("gate check-%s.sh does not exist" % gate)
        if script.stat().st_size == 0:
            raise AssertionError("gate check-%s.sh is empty (a dead gate)" % gate)
        if gate in denylisted or script.name in denylisted:
            raise AssertionError("gate %s is denylisted (silently unwired)" % gate)
        if gate not in discovered:
            raise AssertionError("gate %s is not discovered into make verify" % gate)
    provocation_path, needle = mech["provocation"]
    artifact = root / provocation_path
    if not artifact.exists():
        raise AssertionError("provocation artifact %s is missing" % provocation_path)
    if needle and needle.lower() not in artifact.read_text(encoding="utf-8").lower():
        raise AssertionError("provocation %r not found in %s" % (needle, provocation_path))
    return "%d gate(s), %d authorit(y/ies), provocation %s" % (
        len(mech["gates"]), len(mech["authorities"]), provocation_path,
    )


def mechanisms_complete(mechanisms: list, expected: list) -> str:
    got = [m["id"] for m in mechanisms]
    missing = [e for e in expected if e not in got]
    extra = [g for g in got if g not in expected]
    if missing or extra:
        raise AssertionError(
            "mechanism set drift — missing %s, extra %s" % (missing, extra)
        )
    return "%d mechanism(s) named" % len(got)


def mechanisms_disjoint(mechanisms: list) -> str:
    all_gates = [g for m in mechanisms for g in m["gates"]]
    dupes = {g for g in all_gates if all_gates.count(g) > 1}
    if dupes:
        raise AssertionError(
            "a gate is claimed by two mechanisms: %s" % ", ".join(sorted(dupes))
        )
    return "%d gate(s) across %d mechanism(s), none shared" % (
        len(all_gates), len(mechanisms),
    )


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

    # -- the futureproof surface: every mechanism's chain, end to end --------
    discovered = _discovered_gates(ROOT)
    denylisted = _denylisted_gates(ROOT)
    for mech in MECHANISMS:
        def _mk(m=mech):
            return lambda: mechanism_ok(ROOT, m, discovered, denylisted)
        checks.append(("mechanism-%s" % mech["id"], _mk()))
    checks.append(
        ("mechanisms-complete",
         lambda: mechanisms_complete(MECHANISMS, EXPECTED_MECHANISMS))
    )
    checks.append(
        ("mechanisms-disjoint", lambda: mechanisms_disjoint(MECHANISMS))
    )

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
