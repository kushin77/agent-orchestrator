"""portal.server.fleet — the fleet-state projection adapter (issue #331).

WHY this exists: the terminal dashboard (``fleet/console.py``) renders the
fleet's live state into one tmux pane. A remote operator cannot open that pane,
filter it, or feed it into another system. This module is the *server half* of
the web single-pane-of-glass: it exposes the very same projection over HTTP so a
browser tab receives exactly what the dashboard would have drawn.

Cannibalize, do not duplicate (issue #331). The projection itself is
``fleet/console.py``'s ``snapshot()`` — the one place that gathers fleet state.
This adapter imports that module and calls it; it re-implements no reader. The
event history is ``console.events_snapshot`` (the same ``slog.jsonl`` tail the
dashboard shows) and the change signal is a fingerprint of the same rung
heartbeats the dashboard reads, so a pushed frame always describes a state the
dashboard would also have rendered.

Offline by construction: no sockets, no TTY, no tmux. ``snapshot()`` reads the
fleet's own files (its one optional ``gh`` call already degrades to a cache on
failure), so the surface boots and serves with no terminal and no network
egress. The surface ships **feature-flag-gated OFF** (GR-5): the flag is
declared in ``infra/feature-flags/registry.yaml`` under ``surfaces`` and read
here, and while it is off every ``/api/fleet/*`` route is refused by the app.
"""

from __future__ import annotations

import contextlib
import importlib.util
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

from portal.server import surface_state

#: The registry surface key that gates this endpoint family.
FLEET_SURFACE = "fleet_projection"
#: The flag declaration read at boot (repo-root relative).
REGISTRY_RELATIVE = Path("infra") / "feature-flags" / "registry.yaml"
#: Explicit argument -> this environment variable -> :data:`REGISTRY_RELATIVE`.
#: The same resolution the runtime stores use (``portal/server/surface_state.py``
#: for the rollback overlay, ``portal/server/control_audit.py`` for its rails), so
#: a probe can drive the REAL application against a fixture declaration built
#: from the committed one without copying the tree, and a deployment can point
#: the console at a mounted declaration.
REGISTRY_ENV = "AO_SURFACE_REGISTRY"
#: The SSE event name each pushed frame carries.
SSE_EVENT = "snapshot"
#: Seconds between heartbeat polls on the push channel.
DEFAULT_POLL_SECONDS = 1.0
#: The console is loaded under a private name so the generic basename
#: ``console`` can never collide with another import on ``sys.path``.
_CONSOLE_MODULE_NAME = "ao_fleet_console"


def read_registry_surfaces(
    repo_root: Path | str,
    *,
    registry_path: Optional[Path | str] = None,
) -> Optional[dict[str, Any]]:
    """The registry's whole ``surfaces`` mapping — or ``None`` = CANNOT-ASSESS.

    ``None`` means the declaration could not be read at all: the registry is
    missing, unreadable, not a mapping, or carries no ``surfaces`` mapping. A
    *readable* registry with no entry for a named surface is a different answer —
    the caller receives a mapping and finds the surface absent, which reads as
    ``off`` (an absent declaration is not an enabled surface). Keeping the two
    apart is what lets a readiness signal report CANNOT-ASSESS instead of
    inventing ``off`` for a file it never managed to read (#802).
    """
    path = (
        Path(registry_path)
        if registry_path is not None
        else Path(os.environ.get(REGISTRY_ENV) or Path(repo_root) / REGISTRY_RELATIVE)
    )
    # PyYAML is the repo's accepted stdlib+PyYAML stack. A missing PyYAML is a
    # fail-closed "off", never an enabled surface.
    try:
        import yaml
    except ImportError:
        return None
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, yaml.YAMLError):
        # yaml.YAMLError is NOT a ValueError: a malformed document raises
        # ParserError/ScannerError, so without it a corrupt declaration crashed
        # the reader instead of failing closed (measured while building #802's
        # readiness signal, which must report CANNOT-ASSESS for exactly this
        # document rather than propagate a parse error out of a health route).
        return None
    if not isinstance(document, dict):
        return None
    surfaces = document.get("surfaces")
    if not isinstance(surfaces, dict):
        return None
    return surfaces


def declares_on(entry: Any) -> bool:
    """True only when ``entry`` *explicitly* promotes the surface.

    An explicit ``default: on`` (or boolean ``True``) and nothing else: a
    missing, falsy or unrecognised ``default`` is not a promotion, so the surface
    cannot ship enabled by accident.
    """
    if not isinstance(entry, dict):
        return False
    default = entry.get("default")
    return default is True or (
        isinstance(default, str) and default.strip().lower() == "on"
    )


def read_surface_default(
    repo_root: Path | str,
    *,
    registry_path: Optional[Path | str] = None,
    surface: str = FLEET_SURFACE,
    overlay_path: Optional[Path | str] = None,
) -> str:
    """A surface's EFFECTIVE default: an engaged rollback beats the declaration.

    The rollback half of the rollout contract (#802). A runtime rollback engaged
    by the rollout anchor (``portal.server.surface_state``) reads as ``"off"``
    even while the registry still declares the surface on — one atomic file
    write takes a promoted surface dark, with no deploy and no commit. An
    *unreadable* rollback document is ``"off"`` too: a kill switch nobody can
    read must never be assumed to be disengaged.

    Then the declaration decides, and it fails closed: a missing registry, an
    unreadable/invalid document, a missing ``surfaces`` section, or a missing
    entry all read as ``"off"``. Only an explicit ``default: on`` (or boolean
    ``True``) turns the surface on.
    """
    if surface_state.is_rolled_back(repo_root, surface, path=overlay_path):
        return "off"
    surfaces = read_registry_surfaces(repo_root, registry_path=registry_path)
    if surfaces is None:
        return "off"
    return "on" if declares_on(surfaces.get(surface)) else "off"


def surface_enabled(
    repo_root: Path | str,
    *,
    registry_path: Optional[Path | str] = None,
    surface: str = FLEET_SURFACE,
) -> bool:
    """True only when the registry explicitly promotes ``surface``."""
    return (
        read_surface_default(repo_root, registry_path=registry_path, surface=surface)
        == "on"
    )


_CONSOLE_CACHE: dict[str, Any] = {}


@contextlib.contextmanager
def _fleet_dir_returned_to_the_caller(path: Path) -> Iterator[None]:
    """Run the enclosed import, then take ``<repo>/fleet`` back off ``sys.path``.

    ``fleet/console.py``'s module body puts its own directory on ``sys.path`` so
    its sibling imports resolve (``channel``, ``runtime``), and it does so
    *unconditionally* — there is no way to ask it not to. Left in place that
    entry re-points every later bare import at ``fleet/``: on a checkout
    carrying ``fleet/telemetry.py``, ``import telemetry.metering`` resolved to
    the fleet copy and died with ``ModuleNotFoundError: No module named
    'telemetry.metering'; 'telemetry' is not a package`` — long after this call
    had returned, and only for whichever caller happened to import ``telemetry``
    after the console (#968). The entry is therefore borrowed for the duration
    of the import, exactly as ``skill_studio`` borrows ``registry/`` for its own.

    Only the entries *this* import added are removed: a directory the caller had
    already placed on the path keeps its position, so a caller that put
    ``fleet/`` there deliberately (the fleet's own tests, the operator-terminal
    probe) is unaffected.
    """
    entry = str(path.resolve().parent)
    already = sys.path.count(entry)
    try:
        yield
    finally:
        while sys.path.count(entry) > already:
            sys.path.remove(entry)


def load_fleet_console(repo_root: Path | str) -> Any:
    """Import ``fleet/console.py`` (the single projection) for ``repo_root``.

    Cached per resolved repo root, so an app and a test that redirect the
    module's runtime paths address the *same* module object. Returns whatever
    ``fleet/console.py`` defines; the caller only uses the documented pure
    readers (``snapshot``, ``events_snapshot``, ``rung_specs``, ``read_json``).

    Importing the console changes nothing but ``sys.modules``: the ``fleet/``
    directory the console lends to ``sys.path`` for its own imports is given
    back when the import returns (see
    :func:`_fleet_dir_returned_to_the_caller`).
    """
    key = str(Path(repo_root).resolve())
    cached = _CONSOLE_CACHE.get(key)
    if cached is not None:
        return cached
    path = Path(repo_root) / "fleet" / "console.py"
    spec = importlib.util.spec_from_file_location(_CONSOLE_MODULE_NAME, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load the fleet console at {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[_CONSOLE_MODULE_NAME] = module
    with _fleet_dir_returned_to_the_caller(path):
        spec.loader.exec_module(module)
    _CONSOLE_CACHE[key] = module
    return module


class FleetProjection:
    """Projects ``fleet/console.py``'s snapshot over HTTP (issue #331).

    ``enabled`` is resolved from the feature-flag registry unless supplied
    explicitly (tests pass it; the server lets the registry decide). Every read
    is delegated to the console module — this class owns transport shape, not
    the projection.
    """

    def __init__(
        self,
        *,
        repo_root: Path | str,
        enabled: Optional[bool] = None,
        registry_path: Optional[Path | str] = None,
        poll_interval: float = DEFAULT_POLL_SECONDS,
        max_frames: Optional[int] = None,
        max_polls: Optional[int] = None,
        timeout: Optional[float] = None,
        sleep: Optional[Callable[[float], None]] = None,
        clock: Optional[Callable[[], float]] = None,
        console: Any = None,
    ) -> None:
        self.repo_root = Path(repo_root)
        self.registry_path = Path(registry_path) if registry_path is not None else None
        if enabled is None:
            enabled = surface_enabled(self.repo_root, registry_path=self.registry_path)
        self.enabled = bool(enabled)
        self.poll_interval = float(poll_interval)
        # Bounds/injectables so a test can drive the push channel to a
        # deterministic stop; a browser stream sets none of them.
        self.max_frames = max_frames
        self.max_polls = max_polls
        self.timeout = timeout
        self._sleep = sleep
        self._clock = clock
        self._console = console

    @property
    def console(self) -> Any:
        """The ``fleet/console.py`` module (loaded on first use)."""
        if self._console is None:
            self._console = load_fleet_console(self.repo_root)
        return self._console

    # -- reads --------------------------------------------------------------
    def snapshot(self) -> dict:
        """The exact projection ``fleet/console.py snapshot()`` gathers."""
        return self.console.snapshot()

    def events(self, limit: int) -> list[dict]:
        """The last ``limit`` parsed ``slog.jsonl`` records."""
        return self.console.events_snapshot(int(limit))

    def heartbeat_signature(self) -> tuple:
        """A content fingerprint of every rung heartbeat.

        A frame is pushed exactly when this changes. The heartbeat's derived
        ``beat_age`` is deliberately excluded: it ticks every second and would
        push a frame per poll even when nothing happened.
        """
        signature: list[tuple[str, Optional[str]]] = []
        for name, _pattern, beat_path in self.console.rung_specs():
            beat = self.console.read_json(beat_path)
            signature.append(
                (name, None if beat is None else json.dumps(beat, sort_keys=True))
            )
        return tuple(signature)

    # -- the push channel ---------------------------------------------------
    def sse_frame(self) -> str:
        """One server-sent-events frame carrying the current snapshot."""
        payload = json.dumps(self.snapshot(), separators=(",", ":"))
        return f"event: {SSE_EVENT}\ndata: {payload}\n\n"

    def stream(
        self,
        *,
        poll_interval: Optional[float] = None,
        max_frames: Optional[int] = None,
        max_polls: Optional[int] = None,
        timeout: Optional[float] = None,
        sleep: Optional[Callable[[float], None]] = None,
        clock: Optional[Callable[[], float]] = None,
    ) -> Iterator[str]:
        """Yield an SSE frame immediately, then one per rung-heartbeat change.

        The initial frame guarantees the browser paints without waiting; after
        that a frame is emitted only when the heartbeat fingerprint changes, so
        the channel is quiet while the fleet is idle. The optional bounds
        (``max_frames``, ``max_polls``, ``timeout``) let a test stop the loop
        deterministically; a real stream runs until the client disconnects.
        """
        interval = self.poll_interval if poll_interval is None else float(poll_interval)
        frames_bound = self.max_frames if max_frames is None else max_frames
        polls_bound = self.max_polls if max_polls is None else max_polls
        time_bound = self.timeout if timeout is None else timeout
        sleeper = self._sleep if sleep is None else sleep
        timer = self._clock if clock is None else clock
        sleeper = time.sleep if sleeper is None else sleeper
        timer = time.monotonic if timer is None else timer

        started = timer()
        signature: Any = object()  # sentinel: the first pass always emits
        frames = 0
        polls = 0
        while True:
            current = self.heartbeat_signature()
            if current != signature:
                signature = current
                frames += 1
                yield self.sse_frame()
                if frames_bound is not None and frames >= frames_bound:
                    return
            if polls_bound is not None and polls >= polls_bound:
                return
            if time_bound is not None and (timer() - started) >= time_bound:
                return
            polls += 1
            sleeper(interval)
