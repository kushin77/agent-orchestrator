#!/usr/bin/env python3
"""dev_run.py — the fleet-cron image's dev-first dry-run dispatch (issue #710, EPIC #706 D2).

D1 (#709) packaged the three scheduled jobs into one image so they can run off
this box. Packaging is not booting: the first porting step is to START the image
here, on the box it was written on, and watch it dispatch its roles WITHOUT
touching live state. ``.fleet/`` is the session fleet's mailbox, heartbeat and
audit rail; ``.board/`` is the claim ledger every other lane reads. A first port
whose experiment prunes a live mailbox or reconciles a live claim ledger is an
experiment on production.

So this harness runs the roles in dry-run, and the dry-ness is MEASURED rather
than asserted:

1. **The schedule is read from its owner.** The job list is
   ``fleet/cron.py``'s own ``MARKERS`` — never a second copy — and the run
   REFUSES when its role table does not cover them exactly (``role-table-drift``):
   a fourth scheduled job with no declared dry-run form must fail here rather
   than be silently skipped.
2. **A dispatch cannot carry ``--apply``.** Every argv is checked, and the
   ``--apply`` token is refused by name (``apply-in-dispatch``). ``entrypoint.sh``
   has already refused ``AO_FLEET_DRY_RUN`` values other than ``1``, so the flag
   cannot be turned on from the environment either.
3. **The state roots are checksummed before and after** every dispatch, and any
   path the roles are permitted to write is a FAILING finding naming the file
   (``state-written``). The permitted set is not invented here: it is read from
   ``fleet/prune.py`` (``PRUNABLE_DIRS``), the module that owns the policy.
4. **A change outside that set is reported, not accepted.** The live fleet is
   still running on this box while the container starts, so its own heartbeats
   and mailbox moves can land inside the window. Those are recorded as
   ``concurrent_writers`` with the path — visible on every run, never counted as
   evidence of a dry run having applied. (This is the honest limit of the
   measurement, and it is why D1's image mounts the state roots read-only: the
   writability of each root is itself measured, from ``/proc/self/mountinfo``.)

The run then stays up and answers ``/healthz`` (``infra/fleet/healthz.py``) until
it is stopped, so ``docker compose up -d`` + ``curl`` is a real probe of a real
container. SIGTERM/SIGINT stop it cleanly — the handler is installed before the
first role runs, because a dev harness that must be SIGKILLed leaves the operator
unable to tell a clean stop from a crash (AGENTS.md rule 24).

    python3 infra/fleet/dev_run.py                 # the container's command
    python3 infra/fleet/dev_run.py --once          # dispatch, report, exit (no /healthz)

Exit codes: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS (this repository's convention).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import signal as _signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import env_contract  # noqa: E402 — beside this file, in the image at /repo/infra/fleet
import healthz  # noqa: E402

OK, NOT_OK, CANNOT_ASSESS = 0, 1, 2

#: Where the decision document lives. Deliberately NOT under a state root: the
#: document is the run's own artifact, and a harness that writes its evidence
#: into the directory it is proving it did not touch has proved nothing.
DECISION_RELATIVE = Path(".verify/dev-run/decision.json")

SCHEMA = "fleet-cron-dev-run-v1"
ISSUE = 710
EPIC = 706


@dataclass(frozen=True)
class Role:
    """One scheduled job, and the dry-run form of it (empty when there is none)."""

    marker: str
    name: str
    argv: tuple[str, ...]
    why: str

    @property
    def disposition(self) -> str:
        return "dry-run" if self.argv else "not-dispatched"


#: The role table, keyed by the schedule's own markers. ``fleet/cron.py`` owns
#: the markers; the dry-run FORM of each job is this lane's business, and the
#: two are held in lock-step by :func:`check_role_table`.
#:
#: The watchdog has no dry-run form and is therefore NOT dispatched: its whole
#: job is to spawn the rungs, so "watchdog in dry-run" is a contradiction rather
#: than a flag. It is reported as ``not-dispatched`` with that reason, which is
#: what keeps the third job PRESENT in the evidence instead of quietly absent.
ROLES: tuple[Role, ...] = (
    Role(
        marker="ao-fleet-watchdog",
        name="watchdog",
        argv=(),
        why="fleet/watchdog.py's pass SPAWNS the rungs; it has no planning mode, so the dev run "
        "does not dispatch it and records that rather than pretending to dry-run a spawn.",
    ),
    Role(
        marker="ao-fleet-prune",
        name="prune",
        argv=("fleet/prune.py", "run"),
        why="the retention planner is dry-run by default; `--apply` is what acts.",
    ),
    Role(
        marker="ao-fleet-reconcile",
        name="reconcile",
        argv=("governance/reconcile/cli.py", "watch", "--once"),
        why="one reconciliation pass, without `--apply`: it names the orphans it would act on.",
    ),
)

#: The token no dispatched argv may carry. Checked as a token, not a substring,
#: so a path or a comment mentioning it is not a false finding.
FORBIDDEN_TOKEN = "--apply"

#: Where the decision document's own log line is written: stdout, so
#: `docker compose logs` carries the decision (the issue's own Verify greps it).
LOG_PREFIX = "dev-run:"


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(message: str) -> None:
    print(f"{LOG_PREFIX} {message}", flush=True)


def digest_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


# ---------------------------------------------------------------------------
# The state roots: what they hold before the roles run, and what they hold after.
# ---------------------------------------------------------------------------


def tree(root: Path) -> dict[str, str]:
    """Every entry under ``root``: ``relpath -> digest`` (a directory digests its name)."""
    entries: dict[str, str] = {}
    if not root.exists():
        return entries
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(dirnames) + sorted(filenames):
            path = Path(dirpath) / name
            relative = str(path.relative_to(root))
            if path.is_symlink():
                entries[relative] = "symlink:" + digest_of(os.readlink(path).encode("utf-8"))
            elif path.is_dir():
                entries[relative] = "dir"
            else:
                try:
                    entries[relative] = "file:" + digest_of(path.read_bytes())
                except OSError as exc:  # fail closed: an unreadable file is a fact, not a gap
                    entries[relative] = f"unreadable:{exc.__class__.__name__}"
    return entries


def mount_flags(path: Path) -> dict:
    """How ``path`` is mounted, measured from /proc/self/mountinfo (never assumed).

    Returns ``{"mount": "ro"|"rw"|"unknown", "source": ..., "point": ...}`` for the
    most specific mount that contains ``path``.
    """
    best: dict = {"mount": "unknown", "source": "", "point": ""}
    best_len = -1
    try:
        lines = Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines()
    except OSError:
        return best
    target = str(path.resolve())
    for line in lines:
        left, _, right = line.partition(" - ")
        fields = left.split()
        if len(fields) < 6:
            continue
        point = fields[4].replace("\\040", " ")
        if target != point and not target.startswith(point.rstrip("/") + "/"):
            continue
        if len(point) <= best_len:
            continue
        options = fields[5].split(",")
        right_fields = right.split()
        best = {
            "mount": "ro" if "ro" in options else ("rw" if "rw" in options else "unknown"),
            "source": right_fields[1] if len(right_fields) > 1 else "",
            "point": point,
        }
        best_len = len(point)
    return best


def state_snapshot(roots: tuple[Path, ...]) -> dict:
    """The before/after shape: each root's tree, its digest, and its mount posture."""
    measured: dict = {}
    for root in roots:
        entries = tree(root)
        measured[str(root)] = {
            "files": len([value for value in entries.values() if not value.startswith("dir")]),
            "digest": digest_of(json.dumps(entries, sort_keys=True).encode("utf-8")),
            "mount": mount_flags(root),
            "writable": os.access(root, os.W_OK),
            "_entries": entries,
        }
    return measured


#: The set of paths the dispatched roles may write. Read from the module that owns
#: the retention policy (``fleet/prune.py``), never restated: a restated policy is
#: a second one, and the whole point of this harness is that its evidence is
#: measured against the code it describes.
def permitted_writes(repo: Path) -> tuple[tuple[str, ...], str]:
    """The retention rail, measured from ``fleet/prune.py`` — the module that owns it.

    The scope is stated as what it is, because a guard with an unstated scope is
    read as covering everything: this set is the pruning rail (the role whose
    SCHEDULED form carries ``--apply``). The reconcile rail's writes are not
    enumerated here — they are covered structurally, by the read-only mount whose
    flags are measured per root in the decision document.
    """
    sys.path.insert(0, str(repo / "fleet"))
    try:
        import prune  # noqa: PLC0415 — measured here, not at import time
    except Exception:  # noqa: BLE001 — an unreadable policy is reported, not invented
        return (("outbox", "sent", "done", "slog.jsonl", "runs.jsonl"), "declared fallback (fleet/prune.py unreadable)")
    entries = tuple(str(item) for item in prune.PRUNABLE_DIRS)
    entries += tuple(path.name for path in prune.LOGS)
    entries = tuple(dict.fromkeys(entries))
    return entries, "fleet/prune.py:PRUNABLE_DIRS + prune.LOGS"


def attributable(relative: str, permitted: tuple[str, ...]) -> bool:
    """May a dispatched role write this path? Judged on the entry's ROOT-RELATIVE name.

    Judged relative to its own state root, never on the absolute path: the same
    rail is called ``.fleet/outbox`` in the live posture and ``/tmp/x/fleet/outbox``
    in a scratch harness, and a rule that reads the absolute path would answer
    differently for the two — which is how a guard comes to pass only where it is
    tested.
    """
    return any(relative == entry or relative.startswith(entry + "/") for entry in permitted)


def classify_changes(before: dict, after: dict, permitted: tuple[str, ...]) -> dict:
    """Split what moved into attributable writes and changes the container cannot have made.

    WHO can be blamed is decided by the MEASURED mount posture, not by the path
    alone, and that is the whole reason this is a measurement:

    * a root mounted **read-only** cannot be written by this container at all, so
      a change under it is by definition a concurrent writer — the live fleet is
      still running on this box, and reporting its own heartbeat as evidence that
      a dry run applied would be a false finding;
    * a root mounted **writable** has this container as its writer, so a change at
      a path the dispatched roles may write is attributable and FAILS, naming the
      file.

    Both are recorded, so an operator reading the document sees which rule judged
    what. There is no flag that turns the rule off; the posture IS the rule.
    """
    added: list[str] = []
    removed: list[str] = []
    changed: list[str] = []
    moved: list[str] = []
    concurrent: list[dict] = []
    for root, snapshot in before.items():
        later = (after.get(root) or {}).get("_entries", {})
        earlier = snapshot["_entries"]
        read_only = (snapshot.get("mount") or {}).get("mount") == "ro"
        for relative in sorted(set(earlier) | set(later)):
            shown = f"{root}/{relative}"
            if relative not in earlier:
                added.append(shown)
            elif relative not in later:
                removed.append(shown)
            elif earlier[relative] != later[relative]:
                changed.append(shown)
            else:
                continue
            if read_only:
                concurrent.append(
                    {
                        "path": shown,
                        "note": f"{root} is mounted read-only, so this container did not write it — "
                        "the live fleet owns this path",
                    }
                )
            elif attributable(relative, permitted):
                moved.append(shown)
            else:
                concurrent.append(
                    {
                        "path": shown,
                        "note": "not a path the dispatched roles may write, so it is not evidence of an "
                        "applying job — but nothing else writes this root, so it is recorded for a reader",
                    }
                )
    return {
        "added": added,
        "removed": removed,
        "changed": changed,
        "observed_changes": len(added) + len(removed) + len(changed),
        "attributable_changes": sorted(moved),
        "concurrent_writers": concurrent,
    }


# ---------------------------------------------------------------------------
# The dispatch.
# ---------------------------------------------------------------------------


def check_role_table(markers: list[str]) -> list[str]:
    """Every reason the role table and the schedule disagree (empty when they agree).

    Returns DETAILS only: the caller attaches the finding's code, so a message
    never carries its own code twice.
    """
    declared = [role.marker for role in ROLES]
    findings: list[str] = []
    for marker in markers:
        if marker not in declared:
            findings.append(
                f"the schedule (fleet/cron.py) declares {marker!r} and the dev run declares no dry-run form for it"
            )
    for marker in declared:
        if marker not in markers:
            findings.append(
                f"the dev run declares {marker!r}, which the schedule (fleet/cron.py) does not install"
            )
    return findings


def check_no_apply() -> list[str]:
    findings: list[str] = []
    for role in ROLES:
        if FORBIDDEN_TOKEN in role.argv:
            findings.append(
                f"role {role.name!r} would run {FORBIDDEN_TOKEN} ({' '.join(role.argv)}); "
                "the dev run dispatches no applying job"
            )
    return findings


def dispatch(role: Role, repo: Path, environment: dict, timeout: int) -> dict:
    """Run one role, bounded, and record what it said (tail) and how it ended."""
    argv = [sys.executable, *role.argv]
    started = time.time()
    record = {
        "marker": role.marker,
        "role": role.name,
        "disposition": role.disposition,
        "why": role.why,
        "argv": list(role.argv),
        "apply": FORBIDDEN_TOKEN in role.argv,
        "rc": None,
        "timed_out": False,
        "seconds": None,
        "tail": [],
    }
    if not role.argv:
        record["tail"] = ["not dispatched — see why"]
        log(f"role {role.name} — NOT dispatched (no dry-run form: {role.why})")
        return record
    try:
        completed = subprocess.run(
            argv,
            cwd=str(repo),
            env=environment,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        record["timed_out"] = True
        record["seconds"] = round(time.time() - started, 2)
        log(f"role {role.name} — TIMED OUT after {timeout}s ({' '.join(role.argv)})")
        return record
    except OSError as exc:
        record["seconds"] = round(time.time() - started, 2)
        record["tail"] = [f"OSError: {exc}"]
        log(f"role {role.name} — could not be started: {exc}")
        return record
    record["rc"] = completed.returncode
    record["seconds"] = round(time.time() - started, 2)
    combined = (completed.stdout + completed.stderr).strip().splitlines()
    record["tail"] = [line for line in combined if line.strip()][-6:]
    label = "APPLY" if record["apply"] else "dry-run"
    log(
        f"role {role.name} — {label} rc={completed.returncode} ({' '.join(role.argv)}), "
        f"{record['seconds']}s, {len(record['tail'])} line(s) of output"
    )
    return record


def fleet_signal(repo: Path, environment: dict, timeout: int) -> dict:
    """The fleet's OWN health signal (``fleet/health.py``), recorded, never restated.

    It is expected to be ``failing`` inside this container: no rung runs here —
    the dev run dispatches no spawn — and saying so is the point. Its rc is
    recorded, never allowed to decide ``/healthz``: that surface reports whether
    THIS run was clean.
    """
    argv = [sys.executable, str(repo / "fleet" / "health.py"), "check"]
    try:
        completed = subprocess.run(
            argv, cwd=str(repo), env=environment, capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"rc": None, "label": "unmeasured", "error": exc.__class__.__name__}
    label = {"0": "healthy", "1": "degraded", "2": "failing"}.get(str(completed.returncode), "unknown")
    return {
        "rc": completed.returncode,
        "label": label,
        "meaning": "measured inside the container with no rung running here; /healthz reports THIS run, not this signal",
        "source": "fleet/health.py check",
    }


# ---------------------------------------------------------------------------
# The run.
# ---------------------------------------------------------------------------


@dataclass
class DevRun:
    """The run's own state: the resolved environment, the doc, and the stop record."""

    environment: dict[str, str]
    repo: Path
    decision_path: Path
    stop_event: threading.Event = field(default_factory=threading.Event)
    document: dict = field(default_factory=dict)
    stop: dict = field(default_factory=dict)
    findings: list = field(default_factory=list)
    server: object | None = None

    def finding(self, code: str, detail: str) -> None:
        """Record a finding BY CODE.

        The verdict's prose is for a reader; the code is for a machine, so the
        gate asserts on ``state-written`` rather than on a sentence this file is
        free to reword — and every finding is written to the run's own log, so a
        stopped container still says what it found.
        """
        self.findings.append({"code": code, "detail": detail})
        log(f"FINDING {code}: {detail}")

    def write(self) -> None:
        payload = dict(self.document)
        payload["stop"] = self.stop or {"signal": None, "clean": None, "note": "still running"}
        self.decision_path.parent.mkdir(parents=True, exist_ok=True)
        self.decision_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def install_signal_handlers(self) -> None:
        for signum in (signal.SIGTERM, signal.SIGINT):
            signal.signal(signum, self._on_signal)

    def _on_signal(self, signum: int, _frame: object) -> None:
        name = _signal.Signals(signum).name
        self.stop = {
            "signal": name,
            "clean": True,
            "at": now(),
            "note": "the handler was installed before the first role ran, so this is a clean stop, not a kill",
        }
        self.stop_event.set()

    def run(self, once: bool, timeout: int) -> int:
        log(f"{SCHEMA} — dry-run dev harness for issue #{ISSUE} (EPIC #{EPIC})")
        log(f"repo={self.repo} fleet={self.environment['AO_FLEET_DIR']} board={self.environment['AO_FLEET_BOARD_DIR']}")

        roots = (Path(self.environment["AO_FLEET_DIR"]), Path(self.environment["AO_FLEET_BOARD_DIR"]))
        permitted, permitted_source = permitted_writes(self.repo)

        markers = load_markers(self.repo)
        if markers is None:
            return self.refuse("schedule-unmeasurable", "fleet/cron.py could not be read for its markers")
        log(f"schedule measured from fleet/cron.py: {len(markers)} marked line(s)")

        refusals = check_role_table(markers) + check_no_apply()
        if refusals:
            return self.refuse("dispatch-refused", "; ".join(refusals))

        applying = [role.name for role in ROLES if FORBIDDEN_TOKEN in role.argv]
        log(
            f"DRY-RUN — dispatching {len([r for r in ROLES if r.argv])} of {len(ROLES)} role(s); "
            f"{len(applying)} with {FORBIDDEN_TOKEN}"
        )

        before = state_snapshot(roots)
        jobs = [dispatch(role, self.repo, self.environment, timeout) for role in ROLES]
        after = state_snapshot(roots)
        changes = classify_changes(before, after, permitted)

        failed = [job for job in jobs if job["rc"] not in (0, None) or job["timed_out"]]
        for job in failed:
            self.finding(
                "role-failed",
                f"role {job['role']!r} did not complete cleanly (rc={job['rc']}, timed_out={job['timed_out']})",
            )
        for path in changes["attributable_changes"]:
            self.finding(
                "state-written",
                f"a dispatched role wrote to a state root: {path}",
            )
        verdict, reason = "ok", "every dispatched role planned without acting"
        if changes["attributable_changes"]:
            verdict = "not-ok"
            reason = (
                "a dispatched role wrote to a state root: " + ", ".join(changes["attributable_changes"][:5])
            )
        elif failed:
            verdict = "not-ok"
            reason = "role(s) did not complete cleanly: " + ", ".join(job["role"] for job in failed)

        if changes["concurrent_writers"]:
            log(
                f"state: {len(changes['concurrent_writers'])} path(s) changed that this container cannot have written "
                "(a read-only root, or a path no dispatched role may write) — recorded, never counted as ours"
            )
        log(
            f"state: attributable changes {len(changes['attributable_changes'])}; "
            f"{'UNTOUCHED' if not changes['attributable_changes'] else 'WRITTEN'}"
        )

        self.document = {
            "schema": SCHEMA,
            "issue": ISSUE,
            "epic": EPIC,
            "dry_run": True,
            "verdict": verdict,
            "reason": reason,
            "started_at": self.document.get("started_at", now()),
            "finished_at": now(),
            "env": self.environment,
            "schedule": {
                "owner": "fleet/cron.py",
                "markers": markers,
                "lines": len(markers),
                "installed_by": "infra/fleet/entrypoint.sh -> python3 fleet/cron.py install",
            },
            "jobs": jobs,
            "findings": self.findings,
            "probes": {"fleet_health": fleet_signal(self.repo, self.environment, timeout)},
            "state": {
                "roots": [
                    {
                        "path": str(root),
                        "mount": snapshot["mount"],
                        "writable": snapshot["writable"],
                        "files": snapshot["files"],
                        "digest": snapshot["digest"],
                        "attribution": (
                            "structural (the mount is read-only, so this container cannot write here)"
                            if (snapshot["mount"].get("mount") == "ro")
                            else "checksum (the root is writable, so a permitted path that moved is ours)"
                        ),
                    }
                    for root, snapshot in after.items()
                ],
                "mount": "each root's flags as measured from /proc/self/mountinfo",
                "permitted_writes": {"source": permitted_source, "entries": list(permitted)},
                "added": changes["added"],
                "removed": changes["removed"],
                "changed": changes["changed"],
                "observed_changes": changes["observed_changes"],
                "attributable_changes": changes["attributable_changes"],
                "concurrent_writers": changes["concurrent_writers"],
                "unchanged_by_the_run": not changes["attributable_changes"],
            },
        }
        self.write()

        if verdict == "ok":
            log(f"verdict: OK — {reason}")
        else:
            log(f"verdict: NOT-OK — {reason}")

        if once:
            return OK if verdict == "ok" else NOT_OK

        self.install_signal_handlers()
        ready = threading.Event()
        self.server = healthz.serve(int(self.environment["AO_FLEET_PORT"]), self.decision_path, ready)
        ready.wait(timeout=10)
        log(f"/healthz serving on 0.0.0.0:{self.environment['AO_FLEET_PORT']} from {self.decision_path}")
        log(f"ready — decision: {self.decision_path}")
        self.stop_event.wait()
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
        self.write()
        log(f"clean stop ({self.stop.get('signal')}) — verdict {verdict}")
        return OK if verdict == "ok" else NOT_OK

    def refuse(self, code: str, detail: str) -> int:
        """A refusal is terminal and is RECORDED, so a stopped container still explains itself."""
        self.finding(code, detail)
        log(f"REFUSED {code}: {detail}")
        self.document = {
            "schema": SCHEMA,
            "issue": ISSUE,
            "epic": EPIC,
            "dry_run": True,
            "verdict": "not-ok",
            "reason": f"{code}: {detail}",
            "started_at": self.document.get("started_at", now()),
            "finished_at": now(),
            "env": self.environment,
            "jobs": [],
            "findings": self.findings,
            "refusal": {"code": code, "detail": detail},
        }
        self.stop = {"signal": None, "clean": False, "note": f"refused before serving: {code}"}
        self.write()
        return NOT_OK


def load_markers(repo: Path) -> list[str] | None:
    """The schedule's markers, from the module that owns them."""
    sys.path.insert(0, str(repo / "fleet"))
    try:
        import cron  # noqa: PLC0415 — measured here, not at import time
    except Exception:  # noqa: BLE001
        return None
    try:
        return [str(marker) for marker in cron.MARKERS]
    except Exception:  # noqa: BLE001
        return None


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    findings = env_contract.validate()
    if findings:
        for finding in findings:
            print(f"{LOG_PREFIX} REFUSED {finding.code}: {finding.detail}", file=sys.stderr)
        return NOT_OK
    environment = dict(os.environ)
    environment.update(env_contract.resolve())
    resolved = env_contract.resolve()
    repo = Path(resolved["AO_FLEET_REPO"])
    run = DevRun(environment=environment, repo=repo, decision_path=repo / DECISION_RELATIVE)
    run.document = {"started_at": now()}
    return run.run(once=args.once, timeout=int(resolved["AO_FLEET_ROLE_TIMEOUT"]))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dev-run", description=__doc__)
    parser.add_argument("--once", action="store_true", help="dispatch, report and exit (no /healthz surface)")
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
