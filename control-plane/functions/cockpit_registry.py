#!/usr/bin/env python3
"""The cockpit function registry — loader, validator and resolver (issue #565).

WHY this exists. EPIC #551's cockpit must be able to grow a domain (FinOps, SLO,
audit, tickets, paperclip) by *declaration*. ADR-0026 decision 10 fixes that as
this registry, and decision 9 fixes the order: the closed function set is
declared **before** the client renders it, so no domain arrives as a fork of the
cockpit.

What this module is NOT. It is not a second vocabulary. Every closed set it
enforces is **consumed** from the authority that already owns it, and the gate
proves each one is the live article rather than a copy:

===========================================  ============================================
consume                                       authority (derived, never restated)
===========================================  ============================================
the effect classes, the capabilities and       ``portal.server.control_api.Vocabulary``
every verb a function binds                    over ``control-plane/control/verbs.yaml``
the API route set a function may bind          ``control.api``'s own ``Vocabulary`` +
                                               ``ROUTE_ROOT`` (RC-3, issue #554)
the live streams a function may subscribe to   ``portal.server.fleet`` /
                                               ``portal.server.live_feed`` constants
the scope levels a function may require        ``identity.rbac.model``'s own constants
the tenant scope the control path uses         ``portal.server.fleet_authz.PLATFORM_ORG``
the feature flags a function names             ``infra/feature-flags/registry.yaml``
the panels the cockpit renders                 ``fleet.console.render()``, section by
                                               section, headless
===========================================  ============================================

The findings this validator emits are named, stable and machine-checkable —
``ENDPOINT-NOT-DECLARED``, ``UNKNOWN-EFFECT-CLASS``, ``AUDIT-FORBIDDEN``,
``UNCLAIMED-MNEMONIC``, ``UNDECLARED-PANEL``, ``UNKNOWN-PARAMETER`` and the rest —
because the gate's negative controls assert that a provoked defect is refused
**by name**, not merely with a non-zero exit.

Tri-state exit contract, consumed from ``guardrails/honesty``: 0 OK / 1 NOT-OK /
2 CANNOT-ASSESS. CANNOT-ASSESS must never read as a pass.


---knowledge---
module_id: control-plane.functions.cockpit_registry
system: control-plane
app: functions
solution_class: enterprise
patterns: [consume-never-restate, declared-authority, closed-vocabulary, named-findings]
derives_from: null
owner_sme: frontend-sme
tier: L1
interfaces: [Registry, Function, Parameter, Scope, Stream, Derived, load, from_document, validate, derive, resolve_call, recommended, cockpit_panels]
invariants: "it is not a second vocabulary: every closed set it enforces is CONSUMED from the authority that already owns it, and the gate proves each is the live article rather than a copy"
gotchas: "the effect classes, capabilities and verbs come from portal.server.control_api.Vocabulary over control-plane/control/verbs.yaml, and the route set from RC-3's own Vocabulary plus ROUTE_ROOT"
related: ["#565", "#551"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

import importlib
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional

import yaml

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = Path(__file__).resolve().parent
DEFAULT_REGISTRY = PACKAGE / "functions.yaml"
SCHEMA_PATH = PACKAGE / "schema" / "functions.schema.json"
FIXTURES = PACKAGE / "fixtures"
SNAPSHOT_FIXTURE = FIXTURES / "cockpit-console.json"

REGISTRY_SCHEMA = "cmr.cockpit-functions/v1"

#: The flag registry is the one authority no module exposes a constant for; it is
#: named here once, and the schema block in functions.yaml must agree with it.
FLAGS_REGISTRY = "infra/feature-flags/registry.yaml"

#: The two live surfaces, by the module attribute that declares each. Consumed.
STREAM_MODULES: dict[str, tuple[str, str, str]] = {
    "fleet_projection": ("portal.server.fleet", "FLEET_SURFACE", "SSE_EVENT"),
    "telemetry_live_feed": ("portal.server.live_feed", "LIVE_FEED_SURFACE", "SSE_EVENT"),
}

CONSOLE_MODULE = "fleet/console.py"

ID_PATTERN = re.compile(r"^[A-Z][A-Z0-9]{1,11}$")
PARAMETER_PATTERN = re.compile(r"^[a-z][a-z0-9-]*$")
ENDPOINT_PATTERN = re.compile(r"^([a-z][a-z0-9-]*)/([a-z][a-z0-9-]*)$")
FLAG_PATTERN = re.compile(r"^surfaces\.[a-z][a-z0-9_]*$")

#: The keys a `scope` block may carry. Role information must never appear here:
#: ADR-0026 decision 9 makes role suitability additive filtering only, and the
#: one place permission may live is `identity/rbac`'s capability.
SCOPE_KEYS = frozenset({"capability", "level", "tenant"})

#: Substrings that would betray a role being used as a permission inside `scope`.
PERMISSION_SHAPED = ("role", "permit", "allow", "grant", "acl")


class RegistryError(RuntimeError):
    """The question cannot be answered (CANNOT-ASSESS: exit 2), never a pass."""


@dataclass(frozen=True)
class Finding:
    """One refusal, named. `code` is the stable machine-checkable half."""

    code: str
    detail: str

    def __str__(self) -> str:  # pragma: no cover - formatting only
        return f"{self.code}: {self.detail}"


# ---------------------------------------------------------------------------
# what the registry declares
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Parameter:
    name: str
    type: str
    required: bool
    default: Any = None
    values: tuple[str, ...] = ()

    @property
    def flag(self) -> str:
        """The lever's own argv spelling of this parameter."""
        return f"--{self.name}"


@dataclass(frozen=True)
class Scope:
    capability: str
    level: str
    tenant: str


@dataclass(frozen=True)
class Function:
    id: str
    title: str
    kind: str
    endpoints: tuple[str, ...]
    parameters: tuple[Parameter, ...]
    scope: Scope
    effect_class: str
    audit: Optional[str]
    flags: tuple[str, ...]
    roles: tuple[str, ...]
    stream: Optional[str] = None

    @property
    def mnemonics(self) -> tuple[str, ...]:
        """The API address of each endpoint this function binds."""
        return self.endpoints

    @property
    def parameter_names(self) -> tuple[str, ...]:
        return tuple(p.name for p in self.parameters)

    def parameter(self, name: str) -> Optional[Parameter]:
        for candidate in self.parameters:
            if candidate.name == name:
                return candidate
        return None


@dataclass(frozen=True)
class Registry:
    path: Path
    document: Mapping[str, Any]
    functions: Mapping[str, Function]
    kinds: Mapping[str, str] = field(default_factory=dict)
    parameter_types: Mapping[str, str] = field(default_factory=dict)
    roles: Mapping[str, str] = field(default_factory=dict)
    renders: Mapping[str, str] = field(default_factory=dict)
    consumes: Mapping[str, Any] = field(default_factory=dict)

    def __getitem__(self, function_id: str) -> Function:
        return self.functions[function_id]

    def __contains__(self, function_id: object) -> bool:
        return function_id in self.functions

    def __len__(self) -> int:
        return len(self.functions)

    @property
    def ordered(self) -> list[Function]:
        return list(self.functions.values())


# ---------------------------------------------------------------------------
# the consumed authorities, derived (never restated)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Stream:
    surface: str
    event: str
    module: str


@dataclass(frozen=True)
class Derived:
    """Everything the validator reads from the live articles."""

    #: RC-2's vocabulary, keyed by dotted verb id ("<family>.<action>").
    verbs: Mapping[str, Any]
    #: RC-3's declared route set: path -> dotted verb id, exposed verbs only.
    routes: Mapping[str, str]
    route_root: str
    effect_classes: frozenset[str]
    capabilities: frozenset[str]
    streams: Mapping[str, Stream]
    flags: frozenset[str]
    scope_levels: frozenset[str]
    transport_flag: str
    platform_org: str
    panels: tuple[str, ...]
    consumes: Mapping[str, Any]

    def verb(self, endpoint: str) -> Any:
        """The RC-2 row an `<family>/<action>` endpoint names, or ``None``."""
        match = ENDPOINT_PATTERN.match(endpoint)
        if not match:
            return None
        return self.verbs.get(f"{match.group(1)}.{match.group(2)}")

    def route_for(self, endpoint: str) -> str:
        """The declared route path an endpoint names, or "" when it names none."""
        match = ENDPOINT_PATTERN.match(endpoint)
        if not match:
            return ""
        return f"/api/{self.route_root}/{match.group(1)}/{match.group(2)}"

    def is_routable(self, endpoint: str) -> bool:
        return self.route_for(endpoint) in self.routes


def _relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError as exc:  # pragma: no cover - a relocated checkout
        raise RegistryError(f"{path} is not inside {ROOT}: {exc}") from exc


def _control_api():
    """Import RC-3's transport module (the declared route set's own authority).

    ``portal.server.control_api`` bootstraps ``identity/`` on ``sys.path`` itself
    (``rbac.guard``, ``cpapi.errors`` are imported by bare name), so the only
    thing this module has to add is the repository root for the ``portal``
    package itself.
    """
    for entry in (str(ROOT),):
        if entry not in sys.path:
            sys.path.insert(0, entry)
    try:
        return importlib.import_module("portal.server.control_api")
    except Exception as exc:  # noqa: BLE001 - any import failure is CANNOT-ASSESS
        raise RegistryError(
            f"RC-3's transport module portal/server/control_api.py cannot be imported: {exc!r}"
        ) from exc


def _rbac_model():
    identity = str(ROOT / "identity")
    if identity not in sys.path:
        sys.path.insert(0, identity)
    try:
        return importlib.import_module("rbac.model")
    except Exception as exc:  # noqa: BLE001
        raise RegistryError(
            f"identity/rbac/model.py cannot be imported: {exc!r}"
        ) from exc


def _fleet_authz():
    _control_api()
    try:
        return importlib.import_module("portal.server.fleet_authz")
    except Exception as exc:  # noqa: BLE001
        raise RegistryError(
            f"portal/server/fleet_authz.py cannot be imported: {exc!r}"
        ) from exc


def _console():
    """Import the existing cockpit renderer.

    ``fleet/console.py`` imports ``channel`` and ``runtime`` by bare name — its
    documented host-local shape — so ``fleet/`` is placed on ``sys.path`` exactly
    as that module expects.
    """
    fleet = str(ROOT / "fleet")
    if fleet not in sys.path:
        sys.path.insert(0, fleet)
    try:
        return importlib.import_module("console")
    except Exception as exc:  # noqa: BLE001
        raise RegistryError(f"fleet/console.py cannot be imported: {exc!r}") from exc


def derive() -> Derived:
    """Read every authority this registry consumes. Raises CANNOT-ASSESS."""
    control_api = _control_api()
    vocab = control_api.Vocabulary.load(ROOT / control_api.REGISTRY_RELATIVE)
    rows = dict(vocab.verbs)

    routes = {
        f"/api/{control_api.ROUTE_ROOT}/{row.family}/{row.action}": verb_id
        for verb_id, row in rows.items()
        if row.exposed
    }

    streams: dict[str, Stream] = {}
    for key, (module_name, surface_attr, event_attr) in STREAM_MODULES.items():
        try:
            module = importlib.import_module(module_name)
        except Exception as exc:  # noqa: BLE001
            raise RegistryError(f"{module_name} cannot be imported: {exc!r}") from exc
        surface = getattr(module, surface_attr, None)
        event = getattr(module, event_attr, None)
        if not surface or not event:
            raise RegistryError(
                f"{module_name} no longer declares {surface_attr}/{event_attr}"
            )
        if surface != key:
            raise RegistryError(
                f"the stream {key!r} is declared by {module_name} as {surface!r}"
            )
        streams[key] = Stream(
            surface=surface, event=str(event), module=_relative(Path(module.__file__))
        )

    flags_path = ROOT / FLAGS_REGISTRY
    try:
        flags_doc = yaml.safe_load(flags_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise RegistryError(f"{FLAGS_REGISTRY} is unreadable: {exc!r}") from exc
    surfaces = (flags_doc or {}).get("surfaces")
    if not isinstance(surfaces, Mapping) or not surfaces:
        raise RegistryError(f"{FLAGS_REGISTRY} declares no surfaces")

    rbac_model = _rbac_model()
    levels = frozenset(
        value
        for name, value in vars(rbac_model).items()
        if name.startswith("SCOPE_LEVEL_") and isinstance(value, str)
    )
    if not levels:
        raise RegistryError("identity/rbac/model.py declares no SCOPE_LEVEL_* constants")

    return Derived(
        verbs=rows,
        routes=routes,
        route_root=str(control_api.ROUTE_ROOT),
        effect_classes=frozenset(vocab.effect_classes),
        capabilities=frozenset(row.capability for row in rows.values()),
        streams=streams,
        flags=frozenset(f"surfaces.{key}" for key in surfaces),
        scope_levels=levels,
        transport_flag=f"surfaces.{control_api.SURFACE}",
        platform_org=str(_fleet_authz().PLATFORM_ORG),
        panels=cockpit_panels(),
        consumes={
            "verbs": _relative(ROOT / control_api.REGISTRY_RELATIVE),
            "transport": _relative(Path(control_api.__file__)),
            "scopes": _relative(Path(rbac_model.__file__)),
            "flags": FLAGS_REGISTRY,
            "cockpit": _relative(Path(_console().__file__)),
            "streams": {key: stream.module for key, stream in streams.items()},
        },
    )


def cockpit_panels() -> tuple[str, ...]:
    """The panels the existing cockpit renders, derived by rendering it.

    Headless and deterministic: ``fleet/console.py``'s ``render()`` is a pure
    function over a plain dict, so the fixture snapshot is enough — no live
    plane, no TTY, no tmux.
    """
    console = _console()
    document = json.loads(SNAPSHOT_FIXTURE.read_text(encoding="utf-8"))
    snapshot = document.get("snapshot") if isinstance(document, Mapping) else None
    if not isinstance(snapshot, Mapping):
        raise RegistryError(f"{SNAPSHOT_FIXTURE} carries no snapshot")
    frame = console.render(dict(snapshot))
    panels: list[str] = []
    if frame.splitlines() and frame.splitlines()[0].startswith("\u2550"):
        panels.append("HEADER")
    for title in re.findall(r"^\u2500\u2500 (.+?) \u2500+$", frame, re.M):
        panels.append(title.split(" (")[0].strip())
    if not panels:
        raise RegistryError("fleet/console.py rendered no panel (the derivation is empty)")
    return tuple(panels)


# ---------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------
def load(path: Path | str | None = None) -> Registry:
    """Read the registry. Raises :class:`RegistryError` when it is unreadable.

    A malformed row is collected as a Finding by :func:`validate`, not raised
    here: the gate must NAME every defect, not stop at the first one.
    """
    target = Path(path) if path is not None else DEFAULT_REGISTRY
    try:
        raw = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise RegistryError(f"{target} is unreadable: {exc}") from exc
    try:
        document = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise RegistryError(f"{target} is not valid YAML: {exc}") from exc
    return from_document(document, target)


def from_document(document: Any, path: Path | str | None = None) -> Registry:
    """Build a Registry from an already-parsed document (the tests' seam).

    The gate's negative controls mutate a *copy* of the committed registry and
    re-validate it; this is the one entry point both the reader and those
    controls go through, so a control exercises production code.
    """
    if not isinstance(document, Mapping):
        raise RegistryError(f"{path or DEFAULT_REGISTRY} is not a mapping")

    functions: dict[str, Function] = {}
    for index, entry in enumerate(document.get("functions") or []):
        if not isinstance(entry, Mapping):
            continue
        function_id = entry.get("id")
        if not isinstance(function_id, str) or not function_id:
            continue
        functions[function_id] = _function(entry)

    return Registry(
        path=Path(path) if path is not None else DEFAULT_REGISTRY,
        document=document,
        functions=functions,
        kinds=_mapping(document.get("kinds")),
        parameter_types=_mapping(document.get("parameter_types")),
        roles=_mapping(document.get("roles")),
        renders=_mapping(document.get("renders")),
        consumes=_mapping(document.get("consumes")),
    )


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _function(entry: Mapping[str, Any]) -> Function:
    scope = entry.get("scope") if isinstance(entry.get("scope"), Mapping) else {}
    parameters: list[Parameter] = []
    for raw in entry.get("parameters") or []:
        if not isinstance(raw, Mapping):
            continue
        name = raw.get("name")
        if not isinstance(name, str):
            continue
        values = raw.get("values")
        parameters.append(
            Parameter(
                name=name,
                type=str(raw.get("type") or ""),
                required=bool(raw.get("required")),
                default=raw.get("default"),
                values=tuple(str(v) for v in values) if isinstance(values, list) else (),
            )
        )
    return Function(
        id=str(entry.get("id", "")),
        title=str(entry.get("title", "")),
        kind=str(entry.get("kind", "")),
        endpoints=tuple(str(e) for e in (entry.get("endpoints") or [])),
        parameters=tuple(parameters),
        scope=Scope(
            capability=str(scope.get("capability", "")),
            level=str(scope.get("level", "")),
            tenant=str(scope.get("tenant", "")),
        ),
        effect_class=str(entry.get("effect_class", "")),
        audit=entry.get("audit") if isinstance(entry.get("audit"), str) else None,
        flags=tuple(str(f) for f in (entry.get("flags") or [])),
        roles=tuple(str(r) for r in (entry.get("roles") or [])),
        stream=str(entry["stream"]) if entry.get("stream") else None,
    )


# ---------------------------------------------------------------------------
# the check
# ---------------------------------------------------------------------------
def validate(registry: Registry, derived: Optional[Derived] = None) -> list[Finding]:
    """Every finding this registry has, each named. Empty list == OK."""
    derived = derived if derived is not None else derive()
    findings: list[Finding] = []

    findings.extend(_check_schema_block(registry, derived))
    findings.extend(_check_functions(registry, derived))
    findings.extend(_check_renders(registry, derived))
    findings.extend(_check_mnemonics_claimed(registry, derived))
    return findings


def _check_schema_block(registry: Registry, derived: Derived) -> list[Finding]:
    findings: list[Finding] = []
    document = registry.document

    if document.get("schema") != REGISTRY_SCHEMA:
        findings.append(
            Finding("SCHEMA", f"expected {REGISTRY_SCHEMA!r}, got {document.get('schema')!r}")
        )

    try:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RegistryError(f"{SCHEMA_PATH} is unreadable: {exc!r}") from exc

    for property_name in ("kinds", "parameter_types", "roles"):
        declared = set(registry.__dict__[property_name])
        required = set(schema["properties"][property_name]["required"])
        if declared != required:
            findings.append(
                Finding(
                    "SCHEMA",
                    f"{property_name}: expected exactly {sorted(required)}, "
                    f"got {sorted(declared)}",
                )
            )

    consumes = registry.consumes
    for key, expected in derived.consumes.items():
        actual = consumes.get(key)
        if actual != expected:
            findings.append(
                Finding(
                    "CONSUMES-DRIFT",
                    f"consumes.{key}: declares {actual!r}, the live article is {expected!r}",
                )
            )
    return findings


def _check_functions(registry: Registry, derived: Derived) -> list[Finding]:
    findings: list[Finding] = []
    seen: set[str] = set()
    for index, entry in enumerate(registry.document.get("functions") or []):
        if not isinstance(entry, Mapping):
            findings.append(Finding("SCHEMA", f"functions[{index}] is not a mapping"))
            continue
        function_id = str(entry.get("id", ""))
        where = f"functions[{index}] ({function_id or '?'})"

        if not ID_PATTERN.match(function_id):
            findings.append(
                Finding("ID-PATTERN", f"{where} is not an upper-case mnemonic")
            )
        elif function_id in seen:
            findings.append(Finding("DUPLICATE-ID", f"{function_id} is declared twice"))
        seen.add(function_id)

        if function_id not in registry.functions:
            continue
        function = registry.functions[function_id]

        for field_name in ("title", "kind", "endpoints", "parameters", "scope",
                           "effect_class", "audit", "flags", "roles"):
            if field_name not in entry:
                findings.append(
                    Finding("SCHEMA", f"{where}: missing required field {field_name!r}")
                )

        if function.kind not in registry.kinds:
            findings.append(
                Finding("UNKNOWN-KIND", f"{function_id} declares kind {function.kind!r}")
            )
        if not function.title:
            findings.append(Finding("SCHEMA", f"{where}: title is empty"))

        findings.extend(_check_endpoints(function, derived))
        findings.extend(_check_parameters(function, entry, derived))
        findings.extend(_check_scope(function, entry, derived))
        findings.extend(_check_effect_and_audit(function, derived))
        findings.extend(_check_flags(function, derived))
        findings.extend(_check_roles(function, entry, registry))
    return findings


def _check_endpoints(function: Function, derived: Derived) -> list[Finding]:
    findings: list[Finding] = []
    if not function.endpoints:
        findings.append(
            Finding(
                "NO-BINDING",
                f"{function.id} binds no endpoint: a function must name the API route it reads",
            )
        )
    for endpoint in function.endpoints:
        if not ENDPOINT_PATTERN.match(endpoint):
            findings.append(
                Finding(
                    "ENDPOINT-NOT-DECLARED",
                    f"{function.id} -> {endpoint!r} is not <family>/<action>",
                )
            )
            continue
        if derived.is_routable(endpoint):
            continue
        row = derived.verb(endpoint)
        if row is None:
            findings.append(
                Finding(
                    "ENDPOINT-NOT-DECLARED",
                    f"{function.id} -> {endpoint} names no verb in RC-3's declared route "
                    f"set ({len(derived.routes)} routes: POST /api/{derived.route_root}/"
                    f"<family>/<action>)",
                )
            )
        else:
            findings.append(
                Finding(
                    "ENDPOINT-NOT-EXPOSED",
                    f"{function.id} -> {endpoint} is declared exposed: false in RC-2 and "
                    f"cannot be reached over the control API",
                )
            )
    return findings


def _source_texts(function: Function, derived: Derived) -> dict[str, str]:
    texts: dict[str, str] = {}
    for endpoint in function.endpoints:
        row = derived.verb(endpoint)
        if row is None or row.source in texts:
            continue
        path = ROOT / row.source
        try:
            texts[row.source] = path.read_text(encoding="utf-8")
        except OSError:
            continue
    return texts


def _check_parameters(
    function: Function, entry: Mapping[str, Any], derived: Derived
) -> list[Finding]:
    findings: list[Finding] = []
    raw_parameters = entry.get("parameters")
    if isinstance(raw_parameters, list):
        for index, raw in enumerate(raw_parameters):
            if not isinstance(raw, Mapping):
                findings.append(
                    Finding("SCHEMA", f"{function.id} parameters[{index}] is not a mapping")
                )
                continue
            name = raw.get("name")
            if not isinstance(name, str) or not PARAMETER_PATTERN.match(name):
                findings.append(
                    Finding(
                        "PARAMETER-NAME",
                        f"{function.id} parameters[{index}] has no well-formed name",
                    )
                )
            for required_key in ("name", "type", "required", "default"):
                if required_key not in raw:
                    findings.append(
                        Finding(
                            "SCHEMA",
                            f"{function.id}.{name}: missing required field {required_key!r}",
                        )
                    )

    seen: set[str] = set()
    for parameter in function.parameters:
        if parameter.name in seen:
            findings.append(
                Finding("DUPLICATE-PARAMETER", f"{function.id} declares {parameter.name} twice")
            )
        seen.add(parameter.name)

        if parameter.type not in closed_parameter_types():
            findings.append(
                Finding(
                    "UNKNOWN-PARAMETER-TYPE",
                    f"{function.id}.{parameter.name} declares type {parameter.type!r}; "
                    f"RC-10's closed set is {sorted(closed_parameter_types())}",
                )
            )
        if parameter.type == "enum" and not parameter.values:
            findings.append(
                Finding(
                    "ENUM-VALUES",
                    f"{function.id}.{parameter.name} is an enum with no closed values",
                )
            )

    sources = _source_texts(function, derived)
    for parameter in function.parameters:
        spellings = (parameter.flag, f'"{parameter.name.replace("-", "_")}"')
        if not any(found in text for text in sources.values() for found in spellings):
            findings.append(
                Finding(
                    "UNVERIFIED-PARAMETER",
                    f"{function.id}.{parameter.name} is not a parameter of any lever it "
                    f"binds ({', '.join(sorted(sources)) or 'no readable source'})",
                )
            )
            continue
        findings.extend(_check_required_spelling(function, parameter, sources))
    return findings


def closed_parameter_types() -> frozenset[str]:
    """The closed parameter-type set, read from the schema that fixes it."""
    try:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RegistryError(f"{SCHEMA_PATH} is unreadable: {exc!r}") from exc
    return frozenset(
        schema["properties"]["functions"]["items"]["properties"]["parameters"]
        ["items"]["properties"]["type"]["enum"]
    )


def _check_required_spelling(
    function: Function, parameter: Parameter, sources: Mapping[str, str]
) -> list[Finding]:
    """A parameter's requiredness must agree with the lever's own declaration.

    The lever files carry one subcommand's arguments per parser, and the same flag
    name appears in several of them (``--agent`` is required by ``claim`` and
    defaulted by ``eligible``). So the check is CONSENSUS-based: it fires only when
    every declaration of that flag in the files this function binds agrees. Where
    two subcommands disagree, the registry is stating no more than one of them and
    there is nothing to contradict — guessing at that point would be exactly the
    false positive this registry exists to avoid.
    """
    pattern = re.compile(
        r"""add_argument\(\s*["'](?:--)?%s["']([^)]*)\)""" % re.escape(parameter.name)
    )
    declarations: list[str] = []
    for text in sources.values():
        declarations.extend(match.group(1) for match in pattern.finditer(text))
    required_here = [d for d in declarations if "required=True" in d]
    defaulted_here = [d for d in declarations if "default=" in d and "required=True" not in d]
    if not declarations or (required_here and defaulted_here):
        return []
    if required_here and not parameter.required:
        return [
            Finding(
                "PARAMETER-REQUIRED-MISMATCH",
                f"{function.id}.{parameter.name} is declared optional but the lever "
                f"requires it",
            )
        ]
    if defaulted_here and parameter.required:
        return [
            Finding(
                "PARAMETER-REQUIRED-MISMATCH",
                f"{function.id}.{parameter.name} is declared required but the lever "
                f"gives it a default",
            )
        ]
    return []


def _check_scope(
    function: Function, entry: Mapping[str, Any], derived: Derived
) -> list[Finding]:
    findings: list[Finding] = []
    raw_scope = entry.get("scope") if isinstance(entry.get("scope"), Mapping) else {}
    for key in raw_scope:
        if str(key).lower() in PERMISSION_SHAPED or key not in SCOPE_KEYS:
            findings.append(
                Finding(
                    "ROLE-AS-PERMISSION",
                    f"{function.id} declares {key!r} in scope: role suitability is additive "
                    f"filtering only (ADR-0026 decision 9), permission is the capability",
                )
            )
    if function.scope.capability not in derived.capabilities:
        findings.append(
            Finding(
                "CAPABILITY-UNKNOWN",
                f"{function.id} requires capability {function.scope.capability!r}, which "
                f"RC-2's vocabulary does not declare",
            )
        )
    if function.scope.level not in derived.scope_levels:
        findings.append(
            Finding(
                "SCOPE-LEVEL-UNKNOWN",
                f"{function.id} requires level {function.scope.level!r}; identity/rbac "
                f"declares {sorted(derived.scope_levels)}",
            )
        )
    if function.scope.tenant != derived.platform_org:
        findings.append(
            Finding(
                "TENANT-MISMATCH",
                f"{function.id} requires tenant {function.scope.tenant!r}; the control "
                f"path authorises at {derived.platform_org!r}",
            )
        )

    capabilities = {
        row.capability
        for row in (derived.verb(e) for e in function.endpoints)
        if row is not None
    }
    if len(capabilities) > 1:
        findings.append(
            Finding(
                "MIXED-CAPABILITY",
                f"{function.id} binds verbs requiring {sorted(capabilities)}",
            )
        )
    elif capabilities and function.scope.capability not in capabilities:
        findings.append(
            Finding(
                "CAPABILITY-MISMATCH",
                f"{function.id} requires {function.scope.capability!r} but its verbs need "
                f"{sorted(capabilities)}",
            )
        )
    return findings


def _check_effect_and_audit(function: Function, derived: Derived) -> list[Finding]:
    findings: list[Finding] = []
    if function.effect_class not in derived.effect_classes:
        findings.append(
            Finding(
                "UNKNOWN-EFFECT-CLASS",
                f"{function.id} declares effect_class {function.effect_class!r}; RC-2's "
                f"closed set is {sorted(derived.effect_classes)}",
            )
        )
        return findings

    classes = {
        row.effect_class
        for row in (derived.verb(e) for e in function.endpoints)
        if row is not None
    }
    if len(classes) > 1:
        findings.append(
            Finding("MIXED-EFFECT-CLASS", f"{function.id} binds verbs of {sorted(classes)}")
        )
    elif classes and function.effect_class not in classes:
        findings.append(
            Finding(
                "EFFECT-CLASS-MISMATCH",
                f"{function.id} declares {function.effect_class!r} but its verbs are "
                f"{sorted(classes)}",
            )
        )

    audits = {
        row.audit_action
        for row in (derived.verb(e) for e in function.endpoints)
        if row is not None and getattr(row, "audit_action", None)
    }
    if function.effect_class == "read":
        if function.audit is not None:
            findings.append(
                Finding(
                    "AUDIT-FORBIDDEN",
                    f"{function.id} is a read function and may not declare an audit "
                    f"action ({function.audit!r})",
                )
            )
    else:
        if not function.audit:
            findings.append(
                Finding(
                    "AUDIT-REQUIRED",
                    f"{function.id} is {function.effect_class!r} and must declare the "
                    f"audit action it records",
                )
            )
        elif audits and function.audit not in audits:
            findings.append(
                Finding(
                    "AUDIT-UNKNOWN",
                    f"{function.id} declares audit {function.audit!r}, which is not an "
                    f"audit action of its verbs ({sorted(audits)})",
                )
            )
    return findings


def _check_flags(function: Function, derived: Derived) -> list[Finding]:
    findings: list[Finding] = []
    for flag in function.flags:
        if not FLAG_PATTERN.match(flag):
            findings.append(
                Finding("UNKNOWN-FLAG", f"{function.id} names {flag!r}, not surfaces.<key>")
            )
        elif flag not in derived.flags:
            findings.append(
                Finding(
                    "UNKNOWN-FLAG",
                    f"{function.id} names {flag!r}, which "
                    f"{derived.consumes['flags']} does not declare",
                )
            )
    if function.endpoints and derived.transport_flag not in function.flags:
        findings.append(
            Finding(
                "MISSING-TRANSPORT-FLAG",
                f"{function.id} binds control endpoints but does not name "
                f"{derived.transport_flag}",
            )
        )
    if function.stream:
        stream = derived.streams.get(function.stream)
        if stream is None:
            findings.append(
                Finding(
                    "UNKNOWN-STREAM",
                    f"{function.id} subscribes to {function.stream!r}; the declared "
                    f"surfaces are {sorted(derived.streams)}",
                )
            )
        else:
            flag = f"surfaces.{stream.surface}"
            if flag not in function.flags:
                findings.append(
                    Finding(
                        "MISSING-STREAM-FLAG",
                        f"{function.id} subscribes to {stream.surface} but does not name "
                        f"{flag}",
                    )
                )
    return findings


def _check_roles(
    function: Function, entry: Mapping[str, Any], registry: Registry
) -> list[Finding]:
    findings: list[Finding] = []
    if not function.roles:
        findings.append(
            Finding("ROLE-MISSING", f"{function.id} declares no role it is designed for")
        )
    for role in function.roles:
        if role not in registry.roles:
            findings.append(
                Finding(
                    "UNKNOWN-ROLE",
                    f"{function.id} declares role {role!r}; the closed set is "
                    f"{sorted(registry.roles)}",
                )
            )
    return findings


def _check_renders(registry: Registry, derived: Derived) -> list[Finding]:
    """Both directions between the rendered cockpit and the declared functions."""
    findings: list[Finding] = []
    for panel in derived.panels:
        if panel not in registry.renders:
            findings.append(
                Finding(
                    "UNDECLARED-PANEL",
                    f"the cockpit renders {panel!r} and no function declares it",
                )
            )
    for panel, function_id in registry.renders.items():
        if panel not in derived.panels:
            findings.append(
                Finding(
                    "PHANTOM-PANEL",
                    f"renders declares {panel!r}, which the cockpit does not render",
                )
            )
        if function_id not in registry.functions:
            findings.append(
                Finding(
                    "PHANTOM-PANEL",
                    f"renders maps {panel!r} to {function_id!r}, which is not declared",
                )
            )
            continue
        if registry.functions[function_id].kind != "panel":
            findings.append(
                Finding(
                    "PANEL-KIND",
                    f"renders maps the panel {panel!r} to {function_id}, which is a "
                    f"{registry.functions[function_id].kind}",
                )
            )
    return findings


def _check_mnemonics_claimed(registry: Registry, derived: Derived) -> list[Finding]:
    """Every exposed verb RC-3 serves must be claimed by a declared function."""
    findings: list[Finding] = []
    claimed: set[str] = set()
    for function in registry.functions.values():
        claimed.update(function.endpoints)
    for verb_id, row in sorted(derived.verbs.items()):
        if not row.exposed:
            continue
        address = f"{row.family}/{row.action}"
        if address not in claimed:
            findings.append(
                Finding(
                    "UNCLAIMED-MNEMONIC",
                    f"{verb_id} is exposed by RC-2 and reachable over the control API, "
                    f"and no cockpit function declares it",
                )
            )
    return findings


# ---------------------------------------------------------------------------
# resolving an invocation
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Call:
    function_id: str
    endpoint: str
    endpoint_path: str
    arguments: tuple[str, ...]
    effect_class: str
    audit: Optional[str]


def resolve_call(
    registry: Registry,
    function_id: str,
    given: Mapping[str, Any],
    route_root: Optional[str] = None,
) -> tuple[Optional[Call], list[Finding]]:
    """Resolve one operator invocation, refusing an unknown parameter BY NAME.

    Nothing here reaches a network: a call is resolved into the endpoint and the
    argv the control API would forward, which is what makes the registry
    exercisable headlessly.
    """
    if function_id not in registry.functions:
        return None, [Finding("UNKNOWN-FUNCTION", f"{function_id!r} is not declared")]

    function = registry.functions[function_id]
    findings: list[Finding] = []
    for name in sorted(given):
        if function.parameter(name) is None:
            findings.append(
                Finding(
                    "UNKNOWN-PARAMETER",
                    f"{function_id} does not declare a parameter {name!r} (declared: "
                    f"{list(function.parameter_names) or 'none'})",
                )
            )
    for parameter in function.parameters:
        if parameter.name not in given:
            if parameter.required:
                findings.append(
                    Finding(
                        "MISSING-PARAMETER",
                        f"{function_id} requires {parameter.name!r}",
                    )
                )
            continue
        findings.extend(_check_value(function, parameter, given[parameter.name]))
    if findings:
        return None, findings

    endpoint = function.endpoints[0]
    root = route_root or str(_control_api().ROUTE_ROOT)
    arguments = tuple(
        token
        for parameter in function.parameters
        if parameter.name in given
        for token in (parameter.flag, str(given[parameter.name]))
    )
    return (
        Call(
            function_id=function_id,
            endpoint=endpoint,
            endpoint_path=f"/api/{root}/{endpoint}",
            arguments=arguments,
            effect_class=function.effect_class,
            audit=function.audit,
        ),
        [],
    )


def _check_value(function: Function, parameter: Parameter, value: Any) -> list[Finding]:
    kinds = {
        "string": str,
        "integer": int,
        "number": (int, float),
        "boolean": bool,
        "enum": str,
    }
    expected = kinds[parameter.type]
    if parameter.type == "boolean":
        if not isinstance(value, bool):
            return [
                Finding(
                    "PARAMETER-TYPE",
                    f"{function.id}.{parameter.name} expects a boolean, got {type(value).__name__}",
                )
            ]
    elif parameter.type in ("integer", "number"):
        if isinstance(value, bool) or not isinstance(value, expected):
            return [
                Finding(
                    "PARAMETER-TYPE",
                    f"{function.id}.{parameter.name} expects a {parameter.type}, got "
                    f"{value!r}",
                )
            ]
    elif not isinstance(value, str):
        return [
            Finding(
                "PARAMETER-TYPE",
                f"{function.id}.{parameter.name} expects a {parameter.type}, got "
                f"{value!r}",
            )
        ]
    if parameter.type == "enum" and parameter.values and value not in parameter.values:
        return [
            Finding(
                "PARAMETER-VALUE",
                f"{function.id}.{parameter.name} expects one of "
                f"{list(parameter.values)}, got {value!r}",
            )
        ]
    return []


# ---------------------------------------------------------------------------
# roles: additive filtering only
# ---------------------------------------------------------------------------
def recommended(registry: Registry, role: str) -> list[Function]:
    """The functions DESIGNED FOR ``role`` — a filter that can only ever narrow.

    This is not a permission check and there is deliberately no function in this
    module that answers "may this role use this function". Selecting a function
    the role is not designed for is a UX choice, not a refusal: permission is
    ``identity/rbac``'s, evaluated per call by the control API against the
    capability the function's ``scope`` names.
    """
    if role not in registry.roles:
        raise RegistryError(
            f"{role!r} is not a declared role; the closed set is {sorted(registry.roles)}"
        )
    return [f for f in registry.ordered if role in f.roles]


def render_workspace(registry: Registry, role: str) -> list[str]:
    """The recommended function ids for a role — a subset, never a permission set."""
    return [f.id for f in recommended(registry, role)]
