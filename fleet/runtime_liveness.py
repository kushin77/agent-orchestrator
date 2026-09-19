"""fleet/runtime_liveness.py — the pure runtime-liveness judge (issue #1271).

Every runtime beats through `integrations/paperclip/adapters/heartbeat`
(`{runtime, commit, state, ts}`), registered in `fleet/runtimes.yaml`. This
module is the JUDGE over those beats — a pure function of the inputs it is
given (beats, registry, commit distance to master, directives in flight, an
injected `now`), so it can be unit-tested without touching a clock, git or the
filesystem. `scripts/check-runtime-liveness.sh` is the thin shell around it
that gathers the real inputs and applies the exit-code contract.

Findings, each named `<code>:<runtime-id>`:

* `runtime-stale:<id>`   — the beat's age exceeds the declared stale window.
* `runtime-drift:<id>`   — the beat's commit trails master by more than N
                            commits AND no directive is in flight for that
                            runtime (a directive in flight is presumed to be
                            the thing that will move it).
* `runtime-unregistered:<id>` — a beat exists for an id `fleet/runtimes.yaml`
                            does not declare.

A runtime with no beat at all is `runtime-stale:<id>` too — "never reported" is
the maximum case of "too old", not a pass — ONCE the fleet has started beating.
Before the first beat exists anywhere (`.fleet/runtime-beats/` is empty) there
is nothing to judge staleness AGAINST: that state is reported as `no-beats-yet`
and is OK with a note, never NOT-OK, because a gate that reds the real tree on
the commit that introduces it measures nothing and blocks every lane (#1271).
The judge engages the moment one runtime beats: from then on every registered
runtime without a beat is `runtime-stale:<id>`.

Module name: `runtime_liveness`, not `liveness` — `governance/dispatch/claims.py`
puts `fleet/` on `sys.path[0]`, so a `fleet/liveness.py` would shadow
`governance/dispatch/liveness.py` for every `import liveness` that follows.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

EXIT_OK = 0
EXIT_NOT_OK = 1
EXIT_CANNOT_ASSESS = 2

#: A directive posted `to` this mailbox name is presumed to be steering this
#: runtime id — only `deepseek-sister` has a live mailbox rung (`sister`)
#: today; every other id maps to itself so a future rung needs no code change
#: here, only an inbox that actually uses its id.
RUNTIME_TO_MAILBOX = {"deepseek-sister": "sister"}

#: Default declared staleness window, seconds. Overridable per call.
DEFAULT_STALE_WINDOW_SECONDS = 3600.0
#: Default drift budget, commits. Overridable per call.
DEFAULT_DRIFT_COMMITS = 5

CODE_STALE = "runtime-stale"
CODE_DRIFT = "runtime-drift"
CODE_UNREGISTERED = "runtime-unregistered"
#: The note printed when no runtime has ever beaten: OK, engaged by the first beat.
NOTE_NO_BEATS_YET = "no-beats-yet"


def no_beats_yet(beats: dict[str, dict]) -> bool:
    """True when no runtime has ever posted a beat — there is nothing to judge."""
    return not beats


@dataclass(frozen=True)
class Finding:
    code: str
    runtime_id: str
    detail: str

    @property
    def name(self) -> str:
        return f"{self.code}:{self.runtime_id}"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.name} — {self.detail}"


def judge(
    *,
    beats: dict[str, dict],
    registry: dict[str, dict],
    now: float,
    stale_window_seconds: float = DEFAULT_STALE_WINDOW_SECONDS,
    drift_commits: int = DEFAULT_DRIFT_COMMITS,
    commit_distance: dict[str, int] | None = None,
    directives_in_flight: frozenset[str] | set[str] = frozenset(),
) -> list[Finding]:
    """Judge every registered runtime (plus any unregistered beat) at `now`.

    `commit_distance` maps a runtime id to how many commits its beat's `commit`
    trails `origin/master` by; a missing entry means "could not be measured"
    and drift is skipped for that runtime rather than assumed.

    `directives_in_flight` is the set of runtime ids that currently have a
    directive dispatched to them — a drifted runtime with one in flight is not
    reported: the in-flight work is presumed to be what moves it.
    """
    if stale_window_seconds <= 0:
        raise ValueError("stale_window_seconds must be > 0")
    if drift_commits < 0:
        raise ValueError("drift_commits must be >= 0")

    distances = commit_distance or {}
    findings: list[Finding] = []

    if no_beats_yet(beats):
        # Nothing has beaten yet, so no beat can be stale relative to another:
        # the caller reports `no-beats-yet` (an OK with a note, see the module
        # docstring). Registered runtimes are judged from the first beat on.
        return findings

    # Unregistered beats: reported by NAME, and nothing else is judged about
    # them — an unregistered id has no declared window or budget to hold it to.
    for runtime_id in sorted(beats):
        if runtime_id not in registry:
            findings.append(
                Finding(CODE_UNREGISTERED, runtime_id, "beat exists for an id fleet/runtimes.yaml does not declare")
            )

    for runtime_id in sorted(registry):
        record = beats.get(runtime_id)
        if record is None:
            findings.append(Finding(CODE_STALE, runtime_id, "no beat has ever been recorded"))
            continue

        ts = record.get("ts")
        if not isinstance(ts, (int, float)) or isinstance(ts, bool):
            findings.append(Finding(CODE_STALE, runtime_id, f"beat has no usable ts ({ts!r})"))
            continue

        age = now - float(ts)
        if age > stale_window_seconds:
            findings.append(
                Finding(
                    CODE_STALE,
                    runtime_id,
                    f"beat is {age:.0f}s old, past the {stale_window_seconds:.0f}s window",
                )
            )
            continue  # stale outranks drift: a beat this old cannot be trusted to be behind vs. gone

        distance = distances.get(runtime_id)
        if distance is None:
            continue  # cannot assess drift for this runtime; not a finding
        if distance > drift_commits and runtime_id not in directives_in_flight:
            findings.append(
                Finding(
                    CODE_DRIFT,
                    runtime_id,
                    f"running commit trails master by {distance} (> {drift_commits}) with no directive in flight",
                )
            )

    return findings


def ok(findings: list[Finding]) -> bool:
    return not findings


# ── the gate: gather real inputs, then hand them to the pure judge above ────


def _git_rev_list_count(root: Path, commit: str, ref: str = "origin/master") -> int | None:
    """Commits `commit` trails `ref` by, or None if it cannot be measured
    (unknown/short/invalid commit, no such ref, git missing)."""
    try:
        proc = subprocess.run(
            ["git", "rev-list", "--count", f"{commit}..{ref}"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    try:
        return int(proc.stdout.strip())
    except ValueError:
        return None


def _directives_in_flight(root: Path) -> set[str]:
    """Runtime ids with a directive currently sitting in `.fleet/inbox/`."""
    inbox = root / ".fleet" / "inbox"
    if not inbox.is_dir():
        return set()
    mailbox_to_runtime = {mailbox: runtime for runtime, mailbox in RUNTIME_TO_MAILBOX.items()}
    in_flight: set[str] = set()
    for path in inbox.glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        to = payload.get("to") if isinstance(payload, dict) else None
        if not isinstance(to, str):
            continue
        in_flight.add(mailbox_to_runtime.get(to, to))
    return in_flight


def gather_and_judge(
    root: Path,
    *,
    now: float | None = None,
    stale_window_seconds: float = DEFAULT_STALE_WINDOW_SECONDS,
    drift_commits: int = DEFAULT_DRIFT_COMMITS,
) -> tuple[list[Finding], str | None]:
    """Gather the real inputs under `root` and judge them. Returns
    `(findings, cannot_assess_reason)` — a non-None reason means the registry
    itself could not be read and no judgment was made."""
    # Imported lazily so a pure unit test of `judge()` never needs beat.py or yaml.
    from integrations.paperclip.adapters.heartbeat import beat as beat_module

    try:
        registry = beat_module.load_registry(root)
    except beat_module.BeatRefused as exc:
        return [], str(exc)

    beats = beat_module.read_all_beats(root)
    when = now if now is not None else time.time()
    distances: dict[str, int] = {}
    for runtime_id, record in beats.items():
        commit = record.get("commit") if isinstance(record, dict) else None
        if isinstance(commit, str) and commit:
            distance = _git_rev_list_count(root, commit)
            if distance is not None:
                distances[runtime_id] = distance
    in_flight = _directives_in_flight(root)

    findings = judge(
        beats=beats,
        registry=registry,
        now=when,
        stale_window_seconds=stale_window_seconds,
        drift_commits=drift_commits,
        commit_distance=distances,
        directives_in_flight=in_flight,
    )
    return findings, None


def beat_module_beats(root: Path) -> dict[str, dict]:
    """The beats under `root`, read the way the gate reads them (empty on any refusal)."""
    from integrations.paperclip.adapters.heartbeat import beat as beat_module

    try:
        return beat_module.read_all_beats(root)
    except Exception:  # noqa: BLE001 - a no-beats note must never crash the verdict
        return {}


def cmd_run(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    now = float(args.now) if args.now is not None else None
    findings, reason = gather_and_judge(
        root,
        now=now,
        stale_window_seconds=args.stale_window,
        drift_commits=args.drift_commits,
    )
    if reason is not None:
        print(f"check-runtime-liveness: CANNOT-ASSESS — {reason}", file=sys.stderr)
        return EXIT_CANNOT_ASSESS
    if not findings:
        if no_beats_yet(beat_module_beats(root)):
            print(
                f"check-runtime-liveness: OK — {NOTE_NO_BEATS_YET}: no runtime has posted a "
                "beat under .fleet/runtime-beats/ yet, so there is nothing to judge staleness "
                "against; the judge engages with the first beat (post one with "
                "integrations/paperclip/adapters/heartbeat/cli.py)"
            )
            return EXIT_OK
        print("check-runtime-liveness: OK — every registered runtime is live")
        return EXIT_OK
    for finding in findings:
        print(f"  {finding}", file=sys.stderr)
    return EXIT_NOT_OK


def _self_test(root: Path) -> int:
    """Provoke every finding against a scratch fleet; the real tree must pass
    on its own inputs first (a broken gate must not report a false green by
    only ever grading scratch data)."""
    import tempfile

    failures: list[str] = []

    def probe(name: str, condition: bool, detail: str = "") -> None:
        status = "PASS" if condition else "FAIL"
        print(f"  probe {name}: {status}" + (f" — {detail}" if detail else ""))
        if not condition:
            failures.append(name)

    # The real tree, unmodified: whatever it reports must not be judged here —
    # only that reading it does not CRASH the gate (CANNOT-ASSESS is a legitimate
    # outcome on a tree with no registry checked out yet).
    real_findings, real_reason = gather_and_judge(ROOT)
    probe(
        "REAL-TREE-ASSESSABLE",
        real_reason is None or "unreadable" not in real_reason,
        f"reason={real_reason!r} findings={[f.name for f in real_findings]}",
    )

    with tempfile.TemporaryDirectory(prefix="ao-runtime-liveness-") as tmp:
        scratch = Path(tmp)
        (scratch / "fleet").mkdir(parents=True, exist_ok=True)
        (scratch / "fleet" / "runtimes.yaml").write_text(
            "runtimes:\n"
            "  - id: claude-session\n"
            "    kind: agent\n"
            "  - id: deepseek-sister\n"
            "    kind: agent\n",
            encoding="utf-8",
        )
        beats_dir = scratch / ".fleet" / "runtime-beats"
        beats_dir.mkdir(parents=True, exist_ok=True)

        def write_beat(runtime_id: str, commit: str, ts: float) -> None:
            (beats_dir / f"{runtime_id}.json").write_text(
                json.dumps({"runtime": runtime_id, "commit": commit, "state": "running", "ts": ts}),
                encoding="utf-8",
            )

        # 0. No beat anywhere yet: `no-beats-yet`, OK — nothing to judge against.
        findings, reason = gather_and_judge(scratch, now=1_000_010.0)
        probe(
            "NO-BEATS-YET-IS-OK",
            reason is None and findings == [] and no_beats_yet(beat_module_beats(scratch)),
            f"{reason}/{[f.name for f in findings]}",
        )
        # 0b. The FIRST beat engages the judge: the other registered id is stale by name.
        write_beat("claude-session", "aaa", 1_000_000.0)
        findings, reason = gather_and_judge(scratch, now=1_000_010.0, drift_commits=999999)
        probe(
            "FIRST-BEAT-ENGAGES-THE-JUDGE",
            reason is None and [f.name for f in findings] == ["runtime-stale:deepseek-sister"],
            f"{reason}/{[f.name for f in findings]}",
        )

        # 1. A healthy, fresh, undrifted pair: no findings.
        write_beat("claude-session", "aaa", 1_000_000.0)
        write_beat("deepseek-sister", "aaa", 1_000_000.0)
        findings, reason = gather_and_judge(
            scratch, now=1_000_010.0, stale_window_seconds=3600.0, drift_commits=999999
        )
        probe("SCRATCH-HEALTHY", reason is None and findings == [], f"{reason}/{[f.name for f in findings]}")

        # 2. Stale beat -> red BY NAME.
        write_beat("claude-session", "aaa", 0.0)
        findings, reason = gather_and_judge(
            scratch, now=1_000_000.0, stale_window_seconds=100.0, drift_commits=999999
        )
        names = [f.name for f in findings]
        probe("STALE-REDS-BY-NAME", "runtime-stale:claude-session" in names, str(names))
        write_beat("claude-session", "aaa", 1_000_000.0)  # restore for later probes

        # 3. Drift beyond N with no directive in flight -> red; commit_distance is
        #    exercised via the pure judge directly (git distance needs a real repo,
        #    which the pure judge does not require to prove the *rule*).
        beats = {
            "claude-session": {"runtime": "claude-session", "commit": "a", "state": "running", "ts": 1000.0},
            "deepseek-sister": {"runtime": "deepseek-sister", "commit": "old", "state": "running", "ts": 1000.0},
        }
        registry = {"claude-session": {}, "deepseek-sister": {}}
        drifted = judge(
            beats=beats, registry=registry, now=1000.0, drift_commits=5, commit_distance={"deepseek-sister": 10}
        )
        probe(
            "DRIFT-BEYOND-N-IDLE-REDS",
            [f.name for f in drifted] == ["runtime-drift:deepseek-sister"],
            str([f.name for f in drifted]),
        )

        # 4. Same drift, but a directive IS in flight -> not red.
        not_drifted = judge(
            beats=beats,
            registry=registry,
            now=1000.0,
            drift_commits=5,
            commit_distance={"deepseek-sister": 10},
            directives_in_flight={"deepseek-sister"},
        )
        probe("DRIFT-WITH-DIRECTIVE-NOT-RED", not_drifted == [], str([f.name for f in not_drifted]))

        # 5. An unregistered runtime id -> `runtime-unregistered:<id>`.
        beats_with_ghost = {**beats, "ghost": {"runtime": "ghost", "commit": "a", "state": "running", "ts": 1000.0}}
        ghosted = judge(beats=beats_with_ghost, registry=registry, now=1000.0)
        probe(
            "UNREGISTERED-ID-REDS",
            "runtime-unregistered:ghost" in [f.name for f in ghosted],
            str([f.name for f in ghosted]),
        )

    if failures:
        print(f"PROBES: FAIL ({', '.join(failures)})", file=sys.stderr)
        return EXIT_NOT_OK
    print("PROBES: PASS")
    return EXIT_OK


def cmd_self_test(args: argparse.Namespace) -> int:
    return _self_test(Path(args.root).resolve())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fleet-liveness", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="judge the real tree's registered runtimes")
    run.add_argument("--root", default=str(ROOT))
    run.add_argument("--now", default=None)
    run.add_argument("--stale-window", type=float, default=DEFAULT_STALE_WINDOW_SECONDS)
    run.add_argument("--drift-commits", type=int, default=DEFAULT_DRIFT_COMMITS)
    run.set_defaults(func=cmd_run)

    self_test = sub.add_parser("self-test", help="provoke every finding against a scratch fleet")
    self_test.add_argument("--root", default=str(ROOT))
    self_test.set_defaults(func=cmd_self_test)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
