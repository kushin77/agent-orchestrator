"""Pytest bootstrap: make the ``rbac`` package importable from any working dir.

``identity/`` has no ``__init__.py`` (a later identity-phase lane owns adding
one), so this inserts ``identity/`` - three levels above this file - at the
front of ``sys.path``. Every test can then ``from rbac import ...`` no matter
where pytest is invoked from.
"""

import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
# identity/rbac/tests -> identity/rbac -> identity
_identity_root = os.path.dirname(os.path.dirname(_here))
sys.path.insert(0, _identity_root)
