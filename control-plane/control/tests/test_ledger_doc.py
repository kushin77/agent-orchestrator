"""The canonical-ledger statement is held to the code that writes the rails.

Issue #1548 decided, by measurement, that `control-plane/control/` needs no
ledger of its own: `.board/dispatch-audit.jsonl` is the **dispatch-arbitration**
rail, and the **control-act** rail is `.fleet/slog.jsonl`, whose row shape
`fleet/channel.py::_slog()` really writes. That decision is only durable if the
declaration cannot drift from the writer, so this module reads the writer's keys
out of its source and refuses `README.md` the moment the table names a field the
writer does not produce, drops one it does, or stops naming a rail.

Four provocations run against scratch copies of the real files and must each be
refused **by name**. Every mutation is first proved to have taken: a mutator that
changed nothing would otherwise leave a provocation that proves nothing while
reporting a pass (GR-12).
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
README = ROOT / "control-plane" / "control" / "README.md"
REGISTRY = ROOT / "control-plane" / "control" / "verbs.yaml"
CHANNEL = ROOT / "fleet" / "channel.py"

#: The heading #1548 added, and the rails it must name.
SECTION = "Where an invocation is recorded"
RAILS = (".board/dispatch-audit.jsonl", ".fleet/slog.jsonl", "telemetry/ledger/")
#: The marker that keeps the `audit:` column honest: it names the act, it is not a key.
MARKER = "label, not a field"

_SLOG_DICT_KEY = re.compile(r'^\s*"([a-z_]+)":', re.M)
_TICKED = re.compile(r"`([^`]+)`")


def slog_row_keys() -> set[str]:
    """The keys `fleet/channel.py::_slog()` really writes, read from its source."""
    body = CHANNEL.read_text(encoding="utf-8").split("def _slog(", 1)[1]
    block = body.split("entry = {", 1)[1].split("}", 1)[0]
    return set(_SLOG_DICT_KEY.findall(block))


def section_text(readme: str) -> str:
    """The canonical-ledger section: from its heading to the next `## ` heading."""
    if SECTION not in readme:
        return ""
    return readme.split(SECTION, 1)[1].split("\n## ", 1)[0]


def slog_row_cell(readme: str) -> str:
    """The last table cell on the row that describes the control-act rail."""
    for line in readme.splitlines():
        if ".fleet/slog.jsonl" in line and line.lstrip().startswith("|"):
            return line.rstrip().rstrip("|").rsplit("|", 1)[-1]
    return ""


def findings(readme: str, registry: str, keys: set[str]) -> list[str]:
    """Every way the declaration fails to match the writer. Empty == OK."""
    out: list[str] = []
    section = section_text(readme)
    if not section:
        out.append(f"README.md has no `{SECTION}` section — the canonical ledger is unstated")
        return out

    for rail in RAILS:
        if rail not in readme:
            out.append(f"README.md does not name the canonical rail {rail!r}")

    cell = slog_row_cell(readme)
    if not cell:
        out.append("README.md has no table row for the `.fleet/slog.jsonl` rail")
    else:
        claimed = set(_TICKED.findall(cell))
        missing = sorted(keys - claimed)
        extra = sorted(claimed - keys)
        if missing:
            out.append(f"the `.fleet/slog.jsonl` row omits field(s) the writer produces: {missing}")
        if extra:
            out.append(f"the `.fleet/slog.jsonl` row claims field(s) the writer does not produce: {extra}")

    if MARKER not in section:
        out.append(f"README.md no longer declares that `audit:` is a {MARKER!r}")

    header = registry.split("schema:", 1)[0]
    if "control-plane/control/README.md" not in header or SECTION not in header:
        out.append("verbs.yaml's header does not point at the README's canonical-ledger section")
    return out


def _clean() -> tuple[str, str, set[str]]:
    return README.read_text(encoding="utf-8"), REGISTRY.read_text(encoding="utf-8"), slog_row_keys()


def _mutate(text: str, mutator) -> str:
    """Apply a mutator and PROVE it changed the text — else the provocation proves nothing."""
    after = mutator(text)
    assert after != text, "the mutator changed nothing — this provocation would pass vacuously"
    return after


# --- the real tree ----------------------------------------------------------

def test_the_real_tree_satisfies_the_statement():
    readme, registry, keys = _clean()
    assert findings(readme, registry, keys) == []


def test_the_reader_finds_the_writers_row_shape():
    """Anti-vacuity for `slog_row_keys`: an empty or wrong read must not read as a match."""
    keys = slog_row_keys()
    assert {"ts", "id", "body"} <= keys, f"the reader did not find the row shape it reads: {sorted(keys)}"


def test_the_files_the_statement_is_about_all_exist():
    for path in (README, REGISTRY, CHANNEL):
        assert path.exists(), f"{path} is missing — the statement would be unmeasured"


# --- the provocations -------------------------------------------------------

def test_a_readme_that_stops_naming_a_rail_is_refused():
    readme, registry, keys = _clean()
    assert findings(readme, registry, keys) == []
    for rail in RAILS:
        mutated = _mutate(readme, lambda text, r=rail: text.replace(r, "somewhere-else.jsonl"))
        problems = findings(mutated, registry, keys)
        assert any(rail in problem for problem in problems), problems


def test_a_row_that_claims_an_unwritten_field_is_refused():
    readme, registry, keys = _clean()
    mutated = _mutate(
        readme,
        lambda text: text.replace(
            "`issue`, `severity`, `body` |", "`issue`, `severity`, `body`, `action` |"
        ),
    )
    problems = findings(mutated, registry, keys)
    assert any("does not produce" in problem and "action" in problem for problem in problems), problems


def test_a_row_that_drops_a_written_field_is_refused():
    readme, registry, keys = _clean()
    mutated = _mutate(readme, lambda text: text.replace("`severity`, `body` |", "`severity` |"))
    problems = findings(mutated, registry, keys)
    assert any("omits" in problem and "body" in problem for problem in problems), problems


def test_a_section_that_loses_the_label_marker_is_refused():
    readme, registry, keys = _clean()
    mutated = _mutate(readme, lambda text: text.replace(MARKER, "a field"))
    problems = findings(mutated, registry, keys)
    assert any(MARKER in problem for problem in problems), problems


def test_a_registry_that_loses_the_pointer_is_refused():
    readme, registry, keys = _clean()
    mutated = _mutate(
        registry,
        lambda text: text.replace("control-plane/control/README.md", "some/other/place.md"),
    )
    problems = findings(readme, mutated, keys)
    assert any("verbs.yaml" in problem for problem in problems), problems
