#!/usr/bin/env python3
"""A read-only Cloudflare API stub for the remote SSH route (issue #771).

WHY THIS EXISTS
    Two of the route's load-bearing properties are negative: a dry run sends no
    mutating request, and a refusal (feature flag off, missing identifier)
    happens before any request at all. Neither can be proven against the real
    API -- there are no credentials in this repo and no proof may touch an
    estate. So the route is driven against this stub instead. It:

      * answers the reads the route makes (tunnel configuration, DNS records,
        Access applications, Access policies) with canned documents;
      * RECORDS every request as one ``METHOD PATH`` line, appended to ``--log``
        (so the driver can truncate the file between probes and read exactly
        what one probe sent);
      * REFUSES a mutating method -- 405, plus a ``MUTATING <method> <path>``
        line -- so "no PUT and no POST" is *observed* rather than promised.

FIXTURE ONLY. It binds 127.0.0.1, is started by
`scripts/check-ao-ssh-access.sh` and by the dry-run example in
`docs/OPERATOR-ACCESS.md`, and nothing in the route's live path knows it exists.
The hostnames it serves are reserved placeholders, not anybody's estate.

Usage:
    python3 infra/cloudflare/stub_cf_api.py --port 0 \
        --port-file /tmp/stub.port --log /tmp/stub.requests
"""

from __future__ import annotations

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlsplit

#: The tunnel's live ingress this stub reports: one unrelated hostname the
#: estate already serves, plus the trailing catch-all. A merge that replaces
#: the array instead of merging into it drops the first rule -- which is the
#: defect the route's gate exists for, and this fixture makes it observable.
CURRENT_INGRESS: list[dict] = [
    {"hostname": "stub-estate.example.test", "service": "http://stub-estate:80"},
    {"service": "http_status:404"},
]

#: The DNS record that already exists for the zone, under a different name: the
#: route must CREATE the proxied CNAME for its own hostname, not update this one.
EXISTING_DNS_RECORDS: list[dict] = [
    {"id": "stub-record-1", "name": "stub-estate.example.test", "type": "CNAME"}
]

#: An Access application that exists, for another hostname -- so the route plans
#: a CREATE for its own hostname rather than silently adopting somebody else's.
EXISTING_ACCESS_APPS: list[dict] = [
    {"id": "stub-app-1", "domain": "stub-estate.example.test", "type": "self_hosted"}
]

#: The tunnel list the route's provision step reads. It reports one EXISTING
#: tunnel, so a dry-run provision plans a REUSE rather than a create -- the
#: create branch (an empty list) is what the pure `tunnel_id_from_list` probe
#: covers, and the fail-closed read is provoked in the gate too.
EXISTING_TUNNELS: list[dict] = [
    {"id": "stub-tunnel", "name": "stub-tunnel"},
]


class Handler(BaseHTTPRequestHandler):
    """Serves the canned reads; refuses everything that would mutate."""

    protocol_version = "HTTP/1.1"
    server_version = "stub-cf-api/1"
    sys_version = ""

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        """Silence the default stderr access log (the request log is the record)."""

    # -- recording ---------------------------------------------------------
    def _record(self, line: str) -> None:
        log: Optional[Path] = getattr(self.server, "request_log", None)
        if log is None:
            return
        with log.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def _send(self, status: int, payload: Any) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _ok(self, result: Any) -> None:
        self._send(200, {"success": True, "errors": [], "messages": [], "result": result})

    def _error(self, status: int, message: str) -> None:
        self._send(status, {"success": False, "errors": [{"message": message}], "result": None})

    # -- the verbs ---------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's interface
        self._handle("GET")

    def do_PUT(self) -> None:  # noqa: N802
        self._handle("PUT")

    def do_POST(self) -> None:  # noqa: N802
        self._handle("POST")

    def do_PATCH(self) -> None:  # noqa: N802
        self._handle("PATCH")

    def do_DELETE(self) -> None:  # noqa: N802
        self._handle("DELETE")

    def _handle(self, method: str) -> None:
        parts = urlsplit(self.path)
        path = parts.path
        self._record("%s %s" % (method, self.path))
        if method != "GET":
            # A read-only stub: a mutating call is a finding, and it is named in
            # the log so a driver can refuse it by name rather than by absence.
            self._record("MUTATING %s %s" % (method, self.path))
            self._error(405, "this stub is read-only: %s is refused" % method)
            return
        if "/cfd_tunnel/" in path and path.endswith("/configurations"):
            self._ok({"config": {"ingress": list(CURRENT_INGRESS)}})
            return
        if path.endswith("/cfd_tunnel"):
            # The tunnel list read the provision step makes (find-or-create by
            # name). It reports one existing tunnel so a dry-run plans a reuse.
            self._ok(list(EXISTING_TUNNELS))
            return
        if path.endswith("/dns_records"):
            name = parts.query.removeprefix("name=") if parts.query else ""
            records = [
                record for record in EXISTING_DNS_RECORDS if not name or record["name"] == name
            ]
            self._ok(records)
            return
        if path.endswith("/access/apps"):
            self._ok(list(EXISTING_ACCESS_APPS))
            return
        if "/access/apps/" in path and path.endswith("/policies"):
            self._ok([])
            return
        self._error(404, "the stub serves no route for %s" % path)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument(
        "--port-file",
        default="",
        help="write the bound port here once the socket is listening (race-free startup)",
    )
    parser.add_argument("--log", default="", help="append one line per request here")
    args = parser.parse_args(argv)

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.daemon_threads = True
    if args.log:
        log = Path(args.log)
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("", encoding="utf-8")
        server.request_log = log  # type: ignore[attr-defined]
    else:
        server.request_log = None  # type: ignore[attr-defined]

    bound = server.server_address[1]
    if args.port_file:
        Path(args.port_file).write_text("%d\n" % bound, encoding="utf-8")
    print("stub-cf-api listening on http://%s:%d" % (args.host, bound), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
