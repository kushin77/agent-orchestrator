"""The exactly-once control record path (issue #555, RC-4 of EPIC #551).

What this suite pins, in the issue's own words:

* a replayed command produces **one** ledger record and the caller is handed the
  **original receipt** — and the lever is never reached a second time;
* an unreachable lever returns ``503`` and writes **no** record, and neither does
  a lever that declined (a command with no effect leaves no effect record);
* a **stolen** or **reordered** command id is refused — and the thief does not
  get the receipt;
* the record lands on the rails that already exist: one hash-chained
  ``telemetry/ledger`` record (which ``verify_ledger`` reads back) and one
  ``.fleet/slog.jsonl`` line in the fleet's own writer's shape;
* the one-line wiring is real: a promoted surface builds the audited ledger, not
  RC-3's in-flight guard.

Everything here is exercised through the real route (``POST
/api/control/<family>/<action>``) against a scratch rail directory, so no test
reads or writes the live fleet: the rails are pointed at ``tmp_path`` and the
lever records instead of spawning.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from conftest import AUTH_GATE, ApiClient, login_as  # noqa: E402

from portal.server import control_api  # noqa: E402
from portal.server.app import build_app  # noqa: E402
from portal.server.sso import ConsoleSso  # noqa: E402
from portal.server.control_api import (  # noqa: E402
    LeverResult,
    RemoteControl,
    Vocabulary,
    install,
)
from portal.server.control_audit import (  # noqa: E402
    DEFAULT_TENANT,
    RECEIPT_MARKER,
    AuditUnavailable,
    CommandLike,
    ControlAudit,
    command_binding,
    mint_command_id,
    record_shape,
)
from portal.server.state import seed_state  # noqa: E402
from telemetry.ledger import open_ledger, verify_ledger  # noqa: E402

CONTROL_URL = "/api/control"
REGISTRY = REPO_ROOT / "control-plane" / "control" / "verbs.yaml"
ROOT_ADMIN = "root@platform.example.com"
DEPUTY_ADMIN = "deputy@platform.example.com"


# ---------------------------------------------------------------------------
# doubles and helpers
# ---------------------------------------------------------------------------
class RecordingLever:
    """A lever double: records the row it was handed, never spawns anything."""

    def __init__(self, *, exit_code: int = 0, output: str = "", unreachable: str = "") -> None:
        self.exit_code = exit_code
        self.output = output
        self.unreachable = unreachable
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def run(self, row, args):
        self.calls.append((row.lever, tuple(args)))
        if self.unreachable:
            raise control_api.LeverUnreachable(self.unreachable)
        return LeverResult(
            argv=(row.source, row.local, *args),
            exit_code=self.exit_code,
            stdout=self.output,
            stderr="",
        )


def _app_with_two_roots():
    """An app whose root-admin allowlist binds two privileged principals.

    Two, because "a stolen command id" needs a *different* principal that could
    legitimately reach the control path: a refusal from a caller that never had
    the capability would prove nothing about the id.

    ``conftest.console_sso`` already passes ``root_admin_emails`` (the suite's
    one-root allowlist), so it cannot be asked for a different one; this builds
    the same verifier over the same JWKS mirror with the two-root allowlist the
    stolen-id cases need.
    """
    state = seed_state(repo_root=REPO_ROOT)
    sso = ConsoleSso(
        repo_root=REPO_ROOT,
        jwks=AUTH_GATE.jwks,
        root_admin_emails=(ROOT_ADMIN, DEPUTY_ADMIN),
    )
    return build_app(sso=sso, state=state)


@pytest.fixture
def rails(tmp_path, monkeypatch):
    """Point the surface's default rails at a scratch tree (never the repo's)."""
    ledger_dir = tmp_path / "ledger"
    slog_path = tmp_path / "fleet" / "slog.jsonl"
    monkeypatch.setenv("AO_LEDGER_DIR", str(ledger_dir))
    monkeypatch.setenv("AO_CONTROL_SLOG", str(slog_path))
    return {"ledger_dir": ledger_dir, "slog_path": slog_path}


def _control_url(verb_id: str) -> str:
    family, _, action = verb_id.partition(".")
    return f"{CONTROL_URL}/{family}/{action}"


def _registry_row(verb_id: str):
    """The registry's own row for a verb — never a restated expectation."""
    return Vocabulary.load(REGISTRY).verbs[verb_id]


def _installed(app, rails, **kwargs):
    """Install a promoted surface on the scratch rails and return it."""
    lever = kwargs.pop("lever", None) or RecordingLever()
    surface = RemoteControl(
        app=app, enabled=True, lever=lever, **{k: v for k, v in kwargs.items()}
    )
    install(app, surface)
    assert isinstance(surface.commands, ControlAudit), "the surface builds the audited ledger"
    # The rails belong to the ledger collaborator, not the transport: assert both
    # are the scratch ones, so no case here can reach the repository's real rail.
    assert str(surface.commands.ledger_dir) == str(rails["ledger_dir"]), "the ledger rail is the scratch one"
    assert str(surface.commands.slog_path) == str(rails["slog_path"]), "the stream rail is the scratch one"
    return surface, lever


def _ledger_records(rails) -> list[dict]:
    return open_ledger(str(rails["ledger_dir"])).records(DEFAULT_TENANT)


def _stream_records(rails) -> list[dict]:
    path: Path = rails["slog_path"]
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _receipt_from_message(message: str) -> dict:
    """The receipt a replay refusal hands back, parsed the documented way."""
    assert RECEIPT_MARKER in message, f"no receipt handed back: {message!r}"
    return json.loads(message.split(RECEIPT_MARKER, 1)[1])


# ---------------------------------------------------------------------------
# the wiring: the seam builds the audited ledger
# ---------------------------------------------------------------------------
def test_the_promoted_surface_builds_the_audited_ledger(rails):
    """The one-line seam is what lands RC-4, so the built collaborator is pinned."""
    app = _app_with_two_roots()
    surface, _lever = _installed(app, rails)
    assert isinstance(surface.commands, ControlAudit)
    assert surface.commands.rails()["ledgerDir"] == str(rails["ledger_dir"])
    assert surface.commands.rails()["streamPath"] == str(rails["slog_path"])
    assert surface.commands.rails()["ledgerTenant"] == DEFAULT_TENANT


# ---------------------------------------------------------------------------
# acceptance 1 — a replay: one record, and the original receipt
# ---------------------------------------------------------------------------
def test_a_replay_writes_one_record_and_hands_back_the_original_receipt(rails):
    app = _app_with_two_roots()
    _surface, lever = _installed(app, rails)
    api = login_as(app, ROOT_ADMIN, "acme")

    first_status, first = api.post(
        _control_url("fleet.pause"), {"commandId": "cmd_replay", "args": ["--note", "once"]}
    )
    assert first_status == 200
    second_status, second = api.post(
        _control_url("fleet.pause"), {"commandId": "cmd_replay", "args": ["--note", "once"]}
    )

    assert second_status == 409
    assert second["error"]["code"] == "duplicate_command"
    assert len(lever.calls) == 1, "a replay must not reach the lever"

    records = _ledger_records(rails)
    assert len(records) == 1, "one effect, one record"
    assert len(_stream_records(rails)) == 1

    handed_back = _receipt_from_message(second["error"]["message"])
    assert handed_back == first["data"], "the original receipt, verbatim"
    assert handed_back["commandId"] == "cmd_replay"
    assert handed_back["output"] == first["data"]["output"]


def test_a_replay_after_a_lever_failure_still_writes_nothing_new(rails):
    """A command that never applied has no receipt to hand back, and no record."""
    app = _app_with_two_roots()
    _surface, lever = _installed(app, rails, lever=RecordingLever(exit_code=1))
    api = login_as(app, ROOT_ADMIN, "acme")
    status, payload = api.post(_control_url("fleet.pause"), {"commandId": "cmd_declined"})
    assert status == 409
    assert payload["error"]["code"] == "lever_refused"
    assert _ledger_records(rails) == []
    assert _stream_records(rails) == []
    assert len(lever.calls) == 1, "a refusal is never retried in another form"


def test_concurrent_requests_with_one_command_id_produce_one_effect(rails):
    """The store is atomic: a race on one id is one effect and one refusal."""
    app = _app_with_two_roots()
    surface, lever = _installed(app, rails)
    surface_arg = surface
    results: list[tuple[int, dict]] = []
    barrier = threading.Barrier(2)

    def post():
        client = ApiClient(app)
        client.authenticate(ROOT_ADMIN, "acme")
        barrier.wait()
        results.append(client.post(_control_url("fleet.pause"), {"commandId": "cmd_race"}))

    threads = [threading.Thread(target=post) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    statuses = sorted(status for status, _payload in results)
    assert statuses == [200, 409], statuses
    assert len(lever.calls) == 1
    assert len(_ledger_records(rails)) == 1
    assert len(surface_arg.commands.applied_ids()) == 1


# ---------------------------------------------------------------------------
# acceptance 2 — an unreachable plane: 503, and no record
# ---------------------------------------------------------------------------
def test_an_unreachable_lever_returns_503_and_writes_no_record(rails):
    app = _app_with_two_roots()
    surface, _lever = _installed(
        app, rails, lever=RecordingLever(unreachable="the lever is absent")
    )
    api = login_as(app, ROOT_ADMIN, "acme")
    status, payload = api.post(_control_url("fleet.pause"), {"commandId": "cmd_gone"})
    assert status == 503
    assert payload["error"]["code"] == "lever_unreachable"
    assert _ledger_records(rails) == [], "a command with no effect writes no record"
    assert not rails["slog_path"].exists(), "and nothing on the stream either"
    assert surface.commands.receipt_of("cmd_gone") is None


def test_a_rail_still_serves_after_an_unreachable_command(rails):
    """The refusal path releases its reservation, so the id is usable again."""
    app = _app_with_two_roots()
    surface, lever = _installed(app, rails, lever=RecordingLever(exit_code=2))
    api = login_as(app, ROOT_ADMIN, "acme")
    assert api.post(_control_url("fleet.pause"), {"commandId": "cmd_gone"})[0] == 503
    lever.exit_code = 0
    status, payload = api.post(_control_url("fleet.pause"), {"commandId": "cmd_gone"})
    assert status == 200, "an unreachable lever spent nothing"
    assert payload["data"]["exitCode"] == 0
    assert len(_ledger_records(rails)) == 1


# ---------------------------------------------------------------------------
# acceptance 3 — a stolen or reordered command id is refused
# ---------------------------------------------------------------------------
def test_a_stolen_command_id_is_refused_without_the_receipt(rails):
    app = _app_with_two_roots()
    _surface, lever = _installed(app, rails)
    owner = login_as(app, ROOT_ADMIN, "acme")
    status, _payload = owner.post(_control_url("fleet.pause"), {"commandId": "cmd_mine"})
    assert status == 200

    thief = login_as(app, DEPUTY_ADMIN, "acme")
    status, payload = thief.post(
        _control_url("fleet.pause"), {"commandId": "cmd_mine", "args": []}
    )
    assert status == 409
    assert payload["error"]["code"] == "duplicate_command"
    assert "stolen" in payload["error"]["message"]
    assert RECEIPT_MARKER not in payload["error"]["message"], "a thief gets no receipt"
    assert len(lever.calls) == 1
    assert len(_ledger_records(rails)) == 1


@pytest.mark.parametrize(
    "second_call",
    [
        ("fleet.resume", []),
        ("fleet.pause", ["--note", "different"]),
    ],
    ids=["another-verb", "another-argument"],
)
def test_a_reordered_command_id_is_refused(rails, second_call):
    verb, args = second_call
    app = _app_with_two_roots()
    _surface, lever = _installed(app, rails)
    api = login_as(app, ROOT_ADMIN, "acme")
    assert api.post(_control_url("fleet.pause"), {"commandId": "cmd_order"})[0] == 200

    status, payload = api.post(
        _control_url(verb), {"commandId": "cmd_order", "args": args}
    )
    assert status == 409
    assert "reordered" in payload["error"]["message"]
    assert RECEIPT_MARKER not in payload["error"]["message"]
    assert len(lever.calls) == 1
    assert len(_ledger_records(rails)) == 1


def test_a_fresh_command_id_is_the_way_to_repeat_the_verb(rails):
    """Idempotency is per command id: a new id is a new order, not a replay."""
    app = _app_with_two_roots()
    _surface, lever = _installed(app, rails)
    api = login_as(app, ROOT_ADMIN, "acme")
    assert api.post(_control_url("fleet.pause"), {"commandId": "cmd_a"})[0] == 200
    assert api.post(_control_url("fleet.pause"), {"commandId": "cmd_b"})[0] == 200
    assert len(lever.calls) == 2
    assert len(_ledger_records(rails)) == 2


# ---------------------------------------------------------------------------
# the record itself: the existing rails, in their own shape
# ---------------------------------------------------------------------------
def test_the_ledger_record_names_the_verb_the_actor_and_the_registrys_action(rails):
    app = _app_with_two_roots()
    _surface, _lever = _installed(app, rails)
    api = login_as(app, ROOT_ADMIN, "acme")
    row = _registry_row("fleet.pause")
    assert api.post(_control_url("fleet.pause"), {"commandId": "cmd_record"})[0] == 200

    records = _ledger_records(rails)
    assert len(records) == 1
    record = records[0]
    assert record["tenantId"] == DEFAULT_TENANT
    assert record["action"] == row.audit_action, "the registry's own action, a lookup"
    assert record["actor"] == f"user:{ROOT_ADMIN}"
    assert record["resource"] == "fleet/control/fleet.pause"
    assert record["evidence"] == "control-command:cmd_record"
    assert record["seq"] == 1
    assert verify_ledger(open_ledger(str(rails["ledger_dir"])), DEFAULT_TENANT).status == "OK"


def test_the_ledger_rail_is_the_existing_one_and_keeps_chaining(rails):
    """Two commands are one chain — the rail's own integrity, not a second file."""
    app = _app_with_two_roots()
    _surface, _lever = _installed(app, rails)
    api = login_as(app, ROOT_ADMIN, "acme")
    assert api.post(_control_url("fleet.pause"), {"commandId": "cmd_one"})[0] == 200
    assert api.post(_control_url("fleet.resume"), {"commandId": "cmd_two"})[0] == 200
    records = _ledger_records(rails)
    assert [record["seq"] for record in records] == [1, 2]
    assert records[1]["prevHash"] == records[0]["hash"]
    verdict = verify_ledger(open_ledger(str(rails["ledger_dir"])), DEFAULT_TENANT)
    assert verdict.status == "OK"
    assert list((rails["ledger_dir"]).glob("*.jsonl")), "one chain per tenant, on the rail"


def test_a_read_verb_takes_no_record(rails):
    """RC-2's rule, honoured by the record path: a read verb declares no action."""
    app = _app_with_two_roots()
    surface, _lever = _installed(app, rails)
    api = login_as(app, ROOT_ADMIN, "acme")
    row = _registry_row("fleet.status")
    assert row.effect_class == "read" and row.audit_action is None
    status, _payload = api.post(_control_url("fleet.status"))
    assert status == 200
    assert _ledger_records(rails) == []
    assert not rails["slog_path"].exists()
    assert surface.commands.applied_ids() == ()


def test_the_stream_line_is_the_fleets_own_writers_shape(rails):
    """The stream shape is pinned against ``fleet.channel._slog``, not trusted.

    The fleet's writer is imported in a subprocess: ``fleet.channel`` imports its
    lane siblings by bare module name, which the console process cannot resolve,
    and that is exactly why this module writes the stream itself rather than
    importing it. The two shapes are therefore checked against each other.
    """
    app = _app_with_two_roots()
    _surface, _lever = _installed(app, rails)
    api = login_as(app, ROOT_ADMIN, "acme")
    assert api.post(_control_url("fleet.pause"), {"commandId": "cmd_stream"})[0] == 200
    mine = _stream_records(rails)
    assert len(mine) == 1
    assert list(mine[0]) == list(record_shape())
    assert mine[0]["correlation_id"] == "cmd_stream"
    assert mine[0]["from"] == "control" and mine[0]["to"] == "fleet"
    assert "ledger seq 1" in mine[0]["body"]
    assert len(mine[0]["body"]) <= 200

    probe = rails["slog_path"].parent / "probe.jsonl"
    script = (
        "import json, pathlib, sys\n"
        f"root = pathlib.Path({str(REPO_ROOT)!r})\n"
        "sys.path.insert(0, str(root / 'fleet'))\n"
        "sys.path.insert(0, str(root))\n"
        "import channel\n"
        f"target = pathlib.Path({str(probe)!r})\n"
        "channel.SLOG = target\n"
        "channel._slog({'ts': 'T', 'id': 'i', 'from': 'f', 'to': 't', 'type': 'x',\n"
        "               'correlation_id': 'c', 'issue': None, 'severity': 'info',\n"
        "               'body': 'b'})\n"
        "print(json.dumps(list(json.loads(target.read_text().strip()).keys())))\n"
    )
    done = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    theirs = json.loads(done.stdout.strip())
    assert theirs == list(record_shape()), "the stream shape drifted from the fleet's own writer"


# ---------------------------------------------------------------------------
# the store's own contract: fail closed, and the recovery path
# ---------------------------------------------------------------------------
class _Command:
    """A minimal stand-in for ``control_api.Command`` (the store reads fields)."""

    def __init__(
        self,
        command_id: str,
        verb: str = "fleet.pause",
        actor: str = "user:a@b",
        argv=(),
        audit_action: object = "derive",
    ):
        self.id = command_id
        self.verb = verb
        self.effect_class = "hold"
        self.capability = "fleet:operate"
        self.audit_action = (
            _registry_row(verb).audit_action if audit_action == "derive" else audit_action
        )
        self.actor = actor
        self.lever = _registry_row(verb).lever
        self.argv = tuple(argv)
        self.idempotent = True
        self.requested_at = ""
        self.mutates = True


def test_a_mutating_command_with_no_declared_audit_action_is_refused(rails):
    """A record with no action is not a record: fail closed rather than write it."""
    store = ControlAudit(
        repo_root=REPO_ROOT, ledger_dir=rails["ledger_dir"], slog_path=rails["slog_path"]
    )
    command = _Command("cmd_unauditable", audit_action=None)
    assert store.begin(command) is None
    with pytest.raises(AuditUnavailable) as refusal:
        store.finish(command, _effect(command))
    assert "declares no audit action" in str(refusal.value)
    assert _ledger_records(rails) == []
    assert store.applied_ids() == (), "nothing was recorded, so nothing was spent"


def _effect(command: _Command):
    class _Effect:
        command_id = command.id
        verb = command.verb
        effect_class = command.effect_class
        capability = command.capability
        audit_action = command.audit_action
        actor = command.actor
        idempotent = True
        lever = command.lever
        args = command.argv
        exit_code = 0
        output = "paused"
        requested_at = "2026-09-14T00:00:00Z"

        def as_json(self):
            return {
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

    return _Effect()


def test_an_unwritable_rail_fails_closed_and_spends_the_command_id(rails, tmp_path):
    """The effect applied but could not be recorded — reported, never a success.

    The id is spent either way: that is the recovery path for a mutation that
    succeeds before its receipt fails. A retry must not become a second effect.
    """
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("a file where the rail's directory should be", encoding="utf-8")
    store = ControlAudit(repo_root=REPO_ROOT, ledger_dir=blocked, slog_path=rails["slog_path"])
    command = _Command("cmd_unrecorded")
    assert store.begin(command) is None
    with pytest.raises(AuditUnavailable) as failure:
        store.finish(command, _effect(command))
    assert "the command id is spent" in str(failure.value)
    assert store.applied_ids() == ("cmd_unrecorded",)
    assert store.receipt_of("cmd_unrecorded") is not None, "the receipt the caller saw is kept"
    assert store.begin(command) is not None, "a retry is refused, never re-applied"


def test_recover_rebuilds_the_spent_ids_from_the_rail_and_reports_the_receipt_absent(rails):
    """A restarted console refuses a command the rail already remembers."""
    first = ControlAudit(
        repo_root=REPO_ROOT, ledger_dir=rails["ledger_dir"], slog_path=rails["slog_path"]
    )
    command = _Command("cmd_recovered")
    assert first.begin(command) is None
    first.finish(command, _effect(command))
    assert first.begin(command) is not None, "a replay is refused while the process lives"

    restarted = ControlAudit(
        repo_root=REPO_ROOT, ledger_dir=rails["ledger_dir"], slog_path=rails["slog_path"]
    )
    assert restarted.applied_ids() == (), "a fresh process knows nothing until it reads the rail"
    report = restarted.recover()
    assert report["status"] == "OK", report
    assert report["recovered"] == 1
    assert restarted.applied_ids() == ("cmd_recovered",)

    refusal = restarted.begin(command)
    assert refusal is not None
    assert "already applied" in refusal
    assert RECEIPT_MARKER in refusal and refusal.endswith(RECEIPT_MARKER), (
        "a recovered record's receipt is unavailable, and that is what is handed back"
    )
    record = restarted.record_of("cmd_recovered")
    assert record is not None and record.recovered is True
    assert record.receipt is None
    assert not restarted.receipt_of("cmd_recovered")


def test_recover_reports_cannot_assess_on_an_unreadable_rail(rails, tmp_path):
    """Tri-state, never a silent pass: an unreadable rail is CANNOT-ASSESS."""
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("not a directory", encoding="utf-8")
    store = ControlAudit(repo_root=REPO_ROOT, ledger_dir=blocked, slog_path=rails["slog_path"])
    report = store.recover()
    assert report["status"] == "CANNOT-ASSESS"
    assert report["recovered"] == 0
    assert "unreadable" in report["detail"]


def test_the_stream_is_written_once_per_applied_command(rails):
    """One record per rail, both naming the same command."""
    app = _app_with_two_roots()
    _surface, _lever = _installed(app, rails)
    api = login_as(app, ROOT_ADMIN, "acme")
    assert api.post(_control_url("fleet.pause"), {"commandId": "cmd_x1"})[0] == 200
    assert api.post(_control_url("fleet.pause"), {"commandId": "cmd_x1"})[0] == 409
    assert api.post(_control_url("fleet.resume"), {"commandId": "cmd_x2"})[0] == 200
    records, stream = _ledger_records(rails), _stream_records(rails)
    assert len(records) == 2 and len(stream) == 2
    assert [record["evidence"] for record in records] == [
        "control-command:cmd_x1",
        "control-command:cmd_x2",
    ]
    assert [line["correlation_id"] for line in stream] == ["cmd_x1", "cmd_x2"]
    assert len({line["id"] for line in stream}) == 2, (
        "each stream line is addressed by its own minted id"
    )


def test_minted_ids_are_distinct_and_durable_shaped():
    """The mint is a durable key: full width, prefixed, never repeated."""
    ids = {mint_command_id() for _ in range(200)}
    assert len(ids) == 200
    assert all(value.startswith("cmd_") and len(value) == 36 for value in ids)


def test_a_binding_is_the_order_and_never_the_receipt():
    """The binding covers the verb, the arguments and the principal."""
    one = _Command("cmd_same", argv=("--note", "a"))
    two = _Command("cmd_same", argv=("--note", "b"))
    assert command_binding(one) != command_binding(two)
    assert command_binding(one) == command_binding(_Command("cmd_other", argv=("--note", "a")))
