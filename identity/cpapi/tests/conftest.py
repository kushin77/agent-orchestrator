"""Pytest bootstrap: make ``cpapi`` (and the real merged pillars) importable.

``identity/`` has no ``__init__.py`` (a later identity-phase lane owns adding
one), so this inserts ``identity/`` — three levels above this file — at the
front of ``sys.path``. Every test can then ``from cpapi import ...`` no matter
where pytest is invoked from. The real-rbac wiring tests additionally insert
the pillar roots through ``cpapi.wiring.add_pillar_roots``.
"""

import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
# identity/cpapi/tests -> identity/cpapi -> identity
_identity_root = os.path.dirname(os.path.dirname(_here))
if _identity_root not in sys.path:
    sys.path.insert(0, _identity_root)
