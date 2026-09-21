"""Pytest bootstrap + runtime isolation for the fleet suite (M26).

``fleet/channel.py`` is a standalone script (repo convention: namespace
modules), so the package directory goes to the front of ``sys.path``.

**Isolation is the second job of this file, and it is measured, not assumed.**
On 2026-09-13 running this suite appended fabricated entries to the repo's real
`.fleet/slog.jsonl` — 32 of them over a few runs, including `merged #171` and
`done`, i.e. work that never happened, in the audit log the brain tails and the
health signal reads. The autouse fixture below redirects every runtime path the
fleet writes to a per-test tmp directory, so a new test cannot forget to patch
one.

The cover is PROVED by ``fleet/tests/test_isolation_guard.py``, not promised by
this docstring (issue #284 measured three ways the original guard was inert: its
tests were never collected, its probe was compared against its own JSON escape,
and its "live" path pointed at ``<repo>/fleet/.fleet``, which never exists). This
module therefore exposes the cover as small callables — ``unredirected_paths``,
``leaked_probe``, ``assert_probe_stays_out_of`` — that the guard module drives and
mutates, and ``redirect_runtime_paths`` imports every covered module instead of
skipping the ones no test happened to import.
"""

from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path

import pytest

PKG_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PKG_DIR not in sys.path:
    sys.path.insert(0, PKG_DIR)

import channel  # noqa: E402

# module -> the runtime paths it writes (all redirected for every test)
RUNTIME_PATHS = {
    "channel": (
        "INBOX",
        "SENT",
        "OUTBOX",
        "DONE",
        "SLOG",
        "BRAIN_INBOX",
        "BRAIN_SENT",
        "BRAIN_OUTBOX",
        "BRAIN_DONE",
        "HEARTBEAT",
        "BRAIN_HEARTBEAT",
        "LOGS",
        "STEERS",
    ),
    "terminal": ("HEARTBEAT", "RUNS", "REPORTED", "PAUSED", "STOPPING", "RUNNER_HOLD", "WORKTREE_ROOT"),
    # The runtime-beat producers (#1412) write `.fleet/runtime-beats/<id>.json` for
    # the judgement `scripts/check-runtime-liveness.sh` makes. `beats.ROOT` is the
    # target they resolve, so it is redirected here: ONE stray beat left in the real
    # tree would engage the judge on the next gate run and report every other
    # registered runtime `runtime-stale`, i.e. a test could red the gate of record.
    "beats": ("ROOT",),
    "brain": ("HEARTBEAT", "WAVES", "DISPATCH_MARKERS", "MASTER_ATTESTATION"),
    "health": ("SISTER_HEARTBEAT", "BRAIN_HEARTBEAT"),
    # The dispatch markers' state machine (#796): it reads and writes the marker
    # set, the run registry and the dead-letter store, so a test that reconciles
    # must land in `tmp_path` — the same cover, for the newest runtime tree.
    "markers": ("DISPATCHED", "RUNS", "DEAD_LETTER"),
    "runslog": ("RUNS_LOG",),
    # `watchdog.spawn` OPENS a file named after FLEET_DIR, and `console` reads
    # the same tree — without these three lines a test would create real
    # `.fleet/*.log` files and read the live fleet's state, which is the
    # measured failure this fixture exists to prevent.
    "watchdog": ("FLEET_DIR", "RUNS_DIR"),
    "console": ("FLEET_DIR",),
    "monitor": ("FLEET_DIR", "LOG", "HEARTBEAT", "WAVES_DIR", "SISTER_HEARTBEAT", "BRAIN_HEARTBEAT"),
}


def redirect_runtime_paths(tmp_path, monkeypatch, coverage=None) -> None:
    """Redirect every covered runtime path; never silently skip a module.

    The previous version looked each module up in ``sys.modules`` and
    ``continue``d when it was absent, so a module no test happened to import
    (measured: ``monitor``) was never redirected at all — the docstring promised
    "a new test cannot forget to patch one" and the promise did not hold.
    Importing each module here means every declared path is covered, a module
    that cannot be imported fails the run loudly, and a constant that was
    renamed fails instead of passing un-covered.
    """
    table = RUNTIME_PATHS if coverage is None else coverage
    for module_name, names in table.items():
        module = importlib.import_module(module_name)
        for name in names:
            if not hasattr(module, name):
                pytest.fail(
                    f"RUNTIME_PATHS declares {module_name}.{name}, which no longer "
                    "exists — the isolation cover cannot be assumed"
                )
            monkeypatch.setattr(module, name, tmp_path / module_name / name.lower())


def unredirected_paths(tmp_path, coverage=None) -> list[str]:
    """Covered paths that still point outside ``tmp_path`` (empty is the pass).

    Shared by the guard (which asserts it is empty) and its negative control
    (which must be able to name an offender): a cover assertion that cannot fail
    is a formality (GR-12).
    """
    table = RUNTIME_PATHS if coverage is None else coverage
    offenders: list[str] = []
    for module_name, names in table.items():
        module = importlib.import_module(module_name)
        for name in names:
            value = getattr(module, name, None)
            if value is None or tmp_path not in Path(value).parents:
                offenders.append(f"{module_name}.{name}")
    return offenders


@pytest.fixture(autouse=True)
def isolate_fleet_runtime(tmp_path, monkeypatch):
    """Point every fleet runtime path at a tmp dir for the duration of a test."""
    redirect_runtime_paths(tmp_path, monkeypatch)
    singleton = importlib.import_module("singleton")
    monkeypatch.setattr(singleton, "FLEET", tmp_path / "singleton")
    return tmp_path


#: The fixed "master head" every test sees by default, so this suite never
#: depends on this checkout's own real `origin/master` (which drifts as the
#: repo is worked on, and would make CI flaky against a fixture written once).
_DEFAULT_TEST_MASTER_HEAD = "0" * 40


@pytest.fixture(autouse=True)
def master_health_is_green_by_default(isolate_fleet_runtime, monkeypatch):
    """`brain.dispatch()` refuses on a red/absent/head-stale master attestation
    (RCA 2026-09-17 fix #5) — a fact no suite in this repo was written to
    expect. Every test's `brain.MASTER_ATTESTATION` is already redirected into
    `tmp_path` by `isolate_fleet_runtime`, above; this seeds it green, at a
    fixed stand-in head that `brain.current_master_head` is also repointed to
    (freshness is head-bound, not wall-clock — #1114 follow-up), so an
    existing suite that dispatches an order for an unrelated reason is not
    incidentally refused, and never shells out to real `git`. A test
    exercising the master-health check itself (``fleet/tests/test_brain.py``)
    overwrites this file, or repoints `current_master_head`/the constant, to
    get a red/absent/head-stale verdict instead.
    """
    brain = importlib.import_module("brain")
    monkeypatch.setattr(brain, "current_master_head", lambda: _DEFAULT_TEST_MASTER_HEAD)
    path = brain.MASTER_ATTESTATION
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "result": "PASS",
                "exit_code": 0,
                "commit": _DEFAULT_TEST_MASTER_HEAD,
                "timestamp": __import__("datetime")
                .datetime.now(__import__("datetime").timezone.utc)
                .isoformat(),
            }
        ),
        encoding="utf-8",
    )


#: The executable the fleet's default runner names (`terminal.DEFAULT_RUNNER`).
#: Issue #1787 moved that default to the native DeepSeek CLI, so the stand-in below
#: is named after whatever the default actually is. A suite that drives the run path
#: must resolve the real default; naming it here is how the two stay in step, and a
#: mismatch fails loudly rather than silently resolving some other binary.
DEFAULT_RUNNER_BINARY = "deepseek"


@pytest.fixture(autouse=True)
def resolvable_default_runner(tmp_path, monkeypatch):
    """Put a stand-in runner on PATH, so the run path resolves deterministically.

    The loop resolves its runner explicitly (#733), so a suite that drives the run
    path must not depend on whether the HOST has the agent CLI installed — the same
    reason this file redirects `.fleet/` instead of promising every test will patch
    it. This is NOT a stub of the resolution: ``shutil.which`` really finds a real
    executable file on a PATH built here. A test that wants an unresolvable runner
    asks for one by name (``resolve_runner("claude-733-absent")``) or empties PATH
    itself, which is how the negative controls are written.
    """
    binary = tmp_path / "runner-bin" / DEFAULT_RUNNER_BINARY
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    binary.chmod(0o755)
    monkeypatch.setenv("PATH", f"{binary.parent}{os.pathsep}{os.environ.get('PATH', '')}")
    return binary


#: The environment the default runner profile DECLARES it needs (`fleet/runners.py`,
#: profile `claude-byok`, #841).
BYOK_ENVIRONMENT = ("ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN")


@pytest.fixture(autouse=True)
def byok_runner_environment(monkeypatch):
    """Satisfy the default runner profile's declared environment for every test.

    Same reason as ``resolvable_default_runner`` above, one question later: the
    loop no longer only asks whether its runner *resolves*, it asks whether that
    runner can *honour* the model it is about to be given (#841). A suite that
    drives the run path must therefore supply the environment a BYOK-wired box has,
    or it would be measuring the refusal instead of the path.

    The VALUES are never read — ``fleet/runners.py`` asks only whether each name is
    set, which is the whole of what it can honestly check offline — so these are
    placeholders and no credential is involved. A test that wants the refusal asks
    for it by clearing them (``monkeypatch.delenv(...)``), which is how the
    negative controls are written.

    ``FLEET_RUNNER_PROFILE`` is set for the same reason: several tests drive the run
    path with a no-op stand-in (``/usr/bin/true``), and a stand-in's *filename* cannot
    say which runner it stands for. Naming the profile is exactly what the refusal
    asks an operator with a wrapper to do, so the suite does it rather than weakening
    the refusal for everyone.
    """
    for name in BYOK_ENVIRONMENT:
        monkeypatch.setenv(name, "conftest-placeholder-not-a-credential")
    monkeypatch.setenv("FLEET_RUNNER_PROFILE", "claude-byok")
    return BYOK_ENVIRONMENT


#: The probe the guard writes and then looks for. It deliberately carries a
#: non-ASCII character: ``channel._slog`` serialises with ``json.dumps``'
#: default ``ensure_ascii=True``, so on disk the probe is escaped and a raw
#: substring test can never be true (#284).
PROBE = "isolation probe — this must never reach the live fleet log"


def spawn_source_stub(tmp_path: Path):
    """A complete envelope source set, derived from whatever the caller supplied.

    Since #793 a spawn that cannot present a well-formed envelope is REFUSED —
    that is the whole point of the change — so a suite that drives the loop must
    supply one, exactly as the loop does by claiming the issue and provisioning
    the lane before it builds a prompt. This stub injects the SOURCES (the claim
    ledger, the board, the gate permit, the budget) rather than reading them, so
    the suite stays offline and no test depends on the box's board or on the
    lane's own claim existing.

    Everything the caller passes is honoured — the issue, the lane, the worktree,
    the minted `AO_*` environment — so a test that asserts the prompt names
    `#163` still sees `#163`. A test that wants the REFUSAL patches
    `governance.spawn.sources.collect` itself (or drops a field from this stub's
    return value), which is how the negative controls are written.
    """

    def stub(**kwargs):
        issue = kwargs.get("issue")
        lane = kwargs.get("lane") or "test-lane"
        env = dict(kwargs.get("env") or {})
        agent = kwargs.get("agent_id") or env.get("AO_AGENT_ID") or "test-agent"
        worktree = str(kwargs.get("worktree") or env.get("AO_WORKTREE") or tmp_path / "lane-wt")
        branch = env.get("AO_BRANCH") or f"issue-{issue}"
        session_id = env.get("AO_SESSION_ID") or "testsession0001"
        permit_store = tmp_path / "gate-store"
        return {
            "issue": issue,
            "lane": lane,
            "worktree": worktree,
            "session": {
                "id": session_id,
                "issue": str(issue),
                "agent": agent,
                "lane": lane,
                "branch": branch,
                "worktree": worktree,
                "repo_slug": "kushin77/agent-orchestrator",
                "author_name": f"agent-{agent}",
                "author_email": f"agent+{agent}@agents.invalid",
                "committer_name": f"agent-{agent}",
                "committer_email": f"agent+{agent}@agents.invalid",
            },
            "trailer": f"Refs kushin77/agent-orchestrator#{issue}",
            "claim": {"owner": agent, "state": "claim", "lane": lane, "at": "2026-09-15T00:00:00Z"},
            "focus": {
                "epic": 160,
                "source": "pinned-focus",
                "pinned_epic": 160,
                "wave_cap": 12,
                "max_agents": 0,
            },
            "capacity": {
                "effective": 1,
                "binding": "disjoint",
                "assessed": True,
                "bounds": [{"name": "pool", "limit": 10, "why": "conftest stub"}],
                "problems": [],
                "permit": {
                    "store": str(permit_store),
                    "worktree_key": "conftest-stub-key",
                    "lock": str(permit_store / "worktrees" / "conftest-stub-key.lock"),
                    "max_concurrent": 4,
                },
            },
            "budget": {"attempts": 0, "cap": 5, "state": "pending", "next_attempt_at": None},
            "gate": {
                "of_record": "make verify",
                "bound": "at most one composite gate per worktree, bounded box-wide (AO-GR-22)",
                "entry": "scripts/gate-lock.sh",
                "max_concurrent": 4,
                "ttl_seconds": 900,
            },
            "verify": {"command": "make verify", "source": "gate-of-record"},
            "spawn": {
                "path": kwargs.get("path") or "fleet",
                "agent": agent,
                "directive": str(kwargs.get("directive_id") or ""),
            },
        }

    return stub


@pytest.fixture(autouse=True)
def complete_spawn_envelope(tmp_path, monkeypatch):
    """Give every fleet test a complete spawn envelope (#793).

    Same posture as the two fixtures above: the suite drives the real run path,
    so it must satisfy the precondition that path now has. A spawn without a
    well-formed envelope is REFUSED by design, and `governance/spawn/tests` is
    where that refusal and its per-field provocations are proven — here the stub
    exists so the OTHER fleet behaviour (prompts, runners, streaming, finops)
    keeps being measured instead of the refusal.
    """
    spawn_sources = importlib.import_module("governance.spawn.sources")
    monkeypatch.setattr(spawn_sources, "collect", spawn_source_stub(tmp_path))
    return tmp_path


def live_fleet_dir() -> Path:
    """The REAL `.fleet/` at the repository root — what must stay untouched.

    It is ``<repo>/.fleet``, *beside* ``fleet/``. ``parents[1]`` from this file
    is ``<repo>/fleet``, so the original ``<repo>/fleet/.fleet`` was a directory
    that never exists and the leak assertion below it was unreachable (#284).
    """
    return Path(__file__).resolve().parents[2] / ".fleet"


def records_at(path: Path) -> list[dict]:
    """Parse any JSONL log file; absent, unreadable or torn lines read as none."""
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except OSError:
        return []
    records: list[dict] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def slog_records(fleet_dir: Path) -> list[dict]:
    """Parse ``<fleet_dir>/slog.jsonl`` — the audit log a ``.fleet/`` holds."""
    return records_at(Path(fleet_dir) / "slog.jsonl")


def leaked_probe(fleet_dir: Path, probe: str = PROBE) -> bool:
    """True when a PARSED record in `fleet_dir`'s audit log carries the probe.

    The comparison is on the parsed record body, never the raw line: the probe's
    em dash is written as ``\\u2014``, which is what made the original guard
    unsatisfiable. The probe is truncated to the 200 characters ``_slog`` keeps,
    so a truncated body is still detected.
    """
    marker = probe[:200]
    return any(marker in str(record.get("body", "")) for record in slog_records(fleet_dir))


def assert_probe_stays_out_of(fleet_dir: Path, probe: str = PROBE) -> None:
    """Fail when a test's write reached `fleet_dir`'s audit log (#215, #284)."""
    if leaked_probe(fleet_dir, probe):
        raise AssertionError(
            f"a test wrote {probe!r} into {Path(fleet_dir) / 'slog.jsonl'} — the autouse "
            "isolation fixture is not covering this path"
        )


def write_slog_probe(probe: str = PROBE) -> None:
    """Emit one real channel record — the write the fixture must intercept."""
    channel._slog({"type": "result", "from": "sister", "to": "brain", "body": probe})
