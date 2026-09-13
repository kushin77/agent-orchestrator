"""Pytest bootstrap for governance/remediation (issue #142).

Standalone modules, no package ``__init__.py`` — same convention as
governance/conformance, dispatch, sync and knowledge. Put the package
directory on sys.path so tests import the modules plainly.
"""

from __future__ import annotations

import os
import sys

PKG_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PKG_DIR not in sys.path:
    sys.path.insert(0, PKG_DIR)


class FakeFinding:
    """Duck-typed stand-in for governance.conformance.model.Finding, shaped
    identically (code, message, severity, subject, remediation) so
    generator.build_issue exercises the exact attribute contract it relies on
    without importing the sibling package (avoiding the model.py name clash
    the two packages share)."""

    def __init__(self, code, message, severity="error", subject="", remediation=""):
        self.code = code
        self.message = message
        self.severity = severity
        self.subject = subject
        self.remediation = remediation
