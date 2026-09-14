"""The CLI's own flow — one receipt per action, and refusals that name themselves.

The end-to-end cases the issue's acceptance names:

* an **unreachable plane exits non-zero with a named reason** — never a silent
  success (asserted as a verdict *and* a name, not just a number);
* **``--dry-run`` prints the request it would send and sends nothing** — asserted
  against the transport's own record of what it was asked to do, so "sends
  nothing" is measured rather than promised;
* the **local refusals** — a withheld verb, a missing caller identity, and an
  irreversible verb without its confirmation — each send nothing at all.
"""

from __future__ import annotations

import io
import json

import pytest

from _doubles import (
    ConsoleTransport,
    UnreachableTransport,
    drop,
    effect_record,
    ok_envelope,
    refusal_envelope,
    registry_document,
    withhold,
)

from aoctl import vocabulary
from aoctl.cli import main
from aoctl.refusals import EXIT_CANNOT_ASSESS, EXIT_OK, EXIT_REFUSED

PAUSE_PATH = "/api/control/fleet/pause"
OVERRIDE_PATH = "/api/control/fleet/override"
AUDIT_PATH = "/api/control/board/audit"


@pytest.fixture()
def registry() -> vocabulary.Registry:
    return vocabulary.Registry.load()


def run(argv, *, transport=None, registry=None, session="a-session-token"):
    """Run one invocation with captured streams: (exit code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    code = main(
        argv,
        transport=transport,
        registry=registry,
        session=session,
        out=out,
        err=err,
    )
    return code, out.getvalue(), err.getvalue()


# --- the receipt -----------------------------------------------------------
def test_a_delivered_command_prints_one_receipt_and_exits_zero(registry):
    record = effect_record(verb="fleet.pause", output="paused")
    transport = ConsoleTransport({("POST", PAUSE_PATH): (200, ok_envelope(record))})
    code, out, err = run(["pause"], transport=transport, registry=registry)
    assert code == EXIT_OK
    assert err == ""
    assert "ao-control: OK — fleet.pause (hold)" in out
    assert record["commandId"] in out
    assert len(transport.requests) == 1
    assert transport.requests[0]["path"] == PAUSE_PATH


def test_the_json_document_carries_the_receipt_and_the_request(registry):
    record = effect_record(verb="fleet.pause")
    transport = ConsoleTransport({("POST", PAUSE_PATH): (200, ok_envelope(record))})
    code, out, _ = run(["--json", "pause"], transport=transport, registry=registry)
    assert code == EXIT_OK
    document = json.loads(out)
    assert document["ok"] is True and document["verdict"] == "OK"
    assert document["verb"] == "pause"
    assert document["receipt"] == record
    assert document["request"]["path"] == PAUSE_PATH
    assert "refusal" not in document


def test_a_pinned_command_id_is_the_one_the_plane_is_asked_with(registry):
    record = effect_record(verb="fleet.pause")
    transport = ConsoleTransport({("POST", PAUSE_PATH): (200, ok_envelope(record))})
    code, out, _ = run(
        ["--json", "--command-id", "cmd_pinned", "pause"],
        transport=transport,
        registry=registry,
    )
    assert code == EXIT_OK
    assert transport.requests[0]["body"]["commandId"] == "cmd_pinned"
    document = json.loads(out)
    assert document["commandId"] == "cmd_pinned"
    # The receipt is the plane's own record, so its commandId is the plane's word
    # for it; the CLI's pinned id is asserted where the CLI can be authoritative.
    assert document["receipt"] == record


def test_the_levers_own_arguments_are_forwarded_verbatim(registry):
    record = effect_record(verb="board.audit")
    transport = ConsoleTransport({("POST", AUDIT_PATH): (200, ok_envelope(record))})
    code, _, _ = run(
        ["audit", "--snapshot", ".board/snapshot.json"],
        transport=transport,
        registry=registry,
    )
    assert code == EXIT_OK
    assert transport.requests[0]["body"]["args"] == ["--snapshot", ".board/snapshot.json"]


def test_a_replay_prints_the_original_receipt_the_plane_handed_back(registry):
    from aoctl.contract import receipt_marker

    original = effect_record(verb="fleet.pause", command_id="cmd_1")
    canonical = json.dumps(original, sort_keys=True, separators=(",", ":"))
    transport = ConsoleTransport(
        {
            ("POST", PAUSE_PATH): (
                409,
                refusal_envelope(
                    409,
                    "duplicate_command",
                    f"already applied; original receipt is returned verbatim, "
                    f"{receipt_marker()}{canonical}",
                ),
            )
        }
    )
    code, out, err = run(
        ["--command-id", "cmd_1", "pause"], transport=transport, registry=registry
    )
    assert code == EXIT_REFUSED
    assert "duplicate_command" in err
    assert "original receipt" in err
    assert "fleet.pause" in err and original["commandId"] in err
    assert out == ""


# --- --dry-run -------------------------------------------------------------
def test_dry_run_prints_the_request_and_sends_nothing(registry):
    transport = ConsoleTransport({})
    code, out, err = run(["--dry-run", "pause"], transport=transport, registry=registry)
    assert code == EXIT_OK
    assert err == ""
    assert "DRY-RUN" in out
    assert f"POST http://127.0.0.1:8787{PAUSE_PATH}" in out
    assert '"args": []' in out
    # The measurement, not the promise: the transport was asked for nothing.
    assert transport.requests == []


def test_dry_run_needs_no_session_and_still_renders_the_cookie_decision(registry):
    transport = ConsoleTransport({})
    code, out, _ = run(["--dry-run", "status"], transport=transport, session="", registry=registry)
    assert code == EXIT_OK
    assert "no console session configured" in out
    assert transport.requests == []


def test_dry_run_of_the_irreversible_verb_carries_the_pinned_id(registry):
    transport = ConsoleTransport({})
    code, out, _ = run(
        ["--dry-run", "--command-id", "cmd_fixed", "override"],
        transport=transport,
        registry=registry,
        session="",
    )
    assert code == EXIT_OK
    assert "DRY-RUN" in out
    assert "cmd_fixed" in out
    assert "--confirm fleet.override" in out, "a dry run says what a real call will need"
    assert transport.requests == []


def test_a_dry_run_is_not_guarded_by_the_irreversible_confirmation(registry):
    """The confirmation guards the send; inspecting the request is not a send."""
    transport = ConsoleTransport({})
    code, out, err = run(["--dry-run", "override"], transport=transport, registry=registry)
    assert code == EXIT_OK
    assert err == ""
    assert OVERRIDE_PATH in out
    assert transport.requests == []


def test_dry_run_json_is_machine_shaped_and_sends_nothing(registry):
    transport = ConsoleTransport({})
    code, out, _ = run(["--json", "--dry-run", "resume"], transport=transport, registry=registry)
    assert code == EXIT_OK
    document = json.loads(out)
    assert document["ok"] is True
    assert document["request"]["method"] == "POST"
    assert document["request"]["path"] == "/api/control/fleet/resume"
    assert document["request"]["headers"]["Cookie"].endswith("<set>")
    assert transport.requests == []


# --- an unreachable plane is never a silent success ------------------------
def test_an_unreachable_plane_exits_non_zero_with_a_named_reason(registry):
    transport = UnreachableTransport("connection refused")
    code, out, err = run(["pause"], transport=transport, registry=registry)
    assert code == EXIT_CANNOT_ASSESS != 0
    assert "plane_unreachable" in err
    assert "CANNOT-ASSESS" in err
    assert "never delivered" in err
    assert out == "", "a refusal is not a receipt"
    assert len(transport.requests) == 1, "the CLI did try; it did not pretend"


def test_an_unreachable_plane_in_json_is_still_non_zero_and_named(registry):
    transport = UnreachableTransport("no route to host")
    code, out, err = run(["--json", "pause"], transport=transport, registry=registry)
    assert code == EXIT_CANNOT_ASSESS != 0
    document = json.loads(out)
    assert document["ok"] is False
    assert document["refusal"]["code"] == "plane_unreachable"
    assert document["refusal"]["verdict"] == "CANNOT-ASSESS"
    assert "receipt" not in document
    assert err == ""


# --- the plane's refusals --------------------------------------------------
def test_a_plane_refusal_is_named_and_prints_no_receipt(registry):
    transport = ConsoleTransport(
        {("POST", PAUSE_PATH): (403, refusal_envelope(403, "permission_denied", "no fleet:operate"))}
    )
    code, out, err = run(["pause"], transport=transport, registry=registry)
    assert code == EXIT_REFUSED
    assert "permission_denied" in err
    assert "no fleet:operate" in err
    assert out == ""


def test_the_flag_gate_reads_as_cannot_assess(registry):
    transport = ConsoleTransport(
        {
            ("POST", PAUSE_PATH): (
                404,
                refusal_envelope(404, "feature_disabled", "family is OFF"),
            )
        }
    )
    code, _, err = run(["pause"], transport=transport, registry=registry)
    assert code == EXIT_CANNOT_ASSESS
    assert "feature_disabled" in err
    assert "invisible" in err


# --- the local refusals: nothing is sent ----------------------------------
def test_a_withheld_verb_is_refused_locally_naming_the_registrys_reason():
    edited = vocabulary.Registry(document=withhold(registry_document(), "fleet.pause"))
    transport = ConsoleTransport({})
    code, out, err = run(["pause"], transport=transport, registry=edited)
    assert code == EXIT_REFUSED
    assert "verb_not_exposed" in err
    assert "test: withheld" in err
    assert transport.requests == [], "a withheld verb must not be asked of the plane"
    assert out == ""


def test_a_missing_caller_identity_is_refused_before_anything_is_sent(registry):
    transport = ConsoleTransport({})
    code, out, err = run(["pause"], transport=transport, registry=registry, session="")
    assert code == EXIT_REFUSED
    assert "no_session" in err
    assert "AO_CONTROL_SESSION" in err
    assert transport.requests == []
    assert out == ""


def test_an_irreversible_verb_needs_its_confirmation_before_anything_is_sent(registry):
    transport = ConsoleTransport({})
    code, out, err = run(["override"], transport=transport, registry=registry)
    assert code == EXIT_REFUSED
    assert "confirmation_required" in err
    assert "fleet.override" in err
    assert transport.requests == []
    assert out == ""


def test_the_confirmation_token_is_the_declared_command_id(registry):
    record = effect_record(verb="fleet.override", effect_class="irreversible")
    transport = ConsoleTransport({("POST", OVERRIDE_PATH): (200, ok_envelope(record))})
    code, out, _ = run(
        ["--confirm", "fleet.override", "override"], transport=transport, registry=registry
    )
    assert code == EXIT_OK
    assert "fleet.override" in out
    assert len(transport.requests) == 1


def test_a_confirmation_for_another_verb_does_not_count(registry):
    transport = ConsoleTransport({})
    code, _, err = run(
        ["--confirm", "fleet.pause", "override"], transport=transport, registry=registry
    )
    assert code == EXIT_REFUSED
    assert "confirmation_required" in err
    assert transport.requests == []


# --- the vocabulary itself -------------------------------------------------
def test_a_drifted_surface_is_refused_by_name_and_sends_nothing():
    edited = vocabulary.Registry(document=drop(registry_document(), "fleet.status"))
    transport = ConsoleTransport({})
    code, out, err = run(["status"], transport=transport, registry=edited)
    assert code == EXIT_CANNOT_ASSESS
    assert "surface_drift" in err
    assert "fleet.status" in err
    assert transport.requests == []
    assert out == ""


def test_an_unreadable_vocabulary_is_refused_by_name(monkeypatch):
    def explode(*_args, **_kwargs):
        raise vocabulary.VocabularyUnreadable("the registry is not there")

    monkeypatch.setattr(vocabulary.Registry, "load", classmethod(explode))
    transport = ConsoleTransport({})
    code, out, err = run(["status"], transport=transport)
    assert code == EXIT_CANNOT_ASSESS
    assert "vocabulary_unreadable" in err
    assert "the registry is not there" in err
    assert transport.requests == []
    assert out == ""


def test_an_unknown_verb_is_a_usage_error():
    with pytest.raises(SystemExit) as caught:
        main(["deploy"], out=io.StringIO(), err=io.StringIO())
    assert caught.value.code != 0


def test_help_names_every_verb_and_the_id_it_speaks(capsys):
    with pytest.raises(SystemExit) as caught:
        main(["--help"])
    assert caught.value.code == EXIT_OK
    text = capsys.readouterr().out
    for verb, verb_id in vocabulary.SURFACE:
        assert verb in text and verb_id in text
    assert "exactly one declared command id" in text
