#!/usr/bin/env python3
"""promote_portal.py — the fleet rung that keeps `ai.purebliss.app` in sync with
Artifact Registry (issue #1329, parent #1295).

WHY THIS EXISTS
    `contrib/shared-services/agentconsole.compose.yml` used to fall back to a
    HOST-BUILT image (`build:` from a non-git copy of the repo). Artifact
    Registry receives an immutable `portal:<sha>` on every master push
    (#1244), and nothing consumed them. This rung closes that gap: on a
    schedule, it reads the newest immutable AR tag whose commit is reachable
    from `origin/master`, compares it to the container that is actually
    running, and — if they differ — recreates the service from the immutable
    ref, health-gates the result, rolls back on failure, and records what it
    did.

DESIGN: THE JOB IS A PURE FUNCTION OF FOUR INJECTED FACTS
    1. the AR tag list,
    2. `origin/master` reachability (a predicate per commit sha),
    3. the running container's image ref, and
    4. the healthz answer after a recreate.
    :func:`run_cycle` takes all four as callables and returns a decision
    dict — no subprocess, no socket, no filesystem write inside it. Every
    test in `infra/fleet/tests/test_promote_portal.py` drives this function
    with fixtures. The only real transport (git, gcloud/docker, HTTP, the
    fleet channel, `.fleet/deploys.jsonl`, `scripts/gate-status.sh`) is wired
    up in :func:`main`.

TAG SELECTION
    "Newest" is never `latest` (mutable) and never a tag whose commit cannot
    be proven reachable from `origin/master` via
    `git merge-base --is-ancestor <sha> origin/master` — an unmerged sha is
    refused BY NAME (`tag-not-on-master`), never silently skipped. Among the
    remaining candidates, newest means highest commit position on
    `origin/master` (`git rev-list --count <sha>`), not registry push order,
    which a retag or a rebuild can reorder.

ROLLBACK AND THE PARK STATE
    A failed healthz check re-`up -d`s the previous running ref and escalates
    ONCE via `fleet/channel.py escalate` (the watchdog-bounded shape #1105/
    #830 established: raise it, then go quiet). "Once" is enforced by a park
    marker (`.fleet/portal-promote.park.json` by default): once a tag has
    been tried and rolled back, the rung parks on that exact tag and takes no
    further action — no repeat deploy attempt, no repeat escalation — until a
    NEWER tag appears in the registry.

AUTH
    A declared secret supplies read access to Artifact Registry: either
    `AO_FLEET_AR_READER_KEY_FILE` (a mounted service-account JSON key path) or
    `AO_FLEET_AR_ACCESS_TOKEN_CMD` (a command whose stdout is a bearer token).
    Neither is spelled out as a value anywhere in this module — both are
    PATHS/COMMANDS, resolved at run time. Absent, the rung refuses
    `ar-auth-missing` (CANNOT-ASSESS, rc 2) and escalates once; it never
    creates a credential (GR-6).

Tri-state exit contract (this repository's convention): 0 OK / 1 NOT-OK /
2 CANNOT-ASSESS.

Usage:
    python3 infra/fleet/promote_portal.py run [--apply]
    python3 infra/fleet/promote_portal.py run --dry-run   # print the decision, act on nothing
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

OK, NOT_OK, CANNOT_ASSESS = 0, 1, 2

ROOT = Path(__file__).resolve().parents[2]

#: The Artifact Registry repository the portal image is pushed to (#1244).
DEFAULT_AR_REPO = "us-central1-docker.pkg.dev/purebliss-ghl/ao-images/portal"

#: The compose file + service the rung recreates.
DEFAULT_COMPOSE_FILE = "contrib/shared-services/agentconsole.compose.yml"
DEFAULT_COMPOSE_SERVICE = "agentconsole"
DEFAULT_CONTAINER_NAME = "shared-services-agentconsole"

#: Where the deploy ledger and the park marker live (runtime state, gitignored).
DEFAULT_LEDGER_PATH = ROOT / ".fleet" / "deploys.jsonl"
DEFAULT_PARK_PATH = ROOT / ".fleet" / "portal-promote.park.json"

#: The mutable tag that must never be selected.
LATEST_TAG = "latest"

#: The fleet-channel role this rung escalates as, and the gate-status context
#: it posts (declared once, so a lane never needs to guess the literal).
ESCALATE_ROLE = "portal-promote"
GATE_STATUS_CONTEXT = "ao/deploy"

#: Healthz timeout budget (issue's own "poll ... for up to 60 s").
DEFAULT_HEALTHZ_TIMEOUT_SECONDS = 60


@dataclass(frozen=True)
class TagRef:
    """One Artifact Registry tag: its name and the commit sha it was built from."""

    tag: str
    sha: str

    @property
    def ref(self) -> str:
        return f"{DEFAULT_AR_REPO}:{self.tag}"


@dataclass
class Decision:
    """The outcome of tag selection — pure, no I/O."""

    action: str  # "select" | "refuse" | "cannot-assess"
    code: str
    detail: str = ""
    tag: TagRef | None = None


def select_newest_master_tag(
    tags: list[TagRef],
    is_ancestor: Callable[[str], bool],
    commit_index: Callable[[str], int],
) -> Decision:
    """The newest AR tag whose commit is reachable from `origin/master`.

    `latest` is excluded from consideration outright (never a candidate, not
    merely a loser). A candidate whose commit is NOT an ancestor of
    `origin/master` is excluded too, but if that leaves nothing to choose
    from the refusal names it explicitly (`tag-not-on-master`) rather than
    silently reporting "nothing to do".
    """
    candidates = [t for t in tags if t.tag != LATEST_TAG]
    if not candidates:
        return Decision("cannot-assess", "no-tags", detail="Artifact Registry listed no usable tag")
    ancestors = [t for t in candidates if is_ancestor(t.sha)]
    if not ancestors:
        names = ", ".join(sorted(t.tag for t in candidates))
        return Decision(
            "refuse",
            "tag-not-on-master",
            detail=f"no listed tag's commit is reachable from origin/master (checked: {names})",
        )
    best = max(ancestors, key=lambda t: commit_index(t.sha))
    return Decision("select", "ok", tag=best)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run_cycle(
    *,
    auth_ok: bool,
    list_tags: Callable[[], list[TagRef]],
    is_ancestor: Callable[[str], bool],
    commit_index: Callable[[str], int],
    running_ref: Callable[[], str | None],
    deploy: Callable[[str], None],
    healthz: Callable[[], bool],
    escalate: Callable[[str, str], None],
    record: Callable[[dict], None],
    post_status: Callable[[str, int], None],
    read_park: Callable[[], str | None],
    write_park: Callable[[str | None], None],
    now: Callable[[], str] = _now_iso,
) -> dict:
    """One promotion cycle — a pure orchestration over injected facts/effects.

    Every side-effecting parameter is a seam: tests pass fakes (no network,
    no subprocess, no filesystem), `main()` wires real transport into the
    same seams. Returns the decision it recorded, for a caller (or a test) to
    assert on directly, in addition to whatever `record`/`post_status` were
    given.
    """
    if not auth_ok:
        escalate(
            "ar-auth-missing",
            "no Artifact Registry credential is configured "
            "(AO_FLEET_AR_READER_KEY_FILE or AO_FLEET_AR_ACCESS_TOKEN_CMD)",
        )
        result = {"action": "cannot-assess", "code": "ar-auth-missing", "ts": now()}
        record(result)
        return result

    decision = select_newest_master_tag(list_tags(), is_ancestor, commit_index)
    if decision.action != "select":
        result = {"action": decision.action, "code": decision.code, "detail": decision.detail, "ts": now()}
        record(result)
        return result

    chosen = decision.tag
    assert chosen is not None

    parked = read_park()
    if parked == chosen.tag:
        result = {"action": "parked", "code": "parked-on-tag", "tag": chosen.tag, "ts": now()}
        record(result)
        return result

    current = running_ref()
    if current == chosen.ref:
        if parked is not None:
            write_park(None)
        result = {"action": "noop", "code": "up-to-date", "tag": chosen.tag, "ts": now()}
        record(result)
        return result

    previous = current
    deploy(chosen.ref)
    healthy = healthz()

    if healthy:
        write_park(None)
        result = {
            "action": "deployed",
            "sha": chosen.sha,
            "image": chosen.ref,
            "healthz": "ok",
            "previous": previous,
            "ts": now(),
        }
        record(result)
        post_status(chosen.sha, OK)
        return result

    # Rollback: re-`up -d` the previous ref, escalate ONCE, park on this tag.
    if previous:
        deploy(previous)
    write_park(chosen.tag)
    escalate(
        "healthz-failed",
        f"promotion to {chosen.ref} failed healthz; rolled back to {previous or '(no previous ref known)'}",
    )
    result = {
        "action": "rolled_back",
        "sha": chosen.sha,
        "image": chosen.ref,
        "healthz": "failed",
        "previous": previous,
        "rolled_back": True,
        "ts": now(),
    }
    record(result)
    post_status(chosen.sha, NOT_OK)
    return result


def rc_for(result: dict) -> int:
    """The process exit code for a `run_cycle` result — the repo tri-state."""
    action = result.get("action")
    if action in ("cannot-assess",):
        return CANNOT_ASSESS
    if action == "refuse":
        return CANNOT_ASSESS if result.get("code") == "no-tags" else NOT_OK
    if action == "rolled_back":
        return NOT_OK
    return OK


# ---------------------------------------------------------------------------
# Real transport — used ONLY by main(), never imported by the tests.
# ---------------------------------------------------------------------------


def _auth_ok(env: dict[str, str] | None = None) -> bool:
    source = os.environ if env is None else env
    key_file = source.get("AO_FLEET_AR_READER_KEY_FILE", "")
    token_cmd = source.get("AO_FLEET_AR_ACCESS_TOKEN_CMD", "")
    if key_file and Path(key_file).is_file():
        return True
    return bool(token_cmd.strip())


def _real_list_tags(repo: str) -> list[TagRef]:
    out = subprocess.run(
        ["gcloud", "artifacts", "docker", "tags", "list", repo, "--format=json"],
        capture_output=True, text=True, check=True, cwd=ROOT,
    )
    payload = json.loads(out.stdout or "[]")
    tags: list[TagRef] = []
    for entry in payload:
        tag = str(entry.get("tag", "")).rsplit("/", 1)[-1]
        digest = str(entry.get("version") or entry.get("digest") or "")
        sha = str(entry.get("sha") or entry.get("commit") or tag)
        if tag:
            tags.append(TagRef(tag=tag, sha=sha or digest))
    return tags


def _real_is_ancestor(sha: str) -> bool:
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", sha, "origin/master"], cwd=ROOT,
    )
    return result.returncode == 0


def _real_commit_index(sha: str) -> int:
    out = subprocess.run(
        ["git", "rev-list", "--count", sha], capture_output=True, text=True, cwd=ROOT,
    )
    try:
        return int((out.stdout or "0").strip())
    except ValueError:
        return 0


def _real_running_ref(container: str) -> str | None:
    out = subprocess.run(
        ["docker", "inspect", "--format", "{{.Config.Image}}", container],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        return None
    return (out.stdout or "").strip() or None


def _real_deploy(ref: str, compose_file: str, service: str) -> None:
    subprocess.run(["docker", "pull", ref], check=True, cwd=ROOT)
    env = dict(os.environ)
    env["AGENTCONSOLE_IMAGE"] = ref
    subprocess.run(
        ["docker", "compose", "-f", compose_file, "up", "-d", "--no-build", service],
        check=True, cwd=ROOT, env=env,
    )


def _real_healthz(url: str, timeout_seconds: float) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as response:  # noqa: S310
                if response.status == 200:
                    return True
        except OSError:
            pass
        time.sleep(2)
    return False


def _real_escalate(code: str, detail: str) -> None:
    subprocess.run(
        [
            "python3", str(ROOT / "fleet" / "channel.py"), "escalate",
            "--from", ESCALATE_ROLE, "--correlation", f"portal-promote-{code}",
            "--severity", "high", "--body", detail,
        ],
        cwd=ROOT,
    )


def _real_record(result: dict, ledger_path: Path) -> None:
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    with ledger_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(result, sort_keys=True) + "\n")


def _real_post_status(sha: str, rc: int) -> None:
    env = dict(os.environ)
    env["AO_GATE_CONTEXT"] = GATE_STATUS_CONTEXT
    subprocess.run(
        ["bash", str(ROOT / "scripts" / "gate-status.sh"), "post", "--sha", sha, "--rc", str(rc)],
        cwd=ROOT, env=env,
    )


def _real_read_park(park_path: Path) -> str | None:
    try:
        payload = json.loads(park_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload.get("tag") if isinstance(payload, dict) else None


def _real_write_park(tag: str | None, park_path: Path) -> None:
    if tag is None:
        park_path.unlink(missing_ok=True)
        return
    park_path.parent.mkdir(parents=True, exist_ok=True)
    park_path.write_text(json.dumps({"tag": tag, "ts": _now_iso()}), encoding="utf-8")


def cmd_run(args: argparse.Namespace) -> int:
    repo = args.ar_repo
    compose_file = args.compose_file
    service = args.compose_service
    container = args.container
    healthz_url = args.healthz_url
    ledger_path = Path(args.ledger)
    park_path = Path(args.park)

    if args.dry_run:
        def _noop_deploy(ref: str) -> None:
            print(f"promote-portal: DRY-RUN — would deploy {ref}")

        def _noop_healthz() -> bool:
            print("promote-portal: DRY-RUN — would poll healthz")
            return True

        def _noop_escalate(code: str, detail: str) -> None:
            print(f"promote-portal: DRY-RUN — would escalate {code}: {detail}")

        def _noop_post_status(sha: str, rc: int) -> None:
            print(f"promote-portal: DRY-RUN — would post {GATE_STATUS_CONTEXT}=rc{rc} for {sha}")

        deploy_fn, healthz_fn, escalate_fn, post_status_fn = (
            _noop_deploy, _noop_healthz, _noop_escalate, _noop_post_status,
        )
    else:
        deploy_fn = lambda ref: _real_deploy(ref, compose_file, service)  # noqa: E731
        healthz_fn = lambda: _real_healthz(healthz_url, args.healthz_timeout)  # noqa: E731
        escalate_fn = _real_escalate
        post_status_fn = _real_post_status

    result = run_cycle(
        auth_ok=_auth_ok(),
        list_tags=lambda: _real_list_tags(repo),
        is_ancestor=_real_is_ancestor,
        commit_index=_real_commit_index,
        running_ref=lambda: _real_running_ref(container),
        deploy=deploy_fn,
        healthz=healthz_fn,
        escalate=escalate_fn,
        record=lambda r: _real_record(r, ledger_path),
        post_status=post_status_fn,
        read_park=lambda: _real_read_park(park_path),
        write_park=lambda t: _real_write_park(t, park_path),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return rc_for(result)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="promote-portal", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run one promotion cycle")
    run.add_argument("--ar-repo", default=DEFAULT_AR_REPO)
    run.add_argument("--compose-file", default=DEFAULT_COMPOSE_FILE)
    run.add_argument("--compose-service", default=DEFAULT_COMPOSE_SERVICE)
    run.add_argument("--container", default=DEFAULT_CONTAINER_NAME)
    run.add_argument("--healthz-url", default=os.environ.get(
        "AO_FLEET_PORTAL_HEALTHZ_URL", "http://127.0.0.1:18286/api/healthz"
    ))
    run.add_argument("--healthz-timeout", type=float, default=DEFAULT_HEALTHZ_TIMEOUT_SECONDS)
    run.add_argument("--ledger", default=str(DEFAULT_LEDGER_PATH))
    run.add_argument("--park", default=str(DEFAULT_PARK_PATH))
    run.add_argument("--dry-run", action="store_true", help="decide, but pull/deploy/escalate/post nothing")
    run.set_defaults(func=cmd_run)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
