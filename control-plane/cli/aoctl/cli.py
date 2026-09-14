"""The CLI itself — argparse, the verb table, and the one dispatch path.

One invocation does five things, in this order, and the order is the contract:

1. **read the vocabulary** (RC-2's registry). Unreadable is a named refusal —
   the CLI will not send a command it cannot name.
2. **resolve the verb to one declared command id** (``vocabulary.SURFACE``).
   A verb the registry does not declare, or declares ``exposed: false``, is
   refused **locally**: the CLI does not ask the plane to say no (the acceptance
   "every verb maps to exactly one declared command id" is enforced here, not
   asserted in a README).
3. **check the local preconditions** — a caller identity for anything that will
   be sent, and an explicit confirmation for a verb whose declared
   ``effect_class`` is ``irreversible`` (gap-analysis §8.4: irreversibility is
   declared by the vocabulary, never inferred from a verb's name). The
   confirmation guards the **send**, not the inspection: ``--dry-run`` of an
   irreversible verb is allowed and says so, because teaching an operator to type
   the confirmation token habitually is the habit the guard exists to prevent.
4. **build the request** (pure) and, on ``--dry-run``, print it and stop:
   nothing is sent, and that is asserted by the suite rather than promised.
5. **send it, once**, and report one receipt per action — or one named refusal.

``--dry-run`` needs no session and prints the request it *would* send, including
whether a session is configured; the value is never printed, because the terminal
is shared.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Mapping, Optional, Sequence, TextIO

from . import receipt as receipt_module
from . import vocabulary
from .refusals import EXIT_OK, Refusal, local_refusal

PROG = "ao-control"

#: ``--plane`` -> this environment variable -> the console's own bind.
PLANE_ENV = "AO_CONTROL_PLANE"
#: ``--session`` -> this environment variable. A secret belongs in the
#: environment or a secret manager, never in a file this repo tracks (GR-6).
SESSION_ENV = "AO_CONTROL_SESSION"


def surface_table(registry: Optional[vocabulary.Registry]) -> str:
    """The verb table, built from the registry — the acceptance, printed.

    Each row shows the CLI's verb and the **one declared command id** it speaks,
    with the effect class and capability the registry declares for it. When the
    registry cannot be read the ids are still printed (they are the CLI's own
    table) with the registry columns marked unavailable, so ``--help`` keeps
    working on a checkout whose vocabulary is missing.
    """
    lines = [
        "the verbs — each speaks exactly one declared command id from RC-2",
        f"({vocabulary.REGISTRY_RELATIVE}):",
        "",
        f"  {'verb':<9} {'command id':<16} {'effect':<13} capability",
    ]
    for name, verb_id in vocabulary.SURFACE:
        row = registry.verbs.get(verb_id) if registry is not None else None
        if row is None:
            lines.append(f"  {name:<9} {verb_id:<16} {'?':<13} (registry unavailable)")
            continue
        gate = "" if row.exposed else "  [withheld]"
        lines.append(
            f"  {name:<9} {verb_id:<16} {row.effect_class:<13} {row.capability}{gate}"
        )
    return "\n".join(lines)


def build_parser(registry: Optional[vocabulary.Registry] = None) -> argparse.ArgumentParser:
    """The CLI's parser. Flags go **before** the verb; after it, the lever's argv."""
    parser = argparse.ArgumentParser(
        prog=PROG,
        description=(
            "A thin operator client for the fleet's remote control API. It speaks "
            "only POST /api/control/<family>/<action> on the console app, prints "
            "one receipt per action, and refuses locally when it cannot verify "
            "the plane. It never writes fleet state itself."
        ),
        epilog=surface_table(registry)
        + "\n\n"
        "exit codes (this repo's tri-state): 0 the plane returned a receipt · "
        "1 a named refusal · 2 CANNOT-ASSESS (no verdict was obtainable) — "
        "argparse also exits 2 on a usage error.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "verb",
        choices=[name for name, _ in vocabulary.SURFACE],
        metavar="<verb>",
        help="which verb to command",
    )
    parser.add_argument(
        "args",
        nargs=argparse.REMAINDER,
        help=(
            "everything after <verb> is forwarded verbatim to the lever's own "
            "CLI (put ao-control's own flags before <verb>)"
        ),
    )
    parser.add_argument(
        "--plane",
        default=os.environ.get(PLANE_ENV) or None,
        metavar="URL",
        help=(
            f"the console app serving the control family "
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
        help=(
            "pin the command id (the caller's idempotency key). Default: one "
            "minted per invocation; pass the same id again to replay."
        ),
    )
    parser.add_argument(
        "--confirm",
        default="",
        metavar="<verb id>",
        help=(
            "the explicit confirmation an irreversible verb requires; the token "
            "is the verb's declared command id"
        ),
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
        "--json",
        action="store_true",
        help="print the machine document instead of the human rendering",
    )
    return parser


def main(
    argv: Optional[Sequence[str]] = None,
    *,
    transport: Any = None,
    registry: Optional[vocabulary.Registry] = None,
    session: Optional[str] = None,
    out: Optional[TextIO] = None,
    err: Optional[TextIO] = None,
) -> int:
    """Run one invocation and return the exit code.

    The keyword arguments are the injection seam its own suite uses: an offline
    ``FixtureTransport`` (so no test — and therefore no gate — touches the
    network), a registry built from a modified document (to provoke the local
    refusals), and the output streams.
    """
    stdout = out if out is not None else sys.stdout
    stderr = err if err is not None else sys.stderr

    failure: Optional[Exception] = None
    loaded = registry
    if loaded is None:
        try:
            loaded = vocabulary.Registry.load()
        except vocabulary.VocabularyUnreadable as exc:
            failure = exc
            loaded = None

    parser = build_parser(registry=loaded)
    args = parser.parse_args(argv)

    command_id = args.command_id or ""
    if failure is not None:
        return _refuse(
            stdout, stderr, args,
            local_refusal("vocabulary_unreadable", str(failure)),
            command_id=command_id,
        )

    assert loaded is not None  # guarded above: either a registry or a refusal
    try:
        row = loaded.row_for_verb(args.verb)
    except (vocabulary.SurfaceDrift, vocabulary.UnknownCliVerb) as exc:
        return _refuse(
            stdout, stderr, args,
            local_refusal("surface_drift", str(exc)),
            command_id=command_id,
        )
    if not row.exposed:
        return _refuse(
            stdout, stderr, args,
            local_refusal(
                "verb_not_exposed",
                f"{row.id} is declared with exposed: false — {row.why_not_exposed}",
            ),
            command_id=command_id,
        )
    if row.irreversible and args.confirm != row.id and not args.dry_run:
        return _refuse(
            stdout, stderr, args,
            local_refusal(
                "confirmation_required",
                f"{row.id} declares effect_class {row.effect_class!r}; "
                f"pass --confirm {row.id}",
            ),
            command_id=command_id,
        )

    resolved_session = args.session if session is None else session
    if not args.dry_run and not resolved_session:
        return _refuse(
            stdout, stderr, args,
            local_refusal(
                "no_session",
                f"set --session or ${SESSION_ENV}; the control family's only "
                "caller identity is the console session",
            ),
            command_id=command_id,
        )

    # The wire is imported only when a request is actually built: --help, every
    # local refusal above, and an unreadable vocabulary all work with no
    # `integrations/paperclip` import at all.
    from . import plane as plane_module

    plane = plane_module.Plane(
        base_url=args.plane or plane_module.DEFAULT_PLANE,
        transport=transport,
        timeout=args.timeout if args.timeout is not None else plane_module.DEFAULT_TIMEOUT,
    )
    command_id = command_id or plane_module.mint_command_id()
    request = plane.request_for(row, command_id=command_id, args=args.args)

    if args.dry_run:
        note = ""
        if row.irreversible and args.confirm != row.id:
            note = (
                f"{row.id} is irreversible: a real call will require "
                f"--confirm {row.id} before anything is sent"
            )
        document = receipt_module.envelope(
            verb=args.verb,
            verdict="OK",
            command_id=command_id,
            request=plane.describe(request, session_present=bool(resolved_session)),
            note=note,
        )
        return _emit(stdout, args, document, _render_request(document))

    try:
        record = plane.send(request, session=resolved_session)
    except Refusal as refusal:
        return _refuse(
            stdout, stderr, args, refusal,
            command_id=command_id, request=request.as_json(),
        )

    document = receipt_module.envelope(
        verb=args.verb,
        verdict="OK",
        command_id=command_id,
        request=request.as_json(),
        receipt=record,
    )
    return _emit(stdout, args, document, receipt_module.render(record, verb=args.verb))


def _emit(stream: TextIO, args: argparse.Namespace, document: Mapping[str, Any], text: str) -> int:
    """Print the answer in the shape the operator asked for."""
    if args.json:
        stream.write(json.dumps(document, sort_keys=True, indent=2) + "\n")
    else:
        stream.write(text + "\n")
    return EXIT_OK


def _refuse(
    stdout: TextIO,
    stderr: TextIO,
    args: argparse.Namespace,
    refusal: Refusal,
    *,
    command_id: str = "",
    request: Optional[Mapping[str, Any]] = None,
) -> int:
    """Report one refusal, in the shape the operator asked for, and exit with it.

    In JSON mode the document goes to stdout (a machine caller reads it there, and
    the exit code carries the verdict); the human rendering goes to stderr, so a
    pipeline of receipts is never polluted by a refusal.
    """
    if args.json:
        stdout.write(
            json.dumps(
                receipt_module.envelope(
                    verb=args.verb,
                    verdict=refusal.verdict,
                    command_id=command_id,
                    request=request,
                    refusal=refusal,
                ),
                sort_keys=True,
                indent=2,
            )
            + "\n"
        )
    else:
        stderr.write(refusal.render() + "\n")
    return refusal.exit_code


def _render_request(document: Mapping[str, Any]) -> str:
    """The human rendering of a ``--dry-run`` request."""
    request = document.get("request") or {}
    headers = request.get("headers") or {}
    body = request.get("body") or {}
    lines = [
        "ao-control: DRY-RUN — nothing was sent",
        f"  {request.get('method', 'POST')} {request.get('url') or request.get('path')}",
        f"  body        {json.dumps(body, sort_keys=True)}",
    ]
    for name in sorted(headers):
        lines.append(f"  {name:<11} {headers[name]}")
    if document.get("note"):
        lines.append(f"  note        {document['note']}")
    return "\n".join(lines)
