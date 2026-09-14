#!/usr/bin/env python3
"""CLI for the paperclip cross-boundary auth seam (issue #412).

Verbs an operator (or the gate) needs:

* ``mint-agent``  — mint an agent key bound to a registered agent.
* ``mint-board``  — mint a board token bound to an existing fleet session.
* ``authorize``   — resolve one boundary request (JSON on stdin) to a decision.
* ``controls``    — provoke and prove every negative control, in process.

Secrets are read from an environment variable (default ``PAPERCLIP_AUTH_KEY``),
never from a file or an argv literal, and are never printed (GR-6). ``controls``
uses an ephemeral in-process key unless one is supplied, so it can prove the
refusals with **no** secret material present at all.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

if __package__ in (None, ""):  # executed as a script, not imported as a package
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from integrations.paperclip.auth import board as board_mod  # noqa: E402
from integrations.paperclip.auth import guard as guard_mod  # noqa: E402
from integrations.paperclip.auth import jwt as jwt_mod  # noqa: E402
from integrations.paperclip.auth import registry as registry_mod  # noqa: E402
from integrations.paperclip.auth import runbridge as runbridge_mod  # noqa: E402
from integrations.paperclip.auth.model import AuthError  # noqa: E402

#: Default environment variable holding the boundary signing key.
DEFAULT_KEY_VAR = "PAPERCLIP_AUTH_KEY"

#: Default upstream company scope for the fleet.
DEFAULT_COMPANY = "purebliss"


def _root(args: argparse.Namespace) -> Path:
    return Path(args.root).resolve()


def _key(args: argparse.Namespace) -> str:
    """Resolve the signing key from the environment, or CANNOT-ASSESS."""
    name = args.key_var
    value = os.environ.get(name, "")
    if not value:
        print(
            f"auth: CANNOT-ASSESS — signing key env {name!r} is not set "
            "(provision it via env / secret manager, never a file)",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return value


def cmd_mint_agent(args: argparse.Namespace) -> int:
    token = registry_mod.mint_agent_key(
        _root(args),
        args.agent,
        company=args.company,
        secret=_key(args),
        now=args.now,
        ttl_seconds=args.ttl,
    )
    print(token)
    return 0


def _session(args: argparse.Namespace) -> board_mod.BoardSession:
    if args.session_record:
        record = json.loads(Path(args.session_record).read_text(encoding="utf-8"))
    else:
        found = board_mod.load_operator_session(_root(args), args.session_id)
        if found is None:
            print(
                f"auth: CANNOT-ASSESS — no operator session record for {args.session_id!r}",
                file=sys.stderr,
            )
            raise SystemExit(2)
        record = found
    return board_mod.BoardSession.from_record(record, company=args.company)


def cmd_mint_board(args: argparse.Namespace) -> int:
    token = board_mod.mint_board_token(_session(args), secret=_key(args), now=args.now, ttl_seconds=args.ttl)
    print(token)
    return 0


def cmd_authorize(args: argparse.Namespace) -> int:
    request = json.load(sys.stdin)
    headers = {str(k): str(v) for k, v in (request.get("headers") or {}).items()}
    try:
        context = guard_mod.guard_request(
            str(request.get("method") or "GET"),
            headers,
            root=_root(args),
            company=str(request.get("company") or args.company),
            secret=_key(args),
            now=args.now if args.now is not None else int(time.time()),
            permission=str(request["permission"]),
            bridge=runbridge_mod.RunBridge(),
            correlation_id=request.get("correlation_id"),
        )
    except AuthError as exc:
        print(f"REFUSED {exc.status} {exc.code}: {exc.message}", file=sys.stderr)
        return 1
    decision = {
        "allowed": True,
        "actor": context.principal.actor,
        "company": context.principal.company,
        "permissions": list(context.principal.permissions),
        "run": None if context.run is None else {"run_id": context.run.run_id, "correlation_id": context.run.correlation_id},
    }
    print(json.dumps(decision, indent=2, sort_keys=True))
    return 0


# --------------------------------------------------------------------------
# controls — provoke every negative control and prove each is refused by name
# --------------------------------------------------------------------------


def _provoke(fn: Any, expect_status: int, expect_code: str, expect_name: str, key: str) -> Tuple[bool, str]:
    """Run a provoking call; return (refused_by_name, rendered_refusal)."""
    try:
        fn()
    except AuthError as exc:
        leaked = bool(key) and key in exc.message
        named = (
            exc.status == expect_status
            and exc.code == expect_code
            and expect_name in exc.message
            and not leaked
        )
        rendered = f"{exc.status} {exc.code}: {exc.message}"
        if leaked:
            rendered += "  [FAIL: refusal echoed the key]"
        return named, rendered
    return False, "not refused (the control passed — a false green)"


def cmd_controls(args: argparse.Namespace) -> int:
    root = _root(args)
    company = args.company
    now = int(time.time()) if args.now is None else int(args.now)
    env_key = os.environ.get(args.key_var, "")
    key = env_key or secrets.token_hex(32)
    key_mode = "environment" if env_key else "ephemeral in-process placeholder (never printed)"

    print(f"check-paperclip-auth: signing key source — {key_mode}")
    print(f"check-paperclip-auth: company={company} agent={args.agent}")

    failures: List[str] = []

    # --- positives (a boundary that refuses everything is as useless as one
    #     that authenticates everything, so the happy path is proven too) -----
    agent_token = registry_mod.mint_agent_key(
        root, args.agent, company=company, secret=key, now=now, ttl_seconds=900
    )
    principal = registry_mod.verify_agent_key(root, agent_token, company=company, secret=key, now=now)
    if principal.subject == args.agent and "run:write" in principal.permissions:
        print(f"  OK    agent identity minted+verified from the registry: {principal.actor}")
    else:
        failures.append("agent identity did not resolve from the registry")

    session = board_mod.BoardSession.from_record(
        {"session_id": "sess-operator-1", "agent_id": "operator", "roles": ["operator"]},
        company=company,
    )
    board_token = board_mod.mint_board_token(session, secret=key, now=now, ttl_seconds=900)
    board_principal = board_mod.verify_board_token(
        board_token, company=company, secret=key, now=now, session_lookup=lambda sid: True
    )
    if board_principal.kind == "human" and "approval:request" in board_principal.permissions:
        print(f"  OK    board identity bound to the fleet session path: {board_principal.actor}")
    else:
        failures.append("board identity did not resolve from the session path")

    bridge = runbridge_mod.RunBridge()
    binding = bridge.bind("run-1", "corr-1")
    if bridge.correlation_for("run-1") == "corr-1" and bridge.run_for("corr-1") == "run-1":
        print(f"  OK    run correlation bridged: {binding.run_id} <-> {binding.correlation_id}")
    else:
        failures.append("run correlation bridge did not join run id to correlation id")

    # --- controls that need the whole boundary pipeline ---------------------
    def _boundary_not_allowed() -> None:
        """A known caller on a known route that simply lacks the permission."""
        guard_mod.guard_request(
            "POST",
            {"Authorization": f"Bearer {agent_token}"},
            root=root,
            company=company,
            secret=key,
            now=now,
            permission="approval:request",
        )

    def _boundary_replayed_run_id() -> None:
        """The same X-Paperclip-Run-Id on two mutating requests."""
        headers = {
            "Authorization": f"Bearer {agent_token}",
            "X-Paperclip-Run-Id": "run-replay",
        }
        kwargs = dict(
            root=root,
            company=company,
            secret=key,
            now=now,
            permission="run:write",
            bridge=runbridge_mod.RunBridge(),
        )
        guard_mod.guard_request("POST", headers, **kwargs)
        guard_mod.guard_request("POST", headers, **kwargs)

    # --- negative controls, each shown refused by name ----------------------
    controls = [
        (
            "A expired token (401)",
            401,
            "token_expired",
            "expired",
            lambda: registry_mod.verify_agent_key(
                root,
                registry_mod.mint_agent_key(
                    root, args.agent, company=company, secret=key, now=now, ttl_seconds=-1
                ),
                company=company,
                secret=key,
                now=now,
            ),
        ),
        (
            "A2 unknown identity — token for an unregistered agent (401)",
            401,
            "invalid_token",
            "not bound to a registered agent",
            lambda: _unknown_agent_token(root, company, key, now),
        ),
        (
            "B wrong company — token scoped to another company (403)",
            403,
            "cross_tenant",
            "cross-company",
            lambda: registry_mod.verify_agent_key(
                root,
                registry_mod.mint_agent_key(
                    root, args.agent, company="other-company", secret=key, now=now, ttl_seconds=900
                ),
                company=company,
                secret=key,
                now=now,
            ),
        ),
        (
            "C replayed run id at the boundary (409)",
            409,
            "replayed_run_id",
            "replayed",
            _boundary_replayed_run_id,
        ),
        (
            "D missing Authorization header (401)",
            401,
            "unauthorized",
            "Bearer credential",
            lambda: guard_mod.guard_request(
                "GET", {}, root=root, company=company, secret=key, now=now, permission="run:read"
            ),
        ),
        (
            "E authenticated but not allowed — 403, not 404",
            403,
            "permission_denied",
            "permission",
            _boundary_not_allowed,
        ),
    ]

    for name, status, code, frag, fn in controls:
        ok, rendered = _provoke(fn, status, code, frag, key)
        if ok:
            print(f"  OK    {name}: refused -> {rendered}")
        else:
            print(f"  FAIL  {name}: {rendered}", file=sys.stderr)
            failures.append(name)

    # The not-allowed refusal is genuinely a denial, not a missing route: an
    # *undeclared* permission is a programming error, so the code cannot
    # produce a 404 where a 403 is due.
    try:
        guard_mod.authorize(principal, "no:such-permission")
    except ValueError:
        print(
            "  OK    denial distinguished from a missing route: "
            "undeclared permission -> ValueError, denial -> 403"
        )
    else:
        failures.append("an undeclared permission was not distinguished from a denial")

    if failures:
        print(
            f"check-paperclip-auth: FAIL — {len(failures)} control(s) not refused by name",
            file=sys.stderr,
        )
        return 1
    print("check-paperclip-auth: OK — every negative control refused by name; no key echoed")
    return 0


def _unknown_agent_token(root: Path, company: str, key: str, now: int) -> None:
    """Craft a well-signed token whose subject is not a registered agent."""
    token = jwt_mod.sign(
        {
            "iss": "agent-orchestrator",
            "sub": "ghost-agent",
            "aud": "paperclip",
            "kind": "agent",
            "company": company,
            "iat": now,
            "exp": now + 900,
            "jti": "control-unknown",
        },
        key,
    )
    registry_mod.verify_agent_key(root, token, company=company, secret=key, now=now)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="paperclip-auth",
        description="paperclip cross-boundary auth seam (issue #412, ADR-0013)",
    )
    parser.add_argument("--root", default=".", help="repo root (default: cwd)")
    parser.add_argument("--company", default=DEFAULT_COMPANY, help="company/tenant scope")
    parser.add_argument("--key-var", default=DEFAULT_KEY_VAR, help="env var holding the signing key")
    parser.add_argument("--now", type=int, default=None, help="override the clock (epoch seconds)")
    sub = parser.add_subparsers(dest="verb", required=True)

    mint_agent = sub.add_parser("mint-agent", help="mint an agent key for a registered agent")
    mint_agent.add_argument("--agent", required=True, help="registered agent id")
    mint_agent.add_argument("--ttl", type=int, default=900, help="lifetime in seconds")
    mint_agent.set_defaults(func=cmd_mint_agent)

    mint_board = sub.add_parser("mint-board", help="mint a board token for a fleet session")
    mint_board.add_argument("--session-id", default="", help="fleet session id")
    mint_board.add_argument("--session-record", default="", help="JSON session record path")
    mint_board.add_argument("--ttl", type=int, default=3600, help="lifetime in seconds")
    mint_board.set_defaults(func=cmd_mint_board)

    authorize = sub.add_parser("authorize", help="resolve one boundary request from stdin JSON")
    authorize.set_defaults(func=cmd_authorize)

    controls = sub.add_parser("controls", help="provoke and prove every negative control")
    controls.add_argument("--agent", default="paperclip", help="registered agent used as the subject")
    controls.set_defaults(func=cmd_controls)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if getattr(args, "func", None) is None:  # pragma: no cover - argparse enforces
        return 2
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
