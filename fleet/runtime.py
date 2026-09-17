#!/usr/bin/env python3
"""Namespaced fleet runtime — the single source of runtime state location + identity.

WHY this exists (EPIC #360, issue #361): every fleet module used to build its own
``ROOT / ".fleet"`` paths, so two fleets on one checkout could only share one
runtime directory — their locks, mailboxes and heartbeats collided, and a second
"dispatcher fleet" (e.g. a tmux/dashboard look-and-feel fleet pointed at a different
issue class) had nowhere to run alongside the first. This module makes the fleet's
runtime directory and its tmux session name explicit, overridable knobs, and every
other fleet module derives its paths from HERE instead of hardcoding ``ROOT / ".fleet"``.

* ``AO_FLEET_DIR``     — the fleet's runtime directory (default ``<repo>/.fleet``).
* ``AO_FLEET_SESSION`` — the tmux session name + dashboard header (default ``fleet``).

With both env vars unset every derived path is byte-identical to the pre-
namespacing layout, so an existing fleet is untouched and existing tests pass
unchanged. The claim ledger (``.board/``) is deliberately NOT here: it stays at
the repo root and is SHARED across fleets, so two fleets can never claim the
same issue.

Its third job is the fleet's **environment** facts: where an executor runner may
live when it is not on the ambient PATH. The loop is started by cron, so it
inherits cron's minimal PATH — the fleet must not depend on an environment it
does not control (#733), and the spawn authority (``fleet/watchdog.py``) and the
executor (``fleet/terminal.py``) must agree on where it looked, so the search
path is declared once, here.
"""

from __future__ import annotations

import os
from pathlib import Path

# The repository root this checkout lives in — the anchor every other fleet
# module already derives and the base of the default runtime directory.
ROOT = Path(__file__).resolve().parent.parent

# The fleet's runtime directory, resolved absolute: every runtime path in
# fleet/* derives from this one source. ``Path(Path)`` is a no-op, so an unset
# env leaves the historical ``<repo>/.fleet`` byte-identical.
FLEET_DIR = Path(os.environ.get("AO_FLEET_DIR", ROOT / ".fleet")).resolve()

# The fleet's identity: the tmux session name and the dashboard header label.
SESSION = os.environ.get("AO_FLEET_SESSION", "fleet")

# The runaway guard's two runtime locations (issue #723). They are declared
# HERE, with every other runtime path, so `fleet/runaway.py` re-bases these
# NAMES onto a caller's root instead of inventing a second layout — the same
# reason the module exists at all. `attempts/` holds one persisted attempt
# counter per directive; `dead-letter/` holds the terminal artifact of a
# directive whose budget was exhausted and which must never be dispatched again.
ATTEMPTS = FLEET_DIR / "attempts"
DEAD_LETTER = FLEET_DIR / "dead-letter"

#: Where an executor runner may live when it is NOT on the loop's own PATH.
#:
#: The loop is cron's child, so it inherits cron's minimal PATH — measured
#: 2026-09-14 (#733): the dispatcher could not spawn a single executor because the
#: per-user install directory ``~/.local/bin`` was not on it, and every directive
#: died with ``FileNotFoundError: 'claude'``. These are the documented per-user
#: install directories, derived from HOME rather than a machine-specific literal.
RUNNER_DIRS = (
    "~/.local/bin",
    "~/bin",
    "~/.claude/local",
    "~/.npm-global/bin",
    "~/.bun/bin",
    "~/.cargo/bin",
    "/usr/local/bin",
    "/opt/homebrew/bin",
)


def runner_search_path(env: dict[str, str] | None = None) -> list[str]:
    """Every directory a runner is looked for in, in the order it is searched.

    The ambient PATH comes first — a runner the principal deliberately put on PATH
    *is* the answer — then the per-user install directories that actually exist.
    A directory that does not exist is neither searched nor reported as searched,
    which is what keeps a "not found" message an honest list of where we looked.
    """
    source = os.environ if env is None else env
    entries = [entry for entry in source.get("PATH", "").split(os.pathsep) if entry]
    for candidate in RUNNER_DIRS:
        path = str(Path(candidate).expanduser())
        if path not in entries and Path(path).is_dir():
            entries.append(path)
    return entries


def runner_env(env: dict[str, str] | None = None) -> dict[str, str]:
    """The environment a fleet-spawned process gets: a PATH a runner can be found on.

    Passed explicitly by the spawn authority (``fleet/watchdog.py``) and by the
    executor (``fleet/terminal.py``), so the whole chain — watchdog, launcher,
    loop, executor — resolves the runner the same way instead of each inheriting
    whichever PATH happened to reach it.
    """
    base = dict(os.environ if env is None else env)
    base["PATH"] = os.pathsep.join(runner_search_path(base))
    return base


# The runaway guard's two runtime locations (issue #723). They are declared
# HERE, with every other runtime path, so `fleet/runaway.py` re-bases these
# NAMES onto a caller's root instead of inventing a second layout — the same
# reason the module exists at all. `attempts/` holds one persisted attempt
# counter per directive; `dead-letter/` holds the terminal artifact of a
# directive whose budget was exhausted and which must never be dispatched again.
ATTEMPTS = FLEET_DIR / "attempts"
DEAD_LETTER = FLEET_DIR / "dead-letter"

# The runaway alarm's LATCH (issue #728). `health/` holds the fleet's own
# health state and `alarm.json` is the latch artifact: raised when the queue-
# liveness facet (#728) measures a runaway, and kept raised — naming the
# directives and worktrees responsible — until a principal acknowledges it.
# Declared HERE for the same reason as ATTEMPTS/DEAD_LETTER: `fleet/health.py`
# re-bases these NAMES onto the caller's fleet directory rather than inventing a
# second layout.
HEALTH = FLEET_DIR / "health"
ALARM = HEALTH / "alarm.json"
