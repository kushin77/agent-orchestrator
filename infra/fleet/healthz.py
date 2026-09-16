#!/usr/bin/env python3
"""healthz.py — the dev run's ``/healthz`` surface (issue #710, EPIC #706 D2).

The epic's acceptance is ``curl -fsS http://localhost:<port>/healthz``: a
container that is up must be able to SAY SO, and a container whose dev run
failed must not be able to. Three properties make this probe one that can fail,
rather than a formality:

* it answers from the **decision document the run actually wrote**
  (``.verify/dev-run/decision.json``), re-read on every request — not from a
  variable captured at startup, which would keep answering ``ok`` after the run
  it describes had been contradicted;
* a run that is still in progress, or whose verdict is not ``ok``, answers
  **503** with the reason — never 200 with a hopeful body;
* the URL is the only thing it serves: any other path is a 404. There is no
  directory listing and no file read behind this surface, so the published port
  exposes exactly one fact.

It is stdlib-only and binds the port it is told to. ``fleet/health.py`` remains
the fleet's own health SIGNAL (healthy/degraded/failing, rc 0/1/2) and this
module does not restate it: the dev run records that signal inside the decision
document, and this surface reports the document.

Usage:
    python3 infra/fleet/healthz.py status --decision <file>   # print the verdict (0/1/2)
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

OK, NOT_OK, CANNOT_ASSESS = 0, 1, 2

#: The only path this surface answers.
HEALTH_PATH = "/healthz"

#: The only media type it answers with.
CONTENT_TYPE = "application/json"


def load_decision(path: Path) -> dict | None:
    """The decision document, or ``None`` when it is absent or unreadable.

    Absent is not an error at the HTTP layer: it means the run has not finished
    writing its verdict yet, and the honest answer to ``/healthz`` is then "not
    ready", not "fine".
    """
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def decision_status(document: dict | None) -> tuple[int, dict]:
    """The HTTP status and body for a decision document (pure, so it is testable).

    200 only for a run that both reached a verdict of ``ok`` and left the state
    roots alone; 503 for anything else, naming the reason it refused.
    """
    if document is None:
        return 503, {
            "status": "not-ready",
            "reason": "the dev run has not written its decision document yet",
        }

    verdict = str(document.get("verdict", ""))
    changed = ((document.get("state") or {}).get("attributable_changes")) or []
    jobs = document.get("jobs") or []

    if verdict != "ok":
        return 503, {
            "status": "failed",
            "reason": document.get("reason") or f"the dev run's verdict is {verdict!r}",
            "verdict": verdict,
        }
    if changed:
        return 503, {
            "status": "failed",
            "reason": "a dispatched role wrote to a state root",
            "attributable_changes": changed,
        }
    return 200, {
        "status": "ok",
        "dry_run": True,
        "verdict": verdict,
        "jobs": len(jobs),
        "job_markers": [job.get("marker") for job in jobs],
        "dispatch": {
            "dry_run": len([job for job in jobs if job.get("disposition") == "dry-run"]),
            "not-dispatched": len([job for job in jobs if job.get("disposition") == "not-dispatched"]),
        },
        "state": document.get("state") or {},
        "schedule": document.get("schedule") or {},
    }


class _Handler(BaseHTTPRequestHandler):
    """One path, re-read from disk per request, answered with its own status code."""

    #: Set by :func:`serve` — the decision document and a server reference.
    decision_path: Path = Path("decision.json")
    server_version = "fleet-cron-dev-run/1.0"

    def _answer(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8") + b"\n"
        self.send_response(status)
        self.send_header("Content-Type", CONTENT_TYPE)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 — http.server's own naming
        if self.path.split("?", 1)[0] != HEALTH_PATH:
            self._answer(404, {"status": "not-found", "reason": f"only {HEALTH_PATH} is served"})
            return
        self._answer(*decision_status(load_decision(self.decision_path)))

    def do_HEAD(self) -> None:  # noqa: N802
        self.do_GET()

    def log_message(self, fmt: str, *args: object) -> None:
        """Quiet by default: the run's own log lines carry the decision."""
        return


def serve(port: int, decision_path: Path, ready: threading.Event | None = None) -> ThreadingHTTPServer:
    """Bind ``port`` and answer on a background thread; returns the server to stop."""
    handler = type("_BoundHandler", (_Handler,), {"decision_path": Path(decision_path)})
    server = ThreadingHTTPServer(("0.0.0.0", port), handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, name="healthz", daemon=True).start()
    if ready is not None:
        ready.set()
    return server


def cmd_status(args: argparse.Namespace) -> int:
    status, payload = decision_status(load_decision(Path(args.decision)))
    print(json.dumps({"http_status": status, **payload}, indent=2, sort_keys=True))
    return OK if status == 200 else NOT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="healthz", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    status = sub.add_parser("status", help="print what GET /healthz would answer (exit 0 when 200)")
    status.add_argument("--decision", required=True, help="the decision document to answer from")
    status.set_defaults(func=cmd_status)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
