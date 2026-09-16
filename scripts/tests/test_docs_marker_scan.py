"""The unfinished-marker rule fires BOTH ways — and the guidance names a form it accepts.

Issue #804: the marker alternation in ``scripts/check-docs.sh`` ended in a
trailing word boundary with no leading one, so it matched the TAIL of any run of
three or more capital X — and ``mktemp`` requires a template ending in at least
three. A canonical ``mktemp -d /tmp/<name>.XXXXXX`` therefore failed
``docs-lint`` in every file the scanner reads, with a message that names a marker
rather than a placeholder.

These tests pin the contract OUT OF PROCESS, against the shipped scanner:

  * a canonical six-X template is ACCEPTED (the defect is fixed);
  * a real marker in each of its spellings is still REFUSED, BY NAME — a rule
    that simply stopped matching would pass the first test alone;
  * the scratch guidance names a form the gate recommends AND accepts (#804's
    item 3);
  * ``--markers`` reports CANNOT-ASSESS for a file that is not there, rather than
    answering "no markers" about a file it never read.

The marker spellings are assembled from fragments so that this file — itself one
of the files the rule reads — does not spell one literally.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCANNER = REPO / "scripts" / "check-docs.sh"
GUIDANCE = REPO / "docs" / "SCRATCH-SPACE-DISCIPLINE.md"

MARK_1 = "TO" "DO"
MARK_2 = "FIX" "ME"
MARK_3 = "HA" "CK"
MARK_TOKEN = "XX" "X"

# The scratch form the guidance recommends, as the gate must accept it.
CANONICAL_TEMPLATE = "mktemp -d /tmp/<name>.XXXXXX"


def run_markers(*files: Path) -> subprocess.CompletedProcess[str]:
    """Run the shipped scanner's marker rule over exactly these files."""
    return subprocess.run(
        ["bash", str(SCANNER), "--markers", *[str(f) for f in files]],
        cwd=REPO,
        capture_output=True,
        text=True,
    )


def test_a_canonical_template_is_accepted(tmp_path: Path) -> None:
    """The defect #804 measured: this fixture failed ``docs-lint`` before the fix."""
    fixture = tmp_path / "template.sh"
    fixture.write_text(
        'work="$(mktemp -d /tmp/cbp.XXXXXX)" || exit 2\n'
        f"# the template, spelled in a comment too: {CANONICAL_TEMPLATE}\n",
        encoding="utf-8",
    )
    result = run_markers(fixture)
    assert result.returncode == 0, result.stderr
    assert "unfinished markers: OK" in result.stdout
    assert str(fixture) not in result.stderr


def test_every_marker_spelling_is_still_refused_by_name(tmp_path: Path) -> None:
    """The other half: a rule that matches nothing would pass the test above."""
    fixtures = []
    for index, marker in enumerate((MARK_1, MARK_2, MARK_3, MARK_TOKEN)):
        fixture = tmp_path / f"marker-{index}.sh"
        fixture.write_text(f"# {marker}: planted by the test\n", encoding="utf-8")
        fixtures.append(fixture)
    result = run_markers(*fixtures)
    assert result.returncode == 1, (result.returncode, result.stdout, result.stderr)
    for fixture in fixtures:
        assert str(fixture) in result.stderr, result.stderr


def test_the_guidance_names_a_form_the_gate_accepts(tmp_path: Path) -> None:
    """#804's item 3: the convention must be a form the gate recommends AND passes."""
    guidance = GUIDANCE.read_text(encoding="utf-8")
    assert CANONICAL_TEMPLATE in guidance, (
        "docs/SCRATCH-SPACE-DISCIPLINE.md must name the scratch form the marker scan accepts"
    )
    fixture = tmp_path / "from-the-guidance.sh"
    fixture.write_text(f'dir="$({CANONICAL_TEMPLATE})" || exit 2\n', encoding="utf-8")
    assert run_markers(fixture).returncode == 0


def test_a_missing_file_is_cannot_assess_not_a_pass(tmp_path: Path) -> None:
    """A verdict about a file that was never read would be a false green."""
    result = run_markers(tmp_path / "not-there.sh")
    assert result.returncode == 2, (result.returncode, result.stdout, result.stderr)
    assert "CANNOT-ASSESS" in result.stderr
