#!/usr/bin/env python3
"""paperclip runtime health probe (issue #411, ADR-0013).

A real check, not a hope. This performs ``GET <base>/api/health`` against the
self-hosted upstream paperclip runtime and returns a verdict: it is the probe
the flag-gated deploy pipeline runs after a deploy, and the same probe
``scripts/check-paperclip-deploy.sh`` exercises offline both ways (a live healthy
server passes; an HTTP error and an absent process both fail).

Exit-code contract (shared with every gate in this repo):
    0  OK            — the process answered 2xx with a JSON body
    1  NOT-OK        — the process is absent (refused / timed out / DNS) or
                       unhealthy (non-2xx, or a non-JSON body)
    2  CANNOT-ASSESS — the probe could not be evaluated (bad arguments)

CANNOT-ASSESS must never be reported as a pass.

Usage:
    python3 infra/paperclip/health/healthcheck.py [--base URL] [--path PATH]
                                                  [--timeout SECONDS]
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

DEFAULT_BASE_URL = "http://localhost:3100"
DEFAULT_HEALTH_PATH = "/api/health"
DEFAULT_TIMEOUT = 3.0


def probe(base_url: str, path: str = DEFAULT_HEALTH_PATH,
          timeout: float = DEFAULT_TIMEOUT) -> tuple[int, str]:
    """GET ``base_url`` + ``path`` and return ``(exit_code, message)``."""
    url = base_url.rstrip("/") + "/" + path.lstrip("/")
    request = urllib.request.Request(
        url, method="GET", headers={"Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = response.getcode()
            body = response.read()
    except urllib.error.HTTPError as exc:  # a response that is not 2xx
        return 1, f"unhealthy: HTTP {exc.code} from {url}"
    except urllib.error.URLError as exc:  # refused / timed out / bad DNS
        return 1, f"unreachable: {url} ({exc.reason})"
    except (TimeoutError, OSError) as exc:
        return 1, f"unreachable: {url} ({exc})"

    if not 200 <= status < 300:
        return 1, f"unhealthy: HTTP {status} from {url}"
    try:
        json.loads(body.decode("utf-8") or "{}")
    except (UnicodeDecodeError, ValueError):
        return 1, f"unhealthy: {url} returned a non-JSON body"
    return 0, f"healthy: {url} (HTTP {status})"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="paperclip runtime health probe")
    parser.add_argument("--base", default=DEFAULT_BASE_URL,
                        help="runtime base URL (default: %(default)s)")
    parser.add_argument("--path", default=DEFAULT_HEALTH_PATH,
                        help="health path (default: %(default)s)")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT,
                        help="request timeout in seconds (default: %(default)s)")
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        return 2

    code, message = probe(args.base, args.path, args.timeout)
    stream = sys.stdout if code == 0 else sys.stderr
    print(f"paperclip-health: {message}", file=stream)
    return code


if __name__ == "__main__":
    sys.exit(main())
