#!/usr/bin/env python3
"""healthz.py — the dev run's health + metrics surface (issue #710 D2, #712 D4).

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

D4 (#712, EPIC #706) adds two things ON TOP of that surface, both read from the
SAME decision document rather than a second source of truth:

* **``/health`` is an alias of ``/healthz``** — the peer contract this image is
  ported against (shared-services ``docker/cronrunner``) names its probe
  ``/health``, and the Docker ``HEALTHCHECK`` this issue adds curls that name;
  this repo's own modules (``env_contract.py``, ``dev_run.py``,
  ``inventory.yaml``) already say ``/healthz``, so BOTH paths answer the same
  body rather than picking one and silently breaking the other's caller.
* **``/metrics`` is a Prometheus-format** rendering of the same document: job
  presence, the document's own age, and whether ``/health`` would currently
  answer 200 — so a scrape sees exactly what the probe sees, never a second
  computation of it.
* **staleness is now a FAILURE, not merely a verdict.** #712's own acceptance is
  that ``/healthz`` "fails on a stale heartbeat (not merely 'process exists')":
  a decision document whose ``finished_at`` has not advanced in
  ``stale_after_seconds`` answers 503 even when its stored verdict was ``ok`` —
  a container that stopped updating its own evidence must not keep answering
  "fine" on the strength of the last good answer it happened to write.
* **all expected jobs must be present, and the expected set is DERIVED, never
  written down here.** A document with an "ok" verdict but a ``jobs`` list
  missing an enabled rung (a role that crashed before it could even report
  ``not-dispatched``) answers 503 **naming the missing marker**, rather than 200
  for a partial run. The expected set comes from the schedule's own declaration
  (``config/fleet-jobs.json``) read **through its single owner**
  (``fleet/cron.py``'s ``load_manifest`` + ``enabled_jobs``), so the surface and
  the installer cannot drift — which is exactly how the previous literal
  (``EXPECTED_JOBS = 3``) came to disagree with a four-rung schedule and answer
  200 for a document that was missing the fourth rung. An unreadable or empty
  declaration FAILS CLOSED: the surface refuses, it never falls back to a count
  it cannot justify.

It is stdlib-only and binds the port it is told to. ``fleet/health.py`` remains
the fleet's own health SIGNAL (healthy/degraded/failing, rc 0/1/2) and this
module does not restate it: the dev run records that signal inside the decision
document, and this surface reports the document.

Usage:
    python3 infra/fleet/healthz.py status --decision <file>   # print the verdict (0/1/2)
    python3 infra/fleet/healthz.py metrics --decision <file>  # print the Prometheus text
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

OK, NOT_OK, CANNOT_ASSESS = 0, 1, 2

#: The paths this surface answers identically — ``/healthz`` is this repo's own
#: convention (env_contract.py, dev_run.py, inventory.yaml all name it);
#: ``/health`` is the peer contract's name (shared-services docker/cronrunner)
#: and what the Dockerfile's HEALTHCHECK curls. Both answer the same body.
HEALTH_PATHS = ("/healthz", "/health")
HEALTH_PATH = HEALTH_PATHS[0]

#: The metrics surface, Prometheus text exposition format.
METRICS_PATH = "/metrics"

#: The only media type the health paths answer with.
CONTENT_TYPE = "application/json"

#: The Prometheus exposition media type.
METRICS_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"

#: The repository root this module lives in, used only to locate the schedule's
#: declaration: ``infra/fleet/healthz.py`` -> ``<repo>``. The same shape
#: ``infra/fleet/parity.py`` uses to reach the identical markers.
_REPO_ROOT = Path(__file__).resolve().parents[2]

#: ``None`` in this cache means "the declaration could not be trusted"; the
#: sentinel is distinct from ``()`` (an empty declaration), and both refuse.
#: A `list` rather than `functools.lru_cache` so the one entry can be cleared by
#: a test or a gate that has just rewritten the manifest.
_DECLARED_CACHE: list[tuple[str, ...] | None] = []


def declared_enabled_markers() -> tuple[str, ...] | None:
    """The ENABLED schedule markers — or ``None`` when the declaration cannot be trusted.

    Read from ``config/fleet-jobs.json`` **through** ``fleet/cron.py``:
    ``cron.load_manifest()`` and ``cron.enabled_jobs()`` are the installer's own
    reader and filter, so enabling or disabling a job in the manifest moves this
    set and the installed crontab together. There is deliberately no second copy
    of the schedule here — a literal set (or a literal count) is exactly what
    drifted: ``EXPECTED_JOBS = 3`` kept answering 200 for a document that was
    missing the fourth enabled rung (``ao-fleet-reap``).

    ``None`` is the FAIL-CLOSED answer for a declaration that cannot be trusted at
    all (unreadable, malformed, or failing the renderer's own contract). A
    declaration that IS readable and valid but enables nothing yields the EMPTY
    set, which the caller also refuses (503) — with its own reason, because "the
    schedule owes nothing" and "I could not read the schedule" are different
    facts and collapsing them would lose the one that is actionable.
    """
    if _DECLARED_CACHE:
        return _DECLARED_CACHE[0]

    known: tuple[str, ...] | None = None
    try:
        fleet_dir = str(_REPO_ROOT / "fleet")
        if fleet_dir not in sys.path:
            sys.path.insert(0, fleet_dir)
        import cron  # noqa: PLC0415 — lazy by design: this module must stay
        # importable, and callable, without the schedule on hand. A failure here
        # is a refusal (None), never a crash.

        manifest = cron.load_manifest()
        # A manifest that does not meet the renderer's own contract is refused
        # rather than read for whatever it happens to contain.
        if not cron.validate_manifest(manifest):
            known = tuple(str(job["marker"]) for job in cron.enabled_jobs(manifest))
    except (OSError, ValueError, TypeError, KeyError, AttributeError, ImportError):
        known = None

    _DECLARED_CACHE.append(known)
    return known


def reset_declared_cache() -> None:
    """Forget the derived declaration (a test or gate that has changed the manifest)."""
    _DECLARED_CACHE.clear()

#: A decision document whose `finished_at` has not advanced in this long is
#: stale: the run stopped reporting, and #712's acceptance is that this must
#: flip the probe unhealthy rather than keep answering the last good body.
#: Chosen as 3x the dev-run's own default poll cadence (`AO_FLEET_CRON_INTERVAL`
#: default is minutes, this surface is polled far more often by a HEALTHCHECK),
#: generous enough that a slow role does not flap the probe.
DEFAULT_STALE_AFTER_SECONDS = 900


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


def _age_seconds(document: dict, now: float) -> float | None:
    """Seconds since ``finished_at`` (or ``started_at`` while a run is in flight)."""
    stamp = document.get("finished_at") or document.get("started_at")
    if not stamp:
        return None
    try:
        parsed = datetime.strptime(str(stamp), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return max(0.0, now - parsed.timestamp())


def decision_status(
    document: dict | None,
    now: float | None = None,
    stale_after_seconds: float = DEFAULT_STALE_AFTER_SECONDS,
    expected_markers: tuple[str, ...] | None = None,
) -> tuple[int, dict]:
    """The HTTP status and body for a decision document (pure, so it is testable).

    200 only for a run that reached a verdict of ``ok``, left the state roots
    alone, reported **every enabled rung of the schedule**, and wrote that
    verdict within ``stale_after_seconds`` of ``now``; 503 for anything else,
    naming the reason it refused. A container that is up but whose evidence
    stopped advancing must fail this probe — "the process exists" is
    deliberately not enough (#712).

    ``expected_markers`` is the expected job set. ``None`` (the default) means
    *derive it* from the schedule's own declaration through ``fleet/cron.py``;
    a tuple is used as given, which is the seam the tests and the covering gate
    drive with. An underivable or EMPTY set is refused (503 ``cannot-assess``),
    never silently treated as "nothing expected": the previous version compared
    a literal count and so answered 200 for a document missing the fourth rung.

    THE STALENESS CHECK IS SKIPPED WHILE A RUN IS STILL RUNNING. ``dev_run.py``
    (D2, #710) writes its decision document ONCE, after the dispatch, and then
    parks on ``/healthz`` for as long as the container lives — its own ``stop``
    field stays ``{"clean": None, "note": "still running"}`` until a SIGTERM
    actually stops it; ``finished_at`` never advances again. Enforcing
    ``stale_after_seconds`` against that timestamp would flip a container doing
    exactly what D2 designed it to do from 200 to 503 after the bound elapses —
    a false red (this repo's own precedent, `dc3de7f fix(gates): the false red
    in check-isolation-landed`), and worse under a `restart: unless-stopped`
    policy, which would then restart a healthy container forever. So staleness
    is judged against the RUN's own liveness claim, not merely a clock: a
    document whose ``stop.clean`` is ``None`` is still being actively served by
    the process that wrote it, and its age is not evidence of anything having
    gone stale. Once that process stops (``stop.clean`` is ``True``/``False``),
    or for a document with no ``stop`` field at all (a production writer that
    does not use dev_run.py's shape), the bound applies normally.
    """
    if document is None:
        return 503, {
            "status": "not-ready",
            "reason": "the dev run has not written its decision document yet",
        }

    now = time.time() if now is None else now
    verdict = str(document.get("verdict", ""))
    changed = ((document.get("state") or {}).get("attributable_changes")) or []
    jobs = document.get("jobs") or []
    still_running = "stop" in document and (document.get("stop") or {}).get("clean") is None
    age = None if still_running else _age_seconds(document, now)

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

    expected = declared_enabled_markers() if expected_markers is None else tuple(expected_markers)
    if not expected:
        return 503, {
            "status": "cannot-assess",
            "reason": (
                "the schedule declaration (config/fleet-jobs.json, read through fleet/cron.py) "
                "could not be read, so the expected job set is unknown"
                if expected is None
                else "the schedule declaration names no enabled job, so nothing can be certified"
            ),
            "expected_jobs": None if expected is None else 0,
        }

    present = [str(job.get("marker") or "") for job in jobs if isinstance(job, dict)]
    missing = [marker for marker in expected if marker not in present]
    if missing:
        return 503, {
            "status": "incomplete",
            "reason": (
                f"{len(missing)} of {len(expected)} expected job(s) are missing from the "
                f"decision document: {', '.join(missing)}"
            ),
            "jobs": len(jobs),
            "expected_jobs": len(expected),
            "missing_jobs": missing,
        }
    if not still_running:
        if age is None:
            return 503, {
                "status": "failed",
                "reason": "the decision document has no timestamp to measure freshness from",
            }
        if age > stale_after_seconds:
            return 503, {
                "status": "stale",
                "reason": f"the decision document is {int(age)}s old, past the {int(stale_after_seconds)}s staleness bound",
                "age_seconds": int(age),
                "stale_after_seconds": int(stale_after_seconds),
            }
    return 200, {
        "status": "ok",
        "dry_run": True,
        "verdict": verdict,
        "jobs": len(jobs),
        "expected_jobs": len(expected),
        "expected_markers": list(expected),
        "job_markers": [job.get("marker") for job in jobs],
        "age_seconds": (int(age) if age is not None else None),
        "still_running": still_running,
        "dispatch": {
            "dry_run": len([job for job in jobs if job.get("disposition") == "dry-run"]),
            "not-dispatched": len([job for job in jobs if job.get("disposition") == "not-dispatched"]),
        },
        "state": document.get("state") or {},
        "schedule": document.get("schedule") or {},
    }


def render_metrics(
    document: dict | None,
    now: float | None = None,
    stale_after_seconds: float = DEFAULT_STALE_AFTER_SECONDS,
    expected_markers: tuple[str, ...] | None = None,
) -> str:
    """Prometheus exposition text for the SAME document ``/health`` reads.

    Every value here is derived from ``decision_status`` (or the document it
    judges) rather than recomputed a second way, so a scrape and a probe can
    never disagree about what is healthy. When the expected set cannot be
    derived, ``fleet_cron_jobs_expected`` is **-1** (explicitly unknown) rather
    than a 0 or a stale count that a consumer would read as a measurement.
    """
    now = time.time() if now is None else now
    status_code, body = decision_status(document, now, stale_after_seconds, expected_markers)
    resolved = declared_enabled_markers() if expected_markers is None else tuple(expected_markers)
    expected_count = len(resolved) if resolved else -1
    jobs = (document or {}).get("jobs") or []
    age = _age_seconds(document or {}, now)

    lines = [
        "# HELP fleet_cron_up Whether GET /health would currently answer 200.",
        "# TYPE fleet_cron_up gauge",
        f"fleet_cron_up {1 if status_code == 200 else 0}",
        "# HELP fleet_cron_jobs_present Number of jobs present in the health decision document.",
        "# TYPE fleet_cron_jobs_present gauge",
        f"fleet_cron_jobs_present {len(jobs)}",
        "# HELP fleet_cron_jobs_expected Number of jobs the schedule is expected to report (-1 when the declaration could not be read).",
        "# TYPE fleet_cron_jobs_expected gauge",
        f"fleet_cron_jobs_expected {expected_count}",
        "# HELP fleet_cron_decision_age_seconds Seconds since the health decision document last advanced.",
        "# TYPE fleet_cron_decision_age_seconds gauge",
        f"fleet_cron_decision_age_seconds {int(age) if age is not None else -1}",
        "# HELP fleet_cron_job_ok Whether a job's last recorded run completed cleanly (1) or not (0).",
        "# TYPE fleet_cron_job_ok gauge",
    ]
    for job in jobs:
        marker = str(job.get("marker") or job.get("role") or "unknown")
        rc = job.get("rc")
        job_ok = 1 if (rc in (0, None) and not job.get("timed_out")) else 0
        lines.append(f'fleet_cron_job_ok{{job="{marker}"}} {job_ok}')
    return "\n".join(lines) + "\n"


class _Handler(BaseHTTPRequestHandler):
    """Three paths, re-read from disk per request, each answered with its own status code."""

    #: Set by :func:`serve` — the decision document and a server reference.
    decision_path: Path = Path("decision.json")
    stale_after_seconds: float = DEFAULT_STALE_AFTER_SECONDS
    expected_markers: tuple[str, ...] | None = None
    server_version = "fleet-cron-dev-run/1.0"

    def _answer(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8") + b"\n"
        self.send_response(status)
        self.send_header("Content-Type", CONTENT_TYPE)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _answer_text(self, status: int, text: str, content_type: str) -> None:
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 — http.server's own naming
        path = self.path.split("?", 1)[0]
        document = load_decision(self.decision_path)
        if path in HEALTH_PATHS:
            self._answer(*decision_status(document, stale_after_seconds=self.stale_after_seconds, expected_markers=self.expected_markers))
            return
        if path == METRICS_PATH:
            self._answer_text(200, render_metrics(document, stale_after_seconds=self.stale_after_seconds, expected_markers=self.expected_markers), METRICS_CONTENT_TYPE)
            return
        self._answer(404, {"status": "not-found", "reason": f"only {list(HEALTH_PATHS) + [METRICS_PATH]} are served"})

    def do_HEAD(self) -> None:  # noqa: N802
        self.do_GET()

    def log_message(self, fmt: str, *args: object) -> None:
        """Quiet by default: the run's own log lines carry the decision."""
        return


def serve(
    port: int,
    decision_path: Path,
    ready: threading.Event | None = None,
    stale_after_seconds: float = DEFAULT_STALE_AFTER_SECONDS,
    expected_markers: tuple[str, ...] | None = None,
) -> ThreadingHTTPServer:
    """Bind ``port`` and answer on a background thread; returns the server to stop."""
    handler = type(
        "_BoundHandler",
        (_Handler,),
        {
            "decision_path": Path(decision_path),
            "stale_after_seconds": stale_after_seconds,
            "expected_markers": expected_markers,
        },
    )
    server = ThreadingHTTPServer(("0.0.0.0", port), handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, name="healthz", daemon=True).start()
    if ready is not None:
        ready.set()
    return server


def cmd_status(args: argparse.Namespace) -> int:
    status, payload = decision_status(load_decision(Path(args.decision)), stale_after_seconds=args.stale_after)
    print(json.dumps({"http_status": status, **payload}, indent=2, sort_keys=True))
    return OK if status == 200 else NOT_OK


def cmd_metrics(args: argparse.Namespace) -> int:
    document = load_decision(Path(args.decision))
    print(render_metrics(document, stale_after_seconds=args.stale_after), end="")
    status, _ = decision_status(document, stale_after_seconds=args.stale_after)
    return OK if status == 200 else NOT_OK


def cmd_probe(args: argparse.Namespace) -> int:
    """A local HTTP GET of ``/health``, exit 0/1 — the Dockerfile's HEALTHCHECK CMD.

    `curl` is not a runtime dependency of this image (`inventory.yaml` declares
    it `stage: build` only, and this module stays stdlib-only), so the probe is
    this: a tiny client using `http.client`, catching every network failure as
    UNHEALTHY rather than letting an exception's traceback stand in for a
    verdict Docker cannot parse.
    """
    import http.client

    try:
        connection = http.client.HTTPConnection("127.0.0.1", args.port, timeout=args.timeout)
        connection.request("GET", "/health")
        response = connection.getresponse()
        healthy = response.status == 200
        response.read()
        connection.close()
    except OSError as exc:
        print(f"healthz probe: UNHEALTHY — {exc}", file=sys.stderr)
        return NOT_OK
    if not healthy:
        print(f"healthz probe: UNHEALTHY — HTTP {response.status}", file=sys.stderr)
        return NOT_OK
    return OK


def cmd_serve(args: argparse.Namespace) -> int:
    """Serve /health + /healthz + /metrics in the FOREGROUND until SIGTERM/SIGINT.

    This is the entrypoint's own caller (issue #712, EPIC #706 D4): the image's
    default command (`cron -f`) has no other process answering the Dockerfile's
    HEALTHCHECK, so `entrypoint.sh` backgrounds this alongside it. Until
    something writes ``--decision`` (a production decision-writer is a later
    deliverable — this lane is blocked-by D3, #711, which has not landed), the
    honest answer is 503 `not-ready`, never a fabricated 200: a listener that
    exists but has nothing to report is not the same claim as "the jobs ran
    clean", and this surface never conflates the two.
    """
    import signal as _signal_module

    stop = threading.Event()

    def _stop(_signum: int, _frame: object) -> None:
        stop.set()

    _signal_module.signal(_signal_module.SIGTERM, _stop)
    _signal_module.signal(_signal_module.SIGINT, _stop)

    server = serve(args.port, Path(args.decision), stale_after_seconds=args.stale_after)
    print(
        f"healthz: serving /health, /healthz, /metrics on 0.0.0.0:{args.port} from {args.decision}",
        flush=True,
    )
    stop.wait()
    server.shutdown()
    server.server_close()
    return OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="healthz", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    status = sub.add_parser("status", help="print what GET /healthz (or /health) would answer (exit 0 when 200)")
    status.add_argument("--decision", required=True, help="the decision document to answer from")
    status.add_argument("--stale-after", type=float, default=DEFAULT_STALE_AFTER_SECONDS, help="staleness bound, seconds")
    status.set_defaults(func=cmd_status)

    metrics = sub.add_parser("metrics", help="print what GET /metrics would answer (exit 0 when /health is 200)")
    metrics.add_argument("--decision", required=True, help="the decision document to answer from")
    metrics.add_argument("--stale-after", type=float, default=DEFAULT_STALE_AFTER_SECONDS, help="staleness bound, seconds")
    metrics.set_defaults(func=cmd_metrics)

    serve_cmd = sub.add_parser("serve", help="serve /health, /healthz, /metrics in the foreground")
    serve_cmd.add_argument("--decision", required=True, help="the decision document to answer from")
    serve_cmd.add_argument("--port", type=int, required=True, help="the port to bind")
    serve_cmd.add_argument("--stale-after", type=float, default=DEFAULT_STALE_AFTER_SECONDS, help="staleness bound, seconds")
    serve_cmd.set_defaults(func=cmd_serve)

    probe = sub.add_parser("probe", help="GET /health locally, exit 0/1 (the Dockerfile HEALTHCHECK CMD)")
    probe.add_argument("--port", type=int, required=True, help="the port to probe")
    probe.add_argument("--timeout", type=float, default=4.0, help="socket timeout, seconds")
    probe.set_defaults(func=cmd_probe)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
