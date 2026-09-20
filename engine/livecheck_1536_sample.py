"""Scratch sample for the code-review-sme live check (issue #1536). Never merged."""

import re


def canonical_run_key(text):
    sanitized = re.sub(r"[^A-Za-z0-9._-]", "_", text)
    return sanitized or "agent"
