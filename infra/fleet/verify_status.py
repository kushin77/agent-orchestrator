#!/usr/bin/env python3
"""verify_status.py — classify a verify-runner outcome into a commit-status plan.

Issue #1295 (parent #706). The `verify-runner` job declared in
`infra/fleet/jobs.yaml` runs `bash scripts/verify.sh verify` on the
shared-services pair and publishes the result as a GitHub commit status. This
module is the ONE place that turns the run's outcome into that status, so the
rule can be provoked on every gate run instead of being argued for in prose.

WHY THE CLASSIFICATION IS NOT A ONE-LINER
The gate of record is a tri-state (0 / 1 / 2) and the runner has TWO more
outcome classes that Cloud Build used to have and the pair inherits from
`scripts/gate-lock.sh`:

    rc 0                -> success
    rc 1                -> failure, NAMING the checks that failed
    rc 2                -> error   (CANNOT-ASSESS is never a pass; #739)
    rc 10 / 11 / 12     -> PARKED: the gate never started

A PARKED run is NOT a verdict. Publishing it as `failure` trains an operator to
ignore red; publishing it as `success` is a false green; publishing `error`
records a defect where there is only contention. So a park publishes NOTHING and
is re-queued BY NAME — #1267's surviving rule, kept because Cloud Build was
never what made it true.

TWO WAYS FAILING TO NAME A CHECK IS REFUSED RATHER THAN DEFAULTED
Issue #1295's acceptance asks for a red status "by check name". A status that
says only "make verify: FAIL" sends a reader to the gate log to learn what
failed, so this module reads the run's own attestation
(`.verify/attestation.json`) and refuses -- exit 2, CANNOT-ASSESS -- when it
cannot name the failing checks, or when the attestation it was handed is not the
record of the rc it was given (a stale file would otherwise be published as this
run's evidence).

SINGLE SOURCE FOR THE STATE. The rc -> state table lives in
`scripts/gate-status-map.py` (ADR-0028) and is NOT restated here: this module
imports that file and drives the very function the poster uses. The self-test
asserts the two agree for every outcome, so a change to the table cannot leave
this classifier behind.

Usage:
    verify_status.py plan --rc <n> [--attestation <path>]   -> JSON plan, exit 0
    verify_status.py --self-test                            -> controls, exit 0/1
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

# The outcomes that mean "the gate never ran". They come from
# `scripts/gate-lock.sh`, and they are deliberately OUTSIDE the gate's own
# tri-state so a parked run can never be read as a pass or as a failure.
PARKED_CODES = (10, 11, 12)

# A check with one of these rc values did not fail the run: 0 is a pass and 2 is
# SKIP / CANNOT-ASSESS, which the gate records as a skip and never as a failure.
NOT_FAILED_CODES = (0, 2)


def repo_root() -> Path:
    """The checkout this file lives in (``<root>/infra/fleet/verify_status.py``)."""
    return Path(__file__).resolve().parents[2]


def load_mapper():
    """Import ``scripts/gate-status-map.py`` -- the only home of the rc->state table."""
    path = repo_root() / "scripts" / "gate-status-map.py"
    if not path.is_file():
        raise FileNotFoundError(f"the rc -> state mapper is missing: {path}")
    spec = importlib.util.spec_from_file_location("ao_gate_status_map", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"the rc -> state mapper could not be loaded: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def failing_checks(attestation_path: Path, rc: int) -> list:
    """The names of the checks that failed, read from the run's own attestation.

    Refuses (raises ValueError) when the attestation is not this run's record,
    or when a failing run's attestation names no failing check. Both refusals
    exist so the published status can never be less specific than the run.
    """
    if not attestation_path.is_file():
        raise ValueError(f"the attestation is missing: {attestation_path}")
    try:
        data = json.loads(attestation_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"the attestation is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("the attestation is not a mapping")
    recorded = data.get("exit_code")
    if recorded != rc:
        raise ValueError(
            f"the attestation records exit_code={recorded!r}, not the rc={rc} being published "
            "-- a stale attestation must never be attached to a fresh verdict"
        )
    names = []
    for check in data.get("checks") or []:
        if not isinstance(check, dict):
            continue
        check_rc = check.get("rc")
        if check_rc in NOT_FAILED_CODES or check_rc is None:
            continue
        name = check.get("name")
        if name:
            names.append(str(name))
    return sorted(set(names))


def classify(rc: int, attestation_path: Path) -> dict:
    """The status plan for one outcome. Refuses (ValueError) on an unknown one."""
    mapper = load_mapper()

    if rc in PARKED_CODES:
        return {
            "rc": rc,
            "state": None,
            "publish": False,
            "requeue": "by-name",
            "failing": [],
            "description": f"verify: PARKED (rc {rc}) -- the gate never started; not a verdict",
            "reason": "a parked run is not a pass and not a failure, so it publishes nothing and is re-queued by name",
        }

    # The state comes from the mapper, so this classifier cannot drift from the
    # poster. An unknown rc raises here, which is the refusal we want.
    state = mapper.map_rc(rc)

    if rc != 1:
        return {
            "rc": rc,
            "state": state,
            "publish": True,
            "requeue": None,
            "failing": [],
            "description": mapper.summarize(rc),
            "reason": f"rc {rc} is a gate of record outcome",
        }

    names = failing_checks(attestation_path, rc)
    if not names:
        raise ValueError(
            "rc 1 with no failing check named in the attestation -- a red status that does not "
            "say WHAT failed is not the status #1295 asks for; refusing to publish a vague red"
        )
    return {
        "rc": rc,
        "state": state,
        "publish": True,
        "requeue": None,
        "failing": names,
        "description": "make verify: FAIL (" + ", ".join(names) + ")",
        "reason": "rc 1 is a failure, and the failing checks are named",
    }


# --------------------------------------------------------------------------
# The controls. Each one is asserted against its expectation, and the ACTUAL
# value is printed beside it so a mismatch is visible rather than inferred.
# --------------------------------------------------------------------------
def self_test(scratch: Path) -> int:
    problems = []
    scratch.mkdir(parents=True, exist_ok=True)

    def control(label, expectation, actual, hold):
        print(f"  control {label}: {'PASS' if hold else 'FAIL'}")
        print(f"      expectation: {expectation}")
        print(f"      actual:      {actual}")
        if not hold:
            problems.append(label)

    def attestation(name, exit_code, failing):
        path = scratch / name
        checks = [{"name": n, "rc": 1, "status": "FAIL", "verdict": "FAIL"} for n in failing]
        checks.append({"name": "shell-syntax", "rc": 0, "status": "PASS", "verdict": "OK"})
        checks.append({"name": "terraform", "rc": 2, "status": "SKIP", "verdict": "WARN"})
        path.write_text(
            json.dumps({"exit_code": exit_code, "result": "PASS" if exit_code == 0 else "FAIL",
                        "checks": checks}),
            encoding="utf-8",
        )
        return path

    green = attestation("green.json", 0, [])
    red = attestation("red.json", 1, ["cloudbuild", "shell-patterns"])
    stale = attestation("stale.json", 0, [])
    vague = attestation("vague.json", 1, [])

    # 1. rc 0 is a pass.
    plan = classify(0, green)
    control("RC0-SUCCESS", "state=success publish=True", f"state={plan['state']} publish={plan['publish']}",
            plan["state"] == "success" and plan["publish"] is True)

    # 2. rc 1 is a failure that NAMES the failing checks.
    plan = classify(1, red)
    control("RC1-NAMES-THE-CHECK", "state=failure description names cloudbuild and shell-patterns",
            f"state={plan['state']} description={plan['description']!r}",
            plan["state"] == "failure"
            and plan["failing"] == ["cloudbuild", "shell-patterns"]
            and plan["description"] == "make verify: FAIL (cloudbuild, shell-patterns)")

    # 3. rc 2 is an error, never a pass (#739).
    plan = classify(2, green)
    control("CANNOT-ASSESS-NOT-A-PASS", "state=error", f"state={plan['state']}", plan["state"] == "error")

    # 4. A PARKED run publishes nothing and is re-queued by name.
    for code in PARKED_CODES:
        plan = classify(code, green)
        control(
            f"PARKED-{code}-NOT-A-VERDICT",
            "state=None publish=False requeue=by-name",
            f"state={plan['state']} publish={plan['publish']} requeue={plan['requeue']}",
            plan["state"] is None and plan["publish"] is False and plan["requeue"] == "by-name",
        )

    # 5. An unknown outcome is REFUSED, never defaulted to a status.
    for bogus in (3, 7, 99, -1):
        try:
            plan = classify(bogus, green)
            control(f"UNKNOWN-{bogus}-REFUSED", "ValueError", f"returned state={plan['state']!r}", False)
        except ValueError as exc:
            control(f"UNKNOWN-{bogus}-REFUSED", "ValueError", f"ValueError: {exc}", True)

    # 6. A stale attestation is refused: rc 1 with the PASS run's record.
    try:
        plan = classify(1, stale)
        control("STALE-ATTESTATION-REFUSED", "ValueError", f"returned description={plan['description']!r}", False)
    except ValueError as exc:
        control("STALE-ATTESTATION-REFUSED", "ValueError", f"ValueError: {exc}", True)

    # 7. A red run that cannot name its failing checks is refused, not published vaguely.
    try:
        plan = classify(1, vague)
        control("VAGUE-RED-REFUSED", "ValueError", f"returned description={plan['description']!r}", False)
    except ValueError as exc:
        control("VAGUE-RED-REFUSED", "ValueError", f"ValueError: {exc}", True)

    # 8. The state table is the poster's, not a copy. Compared for every outcome
    #    the mapper owns, so a change to that table cannot leave this behind.
    mapper = load_mapper()
    for rc in (0, 1, 2):
        want = mapper.map_rc(rc)
        got = classify(rc, red if rc == 1 else green)["state"]
        control(f"SINGLE-SOURCE-RC{rc}", f"map_rc({rc})={want}", f"classify={got}", got == want)

    if problems:
        print(f"verify_status: NOT-OK -- control(s) failed: {', '.join(problems)}", file=sys.stderr)
        return 1
    print("verify_status: OK -- every outcome classifies, a park is not a verdict, and a vague or stale red is refused")
    return 0


def main(argv: list) -> int:
    parser = argparse.ArgumentParser(description="Classify a verify-runner outcome into a status plan.")
    parser.add_argument("verb", nargs="?", help="plan")
    parser.add_argument("--rc", type=int, help="the gate's exit code")
    parser.add_argument("--attestation", help="path to .verify/attestation.json")
    parser.add_argument("--self-test", action="store_true", help="run the controls and exit")
    args = parser.parse_args(argv[1:])

    if args.self_test:
        import tempfile

        scratch = Path(tempfile.mkdtemp(prefix="ao-verify-status."))
        try:
            return self_test(scratch)
        finally:
            for child in sorted(scratch.rglob("*"), reverse=True):
                try:
                    child.rmdir() if child.is_dir() else child.unlink()
                except OSError:
                    pass
            try:
                scratch.rmdir()
            except OSError:
                pass

    if args.verb != "plan":
        print("verify_status: usage: verify_status.py plan --rc <n> [--attestation <path>]", file=sys.stderr)
        return 2
    if args.rc is None:
        print("verify_status: CANNOT-ASSESS -- --rc is required", file=sys.stderr)
        return 2

    attestation = Path(args.attestation) if args.attestation else repo_root() / ".verify" / "attestation.json"
    try:
        plan = classify(args.rc, attestation)
    except (ValueError, FileNotFoundError, ImportError) as exc:
        print(f"verify_status: CANNOT-ASSESS -- {exc}", file=sys.stderr)
        return 2
    print(json.dumps(plan, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
