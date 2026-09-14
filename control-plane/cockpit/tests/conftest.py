"""Offline doubles for the cockpit suite (issue #566) — no socket is opened.

The suite exercises the real app modules with injected seams, mirroring the
technique RC-10's fixtures and the RC-5 suite's doubles prove: a recording
transport built on the boundary's own typed errors (``error_for_status``), a
flag registry written to a temp dir, and the committed fixtures.
"""

from __future__ import annotations

import io
import re
import sys
from pathlib import Path
from typing import Any, Mapping, Optional
from urllib.error import URLError

import pytest
import yaml

#: A stale __pycache__ must never shadow this suite (measured trap).
sys.dont_write_bytecode = True

REPO_ROOT = Path(__file__).resolve().parents[3]
PACKAGE_DIR = Path(__file__).resolve().parents[1]  # control-plane/cockpit
for entry in (str(REPO_ROOT), str(PACKAGE_DIR)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from cockpit import _paths, app as app_module, cli as cli_module  # noqa: E402
from cockpit import drill as drill_module, flags as flags_module  # noqa: E402
from cockpit import follow as follow_module, frame as frame_module  # noqa: E402
from cockpit import plane as plane_module, registry as registry_io  # noqa: E402
from cockpit import ticker as ticker_module  # noqa: E402
from integrations.paperclip.client import error_for_status  # noqa: E402
from integrations.paperclip.model import Response  # noqa: E402

#: Every surface a declared function names (consumed from functions.yaml's own
#: flags; a function can name no other surface).
ALL_SURFACES = {
    "remote_control": "on",
    "fleet_projection": "on",
    "telemetry_live_feed": "on",
}


def ok_envelope(data: Mapping[str, Any], status: int = 200) -> dict[str, Any]:
    """The console's success envelope (``portal/server/app.py::_ok``)."""
    return {
        "ok": True,
        "status": status,
        "requestId": "req_test",
        "data": dict(data),
        "error": None,
    }


def refusal_envelope(status: int, code: str, message: str) -> dict[str, Any]:
    """The console's refusal envelope (``ApiError`` -> ``error.code``)."""
    return {
        "ok": False,
        "status": status,
        "requestId": "req_test",
        "data": None,
        "error": {"code": code, "message": message},
    }


def effect_record(**overrides: Any) -> dict[str, Any]:
    """One RC-3 effect record — the receipt the API returns."""
    record: dict[str, Any] = {
        "commandId": "cmd_test",
        "verb": "fleet.pause",
        "effectClass": "hold",
        "capability": "fleet:operate",
        "auditAction": "fleet.pause",
        "actor": "user:ops@example.com",
        "idempotent": True,
        "lever": "fleet/control.py#pause",
        "args": [],
        "exitCode": 0,
        "output": "",
        "requestedAt": "2026-09-14T00:00:00Z",
    }
    record.update(overrides)
    return record


class RecordingTransport:
    """The seam's Transport from a table: records every call, raises typed errors.

    The non-2xx behaviour reproduces ``HttpTransport``'s documented construction
    (``error_for_status(status, path, str(body["error"]))``) — the same shape
    ``control-plane/cli/tests/_doubles.py`` pins for the RC-5 suite, so the two
    clients' suites cannot drift apart on the refusal shape.
    """

    def __init__(
        self,
        responses: Optional[Mapping[tuple, tuple]] = None,
        fail: Optional[Exception] = None,
    ) -> None:
        self.responses = dict(responses or {})
        self.fail = fail
        self.calls: list[dict[str, Any]] = []

    def request(self, method: str, path: str, *, json: Any = None, headers: Any = None):
        self.calls.append(
            {
                "method": method.upper(),
                "path": path,
                "body": json,
                "headers": dict(headers or {}),
            }
        )
        if self.fail is not None:
            raise self.fail
        try:
            status, body = self.responses[(method.upper(), path)]
        except KeyError as exc:
            raise URLError(f"no answer configured for {method.upper()} {path}") from exc
        if not 200 <= int(status) < 300:
            detail = str((body or {}).get("error", "")) if isinstance(body, Mapping) else ""
            raise error_for_status(int(status), path, detail)
        return Response(status=int(status), body=body, headers={})


def write_flags(
    tmp_path: Path,
    cockpit: str = "on",
    surfaces: Optional[Mapping[str, str]] = None,
) -> Path:
    """A temp feature-flag registry, ``surfaces.cockpit`` defaulted as given."""
    document: dict[str, Any] = {
        "schema_version": 1,
        "default_policy": "off",
        "surfaces": {
            "cockpit": {
                "default": cockpit,
                "promoted": False,
                "service": "portal",
                "tf_flag": "enable_portal",
                "description": "test surface",
            }
        },
    }
    for key, value in (surfaces or {}).items():
        document["surfaces"][key] = {
            "default": value,
            "promoted": False,
            "service": "portal",
            "tf_flag": "enable_portal",
            "description": "test surface",
        }
    path = tmp_path / "registry.yaml"
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    return path


def make_cockpit(
    registry: Any,
    transport: Any,
    *,
    role: str = "Analyst",
    flags_registry: Path,
    session: str = "s3cr3t",
    alerts: Optional[list[ticker_module.Alert]] = None,
    source: Optional[drill_module.DrillSource] = None,
) -> app_module.Cockpit:
    """An offline cockpit: fixture drill/alerts, stub plane, flag registry."""
    flag_state = flags_module.FlagState(repo_root=REPO_ROOT, registry_path=flags_registry)
    return app_module.Cockpit(
        registry=registry,
        fixtures=registry_io.panel_fixtures(),
        plane=plane_module.CockpitPlane(
            base_url="http://127.0.0.1:8787", transport=transport
        ),
        flag_state=flag_state.state,
        role=role,
        session=session,
        source=source if source is not None else drill_module.FixtureDrillSource(),
        alerts=alerts if alerts is not None else ticker_module.load_alerts(),
    )


def run_cli(
    argv,
    *,
    flags_registry: Optional[Path] = None,
    transport: Any = None,
    stream_transports: Optional[dict[str, follow_module.SseTransport]] = None,
    session: Optional[str] = None,
    registry_path: Optional[str] = None,
) -> tuple[int, str, str]:
    """Run the real CLI entry point with injected seams; return (rc, out, err)."""
    out, err = io.StringIO(), io.StringIO()
    rc = cli_module.main(
        argv,
        transport=transport,
        stream_transports=stream_transports,
        session=session,
        registry_path=registry_path,
        flags_registry=str(flags_registry) if flags_registry is not None else None,
        out=out,
        err=err,
    )
    return rc, out.getvalue(), err.getvalue()


def panel_ids(frame_text: str) -> set[str]:
    """The declared function ids a composed frame renders, read from the frame."""
    return set(
        re.findall(r"\u250c\u2500 ([A-Z][A-Z0-9]{0,11}) ", frame_text)
    )

@pytest.fixture
def registry():
    return registry_io.load()


@pytest.fixture
def flags_on(tmp_path):
    return write_flags(tmp_path, cockpit="on", surfaces=ALL_SURFACES)
