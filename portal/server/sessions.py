"""portal.server.sessions — the cross-engine Sessions view adapter (issue #1563).

WHY this exists: `.fleet/claims/*` + `.fleet/lanes/*` (the live fleet CLI's own
claim/lane files), `.board/claims.jsonl` (the same claim log `livestore.py`
already joins for the fleet board) and `~/.deepseek-agent` (DeepSeek's own
session store) each describe agent/session activity for their own consumer,
and nothing joins them into one operator view (SPOG-REVIEW-2026-09-20.md §4).

This adapter is read-only and joins nothing it did not read from those three
roots — it invents no row. **Honesty rule:** a root that does not exist on
this checkout is named in ``missing`` rather than silently dropped, and every
engine an unreadable/absent root would have covered renders **"not
reporting"** instead of being omitted from the row set. Hermes and Paperclip
are two more engine roots (``.hermes-agent``, ``.paperclip-agent``) declared
the same way — they contribute rows only when their root exists; with no root
they are neither faked nor listed as "not reporting" (they are not one of the
three roots this issue's Goal names), they are simply absent.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from portal.server.config_flags import SESSIONS_SURFACE, surface_enabled

SCHEMA = "ao.portal-sessions/v1"

#: The three roots issue #1563's Goal names, repo-root relative. Each maps to
#: one engine label rendered "not reporting" when its root is absent.
PRIMARY_ROOTS: dict[str, str] = {
    "claude-fleet": ".fleet",
    "claude-board": ".board",
    "deepseek": ".deepseek-agent",
}

#: Extra engine roots that contribute rows only when present (never a "not
#: reporting" row of their own — they are not among the three primary roots).
OPTIONAL_ROOTS: dict[str, str] = {
    "hermes": ".hermes-agent",
    "paperclip": ".paperclip-agent",
}


def _age(at: Any, *, now: Optional[datetime] = None) -> Optional[str]:
    """A human age (``"3m"``, ``"2h"``, ``"5d"``) from an ISO-8601 timestamp.

    ``None`` for anything unparsable — an honest "unknown age", never a
    fabricated ``0m``.
    """
    if not isinstance(at, str) or not at:
        return None
    try:
        stamp = datetime.fromisoformat(at.replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    clock = now if now is not None else datetime.now(timezone.utc)
    seconds = max(0, int((clock - stamp).total_seconds()))
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def _row(
    *,
    engine: str,
    issue: Any = None,
    branch: str = "",
    state: str = "",
    agent: str = "",
    lane: str = "",
    at: Any = None,
    source: str,
) -> dict[str, Any]:
    return {
        "engine": engine,
        "issue": issue,
        "branch": branch or "",
        "state": state or "",
        "agent": agent or "",
        "lane": lane or "",
        "age": _age(at),
        "source": source,
    }


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _fleet_rows(root: Path) -> list[dict[str, Any]]:
    """``.fleet/claims/*.json`` — one file per live claim (fail-closed per file)."""
    rows: list[dict[str, Any]] = []
    claims_dir = root / "claims"
    if not claims_dir.is_dir():
        return rows
    for path in sorted(claims_dir.glob("*.json")):
        doc = _read_json(path)
        if not isinstance(doc, dict):
            continue
        rows.append(
            _row(
                engine="claude-fleet",
                issue=doc.get("issue"),
                branch=doc.get("branch", ""),
                state=doc.get("state", "claimed"),
                agent=doc.get("agent", ""),
                lane=doc.get("lane", ""),
                at=doc.get("claimed_at") or doc.get("at"),
                source=f".fleet/claims/{path.name}",
            )
        )
    return rows


def _board_rows(root: Path) -> list[dict[str, Any]]:
    """``.board/claims.jsonl`` — latest event per (issue, agent), unreleased only."""
    claims_path = root / "claims.jsonl"
    if not claims_path.is_file():
        return []
    latest: dict[tuple[Any, str], dict[str, Any]] = {}
    with open(claims_path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            key = (event.get("issue"), str(event.get("agent") or ""))
            latest[key] = event
    rows: list[dict[str, Any]] = []
    for (issue, agent), event in sorted(latest.items(), key=lambda kv: str(kv[0])):
        if event.get("event") != "claim":
            continue  # released/reaped — no longer a live session
        rows.append(
            _row(
                engine="claude-board",
                issue=issue,
                branch="",  # the claim log carries no branch field (honest blank)
                state="claimed",
                agent=agent,
                lane=event.get("lane", ""),
                at=event.get("at"),
                source=".board/claims.jsonl",
            )
        )
    return rows


def _deepseek_rows(root: Path) -> list[dict[str, Any]]:
    """``.deepseek-agent/sessions/*.json`` — one file per session, if published.

    DeepSeek's own tooling has not yet published a documented session-file
    shape here (SPOG-REVIEW §4's own flagged, unenumerated gap) — a root that
    exists but carries no ``sessions/`` directory is a real, empty answer, not
    "not reporting" (the root IS readable; it simply has no session rows yet).
    """
    rows: list[dict[str, Any]] = []
    sessions_dir = root / "sessions"
    if not sessions_dir.is_dir():
        return rows
    for path in sorted(sessions_dir.glob("*.json")):
        doc = _read_json(path)
        if not isinstance(doc, dict):
            continue
        rows.append(
            _row(
                engine="deepseek",
                issue=doc.get("issue"),
                branch=doc.get("branch", ""),
                state=doc.get("state", ""),
                agent=doc.get("agent", ""),
                lane=doc.get("lane", ""),
                at=doc.get("at"),
                source=f".deepseek-agent/sessions/{path.name}",
            )
        )
    return rows


def _optional_rows(engine: str, root: Path) -> list[dict[str, Any]]:
    """A generic reader for an optional engine root: same ``sessions/*.json`` shape."""
    rows: list[dict[str, Any]] = []
    sessions_dir = root / "sessions"
    if not sessions_dir.is_dir():
        return rows
    for path in sorted(sessions_dir.glob("*.json")):
        doc = _read_json(path)
        if not isinstance(doc, dict):
            continue
        rows.append(
            _row(
                engine=engine,
                issue=doc.get("issue"),
                branch=doc.get("branch", ""),
                state=doc.get("state", ""),
                agent=doc.get("agent", ""),
                lane=doc.get("lane", ""),
                at=doc.get("at"),
                source=f"{root.name}/sessions/{path.name}",
            )
        )
    return rows


class SessionsView:
    """Joins ``.fleet``, ``.board`` and ``.deepseek-agent`` into one row set.

    Feature-flag-gated OFF (GR-5), declared in the portal's own
    ``portal/config/feature-flags.yaml`` beside the other workbook-11 views.
    Every root is read fresh on each call — no cache, no second store.
    """

    def __init__(
        self,
        *,
        repo_root: Path | str,
        enabled: Optional[bool] = None,
        config_path: Optional[Path | str] = None,
        roots: Optional[dict[str, Path]] = None,
    ) -> None:
        self.repo_root = Path(repo_root)
        self.config_path = Path(config_path) if config_path is not None else None
        if enabled is None:
            enabled = surface_enabled(
                self.repo_root, config_path=self.config_path, surface=SESSIONS_SURFACE
            )
        self.enabled = bool(enabled)
        # A test injects fixture roots by name; a real app resolves them
        # relative to the checkout.
        self._roots = dict(roots or {})

    def _root(self, name: str, relative: str) -> Path:
        override = self._roots.get(name)
        return Path(override) if override is not None else self.repo_root / relative

    def sessions(self) -> dict[str, Any]:
        rows: list[dict[str, Any]] = []
        missing: list[str] = []

        fleet_root = self._root("claude-fleet", PRIMARY_ROOTS["claude-fleet"])
        if fleet_root.is_dir():
            rows.extend(_fleet_rows(fleet_root))
        else:
            missing.append("claude-fleet")

        board_root = self._root("claude-board", PRIMARY_ROOTS["claude-board"])
        if board_root.is_dir():
            rows.extend(_board_rows(board_root))
        else:
            missing.append("claude-board")

        deepseek_root = self._root("deepseek", PRIMARY_ROOTS["deepseek"])
        if deepseek_root.is_dir():
            rows.extend(_deepseek_rows(deepseek_root))
        else:
            missing.append("deepseek")

        for engine, relative in OPTIONAL_ROOTS.items():
            root = self._root(engine, relative)
            if root.is_dir():
                rows.extend(_optional_rows(engine, root))

        not_reporting = sorted(missing)
        return {
            "schema": SCHEMA,
            "sessions": rows,
            "missing": not_reporting,
        }
