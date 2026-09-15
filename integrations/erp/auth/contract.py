"""The two contracts this lane *consumes* — and the seams it reaches them through.

This module exists so that the two cross-pillar dependencies of ERP-08 are named
in exactly one place, with one failure mode each, instead of being scattered as
imports that fail in whichever module happened to be imported first.

**What is consumed, and what is not.**

* ``identity/rbac`` — the platform's authorization contract (issue #12). Its
  README declares a *contract freeze*: "whichever lane lands a shared contract
  first owns it and the rest consume the field/function names, never the files".
  ERP-08 therefore **imports** ``rbac`` and never redefines a role, a
  permission, or the two-gate flow. ``identity/`` has no ``__init__.py`` (a
  later identity-phase lane owns adding it), so the package is reached by
  putting ``identity/`` on ``sys.path`` — the same arrangement ``rbac``'s own
  ``tests/conftest.py`` documents and the same idiom
  ``guardrails/controls/registry.py`` uses for its own sibling.

* ``guardrails/policy`` — the decision vocabulary (``BLOCK``/``WARN``/``LOG``,
  AO-GR-19 guard honesty). A field-level declaration this lane ships has to
  name its effect in *that* vocabulary or guardrails cannot enforce it, so the
  vocabulary is **read from the file that defines it**
  (``guardrails/policy/decision.py``) rather than re-typed. It is loaded by
  path on purpose: ``guardrails/policy/__init__.py`` re-exports the engine,
  the OPA backend and the startup gate, and a field-policy declaration has no
  business importing an OPA transport to learn three words.

**Why a failure here is CANNOT-ASSESS, never a fallback.** If either contract
cannot be read, this module raises ``contract-unavailable``. It never falls
back to a local copy of the vocabulary and it never defaults to "allow": a
permission check that cannot consult the permission contract has not decided
anything, and reporting that as a decision is the defect the tri-state contract
exists to prevent.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, Tuple

from .model import Refused

#: ``<repo>`` — from ``<repo>/integrations/erp/auth/contract.py``.
REPO_ROOT = Path(__file__).resolve().parents[3]

IDENTITY_ROOT = REPO_ROOT / "identity"
GUARDRAILS_ROOT = REPO_ROOT / "guardrails"
DECISION_MODULE_PATH = GUARDRAILS_ROOT / "policy" / "decision.py"


def rbac() -> Any:
    """The ``rbac`` contract module, or ``Refused('contract-unavailable')``.

    Imported lazily (and idempotently) so an environment without
    ``identity/`` reports an un-assessable contract rather than failing at
    package import time for every other consumer of this lane.
    """
    if str(IDENTITY_ROOT) not in sys.path:
        sys.path.insert(0, str(IDENTITY_ROOT))
    try:
        import rbac as _rbac  # noqa: PLC0415  (deliberately lazy, see docstring)
    except Exception as exc:  # pragma: no cover - exercised by the contract test
        raise Refused(
            "contract-unavailable",
            f"identity/rbac is unreadable ({type(exc).__name__}: {exc}); "
            f"expected it at {IDENTITY_ROOT}",
        ) from exc
    return _rbac


def decision_levels() -> Any:
    """The ``DecisionLevel`` enum from ``guardrails/policy/decision.py``.

    Loaded by path, without executing ``guardrails/policy/__init__.py`` — see
    the module docstring for why.

    The path is checked on **every** call, and the cache is keyed to the path it
    was loaded from. Caching without that check made the unreadable-contract
    behaviour depend on whether anything had already read the vocabulary — the
    same call would raise in a cold process and silently return a cached answer
    in a warm one. A control whose result depends on what ran before it is not a
    control, so the seam is order-independent by construction.
    """
    if not DECISION_MODULE_PATH.is_file():
        raise Refused(
            "contract-unavailable",
            f"guardrails/policy/decision.py is absent at {DECISION_MODULE_PATH}",
        )
    name = "erp_auth_policy_decision"
    cached = sys.modules.get(name)
    if cached is not None and getattr(cached, "_erp_source_path", None) == str(DECISION_MODULE_PATH):
        return cached.DecisionLevel
    try:
        spec = importlib.util.spec_from_file_location(name, DECISION_MODULE_PATH)
        if spec is None or spec.loader is None:  # pragma: no cover - defensive
            raise Refused("contract-unavailable", f"no loader for {DECISION_MODULE_PATH}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    except Refused:
        raise
    except Exception as exc:  # pragma: no cover - exercised by the contract test
        sys.modules.pop(name, None)
        raise Refused(
            "contract-unavailable",
            f"guardrails decision vocabulary is unreadable ({type(exc).__name__}: {exc})",
        ) from exc
    module._erp_source_path = str(DECISION_MODULE_PATH)
    return module.DecisionLevel


def effect_vocabulary() -> Tuple[str, ...]:
    """The declared effect words, **derived** from the contract, never typed.

    A hand-typed tuple here would be a second copy of guardrails' vocabulary
    and could drift from it silently; deriving it means a word guardrails adds
    or renames shows up in this lane's schemas and tests the moment it changes.
    """
    levels = decision_levels()
    return tuple(sorted(level.value for level in levels))


def is_effect(value: Any) -> bool:
    """True when ``value`` is one of the contract's declared effects."""
    return isinstance(value, str) and value in effect_vocabulary()
