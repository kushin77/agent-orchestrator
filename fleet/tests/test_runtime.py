"""Namespaced fleet runtime — two fleets, one checkout, one claim ledger (#361).

WHY this module exists (EPIC #360): the fleet used to build every runtime path
from ``ROOT / ".fleet"``, so a second "sister fleet" had nowhere to run alongside
the first. ``fleet/runtime.py`` is now the single source of ``FLEET_DIR`` and
``SESSION`` (from ``AO_FLEET_DIR`` / ``AO_FLEET_SESSION``), and every fleet module
derives its runtime paths from it. These tests prove the contract by measuring it
in a fresh interpreter, so a change that silently re-hardcodes ``.fleet`` or moves
the claim ledger off the repo root fails here.

The claim ledger is the one thing two fleets SHARE: it lives at ``.board/`` and
is deliberately not namespaced, so two fleets cannot claim the same issue.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import console
import control
import runtime

REPO = Path(__file__).resolve().parents[2]

#: A tiny program that prints the derived runtime paths, run in a fresh
#: interpreter so the module-level constants are re-derived under `env`.
DERIVE = (
    "import json, sys\n"
    "sys.path.insert(0, 'fleet')\n"
    "import runtime, channel, singleton, prune\n"
    "print(json.dumps({\n"
    "  'fleet_dir': str(runtime.FLEET_DIR),\n"
    "  'session': runtime.SESSION,\n"
    "  'inbox': str(channel.INBOX),\n"
    "  'lock': str(singleton.FLEET),\n"
    "  'heartbeat': str(channel.HEARTBEAT),\n"
    "  'ledger': str(prune.CLAIMS_LEDGER),\n"
    "}))\n"
)


def _derive(env: dict[str, str] | None = None) -> dict[str, str]:
    result = subprocess.run(
        [sys.executable, "-c", DERIVE],
        cwd=REPO,
        env={**os.environ, **(env or {})},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    return json.loads(result.stdout)


def test_the_default_runtime_is_byte_identical_to_today():
    """With the env vars unset, every path is the historical .fleet/ tree."""
    derived = _derive()
    assert derived["fleet_dir"] == str(REPO / ".fleet")
    assert derived["session"] == "fleet"
    assert derived["inbox"] == str(REPO / ".fleet" / "inbox")
    assert derived["lock"] == str(REPO / ".fleet")
    assert derived["heartbeat"] == str(REPO / ".fleet" / "sister.heartbeat.json")


def test_ao_fleet_dir_is_resolved_absolute():
    assert runtime.FLEET_DIR.is_absolute()


def test_two_fleets_hold_independent_mailboxes_locks_and_heartbeats():
    """Different AO_FLEET_DIR -> disjoint runtime trees; the loops cannot collide."""
    a = _derive({"AO_FLEET_DIR": str(REPO / ".fleet-a")})
    b = _derive({"AO_FLEET_DIR": str(REPO / ".fleet-b")})
    assert a["fleet_dir"] == str(REPO / ".fleet-a")
    assert b["fleet_dir"] == str(REPO / ".fleet-b")
    assert a["inbox"] != b["inbox"]
    assert a["lock"] != b["lock"]
    assert a["heartbeat"] != b["heartbeat"]


def test_the_claim_ledger_is_shared_across_fleets():
    """The ledger is NOT namespaced: two fleets must never claim the same issue."""
    assert _derive({})["ledger"] == str(REPO / ".board" / "claims.jsonl")
    assert _derive({"AO_FLEET_DIR": str(REPO / ".fleet-x")})["ledger"] == str(
        REPO / ".board" / "claims.jsonl"
    )


def test_ao_fleet_session_names_the_session():
    derived = _derive({"AO_FLEET_SESSION": "fleet-session"})
    assert derived["session"] == "fleet-session"


def test_session_name_changes_the_tmux_session_and_dashboard_header(monkeypatch):
    """AO_FLEET_SESSION names the tmux session and the dashboard header line."""
    monkeypatch.setattr(control, "SESSION", "fleet-session")
    layout = " ".join(" ".join(argv) for argv in control.live_layout())
    assert "new-session -d -s fleet-session" in layout
    assert "select-window -t fleet-session:dashboard" in layout

    monkeypatch.setattr(console, "SESSION", "fleet-session")
    header = console.header_section(
        {"repo": "kushin77/agent-orchestrator", "head": "abc", "now": "x", "uptime": "up 1s"}
    )
    assert "fleet-session session" in header
    assert "tmux attach -t fleet-session" in header
