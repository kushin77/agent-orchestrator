"""The cockpit command line (issue #566, RC-11 of EPIC #551).

    cockpit                              the interactive cockpit (alternate screen)
    cockpit --once                       one frame, then exit
    cockpit --follow                     follow the two authenticated SSE streams
    cockpit <MNEMONIC> [name=value ...]  one declared function, once
    cockpit --dry-run <MNEMONIC> ...     print the request, send nothing

The command line's language is ONLY RC-2's declared verbs and RC-10's declared
mnemonics (resolved through ``cockpit_registry``) — no ad-hoc verb exists here.

Exit contract (this repo's tri-state): 0 OK · 1 REFUSED · 2 CANNOT-ASSESS.
A flag-off cockpit, an unreadable registry, or an unreachable plane each exit
non-zero with a NAMED reason — never a silent no-op.


---knowledge---
module_id: control-plane.cockpit.cockpit.cli
system: control-plane
app: cockpit
solution_class: enterprise
patterns: [declared-mnemonics-only, tri-state-exit, named-refusal]
derives_from: null
owner_sme: frontend-sme
tier: L1
interfaces: [main, build_parser, PROG, PLANE_ENV, SESSION_ENV]
invariants: "the command line's language is ONLY RC-2's declared verbs and RC-10's declared mnemonics resolved through cockpit_registry; no ad-hoc verb exists here"
gotchas: "a flag-off cockpit, an unreadable registry or an unreachable plane each exit non-zero with a NAMED reason, never a silent no-op"
related: ["#566"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any, Optional, Sequence, TextIO

from . import _paths, app as app_module, drill as drill_module, flags as flags_module
from . import follow as follow_module, plane as plane_module, registry as registry_io
from . import ticker as ticker_module

PROG = "cockpit"

#: ``--plane`` -> this environment variable -> the console's own bind (the
#: same environment the RC-5 CLI reads).
PLANE_ENV = "AO_CONTROL_PLANE"
#: ``--session`` -> this environment variable. A secret belongs in the
#: environment or a secret manager, never in a file this repo tracks (GR-6).
SESSION_ENV = "AO_CONTROL_SESSION"

EXIT_OK, EXIT_REFUSED, EXIT_CANNOT_ASSESS = 0, 1, 2


def build_parser(registry: Any) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description=(
            "The terminal cockpit — a client of the RC-3 control API and the two "
            "authenticated SSE streams. It renders only what the RC-10 function "
            "registry declares, holds no credential of its own, and writes no "
            "fleet state."
        ),
        epilog=(
            "exit codes (this repo's tri-state): 0 OK · 1 a named refusal · "
            "2 CANNOT-ASSESS (a flag-off cockpit, an unreadable registry, or an "
            "unreachable plane) — argparse also exits 2 on a usage error."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "mnemonic",
        nargs="?",
        metavar="<MNEMONIC>",
        help="one declared RC-10 function id (omitted: the interactive cockpit)",
    )
    parser.add_argument(
        "argument",
        nargs=argparse.REMAINDER,
        metavar="name=value",
        help="the declared parameters, name=value (repeatable)",
    )
    parser.add_argument(
        "--role",
        default="Analyst",
        choices=sorted(registry.roles),
        help="the role workspace (a lens, never a permission; default: Analyst)",
    )
    parser.add_argument(
        "--plane",
        default=os.environ.get(PLANE_ENV) or None,
        metavar="URL",
        help=(
            "the console app serving the control family "
            f"(default: the console's own bind, {PLANE_ENV} or the local address)"
        ),
    )
    parser.add_argument(
        "--session",
        default=os.environ.get(SESSION_ENV),
        metavar="TOKEN",
        help=(
            "the console session token (os-session-token); defaults to "
            f"${SESSION_ENV}. The control family's only caller identity is the "
            "console session (ADR-0025 D2)."
        ),
    )
    parser.add_argument(
        "--command-id",
        default="",
        metavar="ID",
        help="pin the command id (the caller's idempotency key)",
    )
    parser.add_argument(
        "--confirm",
        default="",
        metavar="<MNEMONIC>",
        help="the explicit confirmation an irreversible function requires",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        metavar="SECONDS",
        help="how long to wait for the plane",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the request that would be sent, and send nothing",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="render one frame and exit (no TTY needed)",
    )
    parser.add_argument(
        "--follow",
        action="store_true",
        help=(
            "follow the two authenticated SSE streams (fleet_projection, "
            "telemetry_live_feed) over the session identity path"
        ),
    )
    parser.add_argument(
        "--max-events",
        type=int,
        default=0,
        metavar="N",
        help="bound --follow to N frames per stream (0: unbounded)",
    )
    parser.add_argument(
        "--refresh",
        type=float,
        default=1.0,
        metavar="SECONDS",
        help="seconds between interactive redraws",
    )
    return parser


def main(
    argv: Optional[Sequence[str]] = None,
    *,
    transport: Any = None,
    stream_transports: Optional[dict[str, follow_module.SseTransport]] = None,
    session: Optional[str] = None,
    registry_path: Optional[str] = None,
    flags_registry: Optional[str] = None,
    source: Optional[drill_module.DrillSource] = None,
    alerts: Optional[list[ticker_module.Alert]] = None,
    out: Optional[TextIO] = None,
    err: Optional[TextIO] = None,
) -> int:
    """Run one invocation and return the exit code.

    The keyword arguments are the injection seam this package's suite uses:
    offline transports, a registry built from a modified document, the output
    streams — no test touches the network.
    """
    stdout = out if out is not None else sys.stdout
    stderr = err if err is not None else sys.stderr

    # 1. The function registry (RC-10) — unreadable is CANNOT-ASSESS, named.
    try:
        registry = registry_io.load(registry_path)
    except Exception as exc:  # noqa: BLE001 - any failure is CANNOT-ASSESS
        stderr.write(
            f"cockpit: CANNOT-ASSESS \u2014 registry_unreadable: the RC-10 function "
            f"registry cannot be read: {exc}\n"
        )
        return EXIT_CANNOT_ASSESS

    parser = build_parser(registry)
    args = parser.parse_args(argv)

    # 2. The startup flag gate — fail-closed, named (GR-5 / AO-GR-6).
    flag_state = flags_module.FlagState(
        repo_root=_paths.ROOT, registry_path=flags_registry
    )
    if flag_state.state(flags_module.SURFACE) != "on":
        stdout.write(flags_module.render_flag_off() + "\n")
        return EXIT_CANNOT_ASSESS

    resolved_session = args.session if session is None else session

    if args.follow:
        return _run_follow(args, resolved_session, stream_transports, stdout, stderr)

    plane = plane_module.CockpitPlane(
        base_url=args.plane or plane_module.DEFAULT_PLANE,
        transport=transport,
        timeout=(
            args.timeout if args.timeout is not None else plane_module.DEFAULT_TIMEOUT
        ),
    )
    fixtures = registry_io.panel_fixtures()
    cockpit_app = app_module.Cockpit(
        registry=registry,
        fixtures=fixtures,
        plane=plane,
        flag_state=flag_state.state,
        role=args.role,
        session=resolved_session or "",
        source=source if source is not None else drill_module.FixtureDrillSource(),
        alerts=alerts if alerts is not None else ticker_module.load_alerts(),
    )

    if args.mnemonic:
        function = registry.functions.get(args.mnemonic)
        if function is None:
            stderr.write(
                app_module._cockpit_refusal(
                    "undeclared_function",
                    f"{args.mnemonic!r} is not a declared cockpit function (RC-10)",
                )
                + "\n"
            )
            return EXIT_REFUSED
        given: dict[str, Any] = {}
        errors: list[str] = []
        for token in args.argument:
            if "=" not in token:
                errors.append(f"PARAMETER-SYNTAX: {token!r} is not name=value")
                continue
            name, _, raw = token.partition("=")
            parameter = function.parameter(name)
            if parameter is None:
                errors.append(
                    f"UNKNOWN-PARAMETER: {args.mnemonic} does not declare a parameter "
                    f"{name!r} (declared: {list(function.parameter_names) or 'none'})"
                )
                continue
            try:
                given[name] = app_module._coerce(parameter, raw)
            except ValueError as exc:
                errors.append(f"PARAMETER-TYPE: {args.mnemonic}.{name}: {exc}")
        if errors:
            stderr.write(
                "cockpit: REFUSED\n" + "\n".join(f"  FAIL  {error}" for error in errors) + "\n"
            )
            return EXIT_REFUSED
        exit_code, text = cockpit_app.one_shot(
            args.mnemonic,
            given,
            dry_run=args.dry_run,
            confirm=args.confirm,
            command_id=args.command_id,
        )
        (stdout if exit_code == EXIT_OK else stderr).write(text + "\n")
        return exit_code

    if args.once:
        stdout.write(cockpit_app.compose() + "\n")
        return EXIT_OK

    return cockpit_app.run(stdout, refresh=args.refresh)


def _run_follow(
    args: argparse.Namespace,
    session: str,
    stream_transports: Optional[dict[str, follow_module.SseTransport]],
    out: TextIO,
    err: TextIO,
) -> int:
    """``--follow``: consume the two declared streams over the session identity path."""
    try:
        from aoctl.contract import session_cookie_name
    except Exception as exc:  # noqa: BLE001 - an unreadable contract is CANNOT-ASSESS
        err.write(
            f"cockpit: CANNOT-ASSESS \u2014 contract_unavailable: the console "
            f"session cookie name cannot be read: {exc}\n"
        )
        return EXIT_CANNOT_ASSESS
    transports: dict[str, follow_module.SseTransport] = {}
    if stream_transports is not None:
        transports = dict(stream_transports)
    else:
        base = args.plane or plane_module.DEFAULT_PLANE
        http = follow_module.HttpSseTransport(base)
        transports = {stream.surface: http for stream in follow_module.declared_streams()}
    return follow_module.follow(
        session=session or "",
        cookie_name=session_cookie_name(),
        transports=transports,
        out=out,
        max_events=max(args.max_events, 0),
    )


if __name__ == "__main__":  # pragma: no cover - entry point
    raise SystemExit(main())
