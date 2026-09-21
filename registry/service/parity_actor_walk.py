#!/usr/bin/env python3
"""Walk the last N actor records of each kind and resolve every actor string.

---knowledge---
module_id: registry.service.parity_actor_walk
system: registry
app: service
solution_class: pattern
patterns: [read-only-walk, identity-parity, self-test]
derives_from: null
owner_sme: sync-sme
tier: L1
interfaces: [main, _self_test, _pr_authors, _dir_actor_field]
invariants: "every source is read only, and the walk proves one identity per actor across GitHub, the mailbox, paperclip and hermes"
gotchas: "the PR-author source needs gh plus network and auth, so the walk reports what it could not read rather than silently shrinking"
related: ["#1275"]
do_not_duplicate: null
---knowledge---

Issue #1275: one identity per actor across GitHub, the mailbox, paperclip and
hermes. Sources walked, each read-only:

- PR authors: ``gh pr list --state all --limit N`` (needs ``gh`` + network/auth).
- directives: files under ``.fleet/sent``.
- approvals: files under ``.fleet/approvals``.
- heartbeats: files under ``.fleet/heartbeats``.

A source that is simply absent on this checkout (no ``gh``, no ``.fleet/...``
directory) is reported CANNOT-ASSESS *by name* and does not fail the walk —
only an actor string that a present source actually names, and that does not
resolve, fails it (``actor-unresolved:<string>`` / ``delegation-undeclared:...``).

Exit codes: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS (identity module itself missing).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys


def _load_service(repo_root: str):
    # Python auto-prepends this script's own directory (registry/service/) to
    # sys.path, which contains a sibling module also named ``identity.py`` —
    # that shadows the top-level ``identity`` namespace package that
    # service/identity.py itself needs for ``identity.sso``. Drop it so the
    # real ``identity`` package (off repo_root) wins.
    script_dir = os.path.dirname(os.path.abspath(__file__))
    sys.path[:] = [p for p in sys.path if os.path.abspath(p or ".") != script_dir]

    # ``identity.sso`` (imported by service/identity.py) resolves as a
    # namespace package off the repo root, and ``service`` off registry/ —
    # both must be on sys.path.
    if repo_root not in sys.path:
        sys.path.append(repo_root)
    registry_root = os.path.join(repo_root, "registry")
    if registry_root not in sys.path:
        sys.path.append(registry_root)
    from service import ActorUnresolvedError, DelegationUndeclaredError, resolve_actor

    return resolve_actor, ActorUnresolvedError, DelegationUndeclaredError


def _pr_authors(n: int):
    """Last N PR authors via gh. Returns (list, note) — note set on skip."""
    try:
        out = subprocess.run(
            ["gh", "pr", "list", "--state", "all", "--limit", str(n), "--json", "author,number"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return [], f"gh unavailable ({exc})"
    if out.returncode != 0:
        return [], f"gh pr list failed: {out.stderr.strip()[:200]}"
    try:
        rows = json.loads(out.stdout or "[]")
    except json.JSONDecodeError as exc:
        return [], f"gh output not JSON ({exc})"
    actors = []
    for row in rows:
        login = (row.get("author") or {}).get("login")
        if login:
            actors.append((f"pr#{row.get('number')}", login))
    return actors, None


def _dir_actor_field(path: str, n: int, field_candidates=("actor", "sender", "signer", "source")):
    """Last N files under a .fleet/<kind> dir, each read for an actor field."""
    if not os.path.isdir(path):
        return [], f"{path} is missing"
    files = sorted(
        (os.path.join(path, f) for f in os.listdir(path)),
        key=lambda p: os.path.getmtime(p),
        reverse=True,
    )[:n]
    actors = []
    for f in files:
        try:
            with open(f, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue
        for field in field_candidates:
            if field in data:
                actors.append((f, data[field]))
                break
    return actors, None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=20)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    repo_root = os.path.abspath(args.repo_root)
    try:
        resolve_actor, ActorUnresolvedError, DelegationUndeclaredError = _load_service(repo_root)
    except ImportError as exc:
        print(f"actor-identity-parity: CANNOT-ASSESS — cannot import service.identity ({exc})")
        return 2

    if args.self_test:
        return _self_test(repo_root, resolve_actor, ActorUnresolvedError, DelegationUndeclaredError)

    sources = {
        "pr-authors": _pr_authors(args.n),
        "directives": _dir_actor_field(os.path.join(repo_root, ".fleet", "sent"), args.n),
        "approvals": _dir_actor_field(os.path.join(repo_root, ".fleet", "approvals"), args.n),
        "heartbeats": _dir_actor_field(os.path.join(repo_root, ".fleet", "heartbeats"), args.n),
    }

    failures = 0
    checked = 0
    for kind, (records, note) in sources.items():
        if note:
            print(f"actor-identity-parity: CANNOT-ASSESS — {kind} source absent ({note})")
            continue
        for origin, actor in records:
            checked += 1
            try:
                resolve_actor(actor)
                print(f"  OK    {kind:<12} {origin} -> {actor}")
            except (ActorUnresolvedError, DelegationUndeclaredError) as exc:
                print(f"  FAIL  {kind:<12} {origin} -> {exc}")
                failures += 1

    print(f"actor-identity-parity: checked {checked} actor record(s) across {len(sources)} kind(s), {failures} unresolved")
    return 1 if failures else 0


def _self_test(repo_root, resolve_actor, ActorUnresolvedError, DelegationUndeclaredError) -> int:
    import tempfile

    import yaml

    real_actors = os.path.join(repo_root, "registry", "service", "actors.yaml")
    with open(real_actors, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)

    failures = 0

    # 1: unknown actor string -> ActorUnresolvedError
    try:
        resolve_actor("definitely-not-declared-anywhere")
        print("  FAIL  unknown actor did not raise ActorUnresolvedError")
        failures += 1
    except ActorUnresolvedError as exc:
        if str(exc) == "actor-unresolved:definitely-not-declared-anywhere":
            print("  OK    unknown actor -> actor-unresolved:<string>")
        else:
            print(f"  FAIL  wrong message: {exc}")
            failures += 1

    # 2: undeclared delegation -> DelegationUndeclaredError
    mutated = dict(data)
    mutated["identities"] = dict(data["identities"])
    del mutated["identities"]["tf-runner-purebliss-api"]
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as fh:
        yaml.safe_dump(mutated, fh, sort_keys=False)
        scratch_path = fh.name
    try:
        # resolve_actor reads the module-default path; point it at the scratch
        # copy by calling with actors_path explicitly (public kwarg).
        resolve_actor(
            "cloud-build-purebliss-api@example.iam.gserviceaccount.com",
            actors_path=scratch_path,
        )
        print("  FAIL  undeclared delegation did not raise DelegationUndeclaredError")
        failures += 1
    except DelegationUndeclaredError:
        print("  OK    undeclared delegation -> delegation-undeclared:<actor>-><target>")
    finally:
        os.unlink(scratch_path)

    # 3: absent source -> CANNOT-ASSESS by name, not a failure
    records, note = _dir_actor_field(os.path.join(repo_root, ".fleet", "nonexistent-kind"), 5)
    if note and "missing" in note:
        print("  OK    absent source reported CANNOT-ASSESS by name, not FAIL")
    else:
        print("  FAIL  absent source was not named CANNOT-ASSESS")
        failures += 1

    # 4: the real tree still resolves every declared identity's own id.
    with open(real_actors, "r", encoding="utf-8") as fh:
        real = yaml.safe_load(fh)
    for identity_id in real["identities"]:
        try:
            resolve_actor(identity_id)
        except (ActorUnresolvedError, DelegationUndeclaredError) as exc:
            print(f"  FAIL  declared identity {identity_id!r} does not resolve: {exc}")
            failures += 1
    if not failures:
        print("  OK    every declared identity in the real tree resolves")

    print(f"actor-identity-parity --self-test: {'OK' if not failures else 'NOT-OK'} ({failures} failure(s))")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
