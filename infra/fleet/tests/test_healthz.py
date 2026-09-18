"""Tests for infra/fleet/healthz.py's /health, /healthz and /metrics surfaces.

Issue #712 (EPIC #706 D4) adds three properties to the D2 health surface, and
each gets a control here that FAILS when the property is missing (no-false-green
doctrine, AGENTS.md rule 8):

* `/health` answers exactly what `/healthz` answers (the peer-contract alias);
* a decision document whose `finished_at` has gone stale answers 503, even
  though its stored `verdict` is `ok` — "the process exists" is not enough;
* a decision document MISSING ANY ONE ENABLED RUNG of the schedule answers 503
  naming the missing marker.

Issue #1148 repairs that third property, which could not fail: it compared the
document's job COUNT against a literal (`EXPECTED_JOBS = 3`) while the schedule
declared four enabled rungs, so a document missing `ao-fleet-reap` answered 200.
The expected set is now DERIVED from `config/fleet-jobs.json` through
`fleet/cron.py` (the schedule's single owner), and BOTH halves are asserted
below: a complete document answers 200, and every enabled rung is removed in
turn so a future fifth rung cannot silently go unchecked.

`/metrics` is checked for exposition-format shape and for agreeing with
`/health`'s own up/down verdict, since both are meant to read one document.
"""

from __future__ import annotations

import json
import sys
import threading
import time
import urllib.request
from http.client import HTTPResponse
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import healthz  # noqa: E402

#: The enabled marker set, derived the SAME way the module derives it — through
#: the schedule's own owner. Computed once here, so every test below is about
#: the decision document rather than about re-deriving the declaration.
ENABLED_MARKERS: tuple[str, ...] = healthz.declared_enabled_markers() or ()

#: The rung whose addition is what made the removed literal stale, named so a
#: failure of the derivation says which rung it lost.
FOURTH_RUNG = "ao-fleet-reap"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _document(markers: tuple[str, ...] | None = None, age_seconds=0, verdict="ok", changed=None):
    markers = ENABLED_MARKERS if markers is None else markers
    finished = time.gmtime(time.time() - age_seconds)
    return {
        "verdict": verdict,
        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", finished),
        "jobs": [{"marker": marker, "rc": 0} for marker in markers],
        "state": {"attributable_changes": changed or []},
    }


# ---------------------------------------------------------------------------
# The derivation itself — the expected set is the schedule's, never a literal
# ---------------------------------------------------------------------------


def test_the_enabled_set_is_derived_through_the_schedules_owner():
    """The expected set comes from `fleet/cron.py`, and it is NOT the stale 3.

    If this control fails, every other one here is measuring the wrong thing:
    the whole defect was a hardcoded count, so the derivation is asserted
    before anything is asserted about it.
    """
    assert ENABLED_MARKERS, "the schedule declaration must be derivable in this checkout"
    assert len(ENABLED_MARKERS) > 3, (
        "the schedule declares more than the three rungs the removed literal named; "
        f"derived {ENABLED_MARKERS!r}"
    )
    assert FOURTH_RUNG in ENABLED_MARKERS

    # ... and it agrees with the owner, module for module, rather than with a copy.
    sys.path.insert(0, str(HERE.parent.parent / "fleet"))
    import cron  # noqa: PLC0415

    assert ENABLED_MARKERS == tuple(
        str(job["marker"]) for job in cron.enabled_jobs(cron.load_manifest())
    )


def test_a_disabled_job_is_not_expected():
    """`snapshot-refresh` is ship-gated OFF (#241), so it is NOT part of the set.

    Without this half, "derive the enabled set" and "derive every declared job"
    would pass the same test.
    """
    assert "ao-fleet-snapshot-refresh" not in ENABLED_MARKERS


# ---------------------------------------------------------------------------
# decision_status — pure function controls
# ---------------------------------------------------------------------------


def test_no_document_is_not_ready():
    status, body = healthz.decision_status(None)
    assert status == 503
    assert body["status"] == "not-ready"


def test_ok_document_with_all_jobs_is_200():
    status, body = healthz.decision_status(_document())
    assert status == 200
    assert body["jobs"] == len(ENABLED_MARKERS)
    assert body["expected_jobs"] == len(ENABLED_MARKERS)
    assert body["expected_markers"] == list(ENABLED_MARKERS)


def test_verdict_not_ok_is_503():
    status, body = healthz.decision_status(_document(verdict="not-ok"))
    assert status == 503
    assert body["status"] == "failed"


def test_attributable_state_change_is_503():
    status, body = healthz.decision_status(_document(changed=["a/b"]))
    assert status == 503
    assert body["attributable_changes"] == ["a/b"]


@pytest.mark.parametrize("missing", ENABLED_MARKERS or ["<no markers derivable>"])
def test_a_document_missing_any_one_enabled_rung_is_503_naming_it(missing):
    """THE ACCEPTANCE (issue #1148), one rung at a time.

    Every enabled rung is removed in turn — including `ao-fleet-reap`, the rung
    whose addition is what made the old literal stale — and the surface must
    refuse BY NAME. Parametrising over the DERIVED set, rather than naming one
    rung, is what stops a future fifth rung from silently going unchecked.
    """
    short = tuple(marker for marker in ENABLED_MARKERS if marker != missing)
    status, body = healthz.decision_status(_document(markers=short))
    assert status == 503, f"a document missing {missing} answered {status}"
    assert body["status"] == "incomplete"
    assert body["missing_jobs"] == [missing]
    assert missing in body["reason"], f"the refusal did not name {missing}: {body['reason']!r}"
    assert body["expected_jobs"] == len(ENABLED_MARKERS)


def test_the_old_literal_shape_is_what_this_replaces():
    """A THREE-rung document is short by exactly one, and must be refused.

    This is the measured false-green, asserted: under the removed
    `EXPECTED_JOBS = 3` a document holding the first three enabled rungs
    answered 200. It must now answer 503 naming the fourth.
    """
    first_three = ENABLED_MARKERS[:3]
    assert len(first_three) == 3
    status, body = healthz.decision_status(_document(markers=first_three))
    assert status == 503
    assert body["status"] == "incomplete"
    assert body["jobs"] == 3
    assert body["expected_jobs"] == 4
    assert FOURTH_RUNG in body["missing_jobs"]


def test_an_unreadable_declaration_fails_closed(monkeypatch):
    """The declaration IS the thing under test, so losing it must not be a pass.

    A surface that cannot learn what the schedule owes answers 503 with a named
    reason — never 200 on a stale count, and never a silent 0.
    """
    monkeypatch.setattr(healthz, "declared_enabled_markers", lambda: None)
    status, body = healthz.decision_status(_document())
    assert status == 503
    assert body["status"] == "cannot-assess"
    assert body["expected_jobs"] is None
    assert "fleet/cron.py" in body["reason"]


def test_an_empty_declaration_fails_closed():
    """An empty expected set is `nothing can be certified`, not `nothing owed`."""
    status, body = healthz.decision_status(_document(), expected_markers=())
    assert status == 503
    assert body["status"] == "cannot-assess"
    assert body["expected_jobs"] == 0


def test_an_explicit_expected_set_is_honoured():
    """The seam the tests and the covering gate drive with, asserted directly."""
    status, body = healthz.decision_status(_document(markers=("only-rung",)), expected_markers=("only-rung",))
    assert status == 200
    assert body["expected_jobs"] == 1

    status, body = healthz.decision_status(_document(markers=("only-rung",)), expected_markers=("only-rung", "other"))
    assert status == 503
    assert body["missing_jobs"] == ["other"]


def test_stale_heartbeat_is_503_even_with_verdict_ok():
    """The core #712 acceptance: a stale decision fails even when its own
    stored verdict says ok — staleness is measured by THIS surface, not
    merely trusted from the document.
    """
    stale = _document(age_seconds=10_000)
    status, body = healthz.decision_status(stale, stale_after_seconds=900)
    assert status == 503
    assert body["status"] == "stale"
    assert body["age_seconds"] >= 10_000


def test_fresh_document_within_bound_is_200():
    fresh = _document(age_seconds=10)
    status, body = healthz.decision_status(fresh, stale_after_seconds=900)
    assert status == 200


def test_still_running_dev_run_is_never_stale():
    """dev_run.py (D2) writes its decision document ONCE and then parks on
    /healthz for the container's whole life, `stop.clean` staying None the
    entire time. A two-hour-old document from a run that is still up and
    serving must be 200, never 503 — the false-red this repo already names
    as a precedent (dc3de7f).
    """
    doc = _document(age_seconds=7_200)
    doc["stop"] = {"signal": None, "clean": None, "note": "still running"}
    status, body = healthz.decision_status(doc, stale_after_seconds=900)
    assert status == 200
    assert body["still_running"] is True


def test_stopped_dev_run_is_still_subject_to_the_staleness_bound():
    doc = _document(age_seconds=7_200)
    doc["stop"] = {"signal": "SIGTERM", "clean": True, "note": "clean stop"}
    status, body = healthz.decision_status(doc, stale_after_seconds=900)
    assert status == 503
    assert body["status"] == "stale"


def test_a_document_with_no_stop_field_uses_the_staleness_bound_normally():
    """A production writer that doesn't use dev_run.py's `stop` shape gets the
    ordinary staleness check — the still-running exemption is specific to that
    one field, not a blanket skip for anything without a `stop`.
    """
    doc = _document(age_seconds=7_200)
    assert "stop" not in doc
    status, body = healthz.decision_status(doc, stale_after_seconds=900)
    assert status == 503
    assert body["status"] == "stale"


def test_missing_timestamp_is_503():
    doc = _document()
    del doc["finished_at"]
    status, body = healthz.decision_status(doc)
    assert status == 503
    assert body["status"] == "failed"


# ---------------------------------------------------------------------------
# render_metrics — agrees with decision_status, valid exposition shape
# ---------------------------------------------------------------------------


def test_metrics_up_matches_health_status_ok():
    doc = _document(age_seconds=5)
    text = healthz.render_metrics(doc)
    assert "fleet_cron_up 1" in text
    assert f"fleet_cron_jobs_present {len(ENABLED_MARKERS)}" in text
    assert f"fleet_cron_jobs_expected {len(ENABLED_MARKERS)}" in text
    for marker in ENABLED_MARKERS:
        assert f'fleet_cron_job_ok{{job="{marker}"}} 1' in text


def test_metrics_reports_an_underivable_declaration_as_unknown(monkeypatch):
    """-1, never a 0 and never a stale count a scraper would read as a measurement."""
    monkeypatch.setattr(healthz, "declared_enabled_markers", lambda: None)
    text = healthz.render_metrics(_document())
    assert "fleet_cron_jobs_expected -1" in text
    assert "fleet_cron_up 0" in text


def test_metrics_up_matches_health_status_stale():
    doc = _document(age_seconds=10_000)
    text = healthz.render_metrics(doc, stale_after_seconds=900)
    assert "fleet_cron_up 0" in text


def test_metrics_has_help_and_type_for_every_metric():
    text = healthz.render_metrics(_document())
    for metric in (
        "fleet_cron_up",
        "fleet_cron_jobs_present",
        "fleet_cron_jobs_expected",
        "fleet_cron_decision_age_seconds",
        "fleet_cron_job_ok",
    ):
        assert f"# HELP {metric} " in text
        assert f"# TYPE {metric} " in text


# ---------------------------------------------------------------------------
# The live HTTP surface — /health is an alias of /healthz, /metrics is served,
# an unknown path is 404.
# ---------------------------------------------------------------------------


@pytest.fixture()
def running_server(tmp_path):
    decision = tmp_path / "decision.json"
    decision.write_text(json.dumps(_document(age_seconds=5)), encoding="utf-8")
    ready = threading.Event()
    server = healthz.serve(0, decision, ready, stale_after_seconds=900, expected_markers=ENABLED_MARKERS)
    ready.wait(timeout=5)
    port = server.server_address[1]
    try:
        yield port, decision
    finally:
        server.shutdown()
        server.server_close()


def _get(port: int, path: str) -> tuple[int, bytes, str]:
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}")
    try:
        response: HTTPResponse = urllib.request.urlopen(request, timeout=5)
        return response.status, response.read(), response.headers.get("Content-Type", "")
    except urllib.error.HTTPError as exc:  # noqa: PERF203 — the error carries the body we need
        return exc.code, exc.read(), exc.headers.get("Content-Type", "")


def test_health_and_healthz_answer_identically(running_server):
    port, _decision = running_server
    status_a, body_a, _ct_a = _get(port, "/healthz")
    status_b, body_b, _ct_b = _get(port, "/health")
    assert status_a == status_b == 200
    assert body_a == body_b


def test_metrics_endpoint_is_served(running_server):
    port, _decision = running_server
    status, body, content_type = _get(port, "/metrics")
    assert status == 200
    assert "text/plain" in content_type
    assert b"fleet_cron_up 1" in body


def test_unknown_path_is_404(running_server):
    port, _decision = running_server
    status, body, _ct = _get(port, "/nope")
    assert status == 404
    payload = json.loads(body)
    assert payload["status"] == "not-found"


def test_probe_cli_exits_0_when_healthy(running_server):
    """The Dockerfile's HEALTHCHECK CMD is `healthz.py probe`; it must exit 0
    against a running, healthy server and 1 against nothing listening.
    """
    import subprocess
    import sys as _sys

    port, _decision = running_server
    result = subprocess.run(
        [_sys.executable, str(HERE / "healthz.py"), "probe", "--port", str(port)],
        capture_output=True,
    )
    assert result.returncode == 0


def test_probe_cli_exits_1_when_nothing_listens():
    import subprocess
    import sys as _sys

    result = subprocess.run(
        [_sys.executable, str(HERE / "healthz.py"), "probe", "--port", "1"],
        capture_output=True,
    )
    assert result.returncode == 1


def test_healthz_flips_unhealthy_when_document_goes_stale(running_server):
    """The container-level analogue of #712's own Verify: kill the freshness of
    the evidence (here: rewrite the document as stale) and the probe must flip.
    """
    port, decision = running_server
    status, _body, _ct = _get(port, "/health")
    assert status == 200

    decision.write_text(json.dumps(_document(age_seconds=10_000)), encoding="utf-8")
    status, body, _ct = _get(port, "/health")
    assert status == 503
    payload = json.loads(body)
    assert payload["status"] == "stale"


def test_healthz_flips_unhealthy_when_a_rung_disappears(running_server):
    """The container-level analogue of #1148's own acceptance: drop ONE rung
    from the document on disk and the probe must flip, naming it — over the real
    HTTP surface, not only through the pure function.
    """
    port, decision = running_server
    status, _body, _ct = _get(port, "/healthz")
    assert status == 200

    short = tuple(marker for marker in ENABLED_MARKERS if marker != FOURTH_RUNG)
    decision.write_text(json.dumps(_document(markers=short, age_seconds=5)), encoding="utf-8")
    status, body, _ct = _get(port, "/healthz")
    assert status == 503
    payload = json.loads(body)
    assert payload["status"] == "incomplete"
    assert payload["missing_jobs"] == [FOURTH_RUNG]
    assert FOURTH_RUNG in payload["reason"]
