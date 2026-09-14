"""Test bootstrap for the C-suite instruction-layer suite (issue #643).

The suite runs fully offline.  This conftest puts ``control-plane/instructions``
on ``sys.path`` so the tests import ``aoi`` (the instruction layer under test)
and ``csuite`` (the derivation) regardless of the invocation directory — the
same convention as the sibling ``control-plane/instructions/tests`` suite.

The repository root is prepended so the derivation can read the workbook-1
cards and workbook-8 modules it consumes (read-only).
"""

from __future__ import annotations

import os
import sys

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_CSUITE_DIR = os.path.dirname(_TEST_DIR)          # control-plane/instructions/csuite
_INSTR_DIR = os.path.dirname(_CSUITE_DIR)         # control-plane/instructions
_REPO_ROOT = os.path.dirname(os.path.dirname(_INSTR_DIR))

if _CSUITE_DIR not in sys.path:
    sys.path.insert(0, _CSUITE_DIR)
if _INSTR_DIR not in sys.path:
    sys.path.insert(0, _INSTR_DIR)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
