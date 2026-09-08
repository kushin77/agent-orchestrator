"""Test bootstrap for the model-agnostic instruction layer suite (issue #42).

The suite runs fully offline.  This conftest puts ``control-plane/instructions``
on ``sys.path`` so the tests import the package as ``aoi`` regardless of the
invocation directory (mirrors the repo's per-pillar sys.path convention).
"""

from __future__ import annotations

import os
import sys

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_INSTR_DIR = os.path.dirname(_TEST_DIR)  # control-plane/instructions
if _TEST_DIR not in sys.path:
    sys.path.insert(0, _TEST_DIR)
if _INSTR_DIR not in sys.path:
    sys.path.insert(0, _INSTR_DIR)
