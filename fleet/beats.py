"""fleet/beats.py — the producer side of the runtime-liveness contract (issue #1412).

---knowledge---
module_id: fleet.beats
system: fleet
app: fleet
solution_class: pattern
patterns: []
derives_from: null
owner_sme: unassigned
tier: L1
interfaces: [beats_dir, read, running_commit, Posting, post, best_effort, cmd_post, cmd_show, (+2 more)]
invariants: ""
gotchas: ""
related: []
do_not_duplicate: null
---knowledge---

`fleet/runtime_liveness.py` is the JUDGE: it reads `.fleet/runtime-beats/<id>.json`
and reports `runtime-stale:<id>`, `runtime-drift:<id>`, `runtime-unregistered:<id>`.
Until #1412 nothing WROTE those files, so on the real tree the judge could only
report `no-beats-yet` and the gate was inert. This module is the writer every
runtime goes through, so there is exactly one place that decides what a beat is:

* `integrations/paperclip/adapters/heartbeat/beat.py` still owns the RECORD —
  `{runtime, commit, state, ts}`, atomic write, unregistered ids refused;
* this module owns the PRODUCER's decisions: which commit counts as "running",
  how often a caller may skip a redundant refresh, and what a caller reports when
  it could not beat. It never re-implements the record.

`ROOT` is a module-level constant because it is the **producer's target** — the
fleet tree whose `.fleet/runtime-beats/` the judge reads. `fleet/tests/conftest.py`
redirects it for every test (the isolation cover), so a suite that drives a
producer can never leave a beat in the real tree: one stray beat would engage the
judge and make every other registered runtime `runtime-stale`, i.e. it would red
the gate of record for the whole wave.

Every producer is a caller of `post()`:

| runtime            | producer                                                      |
|--------------------|---------------------------------------------------------------|
| `claude-session`   | `fleet/hooks/claude-beat.sh` (SessionStart / PostToolUse hook) |
| `claude-subagent`  | the same hook, `--runtime claude-subagent` (SubagentStop)      |
| `deepseek-sister`  | `fleet/terminal.py::loop` — at start and on every poll cycle   |
| `deepseek-executor`| `fleet/terminal.py::run_once` — at each spawn, and each run beat|
| `copilot-agent`    | `fleet/channel.py` — a runtime that speaks through the mailbox |
| `hermes`           | `integrations/hermes/cli.py probe` — on a live call            |
| `paperclip`        | `integrations/paperclip/api/cli.py health` — on a live call    |

Usage:

    python3 -m fleet.beats post --runtime claude-session [--state running]
                               [--cwd DIR] [--root DIR] [--min-interval SECONDS] [--force]
    python3 -m fleet.beats show [--root DIR]

Exit contract (guardrails/honesty tri-state): 0 written or legitimately skipped /
1 refused by name (unregistered id, unknown state, no measurable commit, unreadable
registry) / 2 CANNOT-ASSESS (the producer cannot establish the inputs at all).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

#: The fleet tree whose `.fleet/runtime-beats/` the judge reads.
ROOT = Path(__file__).resolve().parent.parent

#: A producer that refreshes a beat more often than this is spending more than the
#: signal is worth. The hook is the case that needs it: it runs on every tool call
#: in every Claude session on the box, and a beat is a *liveness* stamp, not a log.
DEFAULT_MIN_INTERVAL_SECONDS = 300.0

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_CANNOT_ASSESS = 2


def _beat_module():
    """The record's owner, imported on demand (it needs PyYAML).

    `ROOT` goes on `sys.path` first: this module is imported both as
    `fleet.beats` (from the repo root) and as a bare `beats` (the fleet
    convention, where `fleet/` is `sys.path[0]`), and `integrations/` lives at
    the repo root in each case.
    """
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from integrations.paperclip.adapters.heartbeat import beat

    return beat


def beats_dir(root: Path | str | None = None) -> Path:
    """Where the beats live for `root` (default: this checkout)."""
    return _beat_module().beats_dir(root if root is not None else ROOT)


def read(runtime_id: str, root: Path | str | None = None) -> dict | None:
    """A runtime's last beat, or None (a torn file reads as absent, per the adapter)."""
    return _beat_module().read_beat(runtime_id, root if root is not None else ROOT)


def running_commit(cwd: Path | str | None = None) -> str:
    """The commit the caller is RUNNING — `git rev-parse HEAD` of `cwd`.

    Empty when it cannot be measured (no git, not a repository, no commits): the
    caller decides, and `post` refuses an empty commit rather than stamping a
    beat with a commit nobody can compare to master.
    """
    where = str(cwd if cwd is not None else ROOT)
    try:
        proc = subprocess.run(
            ["git", "-C", where, "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


@dataclass(frozen=True)
class Posting:
    """What a producer did, and why — a skip is reported, never silent."""

    wrote: bool
    record: dict
    detail: str

    @property
    def runtime(self) -> str:
        return str(self.record.get("runtime") or "")


def post(
    runtime_id: str,
    state: str = "running",
    *,
    commit: str | None = None,
    cwd: Path | str | None = None,
    root: Path | str | None = None,
    now: float | None = None,
    min_interval: float | None = None,
    force: bool = False,
) -> Posting:
    """Post one runtime's beat. Refuses by name; never invents a record.

    `min_interval` makes the call cheap for a producer that fires often (the hook,
    a poll cycle): a beat younger than the interval is left alone and the
    POSTING says so. `force` bypasses the interval — the path a negative control
    uses to prove the interval is what skipped the write, not a broken producer.
    """
    beat = _beat_module()
    when = time.time() if now is None else float(now)
    resolved_commit = commit if commit is not None else running_commit(cwd)
    if not resolved_commit:
        raise beat.BeatRefused(
            f"the running commit could not be measured"
            f"{f' in {cwd}' if cwd is not None else ''} (git rev-parse HEAD) — "
            "a beat whose commit cannot be compared to master is not a liveness signal"
        )
    target = root if root is not None else ROOT
    if min_interval is not None and not force:
        existing = beat.read_beat(runtime_id, target)
        if existing is not None:
            ts = existing.get("ts")
            if isinstance(ts, (int, float)) and not isinstance(ts, bool):
                age = when - float(ts)
                if 0 <= age < float(min_interval):
                    return Posting(
                        wrote=False,
                        record=existing,
                        detail=(
                            f"{runtime_id} is fresh ({age:.0f}s old, interval "
                            f"{float(min_interval):.0f}s) — nothing to refresh"
                        ),
                    )
    record = beat.write_beat(runtime_id, resolved_commit, state, ts=when, root=target)
    return Posting(wrote=True, record=record, detail=f"{runtime_id} beat at {record['ts']:.0f}")


def best_effort(
    runtime_id: str,
    state: str = "running",
    *,
    root: Path | str | None = None,
    cwd: Path | str | None = None,
    min_interval: float | None = None,
) -> Posting | None:
    """`post` for a long-lived loop: a refused beat is REPORTED, never fatal.

    A liveness stamp must not be able to kill the thing it reports on. The loop
    prints the refusal (so an unregistered id or an unreadable registry is visible
    in `sister.log`) and carries on; the judge then reports that runtime stale,
    which is the honest end state — the beat was not written.
    """
    try:
        return post(runtime_id, state, root=root, cwd=cwd, min_interval=min_interval)
    except Exception as exc:  # noqa: BLE001 - see the docstring: report, never raise
        print(f"[beats] {runtime_id} beat REFUSED — {exc}", file=sys.stderr, flush=True)
        return None


def cmd_post(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve() if args.root else ROOT
    cwd = Path(args.cwd).resolve() if args.cwd else root
    try:
        posting = post(
            args.runtime,
            args.state,
            commit=args.commit or None,
            cwd=cwd,
            root=root,
            min_interval=float(args.min_interval) if args.min_interval is not None else None,
            force=args.force,
        )
    except _beat_module().BeatRefused as exc:
        print(f"beats: REFUSED — {exc}", file=sys.stderr)
        return EXIT_REFUSED
    if posting.wrote:
        print(f"beats: {posting.runtime} {args.state} at {posting.record['commit'][:12]}")
    else:
        print(f"beats: {posting.detail}")
    return EXIT_OK


def cmd_show(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve() if args.root else ROOT
    try:
        registry = _beat_module().load_registry(root)
    except _beat_module().BeatRefused as exc:
        print(f"beats: CANNOT-ASSESS — {exc}", file=sys.stderr)
        return EXIT_CANNOT_ASSESS
    beats = _beat_module().read_all_beats(root)
    payload = {
        "root": str(root),
        "registry": sorted(registry),
        "beats": beats,
        "missing": sorted(set(registry) - set(beats)),
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return EXIT_OK
    print(f"beats: {len(beats)}/{len(registry)} registered runtime(s) have beaten under {root}")
    for runtime_id in sorted(registry):
        record = beats.get(runtime_id)
        if record is None:
            print(f"  missing  {runtime_id}")
            continue
        print(f"  beat     {runtime_id} state={record.get('state')} ts={record.get('ts')}")
    for runtime_id in sorted(set(beats) - set(registry)):
        print(f"  unregistered {runtime_id}")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fleet-beats", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    post_parser = sub.add_parser("post", help="write one runtime's beat")
    post_parser.add_argument("--runtime", required=True, help="the registered runtime id")
    post_parser.add_argument("--state", default="running")
    post_parser.add_argument("--commit", default="", help="default: git rev-parse HEAD of --cwd")
    post_parser.add_argument("--cwd", default="", help="where the RUNNING commit is measured")
    post_parser.add_argument("--root", default="", help="the fleet tree the beat is written into")
    post_parser.add_argument(
        "--min-interval",
        default=None,
        help="skip a refresh younger than this many seconds (default: always write)",
    )
    post_parser.add_argument("--force", action="store_true", help="ignore the existing beat's age")
    post_parser.set_defaults(func=cmd_post)

    show_parser = sub.add_parser("show", help="print the beats on disk beside the registry")
    show_parser.add_argument("--root", default="")
    show_parser.add_argument("--json", action="store_true")
    show_parser.set_defaults(func=cmd_show)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
