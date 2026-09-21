#!/usr/bin/env python3
"""portal.server.main — run the offline console server.

The console is a self-contained static app (design-token CSS/JSON twins +
vanilla JS views) served by this stdlib-only Python HTTP layer with the console
routes + the auth-gate session check. No build step and no node_modules.

Usage:

    python3 -m portal.server.main --port 8787

The console has no login of its own: it redirects unauthenticated visitors to
the shared-frontend OS auth gate (``/auth/login``) and establishes a console
session only from a verified auth-gate RS256 ``os-session-token``. Point it at
a mirror of the gate's published JWKS (``GET /auth/.well-known/jwks.json``) and
set the console allowlist before starting it:

    PORTAL_AUTH_GATE_JWKS_FILE=/etc/ao/auth-gate-jwks.json \\
    ROOT_ADMIN_EMAILS=root@platform.example.com \\
    python3 -m portal.server.main --port 8787

With no JWKS mirror configured the console trusts no key and refuses every
session (fail closed). The offline seed directory (portal/README.md) still
supplies the org bindings those identities are authorized with.


---knowledge---
module_id: portal.server.main
system: portal
app: server
solution_class: class
patterns: [cli-entry, fail-closed]
derives_from: null
owner_sme: platform-sme
tier: L0
interfaces: [main]
invariants: "with no JWKS mirror configured the console trusts no key and refuses every session"
gotchas: "the console has no login of its own - unauthenticated visitors are redirected to the shared-frontend auth gate"
related: ["#39"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="portal.server.main", description="agent-orchestrator console (offline)"
    )
    parser.add_argument("--host", default="127.0.0.1", help="bind host")
    parser.add_argument("--port", type=int, default=8787, help="bind port")
    parser.add_argument(
        "--repo-root",
        default=str(REPO_ROOT),
        help="repo root (defaults to the checkout containing this module)",
    )
    args = parser.parse_args(argv)

    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))

    from portal.server.app import build_app
    from portal.server.httpd import serve

    app = build_app(repo_root=Path(args.repo_root))
    serve(app, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
