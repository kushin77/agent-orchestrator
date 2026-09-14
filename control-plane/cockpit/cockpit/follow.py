"""``--follow`` — the live view, a client of the two authenticated SSE streams.

ADR-0026 D3: ``tmux attach`` + ``tail -f`` is replaced by a CLI ``--follow``
that consumes the two streams that already exist and are already honest —
``portal/server/fleet.py`` (surface ``fleet_projection``, event ``snapshot``)
and ``portal/server/live_feed.py`` (surface ``telemetry_live_feed``, event
``telemetry``) — over the same identity path the control API uses: the console
session cookie, attached at the last moment and never stored (ADR-0025 D2,
D3.2). The follow mode carries no credential of its own, opens no listener and
writes nothing: a watcher is by construction a reader.

The surface and event names are READ from the modules that declare them, never
restated. The wire is stdlib ``urllib``; a test injects a transport seam and
exercises the same parser. Every state that is not "frames arrived" renders a
NAMED condition — ``unreachable``, ``NO_DATA`` — never an empty pane that reads
as healthy.
"""

from __future__ import annotations

import urllib.request
from dataclasses import dataclass
from typing import Iterable, Iterator, Mapping, Optional, TextIO

from . import _paths

#: The two stream endpoints the console route table serves
#: (``portal/server/app.py::_route_api``: GET /api/fleet/stream and
#: GET /api/telemetry/stream).
FLEET_STREAM_PATH = "/api/fleet/stream"
TELEMETRY_STREAM_PATH = "/api/telemetry/stream"


@dataclass(frozen=True)
class Stream:
    surface: str
    event: str
    path: str
    module: str


def declared_streams() -> tuple[Stream, Stream]:
    """The two declared live surfaces, read from the modules that declare them."""
    _paths.ensure_paths()
    import portal.server.fleet as fleet_module
    import portal.server.live_feed as live_module

    return (
        Stream(
            surface=fleet_module.FLEET_SURFACE,
            event=fleet_module.SSE_EVENT,
            path=FLEET_STREAM_PATH,
            module="portal/server/fleet.py",
        ),
        Stream(
            surface=live_module.LIVE_FEED_SURFACE,
            event=live_module.SSE_EVENT,
            path=TELEMETRY_STREAM_PATH,
            module="portal/server/live_feed.py",
        ),
    )


@dataclass(frozen=True)
class SseEvent:
    event: str
    data: str


class SseTransport:
    """The stream transport seam — one open call yields decoded chunks."""

    def open(self, path: str, *, cookie: str) -> Iterator[str]:  # pragma: no cover
        raise NotImplementedError


class HttpSseTransport(SseTransport):
    """The real wire: an SSE GET on the console app with the session cookie."""

    def __init__(self, base_url: str) -> None:
        self.base_url = (base_url or "").rstrip("/")

    def open(self, path: str, *, cookie: str) -> Iterator[str]:
        headers = {"Accept": "text/event-stream", "Cache-Control": "no-cache"}
        if cookie:
            headers["Cookie"] = cookie
        request = urllib.request.Request(f"{self.base_url}{path}", headers=headers)
        with urllib.request.urlopen(request) as response:  # noqa: S310 - the declared stream endpoint
            for chunk in response:
                yield chunk.decode("utf-8", "replace")


class FixtureSseTransport(SseTransport):
    """The offline transport: replays canned frames and records every open."""

    def __init__(
        self,
        frames: Optional[Mapping[str, str]] = None,
        error: Optional[Exception] = None,
    ) -> None:
        self.frames = dict(frames or {})
        self.error = error
        self.opens: list[dict[str, str]] = []

    def open(self, path: str, *, cookie: str) -> Iterator[str]:
        self.opens.append({"path": path, "cookie": cookie})
        if self.error is not None:
            raise self.error
        yield self.frames.get(path, "")


def parse_events(chunks: Iterable[str]) -> list[SseEvent]:
    """A buffered SSE line parser — never guesses across a chunk boundary."""
    events: list[SseEvent] = []
    data: list[str] = []
    event = ""
    pending = ""
    for chunk in chunks:
        pending += chunk
        while "\n" in pending:
            line, pending = pending.split("\n", 1)
            line = line.rstrip("\r")
            if line == "":
                if event or data:
                    events.append(SseEvent(event=event or "message", data="\n".join(data)))
                    event, data = "", []
                continue
            if line.startswith("event:"):
                event = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data.append(line[len("data:"):].lstrip())
    return events


def _clip(text: str, limit: int = 120) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "\u2026"


def follow(
    session: str,
    cookie_name: str,
    transports: Mapping[str, SseTransport],
    out: TextIO,
    *,
    max_events: int = 0,
) -> int:
    """Consume both declared streams and render every frame; named conditions.

    Exit contract (this repo's tri-state): 0 when every stream delivered (a
    stream with no frames renders ``NO_DATA``, named — that is a verdict, not
    a failure) · 2 CANNOT-ASSESS when a stream could not be opened (rendered
    ``unreachable`` by name, never silently dropped).
    """
    streams = declared_streams()
    cookie = f"{cookie_name}={session}" if session else ""
    problems = 0
    total = 0
    out.write("cockpit --follow — a client of the two declared live surfaces\n")
    for stream in streams:
        transport = transports.get(stream.surface)
        if transport is None:
            out.write(f"  unreachable  {stream.surface}: no transport was supplied\n")
            problems += 1
            continue
        chunks: list[str] = []
        try:
            for chunk in transport.open(stream.path, cookie=cookie):
                chunks.append(chunk)
                if max_events and len(chunks) >= max_events:
                    break
        except (OSError, ValueError) as exc:
            out.write(
                f"  unreachable  {stream.surface} ({stream.module}): "
                f"{type(exc).__name__}: {exc}\n"
            )
            problems += 1
            continue
        events = parse_events(chunks)
        if max_events:
            events = events[:max_events]
        out.write(
            f"  stream  {stream.surface} ({stream.module}) · event {stream.event}\n"
        )
        if not events:
            out.write(
                f"  NO_DATA  {stream.surface} delivered no frames "
                f"(absence is never a green state)\n"
            )
            continue
        for event in events:
            out.write(f"    {event.event:<10} {_clip(event.data)}\n")
            total += 1
    verdict = "CANNOT-ASSESS" if problems else "OK"
    out.write(f"follow: {verdict} — {total} frame(s) rendered, {problems} unreachable\n")
    return 2 if problems else 0
