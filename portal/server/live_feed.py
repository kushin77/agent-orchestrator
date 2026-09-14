"""portal.server.live_feed — the live telemetry event feed adapter (issue #345).

WHY this exists: every model call the platform routes already leaves a durable
record — the gateway proxy appends one full call record per dispatch (the
phase-5 telemetry seam) and the DLP pipeline appends one security event per
guardrail decision — but both are append-only JSONL an operator can only read
after the fact. This module is the *server half* of the live feed: it tails
those stores and pushes each new record to a browser as it lands, so the
shared-frontend live feed reflects a routed call within seconds of its landing
in the store.

Cannibalize, do not duplicate. This adapter owns transport shape (a tailing
cursor, the SSE frame, the honest header) — never a record's meaning:

* the routed request frames are the gateway proxy's own call records
  (``gateway/proxy/model.py GatewayCallRecord.to_dict`` — the fleet
  model-call-audit shape the observability pillar consumes), so a frame
  carries exactly the fields the gateway stamped: provider, model, outcome,
  tokens, latency and estimated cost;
* the guardrail verdict frames are the DLP lane's own security events
  (``guardrails/dlp/telemetry.py SecurityEvent.as_dict``), so a verdict is
  the decision the pipeline actually made (``call_sent``, ``scrub_blocked``,
  ``egress_denied``, ``injection_attempt``, ``tamper_detected``, …).

Nothing here re-implements a reader, a cost figure or a verdict, and nothing
is inferred: a field the record does not carry is reported as ``null``, never
as a plausible default.

The honesty rules of the pane (each negative-tested):

* **an empty feed is not "no traffic".** The first frame is a header that names
  what the feed can actually see: ``NO_STORE`` when no store is present (the
  feed has no source at all), ``EMPTY`` when a store exists and holds nothing
  visible, ``ERROR`` when it exists but could not be read, and ``LIVE`` only
  when records are visible. Only ``LIVE`` claims telemetry; the other three
  carry an explicit ``note`` saying the absence is not evidence of zero
  traffic. A malformed line is **counted and reported** in the header
  (``malformed``), never silently dropped — while a half-written trailing line
  (a write in flight) is neither a record nor an error: it is simply not read
  until its newline lands.
* **no invented correlation.** A routed call and its guardrail verdicts share
  an identifier only when the deployment threads one (``verdict.callId ==
  call.requestId``); a verdict is attached to a call frame only on an exact
  match, and every frame keeps its own ``source`` (store + line) so a reader
  can always go back to the record. A verdict that arrives on its own is pushed
  on its own — the feed never guesses which call it belonged to.
* **backfill is labelled.** The frames a connection replays on connect carry
  ``backfill: true``; frames that arrived while the connection was open carry
  ``backfill: false``. A client can always tell history from live traffic.

The metering rollup pane (``portal.server.finops``, issue #341) stays the
billing surface: it answers "what did the month cost". This feed answers "what
is happening right now", which is why it carries the per-dispatch record with
its latency rather than a rollup.

The surface ships **feature-flag-gated OFF** (GR-5): the flag is declared in
``infra/feature-flags/registry.yaml`` under ``surfaces`` and read here through
the same reader the fleet projection uses (``portal.server.fleet``); while it
is off the app refuses every ``/api/telemetry/*`` route before authN.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Iterator, Optional, Sequence

from portal.server.fleet import read_surface_default, surface_enabled

#: The registry surface key that gates this endpoint family.
LIVE_FEED_SURFACE = "telemetry_live_feed"
#: The flag declaration read at boot (repo-root relative).
REGISTRY_RELATIVE = Path("infra") / "feature-flags" / "registry.yaml"
#: The SSE event name every frame carries (``kind`` distinguishes the frames).
SSE_EVENT = "telemetry"

#: The repo-runtime stores this feed tails (both under the console's runtime
#: directory, the same shape ``livestore.DEFAULT_USAGE_STORE`` established):
#: the gateway's per-dispatch call records and the DLP lane's security events.
DEFAULT_CALLS_STORE = Path(".telemetry") / "calls.jsonl"
DEFAULT_VERDICTS_STORE = Path(".telemetry") / "security.jsonl"
#: Store keys (the names the header and each frame's ``source.store`` carry).
CALLS_STORE = "calls"
VERDICTS_STORE = "verdicts"

#: Frame kinds.
KIND_FEED = "feed"
KIND_CALL = "call"
KIND_VERDICT = "verdict"

#: Header states — only ``LIVE`` claims telemetry exists.
STATE_LIVE = "LIVE"
STATE_EMPTY = "EMPTY"
STATE_NO_STORE = "NO_STORE"
STATE_ERROR = "ERROR"

#: The exact sentence each non-live state carries (never "no traffic").
NOTE_NO_STORE = (
    "no telemetry store is present — the feed has no source at all "
    "(not evidence of zero traffic)"
)
NOTE_EMPTY = (
    "the store exists and holds no records visible to this principal — "
    "not evidence of zero traffic"
)
NOTE_ERROR = (
    "the store is present but could not be read — the feed cannot see "
    "telemetry (not evidence of zero traffic)"
)

#: Seconds between polls on the push channel (a call lands in the store, the
#: next poll pushes it: the "within seconds" bound of issue #345).
DEFAULT_POLL_SECONDS = 1.0
#: How many of the most recent records a connection replays on connect.
DEFAULT_REPLAY_LIMIT = 20
#: The largest replay window a client may request.
MAX_REPLAY_LIMIT = 200
#: How far back the replay reader looks for its window (a bound on the read).
REPLAY_READ_BYTES = 512 * 1024


def _as_int(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any) -> Optional[float]:
    """A float, or ``None`` when the record genuinely carries no number.

    A non-numeric value is ``None`` (absent), never coerced to ``0``: an
    unreadable latency must not render as "instant".
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _iso_ts(value: Any) -> Optional[str]:
    """Normalize a record timestamp to the repo-wide RFC 3339 ``Z`` shape.

    Accepts the epoch float the DLP events carry and the ISO string the
    gateway records carry. An unparseable timestamp returns ``None`` (the
    frame then renders no time) rather than the current time — a fabricated
    "now" would make an old event look live.
    """
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    text = str(value).strip()
    if not text:
        return None
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_line(raw: bytes) -> Optional[dict]:
    """One JSONL line as a dict, or ``None`` when it is not one."""
    line = raw.strip()
    if not line:
        return None
    try:
        payload = json.loads(line.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


@dataclass
class StoreTail:
    """An incremental cursor over one append-only JSONL telemetry store.

    The cursor owns *transport* only: which bytes have been read, and how many
    lines were unreadable. Records are returned as the dicts the writer
    appended, so the projection consumes each lane's documented shape without
    this module redefining it.

    Only **complete** lines are consumed: a trailing partial line (a write in
    flight) stays unread until its newline lands, so a torn write is never
    parsed as a record. A store that shrinks (rotation/truncation) is re-read
    from the top. A line that is not a JSON object is counted in
    :attr:`malformed` and skipped — visible in the header, never silent.
    """

    name: str
    path: Path
    offset: int = 0
    line_no: int = 0
    malformed: int = 0
    read_error: Optional[str] = None

    @property
    def present(self) -> bool:
        """Whether the store file exists right now."""
        return self.path.is_file()

    def poll(self) -> list[tuple[int, dict]]:
        """The complete records appended since the previous poll.

        Returns ``(line_number, record)`` pairs in file order. An absent or
        unreadable store yields nothing (the header reports why); it never
        raises into the stream — a feed that dies because its store blinked is
        worse than a feed that reports the store is gone.
        """
        try:
            size = self.path.stat().st_size
        except OSError as exc:
            self.read_error = str(exc)
            return []
        self.read_error = None
        if size < self.offset:
            # Rotated or truncated: the cursor no longer addresses this file.
            self.offset = 0
            self.line_no = 0
        if size == self.offset:
            return []
        try:
            with open(self.path, "rb") as handle:
                handle.seek(self.offset)
                chunk = handle.read()
        except OSError as exc:
            self.read_error = str(exc)
            return []
        consumed = chunk.rfind(b"\n") + 1
        if consumed == 0:
            return []  # a partial line only: wait for its newline
        self.offset += consumed
        records: list[tuple[int, dict]] = []
        fragments = chunk[:consumed].split(b"\n")
        if fragments and fragments[-1] == b"":
            fragments = fragments[:-1]  # the newline that ended the last line
        for raw in fragments:
            self.line_no += 1
            payload = _parse_line(raw)
            if payload is None:
                if raw.strip():
                    self.malformed += 1
                continue
            records.append((self.line_no, payload))
        return records

    def census(self, visible: Optional[Callable[[dict], bool]]) -> dict[str, int]:
        """Count the store's records (and unreadable lines) from the top.

        Used for the connection header, which must report what the feed can
        actually see. ``visible`` filters by the connection's scope so a
        principal never learns another tenant's volume; ``None`` means every
        record counts. ``malformed`` is a store-health signal with no tenant
        attribution, so it always counts the whole file.

        A trailing line without its newline is a write in flight: it is not
        counted as a record *or* as malformed, exactly as the cursor defers it.
        """
        counts = {"records": 0, "malformed": 0}
        try:
            with open(self.path, "rb") as handle:
                data = handle.read()
        except OSError as exc:
            self.read_error = str(exc)
            return counts
        if data and not data.endswith(b"\n"):
            data = data[: data.rfind(b"\n") + 1]
        for raw in data.split(b"\n"):
            if not raw.strip():
                continue
            payload = _parse_line(raw)
            if payload is None:
                counts["malformed"] += 1
                continue
            if visible is None or visible(payload):
                counts["records"] += 1
        return counts

    def replay(self, limit: int) -> list[tuple[int, dict]]:
        """The last ``limit`` records of the store, oldest first.

        Reads a bounded tail window (not the whole file) and consumes only
        complete lines. Line numbers are counted from the file start so a
        replayed frame carries the same ``source.line`` its store holds.
        """
        if limit <= 0:
            return []
        try:
            size = self.path.stat().st_size
        except OSError as exc:
            self.read_error = str(exc)
            return []
        start = max(0, size - REPLAY_READ_BYTES)
        base = 0
        if start:
            try:
                with open(self.path, "rb") as handle:
                    base = handle.read(start).count(b"\n")
            except OSError as exc:
                self.read_error = str(exc)
                return []
        try:
            with open(self.path, "rb") as handle:
                if start:
                    handle.seek(start)
                chunk = handle.read()
        except OSError as exc:
            self.read_error = str(exc)
            return []
        fragments = chunk.split(b"\n")
        if fragments and fragments[-1] == b"":
            fragments = fragments[:-1]  # the newline that ended the last line
        line_no = base + (1 if start else 0)
        if start:
            fragments = fragments[1:]  # a partial line: not a record
        records: list[tuple[int, dict]] = []
        for raw in fragments:
            line_no += 1
            payload = _parse_line(raw)
            if payload is None:
                continue
            records.append((line_no, payload))
        return records[-limit:]


class LiveFeed:
    """Projects the live telemetry stores as an SSE event feed (issue #345).

    ``enabled`` is resolved from the feature-flag registry unless supplied
    explicitly (tests pass it; the server lets the registry decide). The
    stores are read-only: nothing here ever writes to a telemetry store.
    """

    def __init__(
        self,
        *,
        repo_root: Path | str,
        enabled: Optional[bool] = None,
        registry_path: Optional[Path | str] = None,
        calls_store_path: Optional[Path | str] = None,
        verdicts_store_path: Optional[Path | str] = None,
        poll_interval: float = DEFAULT_POLL_SECONDS,
        replay_limit: int = DEFAULT_REPLAY_LIMIT,
        max_frames: Optional[int] = None,
        max_polls: Optional[int] = None,
        timeout: Optional[float] = None,
        sleep: Optional[Callable[[float], None]] = None,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        self.repo_root = Path(repo_root)
        self.registry_path = (
            Path(registry_path) if registry_path is not None else None
        )
        if enabled is None:
            enabled = surface_enabled(
                self.repo_root,
                registry_path=self.registry_path,
                surface=LIVE_FEED_SURFACE,
            )
        self.enabled = bool(enabled)
        self.calls_store_path = (
            Path(calls_store_path)
            if calls_store_path is not None
            else self.repo_root / DEFAULT_CALLS_STORE
        )
        self.verdicts_store_path = (
            Path(verdicts_store_path)
            if verdicts_store_path is not None
            else self.repo_root / DEFAULT_VERDICTS_STORE
        )
        self.poll_interval = float(poll_interval)
        self.replay_limit = int(replay_limit)
        # Bounds/injectables so a test can drive the push channel to a
        # deterministic stop; a browser stream sets none of them.
        self.max_frames = max_frames
        self.max_polls = max_polls
        self.timeout = timeout
        self._sleep = sleep
        self._clock = clock

    # -- stores -------------------------------------------------------------
    def store_paths(self) -> dict[str, Path]:
        """The stores this feed tails, by store key."""
        return {
            CALLS_STORE: self.calls_store_path,
            VERDICTS_STORE: self.verdicts_store_path,
        }

    def _new_tails(self) -> list[StoreTail]:
        """Fresh cursors — one per store, **per connection**.

        A cursor belongs to a connection, never to the projection: two
        subscribers to the same feed each see every frame (a shared cursor
        would hand each event to whichever polled first).
        """
        return [
            StoreTail(name=name, path=path)
            for name, path in self.store_paths().items()
        ]

    # -- projections --------------------------------------------------------
    def _call_frame(
        self, payload: dict, *, line: int, path: Path, backfill: bool
    ) -> Optional[dict]:
        """Project one gateway call record into a feed frame.

        A line without the fields that make it a call record (no tenant, no
        outcome) is not a frame: the caller counts it as malformed. Nothing is
        defaulted — a field the record omits reads ``null``.
        """
        tenant_id = str(payload.get("tenantId") or "")
        outcome = payload.get("outcome")
        if not tenant_id or not isinstance(outcome, str) or not outcome:
            return None
        input_tokens = _as_int(payload.get("inputTokens"))
        output_tokens = _as_int(payload.get("outputTokens"))
        total = (
            _as_int(payload.get("tokens"), input_tokens + output_tokens)
            if payload.get("tokens") is not None
            else input_tokens + output_tokens
        )
        cost = _as_float(payload.get("estimatedCostUsd"))
        request_id = payload.get("requestId")
        return {
            "kind": KIND_CALL,
            "seq": None,  # assigned by the connection (monotonic per stream)
            "backfill": backfill,
            "id": str(request_id) if request_id else None,
            "ts": _iso_ts(payload.get("ts")),
            "tenantId": tenant_id,
            "agentId": payload.get("agentId") or None,
            "provider": payload.get("provider"),
            "model": payload.get("model"),
            "tier": payload.get("tier"),
            "taskType": payload.get("taskType"),
            "status": outcome,
            "tokens": {
                "input": input_tokens,
                "output": output_tokens,
                "total": total,
            },
            "latencyMs": _as_float(payload.get("latencyMs")),
            "cost": {
                "usd": None if cost is None else round(cost, 8),
                # The gateway's own field is ``estimatedCostUsd``: this feed
                # reports it as an estimate because that is what it is.
                "estimated": True,
            },
            "budgetAction": payload.get("budgetAction") or None,
            "attempts": (
                _as_int(payload.get("attempts")) if "attempts" in payload else None
            ),
            "error": payload.get("error") or None,
            "verdicts": [],
            "source": {"store": CALLS_STORE, "path": str(path), "line": line},
        }

    def _verdict_frame(
        self, payload: dict, *, line: int, path: Path, backfill: bool
    ) -> Optional[dict]:
        """Project one DLP security event into a feed frame.

        ``verdict`` is the decision the pipeline recorded: the event's own
        ``detail.verdict`` when it carries one (``blocked``/``suspicious``/
        ``sent``), otherwise the event type itself (``scrub_blocked``,
        ``egress_denied``, ``tamper_detected``, …) — the vocabulary the
        guardrails lane emits. A line without the documented event fields is
        not a verdict frame.
        """
        event_type = payload.get("event_type")
        tenant_id = payload.get("tenant_id")
        severity = payload.get("severity")
        if not isinstance(event_type, str) or not event_type:
            return None
        if not isinstance(tenant_id, str) or not tenant_id:
            return None
        if not isinstance(severity, str) or not severity:
            return None
        detail = payload.get("detail")
        if not isinstance(detail, dict):
            detail = {}
        verdict = detail.get("verdict")
        if not isinstance(verdict, str) or not verdict:
            verdict = event_type
        call_id = detail.get("call_id") or detail.get("callId") or None
        return {
            "kind": KIND_VERDICT,
            "seq": None,  # assigned by the connection (monotonic per stream)
            "backfill": backfill,
            "id": str(call_id) if call_id else None,
            "ts": _iso_ts(payload.get("ts")),
            "tenantId": tenant_id,
            "agentId": payload.get("agent_id") or None,
            "eventType": event_type,
            "verdict": verdict,
            "severity": severity,
            "detail": detail,
            "source": {"store": VERDICTS_STORE, "path": str(path), "line": line},
        }

    # -- connection state ---------------------------------------------------
    def _visible_filter(
        self, tenants: Optional[Sequence[str]]
    ) -> Optional[Callable[[dict], bool]]:
        """The scope predicate for a connection (``None`` = every tenant).

        Applied to raw store records, so a scoped principal's frames and
        header counts both come from the same in-scope subset.
        """
        if tenants is None:
            return None
        allowed = {str(tenant) for tenant in tenants}
        return lambda payload: str(
            payload.get("tenantId") or payload.get("tenant_id") or ""
        ) in allowed

    # -- frames -------------------------------------------------------------
    def sse_frame(self, frame: dict) -> str:
        """One server-sent-events frame carrying one telemetry frame."""
        payload = json.dumps(frame, separators=(",", ":"))
        return f"event: {SSE_EVENT}\ndata: {payload}\n\n"

    def header_frame(
        self,
        tails: Sequence[StoreTail],
        *,
        tenants: Optional[Sequence[str]],
        replayed: int,
    ) -> dict:
        """The honest connection header (state + what the feed can see).

        Only ``LIVE`` claims that telemetry exists. ``NO_STORE`` (nothing to
        read), ``EMPTY`` (a store with nothing visible) and ``ERROR`` (an
        unreadable store) each carry the explicit sentence that the absence is
        not evidence of zero traffic, so an unwired deployment can never be
        mistaken for a quiet one.
        """
        visible = self._visible_filter(tenants)
        stores = []
        records = 0
        malformed = 0
        read_errors = []
        for tail in tails:
            entry = {
                "name": tail.name,
                "path": str(tail.path),
                "present": tail.present,
                "records": 0,
                "malformed": 0,
                "readError": None,
            }
            if tail.present:
                counts = tail.census(visible)
                entry["records"] = counts["records"]
                entry["malformed"] = counts["malformed"]
                records += counts["records"]
                malformed += counts["malformed"]
                if tail.read_error:
                    # A store the process could not read is reported per store
                    # even when another store supplies records (a partial view
                    # must never look like a complete one).
                    entry["readError"] = tail.read_error
                    read_errors.append(f"{tail.name}: {tail.read_error}")
            stores.append(entry)
        present = [entry for entry in stores if entry["present"]]
        if not present:
            state, note = STATE_NO_STORE, NOTE_NO_STORE
        elif read_errors and records == 0:
            state, note = STATE_ERROR, f"{NOTE_ERROR} ({'; '.join(read_errors)})"
        elif records == 0:
            state, note = STATE_EMPTY, NOTE_EMPTY
        else:
            state, note = STATE_LIVE, None
        return {
            "kind": KIND_FEED,
            "state": state,
            "note": note,
            "stores": stores,
            "records": records,
            "malformed": malformed,
            "scope": (
                {"all": True, "tenants": None}
                if tenants is None
                else {"all": False, "tenants": sorted(str(t) for t in tenants)}
            ),
            "replayed": replayed,
            "pollSeconds": self.poll_interval,
        }

    # -- the push channel ---------------------------------------------------
    def stream(
        self,
        *,
        tenants: Optional[Sequence[str]] = None,
        replay: Optional[int] = None,
        poll_interval: Optional[float] = None,
        max_frames: Optional[int] = None,
        max_polls: Optional[int] = None,
        timeout: Optional[float] = None,
        sleep: Optional[Callable[[float], None]] = None,
        clock: Optional[Callable[[], float]] = None,
    ) -> Iterator[str]:
        """Yield a header, the replay window, then every new record as it lands.

        The header frames the connection (state + stores + scope); the replay
        window paints the pane immediately (``backfill: true``); after that a
        frame is pushed on each poll that finds a new complete record, so a
        routed call reaches the client within one poll interval of being
        appended to the store. Optional bounds (``max_frames``, ``max_polls``,
        ``timeout``) let a test stop the loop deterministically; a real stream
        runs until the client disconnects.
        """
        interval = self.poll_interval if poll_interval is None else float(poll_interval)
        frames_bound = self.max_frames if max_frames is None else max_frames
        polls_bound = self.max_polls if max_polls is None else max_polls
        time_bound = self.timeout if timeout is None else timeout
        sleeper = self._sleep if sleep is None else sleep
        sleeper = time.sleep if sleeper is None else sleeper
        timer = self._clock if clock is None else clock
        timer = time.monotonic if timer is None else timer
        replay_limit = self.replay_limit if replay is None else int(replay)

        tails = self._new_tails()
        visible = self._visible_filter(tenants)
        # Each connection consumes its stores from their current end: the
        # replay window is the history, the cursor is what follows it.
        for tail in tails:
            tail.poll()

        started = timer()
        next_seq = 0

        def numbered(frame: dict) -> dict:
            """Stamp a frame with the connection's running sequence number."""
            nonlocal next_seq
            frame["seq"] = next_seq
            next_seq += 1
            return frame

        def full() -> bool:
            return frames_bound is not None and next_seq >= frames_bound

        backfilled = self._backfill(tails, visible=visible, limit=replay_limit)
        yield self.sse_frame(
            numbered(
                self.header_frame(tails, tenants=tenants, replayed=len(backfilled))
            )
        )
        if full():
            return
        for frame in backfilled:
            yield self.sse_frame(numbered(frame))
            if full():
                return

        polls = 0
        announced_gone = False
        while True:
            if time_bound is not None and (timer() - started) >= time_bound:
                return
            if polls_bound is not None and polls >= polls_bound:
                return
            polls += 1
            for frame in self._drain(tails, visible=visible):
                yield self.sse_frame(numbered(frame))
                if full():
                    return
            gone = not any(tail.present for tail in tails)
            if gone and not announced_gone and next_seq > 1:
                # The stores went away under a live connection: say so rather
                # than keep a dead feed looking live.
                announced_gone = True
                yield self.sse_frame(
                    numbered(self.header_frame(tails, tenants=tenants, replayed=0))
                )
                if full():
                    return
            if not gone:
                announced_gone = False
            sleeper(interval)

    def _backfill(
        self,
        tails: Sequence[StoreTail],
        *,
        visible: Optional[Callable[[dict], bool]],
        limit: int,
    ) -> list[dict]:
        """The most recent records of each store, oldest first.

        Frames are ordered by their own timestamp (the stores' clock) so two
        lanes' records interleave the way they happened; a record with no
        readable timestamp falls back to its store line, which keeps the order
        deterministic without inventing a time for it.
        """
        if limit <= 0:
            return []
        frames = self._project(tails, visible=visible, backfill=True, limit=limit)
        frames.sort(
            key=lambda frame: (frame.get("ts") or "", frame["source"]["line"])
        )
        # The window is N *events*: each store contributed its newest `limit`,
        # so the newest `limit` of the merged set is the client's window.
        return frames[-limit:]

    def _drain(
        self,
        tails: Sequence[StoreTail],
        *,
        visible: Optional[Callable[[dict], bool]],
    ) -> list[dict]:
        """The frames for every record appended since the previous drain."""
        return self._project(tails, visible=visible, backfill=False)

    def _project(
        self,
        tails: Sequence[StoreTail],
        *,
        visible: Optional[Callable[[dict], bool]],
        backfill: bool,
        limit: Optional[int] = None,
    ) -> list[dict]:
        """Project new records of every store into frames.

        Verdicts are projected first so a verdict that shares its ``id`` with
        a call appended in the same window is carried by that call's frame —
        the only correlation the feed performs, and only on an exact
        identifier match. A verdict that matches no call is a frame of its
        own; it is never guessed onto a call.
        """
        verdict_frames: list[dict] = []
        call_frames: list[dict] = []
        for tail in tails:
            pairs = (
                tail.replay(limit) if backfill and limit is not None else tail.poll()
            )
            for line, payload in pairs:
                if tail.name == VERDICTS_STORE:
                    frame = self._verdict_frame(
                        payload, line=line, path=tail.path, backfill=backfill
                    )
                    bucket = verdict_frames
                else:
                    frame = self._call_frame(
                        payload, line=line, path=tail.path, backfill=backfill
                    )
                    bucket = call_frames
                if frame is None:
                    tail.malformed += 1
                    continue
                if visible is not None and not visible({"tenantId": frame["tenantId"]}):
                    continue
                bucket.append(frame)

        frames: list[dict] = []
        by_id = {call["id"]: call for call in call_frames if call["id"]}
        for frame in verdict_frames:
            target = by_id.get(frame["id"]) if frame["id"] else None
            if target is not None:
                target["verdicts"].append(
                    {
                        "verdict": frame["verdict"],
                        "eventType": frame["eventType"],
                        "severity": frame["severity"],
                        "ts": frame["ts"],
                        "source": frame["source"],
                    }
                )
                continue
            frames.append(frame)
        frames.extend(call_frames)
        return frames

    # -- the replay endpoint ------------------------------------------------
    def recent(
        self,
        *,
        tenants: Optional[Sequence[str]] = None,
        limit: Optional[int] = None,
    ) -> dict:
        """The feed header plus its replay window as plain JSON.

        The hydration call a client makes before (or between) stream
        connections: exactly the frames a fresh stream would replay, from the
        same stores, with the same honesty header.
        """
        window = (
            self.replay_limit
            if limit is None
            else max(1, min(int(limit), MAX_REPLAY_LIMIT))
        )
        tails = self._new_tails()
        visible = self._visible_filter(tenants)
        frames = self._backfill(tails, visible=visible, limit=window)
        for index, frame in enumerate(frames):
            frame["seq"] = index
        return {
            "feed": self.header_frame(tails, tenants=tenants, replayed=len(frames)),
            "frames": frames,
        }


#: Re-exported so a caller can read the surface default without importing the
#: fleet module directly (the reader itself is the fleet projection's).
__all__ = [
    "LiveFeed",
    "StoreTail",
    "LIVE_FEED_SURFACE",
    "SSE_EVENT",
    "read_surface_default",
    "surface_enabled",
]
