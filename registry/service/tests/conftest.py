"""Pytest bootstrap: make the ``service`` and ``events`` packages importable.

``registry/`` has no ``__init__.py`` (the registry lane may add one later), so
this inserts ``registry/`` - three levels above this file - at the front of
``sys.path``. Every test can then ``from service import ...`` and
``from events import ...`` no matter where pytest is invoked from.
"""

import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
# registry/service/tests -> registry/service -> registry
_registry_root = os.path.dirname(os.path.dirname(_here))
if _registry_root not in sys.path:
    # Append (not prepend): another lane owns a top-level module literally
    # named ``registry`` (registry/prompts/registry.py), so prepending the
    # bare ``registry/`` directory could shadow it as a namespace package.
    sys.path.append(_registry_root)

# Note: keep this conftest free of sibling constants - pytest shares the plain
# module name ``conftest`` across test directories, so ``from conftest import
# ...`` is ambiguous when two suites run in the same invocation.
