#!/usr/bin/env python3
"""Namespaced fleet runtime — the single source of runtime state location + identity.

WHY this exists (EPIC #360, issue #361): every fleet module used to build its own
``ROOT / ".fleet"`` paths, so two fleets on one checkout could only share one
runtime directory — their locks, mailboxes and heartbeats collided, and a second
"sister fleet" (e.g. a tmux/dashboard look-and-feel fleet pointed at a different
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
