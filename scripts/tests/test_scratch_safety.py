"""The scratch guard must refuse, by name, every defect that filled the tmpfs.

Issue #488: a single agent scratch driver doubled its log on every run
(`make verify >> "$L"` plus `tail -6 "$L" >> "$L"`) until the log reached
14.8 GB and `/tmp` -- a 16 GB tmpfs, i.e. RAM -- hit 100%. The knock-on did
more damage than the disk: `cp` wrote a 0-byte "backup" and restoring from that
backup truncated a source file to empty.

These tests pin the three refusals and both directions of each, so the guard
cannot pass vacuously and cannot over-fire:

  * a self-appending line is refused, a correct driver is not;
  * an oversize scratch file and a near-full filesystem are refused, a small
    file on an empty filesystem is not;
  * a copy that wrote 0 bytes over a non-empty source is refused and its
    0-byte artifact removed, a copy that landed is accepted.

The script's own `--self-test` runs the same controls inside the gate; this
suite is the independent, out-of-process check of the same contract.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "check-scratch-safety.sh"

# The incident's driver, verbatim, including the line that caused it.
INCIDENT_DRIVER = """#!/usr/bin/env bash
L=/tmp/ao412.makeverify.log
: > "$L"
env -C "$W" make verify >> "$L" 2>&1
tail -6 "$L" >> "$L"
"""

CORRECT_DRIVER = """#!/usr/bin/env bash
L=/tmp/ao412.makeverify.log
: > "$L"
env -C "$W" make verify > "$L" 2>&1
tail -6 "$L"
"""


def run(*args: str, env: dict[str, str] | None = None, timeout: int = 120):
    """Invoke the guard with a controlled environment (no inherited seams)."""
    base = dict(os.environ)
    for seam in ("SG_DF", "SG_ROOT", "SG_REPO", "SG_MAX_FILE_MB", "SG_MAX_PCT", "SG_FAIL_PCT"):
        base.pop(seam, None)
    if env:
        base.update(env)
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        cwd=REPO,
        capture_output=True,
        text=True,
        env=base,
        timeout=timeout,
    )


def write_driver(directory: Path, text: str, name: str = "driver.sh") -> Path:
    path = directory / name
    path.write_text(text, encoding="utf-8")
    return path


# --- 1. the self-appending log ---------------------------------------------

def test_self_append_is_refused_by_name_and_by_line(tmp_path):
    write_driver(tmp_path, INCIDENT_DRIVER)
    result = run("--lint", str(tmp_path))
    assert result.returncode == 1, result.stdout + result.stderr
    assert "FAIL  SCRATCH-SELF-APPEND" in result.stdout
    assert "driver.sh:5" in result.stdout, "the refusal must name the offending line"


def test_a_correct_driver_is_not_refused(tmp_path):
    write_driver(tmp_path, CORRECT_DRIVER)
    result = run("--lint", str(tmp_path))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "FAIL  SCRATCH-SELF-APPEND" not in result.stdout


def test_appending_to_a_different_file_is_not_a_self_append(tmp_path):
    write_driver(tmp_path, "#!/usr/bin/env bash\ntail -6 \"$L\" >> \"$OTHER\"\n")
    result = run("--lint", str(tmp_path))
    assert result.returncode == 0, result.stdout + result.stderr


# --- 2. the scratch root ----------------------------------------------------

def test_an_oversize_scratch_file_is_refused_by_name(tmp_path):
    big = tmp_path / "bigfile"
    big.write_bytes(b"\0" * (2 * 1024 * 1024))
    result = run("--scan", str(tmp_path), env={"SG_MAX_FILE_MB": "1"})
    assert result.returncode == 1
    assert "FAIL  SCRATCH-FILE-OVERSIZE" in result.stdout


def test_a_near_full_filesystem_is_refused_by_name(tmp_path):
    fixture = tmp_path / "df.full"
    fixture.write_text(
        "Filesystem 1024-blocks Used Available Capacity Mounted on\n"
        "tmpfs 16777216 16106127 671089 96% /tmp\n",
        encoding="utf-8",
    )
    result = run("--scan", str(tmp_path), env={"SG_DF": str(fixture)})
    assert result.returncode == 1
    assert "FAIL  SCRATCH-SPACE-NEAR-FULL" in result.stdout


def test_an_empty_filesystem_is_not_refused(tmp_path):
    fixture = tmp_path / "df.ok"
    fixture.write_text(
        "Filesystem 1024-blocks Used Available Capacity Mounted on\n"
        "tmpfs 16777216 1677722 15104894 10% /tmp\n",
        encoding="utf-8",
    )
    result = run("--scan", str(tmp_path), env={"SG_DF": str(fixture)})
    assert result.returncode == 0, result.stdout + result.stderr
    assert "FAIL  SCRATCH-SPACE-NEAR-FULL" not in result.stdout


def test_a_worktree_on_the_scratch_root_is_refused_by_name(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-qm", "seed", "--allow-empty"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "-q", "--detach", str(repo / "lane")],
        check=True, capture_output=True,
    )
    result = run("--scan", str(tmp_path), env={"SG_REPO": str(repo)})
    assert result.returncode == 1
    assert "FAIL  SCRATCH-TMP-WORKTREE" in result.stdout


# --- 3. the copy that never landed -----------------------------------------

def test_the_incident_post_state_is_refused_by_name(tmp_path):
    """A non-empty source and a 0-byte destination: the backup that truncated a file."""
    src = tmp_path / "src.bin"
    src.write_bytes(b"x" * 4096)
    dst = tmp_path / "backup.bin"
    dst.write_bytes(b"")
    result = run("--verify-copy", str(src), str(dst))
    assert result.returncode == 1
    assert "FAIL  SCRATCH-EMPTY-COPY" in result.stdout


def test_a_copy_that_cannot_land_is_refused_and_removed(tmp_path):
    """A real reproduction: a write-size limit leaves the 0-byte artifact behind.

    That is exactly the post-state a full tmpfs produced for the incident, so
    the guard must refuse it by name AND remove the artifact -- an empty backup
    that survives is the thing that later truncated a source file to empty.
    """
    src = tmp_path / "src.bin"
    src.write_bytes(b"x" * 4096)
    dst = tmp_path / "dst.bin"
    result = subprocess.run(
        [
            "bash", "-c",
            'ulimit -c 0; ulimit -f 0; exec "$0" --copy "$1" "$2"',
            str(SCRIPT), str(src), str(dst),
        ],
        cwd=REPO, capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "FAIL  SCRATCH-EMPTY-COPY" in result.stdout
    assert not dst.exists(), "the 0-byte destination must not survive to be restored from"


def test_a_copy_that_lands_is_accepted(tmp_path):
    src = tmp_path / "src.bin"
    src.write_bytes(b"x" * 4096)
    dst = tmp_path / "dst.bin"
    result = run("--copy", str(src), str(dst))
    assert result.returncode == 0, result.stdout + result.stderr
    assert dst.read_bytes() == src.read_bytes()


# --- 4. the gate itself -----------------------------------------------------

def test_the_gate_is_green_on_this_repo():
    result = run()
    assert result.returncode == 0, result.stdout + result.stderr
    assert "no SCRATCH-SELF-APPEND" in result.stdout
    assert "the live machine verdict above is advisory" in result.stdout


def test_cannot_assess_is_never_a_pass(tmp_path):
    missing = tmp_path / "no-such-root"
    assert run("--scan", str(missing)).returncode == 2
    assert run("--copy", str(tmp_path / "absent.bin"), str(tmp_path / "dst.bin")).returncode == 2


def test_the_gate_never_reddens_on_machine_state(tmp_path):
    """The live verdict is advisory by design; only a detector refusal fails.

    A gate that turns red because a neighbour filled the tmpfs reddens an
    unrelated diff. The machine-level guard owns that verdict (see
    docs/SCRATCH-SPACE-DISCIPLINE.md); the repo gate owns its own artifacts.
    """
    fixture = tmp_path / "df.full"
    fixture.write_text(
        "Filesystem 1024-blocks Used Available Capacity Mounted on\n"
        "tmpfs 16777216 16106127 671089 96% /tmp\n",
        encoding="utf-8",
    )
    result = run(env={"SG_DF": str(fixture)})
    assert result.returncode == 0, result.stdout + result.stderr
    assert "advisory" in result.stdout
    assert "note  SCRATCH-SPACE-NEAR-FULL" in result.stdout
