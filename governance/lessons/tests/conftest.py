"""Pytest bootstrap + fixtures for the governance/lessons suite (issue #141).

The modules under ``governance/lessons/`` are standalone files with no package
``__init__.py`` (the convention shared with governance/merge, sync, dispatch,
knowledge and conformance). Putting the package directory on ``sys.path`` lets
the tests import them plainly.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest

PKG_DIR = Path(__file__).resolve().parent.parent
if str(PKG_DIR) not in sys.path:
    sys.path.insert(0, str(PKG_DIR))

# governance/lessons shares the bare basenames "model", "cli" and "checker"
# with sibling governance/* suites. Evict any stale sys.modules entry from an
# earlier-collected suite before this package's own bare imports (and this
# directory's test modules' bare imports), so they resolve against THIS
# package's files (issues #699, #702, #1042).
for _name in ("model", "cli", "checker"):
    sys.modules.pop(_name, None)

REPO_ROOT = PKG_DIR.parent.parent

from checker import check_ledger, parse_ledger_text  # noqa: E402
from model import RCA_REQUIRED_SECTIONS  # noqa: E402

TODAY = date(2026, 9, 15)
ARTIFACT = "governance/lessons/rca/RCA-0001-sample.md"

#: The board RECORD label: an issue that carries it *records* an incident, so
#: the ledger must hold an `INC-*` line whose origin is that issue (#766).
INCIDENT_LABEL = "incident"

#: The board AREA label that was once read as a record marker. It says where the
#: work lives — it can never say that an issue records an incident.
AREA_LABEL = "area:incident-response"


def rca_body() -> str:
    """A minimal artifact that satisfies every required template section."""
    blocks = ["# RCA-0001 — sample"]
    for section in RCA_REQUIRED_SECTIONS:
        blocks.append("%s\n\nText." % section)
    return "\n\n".join(blocks) + "\n"


def incident(number: int = 1, **overrides) -> dict:
    record = {
        "id": "INC-%04d" % number,
        "kind": "incident",
        "date": "2026-09-01",
        "summary": "a failure with a cause",
        "severity": "high",
        "class": "false-green",
        "origin": {"kind": "issue", "ref": "#100"},
        "status": "closed",
    }
    record.update(overrides)
    return record


def rca(number: int = 1, incident_id: str = "INC-0001", **overrides) -> dict:
    record = {
        "id": "RCA-%04d" % number,
        "kind": "rca",
        "date": "2026-09-02",
        "incident": incident_id,
        "origin": {"kind": "issue", "ref": "#100"},
        "artifact": ARTIFACT,
        "corrective_actions": ["CA-0001"],
        "status": "closed",
        "reviewed_at": "2026-09-10",
    }
    record.update(overrides)
    return record


def action(number: int = 1, rca_id: str = "RCA-0001", **overrides) -> dict:
    record = {
        "id": "CA-%04d" % number,
        "kind": "corrective-action",
        "date": "2026-09-03",
        "rca": rca_id,
        "action": "fix the mechanism",
        "status": "closed",
        "evidence": [{"kind": "commit", "ref": "abc1234"}],
    }
    record.update(overrides)
    return record


def lesson(number: int = 1, rca_id: str = "RCA-0001", **overrides) -> dict:
    record = {
        "id": "LESSON-%04d" % number,
        "kind": "lesson",
        "title": "a durable rule",
        "rca": rca_id,
        "date": "2026-09-04",
        "class": "enterprise",
        "status": "closed",
        "evidence": [{"kind": "commit", "ref": "abc1234"}],
    }
    record.update(overrides)
    return record


def suggestion(number: int = 1, rca_id: str = "RCA-0001", **overrides) -> dict:
    record = {
        "id": "SUGGEST-%04d" % number,
        "kind": "lesson",
        "title": "an open improvement",
        "rca": rca_id,
        "date": "2026-09-04",
        "class": "elite",
        "status": "open",
        "evidence": [{"kind": "issue", "ref": "#100"}],
        "owner": "governance lane",
        "remediation": "land the change",
    }
    record.update(overrides)
    return record


class StubProbe:
    """A :class:`checker.GitProbe` stand-in: no subprocess, explicit answers."""

    def __init__(
        self, *, available: bool = True, shallow: bool = False, narrow: bool = False,
        **options
    ):
        self.available = available
        self.shallow = shallow
        self.narrow = narrow
        self._tracked = options.get("tracked", {})
        self._commits = set(options.get("commits", ()))

    def tracked(self, relative_path: str):
        if not self.available:
            return None
        return self._tracked.get(relative_path, True)

    def commit_exists(self, sha: str):
        if not self.available:
            return None
        return sha in self._commits


def board_issue(number: int, *, state: str = "CLOSED", labels=(INCIDENT_LABEL,) ) -> dict:
    return {
        "number": number,
        "title": "an incident-labelled issue",
        "state": state,
        "milestone": "M24 - Enterprise Knowledge Index",
        "labels": list(labels),
        "parent": None,
        "blocked_by": [],
    }


def board(*issues) -> dict:
    return {issue["number"]: issue for issue in issues}


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """A repository-shaped root: the RCA artifact exists on disk."""
    artifact = tmp_path / ARTIFACT
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(rca_body(), encoding="utf-8")
    (tmp_path / ".board").mkdir(parents=True, exist_ok=True)
    return tmp_path


@pytest.fixture
def report_factory(root: Path):
    """Build a ledger from records and check it, with stubs by default."""

    def build(
        records, *, snapshot=None, probe=None, policy=None, strict=False, today=TODAY
    ):
        text = "\n".join(json.dumps(r, sort_keys=True) for r in records) + "\n"
        ledger = parse_ledger_text(text, root / "ledger.jsonl")
        return check_ledger(
            ledger,
            root=root,
            snapshot=snapshot,
            policy=policy,
            strict=strict,
            today=today,
            git=probe if probe is not None else StubProbe(commits={"abc1234"}),
            generated_at="2026-09-15T00:00:00Z",
        )

    return build


@pytest.fixture
def clean_records():
    """One complete incident: RCA, corrective action and lesson, all closed."""
    return [
        incident(1),
        rca(1),
        action(1),
        lesson(1),
    ]
