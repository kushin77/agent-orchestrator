"""The fleet-health publisher: the verdicts it renders, and what it must refuse.

Three refusals are the point of this file, and each is a mutation target:

* a **stale** beat leaves as `stale`, never as `healthy` (the watchdog's verdict
  is rendered, not softened);
* an orphan with **unmerged** work leaves as `shelved`, never as `reclaimed`
  (`governance/reconcile`'s asymmetry surviving the export);
* a state that **could not be established** leaves as `no-data`, never as a
  fabricated `healthy`.

It also proves the two ADR-0022 obligations that make those verdicts safe to
accept: the payload carries no identity at all (no session id, lane, worktree,
branch or commit — a session id is this fleet's `pod_id`), and the export is
**inert** — OFF by default, no endpoint means no send, and a delivery failure is
reported rather than swallowed.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import channel
import health_publish as hp
import health_signals as hs
import watchdog
from governance.reconcile.heartbeat import ORPHAN, SHELVED, stamp
from governance.reconcile.sweep import (
    PARKED,
    RECLAIMED,
    SHELVED_OUTCOME,
    SweepReport,
    sweep,
)

OLD = 1_000_000.0
HEAD = "0f1e2d3c"
LANE_SESSION = "a1b2c3d4e5f6"
LANE_WORKTREE = "/home/nobody/lanes/ao-498-secret-lane"
LANE_BRANCH = "issue-498-secret-branch"
LANE_AGENT = "subagent-secret-identity"


def iso(offset_seconds: float = 0.0) -> str:
    """A heartbeat timestamp, offset from now — the format `channel` parses."""
    moment = datetime.fromtimestamp(time.time() + offset_seconds, tz=timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def beat(*, commit: str = HEAD, age_seconds: float = 0.0) -> dict:
    """A fresh beat by default; `age_seconds` past the TTL makes it a stale one."""
    return {"pid": 4242, "state": "healthy", "commit": commit, "ts": iso(-age_seconds)}


def observation(
    *,
    store_present: bool = True,
    beats: dict | None = None,
    pids: dict | None = None,
    head: str = HEAD,
    report=None,
    report_error: str = "",
):
    facts = tuple(
        hp.RungFacts(name=name, pid=(pids or {}).get(name), beat=(beats or {}).get(name))
        for name in hs.RUNG_NAMES
    )
    return hp.Observation(
        store_present=store_present,
        facts=facts,
        head=head,
        report=report,
        report_error=report_error,
        timestamp_ns=1_700_000_000_000_000_000,
    )


class ReadOnlyOps:
    """The three read-only ports the dry-run sweep consults, and nothing else.

    Injecting this keeps the fleet suite's shelve proof a REAL `sweep()` decision
    (the same code the reconcile worker runs) while performing no teardown.
    """

    def __init__(self, *, present: bool = True, on_main: bool = False, remotely: bool = False) -> None:
        self.present = present
        self.on_main = on_main
        self.remotely = remotely

    def worktree_present(self, path: str) -> bool:
        return self.present

    def preserved_on_main(self, worktree: str) -> bool:
        return self.on_main

    def preserved_remotely(self, worktree: str, branch: str) -> bool:
        return self.remotely


def lane(root: Path, *, at: float = OLD) -> None:
    """A real heartbeat on disk for a lane whose identity must never be exported."""
    stamp(
        LANE_SESSION,
        issue=498,
        agent=LANE_AGENT,
        root=root,
        lane="fleet-health",
        worktree=LANE_WORKTREE,
        branch=LANE_BRANCH,
        pid=4242,
        at=at,
    )


def states(collected) -> dict[str, str]:
    return {
        signal.labels[hs.LABEL_RUNG]: signal.labels[hs.LABEL_STATE]
        for signal in collected
        if signal.kind == hs.SIGNAL_RUNG_STATE
    }


def counts_by_outcome(collected) -> dict[str, float]:
    return {
        signal.labels[hs.LABEL_OUTCOME]: signal.value
        for signal in collected
        if signal.kind == hs.SIGNAL_RECONCILE_OUTCOME
    }


# ── rung state: the watchdog's verdict, rendered, never softened ────────────


def test_every_rung_state_is_the_watchdogs_verdict_verbatim():
    observed = observation(
        beats={"brain": beat(), "sister": beat(commit="deadbeef")},
        pids={"brain": 1, "sister": 2, "monitor": 3},
    )
    assert states(hp.signals(observed)) == {
        "brain": watchdog.HEALTHY,
        "sister": watchdog.DRIFTED,  # decide()'s own token for a commit mismatch
        "monitor": watchdog.HEALTHY,  # presence IS the watchdog's monitor rule
    }


def test_a_stale_beat_publishes_stale_and_never_healthy():
    """Negative control 1: the refusal that `scrape`-by-hand cannot preserve."""
    observed = observation(
        beats={"brain": beat(age_seconds=channel.STALE_HEARTBEAT_SECONDS + 60), "sister": beat()},
        pids={"brain": 1, "sister": 2, "monitor": 3},
    )
    collected = hp.signals(observed)
    by_rung = states(collected)
    assert by_rung["brain"] == watchdog.STALE
    # The fresh rung is untouched: the staleness is per rung, not a family-wide mode.
    assert by_rung["sister"] == watchdog.HEALTHY


def test_a_beat_with_no_timestamp_publishes_stale_not_healthy():
    """An undated beat cannot prove freshness, so it is never published as health."""
    observed = observation(
        beats={"brain": {"pid": 1, "state": "healthy"}, "sister": beat()},
        pids={"brain": 1, "sister": 2, "monitor": 3},
    )
    assert states(hp.signals(observed))["brain"] == watchdog.STALE


def test_an_absent_store_publishes_no_data_and_never_a_healthy():
    """Negative control 3: honest emptiness — nothing measurable is not health."""
    observed = observation(store_present=False)
    collected = hp.signals(observed)
    assert set(states(collected).values()) == {hs.NO_DATA}
    verdicts = {
        signal.labels[hs.LABEL_VERDICT]
        for signal in collected
        if signal.kind == hs.SIGNAL_WATCHDOG_VERDICT
    }
    assert verdicts == {hs.NO_DATA}
    assert counts_by_outcome(collected) == {hs.NO_DATA: 1}
    assert watchdog.HEALTHY not in states(collected).values()


def test_no_age_point_is_fabricated_for_a_rung_whose_beat_cannot_be_read():
    """A number would read as a measurement; the rung's own verdict carries the fact."""
    observed = observation(beats={"sister": beat()}, pids={"sister": 2})
    collected = hp.signals(observed)
    ages = {
        signal.labels[hs.LABEL_RUNG]
        for signal in collected
        if signal.kind == hs.SIGNAL_RUNG_BEAT_AGE
    }
    assert ages == {"sister"}
    # No process and no beat: the watchdog's own token for that is `missing`, and
    # it is published rather than replaced by an invented age.
    assert states(collected)["brain"] == watchdog.MISSING
    assert states(collected)["monitor"] == watchdog.MISSING


def test_the_beat_age_bucket_follows_the_declared_ttl():
    fresh = hp.beat_age_signals(observation(beats={"brain": beat()}, pids={"brain": 1}))
    stale = hp.beat_age_signals(
        observation(
            beats={"brain": beat(age_seconds=channel.STALE_HEARTBEAT_SECONDS + 60)},
            pids={"brain": 1},
        )
    )
    assert [signal.labels[hs.LABEL_BUCKET] for signal in fresh] == [hs.BUCKET_FRESH]
    assert [signal.labels[hs.LABEL_BUCKET] for signal in stale] == [hs.BUCKET_STALE]


def test_a_rung_with_no_declared_capability_gets_no_invented_verdict():
    """`channel.CAPABILITY_RUNGS` decides which rungs the watchdog speaks for."""
    observed = observation(beats={"brain": beat()}, pids={"brain": 1, "monitor": 3})
    rungs = {
        signal.labels[hs.LABEL_RUNG]
        for signal in hp.signals(observed)
        if signal.kind == hs.SIGNAL_WATCHDOG_VERDICT
    }
    assert rungs == set(channel.CAPABILITY_RUNGS)


# ── reconciliation: the sweep's decision, exported verbatim ─────────────────


def test_a_shelved_orphan_publishes_shelved_and_never_reclaimed(tmp_path: Path):
    """Negative control 2: unmerged work is never traded for an unlocked issue."""
    (tmp_path / ".fleet").mkdir()
    lane(tmp_path)
    report = sweep(tmp_path, apply=False, ops=ReadOnlyOps(present=True, on_main=False, remotely=False))
    assert [action.outcome for action in report.actions] == [SHELVED_OUTCOME]

    observed = hp.observe(
        beats={"brain": beat(), "sister": beat()},
        pids={"brain": 1, "sister": 2, "monitor": 3},
        fleet_dir=tmp_path / ".fleet",
        root=tmp_path,
        ops=ReadOnlyOps(present=True, on_main=False, remotely=False),
        head=HEAD,
    )
    published = counts_by_outcome(hp.signals(observed))
    assert published[SHELVED_OUTCOME] == 1
    assert published[RECLAIMED] == 0
    assert published[PARKED] == 0
    # The judge's own verdict rides alongside: an orphan, which is what it is.
    assert published[ORPHAN] == 1
    assert published[SHELVED] == published[SHELVED_OUTCOME]


def test_a_landed_lane_publishes_reclaimed(tmp_path: Path):
    (tmp_path / ".fleet").mkdir()
    lane(tmp_path)
    observed = hp.observe(
        beats={"brain": beat(), "sister": beat()},
        pids={"brain": 1, "sister": 2, "monitor": 3},
        fleet_dir=tmp_path / ".fleet",
        root=tmp_path,
        ops=ReadOnlyOps(present=True, on_main=True),
        head=HEAD,
    )
    published = counts_by_outcome(hp.signals(observed))
    assert published[RECLAIMED] == 1
    assert published[SHELVED_OUTCOME] == 0


def test_a_lane_preserved_only_remotely_publishes_parked(tmp_path: Path):
    (tmp_path / ".fleet").mkdir()
    lane(tmp_path)
    observed = hp.observe(
        beats={"brain": beat(), "sister": beat()},
        pids={"brain": 1, "sister": 2, "monitor": 3},
        fleet_dir=tmp_path / ".fleet",
        root=tmp_path,
        ops=ReadOnlyOps(present=True, remotely=True),
        head=HEAD,
    )
    published = counts_by_outcome(hp.signals(observed))
    assert published[PARKED] == 1
    assert published[RECLAIMED] == 0


def test_every_declared_outcome_is_published_including_zeros():
    """A zero is a reading ("this pass saw none"), and it must stay distinct from no-data."""
    published = counts_by_outcome(hp.signals(observation(report=SweepReport(actions=[]))))
    assert set(published) == set(hs.RECONCILE_OUTCOMES) - {hs.NO_DATA}
    assert set(published.values()) == {0.0}


def test_an_unreadable_reconciliation_store_publishes_no_data_not_a_zero():
    published = counts_by_outcome(hp.signals(observation(report=None, report_error="OSError: gone")))
    assert published == {hs.NO_DATA: 1}


def test_a_raising_sweep_is_reported_as_no_data_rather_than_crashing(tmp_path: Path):
    class Exploding(ReadOnlyOps):
        def worktree_present(self, path: str) -> bool:
            raise RuntimeError("git is not available")

    (tmp_path / ".fleet").mkdir()
    lane(tmp_path)
    observed = hp.observe(
        beats={"brain": beat(), "sister": beat()},
        pids={"brain": 1, "sister": 2, "monitor": 3},
        fleet_dir=tmp_path / ".fleet",
        root=tmp_path,
        ops=Exploding(),
        head=HEAD,
    )
    assert observed.report is None
    assert "git is not available" in observed.report_error
    assert counts_by_outcome(hp.signals(observed)) == {hs.NO_DATA: 1}


# ── the payload: closed labels, and no identity ────────────────────────────


def test_the_payload_carries_no_identity_at_all(tmp_path: Path):
    """ADR-0022 D5 / vendor #178: a session id is this fleet's `pod_id`."""
    (tmp_path / ".fleet").mkdir()
    lane(tmp_path)
    observed = hp.observe(
        beats={"brain": beat(), "sister": beat()},
        pids={"brain": 1, "sister": 2, "monitor": 3},
        fleet_dir=tmp_path / ".fleet",
        root=tmp_path,
        ops=ReadOnlyOps(present=True),
        head=HEAD,
    )
    rendered = json.dumps(hp.render(observed), sort_keys=True)
    for secret in (LANE_SESSION, LANE_WORKTREE, LANE_BRANCH, LANE_AGENT, HEAD):
        assert secret not in rendered, f"the payload leaked {secret!r}"


def test_every_attribute_key_is_in_the_closed_label_set():
    rendered = hp.render(
        observation(beats={"brain": beat(), "sister": beat()}, pids={"brain": 1, "sister": 2, "monitor": 3})
    )
    keys = {
        attribute["key"]
        for point in _data_points(rendered)
        for attribute in point["attributes"]
    }
    assert keys <= hs.LABEL_KEYS
    assert {"service", "signal"} <= keys


def test_the_payload_declares_the_four_kinds_with_their_units():
    rendered = hp.render(
        observation(beats={"brain": beat(), "sister": beat()}, pids={"brain": 1, "sister": 2, "monitor": 3})
    )
    metrics = rendered["resourceMetrics"][0]["scopeMetrics"][0]["metrics"]
    assert [metric["name"] for metric in metrics] == [
        hs.metric_name(kind) for kind in hs.SIGNAL_KINDS
    ]
    assert {metric["unit"] for metric in metrics} == set(hs.UNITS.values())


def test_the_resource_names_the_service_constant():
    rendered = hp.render(observation())
    attributes = rendered["resourceMetrics"][0]["resource"]["attributes"]
    assert attributes == [{"key": "service.name", "value": {"stringValue": hs.SERVICE}}]


def _data_points(rendered: dict) -> list[dict]:
    points: list[dict] = []
    for metric in rendered["resourceMetrics"][0]["scopeMetrics"][0]["metrics"]:
        points.extend(metric["gauge"]["dataPoints"])
    return points


# ── inert by design: flag OFF, no endpoint, honest failure ─────────────────


def test_the_surface_flag_denies_by_default_when_the_registry_has_no_entry(tmp_path: Path):
    registry = tmp_path / "registry.yaml"
    registry.write_text("surfaces:\n  something_else:\n    default: on\n", encoding="utf-8")
    state, why = hp.surface_flag(env={}, registry=registry)
    assert state == hp.OFF
    assert hp.FLAG_SURFACE in why


def test_the_surface_flag_reads_a_promoted_entry(tmp_path: Path):
    """`default: on` parses as the YAML boolean True — the reader must accept both."""
    registry = tmp_path / "registry.yaml"
    registry.write_text(f"surfaces:\n  {hp.FLAG_SURFACE}:\n    default: on\n", encoding="utf-8")
    assert hp.surface_flag(env={}, registry=registry)[0] == hp.ON
    quoted = tmp_path / "quoted.yaml"
    quoted.write_text(f"surfaces:\n  {hp.FLAG_SURFACE}:\n    default: \"on\"\n", encoding="utf-8")
    assert hp.surface_flag(env={}, registry=quoted)[0] == hp.ON


def test_the_surface_flag_denies_on_an_unreadable_or_unparseable_registry(tmp_path: Path):
    assert hp.surface_flag(env={}, registry=tmp_path / "absent.yaml")[0] == hp.OFF
    broken = tmp_path / "broken.yaml"
    broken.write_text("surfaces: [this is not a mapping\n", encoding="utf-8")
    assert hp.surface_flag(env={}, registry=broken)[0] == hp.OFF


def test_the_environment_override_is_a_recognised_value_or_a_refusal():
    assert hp.surface_flag(env={hp.FLAG_ENV: "on"})[0] == hp.ON
    assert hp.surface_flag(env={hp.FLAG_ENV: "off"})[0] == hp.OFF
    assert hp.surface_flag(env={hp.FLAG_ENV: "maybe"})[0] == hp.OFF


def test_the_surface_gate_agrees_with_the_repos_established_reader(tmp_path: Path):
    """A second, divergent flag reader is how a surface ships on by accident.

    `portal/server/fleet.py` already reads `surfaces.<key>.default` fail-closed for
    the portal's surfaces. This family reads its own entry with the same rule, and
    this test pins the two together on the same documents.
    """
    from portal.server.fleet import read_surface_default

    documents = {
        "promoted-unquoted": f"surfaces:\n  {hp.FLAG_SURFACE}:\n    default: on\n",
        "promoted-quoted": f'surfaces:\n  {hp.FLAG_SURFACE}:\n    default: "on"\n',
        "boolean-true": f"surfaces:\n  {hp.FLAG_SURFACE}:\n    default: true\n",
        "unpromoted": f"surfaces:\n  {hp.FLAG_SURFACE}:\n    default: off\n",
        "absent-entry": "surfaces:\n  something_else:\n    default: on\n",
        "absent-section": "services:\n  portal:\n    default: off\n",
        "not-a-mapping": "surfaces: []\n",
    }
    for name, text in documents.items():
        registry = tmp_path / f"{name}.yaml"
        registry.write_text(text, encoding="utf-8")
        mine = hp.surface_flag(env={}, registry=registry)[0]
        theirs = read_surface_default(tmp_path, registry_path=registry, surface=hp.FLAG_SURFACE)
        assert mine == theirs, f"{name}: this family says {mine!r}, the repo convention says {theirs!r}"


def test_an_unconfigured_endpoint_sends_nothing_at_all():
    calls: list[bytes] = []

    def post(endpoint: str, body: bytes, timeout: float):
        calls.append(body)
        return 200, ""

    delivery = hp.deliver({"resourceMetrics": []}, "", post=post)
    assert delivery.attempted is False
    assert delivery.ok is False
    assert hp.ENDPOINT_ENV in delivery.detail
    assert calls == []


def test_a_rejected_push_is_reported_as_a_failure_not_a_success():
    def post(endpoint: str, body: bytes, timeout: float):
        return 503, "HTTP 503 Service Unavailable"

    delivery = hp.deliver({"resourceMetrics": []}, "http://plane.invalid:4318", post=post)
    assert (delivery.attempted, delivery.ok, delivery.status) == (True, False, 503)
    assert "503" in delivery.detail


def test_a_transport_that_raises_is_reported_not_propagated():
    def post(endpoint: str, body: bytes, timeout: float):
        raise OSError("connection refused")

    delivery = hp.deliver({"resourceMetrics": []}, "http://plane.invalid:4318", post=post)
    assert delivery.ok is False
    assert "OSError" in delivery.detail


def test_a_successful_push_sends_the_rendered_payload():
    sent: list[dict] = []

    def post(endpoint: str, body: bytes, timeout: float):
        sent.append(json.loads(body))
        assert endpoint == "http://plane.invalid:4318"
        return 200, ""

    rendered = hp.render(
        observation(beats={"brain": beat(), "sister": beat()}, pids={"brain": 1, "sister": 2, "monitor": 3})
    )
    delivery = hp.deliver(rendered, "http://plane.invalid:4318", post=post)
    assert delivery.ok is True
    assert sent == [rendered]


def test_push_refuses_with_cannot_assess_while_the_surface_is_off(monkeypatch):
    """The whole point of flag-gated OFF: the CLI must not reach the planner at all."""
    monkeypatch.setenv(hp.FLAG_ENV, "off")
    assert hp.main(["push"]) == hp.EXIT_CANNOT_ASSESS


def test_push_refuses_with_cannot_assess_when_the_plane_named_no_endpoint(monkeypatch):
    monkeypatch.setenv(hp.FLAG_ENV, "on")
    monkeypatch.delenv(hp.ENDPOINT_ENV, raising=False)
    assert hp.main(["push"]) == hp.EXIT_CANNOT_ASSESS
