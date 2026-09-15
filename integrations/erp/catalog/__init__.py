"""The ERP module's declaration surface (EPIC #645, issue #646).

``integrations/erp/`` is a *declaration*: a manifest that admits the module, a
README that states its contract, and a catalogue that carries its domain facts.
This package is the one place that states what those files must be, so a
reviewer reads one module and the gate runs one implementation.

Importing this package puts the repository root on ``sys.path`` (the module
reads the repository's own schema validator, ``governance/modules/schema.py``,
rather than vendoring a second one). The root is derived from this file, never
from the environment, so the module resolves the same way from the CLI, from the
gate and from the suite.
"""

from __future__ import annotations

import sys
from pathlib import Path

#: This file is <root>/integrations/erp/catalog/__init__.py.
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
