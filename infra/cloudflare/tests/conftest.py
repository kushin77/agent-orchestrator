"""Pytest bootstrap for the `infra/cloudflare` suite (issue #771).

``infra/`` carries no ``__init__.py`` (it is a PEP-420 namespace, like
``infra/rollout``), so ``infra.cloudflare.*`` is reached from the repo root,
which is inserted at the front of ``sys.path``. This tests directory is
deliberately NOT added to ``sys.path``, so the plain module name ``conftest``
stays collision-free when sibling suites are run in one session.
"""

from __future__ import annotations

import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))  # .../infra/cloudflare/tests
_cloudflare_root = os.path.dirname(_here)  # .../infra/cloudflare
_infra_root = os.path.dirname(_cloudflare_root)  # .../infra
_repo_root = os.path.dirname(_infra_root)  # repo root
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)
