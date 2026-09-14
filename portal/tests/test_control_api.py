"""The remote control API (issue #554, RC-3 of EPIC #551) — refusals + delegation.

The family is ``POST /api/control/<family>/<action>`` on the console app. What
this suite pins, in the issue's own words:

* every route refuses ``405`` on a wrong method, ``401`` with no caller, ``403``
  for an insufficient capability, ``409`` for a duplicate in-flight command,
  ``422`` for a verb outside the vocabulary, and ``503`` when the local lever is
  unreachable — and each refusal is provoked here rather than asserted by
  inspection;
* the flag is evaluated **before** AuthN, so an unpromoted surface is *invisible*
  (a probe gets ``404``, not ``401`` — it cannot even learn that the family is
  there), mirroring ``docs/LIVE-DATA-BRIDGE.md``;
* the vocabulary is **consumed** from ``control-plane/control/verbs.yaml`` and
  never restated: the served set *is* the registry's exposed set, a withheld verb
  is refused rather than served, and no verb id is hard-coded in the module;
* every command is delivered by running the lever the registry names — proved
  against real lever scripts in a scratch tree, so the delegation is exercised
  end to end rather than mocked away;
* every **mutating** request passes through the single choke point
  (``RemoteControl.apply_command``), which is the extension point RC-4 fills.

Nothing here reads or writes the live fleet: the tests that would delegate inject
a lever that records instead of spawning, and the ones that do spawn run scripts
in a per-test tmp tree. No network, no tmux, no fleet state.
"""

from __future__ import annotations

import json
import sys
import tokenize
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from conftest import ApiClient, console_sso, login_as  # noqa: E402

from portal.server import control_api  # noqa: E402
from portal.server.app import build_app  # noqa: E402
from portal.server.control_api import (  # noqa: E402
    SURFACE,
    EffectRecord,
    InFlightCommands,
    LeverResult,
    ProcessLever,
    RemoteControl,
    Vocabulary,
    VocabularyUnavailable,
    install,
)
from portal.server.state import OrgBinding, seed_state  # noqa: E402

CONTROL_URL = "/api/control"
REGISTRY = REPO_ROOT / "control-plane" / "control" / "verbs.yaml"
FEATURE_FLAGS = REPO_ROOT / "infra" / "feature-flags" / "registry.yaml"

#: A platform-org org-admin: in scope where the fleet is controlled, short of a
#: control capability. The console's own directory seeds no such principal, so
#: the test declares one (a binding is console configuration, not a code path).
PLATFORM_ADMIN_EMAIL = "operator@platform.example.com"
#: A tenant owner — in scope somewhere, but not at the fleet's own org.
TENANT_OWNER_EMAIL = "carol@globex.example.com"


# ---------------------------------------------------------------------------
# doubles: a lever that records instead of spawning, and a ledger that counts
# ---------------------------------------------------------------------------
class RecordingLever:
    """A lever double: records the row it was handed, never spawns anything."""

    def __init__(self, *, exit_code: int = 0, output: str = "", unreachable: str = "") -> None:
        self.exit_code = exit_code
        self.output = output
        self.unreachable = unreachable
        self.calls: list[tuple[str, tuple[str, ...]]] = []
        self.on_call = None

    def run(self, row, args):
        self.calls.append((row.lever, tuple(args)))
        if self.on_call is not None:
            self.on_call()
        if self.unreachable:
            raise control_api.LeverUnreachable(self.unreachable)
        return LeverResult(
            argv=(row.source, row.local, *args),
            exit_code=self.exit_code,
            stdout=self.output,
            stderr="",
        )


class SpyLedger:
    """Records every seam call so a route cannot bypass the record path."""

    def __init__(self) -> None:
        self.begun: list[str] = []
        self.finished: list[str] = []
        self.ended: list[str] = []

    def begin(self, command):
        self.begun.append(command.verb)
        return None

    def finish(self, command, record):
        self.finished.append(command.verb)

    def end(self, command):
        self.ended.append(command.verb)


def _control_url(verb_id: str) -> str:
    family, _, action = verb_id.partition(".")
    return f"{CONTROL_URL}/{family}/{action}"


def _enabled(app, **kwargs) -> RemoteControl:
    """Install a control surface on ``app`` with the real registry and flag ON.

    The flag is forced on because the registry ships the surface OFF: the
    promotion is a registry edit, and these tests are about what the surface does
    once promoted. ``test_the_surface_ships_flag_gated_off`` pins the OFF state.
    """
    surface = RemoteControl(app=app, enabled=True, **kwargs)
    install(app, surface)
    return surface


def _app_with_platform_admin(**kwargs):
    """An app whose org directory binds a platform-org admin (a fixture principal).

    The binding is declared on the *state* the app is built from, because the
    console's rbac store is built from that directory at construction — appending
    to a live app's directory afterwards would not reach the store.
    """
    state = seed_state(repo_root=REPO_ROOT)
    state.bindings.append(OrgBinding(PLATFORM_ADMIN_EMAIL, "platform", "admin"))
    return build_app(sso=console_sso(), state=state, **kwargs)


# ---------------------------------------------------------------------------
# the flag: OFF, and evaluated before AuthN
# ---------------------------------------------------------------------------
def test_the_surface_ships_flag_gated_off():
    """The declaration is the promotion switch, and it says OFF (GR-5)."""
    registry = yaml.safe_load(FEATURE_FLAGS.read_text(encoding="utf-8"))
    entry = registry["surfaces"][SURFACE]
    assert entry["default"] in (False, "off")
    assert entry["promoted"] is False
    assert entry["service"] == "portal"
    assert entry["tf_flag"] == "enable_portal"


def test_an_unpromoted_family_is_invisible_to_a_caller_with_no_session(client):
    """404, not 401: the flag gate precedes AuthN, so the family is *absent*.

    This is the flag's whole point. If the gate ran after AuthN the answer would
    be ``401`` — which tells an unauthenticated probe that the surface exists.
    """
    status, payload = client.post(_control_url("fleet.status"))
    assert status == 404
    assert payload["error"]["code"] == "feature_disabled"
    assert SURFACE in payload["error"]["message"]


def test_an_unpromoted_family_is_invisible_to_an_authenticated_caller_too(super_client):
    """The surface is not merely unauthorised: a super-admin gets the same 404."""
    status, payload = super_client.post(_control_url("fleet.pause"))
    assert status == 404
    assert payload["error"]["code"] == "feature_disabled"


def test_a_promoted_family_answers_authn_before_anything_else(app):
    """With the flag on, no session is a 401 — and it precedes the verb lookup."""
    install(app, RemoteControl(app=app, enabled=True, lever=RecordingLever()))
    api = ApiClient(app)
    status, payload = api.post(f"{CONTROL_URL}/fleet/not-a-verb")
    assert status == 401
    assert payload["error"]["code"] == "unauthorized"


# ---------------------------------------------------------------------------
# the refusal matrix
# ---------------------------------------------------------------------------
def test_405_on_a_wrong_method(app, super_client):
    """Every control verb is a command, so the family is POST-only."""
    install(app, RemoteControl(app=app, enabled=True, lever=RecordingLever()))
    status, payload = super_client.get(_control_url("fleet.status"))
    assert status == 405
    assert payload["error"]["code"] == "method_not_allowed"


def test_422_for_a_verb_outside_the_vocabulary(app, super_client):
    install(app, RemoteControl(app=app, enabled=True, lever=RecordingLever()))
    status, payload = super_client.post(_control_url("fleet.frobnicate"))
    assert status == 422
    assert payload["error"]["code"] == "unknown_verb"


def test_422_for_a_path_that_names_no_verb(app, super_client):
    """A family is not a verb, and neither is a deeper path than a verb."""
    install(app, RemoteControl(app=app, enabled=True, lever=RecordingLever()))
    for path in (f"{CONTROL_URL}/fleet", f"{CONTROL_URL}/telepathy", f"{CONTROL_URL}/fleet/pause/now"):
        status, payload = super_client.post(path)
        assert status == 422, path
        assert payload["error"]["code"] == "unknown_verb", path


def test_a_withheld_verb_is_refused_not_served(app, super_client):
    """``fleet.live`` is declared ``exposed: false`` with a reason — so 403.

    RC-2's registry declares the refusal codes a verb may return, so a withheld
    verb cannot be refused with ``422`` (it *is* in the vocabulary); its own
    declared matrix carries ``403``, and the reason is the registry's.
    """
    install(app, RemoteControl(app=app, enabled=True, lever=RecordingLever()))
    status, payload = super_client.post(_control_url("fleet.live"))
    assert status == 403
    assert payload["error"]["code"] == "verb_not_exposed"
    assert "tmux" in payload["error"]["message"]


def test_403_when_the_caller_is_in_scope_but_lacks_the_capability():
    """The permission gate: in scope at the fleet's org, short of ``fleet:operate``."""
    app = _app_with_platform_admin()
    install(app, RemoteControl(app=app, enabled=True, lever=RecordingLever()))
    api = login_as(app, PLATFORM_ADMIN_EMAIL, "platform")
    status, payload = api.post(_control_url("fleet.pause"))
    assert status == 403
    assert payload["error"]["code"] == "permission_denied"
    assert "fleet:operate" in payload["error"]["message"]


def test_403_when_the_capability_is_out_of_scope(app):
    """The scope gate: a tenant owner is refused before the permission is read.

    Both refusals are 403, which is why the *codes* matter: ``scope_denied``
    means the caller cannot act there at all, ``permission_denied`` means it acts
    there without the right. A single-gate implementation passes the 403 half of
    this suite and fails this distinction.
    """
    install(app, RemoteControl(app=app, enabled=True, lever=RecordingLever()))
    api = login_as(app, TENANT_OWNER_EMAIL, "globex")
    status, payload = api.post(_control_url("fleet.pause"))
    assert status == 403
    assert payload["error"]["code"] == "scope_denied"


def test_400_when_the_request_shape_is_malformed(app, super_client):
    """The console's own transport answer — this is not a control refusal."""
    install(app, RemoteControl(app=app, enabled=True, lever=RecordingLever()))
    for body in ({"args": "pause"}, {"args": [1, 2]}, {"commandId": "  "}):
        status, payload = super_client.post(_control_url("fleet.pause"), body)
        assert status == 400, body
        assert payload["error"]["code"] == "invalid_request", body


def test_409_for_a_duplicate_command_already_in_flight(app, super_client):
    """The first command is still in flight when the second one arrives.

    The lever re-enters the app with the same ``commandId``, which is exactly the
    race the refusal exists for: one effect, one refusal — and the nested answer
    is read back here, so the assertion is about the real route.
    """
    nested: list[tuple[int, dict]] = []
    lever = RecordingLever()
    lever.on_call = lambda: nested.append(
        super_client.post(_control_url("fleet.pause"), {"commandId": "cmd_race"})
    )
    install(app, RemoteControl(app=app, enabled=True, lever=lever))
    status, payload = super_client.post(
        _control_url("fleet.pause"), {"commandId": "cmd_race"}
    )
    assert status == 200
    assert payload["data"]["commandId"] == "cmd_race"
    assert len(nested) == 1
    nested_status, nested_payload = nested[0]
    assert nested_status == 409
    assert nested_payload["error"]["code"] == "duplicate_command"
    assert len(lever.calls) == 1, "the duplicate must not reach the lever"


@pytest.mark.parametrize("lever_output", ["declined"])
def test_409_when_the_lever_declines(tmp_path, app, lever_output):
    """Exit 1 is NOT-OK: the lever refused. A refusal, never a silent no-op."""
    root = _scratch_repo(tmp_path, body=f"print({lever_output!r}); raise SystemExit(1)")
    surface = RemoteControl(
        app=app, enabled=True, registry_path=_scratch_registry(tmp_path),
        lever=ProcessLever(repo_root=root),
    )
    install(app, surface)
    api = login_as(app, "root@platform.example.com", "acme")
    status, payload = api.post(_control_url("fleet.pause"))
    assert status == 409
    assert payload["error"]["code"] == "lever_refused"


def test_503_when_the_lever_cannot_assess(tmp_path, app):
    """Exit 2 (CANNOT-ASSESS) must never read as a pass."""
    root = _scratch_repo(tmp_path, body="raise SystemExit(2)")
    install(
        app,
        RemoteControl(
            app=app, enabled=True, registry_path=_scratch_registry(tmp_path),
            lever=ProcessLever(repo_root=root),
        ),
    )
    api = login_as(app, "root@platform.example.com", "acme")
    status, payload = api.post(_control_url("fleet.pause"))
    assert status == 503
    assert payload["error"]["code"] == "lever_unreachable"


def test_503_when_the_lever_is_absent(app):
    """The registry names a lever this checkout does not carry (a real runner)."""
    install(app, RemoteControl(app=app, enabled=True, lever=ProcessLever(repo_root="/nonexistent-checkout")))
    api = login_as(app, "root@platform.example.com", "acme")
    status, payload = api.post(_control_url("fleet.pause"))
    assert status == 503
    assert payload["error"]["code"] == "lever_unreachable"
    assert "absent" in payload["error"]["message"]


def test_503_when_the_lever_does_not_return_in_time(tmp_path, app):
    """A long-lived verb is not servable by a one-shot route, and says so."""
    root = _scratch_repo(tmp_path, body="import time; time.sleep(30)")
    install(
        app,
        RemoteControl(
            app=app, enabled=True, registry_path=_scratch_registry(tmp_path),
            lever=ProcessLever(repo_root=root, timeout_seconds=0.25),
        ),
    )
    api = login_as(app, "root@platform.example.com", "acme")
    status, payload = api.post(_control_url("fleet.pause"))
    assert status == 503
    assert payload["error"]["code"] == "lever_unreachable"
    assert "SSE" in payload["error"]["message"]


def test_503_when_the_vocabulary_cannot_be_read(tmp_path, app):
    """An unreadable registry is a refusal, never a guess and never a 500."""
    install(
        app,
        RemoteControl(app=app, enabled=True, registry_path=tmp_path / "absent.yaml"),
    )
    api = login_as(app, "root@platform.example.com", "acme")
    status, payload = api.post(_control_url("fleet.status"))
    assert status == 503
    assert payload["error"]["code"] == "vocabulary_unavailable"


# ---------------------------------------------------------------------------
# delegation — the lever the registry names, and nothing re-derived
# ---------------------------------------------------------------------------
def test_the_module_hand_copies_no_verb_id():
    """The vocabulary is consumed, not restated: no id and no capability.

    A hard-coded verb list is how a second vocabulary starts. Comments and
    docstrings are excluded (naming a verb in prose is documentation, not a
    second source of truth), so this pins the absence in *code*: a branch on
    ``verb == "fleet.pause"``, or a copied capability string, would fail here.
    """
    source = _code_only(REPO_ROOT / "portal" / "server" / "control_api.py")
    registry = yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))
    for entry in registry["verbs"]:
        assert entry["id"] not in source, f"control_api.py restates verb {entry['id']!r}"
        assert entry["capability"] not in source, (
            f"control_api.py restates capability {entry['capability']!r}"
        )


def _code_only(path: Path) -> str:
    """The module's executable tokens, with comments and literals removed."""
    with path.open("rb") as handle:
        tokens = tokenize.tokenize(handle.readline)
        return "\n".join(
            token.string
            for token in tokens
            if "STRING" not in tokenize.tok_name[token.type]
            and token.type != tokenize.COMMENT
        )


def test_the_served_vocabulary_is_the_registrys_exposed_set(app, super_client):
    """``fleet.verbs`` serves the closed declaration — neither more nor less."""
    install(app, RemoteControl(app=app, enabled=True, lever=RecordingLever()))
    status, payload = super_client.post(_control_url("fleet.verbs"))
    assert status == 200
    served = payload["data"]["content"]
    registry = Vocabulary.load(REGISTRY)
    assert {row["id"] for row in served["verbs"]} == set(registry.verbs)
    assert {row["id"] for row in served["verbs"] if row["exposed"]} == {
        row.id for row in registry.verbs.values() if row.exposed
    }
    assert served["refusals"] == {
        str(code): text for code, text in sorted(registry.refusals.items())
    }
    assert set(served["effectClasses"]) == set(registry.effect_classes)


def test_fleet_verbs_runs_no_process(app, super_client):
    """The vocabulary is its own lever, so the route must not spawn for it."""
    lever = RecordingLever()
    install(app, RemoteControl(app=app, enabled=True, lever=lever))
    status, payload = super_client.post(_control_url("fleet.verbs"))
    assert status == 200
    assert payload["data"]["lever"] == "control-plane/control/verbs.yaml#verbs"
    assert lever.calls == []


def test_a_command_is_delivered_by_the_lever_the_registry_names(tmp_path, app):
    """End to end through a real process: the registry's source + local verb."""
    root = _scratch_repo(
        tmp_path,
        body="import json, sys; print(json.dumps(sys.argv[1:])); raise SystemExit(0)",
    )
    install(
        app,
        RemoteControl(
            app=app, enabled=True, registry_path=_scratch_registry(tmp_path),
            lever=ProcessLever(repo_root=root),
        ),
    )
    api = login_as(app, "root@platform.example.com", "acme")
    status, payload = api.post(
        _control_url("fleet.pause"), {"args": ["--note", "one effect"]}
    )
    assert status == 200
    record = payload["data"]
    assert record["lever"] == "fleet/control.py#pause"
    assert record["verb"] == "fleet.pause"
    assert json.loads(record["output"]) == ["pause", "--note", "one effect"]
    assert record["exitCode"] == 0


def test_every_exposed_mutating_verb_is_reachable_and_delegated(app, super_client):
    """No exposed verb is missing from the route table, and none is a second impl."""
    lever = RecordingLever()
    install(app, RemoteControl(app=app, enabled=True, lever=lever))
    registry = Vocabulary.load(REGISTRY)
    seen: list[str] = []
    for row in registry.verbs.values():
        if not row.exposed or not row.mutates:
            continue
        status, _payload = super_client.post(_control_url(row.id))
        assert status == 200, row.id
        seen.append(row.lever)
    assert seen == [row.lever for row in registry.verbs.values() if row.exposed and row.mutates]
    assert lever.calls == list(zip(seen, [()] * len(seen)))


def test_every_mutation_passes_through_the_single_choke_point(app, super_client):
    """No route can reach a lever except through ``apply_command`` (RC-4's seam).

    The choke point is replaced by one that answers a sentinel and delegates
    nothing: if any route had its own path to a lever, it would still succeed and
    the sentinel would be missing.
    """
    lever = RecordingLever()
    surface = RemoteControl(app=app, enabled=True, lever=lever)
    install(app, surface)
    calls: list[str] = []
    sentinel = {"chokePoint": "reached"}

    def _choke(row, principal, body, now_iso=""):
        calls.append(row.id)
        return sentinel

    surface.apply_command = _choke  # type: ignore[method-assign]
    registry = Vocabulary.load(REGISTRY)
    for row in registry.verbs.values():
        if not row.exposed or not row.mutates:
            continue
        status, payload = super_client.post(_control_url(row.id))
        assert status == 200, row.id
        assert payload["data"] == sentinel, row.id
    assert calls == [row.id for row in registry.verbs.values() if row.exposed and row.mutates]
    assert lever.calls == [], "a mutation reached the lever outside the choke point"


def test_reads_take_no_reservation_and_mutations_do(app, super_client):
    """The record path sees every command, once — and reads are not commands."""
    ledger = SpyLedger()
    install(app, RemoteControl(app=app, enabled=True, lever=RecordingLever(), commands=ledger))
    registry = Vocabulary.load(REGISTRY)
    for row in registry.verbs.values():
        if not row.exposed:
            continue
        status, _payload = super_client.post(_control_url(row.id))
        assert status == 200, row.id
    mutating = [row.id for row in registry.verbs.values() if row.exposed and row.mutates]
    reading = [row.id for row in registry.verbs.values() if row.exposed and not row.mutates]
    assert ledger.begun == mutating
    assert ledger.finished == mutating
    assert ledger.ended == mutating
    assert not set(reading) & set(ledger.begun)


def test_the_effect_record_names_the_command_the_caller_and_the_audit_action(app, super_client):
    """The record is what an audit append is a lookup on, so it names all of it."""
    install(app, RemoteControl(app=app, enabled=True, lever=RecordingLever(output="paused")))
    status, payload = super_client.post(_control_url("fleet.pause"))
    assert status == 200
    record = payload["data"]
    assert record["commandId"].startswith("cmd_")
    assert record["verb"] == "fleet.pause"
    assert record["effectClass"] == "hold"
    assert record["capability"] == "fleet:operate"
    assert record["auditAction"] == "fleet.pause"
    assert record["actor"] == "user:root@platform.example.com"
    assert record["idempotent"] is True
    assert record["output"] == "paused"
    assert record["args"] == []


def test_a_caller_supplied_command_id_is_the_one_recorded(app, super_client):
    install(app, RemoteControl(app=app, enabled=True, lever=RecordingLever()))
    status, payload = super_client.post(
        _control_url("fleet.pause"), {"commandId": "cmd_operator_1"}
    )
    assert status == 200
    assert payload["data"]["commandId"] == "cmd_operator_1"


def test_an_unreachable_lever_writes_no_record(app, super_client):
    """A command with no effect must leave nothing for RC-4 to append."""
    ledger = SpyLedger()
    install(
        app,
        RemoteControl(
            app=app, enabled=True, commands=ledger,
            lever=RecordingLever(unreachable="the lever is absent"),
        ),
    )
    status, payload = super_client.post(_control_url("fleet.pause"))
    assert status == 503
    assert payload["error"]["code"] == "lever_unreachable"
    assert ledger.finished == [], "a command with no effect recorded an effect"


def test_a_lever_refusal_writes_no_record_either(app, super_client):
    ledger = SpyLedger()
    install(
        app,
        RemoteControl(app=app, enabled=True, commands=ledger, lever=RecordingLever(exit_code=1)),
    )
    status, _payload = super_client.post(_control_url("fleet.pause"))
    assert status == 409
    assert ledger.finished == []


def test_a_refusal_never_reaches_the_lever(app, super_client):
    """Every refusal is decided before anything is delivered."""
    lever = RecordingLever()
    install(app, RemoteControl(app=app, enabled=True, lever=lever))
    super_client.get(_control_url("fleet.status"))
    super_client.post(_control_url("fleet.frobnicate"))
    super_client.post(_control_url("fleet.live"))
    assert lever.calls == []


# ---------------------------------------------------------------------------
# the vocabulary loader's own contract
# ---------------------------------------------------------------------------
def test_a_registry_without_the_declared_schema_is_refused(tmp_path):
    path = tmp_path / "verbs.yaml"
    path.write_text("schema: something/else\nverbs: []\n", encoding="utf-8")
    with pytest.raises(VocabularyUnavailable):
        Vocabulary.load(path)


def test_a_withheld_verb_without_a_reason_is_refused(tmp_path):
    """RC-2's rule, enforced at the boundary: a withheld verb names why."""
    rows = _registry_rows()
    rows[0]["exposed"] = False
    path = tmp_path / "verbs.yaml"
    path.write_text(yaml.safe_dump(_registry_document(rows)), encoding="utf-8")
    with pytest.raises(VocabularyUnavailable):
        Vocabulary.load(path)


def test_a_registry_row_without_a_lever_is_refused(tmp_path):
    rows = _registry_rows()
    del rows[0]["source"]
    path = tmp_path / "verbs.yaml"
    path.write_text(yaml.safe_dump(_registry_document(rows)), encoding="utf-8")
    with pytest.raises(VocabularyUnavailable):
        Vocabulary.load(path)


def test_the_in_flight_guard_is_per_command_id():
    """The guard is a reservation on the id, not a lock on the verb."""
    ledger = InFlightCommands()
    first = _command(command_id="cmd_a", verb="fleet.pause")
    same = _command(command_id="cmd_a", verb="fleet.pause")
    other = _command(command_id="cmd_b", verb="fleet.pause")
    assert ledger.begin(first) is None
    assert ledger.begin(same) is not None
    assert ledger.begin(other) is None
    ledger.finish(first, _effect_for(first))
    ledger.end(first)
    assert ledger.begin(same) is None
    assert ledger.begin(other) is not None


# ---------------------------------------------------------------------------
# scratch-tree helpers: a real lever script + a minimal registry naming it
# ---------------------------------------------------------------------------
def _scratch_repo(tmp_path: Path, *, body: str, source: str = "fleet/control.py") -> Path:
    """A checkout carrying one lever script at the registry's ``source`` path."""
    script = tmp_path / source
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(f"#!/usr/bin/env python3\n{body}\n", encoding="utf-8")
    return tmp_path


def _registry_rows() -> list[dict]:
    return [
        {
            "id": "fleet.status",
            "source": "fleet/control.py",
            "local": "status",
            "effect_class": "read",
            "capability": "fleet:read",
            "audit": None,
            "idempotent": True,
            "exposed": True,
        },
        {
            "id": "fleet.pause",
            "source": "fleet/control.py",
            "local": "pause",
            "effect_class": "hold",
            "capability": "fleet:operate",
            "audit": "fleet.pause",
            "idempotent": True,
            "exposed": True,
        },
        {
            "id": "fleet.live",
            "source": "fleet/control.py",
            "local": "live",
            "effect_class": "read",
            "capability": "fleet:read",
            "audit": None,
            "idempotent": True,
            "exposed": False,
            "why_not_exposed": "attaches a tty (withheld, with a reason)",
        },
    ]


def _registry_document(rows: list[dict]) -> dict:
    return {
        "schema": control_api.REGISTRY_SCHEMA,
        "effect_classes": {
            "read": "changes nothing; safe to repeat",
            "hold": "suspends, resumes or (re)starts a unit",
            "stop": "ends a running unit",
            "irreversible": "persists externally; no verb undoes it",
        },
        "refusals": {
            401: "no authenticated caller",
            403: "the caller lacks the required capability",
            405: "wrong HTTP method for this verb",
            409: "a duplicate command is already in flight",
            422: "verb is not in this closed vocabulary",
            503: "the local lever is unreachable",
        },
        "verbs": rows,
    }


def _scratch_registry(tmp_path: Path) -> Path:
    path = tmp_path / "registry" / "verbs.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(_registry_document(_registry_rows())), encoding="utf-8"
    )
    return path


def _command(*, command_id: str, verb: str):
    family, _, action = verb.partition(".")
    return control_api.Command(
        id=command_id,
        verb=verb,
        effect_class="hold",
        capability="fleet:operate",
        audit_action=verb,
        actor="user:operator@platform.example.com",
        lever="fleet/control.py#pause",
        argv=(),
        idempotent=True,
        requested_at="",
        mutates=True,
    )


def _effect_for(command):
    """The effect record a successful delivery of ``command`` would produce."""
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
        exit_code=0,
        output="",
        requested_at=command.requested_at,
    )
