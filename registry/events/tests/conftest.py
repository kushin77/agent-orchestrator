"""Pytest bootstrap: make the ``events`` package importable from any cwd.

``registry/`` has no ``__init__.py`` (the registry lane may add one later), so
this inserts ``registry/`` - three levels above this file - at the front of
``sys.path``. Every test can then ``from events import ...`` no matter where
pytest is invoked from.
"""

import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
# registry/events/tests -> registry/events -> registry
_registry_root = os.path.dirname(os.path.dirname(_here))
if _registry_root not in sys.path:
    # Append (not prepend): another lane owns a top-level module literally
    # named ``registry`` (registry/prompts/registry.py), so prepending the
    # bare ``registry/`` directory could shadow it as a namespace package.
    sys.path.append(_registry_root)
