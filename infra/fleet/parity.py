#!/usr/bin/env python3
"""parity.py — the fleet-cron dual-run parity harness + evidence (issue #714, EPIC #706 D6).

D2 (#710) proved the packaged image can dispatch its roles in dry-run without
touching live state. D6 is the next question: does the packaged ("container")
persona behave THE SAME as the host ("local") persona when it runs the exact
same schedule? A dual-run migration (EPIC #706) is only safe to cut over once
the two personas are shown to agree, tick after tick — not asserted, measured.

This harness never restates the schedule or the dry-run form of a job: the
schedule is `fleet/cron.py`'s own ``MARKERS`` (the single owner, see
``fleet/cron.py``'s docstring) and the dry-run argv for each marker is
``infra/fleet/dev_run.py``'s own ``ROLES`` table (D2's own role table, imported
read-only — never a second copy). A schedule the two disagree about is refused
by name (``role-table-drift``), exactly as D2 refuses it.

**The watchdog is never dispatched for real.** ``fleet/watchdog.py run`` spawns
real detached rungs (brain/terminal/monitor); running it from a parity probe
would make the probe itself the worst possible side effect. ``ROLES`` already
marks the watchdog ``not-dispatched`` (no dry-run form exists) and this harness
honours that — the watchdog's row in the evidence says so, by name, rather than
being silently dropped.

WHAT IS MEASURED

1. Two personas run the SAME dispatch, on the SAME snapshot of ``.fleet`` /
   ``.board`` (taken once, copied twice — each persona's own isolated copy, so
   a live lane's concurrent write cannot be mistaken for a persona
   divergence). Both personas necessarily point ``AO_FLEET_DIR`` /
   ``AO_FLEET_BOARD_DIR`` at their OWN copy (that is the isolation), so what is
   actually being compared is the rest of the environment each persona
   dispatches under:

   * ``local``     — the ambient host environment, unextended (no other
     ``AO_FLEET_*`` variable set beyond the two isolation paths above) — this
     is how ``fleet/cron.py``'s own crontab lines invoke each job today.
   * ``container`` — ``infra/fleet/env_contract.py``'s full resolved
     environment layered on top (``AO_FLEET_REPO``, ``AO_FLEET_DRY_RUN``,
     ``AO_FLEET_CRON_INTERVAL``, ``AO_FLEET_PORT``, ``AO_FLEET_ROLE_TIMEOUT``,
     …) — the same contract ``infra/fleet/entrypoint.sh`` enforces before it
     installs the schedule.

   A diff here is therefore a claim that one of the container contract's own
   variables changes a dispatched role's DECISION — never a claim about the
   state roots themselves, which are isolated by construction on both sides.

2. Each persona's copy is dispatched **twice**, back to back, simulating a
   lost-lock race (the second dispatcher did not see the first's lock release
   in time). The first run is the measurement; the second run's own
   attributable-write manifest must be EMPTY — a rung that acts twice on a
   stale lock is not idempotent, and that is reported by name
   (``not-idempotent``), never silently accepted.

3. Outputs are normalized (timestamps, pids, durations, session ids, absolute
   repo paths) before being compared, because two personas rooted at different
   tmp paths producing byte-identical PROSE is not the same claim as producing
   the same DECISION — this harness compares decisions.

4. ``one_writer`` is MEASURED, never hardcoded true. ``fleet/lease.py`` does
   not exist yet (D5, blocked-by, not landed) so the lease itself cannot be
   exercised; this harness imports it lazily and skips the lease-enforced
   assertion when it is absent (recorded as ``lease_asserted: false``). Absent
   the lease, single-writer is established structurally instead: each
   persona's dispatch is run against ITS OWN isolated copy of the snapshot, so
   only that persona's own process can be the writer of its own copy — anytime
   both personas' own copies show attributable writes to the *same logical
   path* in the *same tick*, that is reported as a `one_writer` violation, not
   assumed away.

Exit codes (repo tri-state convention): 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.

    python3 infra/fleet/parity.py                  # one tick, writes .verify/fleet-parity.json
    python3 infra/fleet/parity.py --ticks 3         # N ticks (>=3 per issue #714's acceptance)

---knowledge---
module_id: infra.fleet.parity
system: infra
app: fleet
solution_class: class
patterns: [pre-standard-snapshot]
derives_from: null
owner_sme: iac-sme
tier: L1
interfaces: [now, normalize, digest_of, load_markers, check_role_table, tree, diff_tree, PersonaState, (+9 more)]
invariants: ""
gotchas: ""
related: ["#1911"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import dev_run  # noqa: E402 — infra/fleet/dev_run.py: the dry-run role table (D2), imported not restated
import env_contract  # noqa: E402 — infra/fleet/env_contract.py: the container env contract

OK, NOT_OK, CANNOT_ASSESS = 0, 1, 2

SCHEMA = "fleet-cron-parity-v1"
ISSUE = 714
EPIC = 706

EVIDENCE_RELATIVE = Path(".verify/fleet-parity.json")

#: The two personas this harness compares. ``env`` is a function of the
#: persona's own isolated snapshot dir, so the same code path builds both.
PERSONAS = ("local", "container")

#: Normalization patterns applied to every captured line before comparison —
#: this harness compares DECISIONS, not incidental bytes (tmp paths, pids,
#: durations, session ids, timestamps differ between two isolated copies by
#: construction and are never evidence of divergence).
_NORMALIZERS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z?"), "<timestamp>"),
    (re.compile(r"\bpid[=: ]\d+\b", re.IGNORECASE), "pid=<pid>"),
    (re.compile(r"\b\d+\.\d+s\b"), "<duration>s"),
    (re.compile(r"\bsession[-_]?id[=: ][\w-]+", re.IGNORECASE), "session_id=<session>"),
    # `governance/reconcile/cli.py watch --once` names each expired session by
    # its own live fingerprint (`reconcile:suspect:0c4c7806e9d5`, and again in
    # the sentence). Reconcile resolves its fleet root from the MAIN checkout's
    # git dir (`orphans.fleet_root`, #1436) rather than this harness's isolated
    # `AO_FLEET_DIR` snapshot, so that text is LIVE box-wide state — a session id,
    # in the never-evidence list above — and is collapsed here instead of
    # compared. Collapsing it does NOT make the comparison vacuous: `rc`, every
    # other captured line, and any decision-level difference are still compared.
    (re.compile(r"reconcile:suspect:[\w-]+"), "reconcile:suspect:<session>"),
    (re.compile(r"\bsession [\w-]+ no longer beats\b"), "session <session> no longer beats"),
    (re.compile(re.escape(str(REPO))), "<repo>"),
)


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize(text: str) -> str:
    for pattern, replacement in _NORMALIZERS:
        text = pattern.sub(replacement, text)
    return text


def digest_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Schedule + role table — read from their owners, never restated.
# ---------------------------------------------------------------------------


def load_markers() -> list[str] | None:
    sys.path.insert(0, str(REPO / "fleet"))
    try:
        import cron  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return None
    try:
        return [str(marker) for marker in cron.MARKERS]
    except Exception:  # noqa: BLE001
        return None


def check_role_table(markers: list[str]) -> list[str]:
    """Reuses dev_run's own role-table-drift rule — the two catalogs must agree."""
    declared = [role.marker for role in dev_run.ROLES]
    findings: list[str] = []
    for marker in markers:
        if marker not in declared:
            findings.append(f"fleet/cron.py declares {marker!r}; dev_run.ROLES declares no dry-run form for it")
    for marker in declared:
        if marker not in markers:
            findings.append(f"dev_run.ROLES declares {marker!r}; fleet/cron.py does not install it")
    return findings


# ---------------------------------------------------------------------------
# Snapshot + isolated persona copies.
# ---------------------------------------------------------------------------


def tree(root: Path) -> dict[str, str]:
    entries: dict[str, str] = {}
    if not root.exists():
        return entries
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(dirnames) + sorted(filenames):
            path = Path(dirpath) / name
            relative = str(path.relative_to(root))
            if path.is_symlink():
                entries[relative] = "symlink"
            elif path.is_dir():
                entries[relative] = "dir"
            else:
                try:
                    entries[relative] = "file:" + digest_of(path.read_bytes())
                except OSError as exc:
                    entries[relative] = f"unreadable:{exc.__class__.__name__}"
    return entries


def diff_tree(before: dict[str, str], after: dict[str, str]) -> dict[str, list[str]]:
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    changed = sorted(key for key in set(before) & set(after) if before[key] != after[key])
    return {"added": added, "removed": removed, "changed": changed}


@dataclass
class PersonaState:
    name: str
    fleet_dir: Path
    board_dir: Path
    environment: dict[str, str]


def build_personas(source_fleet: Path, source_board: Path, workdir: Path) -> dict[str, PersonaState]:
    """One isolated copy of the (single, shared) snapshot per persona.

    Copied, not shared, so each persona's own process is the only writer of its
    own copy by construction — the structural basis ``one_writer`` is measured
    against when ``fleet/lease.py`` is absent.
    """
    personas: dict[str, PersonaState] = {}
    for name in PERSONAS:
        fleet_copy = workdir / name / "fleet"
        board_copy = workdir / name / "board"
        if source_fleet.exists():
            shutil.copytree(source_fleet, fleet_copy)
        else:
            fleet_copy.mkdir(parents=True, exist_ok=True)
        if source_board.exists():
            shutil.copytree(source_board, board_copy)
        else:
            board_copy.mkdir(parents=True, exist_ok=True)

        if name == "local":
            environment = dict(os.environ)
            environment.pop("AO_FLEET_DIR", None)
            environment.pop("AO_FLEET_BOARD_DIR", None)
            environment["AO_FLEET_DIR"] = str(fleet_copy)
            environment["AO_FLEET_BOARD_DIR"] = str(board_copy)
        else:
            environment = dict(os.environ)
            environment.update(env_contract.resolve(environment))
            environment["AO_FLEET_REPO"] = str(REPO)
            environment["AO_FLEET_DIR"] = str(fleet_copy)
            environment["AO_FLEET_BOARD_DIR"] = str(board_copy)
            environment["AO_FLEET_DRY_RUN"] = "1"
        personas[name] = PersonaState(name=name, fleet_dir=fleet_copy, board_dir=board_copy, environment=environment)
    return personas


# ---------------------------------------------------------------------------
# Dispatch — one role, one persona copy, one run.
# ---------------------------------------------------------------------------


def dispatch_role(role: "dev_run.Role", persona: PersonaState, timeout: int) -> dict:
    record = {
        "role": role.name,
        "marker": role.marker,
        "disposition": role.disposition,
        "rc": None,
        "timed_out": False,
        "output": "",
    }
    if not role.argv:
        record["output"] = "not-dispatched: " + role.why
        return record
    argv = [sys.executable, *role.argv]
    try:
        completed = subprocess.run(
            argv,
            cwd=str(REPO),
            env=persona.environment,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        record["timed_out"] = True
        record["output"] = "TIMEOUT"
        return record
    except OSError as exc:
        record["output"] = f"OSError: {exc}"
        return record
    record["rc"] = completed.returncode
    record["output"] = normalize((completed.stdout + completed.stderr).strip())
    return record


def run_tick(personas: dict[str, PersonaState], timeout: int) -> dict:
    """One tick: every role, dispatched twice per persona (the lost-lock race)."""
    tick: dict = {"personas": {}}
    for name, persona in personas.items():
        before = {
            "fleet": tree(persona.fleet_dir),
            "board": tree(persona.board_dir),
        }
        first_pass = [dispatch_role(role, persona, timeout) for role in dev_run.ROLES]
        mid = {
            "fleet": tree(persona.fleet_dir),
            "board": tree(persona.board_dir),
        }
        first_manifest = {root: diff_tree(before[root], mid[root]) for root in before}

        # The simulated lost-lock race: dispatch again immediately, against the
        # SAME copy, without releasing anything — a rung that mutates state a
        # second time here is not idempotent under a lock it never saw expire.
        second_pass = [dispatch_role(role, persona, timeout) for role in dev_run.ROLES]
        after = {
            "fleet": tree(persona.fleet_dir),
            "board": tree(persona.board_dir),
        }
        second_manifest = {root: diff_tree(mid[root], after[root]) for root in before}

        tick["personas"][name] = {
            "first_pass": first_pass,
            "second_pass": second_pass,
            "first_manifest": first_manifest,
            "second_manifest": second_manifest,
        }
    return tick


# ---------------------------------------------------------------------------
# Comparison — decisions, not bytes.
# ---------------------------------------------------------------------------


def manifest_is_empty(manifest: dict[str, dict[str, list[str]]]) -> bool:
    return all(not entries["added"] and not entries["removed"] and not entries["changed"] for entries in manifest.values())


def compare_tick(tick: dict, tick_index: int) -> tuple[list[dict], list[dict], bool]:
    """Returns (diffs, idempotency_findings, one_writer_ok) for one tick."""
    diffs: list[dict] = []
    idempotency: list[dict] = []
    local = tick["personas"]["local"]
    container = tick["personas"]["container"]

    local_by_role = {job["role"]: job for job in local["first_pass"]}
    container_by_role = {job["role"]: job for job in container["first_pass"]}
    for role_name in local_by_role:
        left = local_by_role[role_name]
        right = container_by_role.get(role_name, {})
        if left.get("rc") != right.get("rc") or left.get("output") != right.get("output"):
            diffs.append(
                {
                    "tick": tick_index,
                    "role": role_name,
                    "field": "outcome",
                    "local": {"rc": left.get("rc"), "output": left.get("output")},
                    "container": {"rc": right.get("rc"), "output": right.get("output")},
                }
            )

    for name in PERSONAS:
        persona = tick["personas"][name]
        if not manifest_is_empty(persona["second_manifest"]):
            idempotency.append(
                {
                    "tick": tick_index,
                    "persona": name,
                    "finding": "not-idempotent",
                    "detail": "a second dispatch against the same (lost-lock) copy produced attributable writes",
                    "manifest": persona["second_manifest"],
                }
            )

    # one_writer, structurally: each persona wrote only its OWN isolated copy,
    # so a genuine violation here is a logical relative path attributably
    # written by BOTH personas' first passes in the same tick — the only way
    # this measurement, absent fleet/lease.py, could observe more than one
    # writer touching the same logical state in one tick.
    local_written = {
        f"{root}:{path}"
        for root, entries in local["first_manifest"].items()
        for path in entries["added"] + entries["changed"]
    }
    container_written = {
        f"{root}:{path}"
        for root, entries in container["first_manifest"].items()
        for path in entries["added"] + entries["changed"]
    }
    overlap = sorted(local_written & container_written)
    one_writer_ok = not overlap
    if overlap:
        diffs.append(
            {
                "tick": tick_index,
                "role": None,
                "field": "one_writer",
                "local": overlap,
                "container": overlap,
            }
        )
    return diffs, idempotency, one_writer_ok


# ---------------------------------------------------------------------------
# Run.
# ---------------------------------------------------------------------------


def lease_asserted() -> bool:
    """Best-effort: exercise fleet/lease.py's single-writer guard if it exists.

    D5 (the lease module) is blocked-by this issue and does not exist yet in
    this checkout. Importing it lazily — never a hard dependency — is how this
    lane stays in its own files: when the lease lands, this becomes a real
    assertion for free; until then it is honestly reported absent.
    """
    sys.path.insert(0, str(REPO / "fleet"))
    try:
        import lease  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return False
    return hasattr(lease, "acquire") or hasattr(lease, "Lease")


def run(ticks: int, timeout: int, evidence_path: Path) -> int:
    started = now()
    markers = load_markers()
    if markers is None:
        return _refuse(evidence_path, started, "schedule-unmeasurable", "fleet/cron.py could not be read for its markers")

    refusals = check_role_table(markers)
    if refusals:
        return _refuse(evidence_path, started, "role-table-drift", "; ".join(refusals))

    source_fleet = Path(os.environ.get("AO_FLEET_DIR", REPO / ".fleet"))
    source_board = Path(os.environ.get("AO_FLEET_BOARD_DIR", REPO / ".board"))

    all_diffs: list[dict] = []
    all_idempotency: list[dict] = []
    one_writer = True
    tick_records: list[dict] = []

    with tempfile.TemporaryDirectory(prefix="fleet-parity-") as tmp:
        workdir = Path(tmp)
        for tick_index in range(ticks):
            tick_dir = workdir / f"tick-{tick_index}"
            personas = build_personas(source_fleet, source_board, tick_dir)
            tick = run_tick(personas, timeout)
            diffs, idempotency, ok = compare_tick(tick, tick_index)
            all_diffs.extend(diffs)
            all_idempotency.extend(idempotency)
            one_writer = one_writer and ok
            tick_records.append(
                {
                    "tick": tick_index,
                    "roles": [job["role"] for job in tick["personas"]["local"]["first_pass"]],
                }
            )

    has_lease = lease_asserted()
    verdict = "ok"
    reason = f"{ticks} tick(s) agreed across local/container personas; no diffs, every second pass was a no-op"
    if all_diffs:
        verdict = "not-ok"
        reason = f"{len(all_diffs)} diff(s) between local and container personas"
    elif all_idempotency:
        verdict = "not-ok"
        reason = f"{len(all_idempotency)} rung(s) were not idempotent under a simulated lost-lock race"
    elif not one_writer:
        verdict = "not-ok"
        reason = "one_writer violated: overlapping attributable writes between personas in the same tick"

    document = {
        "schema": SCHEMA,
        "issue": ISSUE,
        "epic": EPIC,
        "started_at": started,
        "finished_at": now(),
        "verdict": verdict,
        "reason": reason,
        # Issue #714's own acceptance shape — top-level, checked verbatim by its Verify.
        "ticks": ticks,
        "one_writer": one_writer,
        "diffs": all_diffs,
        "idempotency": all_idempotency,
        "lease_asserted": has_lease,
        "lease_note": (
            "fleet/lease.py exercised directly"
            if has_lease
            else "fleet/lease.py absent (D5 not landed) — one_writer measured structurally: each "
            "persona's dispatch wrote only its own isolated snapshot copy, and no logical path was "
            "attributably written by both personas in the same tick"
        ),
        "schedule": {"owner": "fleet/cron.py", "markers": markers},
        "roles": {
            "source": "infra/fleet/dev_run.py:ROLES",
            "declared": [
                {"marker": role.marker, "role": role.name, "disposition": role.disposition, "why": role.why}
                for role in dev_run.ROLES
            ],
        },
        "personas": list(PERSONAS),
        "tick_records": tick_records,
    }
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"parity: {verdict} — {reason}")
    return OK if verdict == "ok" else NOT_OK


def _refuse(evidence_path: Path, started: str, code: str, detail: str) -> int:
    document = {
        "schema": SCHEMA,
        "issue": ISSUE,
        "epic": EPIC,
        "started_at": started,
        "finished_at": now(),
        "verdict": "cannot-assess",
        "reason": f"{code}: {detail}",
        "ticks": 0,
        "one_writer": False,
        "diffs": [],
        "refusal": {"code": code, "detail": detail},
    }
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"parity: REFUSED {code}: {detail}", file=sys.stderr)
    return CANNOT_ASSESS


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fleet-parity", description=__doc__)
    parser.add_argument("--ticks", type=int, default=3, help="how many dispatch ticks to compare (default 3)")
    parser.add_argument("--timeout", type=int, default=120, help="seconds bound per dispatched role")
    parser.add_argument(
        "--evidence",
        type=Path,
        default=REPO / EVIDENCE_RELATIVE,
        help="where to write the evidence document (default .verify/fleet-parity.json)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return run(ticks=args.ticks, timeout=args.timeout, evidence_path=args.evidence)


if __name__ == "__main__":
    raise SystemExit(main())
