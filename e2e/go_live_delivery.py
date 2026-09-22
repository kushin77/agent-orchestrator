"""E2E probe: the ordered go-live run delivering a surface a client can read (#955).

WHY this leg exists. Two halves of the delivery claim were each proven on their
own and **nothing ran them in sequence**:

* the capstone suite proves the EPIC-00 DoD (signup -> org -> personas -> agents
  -> routed calls -> audit + billing) — the *platform*;
* ``e2e/workbook11_portal.py`` proves the workbook-11 portal surfaces ship OFF
  and are invisible unauthorised — the *portal*;
* ``infra/rollout/`` promotes flags through an ordered, owner-gated ladder and
  records the result in ``live-state.yaml`` — the *delivery*.

Measured before this file: ``grep -rln 'go_live\\|go-live' --include='*.sh'
--include='*.py' scripts/ e2e/`` returned nothing and ``grep -rln 'infra.rollout'
e2e/`` returned nothing. So the sentence EPIC #607 is *about* — "the ordered
owner-gated run delivers a surface a client can read" — had no proof at all: the
ladder could reach every declared stage and leave the portal dark, and no test
would notice.

WHAT THIS PROBE DOES — the six stages, over the real modules, offline:

| Stage | Real API consumed | What is proven |
|---|---|---|
| ``declared`` | ``infra.rollout.cli validate`` + ``go_live.py --preflight`` | the run is ordered and lawful, and names the owner-gated transitions |
| ``refused`` | ``go_live.py`` with no gate inputs | rc 1 **and nothing written** (the sandbox tree is byte-identical) |
| ``promoted`` | ``infra/rollout/go_live.py``'s ``GoLiveDriver`` | every planned flag reaches its declared ``go_live_stage``; ``live-state.yaml`` records each with an ``audit_record`` that exists; the hash-chained log verifies |
| ``served`` | ``portal.server.app.build_app`` | with the promoted state an authenticated client reads ``200`` carrying the console's *own* projection; an unauthenticated one gets ``401`` |
| ``dark`` | the same app, the shipped declaration | ``/api/fleet/snapshot`` is ``404 feature_disabled`` — an unpromoted surface is *absent*, not merely unauthorised |
| ``rolled-back`` | ``infra.rollout.cli rollback`` + the runtime overlay | a rollback takes the surface dark with **no commit**: the declaration file is byte-identical either side |

WHY IT CANNOT PASS VACUOUSLY. The ``served`` stage's predicate
(:func:`serves_the_client`) is asserted in **both directions of one switch**: the
promoted state must PASS it and the shipped state must FAIL it, and
:func:`mutation_control` additionally feeds it a *weakened* predicate to prove the
verdict distinguishes "served" from "answered at all" (the ``dark`` stage alone
would pass if the predicate only looked at the status code).
:func:`negative_controls` provokes four more refusals against the real engine,
driver and validator — each control passes only when the real guard genuinely
blocks, and each records the exact refusal it produced.

OFFLINE BY CONSTRUCTION. No network, no sockets, no docker, no credential, no
GCP call. The ladder runs against a **sandbox root** (its own
``infra/rollout/{stage-model,go-live-plan,rollout-state,live-state}.yaml`` and its
own ``audit/``+``approvals/`` directories) and the portal is pointed at that
sandbox through its documented seams (``AO_SURFACE_REGISTRY``,
``AO_SURFACE_STATE``). Nothing is written into the repository: the probe copies
the declarations *out* and asserts the checkout is untouched
(:func:`repo_fingerprint`, before and after).

The clock is the only thing that is not real: the declared ``gradual`` dwell is
24h, so the second pass drives the same driver with its ``now`` field set past
the recorded hold (the driver's own time source — no sleeping, no faked
timestamp). The evidence records exactly how far ahead it was advanced.

---knowledge---
module_id: e2e.go_live_delivery
system: e2e
app: delivery
solution_class: enterprise
patterns: [no-false-green, offline-composition-root]
derives_from: e2e/golden_path.py
owner_sme: qa-sme
tier: L1
interfaces: [ordered go-live run]
invariants: "runs the platform, portal and delivery halves in sequence, not just each alone"
gotchas: ""
related: ["#955"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional, Sequence, Tuple

from e2e._paths import REPO_ROOT, ensure_sys_paths

ensure_sys_paths()

import yaml  # noqa: E402

# ``portal.server.app`` FIRST, deliberately. ``portal.server.livestore`` imports
# ``telemetry.metering.model`` at module import time, and
# ``portal.server.fleet.load_fleet_console`` inserts ``<repo>/fleet`` on
# ``sys.path`` — where ``fleet/telemetry.py`` then shadows the ``telemetry``
# *package* for every later ``import telemetry.*`` ("'telemetry' is not a
# package"). Importing the app first caches the package; a probe that loaded the
# console first could not build the app at all (measured while writing this).
from portal.server.app import build_app  # noqa: E402
from portal.server.fleet import FLEET_SURFACE, declares_on, load_fleet_console  # noqa: E402

from infra.rollout import go_live as go_live_module  # noqa: E402
from infra.rollout.checks.check_rollout import check_live_state  # noqa: E402
from infra.rollout.engine import (  # noqa: E402
    Approval,
    ApprovalLedger,
    RolloutEngine,
    RolloutError,
)
from infra.rollout.go_live import (  # noqa: E402
    EXIT_CANNOT_ASSESS,
    EXIT_NOT_OK,
    EXIT_OK,
    GoLiveDriver,
)
from infra.rollout.model import RolloutStage  # noqa: E402

# --------------------------------------------------------------------------- #
# What the delivery claim is about
# --------------------------------------------------------------------------- #

#: The surface the EPIC's acceptance criterion reads: ``portal/server/fleet.py``
#: refuses every ``/api/fleet/*`` route while this surface's declaration is off.
REQUIRED_FLAG = f"surfaces.{FLEET_SURFACE}"
REQUIRED_SURFACE = FLEET_SURFACE
#: The phase ``go-live-plan.yaml`` declares the SPoG in (control plane + portal).
REQUIRED_PHASE = "7"
#: The route family the client reads.
REQUIRED_ROUTE = "/api/fleet/snapshot"

#: The identity the console binds to every tenant offline (``portal/server/state.py``
#: seed directory); a root-admin session sees the whole projection.
ROOT_ADMIN_EMAIL = "root@platform.example.com"
TENANT = "platform"

#: The executing identity and the approver. They MUST differ (AO-GR-14); the
#: engine refuses an approval whose approver is the actor, and that refusal is
#: provoked by the ``approver-must-differ`` control.
ACTOR = "deployer-sa"
APPROVER = "owner-sme"

#: The declarations the ladder is driven over, copied into the sandbox.
DECLARATIONS = (
    "stage-model.yaml",
    "go-live-plan.yaml",
    "rollout-state.yaml",
    "live-state.yaml",
)
LIVE_STATE_REL = "live-state.yaml"
AUDIT_LOG_REL = "audit/promotion-audit.jsonl"
APPROVALS_REL = "approvals"
AUDIT_SUBDIR = "audit"

#: How far past the recorded ``gradual`` hold the second pass is driven. The
#: declared dwell is 24h (``stage-model.yaml`` ``gradual.ramp.dwell``); the probe
#: does not sleep, it moves the driver's own clock.
DWELL_ADVANCE = timedelta(hours=25)
#: How far past the recorded hold the *advanced* clock is asserted to be.
DWELL_MINIMUM = timedelta(hours=24)


class DeliveryCannotAssess(Exception):
    """A declaration the probe depends on is missing or unreadable (no false green)."""


# --------------------------------------------------------------------------- #
# The sandbox
# --------------------------------------------------------------------------- #
def build_sandbox(
    work_dir: Path, *, declarations_root: Path | str = REPO_ROOT
) -> Path:
    """Copy the rollout declarations into a sandbox root the run writes into.

    A *root*, not a directory of fixtures: ``infra/rollout/{cli,go_live}.py``
    resolve everything they read and write through ``<root>/infra/rollout``, so a
    copied tree is the whole seam — the run needs no repository write access and
    the probe can assert the checkout stayed byte-identical.

    ``declarations_root`` is the checkout the declarations are copied FROM. The
    suite never passes it (the repository's own declarations are the subject); it
    exists so the same probe can be pointed at another checkout of the same
    repository — e.g. the branch a sibling lane lands the registration on — and
    never at a hand-written fixture, because a fixture would prove nothing about
    the repository.
    """
    source = Path(declarations_root) / "infra" / "rollout"
    dest = Path(work_dir) / "sandbox" / "infra" / "rollout"
    (dest / AUDIT_SUBDIR).mkdir(parents=True, exist_ok=True)
    (dest / APPROVALS_REL).mkdir(parents=True, exist_ok=True)
    for name in DECLARATIONS:
        origin = source / name
        if not origin.is_file():
            raise DeliveryCannotAssess(f"missing rollout declaration: {origin}")
        shutil.copy2(origin, dest / name)
    return Path(work_dir) / "sandbox"


def sandbox_rollout(sandbox: Path) -> Path:
    return sandbox / "infra" / "rollout"


def read_yaml(path: Path) -> Any:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def live_state_doc(sandbox: Path) -> Dict[str, Any]:
    document = read_yaml(sandbox_rollout(sandbox) / LIVE_STATE_REL) or {}
    flags = document.get("flags")
    return dict(flags) if isinstance(flags, Mapping) else {}


def stages_of(entries: Mapping[str, Any]) -> Dict[str, str]:
    return {str(name): str(entry.get("stage", "")) for name, entry in entries.items()}


def plan_targets(sandbox: Path) -> Dict[str, str]:
    """Every flag the plan declares, with the ``go_live_stage`` it asks for."""
    document = read_yaml(sandbox_rollout(sandbox) / "go-live-plan.yaml") or {}
    out: Dict[str, str] = {}
    for phase in document.get("phase_order") or []:
        body = (document.get("phases") or {}).get(phase) or {}
        for surface in body.get("surfaces") or []:
            out[str(surface["flag"])] = str(surface["go_live_stage"])
    return out


def plan_phase_of(sandbox: Path) -> Dict[str, str]:
    document = read_yaml(sandbox_rollout(sandbox) / "go-live-plan.yaml") or {}
    out: Dict[str, str] = {}
    for phase in document.get("phase_order") or []:
        body = (document.get("phases") or {}).get(phase) or {}
        for surface in body.get("surfaces") or []:
            out[str(surface["flag"])] = str(phase)
    return out


def tree_digest(root: Path) -> str:
    """A content digest of every file under ``root`` (absent -> ``"absent"``)."""
    if not root.exists():
        return "absent"
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_file():
            digest.update(str(path.relative_to(root)).encode("utf-8"))
            digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def repo_fingerprint(repo_root: Path | str = REPO_ROOT) -> Dict[str, str]:
    """What the run must not change: the tracked tree's status and the declarations.

    ``git status --porcelain -uall`` is the whole-tree assertion (a new untracked
    file counts); the digests are the content evidence behind it, over the three
    trees a rollout or a portal read could plausibly write into.
    """
    repo = Path(repo_root)
    status = subprocess.run(
        ["git", "status", "--porcelain", "-uall"],
        cwd=str(repo),
        capture_output=True,
        text=True,
        timeout=300,
    )
    return {
        "git_status": status.stdout,
        "infra": tree_digest(repo / "infra"),
        "registry": tree_digest(repo / "infra" / "feature-flags" / "registry.yaml"),
        "rollout_overlay": tree_digest(repo / ".rollout"),
        "portal_config": tree_digest(repo / "portal" / "config"),
    }


# --------------------------------------------------------------------------- #
# Stage 1 — declared
# --------------------------------------------------------------------------- #
def _run(argv: Sequence[str], *, cwd: Path) -> Dict[str, Any]:
    """One offline subprocess, in the repository (the CLI resolves its own root)."""
    result = subprocess.run(
        list(argv),
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=900,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    return {
        "argv": " ".join(argv),
        "rc": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def probe_declared(sandbox: Path, *, repo_root: Path | str = REPO_ROOT) -> Dict[str, Any]:
    """``validate`` + ``--preflight``: the run is ordered, lawful and owner-gated."""
    validate = _run(
        [sys.executable, "-m", "infra.rollout.cli", "validate", "--root", str(sandbox)],
        cwd=Path(repo_root),
    )
    preflight = _run(
        [sys.executable, "infra/rollout/go_live.py", "--root", str(sandbox), "--preflight"],
        cwd=Path(repo_root),
    )
    text = preflight["stdout"]
    owner_gated = [line.strip() for line in text.splitlines() if line.strip().startswith("OWNER-GATED")]
    needs_human = [
        line.strip()
        for line in text.splitlines()
        if line.strip().startswith("approval MISSING:")
    ]
    return {
        "validate": {k: validate[k] for k in ("argv", "rc", "stdout")},
        "preflight": {
            "argv": preflight["argv"],
            "rc": preflight["rc"],
            "orderedAndLawful": "PREFLIGHT OK" in text,
            "promotionOrder": "strict-by-phase" in text,
            "ownerGatedLines": owner_gated,
            "ownerGatedTransitions": len(needs_human),
            "nothingWritten": "nothing was promoted" in text,
        },
    }


# --------------------------------------------------------------------------- #
# Stage 2 — refused
# --------------------------------------------------------------------------- #
def probe_refused(sandbox: Path, *, repo_root: Path | str = REPO_ROOT) -> Dict[str, Any]:
    """A run missing its gate inputs is refused BEFORE anything is written."""
    before = tree_digest(sandbox)
    run = _run(
        [
            sys.executable,
            "infra/rollout/go_live.py",
            "--root",
            str(sandbox),
            "--actor",
            ACTOR,
            "--approvals-dir",
            APPROVALS_REL,
            "--live-state-out",
            LIVE_STATE_REL,
            "--audit-log",
            AUDIT_LOG_REL,
        ],
        cwd=Path(repo_root),
    )
    reason = ""
    match = re.search(r"go-live driver: NOT-OK - (.*)", run["stderr"])
    if match:
        reason = match.group(1).splitlines()[0].strip()
    # The refusal names one blocked transition per line; the PLAN section names
    # them too, so counting stdout as well would double every one of them.
    refusal_lines = [
        line.strip()
        for line in run["stderr"].splitlines()
        if "blocked: missing gate signal" in line
    ]
    return {
        "argv": run["argv"],
        "rc": run["rc"],
        "reason": reason,
        "blockingSignals": sorted(
            {line.rsplit("missing gate signal", 1)[1].split(" -")[0].strip() for line in refusal_lines}
        ),
        "blockedTransitions": len(refusal_lines),
        "blockedFlags": sorted({line.split("->", 1)[0].strip() for line in refusal_lines}),
        "planNamesThemToo": sum(
            1 for line in run["stdout"].splitlines() if "blocked: missing gate signal" in line
        ),
        "wroteNothing": tree_digest(sandbox) == before,
        "liveStateEmpty": not live_state_doc(sandbox),
        "auditRecordsWritten": len(list((sandbox_rollout(sandbox) / AUDIT_SUBDIR).glob("*.md"))),
    }


# --------------------------------------------------------------------------- #
# Stage 3 — promoted
# --------------------------------------------------------------------------- #
def grant_approvals(
    sandbox: Path, targets: Mapping[str, str], *, repo_root: Path | str = REPO_ROOT
) -> Dict[str, Any]:
    """Approval-as-code for every flag whose declared stage is ``full``.

    The real CLI writes each record (``grant-approval``), for the same reason the
    engine reads them: the file format is the declaration the pipeline grants in
    production, and the approver is deliberately NOT the executing actor.
    """
    grants: Dict[str, str] = {}
    for flag in sorted(targets):
        if targets[flag] != RolloutStage.FULL.value:
            continue
        approval_id = f"approval-{flag.replace('.', '-')}-full"
        run = _run(
            [
                sys.executable,
                "-m",
                "infra.rollout.cli",
                "grant-approval",
                flag,
                "--to",
                RolloutStage.FULL.value,
                "--approver",
                APPROVER,
                "--approval-id",
                approval_id,
                "--approvals-dir",
                str(sandbox_rollout(sandbox) / APPROVALS_REL),
            ],
            cwd=Path(repo_root),
        )
        if run["rc"] != EXIT_OK:
            raise DeliveryCannotAssess(
                f"grant-approval refused {flag}: rc {run['rc']} {run['stderr'].strip()[:200]}"
            )
        grants[flag] = approval_id
    return {"grants": grants, "count": len(grants)}


def drive(
    sandbox: Path,
    *,
    now: Optional[datetime] = None,
    phase_tokens: Sequence[str] = (),
    only: Sequence[str] = (),
    dry_run: bool = False,
    actor: str = ACTOR,
    canary_health_ok: bool = True,
) -> Tuple[int, GoLiveDriver]:
    """One real ``GoLiveDriver`` run over the sandbox; returns ``(rc, driver)``.

    The driver is the module's own entry point (``go_live.py``'s CLI is a thin
    argparse wrapper around exactly this object, which is why the refusal paths
    are asserted through ``run()``'s exit code).
    """
    driver = GoLiveDriver(
        root=Path(sandbox),
        live_state_rel=LIVE_STATE_REL,
        audit_log_rel=AUDIT_LOG_REL,
        approvals_rel=APPROVALS_REL,
        actor=actor,
        canary_health_ok=canary_health_ok,
        verify_green=True,
        phase_tokens=tuple(phase_tokens),
        only=tuple(only),
        dry_run=dry_run,
        command_line="python3 infra/rollout/go_live.py (e2e probe, issue #955)",
        **({"now": now} if now is not None else {}),
    )
    return driver.run(), driver


def _silently(fn: Callable[[], Any]) -> Any:
    """Run ``fn`` with its stdout/stderr captured (every driver run prints a plan)."""
    import contextlib
    import io

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
        value = fn()
    return value, buffer.getvalue()


def probe_promoted(sandbox: Path, *, repo_root: Path | str = REPO_ROOT) -> Dict[str, Any]:
    """The ordered run: every planned flag reaches its declared go-live stage.

    Two passes, because the stage model declares a real hold: a ``gradual -> full``
    step is *deferred* until the ``gradual`` entry has been recorded for its
    declared dwell, so the first pass lawfully reaches ``gradual`` (rc 1 — the run
    is honestly incomplete) and the second, driven past the hold with the owner's
    approval-as-code in the ledger, completes the plan (rc 0).

    A plan that does not declare the required flag is **reported, not skipped**:
    the run still happens (so the ladder's own behaviour is measured either way),
    and ``registrationMissing`` names the gap for the stages that depend on it.
    Without that registration the ordered run can reach every stage it declares
    and still leave the SPoG dark — which is the delivery failure this file
    exists to make visible.
    """
    targets = plan_targets(sandbox)
    registration_missing = ""
    if REQUIRED_FLAG not in targets:
        registration_missing = (
            f"go-live-plan.yaml does not declare '{REQUIRED_FLAG}': the ordered run cannot "
            f"promote the flag {REQUIRED_ROUTE} is refused on, so the plan cannot deliver "
            "the surface the epic's acceptance criterion reads"
        )

    # -- pass 1: off -> canary -> gradual, with the 24h hold refusing `full` ----
    (rc1, pass1), _pass1_text = _silently(lambda: drive(sandbox))
    stages1 = stages_of(live_state_doc(sandbox))
    dispositions1: Dict[str, int] = {}
    for assessment in pass1.assessments:
        dispositions1[assessment.disposition] = dispositions1.get(assessment.disposition, 0) + 1

    # The state the run is in *between* the two passes: every flag at a stage
    # BELOW its declared target. The controls provoke here, because a guard that
    # is asked to refuse a transition that is already complete would report the
    # wrong refusal ("not forward") and prove nothing.
    mid_state = Path(sandbox).parent / "state-after-pass1"
    shutil.copytree(Path(sandbox), mid_state, dirs_exist_ok=True)

    # -- the owner's approvals -------------------------------------------------
    grants = grant_approvals(sandbox, targets, repo_root=repo_root)

    # -- pass 2: the declared hold has elapsed --------------------------------
    recorded = live_state_doc(sandbox)
    since = [go_live_module._parse_ts(entry.get("since")) for entry in recorded.values()]
    since = [moment for moment in since if moment is not None]
    if not since:
        raise DeliveryCannotAssess("no recorded transition timestamp: the dwell cannot be measured")
    entered = max(since)
    advanced_by = max((entered + DWELL_ADVANCE) - datetime.now(timezone.utc), DWELL_ADVANCE)
    advanced_now = entered + advanced_by

    (rc2, pass2), _pass2_text = _silently(lambda: drive(sandbox, now=advanced_now))
    stages2 = stages_of(live_state_doc(sandbox))
    dispositions2: Dict[str, int] = {}
    for assessment in pass2.assessments:
        dispositions2[assessment.disposition] = dispositions2.get(assessment.disposition, 0) + 1

    entries = live_state_doc(sandbox)
    rollout = sandbox_rollout(sandbox)
    missing_records = [
        {"flag": name, "auditRecord": str(entry.get("audit_record", ""))}
        for name, entry in sorted(entries.items())
        if not (rollout / str(entry.get("audit_record", ""))).is_file()
    ]
    engine = RolloutEngine.load(
        stage_model_path=str(rollout / "stage-model.yaml"),
        rollout_state_path=str(rollout / "rollout-state.yaml"),
        audit_path=str(rollout / AUDIT_LOG_REL),
        live_state_path=str(rollout / LIVE_STATE_REL),
    )
    short_of_target = {
        flag: {"at": stages2.get(flag), "declared": targets[flag]}
        for flag in sorted(targets)
        if stages2.get(flag) != targets[flag]
    }
    return {
        "planTargets": targets,
        "planPhases": plan_phase_of(sandbox),
        "requiredFlag": REQUIRED_FLAG,
        "requiredDeclaredStage": targets.get(REQUIRED_FLAG),
        "registrationMissing": registration_missing,
        "requiredFlagDeclared": REQUIRED_FLAG in targets,
        "snapshotAfterPass1": str(mid_state),
        "pass1": {
            "rc": rc1,
            "stages": stages1,
            "dispositions": dispositions1,
            "deferredTransitions": dispositions1.get("deferred", 0),
            "reachedRequiredFlag": stages1.get(REQUIRED_FLAG),
            "auditRecords": len(list((rollout / AUDIT_SUBDIR).glob("*.md"))),
        },
        "approvals": grants,
        "dwell": {
            "enteredAt": entered.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "advancedBy": str(advanced_by),
            "advancedByIsAtLeastADay": advanced_by >= DWELL_MINIMUM,
            "drivenAt": advanced_now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "slept": "0s (the driver's own clock was moved; nothing was waited for)",
        },
        "pass2": {
            "rc": rc2,
            "stages": stages2,
            "dispositions": dispositions2,
            "promotedTransitions": dispositions2.get("promoted", 0),
            "shortOfTarget": short_of_target,
        },
        "liveState": {
            "entries": len(entries),
            "stages": stages2,
            "missingAuditRecords": missing_records,
            "auditChainVerifies": engine.audit.verify(),
            "auditLogRecords": len(engine.audit.records()),
            "requiredEntry": entries.get(REQUIRED_FLAG),
        },
        "reachedDeclaredStage": not short_of_target and not registration_missing,
    }


# --------------------------------------------------------------------------- #
# The projection: promoted state -> the declaration the portal reads
# --------------------------------------------------------------------------- #
def project_registry(
    sandbox: Path, *, declarations_root: Path | str = REPO_ROOT
) -> Dict[str, Any]:
    """The declaration the portal reads, PROJECTED from what the run recorded.

    Promotion and serving are two documents on purpose: the ladder records a
    *flag's* stage in ``live-state.yaml`` and the portal reads the *surface's*
    declaration in ``infra/feature-flags/registry.yaml`` — a reviewed IaC change
    is what couples them (GR-5: never an ambient side effect of a deploy). This
    function applies that coupling mechanically and derivably: a surface whose
    flag the run recorded above ``off`` is declared ``on``; every other surface
    keeps the value it ships with, so a promotion of one flag cannot widen
    another surface. It is the same one-value edit
    ``scripts/portal-dev-session.py::write_promoted_registry`` makes by hand and
    ``e2e/erp/golden_path.py::promoted_config`` makes for the ERP module; this one
    derives *which* surfaces from the run's own record instead of taking a name.
    """
    source = Path(declarations_root) / "infra" / "feature-flags" / "registry.yaml"
    if not source.is_file():
        raise DeliveryCannotAssess(f"missing declaration: {source}")
    document = read_yaml(source)
    if not isinstance(document, Mapping):
        raise DeliveryCannotAssess(f"{source} is not a mapping")
    surfaces = document.get("surfaces")
    if not isinstance(surfaces, Mapping):
        raise DeliveryCannotAssess(f"{source} carries no surfaces mapping")

    recorded = live_state_doc(sandbox)
    promoted: Dict[str, Any] = {}
    flipped: Dict[str, str] = {}
    shipped: Dict[str, Any] = {}
    for name, raw in surfaces.items():
        entry = dict(raw) if isinstance(raw, Mapping) else {}
        shipped[str(name)] = entry.get("default")
        key = f"surfaces.{name}"
        stage = recorded.get(key, {}).get("stage")
        if stage and RolloutStage.coerce(stage) is not RolloutStage.OFF:
            entry["default"] = "on"
            entry["promoted"] = True
            flipped[str(name)] = str(stage)
        promoted[str(name)] = entry

    projected = dict(document)
    projected["surfaces"] = promoted
    target = sandbox / "sandbox" / "infra" / "feature-flags" / "registry-projected.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(projected, sort_keys=False), encoding="utf-8")
    return {
        "path": target,
        "source": source,
        "sourceRoot": str(Path(declarations_root)),
        "digest": hashlib.sha256(target.read_bytes()).hexdigest(),
        "sourceDigest": hashlib.sha256(source.read_bytes()).hexdigest(),
        "flipped": flipped,
        "shippedDefaults": shipped,
        "requiredDeclaredOn": declares_on(promoted.get(REQUIRED_SURFACE)),
    }


def registry_declares_on(registry_path: Path | str, surface: str) -> bool:
    """Does this declaration file promote ``surface``? (the reader's own predicate)."""
    document = read_yaml(Path(registry_path)) or {}
    surfaces = document.get("surfaces") if isinstance(document, Mapping) else None
    entry = (surfaces or {}).get(surface) if isinstance(surfaces, Mapping) else None
    return declares_on(entry)


def shipped_registry_copy(
    sandbox: Path, *, declarations_root: Path | str = REPO_ROOT
) -> Path:
    """An unmodified copy of the registry as it ships (what ``dark`` reads)."""
    source = Path(declarations_root) / "infra" / "feature-flags" / "registry.yaml"
    target = sandbox / "sandbox" / "infra" / "feature-flags" / "registry-shipped.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return target


# --------------------------------------------------------------------------- #
# The session + the observation
# --------------------------------------------------------------------------- #
def mint_session() -> Dict[str, str]:
    """A genuine offline console session (the recipe ``portal/tests/conftest.py`` uses).

    The console issues no credential of its own: the shared-frontend auth gate
    does, and the console verifies it offline against the gate's published JWKS.
    The probe plays the gate — key generation is local, nothing is fetched.
    """
    import time

    from identity.sso.jose import generate_rsa_keypair, jwks_for_keys
    from identity.sso.tokens import console_kid_for, issue_console_session_token
    from portal.server.sso import SESSION_COOKIE

    private_key, public_key = generate_rsa_keypair()
    kid = console_kid_for(public_key)
    token, _ = issue_console_session_token(
        private_key,
        kid=kid,
        tenant_id=TENANT,
        subject_id=ROOT_ADMIN_EMAIL,
        email=ROOT_ADMIN_EMAIL,
        name="root",
        role="admin",
        now=int(time.time()),
        ttl=3600,
    )
    return {
        "jwks": jwks_for_keys([(kid, public_key)]),
        "cookies": {SESSION_COOKIE: token},
    }


class _Pointed:
    """Point the portal at the sandbox through its documented env seams.

    ``AO_SURFACE_REGISTRY`` is the declared seam ``portal/server/fleet.py`` reads
    (a deployment can mount its declaration elsewhere; a probe can point the real
    application at a projected one). ``AO_SURFACE_STATE`` is the rollback overlay's
    seam — it is set for every observation so a leftover overlay in the checkout
    can never silently take the surface dark under the probe (the trap
    ``docs/PORTAL-OFFLINE-DEV.md`` documents).
    """

    def __init__(self, *, registry: Path, overlay: Path, restore_overlay: bool = True) -> None:
        self.registry = str(registry)
        self.overlay = str(overlay)
        self.restore_overlay = restore_overlay
        self._saved: Dict[str, Optional[str]] = {}

    def __enter__(self) -> "_Pointed":
        self._saved = {
            "AO_SURFACE_REGISTRY": os.environ.get("AO_SURFACE_REGISTRY"),
            "AO_SURFACE_STATE": os.environ.get("AO_SURFACE_STATE"),
        }
        os.environ["AO_SURFACE_REGISTRY"] = self.registry
        os.environ["AO_SURFACE_STATE"] = self.overlay
        return self

    def __exit__(self, *exc: object) -> None:
        for key, previous in self._saved.items():
            if previous is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = previous


def observe_surface(
    sandbox: Path,
    *,
    registry: Path,
    repo_root: Path | str = REPO_ROOT,
    session: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Read ``/api/fleet/snapshot`` off the REAL app, anonymously and authenticated.

    Every expectation is recorded **from the producers themselves** (the console's
    own rung list, the authz adapter's own section list, the console's own repo
    constant), so the assertion downstream is "the client reads the projection the
    console declares", not "the client read a 200".
    """
    repo = Path(repo_root)
    session = session or mint_session()
    overlay = Path(sandbox) / "sandbox" / "runtime" / "surface-state.json"
    overlay.parent.mkdir(parents=True, exist_ok=True)

    from portal.server.fleet_authz import SNAPSHOT_SECTIONS
    from portal.server.sso import ConsoleSso

    console = load_fleet_console(repo)
    expected_rungs = sorted(str(name) for name, _pattern, _beat in console.rung_specs())
    rung_row_keys = sorted(console.rungs_snapshot()[expected_rungs[0]])

    with _Pointed(registry=Path(registry), overlay=overlay):
        app = build_app(
            repo_root=repo,
            sso=ConsoleSso(
                jwks=session["jwks"], root_admin_emails=(ROOT_ADMIN_EMAIL,)
            ),
        )
        enabled = bool(app.fleet.enabled)
        anonymous = _read(app, REQUIRED_ROUTE)
        authenticated = _read(app, REQUIRED_ROUTE, cookies=session["cookies"])

    data = authenticated["data"] or {}
    return {
        "route": REQUIRED_ROUTE,
        "registry": str(registry),
        "registryDigest": hashlib.sha256(Path(registry).read_bytes()).hexdigest(),
        "surfaceEnabled": enabled,
        "statusAnonymous": anonymous["status"],
        "codeAnonymous": anonymous["code"],
        "status": authenticated["status"],
        "code": authenticated["code"],
        "projection": {
            "repo": data.get("repo"),
            "head": data.get("head"),
            "now": data.get("now"),
            "uptime": data.get("uptime"),
            "sections": sorted(data) if data else [],
            "rungs": sorted((data.get("rungs") or {})),
            "rungRowKeys": (
                sorted((data.get("rungs") or {}).get(expected_rungs[0]) or {})
                if data.get("rungs")
                else []
            ),
            "lanes": sorted((data.get("claims") or [])),
            "waveCount": len(data.get("waves") or []),
        },
        "expectedSections": sorted(SNAPSHOT_SECTIONS),
        "expectedRungs": expected_rungs,
        "expectedRungRowKeys": rung_row_keys,
        "expectedRepo": str(console.REPO),
        "error": authenticated["error"],
    }


def _read(app: Any, path: str, *, cookies: Optional[Mapping[str, str]] = None) -> Dict[str, Any]:
    response = app.handle("GET", path, cookies=dict(cookies or {}))
    try:
        payload = json.loads(response.as_bytes().decode("utf-8"))
    except ValueError:
        payload = {}
    return {
        "status": response.status,
        "code": ((payload.get("error") or {}) or {}).get("code"),
        "error": payload.get("error"),
        "data": payload.get("data"),
    }


# --------------------------------------------------------------------------- #
# The served predicate (one switch, two directions — and a mutation to prove it)
# --------------------------------------------------------------------------- #
def serves_the_client(observation: Mapping[str, Any]) -> bool:
    """True only when an authenticated client read the console's OWN projection.

    Deliberately strict: a 200 over an empty body is not "the client can read the
    surface". The four facts below are the console's own vocabulary (its repo id,
    its rung list, its row schema, the authz adapter's section list), so a stub or
    a truncated payload fails on the second one.
    """
    if observation.get("status") != 200 or observation.get("code"):
        return False
    projection = observation.get("projection") or {}
    if sorted(projection.get("sections") or []) != sorted(observation.get("expectedSections") or []):
        return False
    if sorted(projection.get("rungs") or []) != sorted(observation.get("expectedRungs") or []):
        return False
    if sorted(projection.get("rungRowKeys") or []) != sorted(observation.get("expectedRungRowKeys") or []):
        return False
    if projection.get("repo") != observation.get("expectedRepo"):
        return False
    return bool(projection.get("head")) and bool(projection.get("now"))


def served_verdict(
    observation: Mapping[str, Any], *, predicate: Optional[Callable[[Mapping[str, Any]], bool]] = None
) -> str:
    """``"PASS"`` / ``"FAIL"`` for one observation — over an injectable predicate.

    The injection is what makes the predicate *provable*: the control feeds a
    weakened predicate over the same observation and requires a different verdict.
    """
    decided = predicate if predicate is not None else serves_the_client
    return "PASS" if decided(observation) else "FAIL"


def answers_at_all(observation: Mapping[str, Any]) -> bool:
    """A deliberately WEAK predicate: an answer of any kind counts as served.

    This is the false green the served stage must not have: the ``dark``
    observation answers (``404 feature_disabled``), so a probe that only asked
    "did the route answer?" would report the unpromoted surface as served.
    """
    return observation.get("status") is not None


# --------------------------------------------------------------------------- #
# Stages 4-6 — served / dark / rolled back
# --------------------------------------------------------------------------- #
def probe_served(sandbox: Path, projection: Mapping[str, Any], *, repo_root: Path | str = REPO_ROOT) -> Dict[str, Any]:
    """The promoted state, read by an authenticated client — and by nobody else."""
    observation = observe_surface(sandbox, registry=Path(projection["path"]), repo_root=repo_root)
    observation["verdict"] = served_verdict(observation)
    observation["projectedSurfaces"] = dict(projection["flipped"])
    return observation


def probe_dark(sandbox: Path, *, declarations_root: Path | str = REPO_ROOT, repo_root: Path | str = REPO_ROOT) -> Dict[str, Any]:
    """The surface as it SHIPS: absent, not merely unauthorised — and absent to all."""
    registry = shipped_registry_copy(sandbox, declarations_root=declarations_root)
    observation = observe_surface(sandbox, registry=registry, repo_root=repo_root)
    document = read_yaml(registry) or {}
    entry = ((document.get("surfaces") or {}).get(REQUIRED_SURFACE) or {}) if isinstance(document, Mapping) else {}
    return {
        **observation,
        "verdict": served_verdict(observation),
        "declaresOn": registry_declares_on(registry, REQUIRED_SURFACE),
        "declaredDefault": entry.get("default"),
    }


def probe_rolled_back(
    sandbox: Path,
    projection: Mapping[str, Any],
    *,
    repo_root: Path | str = REPO_ROOT,
) -> Dict[str, Any]:
    """A rollback takes the surface dark with no commit — at both layers.

    Two halves, because either alone is a half-truth:

    * the **ladder's** rollback (``cli rollback``) moves the flag to the declared
      rollback target and re-records live-state, so the next projection of the
      declaration is off;
    * the **runtime** withdrawal (``portal/server/surface_state.py``) takes the
      surface dark *while the declaration still says on* — one atomic file write,
      and the round trip is proven by clearing it and reading the surface again.
    """
    committed = Path(repo_root) / "infra" / "feature-flags" / "registry.yaml"
    committed_before = hashlib.sha256(committed.read_bytes()).hexdigest()
    before_stages = stages_of(live_state_doc(sandbox))

    # The declaration AS IT STOOD while the surface was served. It is captured
    # here because the re-projection below writes to the same path: the runtime
    # half of the rollback has to be provoked against a declaration that still
    # says `on`, or it would prove nothing (measured — this is what the first run
    # of this probe got wrong).
    while_promoted = sandbox / "sandbox" / "infra" / "feature-flags" / "registry-while-promoted.yaml"
    while_promoted.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(Path(projection["path"]), while_promoted)

    run = _run(
        [
            sys.executable,
            "-m",
            "infra.rollout.cli",
            "rollback",
            REQUIRED_FLAG,
            "--reason",
            "e2e_go_live_delivery_probe",
            "--actor",
            ACTOR,
            "--root",
            str(sandbox),
            "--live-state-out",
            str(sandbox_rollout(sandbox) / LIVE_STATE_REL),
            "--audit-log",
            str(sandbox_rollout(sandbox) / AUDIT_LOG_REL),
        ],
        cwd=Path(repo_root),
    )
    after_stages = stages_of(live_state_doc(sandbox))
    reprojected = project_registry(sandbox, declarations_root=projection["sourceRoot"])
    ladder = observe_surface(sandbox, registry=Path(reprojected["path"]), repo_root=repo_root)

    # -- the runtime half: the declaration is unchanged, the surface is dark ----
    from portal.server import surface_state

    overlay = Path(sandbox) / "sandbox" / "runtime" / "surface-state.json"
    declared_after = hashlib.sha256(while_promoted.read_bytes()).hexdigest()
    record = surface_state.record_rollback(
        repo_root,
        REQUIRED_SURFACE,
        reason="e2e go_live_delivery probe (issue #955)",
        actor=ACTOR,
        path=overlay,
    )
    with_overlay = observe_surface(sandbox, registry=while_promoted, repo_root=repo_root)
    still_declared = hashlib.sha256(while_promoted.read_bytes()).hexdigest()
    cleared = surface_state.clear_rollback(repo_root, REQUIRED_SURFACE, path=overlay)
    after_clear = observe_surface(sandbox, registry=while_promoted, repo_root=repo_root)

    return {
        "argv": run["argv"],
        "rc": run["rc"],
        "stdout": run["stdout"].strip(),
        "stderr": run["stderr"].strip(),
        "stageBefore": before_stages.get(REQUIRED_FLAG),
        "stageAfter": after_stages.get(REQUIRED_FLAG, "(absent — a rollback leaves no stale entry)"),
        "entryAbsentAfterRollback": REQUIRED_FLAG not in after_stages,
        "ladder": {**ladder, "verdict": served_verdict(ladder), "flipped": dict(reprojected["flipped"])},
        "runtime": {
            "overlayPath": str(record),
            "declarationUsed": str(while_promoted),
            "declarationDeclaresOn": registry_declares_on(while_promoted, REQUIRED_SURFACE),
            "statusWithOverlayEngaged": with_overlay["status"],
            "codeWithOverlayEngaged": with_overlay["code"],
            "enabledWithOverlayEngaged": with_overlay["surfaceEnabled"],
            "declarationUnchanged": declared_after == still_declared,
            "cleared": cleared,
            "statusAfterClear": after_clear["status"],
            "verdictAfterClear": served_verdict(after_clear),
        },
        "committedRegistryUnchanged": hashlib.sha256(committed.read_bytes()).hexdigest() == committed_before,
    }


# --------------------------------------------------------------------------- #
# Negative controls (no-false-green: each must genuinely block)
# --------------------------------------------------------------------------- #
def _control(control_id: str, blocked: bool, **evidence: Any) -> Dict[str, Any]:
    return {"controlId": control_id, "passed": bool(blocked), **evidence}


def subject_flag(sandbox: Path, *, required: str = REQUIRED_FLAG) -> Tuple[str, bool]:
    """The flag a control provokes against — and whether it is the required one.

    The guards these controls provoke (the promotion gate, the approver-distinct
    rule, the audit-record-existence rule) belong to the *engine* and apply to
    every flag alike. So a control provokes the required flag when the run has
    promoted it, and otherwise the first flag the run did promote to a ``full``
    target — a control that could not run at all would be a formality, and a
    control whose refusal came from "unknown flag" would prove nothing. Which flag
    was used is recorded in the evidence.
    """
    recorded = live_state_doc(sandbox)
    if required in recorded:
        return required, True
    targets = plan_targets(sandbox)
    for name in sorted(recorded):
        if targets.get(name) == RolloutStage.FULL.value:
            return name, False
    return required, False


def _load_engine(sandbox: Path) -> RolloutEngine:
    rollout = sandbox_rollout(sandbox)
    return RolloutEngine.load(
        stage_model_path=str(rollout / "stage-model.yaml"),
        rollout_state_path=str(rollout / "rollout-state.yaml"),
        audit_path=str(rollout / AUDIT_LOG_REL),
        live_state_path=str(rollout / LIVE_STATE_REL),
    )


def control_full_cannot_carry_a_policy_approval(sandbox: Path, *, repo_root: Path | str = REPO_ROOT) -> Dict[str, Any]:
    """A ``full`` promotion must carry a human ``approval_id``, never a policy.

    Two halves, because the refusals live in two places: the engine will not make
    the transition (the policy auto-approval covers ``canary``/``gradual`` only),
    and the validator refuses a live-state that *records* it anyway.
    """
    work = Path(sandbox).parent / "control-policy-full" / "sandbox"
    shutil.copytree(Path(sandbox), work, dirs_exist_ok=True)
    flag, is_required = subject_flag(work)
    engine = _load_engine(work)
    engine_message = ""
    try:
        engine.promote(
            flag,
            RolloutStage.FULL.value,
            verify_green=True,
            approval_id=None,
            actor=ACTOR,
            canary_health_ok=True,
            gradual_complete=True,
        )
    except RolloutError as exc:
        engine_message = str(exc)

    # The validator half: the same transition forged into the record.
    rollout = sandbox_rollout(work)
    record = sorted((rollout / AUDIT_SUBDIR).glob("*.md"))[0]
    forged = {
        "schema_version": 1,
        "flags": {
            flag: {
                "stage": "full",
                "from_stage": "gradual",
                "since": "2026-01-01T00:00:00Z",
                "audit_record": record.name,
                "policy": "low-risk-auto-approve",
            }
        },
    }
    engine_for_model = _load_engine(work)
    validator_errors = check_live_state(
        forged,
        list(engine_for_model.flags),
        engine_for_model.model,
        rollout_dir=str(rollout),
    )
    validator_message = next(
        (error for error in validator_errors if "human approval_id" in error), ""
    )
    return _control(
        "full-cannot-carry-a-policy-approval",
        "missing gate signal: approval_code" in engine_message
        and "is at full but has no human approval_id" in validator_message,
        subjectFlag=flag,
        subjectIsTheRequiredFlag=is_required,
        engineRefusal=engine_message,
        engineNamedTheMissingApprovalCode="missing gate signal: approval_code" in engine_message,
        validatorRefusal=validator_message,
        validatorRefused=bool(validator_message),
    )


def control_approver_must_differ_from_the_actor(
    sandbox: Path, *, repo_root: Path | str = REPO_ROOT
) -> Dict[str, Any]:
    """An approval granted by the executing actor is not an approval (AO-GR-14).

    Provoked twice: the ledger refuses the record for the executing actor, and the
    driver — which deliberately does NOT skip a self-granted record in favour of a
    later one — refuses the whole run before promoting anything.
    """
    work_root = Path(sandbox).parent / "control-self-approval"
    work = work_root / "sandbox"
    shutil.copytree(Path(sandbox), work, dirs_exist_ok=True)
    flag, is_required = subject_flag(work)
    approvals = sandbox_rollout(work) / APPROVALS_REL
    for stale in approvals.glob("*.yaml"):
        stale.unlink()
    self_granted = Approval(
        approval_id=f"approval-{flag.replace('.', '-')}-full",
        flag=flag,
        target_stage=RolloutStage.FULL,
        approver=ACTOR,
        granted_at="2026-01-01T00:00:00Z",
    )
    (approvals / f"{self_granted.approval_id}.yaml").write_text(
        yaml.safe_dump(
            {
                "approval_id": self_granted.approval_id,
                "flag": self_granted.flag,
                "target_stage": self_granted.target_stage.value,
                "approver": self_granted.approver,
                "posture": self_granted.posture,
                "granted_at": self_granted.granted_at,
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    engine = _load_engine(work)
    engine.approvals = ApprovalLedger.load_dir(str(approvals))
    ledger_message = ""
    try:
        engine.approvals.require(
            flag, RolloutStage.FULL, actor=ACTOR, approval_id=self_granted.approval_id
        )
    except RolloutError as exc:
        ledger_message = str(exc)

    recorded = live_state_doc(work)
    since = [go_live_module._parse_ts(e.get("since")) for e in recorded.values()]
    since = [moment for moment in since if moment is not None]
    advanced_now = (max(since) + DWELL_ADVANCE) if since else datetime.now(timezone.utc)
    before = stages_of(recorded)
    (rc, driver), text = _silently(lambda: drive(work, now=advanced_now))
    after = stages_of(live_state_doc(work))
    blocked_lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip().startswith(f"{flag} ->") and "not usable" in line
    ]
    return _control(
        "approver-must-differ-from-the-actor",
        ledger_message == "approver must be distinct from the executing actor"
        and rc == EXIT_NOT_OK
        and bool(blocked_lines)
        and "approver must be distinct from the executing actor" in (blocked_lines[0] if blocked_lines else "")
        and after == before,
        subjectFlag=flag,
        subjectIsTheRequiredFlag=is_required,
        ledgerRefusal=ledger_message,
        driverRc=rc,
        driverRefusal=next(
            (line for line in text.splitlines() if "refused before promoting anything" in line), ""
        ).strip(),
        driverBlockedTheStep=bool(blocked_lines),
        driverRefusalForTheFlag=(blocked_lines[0] if blocked_lines else ""),
        nothingMoved=after == before,
        stageBefore=before.get(flag),
        stageAfter=after.get(flag),
    )


def control_a_promoted_entry_needs_its_audit_record(
    sandbox: Path, *, repo_root: Path | str = REPO_ROOT
) -> Dict[str, Any]:
    """A promoted entry whose evidence file does not exist is refused, by name."""
    work = Path(sandbox).parent / "control-missing-record" / "sandbox"
    shutil.copytree(Path(sandbox), work, dirs_exist_ok=True)
    rollout = sandbox_rollout(work)
    path = rollout / LIVE_STATE_REL
    document = read_yaml(path)
    flag, is_required = subject_flag(work)
    missing_rel = f"{AUDIT_SUBDIR}/does-not-exist-955.md"
    document["flags"][flag]["audit_record"] = missing_rel
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")

    engine = _load_engine(work)
    validator_errors = check_live_state(
        read_yaml(path), list(engine.flags), engine.model, rollout_dir=str(rollout)
    )
    validator_message = next((error for error in validator_errors if missing_rel in error), "")

    (rc, _driver), text = _silently(lambda: drive(work, dry_run=True))
    return _control(
        "a-promoted-entry-needs-its-audit-record",
        f"audit_record '{missing_rel}' does not exist" in validator_message
        and rc == EXIT_CANNOT_ASSESS
        and "live-state cannot be assessed" in text
        and missing_rel in text,
        subjectFlag=flag,
        subjectIsTheRequiredFlag=is_required,
        validatorRefusal=validator_message,
        validatorRefused=bool(validator_message),
        driverRc=rc,
        driverRefusal=next(
            (line.strip() for line in text.splitlines() if "CANNOT-ASSESS" in line), ""
        ),
        expectedCannotAssess=EXIT_CANNOT_ASSESS,
    )


def control_a_phase_cannot_run_before_the_earlier_phases(
    work_dir: Path, *, declarations_root: Path | str = REPO_ROOT
) -> Dict[str, Any]:
    """Phase 7 is refused while phases 0-6 are short of their go-live stage.

    Provoked on a FRESH sandbox (nothing promoted), which is exactly the state a
    reviewer is in before the run starts — and asserted to name the blocking flag,
    not merely to return 1. ``go-live-plan.yaml`` declared ``strict-by-phase`` and
    no code read it before issue #619; this is the proof that it is enforced.
    """
    sandbox = build_sandbox(Path(work_dir) / "control-phase-order", declarations_root=declarations_root)
    before = tree_digest(sandbox)
    (rc, _driver), text = _silently(lambda: drive(sandbox, phase_tokens=(REQUIRED_PHASE,)))
    blockers = plan_phase_of(sandbox)
    blocking_flag = next(
        (flag for flag in plan_targets(sandbox) if blockers.get(flag) not in (None, REQUIRED_PHASE)),
        "",
    )
    refusal = next(
        (line.strip() for line in text.splitlines() if "strict-by-phase" in line), ""
    )
    return _control(
        "a-phase-cannot-run-before-the-earlier-phases",
        rc == EXIT_NOT_OK
        and "strict-by-phase" in refusal
        and f"phase {REQUIRED_PHASE} may not run before phase 0 is complete" in refusal
        and bool(blocking_flag)
        and blocking_flag in refusal
        and tree_digest(sandbox) == before,
        driverRc=rc,
        refusal=refusal,
        blockingFlagNamed=blocking_flag,
        namesTheBlockingFlag=bool(blocking_flag) and blocking_flag in refusal,
        namesThePhase=f"phase {REQUIRED_PHASE}" in refusal,
        wroteNothing=tree_digest(sandbox) == before,
    )


def mutation_control(
    sandbox: Path,
    projection: Mapping[str, Any],
    served: Mapping[str, Any],
    dark: Mapping[str, Any],
    *,
    repo_root: Path | str = REPO_ROOT,
) -> Dict[str, Any]:
    """The served predicate, mutated: a surface served while its flag is off.

    "Never settle for a bare 200" cuts both ways, so the predicate has to be shown
    to differentiate. This control feeds the SAME observation through a weakened
    predicate (``answers_at_all`` — any answer counts as served) and requires a
    different verdict: with it, the unpromoted surface reads "served", which is the
    false green the ``served`` stage would have shipped if it only checked that the
    route answered. The control passes only when the real predicate refuses the
    dark observation AND the weakened one accepts it — the mutation is what proves
    the check can fail.

    (The other direction — the promoted state must PASS the real predicate — is
    asserted by the ``served`` stage; together they sandwich the predicate between
    "always false" and "always true".)
    """
    off_observation = {key: value for key, value in dark.items() if key != "verdict"}
    real_verdict = served_verdict(off_observation)
    mutated_verdict = served_verdict(off_observation, predicate=answers_at_all)
    return _control(
        "a-surface-served-while-its-flag-is-off-is-not-a-pass",
        real_verdict == "FAIL" and mutated_verdict == "PASS",
        observationStatus=off_observation.get("status"),
        observationCode=off_observation.get("code"),
        surfaceEnabled=off_observation.get("surfaceEnabled"),
        realVerdict=real_verdict,
        weakenedVerdict=mutated_verdict,
        weakenedPredicate="answers_at_all (any answer counts as served)",
        promotedVerdict=served_verdict({key: value for key, value in served.items() if key != "verdict"}),
        promotedSurfaceDeclaredOn=projection.get("requiredDeclaredOn"),
    )


def negative_controls(
    sandbox: Path,
    projection: Mapping[str, Any],
    served: Mapping[str, Any],
    dark: Mapping[str, Any],
    *,
    work_dir: Optional[Path] = None,
    declarations_root: Path | str = REPO_ROOT,
    repo_root: Path | str = REPO_ROOT,
) -> Dict[str, Any]:
    """Every control, each provoked against the real guard that must block."""
    root = Path(work_dir) if work_dir is not None else Path(sandbox).parent / "controls"
    root.mkdir(parents=True, exist_ok=True)
    controls = [
        control_full_cannot_carry_a_policy_approval(sandbox, repo_root=repo_root),
        control_approver_must_differ_from_the_actor(sandbox, repo_root=repo_root),
        control_a_promoted_entry_needs_its_audit_record(sandbox, repo_root=repo_root),
        control_a_phase_cannot_run_before_the_earlier_phases(root, declarations_root=declarations_root),
        mutation_control(sandbox, projection, served, dark, repo_root=repo_root),
    ]
    return {
        "controls": controls,
        "count": len(controls),
        "passed": sum(1 for control in controls if control["passed"]),
        "allBlocked": all(control["passed"] for control in controls),
    }


# --------------------------------------------------------------------------- #
# The whole delivery
# --------------------------------------------------------------------------- #
@dataclass
class Delivery:
    """One measured delivery: the run, the projection, the reads, the controls."""

    work_dir: Path
    sandbox: Path
    repo_root: Path
    declarations_root: Path
    repoBefore: Dict[str, str] = field(default_factory=dict)
    repoAfter: Dict[str, str] = field(default_factory=dict)
    declared: Dict[str, Any] = field(default_factory=dict)
    refused: Dict[str, Any] = field(default_factory=dict)
    promoted: Dict[str, Any] = field(default_factory=dict)
    projection: Dict[str, Any] = field(default_factory=dict)
    served: Dict[str, Any] = field(default_factory=dict)
    dark: Dict[str, Any] = field(default_factory=dict)
    rolledBack: Dict[str, Any] = field(default_factory=dict)
    controls: Dict[str, Any] = field(default_factory=dict)

    @property
    def repoUnchanged(self) -> bool:
        """Nothing the run did reached the repository tree."""
        return bool(self.repoBefore) and self.repoBefore == self.repoAfter

    @property
    def comparedPaths(self) -> list[str]:
        return sorted(key for key in self.repoBefore if key != "git_status")

    def report(self) -> Dict[str, Any]:
        return {
            "requiredFlag": REQUIRED_FLAG,
            "requiredRoute": REQUIRED_ROUTE,
            "sandbox": str(self.sandbox),
            "declarationsRoot": str(self.declarations_root),
            "repoRoot": str(self.repo_root),
            "repoUnchanged": self.repoUnchanged,
            "declared": self.declared,
            "refused": self.refused,
            "promoted": self.promoted,
            "projection": {
                "flipped": self.projection.get("flipped"),
                "requiredDeclaredOn": self.projection.get("requiredDeclaredOn"),
                "digest": self.projection.get("digest"),
            },
            "served": self.served,
            "dark": {key: value for key, value in self.dark.items()},
            "rolledBack": self.rolledBack,
            "controls": self.controls,
        }


def probe_delivery(
    work_dir: Path,
    *,
    repo_root: Path | str = REPO_ROOT,
    declarations_root: Optional[Path | str] = None,
) -> Delivery:
    """Run the whole delivery probe: ladder -> live state -> a surface a client reads.

    ``declarations_root`` defaults to ``repo_root``: the subject is the
    repository's own declarations. It is a parameter only so the same probe can be
    pointed at another checkout of this same repository (a branch a sibling lane
    lands a registration on) — never at a hand-written declaration set.
    """
    repo = Path(repo_root)
    source = Path(declarations_root) if declarations_root is not None else repo
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)

    delivery = Delivery(
        work_dir=work,
        sandbox=build_sandbox(work, declarations_root=source),
        repo_root=repo,
        declarations_root=source,
        repoBefore=repo_fingerprint(repo),
    )
    delivery.declared = probe_declared(delivery.sandbox, repo_root=repo)
    delivery.refused = probe_refused(delivery.sandbox, repo_root=repo)
    delivery.promoted = probe_promoted(delivery.sandbox, repo_root=repo)
    delivery.projection = project_registry(delivery.sandbox, declarations_root=source)
    delivery.served = probe_served(delivery.sandbox, delivery.projection, repo_root=repo)
    delivery.dark = probe_dark(
        delivery.sandbox, declarations_root=source, repo_root=repo
    )
    delivery.rolledBack = probe_rolled_back(
        delivery.sandbox, delivery.projection, repo_root=repo
    )
    delivery.controls = negative_controls(
        Path(str(delivery.promoted.get("snapshotAfterPass1") or delivery.sandbox)),
        delivery.projection,
        delivery.served,
        delivery.dark,
        work_dir=work / "controls",
        declarations_root=source,
        repo_root=repo,
    )
    delivery.repoAfter = repo_fingerprint(repo)
    return delivery


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Print the delivery report as JSON (evidence a human or a driver can read)."""
    import argparse
    import tempfile

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=None, help="write the report here (default: stdout)")
    parser.add_argument("--declarations-root", default=None)
    args = parser.parse_args(list(argv) if argv is not None else None)

    work = Path(tempfile.mkdtemp(prefix="ao955-go-live-delivery."))
    delivery = probe_delivery(work, declarations_root=args.declarations_root)
    report = json.dumps(delivery.report(), indent=2, sort_keys=True, default=str)
    if args.out:
        Path(args.out).write_text(report + "\n", encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        print(report)
    return 0 if delivery.promoted.get("reachedDeclaredStage") else 1


if __name__ == "__main__":
    raise SystemExit(main())
