"""portal.server.bridge — the **versioned** live-data bridge (issue #339).

WHY this exists: the platform already gathers and serves its own state, but it
does so under four *independent, unversioned* endpoint families
(``/api/fleet/*``, ``/api/telemetry/*``, ``/api/finops/*``, ``/api/ops/*``,
``/api/portal/*``). A consumer has no single, stable, versioned address for the
platform's four state families, and no single change signal that says "the
platform's state moved". Issue #339 is the cross-cutting enabler for every
pane-of-glass surface: the data exists but is never served under one contract.

This module is the *versioned façade* over those families. It is deliberately
**not** a second projection stack: every family it serves is delegated to the
lane that already owns that data —

* ``registry``  -> :class:`portal.server.livestore.RegistrySnapshot` (the live
  ``registry/profiles`` seeds + the closed tier ladder), the same adapter the
  roster endpoint already reads (issue #348);
* ``gateway``   -> the gateway lane's own published artifacts,
  ``gateway/proxy/config/routing.yaml`` (the routing contract) and
  ``gateway/catalog/modules/*/module.json`` (the provider catalog). Nothing is
  imported from the gateway runtime and no figure is restated;
* ``telemetry`` -> the LIVE tail is :class:`portal.server.live_feed.LiveFeed`
  (issue #345, the same reader ``/api/telemetry/recent`` delegates to), and the
  budget/metering/observability surfaces are named **by reference** under the
  versioned contract (``/api/finops/*``, ``/api/ops/*``) rather than re-derived
  here;
* ``guardrails``-> :class:`portal.server.controls.ControlCatalog` over
  ``guardrails/policy/controls.yaml`` (the same catalog the policy gate reads).

The contract is ``ao.bridge/v1``. Every read is ``GET``; every read is
**re-read from its source on every call** (no cached snapshot), which is the
property that lets a registry edit become visible without a rebuild — and the
exact property the mutation proof breaks on purpose.

The push channel (``/api/v1/bridge/stream``) is a **change signal**, not a data
channel: it emits one manifest frame on connect, then a frame whenever a
family's source *revision* changes. The client re-reads the family's GET for
the data. Keeping the push channel to revisions is deliberate: the telemetry
family is tenant-scoped, and a data-carrying push would have to re-implement
that scoping per subscriber — a signal carries no tenant data at all.

The surface ships **feature-flag-gated OFF** (GR-5): the flag is declared in
``infra/feature-flags/registry.yaml`` under ``surfaces`` (``live_bridge``) and
read here through the same reader the fleet projection uses; while it is off the
app refuses every ``/api/v1/*`` route before authN.

AuthN/AuthZ: every endpoint requires a verified console session (the app's
session pipeline runs before the bridge is reached). Platform-config families
(``registry``/``gateway``/``guardrails``) are readable by any authenticated
principal, exactly like the portal-surfaces feed (``/api/portal/surfaces``).
The ``telemetry`` family is tenant-scoped: the app passes the caller's visible
tenants through the same rule the live feed uses, and a caller with no visible
tenant is refused rather than shown an empty feed.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Callable, Iterator, Optional, Sequence

from portal.server.controls import ControlCatalog
from portal.server.fleet import read_surface_default, surface_enabled
from portal.server.live_feed import LiveFeed
from portal.server.livestore import RegistrySnapshot

#: The registry surface key that gates this endpoint family.
BRIDGE_SURFACE = "live_bridge"
#: The flag declaration read at boot (repo-root relative).
REGISTRY_RELATIVE = Path("infra") / "feature-flags" / "registry.yaml"
#: The versioned contract this module serves. The ``/v1`` path segment and this
#: literal are the same promise; a breaking change is a new segment + a new id.
CONTRACT = "ao.bridge/v1"

#: The SSE event names the push channel carries.
SSE_EVENT_MANIFEST = "bridge"
SSE_EVENT_FAMILY = "bridge-family"

#: Seconds between revision polls on the push channel.
DEFAULT_POLL_SECONDS = 1.0
#: How many recent telemetry records a family read hydrates with.
DEFAULT_TELEMETRY_LIMIT = 50
#: The largest telemetry hydration window a client may request.
MAX_TELEMETRY_LIMIT = 200

#: The four state families the bridge versions.
FAMILY_REGISTRY = "registry"
FAMILY_GATEWAY = "gateway"
FAMILY_TELEMETRY = "telemetry"
FAMILY_GUARDRAILS = "guardrails"
FAMILIES: tuple[str, ...] = (
    FAMILY_REGISTRY,
    FAMILY_GATEWAY,
    FAMILY_TELEMETRY,
    FAMILY_GUARDRAILS,
)

#: The declarative artifacts each non-telemetry family reads (repo-relative).
_REGISTRY_DIR = Path("registry") / "profiles"
_GATEWAY_ROUTING = Path("gateway") / "proxy" / "config" / "routing.yaml"
_GATEWAY_MODULES = Path("gateway") / "catalog" / "modules"
_GUARDRAILS_POLICY = Path("guardrails") / "policy" / "controls.yaml"
_TELEMETRY_LEDGER_DIR = Path("telemetry") / "ledger"


def _sha256_bytes(parts: bytes) -> str:
    return hashlib.sha256(parts).hexdigest()


def _file_digest(path: Path) -> Optional[str]:
    """The sha256 of a file's bytes, or ``None`` when it cannot be read."""
    try:
        return _sha256_bytes(path.read_bytes())
    except OSError:
        return None


def _read_yaml(path: Path) -> Any:
    """Parse a YAML artifact; ``None`` when it is absent or unparseable.

    Fails *closed to None*, never to an invented value: a family whose source
    cannot be read reports ``available: false`` with the reason, and never a
    plausible-looking empty document.
    """
    import yaml

    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


class LiveBridge:
    """The versioned read transport over the platform's four state families.

    ``enabled`` is resolved from the feature-flag registry unless supplied
    explicitly (tests pass it; the server lets the registry decide). Every read
    is delegated to the owning lane's own artifact or adapter — this class owns
    the *versioned contract*, the *revision*, and the *push signal*, nothing
    else.
    """

    def __init__(
        self,
        *,
        repo_root: Path | str,
        enabled: Optional[bool] = None,
        registry_path: Optional[Path | str] = None,
        live_feed: Optional[LiveFeed] = None,
        poll_interval: float = DEFAULT_POLL_SECONDS,
        max_frames: Optional[int] = None,
        max_polls: Optional[int] = None,
        timeout: Optional[float] = None,
        sleep: Optional[Callable[[float], None]] = None,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        self.repo_root = Path(repo_root)
        self.registry_path = Path(registry_path) if registry_path is not None else None
        if enabled is None:
            enabled = surface_enabled(
                self.repo_root,
                registry_path=self.registry_path,
                surface=BRIDGE_SURFACE,
            )
        self.enabled = bool(enabled)
        self.poll_interval = float(poll_interval)
        # Bounds/injectables so a test can drive the push channel to a
        # deterministic stop; a browser stream sets none of them.
        self.max_frames = max_frames
        self.max_polls = max_polls
        self.timeout = timeout
        self._sleep = sleep
        self._clock = clock
        self._live = live_feed

    # -- shared readers -----------------------------------------------------
    @property
    def live(self) -> LiveFeed:
        """The telemetry family's live tail, **reused** from issue #345."""
        if self._live is None:
            self._live = LiveFeed(repo_root=self.repo_root)
        return self._live

    def families(self) -> tuple[str, ...]:
        """The family ids this contract serves."""
        return FAMILIES

    # -- the manifest -------------------------------------------------------
    def manifest(self) -> dict[str, Any]:
        """The versioned contract's index: one row per family + its source.

        Deterministic on purpose (no timestamp): ``revision`` is the change
        signal, and a manifest that changed on every call would make the push
        channel fire forever.
        """
        rows = [
            {
                "id": FAMILY_REGISTRY,
                "contract": CONTRACT,
                "source": "portal.server.livestore.RegistrySnapshot",
                "path": str(_REGISTRY_DIR),
                "kind": "read",
            },
            {
                "id": FAMILY_GATEWAY,
                "contract": CONTRACT,
                "source": "gateway/proxy/config/routing.yaml + gateway/catalog/modules",
                "path": str(_GATEWAY_ROUTING),
                "kind": "read",
            },
            {
                "id": FAMILY_TELEMETRY,
                "contract": CONTRACT,
                "source": "portal.server.live_feed.LiveFeed (+ delegated /api/finops, /api/ops)",
                "path": str(Path(".telemetry")),
                "kind": "read+push",
            },
            {
                "id": FAMILY_GUARDRAILS,
                "contract": CONTRACT,
                "source": "portal.server.controls.ControlCatalog",
                "path": str(_GUARDRAILS_POLICY),
                "kind": "read",
            },
        ]
        return {
            "contract": CONTRACT,
            "families": rows,
            "stream": {
                "path": "/api/v1/bridge/stream",
                "kind": "sse",
                "frame": "change-signal",
                "note": (
                    "the push channel carries a family REVISION change signal, "
                    "not tenant data; re-read the family GET for the payload"
                ),
            },
        }

    def family(self, name: str) -> dict[str, Any]:
        """Read one family by id (``registry``/``gateway``/``guardrails``)."""
        if name == FAMILY_REGISTRY:
            return self.registry()
        if name == FAMILY_GATEWAY:
            return self.gateway()
        if name == FAMILY_GUARDRAILS:
            return self.guardrails()
        if name == FAMILY_TELEMETRY:
            return self.telemetry()
        raise KeyError(name)

    # -- revision (the change signal) ---------------------------------------
    def revision(self, name: str) -> str:
        """A content digest of a family's source, stable while it is unchanged.

        This is what the push channel watches. A registry seed edited on disk
        changes ``registry``'s revision with no process restart; a call record
        appended to a telemetry store changes ``telemetry``'s revision. An
        unreadable source digests the absence itself, so "became unreadable" is
        also a change the subscriber sees.
        """
        if name == FAMILY_REGISTRY:
            parts = [str(_REGISTRY_DIR)]
            profiles_dir = self.repo_root / _REGISTRY_DIR
            catalog = profiles_dir / "catalog.yaml"
            parts.append(f"catalog={_file_digest(catalog)}")
            seeds = sorted((profiles_dir / "seeds").glob("*.yaml"))
            for seed in seeds:
                parts.append(f"{seed.name}={_file_digest(seed)}")
            return _sha256_bytes("\n".join(parts).encode("utf-8"))[:16]
        if name == FAMILY_GATEWAY:
            parts = [f"routing={_file_digest(self.repo_root / _GATEWAY_ROUTING)}"]
            modules_dir = self.repo_root / _GATEWAY_MODULES
            for module in sorted(modules_dir.glob("*/module.json")):
                parts.append(f"{module.parent.name}={_file_digest(module)}")
            return _sha256_bytes("\n".join(parts).encode("utf-8"))[:16]
        if name == FAMILY_GUARDRAILS:
            digest = _file_digest(self.repo_root / _GUARDRAILS_POLICY)
            return _sha256_bytes(f"controls={digest}".encode("utf-8"))[:16]
        if name == FAMILY_TELEMETRY:
            # The revision is the *size* of each tailed store: a record landing
            # changes it without reading (and re-parsing) the whole store.
            parts = ["telemetry"]
            for store_name, path in sorted(self.live.store_paths().items()):
                try:
                    size = path.stat().st_size
                except OSError:
                    size = -1
                parts.append(f"{store_name}={size}")
            return _sha256_bytes("\n".join(parts).encode("utf-8"))[:16]
        raise KeyError(name)

    def revisions(self) -> dict[str, str]:
        """Every family's current revision (the manifest of the push channel)."""
        return {name: self.revision(name) for name in FAMILIES}

    # -- family reads -------------------------------------------------------
    def registry(self) -> dict[str, Any]:
        """The live AgentProfile registry, re-read on every call.

        Delegated to :class:`RegistrySnapshot` — the same adapter the roster
        endpoint reads — so the bridge can never project a profile the registry
        does not publish. A registry that cannot be read is reported honestly
        (``available: false`` + reason), never as an empty roster.
        """
        try:
            snapshot = RegistrySnapshot(self.repo_root)
        except Exception as exc:  # noqa: BLE001 - unreadable source is reported
            return {
                "family": FAMILY_REGISTRY,
                "contract": CONTRACT,
                "available": False,
                "reason": str(exc),
                "revision": self.revision(FAMILY_REGISTRY),
                "profiles": [],
                "count": 0,
            }
        profiles: list[dict[str, Any]] = []
        for profile_id in snapshot.profile_ids():
            profile = snapshot.profile(profile_id)
            profiles.append(
                {
                    "id": profile.id,
                    "version": profile.version,
                    "owner": profile.owner,
                    "modelTier": profile.model_tier,
                    "model": profile.model,
                    "capabilities": list(profile.capabilities),
                }
            )
        return {
            "family": FAMILY_REGISTRY,
            "contract": CONTRACT,
            "available": True,
            "source": "portal.server.livestore.RegistrySnapshot",
            "revision": self.revision(FAMILY_REGISTRY),
            "profiles": profiles,
            "count": len(profiles),
        }

    def gateway(self) -> dict[str, Any]:
        """The model gateway's `declarative` routing surface, re-read per call.

        Reads the gateway lane's own published artifacts — never its runtime —
        so the bridge restates no provider, tier or fallback: the routing
        contract is served verbatim and the provider catalog is the module
        documents themselves.
        """
        routing = _read_yaml(self.repo_root / _GATEWAY_ROUTING)
        modules_dir = self.repo_root / _GATEWAY_MODULES
        providers: list[dict[str, Any]] = []
        for module_path in sorted(modules_dir.glob("*/module.json")):
            document = _read_yaml(module_path)
            if not isinstance(document, dict):
                continue
            versions = document.get("versions")
            source = document.get("source")
            providers.append(
                {
                    "id": document.get("id") or module_path.parent.name,
                    "name": document.get("name"),
                    "type": document.get("type"),
                    "class": document.get("class") or [],
                    "repo": (source or {}).get("repo") if isinstance(source, dict) else None,
                    "version": (versions or {}).get("latest")
                    if isinstance(versions, dict)
                    else None,
                    "features": [
                        {
                            "id": feature.get("id"),
                            "default": feature.get("default"),
                            "flags": feature.get("flags") or [],
                        }
                        for feature in (document.get("features") or [])
                        if isinstance(feature, dict)
                    ],
                }
            )
        if not isinstance(routing, dict) and not providers:
            return {
                "family": FAMILY_GATEWAY,
                "contract": CONTRACT,
                "available": False,
                "reason": (
                    f"no gateway routing contract at {_GATEWAY_ROUTING} and no "
                    f"provider modules under {_GATEWAY_MODULES}"
                ),
                "revision": self.revision(FAMILY_GATEWAY),
            }
        routing = routing if isinstance(routing, dict) else {}
        return {
            "family": FAMILY_GATEWAY,
            "contract": CONTRACT,
            "available": True,
            "source": "gateway/proxy/config/routing.yaml + gateway/catalog/modules",
            "revision": self.revision(FAMILY_GATEWAY),
            "routes": routing.get("routes") or {},
            "tierMap": routing.get("tierMap") or {},
            "providerChains": routing.get("providerChains") or {},
            "providers": providers,
        }

    def telemetry(
        self,
        *,
        tenants: Optional[Sequence[str]] = None,
        limit: Optional[int] = None,
    ) -> dict[str, Any]:
        """The telemetry family: a live tail + the delegated telemetry surfaces.

        The live tail is :meth:`LiveFeed.recent` (issue #345's own reader) so
        the bridge invents no record and restates no cost. The
        budget/metering/observability figures are **named by reference** — the
        versioned contract points at ``/api/finops/*`` and ``/api/ops/*`` under
        their own flags rather than re-deriving them here, which is the
        "wire it under the contract, do not duplicate it" rule of issue #339.
        """
        window = (
            DEFAULT_TELEMETRY_LIMIT
            if limit is None
            else max(1, min(int(limit), MAX_TELEMETRY_LIMIT))
        )
        feed = self.live.recent(tenants=tenants, limit=window)
        ledger_present = (self.repo_root / _TELEMETRY_LEDGER_DIR / "store.py").is_file()
        return {
            "family": FAMILY_TELEMETRY,
            "contract": CONTRACT,
            "available": True,
            "source": "portal.server.live_feed.LiveFeed",
            "revision": self.revision(FAMILY_TELEMETRY),
            "feed": feed,
            "ledger": {
                "present": ledger_present,
                "path": str(_TELEMETRY_LEDGER_DIR),
            },
            "surfaces": [
                {
                    "id": "live_feed",
                    "route": "/api/telemetry/stream",
                    "kind": "sse",
                    "flag": "telemetry_live_feed",
                    "scope": "tenant",
                },
                {
                    "id": "finops",
                    "route": "/api/finops/overview",
                    "kind": "read",
                    "flag": "finops_reports",
                    "scope": "tenant",
                },
                {
                    "id": "ops_health",
                    "route": "/api/ops/overview",
                    "kind": "read",
                    "flag": "ops_health",
                    "scope": "tenant",
                },
            ],
        }

    def guardrails(self) -> dict[str, Any]:
        """The guardrail policy controls, re-read per call.

        Delegated to :class:`ControlCatalog` over ``guardrails/policy`` — the
        same catalog the portal's policy gate enforces against — so the bridge
        can never present a control the policy engine does not know.
        """
        path = self.repo_root / _GUARDRAILS_POLICY
        try:
            controls = ControlCatalog.load_guardrails(path)
        except Exception as exc:  # noqa: BLE001 - unreadable source is reported
            return {
                "family": FAMILY_GUARDRAILS,
                "contract": CONTRACT,
                "available": False,
                "reason": str(exc),
                "revision": self.revision(FAMILY_GUARDRAILS),
                "controls": [],
                "count": 0,
            }
        rows = [control.as_json() for control in controls]
        return {
            "family": FAMILY_GUARDRAILS,
            "contract": CONTRACT,
            "available": True,
            "source": "portal.server.controls.ControlCatalog",
            "revision": self.revision(FAMILY_GUARDRAILS),
            "controls": rows,
            "count": len(rows),
        }

    # -- the push channel ---------------------------------------------------
    def sse_frame(self, event: str, payload: dict[str, Any]) -> str:
        """One server-sent-events frame."""
        body = json.dumps(payload, separators=(",", ":"), default=str)
        return f"event: {event}\ndata: {body}\n\n"

    def stream(
        self,
        *,
        families: Optional[Sequence[str]] = None,
        poll_interval: Optional[float] = None,
        max_frames: Optional[int] = None,
        max_polls: Optional[int] = None,
        timeout: Optional[float] = None,
        sleep: Optional[Callable[[float], None]] = None,
        clock: Optional[Callable[[], float]] = None,
    ) -> Iterator[str]:
        """Yield a manifest frame, then one frame per family-revision change.

        The first frame is the manifest (so a subscriber knows what it may
        watch) and every subsequent frame is a change signal carrying the
        family id and its new revision — never tenant data. A frame is emitted
        only when a watched family's revision actually moves, so the channel is
        quiet while the platform is idle. The optional bounds let a test stop
        the loop deterministically; a real stream runs until the client
        disconnects.
        """
        watched = tuple(families) if families else FAMILIES
        interval = self.poll_interval if poll_interval is None else float(poll_interval)
        frames_bound = self.max_frames if max_frames is None else max_frames
        polls_bound = self.max_polls if max_polls is None else max_polls
        time_bound = self.timeout if timeout is None else timeout
        sleeper = self._sleep if sleep is None else sleep
        timer = self._clock if clock is None else clock
        sleeper = time.sleep if sleeper is None else sleeper
        timer = time.monotonic if timer is None else timer

        started = timer()
        yield self.sse_frame(
            SSE_EVENT_MANIFEST,
            {"contract": CONTRACT, "watched": list(watched)},
        )
        frames = 1
        polls = 0
        seen: dict[str, str] = {}
        while True:
            for name in watched:
                try:
                    current = self.revision(name)
                except KeyError:
                    continue
                if seen.get(name) != current:
                    seen[name] = current
                    frames += 1
                    yield self.sse_frame(
                        SSE_EVENT_FAMILY,
                        {"family": name, "revision": current, "contract": CONTRACT},
                    )
                    if frames_bound is not None and frames >= frames_bound:
                        return
            if polls_bound is not None and polls >= polls_bound:
                return
            if time_bound is not None and (timer() - started) >= time_bound:
                return
            polls += 1
            sleeper(interval)


__all__ = [
    "LiveBridge",
    "BRIDGE_SURFACE",
    "CONTRACT",
    "FAMILIES",
    "FAMILY_REGISTRY",
    "FAMILY_GATEWAY",
    "FAMILY_TELEMETRY",
    "FAMILY_GUARDRAILS",
    "read_surface_default",
    "surface_enabled",
]
