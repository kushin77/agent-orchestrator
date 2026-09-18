"""Loading the fleet console must not leave ``fleet/`` on ``sys.path`` (#968).

WHY this file exists: :func:`portal.server.fleet.load_fleet_console` executes
``fleet/console.py`` at an arbitrary path, and that module's body puts its own
directory on ``sys.path`` so its sibling imports (``channel``, ``runtime``)
resolve. The entry used to outlive the import, which re-points every *later*
bare import at ``fleet/``. On a checkout carrying ``fleet/telemetry.py`` that
made ``import telemetry.metering`` resolve to the fleet copy and die with
``ModuleNotFoundError: No module named 'telemetry.metering'; 'telemetry' is not
a package`` — long after the console had loaded, and only for whichever caller
happened to import ``telemetry`` first.

The failure is *latent by construction*: nothing in the console's own load path
imports ``telemetry``, so the defect is invisible until an unrelated caller does
it afterwards. The workaround it bred is visible in the tree —
``scripts/check-operator-terminal.sh`` carries an explicit "``fleet/`` goes on
the path only now, after ``portal.server.app`` (and therefore
``telemetry.metering``) is already imported and cached" ordering note. Ordering
the caller is not a fix; the loader is.

Two independent properties are pinned here, because either alone is escapable:

* the *invariant* — importing the console changes nothing but ``sys.modules``,
  so the ``fleet/`` entry is gone once ``load_fleet_console`` returns, and an
  entry a **caller** supplied is left alone (the fleet's own tests and the
  operator-terminal probe put it there deliberately);
* the *consequence* — the issue's own ``Verify:``, run in a **fresh
  interpreter** so the real, already-imported ``telemetry`` package in this
  process cannot mask the shadowing. A shadowed import can only be observed in
  a process that has not imported ``telemetry`` yet, which is exactly the
  latent case that bites in production.
"""

from __future__ import annotations

import contextlib
import json
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Iterator

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from portal.server.fleet import load_fleet_console  # noqa: E402

#: A console stand-in that mirrors the real one's module body exactly: it puts
#: its own directory on ``sys.path``, *unconditionally*, and then imports a
#: sibling through that entry — so the entry cannot be removed before the import
#: without breaking the console itself, and leaving it behind is the defect.
#:
#: The sibling's name is deliberately *unlike* any real module
#: (``ao968_console_runtime``, not ``runtime``). The real console imports
#: ``runtime`` bare, and in a full-suite run some other test module has already
#: imported the real ``fleet/runtime.py``, so that ``import`` would be answered
#: from the interpreter's module cache and could not tell us whether the borrowed
#: entry was live during the load at all. A name nothing else can supply makes
#: the import a real measurement.
_FIXTURE_CONSOLE = '''\
"""Fixture console: mirrors fleet/console.py's sys.path mutation (issue #968)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "fleet"))

import ao968_console_runtime  # noqa: E402  (served only by the borrowed entry)


def snapshot():
    return {"runtime": ao968_console_runtime.FLEET_DIR}
'''

#: The sibling the console's borrowed entry is there to resolve — see the note
#: above on why the name is unique to the fixture.
_FIXTURE_RUNTIME = '''\
"""Fixture sibling module, resolved only through the console's sys.path entry."""
FLEET_DIR = "fixture-fleet-dir"
'''

#: The shadower reported in #968: a module (not a package) named after the
#: top-level package, which wins the bare name once ``fleet/`` is on the path.
_FIXTURE_SHADOWER = '''\
"""Fixture shadower — the `fleet/telemetry.py` reported in #968."""
RUNS_LOG = "fixture-only"
'''

#: The driver, run in its own interpreter: it is the only place the shadowing
#: can be observed, because it has not imported ``telemetry`` before the console.
#: It reports by *file*, not by stdout, so a stray library print cannot be
#: mistaken for its verdict.
_DRIVER = '''\
import json
import sys
from pathlib import Path

fixture, repo, report_path = (Path(arg).resolve() for arg in sys.argv[1:4])
sys.path.insert(0, str(repo))
sys.path.insert(0, str(fixture))

from portal.server.fleet import load_fleet_console

result = {"leaked": None, "console_ok": None, "telemetry_file": None,
          "metering": None, "error": ""}
console = load_fleet_console(fixture)
result["leaked"] = str(fixture / "fleet") in sys.path
result["console_ok"] = console.snapshot() == {"runtime": "fixture-fleet-dir"}

import telemetry

result["telemetry_file"] = getattr(telemetry, "__file__", None) or None
try:
    import telemetry.metering

    result["metering"] = str(Path(telemetry.metering.__file__).resolve())
except Exception as exc:  # the failure IS the measurement
    result["error"] = f"{type(exc).__name__}: {exc}"

Path(report_path).write_text(json.dumps(result), encoding="utf-8")
'''


def _build_fixture(root: Path) -> Path:
    """A throwaway tree where ``fleet/`` shadows the top-level ``telemetry``."""
    (root / "fleet").mkdir(parents=True)
    (root / "telemetry" / "metering").mkdir(parents=True)
    (root / "fleet" / "console.py").write_text(_FIXTURE_CONSOLE, encoding="utf-8")
    (root / "fleet" / "ao968_console_runtime.py").write_text(
        _FIXTURE_RUNTIME, encoding="utf-8"
    )
    (root / "fleet" / "telemetry.py").write_text(_FIXTURE_SHADOWER, encoding="utf-8")
    (root / "telemetry" / "metering" / "__init__.py").write_text(
        '"""The real package the console must not shadow."""\n', encoding="utf-8"
    )
    return root


@contextlib.contextmanager
def _nothing_leaks_from_the_fixture() -> Iterator[None]:
    """Confine a fixture load to its own ``sys.path``, ``sys.modules`` and cache.

    The fixture console imports its sibling under a **bare** name, exactly as the
    real ``fleet/console.py`` does. Loading it in this process therefore parks
    ``sys.modules['ao968_console_runtime']`` — a stub — where nothing else will
    find it, and a differently-named fixture once parked ``sys.modules['runtime']``
    where the real console's own ``import runtime`` found it instead (measured:
    ``fleet/channel.py`` then did ``channel.FLEET_DIR / "inbox"`` against the
    fixture's string and raised ``TypeError``). The fixture is confined so that the
    shared module table is exactly as it was, whatever the fixture did to it.

    The loader's per-root module cache is deliberately left alone: the fixture
    root is a fresh ``tmp_path`` per test, so its cache entry is never reached
    again.
    """
    saved_path = list(sys.path)
    saved_modules = dict(sys.modules)
    try:
        yield
    finally:
        for name in [n for n in sys.modules if n not in saved_modules]:
            del sys.modules[name]
        sys.modules.update(saved_modules)
        sys.path[:] = saved_path


# --- the invariant -----------------------------------------------------------


def test_the_fleet_entry_is_returned_once_the_console_has_loaded(tmp_path):
    """``load_fleet_console`` must not outlive its own ``sys.path`` mutation."""
    fixture = _build_fixture(tmp_path / "fixture")
    entry = str(fixture / "fleet")
    before = list(sys.path)
    assert entry not in sys.path, "fixture precondition: caller supplied no entry"
    with _nothing_leaks_from_the_fixture():
        console = load_fleet_console(fixture)

        assert entry not in sys.path, (
            "the console left its own directory on sys.path — every later bare "
            "import now resolves against fleet/ (issue #968)"
        )
        # ...and the console still works: the entry was borrowed for the import
        # rather than removed before it, so its sibling resolved and its own
        # readers remain callable.
        assert console.snapshot() == {"runtime": "fixture-fleet-dir"}
        assert Path(console.__file__).resolve() == (fixture / "fleet" / "console.py")
    assert sys.path == before


def test_an_entry_a_caller_supplied_is_left_exactly_as_it_was(tmp_path):
    """Only the entries this import added are removed — never the caller's."""
    fixture = _build_fixture(tmp_path / "fixture")
    entry = str(fixture / "fleet")
    before = list(sys.path)
    assert entry not in before, "fixture precondition: the caller's entry is new"
    sys.path.insert(0, entry)
    try:
        with _nothing_leaks_from_the_fixture():
            load_fleet_console(fixture)

            assert sys.path.count(entry) == 1, (
                "the caller's own fleet/ entry was removed along with the console's"
            )
            assert sys.path[0] == entry, "the caller's entry lost its position"
    finally:
        sys.path[:] = before


# --- the consequence: the issue's own Verify: -------------------------------


def test_telemetry_metering_survives_the_console_loading_first(tmp_path):
    """#968's ``Verify:`` — the import must resolve to the package, in a fresh process.

    Run in a child interpreter on purpose: this process has already imported the
    real ``telemetry`` package (``conftest`` pulls in ``portal.server.app``), and
    a cached package would hide the shadowing the test exists to catch.
    """
    fixture = _build_fixture(tmp_path / "fixture")
    driver = tmp_path / "driver.py"
    report = tmp_path / "report.json"
    driver.write_text(textwrap.dedent(_DRIVER), encoding="utf-8")

    completed = subprocess.run(
        [sys.executable, str(driver), str(fixture), str(REPO_ROOT), str(report)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    where = (
        f"--- probe stdout ---\n{completed.stdout}"
        f"\n--- probe stderr ---\n{completed.stderr}"
    )
    assert completed.returncode == 0, (
        f"the probe interpreter failed (rc={completed.returncode}):\n{where}"
    )
    assert report.is_file(), f"the probe wrote no report:\n{where}"
    result = json.loads(report.read_text(encoding="utf-8"))

    assert result["leaked"] is False, f"fleet/ leaked onto sys.path\n{where}"
    assert result["console_ok"] is True, (
        f"the console no longer resolves its own siblings\n{where}"
    )
    assert result["error"] == "", (
        f"import telemetry.metering failed after the console loaded: "
        f"{result['error']}\n{where}"
    )
    shadower = str((fixture / "fleet" / "telemetry.py").resolve())
    assert result["telemetry_file"] != shadower, (
        f"`telemetry` resolved to the fleet shadower instead of the package\n{where}"
    )
    assert result["metering"] == str(
        (fixture / "telemetry" / "metering" / "__init__.py").resolve()
    ), f"`telemetry.metering` did not resolve to the package\n{where}"


# --- the console we must not have broken ------------------------------------


def test_the_real_console_still_loads_and_exposes_its_readers():
    """The fix must not cost the projection its source: the real console loads."""
    console = load_fleet_console(REPO_ROOT)

    assert Path(console.__file__).resolve() == (REPO_ROOT / "fleet" / "console.py")
    for reader in ("snapshot", "events_snapshot", "rung_specs", "read_json"):
        assert callable(getattr(console, reader)), f"console.{reader} went missing"
