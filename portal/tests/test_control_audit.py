"""Exactly-once control with an audit record and a refusal path (issue #555, RC-4).

``portal/server/control_audit.py`` is the record path RC-3's choke point
(``RemoteControl.apply_command``) already calls. What this suite pins, in the
issue's own words:

* a **replayed** command produces exactly **one** ledger record, runs the lever
  **once**, and returns the **original receipt** — proved end to end over the real
  route, not by inspecting the store;
* an **unreachable** lever returns ``503`` and writes **no** record on either
  rail;
* a **reordered or stolen** command id is **refused**, and the id stays spent;
* the record lands on the rails that already exist — the hash-chained
  ``telemetry/ledger`` store and the ``.fleet/slog.jsonl`` writer — and a single
  applied command mints **no** new ledger (a second rail would be visible here);
* the fail-closed completion of "one effect, one record": an effect whose record
  cannot be written is reported as ``503 audit_unavailable``, never as a success,
  and its id is still spent so a retry cannot double the effect.

Nothing here reads or writes the live fleet: the ledger is opened in a per-test
tmp tree, the slog stream is redirected, and the lever is a recording double.
No network, no tmux, no fleet state.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from conftest import AUTH_GATE, ApiClient  # noqa: E402

from portal.server import control_audit as ca  # noqa: E402
from portal.server.app import build_app  # noqa: E402
from portal.server.control_api import (  # noqa: E402
    Command,
    EffectRecord,
    InFlightCommands,
    LeverResult,
    LeverUnreachable,
    RemoteControl,
    install,
)
from portal.server.sso import ConsoleSso  # noqa: E402

CONTROL_URL = "/api/control"
#: A declared, exposed, mutating verb (effect class ``hold``) — the registry's own.
MUTATING_VERB = "fleet.pause"
#: A declared, exposed ``read`` verb: same delivery, no reservation, no record.
READ_VERB = "fleet.status"
#: The allowlisted operators the console under test is configured with. The
#: control family authorises against the fleet's own org, which a root admin is
#: in scope for — RC-3's own suite drives its success paths the same way. Two
#: principals are needed so the *stolen* id case can be provoked **through the
#: route**: a caller the capability gate refuses never reaches the record path.
OPERATOR_EMAIL = "operator@platform.example.com"
INTRUDER_EMAIL = "second-operator@platform.example.com"
OPERATOR_TENANT = "acme"
INTRUDER_TENANT = "globex"


def _url(verb_id: str) -> str:
    family, _, action = verb_id.partition(".")
    return f"{CONTROL_URL}/{family}/{action}"


class RecordingLever:
    """A lever double: records the row it was handed, never spawns anything."""

    def __init__(self, *, exit_code: int = 0, output: str = "applied", unreachable: str = "") -> None:
        self.exit_code = exit_code
        self.output = output
        self.unreachable = unreachable
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def run(self, row, args):
        self.calls.append((row.lever, tuple(args)))
        if self.unreachable:
            raise LeverUnreachable(self.unreachable)
        return LeverResult(
            argv=(row.source, row.local, *args),
            exit_code=self.exit_code,
            stdout=self.output,
            stderr="",
        )


class BrokenStore:
    """A rail that cannot be written — the audit-outage case, provoked."""

    def records(self, tenant_id):
        return []

    def append(self, tenant_id, **kwargs):
        raise OSError("the ledger rail is not writable")


@pytest.fixture
def rig(tmp_path, monkeypatch):
    """A promoted control family whose record path writes to a tmp tree.

    Returns a factory so one test can build a second surface over the same rails
    (the restart case) or with a different lever (the unreachable case).
    """

    chan = ca.load_fleet_channel(REPO_ROOT)
    monkeypatch.setattr(chan, "SLOG", tmp_path / "slog.jsonl")

    sso = ConsoleSso(jwks=AUTH_GATE.jwks, root_admin_emails=(OPERATOR_EMAIL, INTRUDER_EMAIL))
    app = build_app(sso=sso)

    ledger_dir = tmp_path / "ledger"

    def make(*, lever=None, store=None, audit=None):
        lever = lever if lever is not None else RecordingLever()
        if audit is None:
            audit = ca.ControlAudit(
                repo_root=REPO_ROOT,
                ledger_dir=None if store is not None else ledger_dir,
                store=store,
                channel=chan,
            )
        surface = RemoteControl(app=app, enabled=True, lever=lever, commands=audit)
        install(app, surface)
        client = ApiClient(app)
        client.authenticate(OPERATOR_EMAIL, OPERATOR_TENANT)
        intruder = ApiClient(app)
        intruder.authenticate(INTRUDER_EMAIL, INTRUDER_TENANT)
        return SimpleNamespace(
            app=app,
            surface=surface,
            audit=audit,
            lever=lever,
            client=client,
            intruder=intruder,
            ledger_dir=ledger_dir,
            chan=chan,
        )

    return make


# ---------------------------------------------------------------------------
# acceptance 1 — a replay: one record, one effect, the original receipt
# ---------------------------------------------------------------------------
def test_a_replayed_command_returns_the_original_receipt_and_one_ledger_record(rig):
    """The issue's first acceptance criterion, driven end to end over the route."""
    r = rig()
    body = {"commandId": "cmd_replay", "args": ["--why", "drill"]}

    first_status, first_payload = r.client.post(_url(MUTATING_VERB), body)
    assert first_status == 200
    receipt = first_payload["data"]
    assert receipt["commandId"] == "cmd_replay"

    second_status, second_payload = r.client.post(_url(MUTATING_VERB), body)
    assert second_status == 409
    assert second_payload["error"]["code"] == "duplicate_command"
    message = second_payload["error"]["message"]
    assert ca.REPLAY_MARKER in message, message
    assert ca.RECEIPT_SEPARATOR in message, message

    returned = json.loads(message.split(ca.RECEIPT_SEPARATOR, 1)[1])
    assert returned == receipt, "a replay did not return the ORIGINAL receipt"

    # Exactly one effect, exactly one record — on each existing rail.
    assert len(r.lever.calls) == 1, "a replay performed a second effect"
    records = r.audit.records_for("cmd_replay")
    assert len(records) == 1, f"a replay produced {len(records)} ledger records"
    assert len(r.audit.slog_records_for("cmd_replay")) == 1


def test_a_replay_after_a_restart_is_refused_from_the_rail(rig):
    """Durability without a second store: the rail itself is the store.

    A fresh record path over the same ledger re-derives the spent id, so a
    retry after a process restart cannot become a second effect either.
    """
    r = rig()
    r.client.post(_url(MUTATING_VERB), {"commandId": "cmd_restart", "args": []})

    reopened = ca.ControlAudit(repo_root=REPO_ROOT, ledger_dir=r.ledger_dir, channel=r.chan)
    prior = reopened.applied("cmd_restart")
    assert prior is not None, "the rail did not rehydrate the spent command id"
    assert prior.ledger_seq == 1

    command = Command(
        id="cmd_restart",
        verb=MUTATING_VERB,
        effect_class="hold",
        capability="fleet:operate",
        audit_action=MUTATING_VERB,
        actor=f"user:{OPERATOR_EMAIL}",
        lever="fleet/control.py#pause",
        argv=(),
        idempotent=True,
        requested_at="",
        mutates=True,
    )
    refusal = reopened.begin(command)
    assert refusal is not None and ca.REPLAY_MARKER in refusal
    assert "ledger seq 1" in refusal
    reopened.end(command)
    # Nothing new was written by the refusal.
    assert len(reopened.records_for("cmd_restart")) == 1


@pytest.mark.parametrize("extra", [True, False])
def test_a_replay_is_refused_with_or_without_the_caller_s_arguments(rig, extra):
    """Identity includes the argv when it is known, and the refusal still holds."""
    r = rig()
    args = ["--why", "drill"] if extra else []
    r.client.post(_url(MUTATING_VERB), {"commandId": "cmd_args", "args": args})
    status, payload = r.client.post(_url(MUTATING_VERB), {"commandId": "cmd_args", "args": args})
    assert status == 409
    assert ca.REPLAY_MARKER in payload["error"]["message"]
    assert len(r.lever.calls) == 1


# ---------------------------------------------------------------------------
# acceptance 2 — an unreachable lever: 503, and no record on either rail
# ---------------------------------------------------------------------------
def test_an_unreachable_lever_returns_503_and_writes_no_record(rig):
    """The issue's second acceptance criterion: a refusal, never a silent no-op."""
    r = rig(lever=RecordingLever(unreachable="the lever is absent"))
    status, payload = r.client.post(_url(MUTATING_VERB), {"commandId": "cmd_dead", "args": []})
    assert status == 503
    assert payload["error"]["code"] == "lever_unreachable"
    assert r.audit.records_for("cmd_dead") == []
    assert r.audit.ledger_records() == [], "a command with no effect wrote a ledger record"
    assert r.audit.slog_records_for("cmd_dead") == []
    assert r.audit.applied("cmd_dead") is None
    # The id was never spent, so a later, reachable retry is a fresh command.
    assert r.audit.records_for("cmd_dead") == []


def test_a_lever_refusal_writes_no_record_either(rig):
    """A declined lever (exit 1) had no effect, so it earns no record."""
    r = rig(lever=RecordingLever(exit_code=1, output="cannot pause"))
    status, payload = r.client.post(_url(MUTATING_VERB), {"commandId": "cmd_denied", "args": []})
    assert status == 409
    assert payload["error"]["code"] == "lever_refused"
    assert r.audit.ledger_records() == []


# ---------------------------------------------------------------------------
# acceptance 3 — a reordered / stolen command id is refused
# ---------------------------------------------------------------------------
def test_a_reordered_command_id_is_refused(rig):
    """The same id naming a different verb is refused, and the id stays spent."""
    r = rig()
    r.client.post(_url(MUTATING_VERB), {"commandId": "cmd_order", "args": []})
    status, payload = r.client.post(_url("fleet.resume"), {"commandId": "cmd_order", "args": []})
    assert status == 409
    message = payload["error"]["message"]
    assert ca.CONFLICT_MARKER in message
    assert "reordered" in message
    assert len(r.lever.calls) == 1, "a refused reordering reached the lever"
    assert len(r.audit.records_for("cmd_order")) == 1


def test_a_stolen_command_id_is_refused(rig):
    """Another principal reusing an id is refused, not answered with the receipt."""
    r = rig()
    r.client.post(_url(MUTATING_VERB), {"commandId": "cmd_steal", "args": []})
    status, payload = r.intruder.post(_url(MUTATING_VERB), {"commandId": "cmd_steal", "args": []})
    assert status == 409
    message = payload["error"]["message"]
    assert ca.CONFLICT_MARKER in message
    assert "stolen" in message
    assert ca.RECEIPT_SEPARATOR not in message, "a stolen id was answered with the receipt"
    assert len(r.lever.calls) == 1


# ---------------------------------------------------------------------------
# the rails: existing store, existing slog writer, no new ledger
# ---------------------------------------------------------------------------
def test_the_record_lands_on_the_existing_rails_and_mints_no_new_ledger(rig, tmp_path):
    r = rig()
    before = {path for path in tmp_path.rglob("*") if path.is_file()}
    status, _payload = r.client.post(
        _url(MUTATING_VERB), {"commandId": "cmd_rail", "args": ["--why", "drill"]}
    )
    assert status == 200

    ledger_file = r.ledger_dir / f"{ca.TENANT_ID}.jsonl"
    assert ledger_file.is_file(), "the record did not land on the telemetry/ledger chain"

    records = r.audit.ledger_records()
    assert len(records) == 1
    record = records[0]
    assert record["tenantId"] == ca.TENANT_ID
    assert record["evidence"] == f"{ca.EVIDENCE_PREFIX}cmd_rail"
    assert record["action"] == MUTATING_VERB  # the registry's own audit action
    assert record["actor"] == f"user:{OPERATOR_EMAIL}"
    assert record["resource"] == "fleet/control.py#pause"

    after = {path for path in tmp_path.rglob("*") if path.is_file()}
    assert after - before == {ledger_file, tmp_path / "slog.jsonl"}, (
        "an applied command created something other than the two existing rails"
    )

    # The rail is the ledger's own chain, and it verifies.
    ledger_pkg = ca._load_ledger()
    verdict = ledger_pkg.verify_ledger(r.audit.store, ca.TENANT_ID)
    assert verdict.status == "OK", verdict.detail


def test_the_slog_line_is_the_existing_writer_s_own_shape(rig):
    """The line is ``fleet/channel.py``'s, inside its declared vocabulary."""
    r = rig()
    r.client.post(_url(MUTATING_VERB), {"commandId": "cmd_slog", "args": []})
    lines = [entry for entry in r.audit.slog_records() if entry.get("correlation_id") == "cmd_slog"]
    assert len(lines) == 1
    line = lines[0]
    assert line["from"] == ca.SLOG_FROM == "operator"
    assert line["to"] == ca.SLOG_TO == "brain"
    assert line["type"] == ca.SLOG_TYPE == "result"
    assert line["id"] == "ctl-cmd_slog"
    assert MUTATING_VERB in line["body"]


def test_a_read_verb_is_delivered_with_no_reservation_and_no_record(rig):
    """A read mutates nothing, so it earns neither a reservation nor a record."""
    r = rig()
    status, _payload = r.client.post(_url(READ_VERB), {})
    assert status == 200
    assert len(r.lever.calls) == 1
    assert r.audit.ledger_records() == []
    assert r.audit.slog_records() == []


# ---------------------------------------------------------------------------
# the fail-closed completion: an unrecorded effect is never a success
# ---------------------------------------------------------------------------
def test_an_unwritable_rail_is_reported_as_a_refusal_and_the_id_is_spent(rig):
    """The record is not optional: if it cannot be written, this is not a success."""
    r = rig(store=BrokenStore())
    status, payload = r.client.post(_url(MUTATING_VERB), {"commandId": "cmd_unwritten", "args": []})
    assert status == 503
    assert payload["error"]["code"] == "audit_unavailable"
    assert "unrecorded" in payload["error"]["message"]
    # The id is spent anyway, so a retry cannot double an effect that did happen.
    assert r.audit.applied("cmd_unwritten") is not None
    assert len(r.lever.calls) == 1


def test_the_finish_step_is_idempotent(rig):
    """A second ``finish`` for an id already spent writes no second record."""
    r = rig()
    r.client.post(_url(MUTATING_VERB), {"commandId": "cmd_once", "args": []})
    command = Command(
        id="cmd_once",
        verb=MUTATING_VERB,
        effect_class="hold",
        capability="fleet:operate",
        audit_action=MUTATING_VERB,
        actor=f"user:{OPERATOR_EMAIL}",
        lever="fleet/control.py#pause",
        argv=(),
        idempotent=True,
        requested_at="",
        mutates=True,
    )
    before = len(r.audit.ledger_records())
    r.audit.finish(
        command,
        EffectRecord(
            command_id=command.id,
            verb=command.verb,
            effect_class=command.effect_class,
            capability=command.capability,
            audit_action=command.audit_action,
            actor=command.actor,
            idempotent=command.idempotent,
            lever=command.lever,
            args=(),
            exit_code=0,
            output="applied",
            requested_at="",
        ),
    )
    r.audit.end(command)
    assert len(r.audit.ledger_records()) == before


# ---------------------------------------------------------------------------
# the seam, and RC-3's half of it
# ---------------------------------------------------------------------------
def test_the_record_path_is_the_seam_control_api_declares():
    """Three calls, and the transport's own collaborator type accepts it."""
    audit = ca.ControlAudit(repo_root=REPO_ROOT, store=BrokenStore(), channel=object(), rehydrate=False)
    for name in ("begin", "finish", "end"):
        assert callable(getattr(audit, name)), name
    assert isinstance(audit._in_flight, InFlightCommands), (
        "RC-3's in-flight guard must be consumed, not restated"
    )


def test_a_duplicate_in_flight_is_still_refused_by_rc3_guard(rig):
    """RC-4 adds the exactly-once store on top of RC-3's guard, not instead of it."""
    r = rig()
    command = Command(
        id="cmd_inflight",
        verb=MUTATING_VERB,
        effect_class="hold",
        capability="fleet:operate",
        audit_action=MUTATING_VERB,
        actor=f"user:{OPERATOR_EMAIL}",
        lever="fleet/control.py#pause",
        argv=(),
        idempotent=True,
        requested_at="",
        mutates=True,
    )
    assert r.audit.begin(command) is None
    in_flight = r.audit.begin(command)
    assert in_flight is not None
    assert ca.REPLAY_MARKER not in in_flight
    assert ca.CONFLICT_MARKER not in in_flight
    r.audit.end(command)
    # Still fresh: nothing was applied, so it can be delivered.
    assert r.audit.begin(command) is None
    r.audit.end(command)


def test_the_record_names_the_registry_s_own_audit_action(rig):
    """The audit action is a lookup on the effect record, never an invention."""
    r = rig()
    r.client.post(_url(MUTATING_VERB), {"commandId": "cmd_action", "args": []})
    record = r.audit.records_for("cmd_action")[0]
    assert record["action"] == MUTATING_VERB
    assert record["resource"] == "fleet/control.py#pause"


def test_the_default_ledger_dir_is_the_deployment_s_rail_not_a_second_one():
    """The default is overridable, and the store is the existing package."""
    import ledger as ledger_pkg  # noqa: PLC0415  (imported after the seam's bootstrap)

    assert ca.TENANT_ID == "platform"
    assert ca.DEFAULT_LEDGER_DIR == Path(".fleet") / "audit-ledger"
    assert ca.LEDGER_DIR_ENV == "AO_AUDIT_LEDGER_DIR"
    assert ca._load_ledger() is ledger_pkg
