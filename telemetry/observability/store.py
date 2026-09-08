"""telemetry/observability — offline trace store (issue #32, phase 5).

An append-only JSONL telemetry store plus a query/read surface.  The store is
the file-backed :class:`TelemetrySink` the recorder writes into (see
``intake.py``) and the offline source the SLO/usage/dashboard consumers read
from.  Every record is one JSON object per line in the fleet
model-call-audit shape (camelCase, ``SpanRecord.to_dict``).

Honesty rules (no-false-green):

- the reader never silently drops a corrupt line — a malformed record raises
  :class:`CorruptRecordError` so a truncated/corrupt store cannot quietly read
  as healthy;
- an empty window is *not* an empty result set that callers can mistake for
  health: queries return the real (empty) list and consumers (the SLO
  evaluator) must treat "no attempts in the window" as NOT-OK, never as 100%.
"""

from __future__ import annotations

import json
import os
from typing import Any, Iterable, Iterator, Mapping, Optional

from telemetry.observability.intake import TelemetrySink
from telemetry.observability.model import (
    KIND_TRACE,
    SpanRecord,
    Trace,
    epoch_of,
    is_attempt_kind,
)

#: JSON record envelope version (the span JSONL schema version).
SCHEMA_VERSION = 1


class CorruptRecordError(ValueError):
    """A stored JSONL line could not be parsed as a span record."""


class JsonlSpanSink(TelemetrySink):
    """Append-only file sink writing one ``SpanRecord`` JSON line per span.

    Every line is flushed on write so a crash mid-stream never loses an
    acknowledged span (the cost is acceptable for an offline store).
    """

    def __init__(self, path: str) -> None:
        self.path = os.path.abspath(path)
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._handle = open(self.path, "a", encoding="utf-8")
        self._closed = False

    def write_span(self, span: SpanRecord) -> None:  # noqa: D102
        if self._closed:
            raise RuntimeError(f"store {self.path} is closed")
        record = span.to_dict()
        record["_schemaVersion"] = SCHEMA_VERSION
        self._handle.write(json.dumps(record, sort_keys=True) + "\n")
        self._handle.flush()

    def flush(self) -> None:  # noqa: D102
        if not self._closed:
            self._handle.flush()

    def close(self) -> None:  # noqa: D102
        if not self._closed:
            self._handle.flush()
            self._handle.close()
            self._closed = True

    def __enter__(self) -> "JsonlSpanSink":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


def _parse_line(line: str, lineno: int, path: str) -> SpanRecord:
    line = line.strip()
    if not line:
        raise CorruptRecordError(f"{path}:{lineno}: empty line in store")
    try:
        data = json.loads(line)
    except json.JSONDecodeError as exc:
        raise CorruptRecordError(
            f"{path}:{lineno}: unparseable JSON line: {exc}"
        ) from exc
    if not isinstance(data, Mapping):
        raise CorruptRecordError(f"{path}:{lineno}: record is not a JSON object")
    try:
        return SpanRecord.from_dict(data)
    except (KeyError, TypeError, ValueError) as exc:
        raise CorruptRecordError(
            f"{path}:{lineno}: malformed span record: {exc}"
        ) from exc


class TraceStore:
    """Offline reader + query surface over span records.

    Sources: a JSONL file path, an iterable of :class:`SpanRecord`, or an
    iterable of raw dicts (camelCase records, e.g. gateway audit lines or
    previously serialized spans).  ``traces()`` rebuilds end-to-end request
    traces by grouping spans on ``trace_id``.
    """

    def __init__(
        self,
        *,
        path: Optional[str] = None,
        spans: Optional[Iterable[SpanRecord]] = None,
        records: Optional[Iterable[Mapping[str, Any]]] = None,
    ) -> None:
        if sum(x is not None for x in (path, spans, records)) != 1:
            raise ValueError("provide exactly one source: path, spans or records")
        self._path = os.path.abspath(path) if path else None
        if records is not None:
            spans = [SpanRecord.from_dict(r) for r in records]
        if spans is not None:
            self._spans: list[SpanRecord] = list(spans)
        else:
            self._spans = self._load(self._path)

    @staticmethod
    def _load(path: str) -> list[SpanRecord]:
        if not os.path.exists(path):
            raise FileNotFoundError(f"telemetry store not found: {path}")
        spans: list[SpanRecord] = []
        with open(path, "r", encoding="utf-8") as handle:
            for lineno, line in enumerate(handle, start=1):
                spans.append(_parse_line(line, lineno, path))
        return spans

    # -- accessors --------------------------------------------------------
    @property
    def path(self) -> Optional[str]:
        return self._path

    def all_spans(self) -> list[SpanRecord]:
        """Every span in the store (insertion order)."""
        return list(self._spans)

    def trace_ids(self) -> list[str]:
        """Distinct trace ids (correlation ids), in first-seen order."""
        seen: list[str] = []
        for span in self._spans:
            if span.trace_id not in seen:
                seen.append(span.trace_id)
        return seen

    def tenants(self) -> list[str]:
        """Distinct tenant ids, in first-seen order."""
        seen: list[str] = []
        for span in self._spans:
            if span.tenant_id not in seen:
                seen.append(span.tenant_id)
        return seen

    # -- queries ----------------------------------------------------------
    def query(
        self,
        *,
        tenant_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        service: Optional[str] = None,
        kind: Optional[str] = None,
        outcome: Optional[str] = None,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        since_iso: Optional[str] = None,
        until_iso: Optional[str] = None,
    ) -> list[SpanRecord]:
        """Filter spans on any combination of fields/time bounds.

        ``since_iso``/``until_iso`` are ISO timestamps compared on epoch
        seconds (inclusive on both ends).
        """
        since = epoch_of(since_iso) if since_iso else None
        until = epoch_of(until_iso) if until_iso else None
        result: list[SpanRecord] = []
        for span in self._spans:
            if tenant_id is not None and span.tenant_id != tenant_id:
                continue
            if agent_id is not None and span.agent_id != agent_id:
                continue
            if service is not None and span.service != service:
                continue
            if kind is not None and span.kind != kind:
                continue
            if outcome is not None and span.outcome != outcome:
                continue
            if provider is not None and span.provider != provider:
                continue
            if model is not None and span.model != model:
                continue
            ts = epoch_of(span.ts)
            if since is not None and ts < since:
                continue
            if until is not None and ts > until:
                continue
            result.append(span)
        return result

    def spans_for_trace(self, trace_id: str) -> list[SpanRecord]:
        """All spans belonging to one trace (sorted, parents first)."""
        return TraceStore._sorted(
            [s for s in self._spans if s.trace_id == trace_id]
        )

    def traces(self) -> list[Trace]:
        """Rebuild end-to-end traces by grouping spans on ``trace_id``."""
        grouped: dict[str, list[SpanRecord]] = {}
        order: list[str] = []
        for span in self._spans:
            if span.trace_id not in grouped:
                grouped[span.trace_id] = []
                order.append(span.trace_id)
            grouped[span.trace_id].append(span)
        traces: list[Trace] = []
        for trace_id in order:
            spans = TraceStore._sorted(grouped[trace_id])
            root = next((s for s in spans if s.parent_span_id is None), None)
            request_id = root.request_id if root and root.request_id else trace_id
            started_at = min((s.ts for s in spans), default="")
            trace = Trace(
                trace_id=trace_id,
                tenant_id=spans[0].tenant_id if spans else "",
                request_id=request_id,
                started_at=started_at,
                spans=spans,
            )
            traces.append(trace)
        return traces

    @staticmethod
    def _sorted(spans: Iterable[SpanRecord]) -> list[SpanRecord]:
        return sorted(
            spans,
            key=lambda s: (
                0 if s.parent_span_id is None else 1,
                epoch_of(s.ts),
                s.span_id,
            ),
        )

    def root_spans(self) -> list[SpanRecord]:
        """Spans that start a trace (parent None, root/kind trace first)."""
        return [s for s in self._spans if s.parent_span_id is None]

    def attempt_spans(
        self,
        *,
        tenant_id: Optional[str] = None,
        since_iso: Optional[str] = None,
        until_iso: Optional[str] = None,
    ) -> list[SpanRecord]:
        """Completed serve attempts (good or bad) — the SLO availability sample.

        Root trace spans (kind ``trace``) and policy rejections (blocked/
        denied) are not serve attempts and are excluded here.
        """
        out: list[SpanRecord] = []
        for span in self.query(
            tenant_id=tenant_id, since_iso=since_iso, until_iso=until_iso
        ):
            if span.kind == KIND_TRACE:
                continue
            if not is_attempt_kind(span.kind):
                continue
            if not span.is_attempt:
                continue
            out.append(span)
        return out

    def __iter__(self) -> Iterator[SpanRecord]:
        return iter(self._spans)

    def __len__(self) -> int:
        return len(self._spans)


def store_span_lines(spans: Iterable[SpanRecord]) -> list[str]:
    """Serialize spans to JSONL lines (used to build store fixtures)."""
    return [
        json.dumps({**s.to_dict(), "_schemaVersion": SCHEMA_VERSION}, sort_keys=True)
        for s in spans
    ]


__all__ = [
    "SCHEMA_VERSION",
    "CorruptRecordError",
    "JsonlSpanSink",
    "TraceStore",
    "store_span_lines",
]
