"""Pytest bootstrap + fixtures for the governance/conformance suite (issue #140).

The modules under governance/conformance/ are standalone files with no package
``__init__.py`` (the convention shared with governance/merge, sync, dispatch and
knowledge). Putting the package directory on sys.path lets the tests import them
plainly.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

PKG_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PKG_DIR not in sys.path:
    sys.path.insert(0, PKG_DIR)

SAMPLE_POLICY = """\
schema: cmr.conformance/policy-v1
ladder:
  - template
  - class
  - pattern
  - enterprise
  - faang
  - elite
mandates:
  iac:
    infra_paths:
      - infra/
required:
  - class
  - type
  - priority
  - area
expectations:
  enterprise:
    - gdc
  elite:
    - gdc
    - pillar
prefixed:
  - class
  - type
  - priority
  - area
  - pillar
  - gdc
"""


def issue(
    number,
    *,
    labels=("class:enterprise", "type:feature", "priority:P1", "area:board"),
    milestone="M24 - Enterprise Knowledge Index",
    state="OPEN",
    title="sample",
):
    """One board-snapshot issue entry."""
    return {
        "number": number,
        "title": title,
        "state": state,
        "milestone": milestone,
        "labels": list(labels),
        "parent": None,
        "blocked_by": [],
    }


@pytest.fixture
def policy_text() -> str:
    return SAMPLE_POLICY


@pytest.fixture
def policy_file(tmp_path: Path, policy_text: str) -> Path:
    path = tmp_path / "policy.yaml"
    path.write_text(policy_text, encoding="utf-8")
    return path


@pytest.fixture
def policy(policy_file: Path):
    from checker import load_policy

    return load_policy(policy_file)


@pytest.fixture
def snapshot_file(tmp_path: Path) -> Path:
    path = tmp_path / "snapshot.json"
    path.write_text(
        json.dumps(
            {
                "generated_at": "2026-09-13T00:00:00Z",
                "source": "acme/widgets",
                "issues": [
                    issue(1, labels=("class:enterprise", "type:feature",
                                     "priority:P1", "area:board", "gdc:enterprise")),
                    issue(2, labels=("class:elite", "type:governance",
                                     "priority:P0", "area:board",
                                     "gdc:enterprise", "pillar:governance")),
                    issue(3, milestone="", labels=()),
                    issue(4, state="CLOSED", labels=("class:elite",)),
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return path
