"""Worktree provisioning — one lane, one branch, one signature.

The hard part of lane isolation is not creating a worktree; it is making the
*signature* lane-local. ``git config user.email`` run inside a linked worktree
writes to the **shared** repository config, so two lanes on one machine would
silently overwrite each other's identity and every commit would be attributable
to whoever ran last.

This module therefore writes the session's identity with ``git config
--worktree`` (per-worktree ``config.worktree``, which git keeps beside the
worktree's own git dir) after enabling ``extensions.worktreeConfig`` on the
repository. The shared config is never the place a lane stores who it is.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .identity import SessionIdentity, worktree_name_for

RECORD_DIR = ".fleet/lanes"

#: A worktree root inside the checkout would put lanes back in the shared tree.
REFUSED_ROOTS = (".git",)

#: Filesystems a lane may not be provisioned onto. ``/tmp`` is a tmpfs on this
#: fleet's machines: it costs RAM and inodes instead of disk, and it is **lost
#: on reboot**, so a lane parked there silently evaporates (issue #516).
REFUSED_FILESYSTEMS = ("tmpfs", "ramfs")

#: Where the mount table is read from. A parameter of the check, never an
#: environment variable: turning the rule off must be a code-level decision.
DEFAULT_MOUNTS = "/proc/self/mounts"

# Refusal codes. Every provisioning refusal names itself, so a finding can be
# grepped for and a reader of the README's refusal table can match prose to
# cause. ``lane-worktree-on-tmpfs`` is the one issue #516 adds.
REFUSAL_SHARED_CHECKOUT = "lane-worktree-is-the-shared-checkout"
REFUSAL_INSIDE_CHECKOUT = "lane-worktree-inside-the-shared-checkout"
REFUSAL_EXISTS = "lane-path-exists-but-is-not-a-worktree"
REFUSAL_BASE_UNRESOLVED = "lane-base-ref-unresolved"
REFUSAL_ADD_FAILED = "git-worktree-add-failed"
REFUSAL_UNSAFE_NAME = "unsafe-lane-name"
REFUSAL_TMPFS = "lane-worktree-on-tmpfs"


class ProvisionRefused(RuntimeError):
    """The lane cannot be provisioned safely; nothing was created."""


@dataclass(frozen=True)
class Provision:
    """The outcome of provisioning one lane."""

    identity: SessionIdentity
    created: bool
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def git(cwd: Path | str, *args: str) -> subprocess.CompletedProcess:
    """Run git without ever touching the developer's global config."""
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_TERMINAL_PROMPT": "0"},
    )


def default_worktree_root() -> Path:
    """Where lanes live: ``$AO_WORKTREE_ROOT``, else ``~/ao-worktrees``."""
    return Path(os.environ.get("AO_WORKTREE_ROOT") or (Path.home() / "ao-worktrees"))


def main_repo_root(start: Path | str) -> Path:
    """The repository a path belongs to (the *main* checkout, not a lane)."""
    result = git(start, "rev-parse", "--show-toplevel")
    if result.returncode != 0:
        raise ProvisionRefused(f"{start} is not inside a git repository: {result.stderr.strip()[-200:]}")
    return Path(result.stdout.strip())


def is_linked_worktree(path: Path | str) -> bool:
    """True when ``path`` is a linked worktree rather than the main checkout."""
    git_dir = git(path, "rev-parse", "--git-dir")
    if git_dir.returncode != 0:
        return False
    return "worktrees" in Path(git_dir.stdout.strip()).parts


def enable_worktree_config(main: Path | str) -> None:
    """Allow per-worktree config, idempotently.

    Additive and safe: without it every lane that sets an identity would be
    writing into the one config every other lane reads.
    """
    git(main, "config", "extensions.worktreeConfig", "true")


def _guard_paths(main: Path, worktree: Path) -> None:
    if worktree.resolve() == main.resolve():
        raise ProvisionRefused(f"{REFUSAL_SHARED_CHECKOUT}: the lane worktree path is the shared checkout itself")
    try:
        worktree.resolve().relative_to(main.resolve())
    except ValueError:
        return
    raise ProvisionRefused(
        f"{REFUSAL_INSIDE_CHECKOUT}: lane worktree {worktree} sits inside the shared checkout {main}; "
        "it would not be isolated"
    )


def _unescape_mount_point(field: str) -> str:
    """Undo the octal escaping the mount table applies to spaces and tabs."""
    return re.sub(r"\\([0-7]{3})", lambda match: chr(int(match.group(1), 8)), field)


def filesystem_type(path: Path | str, mounts: Path | str | None = None) -> str:
    """The filesystem ``path`` lives on, per the mount table (``""`` if unknown).

    A worktree root that does not exist yet is classified by the deepest
    *existing* ancestor, because that is the filesystem ``git worktree add``
    would create it on. ``mounts`` is a parameter rather than an environment
    variable so the rule can be proved offline, against a declared table,
    instead of whatever the running machine happens to mount.
    """
    probe = Path(path)
    try:
        probe = probe.resolve()
    except OSError:
        return ""
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    try:
        table = Path(mounts if mounts is not None else DEFAULT_MOUNTS).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    best, best_depth = "", -1
    for line in table.splitlines():
        fields = line.split()
        if len(fields) < 3:
            continue
        mount_point = Path(_unescape_mount_point(fields[1]))
        try:
            probe.relative_to(mount_point)
        except ValueError:
            continue
        depth = len(mount_point.parts)
        if depth > best_depth:
            best, best_depth = fields[2].lower(), depth
    return best


def tmpfs_lane_root(path: Path | str, mounts: Path | str | None = None) -> str:
    """The refusal message when the lane root is RAM-backed, else ``""``."""
    filesystem = filesystem_type(path, mounts)
    if filesystem not in REFUSED_FILESYSTEMS:
        return ""
    return (
        f"{REFUSAL_TMPFS}: lane worktree root {path} is on {filesystem} — RAM and inodes rather than "
        "disk, and lost on reboot; pass a root on a persistent filesystem (or allow_tmpfs=True for "
        "throwaway scratch that cannot outlive the run)"
    )


def resolve_base(main: Path | str, base: str) -> str:
    """Verify the base ref exists. A lane based on nothing is not isolated."""
    result = git(main, "rev-parse", "--verify", f"{base}^{{commit}}")
    if result.returncode != 0:
        raise ProvisionRefused(
            f"{REFUSAL_BASE_UNRESOLVED}: base ref {base!r} does not resolve in {main}: "
            f"{result.stderr.strip()[-200:]} (fetch the remote, or pass an explicit base)"
        )
    return result.stdout.strip()


def branch_exists(main: Path | str, branch: str) -> bool:
    return git(main, "rev-parse", "--verify", f"refs/heads/{branch}").returncode == 0


def stamp_identity(worktree: Path | str, identity: SessionIdentity) -> None:
    """Write the session signature into the worktree's *own* config."""
    git(worktree, "config", "--worktree", "user.name", identity.author_name)
    git(worktree, "config", "--worktree", "user.email", identity.author_email)


def read_stamped_identity(worktree: Path | str) -> tuple[str, str] | None:
    """The worktree-scoped signature, or None when it is not worktree-scoped.

    Returns None (rather than the inherited value) when the repository does not
    allow per-worktree config, because an inherited value is exactly the failure
    this module exists to prevent.
    """
    name = git(worktree, "config", "--worktree", "--get", "user.name")
    email = git(worktree, "config", "--worktree", "--get", "user.email")
    if name.returncode != 0 or email.returncode != 0:
        return None
    return name.stdout.strip(), email.stdout.strip()


def shared_identity(main: Path | str) -> tuple[str, str] | None:
    """The repository-level signature every lane would inherit."""
    name = git(main, "config", "--local", "--get", "user.name")
    email = git(main, "config", "--local", "--get", "user.email")
    if name.returncode != 0 or email.returncode != 0:
        return None
    return name.stdout.strip(), email.stdout.strip()


def provision(
    identity: SessionIdentity,
    main: Path | str,
    base: str = "origin/master",
    fetch_remote: str = "",
    *,
    allow_tmpfs: bool = False,
    mounts: Path | str | None = None,
) -> Provision:
    """Create the lane: worktree on the issue branch, identity stamped inside.

    Idempotent — re-provisioning an existing lane re-stamps its identity and
    returns ``created=False`` instead of forking a second worktree.

    Two guards run **before** anything is created: the worktree may not sit in
    the shared checkout, and — unless the caller explicitly accepts throwaway
    scratch — it may not sit on a RAM-backed filesystem such as ``/tmp``
    (``lane-worktree-on-tmpfs``, issue #516). ``mounts`` is the mount table the
    filesystem check reads, so the refusal is provable offline.
    """
    main_root = main_repo_root(main)
    _guard_paths(main_root, identity.worktree)
    if not allow_tmpfs:
        refusal = tmpfs_lane_root(identity.worktree, mounts)
        if refusal:
            raise ProvisionRefused(refusal)

    if fetch_remote:
        git(main_root, "fetch", fetch_remote, "master")

    if identity.worktree.exists():
        if not is_linked_worktree(identity.worktree):
            raise ProvisionRefused(f"{REFUSAL_EXISTS}: {identity.worktree} exists but is not a git worktree")
        enable_worktree_config(main_root)
        stamp_identity(identity.worktree, identity)
        return Provision(identity, created=False)

    resolve_base(main_root, base)
    identity.worktree.parent.mkdir(parents=True, exist_ok=True)

    if branch_exists(main_root, identity.branch):
        # The branch is the lane's identity: reattach rather than fork a second
        # branch for the same issue (one issue = one branch).
        add = git(main_root, "worktree", "add", str(identity.worktree), identity.branch)
    else:
        add = git(main_root, "worktree", "add", "-b", identity.branch, str(identity.worktree), base)
    if add.returncode != 0:
        raise ProvisionRefused(f"{REFUSAL_ADD_FAILED}: git worktree add failed: {add.stderr.strip()[-300:]}")

    enable_worktree_config(main_root)
    stamp_identity(identity.worktree, identity)
    return Provision(identity, created=True)


def record_dir(main: Path | str) -> Path:
    return Path(main) / RECORD_DIR


def write_record(identity: SessionIdentity, main: Path | str) -> Path:
    """Persist the lane record so a later audit knows what to check against."""
    directory = record_dir(main)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{identity.session_id}.json"
    path.write_text(json.dumps(identity.to_json(), indent=2) + "\n", encoding="utf-8")
    return path


def read_record(session_id: str, main: Path | str) -> SessionIdentity | None:
    path = record_dir(main) / f"{session_id}.json"
    if not path.exists():
        return None
    return SessionIdentity.from_json(json.loads(path.read_text(encoding="utf-8")))


def list_records(main: Path | str) -> list[SessionIdentity]:
    directory = record_dir(main)
    if not directory.exists():
        return []
    identities = []
    for path in sorted(directory.glob("*.json")):
        try:
            identities.append(SessionIdentity.from_json(json.loads(path.read_text(encoding="utf-8"))))
        except (OSError, ValueError, KeyError):
            continue
    return identities


def forget_record(session_id: str, main: Path | str) -> None:
    (record_dir(main) / f"{session_id}.json").unlink(missing_ok=True)


def dirty(path: Path | str) -> bool:
    """Uncommitted work — including untracked files. Never thrown away blindly."""
    result = git(path, "status", "--porcelain")
    return bool(result.stdout.strip())


def close(identity: SessionIdentity, main: Path | str, force: bool = False) -> list[str]:
    """Remove the lane's worktree, refusing to discard uncommitted work.

    Returns the list of reasons the lane was *kept*; empty means it was removed.
    """
    problems = []
    if identity.worktree.exists():
        if dirty(identity.worktree) and not force:
            return [f"worktree {identity.worktree} has uncommitted work; commit it or pass --force"]
        remove = git(main_repo_root(main), "worktree", "remove", str(identity.worktree))
        if remove.returncode != 0:
            problems.append(f"git worktree remove failed: {remove.stderr.strip()[-200:]} (pass --force to override)")
    if not problems:
        forget_record(identity.session_id, main)
    return problems


def lane_path(issue: int, session_id: str, root: Path | str | None = None) -> Path:
    """The path a lane would occupy, without minting it."""
    return (Path(root) if root is not None else default_worktree_root()) / worktree_name_for(issue, session_id)


def guard_lane_name(name: str) -> str:
    """Reject a lane name that is not a plain worktree directory name."""
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]*$", name):
        raise ProvisionRefused(f"{REFUSAL_UNSAFE_NAME}: unsafe lane name {name!r}")
    return name
