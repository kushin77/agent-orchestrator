"""The one clock seam — the money path can be pinned, in one place (#1025).

WHY this module exists
----------------------
``telemetry/`` used to answer "what time is it?" in nine places, each behind its
own copy of a timestamp helper, so a test could pin its *seed* while the
*evaluation* silently resolved to whatever day the suite ran on.  That is the
#506 date bomb (``docs/PYTHON-PATTERNS.md`` PP-1): green on the day it was
written, red every day after, with no commit in between.  ``telemetry/clock.py``
is the one seam now, and this module is its proof — not a re-test of the helpers
(``test_model_ratecards`` and friends already cover their shapes).

WHAT each half proves
---------------------
* **the freeze reaches every helper** — every helper that used to read the clock
  itself returns *exactly* the pinned instant, and both the instant and its
  successor (a different year, month and day) are literals, so nothing here can
  expire with the calendar.  A module that kept a raw read cannot satisfy both
  halves: that is the mutation, in behavioural form;
* **the mutant is caught out-of-process** — a scratch copy of the tree carries
  the pre-#1025 shape restored in ``metering/model.py`` (its own ``now_utc_iso``
  calling the clock directly), and the same probe must go red on it.  The
  unmutated copy is probed first and must be green, so a red mutant is the
  mutation and not a broken import;
* **no module reads the wall clock directly** — a closed, named rule over the
  non-test modules of this pillar, with a planted read proving the rule can
  fail (a rule that finds nothing is not a gate).  A *duration* clock
  (``time.monotonic``) is deliberately not a refusal: it cannot expire with the
  calendar.  The latency surfaces' injectable defaults
  (``observability/intake.py``, ``observability/exposition.py``) are likewise
  not direct call sites — this rule refuses a *call*, and a caller can override
  those two;
* **both import roots reach the same seam** — ``telemetry/`` is a PEP-420
  namespace, and this pillar's ledger package is imported both as
  ``telemetry.ledger`` and flat as ``ledger`` (``telemetry/ledger/tests``,
  ``telemetry/audit/read_model.py``, ``scripts/check-audit-read-model.sh``).
  Measured: ``import telemetry.clock`` fails under the flat root, so the seam is
  reached there by its second name — and both names must obey one freeze;
* **a freeze that cannot be understood is refused**, never silently ignored: a
  typo in a gate's environment would otherwise restore the live clock and turn
  the frozen run back into the date bomb it exists to catch.

Nothing in this module weakens a sibling's assertion: the two tests #506 names
(``test_budget_guard.py``, ``test_negative_controls.py``) are untouched by this
lane and must keep passing exactly as they stand.

This module is itself runnable under a *globally* frozen clock (the class's own
probe: ``AO_FROZEN_CLOCK=<instant> python3 -m pytest telemetry/...``).  The two
halves that assert the clock is live while unfrozen skip **visibly** in that
mode rather than being weakened — a skip is reported, a softened assertion is
not.
"""

from __future__ import annotations

import ast
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Tuple

import pytest

from telemetry import clock

REPO_ROOT = Path(__file__).resolve().parents[3]

#: Two pinned instants, both literals, a different year/month/day apart, both in
#: the past — a past literal can never be "reached" by waiting, which is why the
#: #506 repair dates its fixtures the same way.
PINNED = "2025-03-04T05:06:07Z"
MOVED = "2025-11-12T13:14:15Z"

#: Every helper that read the clock itself before #1025, with the shape it
#: answers in.  ``clock``'s own four are here too: the seam must satisfy the
#: same contract it hands out.
MONEY_PATH: Tuple[Tuple[str, str, str], ...] = (
    ("telemetry.clock", "now_utc_iso", "iso"),
    ("telemetry.clock", "now_epoch", "epoch"),
    ("telemetry.clock", "today_utc", "day"),
    ("telemetry.clock", "this_month_utc", "month"),
    ("telemetry.metering.model", "now_utc_iso", "iso"),
    ("telemetry.budgets.model", "now_utc_iso", "iso"),
    ("telemetry.budgets.model", "today_utc", "day"),
    ("telemetry.budgets.model", "this_month_utc", "month"),
    ("telemetry.budgets.chargeback", "_now_utc", "iso"),
    ("telemetry.ledger.schema", "now_utc", "iso"),
    ("telemetry.observability.model", "now_utc_iso", "iso"),
    ("telemetry.observability.dashboard", "_now_iso", "iso"),
    ("telemetry.role_health", "_now_epoch", "epoch"),
    ("telemetry.role_health", "_now_iso", "iso"),
)

#: The modules that carried a copy of the seam.  Asserted so the scan below
#: cannot pass by scanning an empty set.
DEFERRED_MODULES = (
    "telemetry/metering/model.py",
    "telemetry/budgets/model.py",
    "telemetry/budgets/chargeback.py",
    "telemetry/ledger/schema.py",
    "telemetry/observability/model.py",
    "telemetry/observability/dashboard.py",
    "telemetry/role_health.py",
)

#: A raw wall-clock read, by the call shape it takes.  Closed on purpose: a
#: dotted name that is not on this list is not a clock read.
RAW_READS = frozenset(
    {
        "datetime.now",
        "datetime.utcnow",
        "date.today",
        "time.time",
        "time.time_ns",
    }
)

#: The pre-#1025 shape, restored in a scratch copy to prove the proof can fail.
PRE_SEAM_READ = '''


def now_utc_iso() -> str:
    """The pre-#1025 shape: this module's own read of the live clock."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
'''


def expected(kind: str, instant: str) -> object:
    """What a helper answering in ``kind`` must return for ``instant``."""
    if kind == "iso":
        return instant
    if kind == "epoch":
        return datetime.fromisoformat(instant.replace("Z", "+00:00")).timestamp()
    if kind == "day":
        return instant[:10]
    if kind == "month":
        return instant[:7]
    raise AssertionError(f"unknown shape {kind!r}")


def dotted_name(node: ast.AST) -> str:
    """The dotted name a call is made through, or ``""`` if it is not one."""
    parts: List[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return ""


def raw_clock_reads(path: Path) -> List[Tuple[int, str]]:
    """Every raw wall-clock read in a module, as ``(line, dotted name)``.

    Parsed, not grepped: prose in a docstring must not read as a clock read, and
    a read must be a *call* the module makes rather than a name it passes on
    (``self._clock = clock or time.time`` on the latency surfaces).
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return sorted(
        (node.lineno, dotted_name(node.func))
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and dotted_name(node.func) in RAW_READS
    )


def pillar_modules() -> List[Path]:
    """The pillar's non-test Python modules — the rule's own jurisdiction."""
    return [
        path
        for path in sorted((REPO_ROOT / "telemetry").rglob("*.py"))
        if not path.name.startswith("test_") and path.name != "conftest.py"
    ]


def helper(module_name: str, attr: str):
    """Resolve one helper by name (one import failure = one failing case)."""
    import importlib

    return getattr(importlib.import_module(module_name), attr)


# --------------------------------------------------------------------------- #
# The freeze reaches every helper
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(("module_name", "attr", "kind"), MONEY_PATH)
def test_a_pinned_instant_reaches_every_money_path_helper(module_name, attr, kind):
    """Held behind the seam, every helper answers the pinned instant exactly."""
    with clock.frozen(PINNED):
        assert clock.is_frozen() is True
        assert helper(module_name, attr)() == expected(kind, PINNED), (
            f"{module_name}.{attr}() ignored the pinned clock — it is reading the "
            f"live one, which is what made the #506 green expire with the calendar"
        )


@pytest.mark.parametrize(("module_name", "attr", "kind"), MONEY_PATH)
def test_the_frozen_answer_moves_with_the_pinned_instant(module_name, attr, kind):
    """The same helper, a different instant: the pair is the behavioural proof.

    A module that kept its own ``datetime.now()`` returns the same (live) value
    under both halves after the first one fails; a module that asks the seam
    returns each instant in turn.
    """
    with clock.frozen(MOVED):
        assert helper(module_name, attr)() == expected(kind, MOVED)


@pytest.mark.skipif(
    clock.is_frozen(),
    reason="needs a live clock: this half is about the clock being live",
)
def test_the_pinned_instants_are_not_the_live_clock():
    """The literals above are past days, so the freeze is what changed the answer.

    Asserted against the *live* reading rather than against a second literal, so
    this half cannot expire either.  Skipped — visibly, never silently — under a
    run that has frozen the clock for every suite.
    """
    assert not clock.is_frozen()
    live = clock.now_utc_iso()
    assert live != PINNED and live != MOVED
    assert live[:10] != PINNED[:10] and live[:10] != MOVED[:10]


@pytest.mark.skipif(
    clock.is_frozen(),
    reason="needs a live clock: this half is about the clock being live",
)
def test_an_unfrozen_read_is_still_the_live_clock():
    """Public behaviour is unchanged: with no freeze, the clock is the clock."""
    live = datetime.fromisoformat(clock.now_utc_iso().replace("Z", "+00:00"))
    elapsed = abs(datetime.now(live.tzinfo).timestamp() - live.timestamp())
    assert elapsed < 60, "an unfrozen read drifted from the live clock"


def test_a_freeze_is_restored_and_nests():
    """A freeze ends with its block, and an outer one survives an inner one."""
    ambient = os.environ.get(clock.FROZEN_ENV)
    with clock.frozen(PINNED):
        with clock.frozen(MOVED):
            assert clock.now_utc_iso() == MOVED
        assert clock.now_utc_iso() == PINNED, "the outer freeze must survive"
    assert os.environ.get(clock.FROZEN_ENV) == ambient, "the freeze was not restored"
    assert clock.is_frozen() is (ambient is not None)


def test_a_malformed_freeze_is_refused_not_ignored(monkeypatch):
    """A freeze that cannot be understood raises — it never reverts to live."""
    ambient = os.environ.get(clock.FROZEN_ENV)
    monkeypatch.setenv(clock.FROZEN_ENV, "yesterday-ish")
    with pytest.raises(ValueError, match="is not an ISO 8601 instant"):
        clock.now_utc_iso()
    monkeypatch.undo()
    assert os.environ.get(clock.FROZEN_ENV) == ambient


def test_frozen_refuses_a_naive_datetime():
    """A freeze with no timezone is ambiguous, so it is refused."""
    with pytest.raises(ValueError, match="needs an aware datetime"):
        with clock.frozen(datetime(2025, 3, 4, 5, 6, 7)):
            pass  # pragma: no cover  (frozen() raises on entry)


# --------------------------------------------------------------------------- #
# The mutant: a module that reads the clock itself escapes the freeze
# --------------------------------------------------------------------------- #
def probe_source() -> str:
    """A self-contained probe over a given import root (the mutant's judge)."""
    rows = ",\n".join(
        f"    ({module!r}, {attr!r}, {expected(kind, PINNED)!r})"
        for module, attr, kind in MONEY_PATH
    )
    return f'''"""Answer every money-path helper and require the pinned instant."""
import importlib
import sys

EXPECTED = [
{rows},
]


def main() -> int:
    sys.path.insert(0, sys.argv[1])
    for module_name, attr, want in EXPECTED:
        got = getattr(importlib.import_module(module_name), attr)()
        if got != want:
            print(
                f"MISMATCH {{module_name}}.{{attr}}() = {{got!r}}, pinned was {{want!r}}"
                " — this module reads the live clock instead of the seam"
            )
            return 1
    print(f"seam holds across {{len(EXPECTED)}} helper(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''


@pytest.fixture()
def scratch_root(tmp_path):
    """A self-contained copy of the two pillars the money path imports."""
    root = tmp_path / "tree"
    root.mkdir()
    for pillar in ("telemetry", "gateway"):
        shutil.copytree(
            REPO_ROOT / pillar,
            root / pillar,
            ignore=shutil.ignore_patterns("__pycache__"),
        )
    return root


def run_probe(root: Path, tmp_path: Path):
    """Run the probe against ``root`` with the clock pinned."""
    script = tmp_path / "probe.py"
    script.write_text(probe_source(), encoding="utf-8")
    env = dict(os.environ)
    env[clock.FROZEN_ENV] = PINNED
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [sys.executable, str(script), str(root)],
        capture_output=True,
        text=True,
        cwd=str(tmp_path),
        env=env,
        check=False,
    )


def test_the_scratch_copy_is_held_by_the_seam(scratch_root, tmp_path):
    """The control half: unmutated, the probe is green — a red mutant means more."""
    result = run_probe(scratch_root, tmp_path)
    assert result.returncode == 0, (
        f"the probe failed on the unmutated copy, so it cannot judge a mutant:\n"
        f"{result.stdout}{result.stderr}"
    )
    assert "seam holds" in result.stdout


def test_a_module_reading_the_clock_itself_escapes_the_freeze(scratch_root, tmp_path):
    """The pre-#1025 shape restored in one module must be caught, by name.

    This is the mutation the seam exists to make impossible: restore
    ``metering/model.py``'s own ``now_utc_iso`` and the pinned clock stops
    reaching it, so the probe goes red naming the module that escaped.
    """
    target = scratch_root / "telemetry" / "metering" / "model.py"
    before = target.read_text(encoding="utf-8")
    target.write_text(before + PRE_SEAM_READ, encoding="utf-8")
    assert target.read_text(encoding="utf-8") != before, "the mutation did not land"

    result = run_probe(scratch_root, tmp_path)
    assert result.returncode != 0, (
        "the mutant read the live clock and the probe still passed — the freeze "
        "is a formality"
    )
    assert "MISMATCH telemetry.metering.model.now_utc_iso" in result.stdout
    assert "reads the live clock instead of the seam" in result.stdout


# --------------------------------------------------------------------------- #
# No module in this pillar reads the wall clock directly
# --------------------------------------------------------------------------- #
def test_the_pillars_money_path_modules_are_all_under_the_rule():
    """The rule's jurisdiction is asserted, so a rule over nothing cannot pass."""
    scanned = {path.relative_to(REPO_ROOT).as_posix() for path in pillar_modules()}
    missing = sorted(set(DEFERRED_MODULES) - scanned)
    assert not missing, f"the rule does not cover {missing}"
    assert len(scanned) > len(DEFERRED_MODULES), "the scan is suspiciously narrow"


def test_no_module_reads_the_wall_clock_directly():
    """One seam, and it is ``telemetry/clock.py`` — everything else delegates."""
    offenders = []
    for path in pillar_modules():
        if path.name == "clock.py":
            continue  # the seam itself: the one legitimate read
        for line, name in raw_clock_reads(path):
            offenders.append(f"{path.relative_to(REPO_ROOT).as_posix()}:{line} {name}()")
    assert not offenders, (
        "these modules read the wall clock instead of telemetry.clock, so a test "
        "can no longer pin them (issue #1025):\n  " + "\n  ".join(offenders)
    )


def test_the_rule_can_fail_and_names_what_it_refuses(tmp_path):
    """Both halves of the rule, on planted files outside the repository.

    A rule that finds nothing is not a gate (GR-12), and a rule that refuses
    everything is not one either.
    """
    planted = tmp_path / "planted.py"
    planted.write_text(
        "import time\n"
        "from datetime import UTC, date, datetime\n"
        "\n"
        "\n"
        "def stamp() -> str:\n"
        "    return datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')\n"
        "\n"
        "\n"
        "def other() -> float:\n"
        "    return time.time()\n"
        "\n"
        "\n"
        "def today() -> str:\n"
        "    return date.today().isoformat()\n"
        "\n"
        "\n"
        "def duration() -> float:\n"
        "    return time.monotonic()\n"
        "\n"
        "\n"
        "def delegated() -> str:\n"
        "    from telemetry.clock import now_utc_iso\n"
        "\n"
        "    return now_utc_iso()\n",
        encoding="utf-8",
    )
    assert raw_clock_reads(planted) == [
        (6, "datetime.now"),
        (10, "time.time"),
        (14, "date.today"),
    ], "a duration clock (time.monotonic) and a delegated read are not refusals"

    clean = tmp_path / "clean.py"
    clean.write_text(
        '"""A docstring that says datetime.now(UTC) and time.time() out loud."""\n'
        "from datetime import datetime\n"
        "\n"
        "\n"
        "def parsed(value: str) -> float:\n"
        "    return datetime.fromtimestamp(float(value)).timestamp()\n",
        encoding="utf-8",
    )
    assert raw_clock_reads(clean) == [], "prose and a parse are not clock reads"


# --------------------------------------------------------------------------- #
# Both import roots reach the same seam
# --------------------------------------------------------------------------- #
def test_the_ledger_package_follows_the_seam_under_its_flat_import_root(tmp_path):
    """``telemetry/`` on ``sys.path`` — the root where ``telemetry.clock`` is absent.

    Measured here rather than assumed: the flat root cannot import
    ``telemetry.clock``, so the ledger module reaches the seam by a second name,
    and that name must obey the same freeze.
    """
    script = tmp_path / "flat_probe.py"
    script.write_text(
        "import os\n"
        "import sys\n"
        "\n"
        "root = os.path.abspath(sys.argv[1])\n"
        "sys.path = [p for p in sys.path if p not in ('', os.getcwd(), root)]\n"
        "sys.path.append(os.path.join(root, 'telemetry'))\n"
        "\n"
        "import ledger  # noqa: E402  (flat: `telemetry/` is on sys.path)\n"
        "\n"
        "assert sys.modules['ledger.schema'].__name__ == 'ledger.schema'\n"
        "assert 'telemetry.clock' not in sys.modules, 'the flat rig is not flat'\n"
        "seam = sys.modules['clock']\n"
        "assert seam.is_frozen(), 'the flat seam did not see the freeze'\n"
        "assert ledger.now_utc() == os.environ['AO_FROZEN_CLOCK'], ledger.now_utc()\n"
        "print('flat root follows the seam')\n",
        encoding="utf-8",
    )
    env = dict(os.environ)
    env[clock.FROZEN_ENV] = PINNED
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(
        [sys.executable, str(script), str(REPO_ROOT)],
        capture_output=True,
        text=True,
        cwd=str(tmp_path),
        env=env,
        check=False,
    )
    assert result.returncode == 0, f"{result.stdout}{result.stderr}"
    assert "flat root follows the seam" in result.stdout
