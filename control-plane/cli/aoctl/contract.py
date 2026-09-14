"""The wire constants the console declares — read from it, never restated here.

Two strings of the console's contract are load-bearing for a client:

``os-session-token``
    the cookie the control family authenticates on
    (``portal/server/sso.py``; ADR-0025 D2.1 — the caller *is* the console
    session, and there is no second credential for a CLI);
``receipt: ``
    the marker after which a replay refusal carries the **original** receipt as
    canonical JSON (``portal/server/control_audit.py``; RC-4's documented replay
    vocabulary).

Both are read from the module that declares them. A literal copy here would be a
second declaration of the console's contract, free to drift from the first — and
drift in either of these two is invisible until an operator's command is refused
or a receipt is parsed wrong.

The import is lazy and cached, so ``--help`` and every local refusal work with no
console module importable at all; a genuinely unimportable contract is a named
refusal (``contract_unavailable``), never a traceback.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]


class ContractUnavailable(RuntimeError):
    """A console constant the CLI must read could not be imported."""


_CACHE: dict[str, Any] = {}


def _constant(module: str, name: str) -> str:
    """One constant, imported from the console module that declares it."""
    key = f"{module}.{name}"
    if key in _CACHE:
        return str(_CACHE[key])
    root = str(REPO_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        imported = importlib.import_module(f"portal.server.{module}")
    except ImportError as exc:
        raise ContractUnavailable(
            f"the console contract module portal.server.{module} could not be "
            f"imported, so {name} cannot be read: {exc}"
        ) from exc
    value = getattr(imported, name, None)
    if not isinstance(value, str) or not value:
        raise ContractUnavailable(
            f"portal.server.{module} declares no {name} for this CLI to read"
        )
    _CACHE[key] = value
    return value


def session_cookie_name() -> str:
    """The console session cookie the control family authenticates on."""
    return _constant("sso", "SESSION_COOKIE")


def receipt_marker() -> str:
    """The marker a replay refusal uses to hand the original receipt back."""
    return _constant("control_audit", "RECEIPT_MARKER")
