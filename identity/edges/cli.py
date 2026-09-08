"""Offline CLI for the public API + proxy allowlist boundary (issue #37).

Demonstrates the authn-never-authz edge without a real HTTP server (the real
REST server arrives in issue #38): print the public allowlist, probe a single
request end to end, or walk the demo matrix including the negatives.

The authenticated probes use a **demo-harness verifier** (a fixed demo token
for tenant ``acme``) so the CLI runs on stdlib alone.  The real verifier is
issue #35 ``identity.sso.SsoService.verify_session``, injected in production
and exercised by the test suite against real (offline) SSO sessions.

Usage (from the repo root):

    python3 identity/edges/cli.py routes
    python3 identity/edges/cli.py probe --method POST --path /v1/agents/a1/tasks
    python3 identity/edges/cli.py demo
"""

from __future__ import annotations

import argparse
import json
import os
import sys

# Self-bootstrap: ``identity/`` is a PEP-420 namespace package rooted at the
# repo root, so when this file is run directly (``python3 identity/edges/
# cli.py``) the repo root must be importable.
_REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from identity.edges.allowlist import default_public_routes  # noqa: E402
from identity.edges.authn import Verifier  # noqa: E402
from identity.edges.edge import PublicEdge, default_edge  # noqa: E402
from identity.edges.model import ForwardedRequest  # noqa: E402

#: Demo harness token -> verified claims (offline CLI only; never production).
DEMO_TOKEN = "demo-token"
DEMO_CLAIMS = {
    "iss": "urn:agent-orchestrator:sso",
    "sub": "u_alice",
    "purpose": "api-session",
    "tenantId": "acme",
    "subjectType": "user",
    "role": "member",
    "email": "alice@acme.example.com",
}


def demo_verifier(token: str, **_: object) -> dict[str, str]:
    """A demo-harness verifier accepting the fixed demo token (offline CLI)."""
    if token != DEMO_TOKEN:
        raise ValueError("invalid demo token")
    return DEMO_CLAIMS


def echo_backend(request: ForwardedRequest) -> tuple[int, dict[str, object]]:
    """A demo backend that echoes the outbound request as a 200 body.

    Lets the operator *see* the built (not copied) outbound request - which
    headers survived, which identity was derived, which backend path was
    rendered.  A real backend answers with its own semantics; the edge passes
    the answer through untouched.
    """
    return 200, {
        "method": request.method,
        "backendPath": request.backend_path,
        "headers": request.headers,
        "identity": None
        if request.identity is None
        else {
            "tenantId": request.identity.tenant_id,
            "subjectId": request.identity.subject_id,
            "subjectType": request.identity.subject_type,
        },
        "roleSnapshot": list(request.role_snapshot),
        "body": request.body,
    }


def _print_table(edge: PublicEdge) -> None:
    print("public allowlist (issue #37) - authn never authz")
    print(f"{'route id':<22} {'method':<8} api path -> backend path")
    print("-" * 78)
    for route in edge.routes.routes:
        arrow = f"{route.api_path} -> {route.backend_path}"
        auth = "authn" if route.authenticated else "open"
        print(f"{route.route_id:<22} {route.method:<8} {arrow}  [{auth}]")


def _cmd_probe(args: argparse.Namespace) -> int:
    backend = None if args.no_backend else echo_backend
    verifier: Verifier | None = demo_verifier if not args.no_verifier else None
    edge = PublicEdge(
        default_public_routes(), verifier=verifier, backend=backend
    )
    headers = {}
    if args.token:
        headers["authorization"] = f"Bearer {args.token}"
    if args.trace:
        headers["x-request-id"] = args.trace
    body = None
    if args.body:
        try:
            body = json.loads(args.body)
        except json.JSONDecodeError as exc:
            print(f"body is not valid JSON: {exc}", file=sys.stderr)
            return 2
    response = edge.handle_request(
        args.method, args.path, headers, body=body, query=args.query
    )
    print(json.dumps(response.to_dict(), indent=2, sort_keys=True))
    return 0


def _cmd_demo() -> int:
    print("demo harness (issue #37) - negative controls first")
    demo_backend = echo_backend
    edge = PublicEdge(
        default_public_routes(), verifier=demo_verifier, backend=demo_backend
    )
    checks = [
        # (label, method, path, token)
        ("unauthenticated (no token)  ", "GET", "/v1/tenants/me/usage", None),
        ("unauthenticated (bad token) ", "GET", "/v1/tenants/me/usage", "garbage"),
        ("unknown route               ", "GET", "/v1/agents/nope/nope", DEMO_TOKEN),
        ("traversal attempt           ", "GET", "/v1/agents/../../internal/x", DEMO_TOKEN),
        ("method not allowed          ", "DELETE", "/v1/agents/a1/tasks", DEMO_TOKEN),
        ("allowlisted + authenticated ", "POST", "/v1/agents/a1/tasks", DEMO_TOKEN),
        ("tenant me -> claims tenant  ", "GET", "/v1/tenants/me/usage", DEMO_TOKEN),
        ("unauthenticated route       ", "GET", "/healthz", None),
    ]
    for label, method, path, token in checks:
        headers = {}
        if token:
            headers["authorization"] = f"Bearer {token}"
        response = edge.handle_request(method, path, headers)
        print(f"- {label} {method} {path}")
        print(json.dumps(response.to_dict(), indent=2, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="identity.edges.cli",
        description="Public API + proxy allowlist boundary (issue #37) - offline",
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("routes", help="print the public allowlist table")

    probe = sub.add_parser("probe", help="run one request through the edge")
    probe.add_argument("--method", required=True)
    probe.add_argument("--path", required=True)
    probe.add_argument("--token", default=None)
    probe.add_argument("--body", default=None)
    probe.add_argument("--query", default="")
    probe.add_argument("--trace", default=None)
    probe.add_argument("--no-backend", action="store_true")
    probe.add_argument("--no-verifier", action="store_true")

    demo = sub.add_parser("demo", help="end-to-end walkthrough incl. negatives")
    demo.set_defaults(command="demo")

    args = parser.parse_args(argv)
    if args.command == "routes":
        _print_table(default_edge(backend=None))
        return 0
    if args.command == "probe":
        return _cmd_probe(args)
    if args.command == "demo":
        return _cmd_demo()
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
