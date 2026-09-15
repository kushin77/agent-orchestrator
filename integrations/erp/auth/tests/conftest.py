"""Pytest bootstrap: make ``integrations.erp.auth`` importable from any working dir.

``integrations/`` has no ``__init__.py`` (it is a namespace package), so the
package is importable once the *repository root* is on ``sys.path``. Pytest's
rootdir handling usually arranges that, but "usually" is not a property a suite
should rest on — the same reasoning as ``identity/rbac/tests/conftest.py``,
which inserts its own root rather than assuming one.
"""

import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
# integrations/erp/auth/tests -> integrations/erp/auth -> integrations/erp -> integrations -> <repo>
_repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(_here))))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)
