"""Named controls for the verdict-convergence pass (issue #1506).

`scripts/gate-status.sh conclude` is the verb nothing scheduled until this pass
existed, and a scheduled pass has two ways to become worse than the gap it
closes: it can re-publish a status on every tick (API churn wearing a
convergence's clothes), or it can report a publication it never made. Both are
named controls below.

Every test drives the pass with FAKE seams: no test touches the venue, the
poster, or the real repository.
"""

from __future__ import annotations

from pathlib import Path

from fleet.runner import evidence as ev
from fleet.runner.model import SOURCE_GATE_STATUS, SOURCE_LOCAL, Evidence
from fleet.runner.verify import (
    MAX_CONCLUDE_PER_CYCLE,
    Ledger,
    Result,
    classify_conclude,
    heads_carrying_the_context,
    publish_concluded,
    real_conclude_status,
)

PR, SHA = 41, "a" * 40


class Fake:
    """A recording transport: `handler(argv) -> Result | None`."""

    def __init__(self, handler=None):
        self.calls: list[tuple[list[str], dict]] = []
        self.handler = handler

    def __call__(self, argv, **kwargs) -> Result:
        self.calls.append((list(argv), kwargs))
        if self.handler is not None:
            return self.handler(list(argv))
        return Result(0, "")

    def argvs(self) -> list[list[str]]:
        return [argv for argv, _ in self.calls]


def published_ok(sha: str) -> Result:
    return Result(0, "gate-status: conclude OK — ao/gate-of-record=success for %s: the venue of record's run B1 concluded SUCCESS, and the read-back agrees that this is the newest claim on the commit" % sha[:12])


def in_flight(sha: str) -> Result:
    return Result(
        2,
        "",
        "gate-status: REFUSED — the venue of record's run B1 for %s has NOT concluded (status 'WORKING'): a verdict is published only once the run reaches one" % sha[:12],
    )


def table_with(pr: int, sha: str, source: str, state: str = "success") -> ev.EvidenceTable:
    table = ev.EvidenceTable()
    table.add(Evidence(pr=pr, sha=sha, source=source, state=state))
    return table


def test_a_head_that_already_carries_the_context_is_not_asked(tmp_path: Path):
    """The filter is what stops a per-tick re-poster from accumulating statuses.

    #1504's measured state is a head carrying NOTHING; a head that already
    carries the context is converged, and asking the poster again would post a
    second status for the same commit every tick.
    """
    sh = Fake()
    ledger = Ledger(tmp_path / "ledger.jsonl")
    table = table_with(PR, SHA, SOURCE_GATE_STATUS)
    assert heads_carrying_the_context(table) == {(PR, SHA)}
    out: list[tuple[int, str]] = publish_concluded(
        [(PR, SHA)],
        already_carrying=heads_carrying_the_context(table),
        conclude=real_conclude_status(sh, tmp_path),
        ledger=ledger,
        out=open("/dev/null", "w"),
    )
    assert out == [] and sh.argvs() == [], "a converged head must cost no call at all"
    assert ledger.rows() == [], "a head with nothing to do writes no ledger row"


def test_a_local_marker_is_not_the_context(tmp_path: Path):
    """Only `ao/gate-of-record` counts as carrying the context.

    A local-marker record says the BOX measured the head; it is not the required
    context, so a head whose only record is a local marker still needs the
    venue's verdict published.
    """
    table = table_with(PR, SHA, SOURCE_LOCAL)
    assert heads_carrying_the_context(table) == set()


def test_a_head_carrying_nothing_is_asked_once_with_the_posters_verb(tmp_path: Path):
    """The invoker asks the POSTER, with the poster's own verb (issue #1506).

    Nothing here re-implements the API call or the refusals: the argv is the
    poster's, so the read-back that decides which claim stands stays in ONE
    owner.
    """
    sh = Fake(lambda argv: published_ok(SHA))
    ledger = Ledger(tmp_path / "ledger.jsonl")
    out = publish_concluded(
        [(PR, SHA)],
        already_carrying=set(),
        conclude=real_conclude_status(sh, tmp_path),
        ledger=ledger,
        out=open("/dev/null", "w"),
    )
    assert out == [(PR, SHA)]
    assert sh.argvs() == [["bash", "scripts/gate-status.sh", "conclude", "--sha", SHA]]
    rows = ledger.rows()
    assert len(rows) == 1 and rows[0]["state"] == "published" and rows[0]["rc"] == 0


def test_an_in_flight_venue_run_is_awaited_and_never_counted_as_published(tmp_path: Path):
    """A run that has not concluded is a WAIT, not a refusal and never a publish.

    #1504's property 2: a verdict is published only once the run reaches one. A
    pass that counted this as published would report a verdict the venue had
    not reached, and the head would stay ungated.
    """
    sh = Fake(lambda argv: in_flight(SHA))
    ledger = Ledger(tmp_path / "ledger.jsonl")
    out = publish_concluded(
        [(PR, SHA)],
        already_carrying=set(),
        conclude=real_conclude_status(sh, tmp_path),
        ledger=ledger,
        out=open("/dev/null", "w"),
    )
    assert out == []
    assert ledger.rows()[0]["state"] == "awaiting-verdict"


def test_an_unrecognised_answer_is_never_a_publication(tmp_path: Path):
    """Fail closed: a result the pass cannot read is not evidence of a publish."""
    assert classify_conclude(0, "")[0] == "unassessable"
    assert classify_conclude(137, "bash: bash: command not found")[0] == "unassessable"
    assert classify_conclude(2, "gate-status: CANNOT-ASSESS — the CI venue's verdict for abc could not be read (boom)")[0] == "unassessable"
    # ...and the poster's own words land where they should.
    assert classify_conclude(0, "gate-status: conclude OK — ao/gate-of-record=success for abc")[0] == "published"
    assert classify_conclude(2, in_flight(SHA).err)[0] == "awaiting-verdict"
    assert classify_conclude(2, "gate-status: CANNOT-ASSESS — the venue of record produced no run for abc")[0] == "no-venue-run"


def test_the_cap_defers_by_name_and_never_drops_a_head(tmp_path: Path):
    """The bound is named, and what it cannot take is DEFERRED, never dropped.

    A pass that silently stopped at the cap would leave the heads past it
    ungated with nothing in the record to say so.
    """
    heads = [(100 + index, "%040d" % index) for index in range(MAX_CONCLUDE_PER_CYCLE + 2)]
    sh = Fake(lambda argv: published_ok(argv[4]))
    ledger = Ledger(tmp_path / "ledger.jsonl")
    out = publish_concluded(
        heads,
        already_carrying=set(),
        conclude=real_conclude_status(sh, tmp_path),
        ledger=ledger,
        out=open("/dev/null", "w"),
    )
    assert len(out) == MAX_CONCLUDE_PER_CYCLE
    deferred = [row for row in ledger.rows() if row["state"] == "deferred"]
    assert len(deferred) == 2
    assert all(row["reason"] == "capacity:%d" % MAX_CONCLUDE_PER_CYCLE for row in deferred)
    assert len(sh.argvs()) == MAX_CONCLUDE_PER_CYCLE, "the cap bounds the calls, not just the reporting"
