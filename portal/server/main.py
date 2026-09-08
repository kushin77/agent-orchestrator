#!/usr/bin/env python3
"""portal.server.main — run the offline console server.

The console is a self-contained static app (design-token CSS/JSON twins +
vanilla JS views) served by this stdlib-only Python HTTP layer with the
console routes + SSO session check. No build step and no node_modules.

Usage:

    python3 -m portal.server.main --port 8787

Then open http://127.0.0.1:8787/ in a browser. Demo logins (offline demo
directory, see portal/README.md):

    root@platform.example.com   super-admin (multi-tenant console)
    alice@acme.example.com      tenant owner of acme
    bob@acme.example.com        tenant admin of acme (read-only on controls)
    carol@globex.example.com    tenant owner of globex
    dan@initech.example.com     tenant owner of initech
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
