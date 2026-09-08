"""Test bootstrap for the consumer SDK suite (issue #41).

The suite runs fully offline.  This conftest puts ``control-plane/sdk/python``
on ``sys.path`` so the tests import the SDK as ``aosdk`` regardless of the
invocation directory (mirrors the repo's per-pillar sys.path convention).
"""

from __future__ import annotations

import os
import sys

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SDK_DIR = os.path.dirname(_TEST_DIR)
if _TEST_DIR not in sys.path:
    sys.path.insert(0, _TEST_DIR)
if _SDK_DIR not in sys.path:
    sys.path.insert(0, _SDK_DIR)
