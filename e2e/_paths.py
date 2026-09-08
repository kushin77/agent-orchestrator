"""sys.path bootstrap for the e2e suite (issue #46).

The merged pillar modules import under two conventions (never repo-root
``package.subpackage`` for every pillar):

* pillar dirs with no ``__init__.py`` are exposed as **top-level** packages by
  inserting the pillar dir itself on ``sys.path`` (their own lane convention):
  ``gateway/`` -> ``from proxy import ...``, ``identity/`` -> ``from rbac
  import ...``, ``registry/`` -> ``from service import ...``,
  ``guardrails/`` -> ``from dlp import ...``.
* telemetry + engine + control-plane are reached through the **repo-root
  PEP-420 namespace** (``telemetry.metering``, ``engine.core``,
  ``portal.server``) by inserting the repo root on ``sys.path``.

``telemetry/`` is deliberately NOT added as a top-level root: gateway/finops
ships a plain top-level module literally named ``metering`` (no ``__init__``)
and ``gateway/proxy/wiring.py`` imports it as ``import metering`` after
appending ``gateway/finops`` to ``sys.path``.  Adding ``telemetry/``
top-level would shadow it with ``telemetry/metering`` (a different module
with a different contract) and break ``build_real_gateway``.
"""

from __future__ import annotations

import os
import sys

# e2e/_paths.py -> e2e/ -> repo root
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Pillar dirs exposed as top-level packages (their own merged-lane convention).
_TOP_LEVEL_ROOTS = ("gateway", "identity", "registry", "guardrails")

_done = False


def ensure_sys_paths() -> None:
    """Idempotently make every merged pillar module importable (front)."""
    global _done
    if _done:
        return
    roots = [REPO_ROOT] + [os.path.join(REPO_ROOT, rel) for rel in _TOP_LEVEL_ROOTS]
    for path in roots:
        if path not in sys.path:
            sys.path.insert(0, path)
    _done = True
