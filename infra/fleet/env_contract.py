#!/usr/bin/env python3
"""env_contract.py — the fleet-cron image's ENVIRONMENT CONTRACT (issue #710, EPIC #706 D2).

D1 (#709) packaged the three scheduled jobs into one image. D2 starts that image
here, on the box it was written on, with the jobs in dry-run — and the first
thing a started container does is read its environment. That read used to be
implicit: each variable was defaulted where it was used, so an operator could
start the container with a value no code path could honour and the container
would come up looking healthy.

This module is the ONE declaration of what this image's environment may be:

* ``VARS`` names every variable the image reads, its default, the rule its value
  must satisfy, the finding code reported when it does not, and why it exists.
* :func:`resolve` applies the defaults; :func:`validate` returns every refusal.
* ``python3 infra/fleet/env_contract.py check`` is the seam ``entrypoint.sh``
  calls BEFORE it installs the schedule, and ``dev_run.py`` calls before it
  dispatches a role. One definition, two callers — so "the entrypoint validates
  env" and "the harness validates env" cannot disagree about what valid means.

Two deliberate refusals, stated here because a reader will ask:

* **an absent variable is never a refusal** — this is the dev-first harness, and
  every default is a value that works on this box. An INVALID value is refused,
  by name, with the value it saw: a container that comes up with
  ``AO_FLEET_CRON_INTERVAL=banana`` and silently does nothing is the failure this
  file exists to prevent.
* **``AO_FLEET_DRY_RUN`` must be ``1``** — this lane is the dry-run harness, so a
  value that would make it apply is refused rather than honoured. The applying
  mode is D3+'s deliverable (EPIC #706); when it lands it must edit this rule
  deliberately, and the gate's provocation for ``dry-run-required`` will make
  that edit visible instead of silent.

Tri-state exit contract (this repository's convention): 0 OK / 1 NOT-OK /
2 CANNOT-ASSESS.

---knowledge---
module_id: infra.fleet.env_contract
system: infra
app: fleet
solution_class: class
patterns: [pre-standard-snapshot]
derives_from: null
owner_sme: iac-sme
tier: L1
interfaces: [Var, Finding, resolve, validate, undeclared, cmd_check, cmd_print, build_parser, (+1 more)]
invariants: ""
gotchas: ""
related: ["#1911"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path

OK, NOT_OK, CANNOT_ASSESS = 0, 1, 2


@dataclass(frozen=True)
class Var:
    """One variable: how it defaults, what makes a value invalid, and why it exists."""

    name: str
    default: str
    kind: str
    code: str
    why: str
    choices: tuple[str, ...] = ()

    def invalid(self, value: str) -> str | None:
        """The refusal detail for ``value``, or ``None`` when it satisfies the rule."""
        if self.kind == "dir":
            return None if Path(value).is_dir() else f"{value!r} is not a directory"
        if self.kind == "positive-int":
            if value.isdigit() and int(value) > 0:
                return None
            return f"{value!r} is not a positive integer"
        if self.kind == "port":
            if value.isdigit() and 1 <= int(value) <= 65535:
                return None
            return f"{value!r} is not a port in 1..65535"
        if self.kind == "one-of":
            if value in self.choices:
                return None
            allowed = ", ".join(repr(item) for item in self.choices)
            return f"{value!r} is not one of {allowed}"
        if self.kind == "optional":
            # Any value, including empty, is valid — used for a var whose
            # ABSENCE is meaningful (e.g. an auth command that legitimately
            # has no default) rather than a value this contract can shape-check.
            return None
        if self.kind == "nonempty":
            return None if value.strip() else "must not be empty"
        raise AssertionError(f"unknown rule kind {self.kind!r}")  # pragma: no cover


# THE declaration. Ordered as the container reads them: where the repo is, where
# its state is, then how the run behaves.
VARS: tuple[Var, ...] = (
    Var(
        name="AO_FLEET_REPO",
        default="/repo",
        kind="dir",
        code="repo-not-a-directory",
        why="the checkout the image COPY'd; every role is executed from it.",
    ),
    Var(
        name="AO_FLEET_DIR",
        default="/repo/.fleet",
        kind="dir",
        code="fleet-dir-not-a-directory",
        why="the fleet's runtime root (fleet/runtime.py). The dev run mounts the live one read-only.",
    ),
    Var(
        name="AO_FLEET_BOARD_DIR",
        default="/repo/.board",
        kind="dir",
        code="board-dir-not-a-directory",
        why="the SHARED claim ledger. It is deliberately not namespaced, so two fleets cannot claim one issue.",
    ),
    Var(
        name="AO_FLEET_CRON_INTERVAL",
        default="2",
        kind="positive-int",
        code="interval-not-a-positive-integer",
        why="the schedule's own interval, passed to `fleet/cron.py install` — the schedule's single owner.",
    ),
    Var(
        name="AO_FLEET_CRON_NO_INSTALL",
        default="0",
        kind="one-of",
        choices=("0", "1"),
        code="no-install-not-a-flag",
        why="1 skips the schedule assertion, which is how D1's gate proves the entrypoint is what installs it.",
    ),
    Var(
        name="AO_FLEET_DRY_RUN",
        default="1",
        kind="one-of",
        choices=("1",),
        code="dry-run-required",
        why="this image (D2) is the dev-first dry-run harness; a run that would apply is refused.",
    ),
    Var(
        name="AO_FLEET_PORT",
        default="8790",
        kind="port",
        code="port-out-of-range",
        why="the port /healthz answers on; the compose file publishes this same variable.",
    ),
    Var(
        name="AO_FLEET_ROLE_TIMEOUT",
        default="120",
        kind="positive-int",
        code="role-timeout-not-a-positive-integer",
        why="the bound on each dispatched role, so a wedged role is reported rather than waited on forever.",
    ),
    Var(
        name="AO_FLEET_STATE_RW",
        default="0",
        kind="one-of",
        choices=("0", "1"),
        code="state-rw-not-a-flag",
        why=(
            "D3 (issue #711, EPIC #706): whether the state mounts (.fleet/.board) are bound "
            "read-write instead of read-only. OFF by default — the compose file's primary "
            "service never sets this to '1'; only the flag-gated `state-rw` profile service "
            "does. This is independent of AO_FLEET_DRY_RUN: a writable mount is not permission "
            "to apply — dry-run-required still refuses AO_FLEET_DRY_RUN=0 regardless of this flag."
        ),
    ),
    Var(
        name="AO_FLEET_AR_READER_KEY_FILE",
        default="",
        kind="optional",
        code="ar-reader-key-file-invalid",
        why=(
            "issue #1329: a mounted Artifact Registry service-account JSON key "
            "PATH for infra/fleet/promote_portal.py. Optional — the rung accepts "
            "this OR AO_FLEET_AR_ACCESS_TOKEN_CMD; absent both, it refuses "
            "ar-auth-missing rather than defaulting to a key it invents."
        ),
    ),
    Var(
        name="AO_FLEET_AR_ACCESS_TOKEN_CMD",
        default="",
        kind="optional",
        code="ar-access-token-cmd-invalid",
        why=(
            "issue #1329: an alternative auth shape for promote_portal.py — a "
            "COMMAND (never a value) whose stdout is a bearer token for "
            "Artifact Registry. Optional, same fallback pair as "
            "AO_FLEET_AR_READER_KEY_FILE above."
        ),
    ),
    Var(
        name="AO_FLEET_PORTAL_HEALTHZ_URL",
        default="http://127.0.0.1:18286/api/healthz",
        kind="nonempty",
        code="portal-healthz-url-invalid",
        why="issue #1329: the URL promote_portal.py polls after a recreate before deciding healthy/rollback.",
    ),
    Var(
        name="AO_RUNNER_HOST_ROLE",
        default="standby",
        kind="one-of",
        choices=("primary", "standby"),
        code="runner-host-role-invalid",
        why=(
            "issue #1343: whether THIS host runs the PR runner rung (fleet/runner/cli.py). "
            "`primary` is the shared-services pair only: fleet/cron.py renders the `runner` "
            "line only when this says primary, and cli.py refuses `host-role-not-primary` "
            "otherwise. The default is standby, so a host that says nothing merges nothing."
        ),
    ),
    Var(
        name="AO_RUNNER_CAPACITY",
        default="",
        kind="optional",
        code="runner-capacity-invalid",
        why=(
            "issue #1343: the PR runner's parallel-verify width. Empty = min(8, nproc // 2) "
            "(fleet/runner/capacity.py); a positive integer declares it. Backed off under load "
            "or below the memory floor either way, never below 1."
        ),
    ),
    Var(
        name="AO_RUNNER_MEM_FLOOR_GB",
        default="8",
        kind="positive-int",
        code="runner-mem-floor-not-a-positive-integer",
        why=(
            "issue #1343: the MemAvailable floor (GB) below which the PR runner halves its "
            "fan-out before each cycle (capacity-backoff:memory)."
        ),
    ),
    Var(
        name="AO_FLEET_PYTHON",
        default="/usr/bin/python3",
        kind="optional",
        code="fleet-python-invalid",
        why=(
            "issue #1370: the interpreter `fleet/cron.py install` renders every managed "
            "crontab line with. The runner rung's own verify needs the ~/ao-verify-venv "
            "Python 3.14 venv, not the box's system /usr/bin/python3 (3.12's "
            "`Path.glob('**')` silently skips files — #1245's knowledge-index red). "
            "Optional: any path is accepted, since a container can point this at a venv "
            "this contract cannot see."
        ),
    ),
    Var(
        name="AO_FLEET_CRON_PATH",
        default="",
        kind="optional",
        code="fleet-cron-path-invalid",
        why=(
            "issue #1370: when set, `fleet/cron.py install` emits exactly one `PATH=...` "
            "line at the top of the managed crontab block, so every rung inherits it — the "
            "runner rung needs `gh`/`gcloud` from /snap/bin, which cron's own PATH lacks. "
            "Empty (default) installs no PATH= line, current behaviour unchanged."
        ),
    ),
)

BY_NAME: dict[str, Var] = {var.name: var for var in VARS}


@dataclass(frozen=True)
class Finding:
    """One refusal: the variable, the rule it broke, and the value it saw."""

    code: str
    detail: str


def resolve(env: dict[str, str] | None = None) -> dict[str, str]:
    """The resolved environment: every declared variable set, defaults applied."""
    source = os.environ if env is None else env
    return {var.name: source.get(var.name, var.default) for var in VARS}


def validate(env: dict[str, str] | None = None) -> list[Finding]:
    """Every refusal. An empty list means the environment satisfies the contract."""
    resolved = resolve(env)
    findings: list[Finding] = []
    for var in VARS:
        detail = var.invalid(resolved[var.name])
        if detail is not None:
            findings.append(Finding(code=var.code, detail=f"{var.name}={detail}"))
    return findings


def undeclared(env: dict[str, str] | None = None) -> list[str]:
    """Set ``AO_FLEET_*`` variables that this image does not declare.

    Reported, never refused: a variable this contract does not know about is a
    finding for the reader — it may be another module's (``AO_FLEET_SESSION`` is
    ``fleet/runtime.py``'s, and it is legitimately unset in a container) — but it
    is exactly what a typo looks like, so it is printed on every check.
    """
    source = os.environ if env is None else env
    return sorted(name for name in source if name.startswith("AO_FLEET_") and name not in BY_NAME)


def cmd_check(args: argparse.Namespace) -> int:
    findings = validate()
    for var in VARS:
        resolved = resolve()[var.name]
        note = "" if resolved == var.default else f"  (default {var.default})"
        print(f"  OK    {var.name}={resolved}{note}")
    for name in undeclared():
        print(f"  NOTE  {name} is not declared by this contract and is not read by it")
    for finding in findings:
        print(f"  FAIL  {finding.code}: {finding.detail}", file=sys.stderr)
    if findings:
        print(f"env-contract: REFUSED — {len(findings)} finding(s)", file=sys.stderr)
        return NOT_OK
    print(f"env-contract: OK — {len(VARS)} declared variable(s) satisfy the contract")
    return OK


def cmd_print(args: argparse.Namespace) -> int:
    """The resolved values as ``NAME=value`` lines, for a shell caller."""
    for name, value in resolve().items():
        print(f"{name}={value}")
    return OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="env-contract", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check", help="validate the ambient environment (exit 0/1)")
    check.set_defaults(func=cmd_check)
    printing = sub.add_parser("print", help="print the resolved values, defaults applied")
    printing.set_defaults(func=cmd_print)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
