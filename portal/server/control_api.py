"""portal.server.control_api — the remote control API (issue #554, RC-3 of #551).

WHY this exists. EPIC #551 exists because the fleet can be commanded only from
its own keyboard: ``docs/REMOTE-CONTROL-GAP-ANALYSIS.md`` §2.6 measures eighteen
verbs in ``fleet/control.py``, thirteen in ``fleet/channel.py`` and twenty-two
across the governance CLIs, and **zero** reachable from off the host — every one
is delivered by ``os.kill``, a ``.fleet/`` flag file, the host crontab or ``tmux
attach``. This module is the server half of the control channel
[`ADR-0025`](../../docs/decision-records/ADR-0025-remote-control-transport.md)
fixes: ``POST`` routes on the existing console app that expose the closed
vocabulary RC-2 declared over HTTP to an *identified* caller — and refuse
everything else.

Delegate, never re-derive. The vocabulary is **consumed** from
``control-plane/control/verbs.yaml`` (issue #553): no verb id, capability,
effect class or refusal code is written in this file, and a request that names a
verb the registry does not declare is refused rather than served. Every command
is delivered by running the lever's **own CLI at the path the registry names**
(``source`` + ``local``) — the API holds no second implementation of any control
action, and a lever that declines is reported as a refusal, never retried
another way. A subprocess rather than an in-process import is deliberate: the
registry declares a lever *as a file and a local verb* and ``fleet/control.py``'s
argparse subcommands are its documented entry point; the levers import sibling
modules by bare name (``runtime``, ``claims``, ``order``, ``snapshot``) that
would collide inside one process; a lever's ``SystemExit`` or ``argparse``
``exit()`` must not escape into an HTTP request; and *"the local lever is
unreachable"* is only a meaningful refusal across a process boundary.

The one choke point — RC-4's extension point. Every **mutating** request passes
through :meth:`RemoteControl.apply_command` and through nothing else: the route
resolves the verb, authorises the caller, and then hands the command to that one
function, which performs the duplicate-in-flight refusal, the sanctioned
delegation and the effect record. No route touches a lever directly, which is
what makes RC-4 (#555) a one-line change to land: RC-4 ships
``portal/server/control_audit.py`` — the exactly-once store, the audit record on
the **existing** ``telemetry/ledger/`` + ``.fleet/slog.jsonl`` rails, and the
refusal path — behind the :class:`CommandLedger` seam this module already calls,
and the single line that changes is the collaborator built in ``__init__``. The
call site does not move, because the funnel already exists. (Today's ledger,
:class:`InFlightCommands`, is RC-3's own half of the ``409``: a command *id
already in flight* is refused. It stores nothing on completion on purpose —
returning the original receipt for a replay of a *finished* command is RC-4's
exactly-once store, not the transport's duplicate guard.)

Flag discipline. The family is one surface, ``surfaces.remote_control`` in
``infra/feature-flags/registry.yaml``, shipped **OFF** (GR-5), and the flag is
evaluated **before authN** — an unpromoted surface is *invisible*, not merely
unauthorised — exactly as ``docs/LIVE-DATA-BRIDGE.md`` and ADR-0025 D1.1 fix it.

The refusal matrix, in the order it is applied. Each row names its authority, so
a new failure mode cannot be introduced without a decision:

===================================  =================================  ==========================
refusal                              when                               authority
===================================  =================================  ==========================
``404 feature_disabled``             ``surfaces.remote_control`` off    ADR-0025 D1.1, GR-5
``401 unauthorized``                 no verified ``os-session-token``   ADR-0025 D2.2
``405 method_not_allowed``           anything but ``POST``              the family is POST-only
``422 unknown_verb``                 not in RC-2's closed vocabulary    ADR-0025 D2
``403 verb_not_exposed``             declared ``exposed: false``        RC-2's registry
``403 scope_denied``                 the scope gate denied              ADR-0025 D2.3
``403 permission_denied``            the permission gate denied         ADR-0025 D2.3
``409 duplicate_command``            the same command id is in flight   the issue's matrix
``409 lever_refused``                the lever declined (exit 1)        the lever is the authority
``503 lever_unreachable``            lever absent, timed out, exit 2    the issue's matrix
``503 vocabulary_unavailable``       the registry cannot be read        fail closed, never guess
``400 invalid_request``              the request body is malformed      the console's own shape
===================================  =================================  ==========================

``401`` precedes the verb lookup and ``405`` precedes it too, so an
unauthenticated probe learns nothing about the vocabulary (ADR-0025 D2.2: *"a
control action with no verified principal is refused 401 before the verb is
looked up"*). ``422`` is the family-level code for "this is not a verb this API
serves" — the registry's own text for it, and the reason RC-2 bans it per verb.
A verb the registry *withholds* is a different case with a different answer:
it exists, and the operator is being told it is not reachable remotely.

Authorisation. The two gates of ``identity/rbac``, in the only safe order
(scope, then permission), evaluated through the console's **own** store —
``portal.server.fleet_authz.FleetAuthorizer`` (issue #333) — so the control path
adds no second policy engine and no second permission language. The capability
evaluated is the registry's own ``capability`` field (RC-2 declares it; ADR-0025
D2.3 makes the action segment RC-2's to name), checked at the **platform org**,
because the thing being controlled is the fleet itself and nothing else. A
denial carries the control plane's own machine codes (``identity/cpapi``'s
``scope_denied`` / ``permission_denied``), exactly as ``fleet_authz`` maps them
for the read surface.

Authority split (ADR-0025 D3). Nothing here acts on a signal: every command is
operator-requested — it arrives on this authenticated, POST-only family — and
the record names the principal it is attributed to (D2.5). A monitoring export,
a cron or a watchdog holds no console session and therefore cannot reach this
path at all.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Protocol, Sequence

import yaml

from portal.server.app import ApiError, Response

REPO_ROOT = Path(__file__).resolve().parents[2]
# ``identity/`` is a PEP-420 namespace whose modules import their lane siblings
# by bare name (``rbac.guard``, ``cpapi.errors``); this is the same bootstrap
# ``portal/server/fleet_authz.py`` performs before importing them.
_IDENTITY_ROOT = REPO_ROOT / "identity"
if str(_IDENTITY_ROOT) not in sys.path:
    sys.path.insert(0, str(_IDENTITY_ROOT))

from cpapi import errors as cpapi_errors  # noqa: E402
from rbac.guard import guard  # noqa: E402
from rbac.model import ScopeNode  # noqa: E402

from portal.server.control_audit import ControlAudit  # noqa: E402
from portal.server.fleet import surface_enabled  # noqa: E402
from portal.server.fleet_authz import PLATFORM_ORG  # noqa: E402

#: The registry surface key that gates this endpoint family (OFF until promoted).
SURFACE = "remote_control"
#: The URL segment that opens the family: ``POST /api/control/<family>/<action>``.
ROUTE_ROOT = "control"
#: The committed vocabulary (RC-2, issue #553) — the ONE declaration of a verb.
REGISTRY_RELATIVE = Path("control-plane") / "control" / "verbs.yaml"
#: The only registry schema this module serves.
REGISTRY_SCHEMA = "cmr.control-verbs/v1"
#: The registry's own ``source`` for a verb the vocabulary itself serves. RC-2's
#: validator exempts exactly this value from its "the file must contain this
#: subcommand" direction, for the same reason: the vocabulary names itself.
SELF_SOURCE = "control-plane/control/verbs.yaml"
#: How long one lever invocation may take before it counts as unreachable. A
#: long-lived verb (``fleet.watch`` is one) is served by the SSE surface, not by
#: a one-shot route, and its timeout is reported rather than silently truncated.
DEFAULT_LEVER_TIMEOUT_SECONDS = 15.0
#: How much of a lever's output the effect record carries (a receipt, not a log).
OUTPUT_LIMIT = 2000


class VocabularyUnavailable(RuntimeError):
    """The committed vocabulary could not be read or trusted (fail closed)."""


class LeverUnreachable(RuntimeError):
    """The local lever could not be reached (absent, unstoppable or too slow)."""


# ---------------------------------------------------------------------------
# the closed vocabulary (consumed, never restated)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class VerbRow:
    """One declared control verb — the registry's own fields, named as it names them."""

    id: str
    family: str
    action: str
    source: str
    local: str
    effect_class: str
    capability: str
    audit_action: Optional[str]
    idempotent: bool
    exposed: bool
    why_not_exposed: Optional[str]

    @property
    def mutates(self) -> bool:
        """True for every effect class except ``read`` (the registry's own word)."""
        return self.effect_class != "read"

    @property
    def lever(self) -> str:
        """The lever this verb is delivered by, as the registry declares it."""
        return f"{self.source}#{self.local}"

    def as_json(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "family": self.family,
            "action": self.action,
            "source": self.source,
            "local": self.local,
            "effectClass": self.effect_class,
            "capability": self.capability,
            "auditAction": self.audit_action,
            "idempotent": self.idempotent,
            "exposed": self.exposed,
        }
        if self.why_not_exposed:
            payload["whyNotExposed"] = self.why_not_exposed
        return payload


class Vocabulary:
    """The committed control-verb registry, loaded once and served from memory.

    This class checks only what the transport needs in order to serve a verb or
    refuse it structurally: the schema id, a non-empty ``verbs`` list, and the
    fields a row is read for. The **full** contract — closed refusal set, closed
    effect classes, the two-way cross-reference against the lever files — is the
    registry's own gate (``control-plane/control/cli.py validate``, run by
    ``scripts/check-control-verbs.sh``); re-deriving it here would be a second
    authority that could disagree with the first.
    """

    def __init__(self, *, path: Path, document: Mapping[str, Any]) -> None:
        self.path = Path(path)
        schema = document.get("schema")
        if schema != REGISTRY_SCHEMA:
            raise VocabularyUnavailable(
                f"{self.path} declares schema {schema!r}, expected {REGISTRY_SCHEMA!r}"
            )
        entries = document.get("verbs")
        if not isinstance(entries, list) or not entries:
            raise VocabularyUnavailable(f"{self.path} declares no verbs[] list")
        classes = document.get("effect_classes")
        self.effect_classes: tuple[str, ...] = (
            tuple(str(key) for key in classes) if isinstance(classes, Mapping) else ()
        )
        refusals = document.get("refusals")
        self.refusals: dict[int, str] = (
            {int(code): str(text) for code, text in refusals.items()}
            if isinstance(refusals, Mapping)
            else {}
        )
        self.verbs: dict[str, VerbRow] = {}
        for index, entry in enumerate(entries):
            row = self._row(entry, index)
            self.verbs[row.id] = row

    def _row(self, entry: Any, index: int) -> VerbRow:
        where = f"{self.path} verbs[{index}]"
        if not isinstance(entry, Mapping):
            raise VocabularyUnavailable(f"{where} is not a mapping")
        verb_id = entry.get("id")
        if not isinstance(verb_id, str) or "." not in verb_id:
            raise VocabularyUnavailable(f"{where} has no `<family>.<action>` id")
        family, _, action = verb_id.partition(".")
        source = entry.get("source")
        local = entry.get("local")
        effect_class = entry.get("effect_class")
        capability = entry.get("capability")
        for field, value in (
            ("source", source), ("local", local), ("capability", capability),
        ):
            if not isinstance(value, str) or not value:
                raise VocabularyUnavailable(f"{where} ({verb_id}) has no {field}")
        if not isinstance(effect_class, str) or not effect_class:
            raise VocabularyUnavailable(f"{where} ({verb_id}) has no effect_class")
        if self.effect_classes and effect_class not in self.effect_classes:
            raise VocabularyUnavailable(
                f"{where} ({verb_id}) effect_class {effect_class!r} is outside "
                f"the declared set {sorted(self.effect_classes)}"
            )
        exposed = entry.get("exposed")
        if not isinstance(exposed, bool):
            raise VocabularyUnavailable(f"{where} ({verb_id}) has no boolean exposed")
        why = entry.get("why_not_exposed")
        if exposed is False and not why:
            raise VocabularyUnavailable(
                f"{where} ({verb_id}) is withheld without a reason"
            )
        audit_action = entry.get("audit")
        if audit_action is not None and not isinstance(audit_action, str):
            raise VocabularyUnavailable(f"{where} ({verb_id}) has a non-string audit")
        return VerbRow(
            id=verb_id,
            family=family,
            action=action,
            source=source,
            local=local,
            effect_class=effect_class,
            capability=capability,
            audit_action=audit_action,
            idempotent=bool(entry.get("idempotent")),
            exposed=exposed,
            why_not_exposed=str(why) if why else None,
        )

    @classmethod
    def load(cls, path: Path | str) -> "Vocabulary":
        """Read and validate the registry at ``path`` (raises on any doubt)."""
        target = Path(path)
        try:
            raw = target.read_text(encoding="utf-8")
        except OSError as exc:
            raise VocabularyUnavailable(f"{target} is unreadable: {exc}") from exc
        try:
            document = yaml.safe_load(raw)
        except yaml.YAMLError as exc:
            raise VocabularyUnavailable(f"{target} is not valid YAML: {exc}") from exc
        if not isinstance(document, Mapping):
            raise VocabularyUnavailable(f"{target} is not a mapping")
        return cls(path=target, document=document)

    # -- lookups ------------------------------------------------------------
    def resolve(self, parts: Sequence[str]) -> Optional[VerbRow]:
        """The verb a request path names, or ``None`` (the caller refuses 422).

        The canonical address is ``<family>/<action>``; because a verb's id *is*
        its dotted name, ``<family>.<action>`` is accepted as the same request
        rather than a second vocabulary. Anything else names no verb.
        """
        segments = [segment for segment in parts if segment]
        if len(segments) == 2:
            candidate = f"{segments[0]}.{segments[1]}"
        elif len(segments) == 1 and "." in segments[0]:
            candidate = segments[0]
        else:
            return None
        return self.verbs.get(candidate)

    def as_json(self) -> dict[str, Any]:
        """The whole closed vocabulary — what ``fleet.verbs`` serves."""
        return {
            "schema": REGISTRY_SCHEMA,
            "effectClasses": list(self.effect_classes),
            "refusals": {str(code): text for code, text in sorted(self.refusals.items())},
            "verbs": [row.as_json() for row in self.verbs.values()],
        }


# ---------------------------------------------------------------------------
# the command record path (RC-4's seam)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Command:
    """One identified control request — the key the record path is asked about.

    ``id`` is the caller's ``commandId`` when it sent one, and a minted id
    otherwise. RC-4 owns durable command-id minting and answers a replay of a
    *completed* id with the original receipt; RC-3 treats the id as an opaque
    reservation key.
    """

    id: str
    verb: str
    effect_class: str
    capability: str
    audit_action: Optional[str]
    actor: str
    lever: str
    argv: tuple[str, ...]
    idempotent: bool
    requested_at: str
    mutates: bool


@dataclass(frozen=True)
class EffectRecord:
    """What one applied command did — the transport's receipt of the effect.

    It names the audit action the registry declares for the verb and the
    principal the effect is attributed to, so RC-4's ledger append is a lookup
    rather than an invention; what RC-4 adds to it is the durable half — the
    sequence number and hash of the record it writes.

    ``output`` is what a lever *said* (a bounded receipt, never a log), and
    ``content`` is the structured payload of a verb that runs no process at all
    — today only ``fleet.verbs``, whose lever is the declaration itself.
    """

    command_id: str
    verb: str
    effect_class: str
    capability: str
    audit_action: Optional[str]
    actor: str
    idempotent: bool
    lever: str
    args: tuple[str, ...]
    exit_code: int
    output: str
    requested_at: str
    content: Optional[Any] = None

    def as_json(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "commandId": self.command_id,
            "verb": self.verb,
            "effectClass": self.effect_class,
            "capability": self.capability,
            "auditAction": self.audit_action,
            "actor": self.actor,
            "idempotent": self.idempotent,
            "lever": self.lever,
            "args": list(self.args),
            "exitCode": self.exit_code,
            "output": self.output,
            "requestedAt": self.requested_at,
        }
        if self.content is not None:
            payload["content"] = self.content
        return payload


class CommandLedger(Protocol):
    """The command record path: the seam RC-4 (``control_audit.py``) implements.

    Three calls, in this order, for every command the transport applies:

    ``begin(command)``
        Called before anything is delivered. ``None`` means "proceed"; a string
        is the refusal to answer with (a duplicate already in flight, or — once
        RC-4 lands it — a replayed or reordered command id).
    ``finish(command, record)``
        Called after the lever applied the command, with the effect record. This
        is where RC-4 appends the one audit record to the existing rails and
        stores the receipt. It is deliberately **not** called when the lever was
        unreachable or declined: a command that had no effect writes no record.
    ``end(command)``
        Called in a ``finally``, whether or not the command applied, to release
        the reservation taken by ``begin``.
    """

    def begin(self, command: Command) -> Optional[str]:
        """Reserve ``command``; return a refusal message, or ``None`` to proceed."""

    def finish(self, command: Command, record: EffectRecord) -> None:
        """Record the effect of one applied command."""

    def end(self, command: Command) -> None:
        """Release the reservation taken for ``command``."""


class InFlightCommands:
    """RC-3's half of the ``409``: a command id already in flight is refused.

    Two requests carrying the same ``commandId`` produce one effect and one
    refusal, because the second one finds the first still reserved. A lock,
    because the console serves requests on threads (``portal/server/httpd.py``
    is a ``ThreadingMixIn`` server) and the reservation is shared state.

    Nothing is stored once a command completes: a replay of a *finished* command
    returning the original receipt is RC-4's exactly-once store, and this guard
    answering it with a stored effect would be a second, weaker copy of that
    store. It is a collaborator rather than inline state in
    ``apply_command`` precisely so RC-4 can replace it wholesale.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._in_flight: set[str] = set()

    def begin(self, command: Command) -> Optional[str]:
        with self._lock:
            if command.id in self._in_flight:
                return (
                    f"command {command.id!r} for {command.verb} is already in "
                    "flight: it has been accepted and its effect is not yet known"
                )
            self._in_flight.add(command.id)
        return None

    def finish(self, command: Command, record: EffectRecord) -> None:
        """No record is kept here — see the class docstring (RC-4 fills this)."""

    def end(self, command: Command) -> None:
        with self._lock:
            self._in_flight.discard(command.id)


# ---------------------------------------------------------------------------
# the lever boundary
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class LeverResult:
    """One invocation of a lever: what ran, what it exited with, what it said."""

    argv: tuple[str, ...]
    exit_code: int
    stdout: str
    stderr: str

    @property
    def output(self) -> str:
        text = (self.stdout + self.stderr).strip()
        if len(text) <= OUTPUT_LIMIT:
            return text
        return text[:OUTPUT_LIMIT] + f"... [{len(text) - OUTPUT_LIMIT} more chars]"


class Lever(Protocol):
    """Delivers a command to the local lever the registry names."""

    def run(self, row: VerbRow, args: Sequence[str]) -> LeverResult:
        """Run the verb, or raise :class:`LeverUnreachable`."""


class ProcessLever:
    """Delivers a command by running the lever's own CLI, as a process.

    The command line is derived from the registry row and nothing else:
    ``python3 <repo>/<source> <local> [args…]``. No shell, no string
    interpolation of a caller's input, and no re-implementation of what the
    lever does — the levers are argparse CLIs whose subcommands *are* their
    documented entry point, and the caller's arguments are forwarded verbatim so
    the lever owns their meaning.
    """

    def __init__(
        self,
        *,
        repo_root: Path | str,
        timeout_seconds: float = DEFAULT_LEVER_TIMEOUT_SECONDS,
    ) -> None:
        self.repo_root = Path(repo_root)
        self.timeout_seconds = float(timeout_seconds)

    def run(self, row: VerbRow, args: Sequence[str]) -> LeverResult:
        script = self.repo_root / row.source
        if not script.is_file():
            raise LeverUnreachable(
                f"the lever {row.lever} is absent at {script} — the registry names "
                "a lever this checkout does not carry"
            )
        argv = (sys.executable, str(script), row.local, *args)
        try:
            completed = subprocess.run(
                list(argv),
                cwd=str(self.repo_root),
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise LeverUnreachable(
                f"{row.lever} did not return within {self.timeout_seconds}s — a "
                "long-lived verb is served by the SSE surface, not by a one-shot route"
            ) from exc
        except OSError as exc:
            raise LeverUnreachable(f"{row.lever} could not be started: {exc}") from exc
        return LeverResult(
            argv=tuple(argv),
            exit_code=int(completed.returncode),
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
        )


# ---------------------------------------------------------------------------
# the surface
# ---------------------------------------------------------------------------
def _command_id(body: Mapping[str, Any]) -> str:
    raw = body.get("commandId")
    if raw is None:
        return f"cmd_{uuid.uuid4().hex[:12]}"
    if not isinstance(raw, str) or not raw.strip():
        raise ApiError(
            400, "invalid_request", "commandId must be a non-empty string when sent"
        )
    return raw.strip()


def _args(body: Mapping[str, Any]) -> tuple[str, ...]:
    """The caller's arguments, forwarded verbatim to the lever's own CLI.

    The API does not model a verb's arguments: the lever's argparse parser is
    the only place that knows what ``claim`` or ``override`` requires, and
    restating it here would be a second copy of the lever's own contract.
    """
    raw = body.get("args")
    if raw is None:
        return ()
    if not isinstance(raw, (list, tuple)) or not all(
        isinstance(item, str) for item in raw
    ):
        raise ApiError(
            400, "invalid_request", "args must be a list of strings (the lever's own argv)"
        )
    return tuple(raw)


class RemoteControl:
    """The control family: transport, refusal shape, and the one choke point."""

    def __init__(
        self,
        *,
        app: Any,
        registry_path: Optional[Path | str] = None,
        lever: Optional[Lever] = None,
        commands: Optional[CommandLedger] = None,
        enabled: Optional[bool] = None,
    ) -> None:
        self.app = app
        self.repo_root = Path(getattr(app, "repo_root", REPO_ROOT))
        self.registry_path = (
            Path(registry_path)
            if registry_path is not None
            else self.repo_root / REGISTRY_RELATIVE
        )
        self.enabled = (
            surface_enabled(self.repo_root, surface=SURFACE)
            if enabled is None
            else bool(enabled)
        )
        self.lever: Lever = (
            lever if lever is not None else ProcessLever(repo_root=self.repo_root)
        )
        # RC-4 (#555) replaces exactly this line: its audited ledger implements
        # the same three calls (begin / finish / end) and appends the record.
        self.commands: CommandLedger = (
            commands
            if commands is not None
            else ControlAudit(repo_root=self.repo_root)
        )
        self._vocabulary: Optional[Vocabulary] = None

    # -- the transport ------------------------------------------------------
    def route(
        self,
        surface_parts: Sequence[str],
        method: str,
        body: Mapping[str, Any],
        cookies: Mapping[str, str],
        now_iso: str = "",
    ) -> Response:
        """Answer one ``/api/control/…`` request.

        The order is the contract: the flag (before authN), the caller, the
        method, the verb, the capability, then the command. Each refusal carries
        the status and code the matrix above names, and no refusal writes
        anything.
        """
        if not self.enabled:
            raise ApiError(
                404,
                "feature_disabled",
                "the remote control family is feature-flag-gated OFF "
                f"(infra/feature-flags/registry.yaml surfaces.{SURFACE})",
            )
        principal, _claims = self.app._require_session(cookies)
        if method != "POST":
            raise ApiError(
                405,
                "method_not_allowed",
                f"the control family is POST only (got {method}): every control "
                "verb is a command, and a read verb is commanded the same way",
            )
        row = self._vocabulary_for_this_call().resolve(surface_parts)
        if row is None:
            raise ApiError(
                422,
                "unknown_verb",
                f"{method} /api/{ROUTE_ROOT}/{'/'.join(surface_parts)} names no verb "
                f"in the closed vocabulary ({REGISTRY_RELATIVE})",
            )
        if not row.exposed:
            raise ApiError(
                403,
                "verb_not_exposed",
                f"{row.id} is declared over the control API with exposed: false — "
                f"{row.why_not_exposed}",
            )
        self._require_capability(principal, row)
        if not row.mutates:
            return self.app._ok(self.read(row, principal, body, now_iso))
        return self.app._ok(self.apply_command(row, principal, body, now_iso))

    # -- the choke point ----------------------------------------------------
    def apply_command(
        self,
        row: VerbRow,
        principal: Any,
        body: Mapping[str, Any],
        now_iso: str = "",
    ) -> dict[str, Any]:
        """THE single choke point: every mutating request passes through here.

        This is the extension point RC-4 (issue #555) fills. RC-4 lands
        ``portal/server/control_audit.py`` — the exactly-once store, the audit
        record on the existing ``telemetry/ledger/`` + ``.fleet/slog.jsonl``
        rails, and the refusal path — as the :class:`CommandLedger` this function
        already calls, and the module changes in exactly one place: the
        collaborator built in ``__init__``. The call site does not move, and no
        route needs reviewing, because no route can reach a lever any other way.

        Today the function does the two things RC-3 owns and nothing else: it
        refuses a duplicate command id that is still in flight, and it delivers
        the command to the lever the registry names through
        :meth:`_deliver` — the module's only delegation site. It returns the
        effect record, which names the audit action the applied command records.
        """
        command = self._command(row, principal, body, now_iso)
        refusal = self.commands.begin(command)
        if refusal is not None:
            raise ApiError(409, "duplicate_command", refusal)
        try:
            record = self._deliver(row, command)
            self.commands.finish(command, record)
            return record.as_json()
        finally:
            self.commands.end(command)

    def read(
        self,
        row: VerbRow,
        principal: Any,
        body: Mapping[str, Any],
        now_iso: str = "",
    ) -> dict[str, Any]:
        """Serve a declared ``read`` verb: same delivery, no reservation.

        A read mutates nothing, so it takes no command id and writes no record —
        but it travels the same path, so a read verb cannot acquire a private
        route that later grows a side effect.
        """
        return self._deliver(row, self._command(row, principal, body, now_iso)).as_json()

    # -- delivery -----------------------------------------------------------
    def _deliver(self, row: VerbRow, command: Command) -> EffectRecord:
        """The module's ONLY delegation: run the lever, or refuse by its verdict.

        The tri-state exit contract of every lever in this repo is read as what
        it means: ``0`` applied, ``1`` NOT-OK (the lever declined — a conflict
        with the state it holds, never a retry in another form), ``2``
        CANNOT-ASSESS (the lever could not give a verdict, which never reads as a
        pass). Neither failing exit writes anything.
        """
        try:
            result, content = self._run_lever(row, command.argv)
        except LeverUnreachable as exc:
            raise ApiError(503, "lever_unreachable", str(exc)) from exc
        if result.exit_code == 2:
            raise ApiError(
                503,
                "lever_unreachable",
                f"{row.lever} could not assess the command (exit 2): {result.output}",
            )
        if result.exit_code != 0:
            raise ApiError(
                409,
                "lever_refused",
                f"{row.lever} refused the command (exit {result.exit_code}): "
                f"{result.output}",
            )
        return self._effect(command, result, content=content)

    def _run_lever(
        self, row: VerbRow, args: Sequence[str]
    ) -> tuple[LeverResult, Optional[Any]]:
        """Hand one command to the lever the registry names for it.

        Returns the invocation and, for a verb that runs no process, the
        structured payload the receipt carries. One lever is not a process: the
        registry declares ``fleet.verbs`` with ``source:
        control-plane/control/verbs.yaml`` — the vocabulary is its own lever — so
        it is served from the declaration this module already holds rather than
        from a file that is not a program (RC-2's validator exempts exactly this
        source for the same reason). Everything else goes to
        :class:`ProcessLever`.
        """
        if row.source == SELF_SOURCE:
            served = LeverResult(
                argv=(SELF_SOURCE, row.local), exit_code=0, stdout="", stderr=""
            )
            return served, self._vocabulary_for_this_call().as_json()
        return self.lever.run(row, args), None

    # -- request -> command -------------------------------------------------
    def _command(
        self,
        row: VerbRow,
        principal: Any,
        body: Mapping[str, Any],
        now_iso: str,
    ) -> Command:
        actor = f"user:{getattr(principal, 'email', '') or 'unknown'}"
        return Command(
            id=_command_id(body),
            verb=row.id,
            effect_class=row.effect_class,
            capability=row.capability,
            audit_action=row.audit_action,
            actor=actor,
            lever=row.lever,
            argv=_args(body),
            idempotent=row.idempotent,
            requested_at=now_iso,
            mutates=row.mutates,
        )

    def _effect(
        self,
        command: Command,
        result: LeverResult,
        *,
        content: Optional[Any] = None,
    ) -> EffectRecord:
        return EffectRecord(
            command_id=command.id,
            verb=command.verb,
            effect_class=command.effect_class,
            capability=command.capability,
            audit_action=command.audit_action,
            actor=command.actor,
            idempotent=command.idempotent,
            lever=command.lever,
            args=command.argv,
            exit_code=result.exit_code,
            output=result.output,
            requested_at=command.requested_at,
            content=content,
        )

    # -- authorisation ------------------------------------------------------
    def _authz_authorizer(self) -> Any:
        """The console's own authorizer, or a refusal (fail closed)."""
        authorizer = getattr(self.app, "fleet_authz", None)
        if authorizer is None:  # fail closed: an app without a store cannot decide
            raise ApiError(
                503,
                "authorizer_unavailable",
                "the console carries no rbac store, so no control verb can be authorised",
            )
        return authorizer

    def _decision(self, subject: str, capability: str) -> Any:
        """The two rbac gates — scope, then permission — at the platform org.

        The console's own authorizer is consumed rather than re-built: it holds
        the rbac store, the platform role pack and this console's org directory,
        and ``_prepare`` is the one call that expresses the console's own
        super-admin rule (the local allowlist, never a token claim) as an rbac
        binding. Evaluating at ``PLATFORM_ORG`` is what ADR-0025 D2.3 means by
        fixing the resource: the thing being controlled is the fleet, so control
        is a platform-scope capability and a tenant-scoped role cannot reach it
        by holding a capability string.

        The decision takes an already-prepared *subject* rather than a principal
        so both callers of it can share one preparation: :meth:`_require_capability`
        asks it once to refuse by name, and :func:`permitted_verb_ids` asks it
        once per declared verb to answer the operator terminal's "what may this
        caller run?". Reading the same function twice is what makes the panel's
        answer and the dispatch path's answer the same answer — a second
        evaluation of the rule here is exactly the drift this module exists to
        prevent.
        """
        return guard(
            self._authz_authorizer().store,
            subject,
            ScopeNode(org_id=PLATFORM_ORG),
            capability,
        )

    def prepared_subject(self, principal: Any) -> str:
        """The rbac subject a principal's capability decisions are made for."""
        return self._authz_authorizer()._prepare(principal)

    def _require_capability(
        self, principal: Any, row: VerbRow, subject: Optional[str] = None
    ) -> None:
        """Refuse a verb the caller's platform capabilities do not reach."""
        if subject is None:
            subject = self.prepared_subject(principal)
        decision = self._decision(subject, row.capability)
        if decision.allowed:
            return
        if decision.reason == "permission":
            refusal = cpapi_errors.permission_denied(
                row.capability, missing=list(decision.missing_permissions)
            )
        else:
            refusal = cpapi_errors.scope_denied(str(decision.code or "out_of_scope"))
        raise ApiError(
            refusal.status,
            refusal.code,
            f"{refusal.message} (verb {row.id} needs {row.capability!r} at the "
            f"fleet's own org {PLATFORM_ORG!r})",
        )

    # -- the vocabulary -----------------------------------------------------
    def _vocabulary_for_this_call(self) -> Vocabulary:
        """The registry, read once per surface; unreadable is a refusal, not a 500."""
        if self._vocabulary is None:
            try:
                self._vocabulary = Vocabulary.load(self.registry_path)
            except VocabularyUnavailable as exc:
                raise ApiError(
                    503,
                    "vocabulary_unavailable",
                    f"the control vocabulary cannot be read: {exc}",
                ) from exc
        return self._vocabulary


# ---------------------------------------------------------------------------
# the caller's own vocabulary (what the operator terminal may offer)
# ---------------------------------------------------------------------------
def permitted_verb_ids(app: Any, principal: Any) -> Optional[list[str]]:
    """The exposed verbs *this* caller's capabilities reach — or ``None``.

    The operator terminal renders the closed vocabulary (``fleet.verbs``), but
    the declaration is the same for every caller: it says which verbs exist, not
    which ones the operator reading it may run. Left at that, the panel offers a
    steer button for every exposed verb in the registry — including the
    irreversible ones (``closure.retire``, ``board.reap``) — to a caller whose
    capabilities reach none of them, so every click is a 403 the panel could
    have predicted. That is the defect this function exists for (issue #1523).

    It answers with the SAME decision the dispatch path refuses with: one
    :meth:`RemoteControl._decision` per declared verb, over one prepared
    subject, against the caller's own org. A verb listed here is therefore a verb
    the plane would not refuse 403 for *this* caller, and a verb absent from the
    list is one the plane would refuse — the two can never disagree, because
    there is one implementation of the rule and this is not a second one.

    ``None`` means "cannot be assessed", and it is deliberately distinct from
    ``[]``: the surface being flag-gated OFF, or an app with no rbac store, is
    not the same claim as "this caller may run nothing", and a client that
    conflated them would either hide a working panel or offer a broken one. The
    operator terminal treats ``None`` as fail-closed — no steer button is
    rendered — and says so instead of showing an empty panel that reads as an
    idle vocabulary.
    """
    surface = _surface(app)
    if not surface.enabled:
        return None
    try:
        vocabulary = surface._vocabulary_for_this_call()
        subject = surface.prepared_subject(principal)
        return [
            row.id
            for row in vocabulary.verbs.values()
            if row.exposed and surface._decision(subject, row.capability).allowed
        ]
    except ApiError:
        # No store, no readable registry: no verdict, so no list (never a
        # partial one that would read as "you may run this" for a verb the
        # dispatch path would then refuse).
        return None


# ---------------------------------------------------------------------------
# wiring
# ---------------------------------------------------------------------------
def install(app: Any, surface: RemoteControl) -> RemoteControl:
    """Attach ``surface`` as this app's control family (the test/promotion seam).

    ``portal/server/app.py`` gains one hook line and no state: the surface is
    built on first use out of the app's own repo root, rbac store and session
    pipeline. This function exists so a test — or the RC-4 wiring — can install
    one built with a different lever, registry or command ledger instead.
    """
    app.control_surface = surface
    return surface


def _surface(app: Any) -> RemoteControl:
    """The app's control surface, built once and kept on the app."""
    surface = getattr(app, "control_surface", None)
    if surface is None:
        surface = install(app, RemoteControl(app=app))
    return surface


def control(
    app: Any,
    surface_parts: Sequence[str],
    method: str,
    body: Mapping[str, Any],
    cookies: Mapping[str, str],
    now_iso: str = "",
) -> Response:
    """The family's entry point — the one line ``portal/server/app.py`` calls."""
    return _surface(app).route(
        surface_parts, method=method, body=body, cookies=cookies, now_iso=now_iso
    )
