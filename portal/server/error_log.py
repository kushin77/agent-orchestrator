"""portal.server.error_log — structured server-error sink (issue #2000).

---knowledge---
module_id: portal.server.error_log
system: portal
app: server
solution_class: enterprise
patterns: []
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [default_log_path, ErrorLog]
invariants: ""
gotchas: ""
related: ["#2000"]
do_not_duplicate: null
---knowledge---

Every uncaught exception ConsoleApplication.handle() catches is appended here
as one JSON line (path, method, exception type/message, UTC timestamp,
request id). Stdlib only — no new dependency for what is a few lines. Read
back through ``GET /api/errors`` (root_admin only, wired in app.py) so an
operator can triage without shelling into the box.

Path defaults to ``<repo_root>/.portal/error_log.jsonl`` (already outside
git — ``.portal/`` is the chat store's convention too) and is overridable via
``AO_PORTAL_ERROR_LOG`` for tests/tooling, same env-seam pattern the rest of
portal/server uses (config_flags.py, chat.py).

# ponytail: single flat file, append-only, no rotation — add a size-based
# rotation if this ever runs unattended for weeks.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional


def default_log_path(repo_root: Path) -> Path:
    override = os.environ.get("AO_PORTAL_ERROR_LOG")
    if override:
        return Path(override)
    return Path(repo_root) / ".portal" / "error_log.jsonl"


_LOCK = threading.Lock()


class ErrorLog:
    """Append/read the JSONL error sink."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def record(
        self,
        *,
        method: str,
        path: str,
        exc: BaseException,
        request_id: str = "",
    ) -> dict[str, Any]:
        entry = {
            "id": f"err_{uuid.uuid4().hex[:12]}",
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "method": method,
            "path": path,
            "requestId": request_id,
            "excType": type(exc).__name__,
            "message": str(exc),
        }
        with _LOCK:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, sort_keys=True) + "\n")
        return entry

    def tail(self, limit: int = 100) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        with _LOCK:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        rows: list[dict[str, Any]] = []
        for line in lines[-limit:]:
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
        return rows


def _demo() -> None:
    """Runnable self-check (issue #2000 acceptance): trigger + verify."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        log = ErrorLog(Path(tmp) / "error_log.jsonl")
        try:
            raise ValueError("deliberate self-check error")
        except ValueError as exc:
            entry = log.record(method="GET", path="/api/self-check", exc=exc)
        rows = log.tail()
        assert len(rows) == 1, rows
        assert rows[0]["id"] == entry["id"]
        assert rows[0]["excType"] == "ValueError"
        assert rows[0]["message"] == "deliberate self-check error"
        print("error_log self-check OK:", json.dumps(rows[0], sort_keys=True))


if __name__ == "__main__":
    _demo()
