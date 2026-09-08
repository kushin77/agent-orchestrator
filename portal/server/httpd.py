"""portal.server.httpd — stdlib http.server binding for the console app.

Thin transport: decodes the request (path, query, JSON body, ``Cookie``
header) into the :class:`ConsoleApplication` pipeline and writes the returned
:class:`Response` (including any ``Set-Cookie`` the session endpoints issue).
No framework, no sockets in the tests — the pytest suite drives
``ConsoleApplication.handle`` directly; this module exists so
``python -m portal.server.main`` can serve the static console offline.
"""

from __future__ import annotations

import http.server
import json
import socketserver
from typing import Optional
from urllib.parse import parse_qs, urlparse

from portal.server.app import ConsoleApplication, Response


def _parse_cookies(cookie_header: Optional[str]) -> dict[str, str]:
    cookies: dict[str, str] = {}
    if not cookie_header:
        return cookies
    for part in cookie_header.split(";"):
        part = part.strip()
        if "=" not in part:
            continue
        name, _, value = part.partition("=")
        cookies[name.strip()] = value.strip()
    return cookies


class ConsoleRequestHandler(http.server.BaseHTTPRequestHandler):
    """One stdlib handler per request; ``server.app`` carries the app."""

    server_version = "AgentOrchestratorConsole/1.0"

    # -- helpers ------------------------------------------------------------
    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        if not raw:
            return {}
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def _dispatch(self, method: str) -> None:
        app: ConsoleApplication = self.server.app  # type: ignore[attr-defined]
        parsed = urlparse(self.path)
        query = {
            key: values[-1]
            for key, values in parse_qs(parsed.query).items()
        }
        body = self._read_body() if method == "POST" else {}
        response: Response = app.handle(
            method,
            parsed.path,
            query=query,
            body=body,
            cookies=_parse_cookies(self.headers.get("Cookie")),
        )
        self._write(response)

    def _write(self, response: Response) -> None:
        body = response.as_bytes()
        content_type = response.content_type or "application/json; charset=utf-8"
        self.send_response(response.status)
        for name, value in response.headers:
            self.send_header(name, value)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if body:
            self.wfile.write(body)

    # -- verbs --------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def log_message(self, fmt: str, *args: object) -> None:
        # Keep the console quiet on stdout by default (still debuggable).
        pass


class ConsoleServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    """Threaded stdlib server carrying the console application."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        app: ConsoleApplication,
    ) -> None:
        self.app = app
        super().__init__(server_address, ConsoleRequestHandler)


def serve(app: ConsoleApplication, host: str = "127.0.0.1", port: int = 8787) -> None:
    """Run the console server until interrupted (Ctrl-C stops it)."""
    server = ConsoleServer((host, port), app)
    print(f"agent-orchestrator console listening on http://{host}:{port} "
          f"(static root: {app.static_dir})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
