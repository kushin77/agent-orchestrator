"""The gate's exit vocabulary, consumed by the close-out that asks it for evidence.

Close-out has exactly one question for the repository gate: *was this tree green?*
To answer it, step 2 runs ``make verify`` in the lane worktree and reads the exit
code. Until #840 the whole implementation was "non-zero means no green
attestation", which is wrong, because ``scripts/verify.sh`` is no longer the only
thing that decides what a non-zero code means. Since the admission control of
#724 a gate that cannot take a permit exits with a code of its own, deliberately
**outside** the gate's own 0/1/2 tri-state:

=====  =========================================================================
0        ADMITTED — the gate ran to completion
1        NOT-OK — the gate ran, and a check failed
2        CANNOT-ASSESS — the gate ran, and could not measure
10       PARKED — another gate already holds this worktree; nothing was run
11       PARKED — every box-wide permit slot is taken; nothing was run
12       CANNOT-ASSESS — the permit store cannot be trusted; nothing was run
=====  =========================================================================

Measured on #836 (2026-09-15): ``record-verification`` read rc 11 as "no green
attestation", so a *capacity* condition was reported as a **missing** verification
- an assertion that something was measured and came back absent, when nothing was
measured at all - and the item was filed on the board as a defect it was not. Worse,
the remediation printed with it ("run ``make verify`` on the branch head") re-parked
on every attempt, because the cap was still full: the system was instructing an
operator to perform a thing it would refuse. A verdict the producer's own words call
"not a pass and not a failure" cannot be consumed as either one.

**The exit code alone cannot carry that distinction, and this is measured.** GNU
make exits **2** for *any* failing recipe and merely prints the recipe's own code:

>>> make verify ; echo $?
verify: PARKED (rc 11, not a pass and not a failure) — the box-wide gate cap is reached; nothing was run and no attestation was touched
make: *** [Makefile:146: verify] Error 11
2

So a park (10, 11), an unusable permit store (12), the gate's own CANNOT-ASSESS (2)
and a genuine check failure (1) all reach the consumer as the same number. The
distinction lives in the gate's **own final verdict line**, which this module reads —
``verify: PARKED (rc 11, …)``, ``verify: FAIL (…)``, ``verify: PASS (…)``,
``verify: CANNOT-ASSESS (rc N, …)`` — with the process exit code as the fallback for a
caller that invokes the gate without make. Reading only the exit code was the first
form of this fix, and a real park (real cap, real lane) showed it mislabelling the
case as an unusable permit store.

Two properties make the table trustworthy:

* **It is closed.** Every outcome maps to one of four verdicts, and this module is
  the only place that decision is made. ``failed`` is reserved for the gate's own
  *"a check ran and failed"* report, so a genuine failure is still a failure however
  the admission control is configured.
* **It is not restated.** The admission codes are imported from their owner
  (``fleet/gatelock.py``) rather than copied, so a change on the producer's side
  cannot silently re-label the consumer's verdict.

Nothing here is a pass. An unassessed run is the *absence* of evidence, and an
absence of evidence is not evidence: the verdict is CANNOT-ASSESS, and the
close-out carries it rather than inventing a result.
"""

from __future__ import annotations

import os
import re
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Mapping

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# The admission control's codes, from their owner (issue #724) — never restated.
from fleet.gatelock import EXIT_PARKED, EXIT_REFUSED, EXIT_STORE_UNUSABLE  # noqa: E402

MODULE = "lifecycle-gate"

#: The composite gate's own tri-state, as ``scripts/verify.sh`` declares it.
EXIT_ADMITTED = 0
EXIT_FAILED = 1
EXIT_CANNOT_ASSESS = 2

#: 128 + N is the shell's convention for "killed by signal N" (SIGINT 130, SIGTERM
#: 143, SIGHUP 129); a child killed directly reports a negative return code.
#: Measured on this box: a long gate is regularly SIGTERM'd by a neighbouring
#: lane's interrupt, and an interrupt is not a check failure.
SIGNAL_EXIT_BASE = 128
SIGNAL_EXIT_TOP = 192

#: What a gate run means to a consumer. Four values, and no fifth.
VERDICT_ADMITTED = "admitted"
VERDICT_FAILED = "failed"
VERDICT_PARKED = "parked"
VERDICT_UNASSESSED = "unassessed"

#: The verdicts that are neither a pass nor a failure.
UNASSESSED_VERDICTS = (VERDICT_PARKED, VERDICT_UNASSESSED)

#: The environment variable naming the box-wide permit bound, so a reader can see
#: the cap rather than being told to guess it.
CAP_ENV = "AO_GATE_MAX_CONCURRENT"

#: The bounded retry budget: attempts *after* the first, and the wait between them.
#: A park is a capacity condition and permits do free, so one wait is worth taking;
#: more than a couple is a close-out spinning in a lane the board wants back. Both
#: are overridable, and ``AO_LIFECYCLE_GATE_RETRIES=0`` disables the retry.
RETRIES_ENV = "AO_LIFECYCLE_GATE_RETRIES"
RETRY_WAIT_ENV = "AO_LIFECYCLE_GATE_RETRY_WAIT"
DEFAULT_RETRIES = 1
DEFAULT_RETRY_WAIT = 30.0

_CAP_PATTERN = re.compile(r"\bgate cap\D{0,4}(\d+)")
#: The gate's own verdict line. Anchored at a line start and matched at the *end* of
#: the transcript, because every check's output is teed into the same stream.
_VERDICT_PATTERN = re.compile(
    r"^\s*verify:\s*(PASS|FAIL|PARKED|CANNOT-ASSESS)\b[^\n]*", re.MULTILINE | re.IGNORECASE
)
_CODE_PATTERN = re.compile(r"\brc\s*(\d+)")
#: GNU make's own report of the recipe's status: `make: *** [Makefile:146: verify]
#: Error 11`. The gate's FAIL banner says how many checks failed but not the code, so
#: this is where a failing run's declared code is read from.
_MAKE_ERROR_PATTERN = re.compile(r"\bmake: \*\*\* .*?\bError (\d+)")


class CannotAssess(RuntimeError):
    """A gate run that cannot be read as a verification result (#840).

    Raised by the consumer's gate port when the gate was PARKED (10/11), when its
    permit store could not be trusted (12), when the gate's own tri-state said
    CANNOT-ASSESS (2), or when it was killed by a signal. It carries the verdict
    and the remediation, because the remedy for a capacity condition is to retry
    when capacity frees - not to re-run a command that will be refused again.
    """

    def __init__(self, verdict: str, detail: str, remediation: str) -> None:
        super().__init__(detail)
        self.verdict = verdict
        self.detail = detail
        self.remediation = remediation


def interrupted(exit_code: int) -> bool:
    """Whether an exit code says the process was stopped, not that it answered."""
    return exit_code < 0 or SIGNAL_EXIT_BASE <= exit_code <= SIGNAL_EXIT_TOP


def verdict_of(exit_code: int) -> str:
    """What a gate *process's* exit code means — the fallback, when it said nothing.

    Used for a caller that invokes the gate without ``make`` (which propagates the
    code) and for a run that died before announcing a verdict. ``1`` is the only
    code that means *the gate ran and a check failed*; every other non-zero value is
    the absence of a result, and is named as one.
    """
    if exit_code == EXIT_ADMITTED:
        return VERDICT_ADMITTED
    if exit_code == EXIT_FAILED:
        return VERDICT_FAILED
    if exit_code in (EXIT_REFUSED, EXIT_PARKED):
        return VERDICT_PARKED
    return VERDICT_UNASSESSED


def declared_verdict(output: str) -> tuple[str, int | None, str] | None:
    """The gate's own last verdict line: ``(name, code, line)``, or ``None``.

    Read from the **end** of the transcript on purpose. ``scripts/verify.sh`` runs
    every check through ``tee`` into the same stream, so an earlier ``PARKED`` can be
    a check provoking a refusal to prove the control works; the gate's own banner is
    printed last, and it is the only line that reports *this* run's outcome.
    """
    matches = list(_VERDICT_PATTERN.finditer(output or ""))
    if not matches:
        return None
    line = matches[-1].group(0).strip()
    code = _CODE_PATTERN.search(line)
    return matches[-1].group(1).upper(), (int(code.group(1)) if code else None), line


def verdict_of_run(exit_code: int, output: str = "") -> str:
    """What a gate run means: its own declared verdict first, its exit code second."""
    declared = declared_verdict(output)
    if declared is None:
        return verdict_of(exit_code)
    name = declared[0]
    if name == "PARKED":
        return VERDICT_PARKED
    if name == "FAIL":
        return VERDICT_FAILED
    if name == "PASS":
        # A verdict contradicting the process status is not a result to trust.
        return VERDICT_ADMITTED if exit_code == EXIT_ADMITTED else VERDICT_UNASSESSED
    return VERDICT_UNASSESSED


def reason_of(verdict: str, declared: int | None, exit_code: int) -> str:
    """A named reason for the verdict, claiming no more than is knowable."""
    if verdict == VERDICT_FAILED:
        return "the gate ran, and a check failed"
    if verdict == VERDICT_PARKED:
        if declared == EXIT_REFUSED:
            return "PARKED — another gate already holds this worktree, and nothing was run"
        if declared == EXIT_PARKED:
            return "PARKED — every box-wide permit slot is taken, and nothing was run"
        return "PARKED — the gate was refused a permit, and nothing was run"
    if declared == EXIT_CANNOT_ASSESS:
        return "CANNOT-ASSESS — the gate ran and could not measure"
    if declared == EXIT_STORE_UNUSABLE:
        return "CANNOT-ASSESS — the gate permit store is unusable, and nothing was run"
    if interrupted(exit_code):
        return f"the gate was killed by a signal (rc {exit_code}), so it never answered"
    return f"the gate ended without reporting one of its own outcomes (rc {exit_code})"


def cap_of(output: str = "", env: Mapping[str, str] | None = None) -> int | None:
    """The box-wide permit bound: what the gate's message named, else the environment.

    The gate prints the cap in its own PARKED refusal (``fleet/gatelock.py``), and
    it inherits this process's environment, so either source is authoritative.
    Neither yields a value means the cap is *unknown*, and it is reported as the
    name of the variable to read — never guessed, because a wrong number in a
    remediation is worse than a missing one.
    """
    match = _CAP_PATTERN.search(output or "")
    if match:
        return int(match.group(1))
    raw = (os.environ if env is None else env).get(CAP_ENV, "").strip()
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def retry_budget(env: Mapping[str, str] | None = None) -> tuple[int, float]:
    """The bounded retry budget: retries after the first attempt, and the wait."""
    source = os.environ if env is None else env
    return (
        _non_negative_int(source.get(RETRIES_ENV), DEFAULT_RETRIES),
        _non_negative_float(source.get(RETRY_WAIT_ENV), DEFAULT_RETRY_WAIT),
    )


def _non_negative_int(raw: str | None, default: int) -> int:
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return value if value >= 0 else default


def _non_negative_float(raw: str | None, default: float) -> float:
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return value if value >= 0 else default


@dataclass(frozen=True)
class GateAttempt:
    """One run of the composite gate: exit code, transcript, and what it means."""

    #: The *process* exit code. With ``make`` this is make's own status (2 for any
    #: failing recipe), so it is never the whole story on its own.
    exit_code: int
    output: str = ""
    attempt: int = 1

    @property
    def declared(self) -> tuple[str, int | None, str] | None:
        """The gate's own last verdict line, when it printed one."""
        return declared_verdict(self.output)

    @property
    def declared_code(self) -> int | None:
        """The code this run declared — the gate's own, else the one make named.

        For a park the gate states it in its verdict line (``rc 11``); for a failed
        check make names the recipe's status (``Error 1``) while the recipe's own
        banner says only how many checks failed. Either way this is the code to show
        *beside* the wrapper's, and it is never mistaken for the wrapper's own.
        """
        declared = self.declared
        if declared and declared[1] is not None:
            return declared[1]
        match = _MAKE_ERROR_PATTERN.search(self.output or "")
        return int(match.group(1)) if match else None

    @property
    def verdict(self) -> str:
        return verdict_of_run(self.exit_code, self.output)

    @property
    def admitted(self) -> bool:
        return self.verdict == VERDICT_ADMITTED

    @property
    def parked(self) -> bool:
        return self.verdict == VERDICT_PARKED

    def reason(self) -> str:
        return reason_of(self.verdict, self.declared_code, self.exit_code)

    def codes(self) -> str:
        """Both codes when the wrapper masked the gate's own, so neither is hidden."""
        declared = self.declared_code
        if declared is None:
            return f"rc {self.exit_code}"
        if declared == self.exit_code:
            return f"rc {declared}"
        return f"the gate declared rc {declared}, the wrapper exited {self.exit_code}"

    def headline(self) -> str:
        """The gate's own sentence for this outcome, quoted rather than paraphrased.

        ``scripts/verify.sh`` produces the PARKED sentence; the operator should read
        its words, not this module's approximation of them.
        """
        declared = self.declared
        return declared[2] if declared else ""


@dataclass(frozen=True)
class GateRun:
    """Every attempt a bounded retry made, and the verdict the run ended on.

    The attempts are kept, not collapsed: a retry that is not recorded is a retry
    that can be mistaken for a single pass or a single failure, and the whole
    defect behind #840 is a consumer reporting an outcome it did not measure.
    """

    attempts: tuple[GateAttempt, ...]
    retries: int = 0
    waited: float = 0.0

    @property
    def final(self) -> GateAttempt:
        return self.attempts[-1]

    @property
    def exit_code(self) -> int:
        return self.final.exit_code

    @property
    def verdict(self) -> str:
        return self.final.verdict

    @property
    def admitted(self) -> bool:
        return self.verdict == VERDICT_ADMITTED

    @property
    def failed(self) -> bool:
        return self.verdict == VERDICT_FAILED

    @property
    def parked(self) -> bool:
        return self.verdict == VERDICT_PARKED

    @property
    def cannot_assess(self) -> bool:
        """True when the run produced no verification result at all."""
        return self.verdict in UNASSESSED_VERDICTS

    @property
    def cap(self) -> int | None:
        return cap_of("\n".join(attempt.output for attempt in self.attempts))

    def detail(self) -> str:
        """One line naming the verdict, both codes, the cap, and what the retry did."""
        headline = self.final.headline() or f"verify: {self.verdict.upper()} (rc {self.exit_code})"
        cap = self.cap
        cap_text = f"{CAP_ENV}={cap}" if cap is not None else f"{CAP_ENV} unset"
        if len(self.attempts) > 1:
            retry_text = f"{len(self.attempts)} attempt(s) over {self.waited:.1f}s"
        elif self.retries:
            retry_text = f"1 attempt (retry budget {self.retries}: retry when capacity frees)"
        else:
            retry_text = "1 attempt (retry disabled)"
        return f"{headline} [{self.final.reason()}; {self.final.codes()}; {cap_text}; {retry_text}]"

    def remediation(self) -> str:
        """What clears this outcome — for a capacity condition, waiting, not re-running."""
        if self.verdict == VERDICT_PARKED:
            return (
                "retry when capacity is free: the gate was never started, so there is nothing to "
                f"fix. A permit frees when another gate finishes, or {CAP_ENV} can be raised."
            )
        if interrupted(self.exit_code):
            return "retry: the gate was stopped by a signal and measured nothing."
        return "repair the gate's permit store, then retry: the gate could not measure."


def run_gate(
    attempt: Callable[[], GateAttempt],
    *,
    retries: int | None = None,
    wait: float | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> GateRun:
    """Run the gate once, retrying a PARKED run inside a bounded budget.

    **Only a park is retried.** A failure (rc 1) is terminal: the gate already
    answered, and re-running it would be asking the same question twice while
    calling the second answer a retry. A pass is obviously terminal. Every attempt
    is recorded in the returned run, so the retry can never silently become a pass
    it did not measure.
    """
    budget_retries, budget_wait = retry_budget()
    retries = budget_retries if retries is None else retries
    wait = budget_wait if wait is None else wait

    attempts: list[GateAttempt] = []
    waited = 0.0
    for index in range(1, retries + 2):
        attempts.append(replace(attempt(), attempt=index))
        if not attempts[-1].parked or index > retries:
            break
        sleep(wait)
        waited += wait
    return GateRun(tuple(attempts), retries, waited)
