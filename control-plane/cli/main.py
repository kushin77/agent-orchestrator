#!/usr/bin/env python3
"""``ao-control`` — the remote command center CLI (issue #556, RC-5 of #551).

    python3 control-plane/cli/main.py status
    python3 control-plane/cli/main.py --dry-run pause
    python3 control-plane/cli/main.py --json verbs
    python3 control-plane/cli/main.py --confirm fleet.override override <args…>

This file is the entry point and nothing else: it puts the CLI's own directory on
``sys.path`` (``control-plane`` is not an importable package name — it carries a
hyphen) and calls ``aoctl.cli.main``, whose module docstring states the
invocation contract. Flags go **before** the verb; everything after the verb is
forwarded verbatim to the lever's own CLI, which owns the meaning of its
arguments.

Run ``--help`` for the verb table: every verb and the **one declared command id**
from RC-2's registry it speaks. The CLI's own suite, its README and the module
docstrings carry the rest.
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:  # `aoctl` lives beside this file, not on a package path
    sys.path.insert(0, str(HERE))

from aoctl.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
