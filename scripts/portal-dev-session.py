#!/usr/bin/env python3
"""portal-dev-session.py — mint the offline console dev session (issue #732).

WHY this exists. The console (the fleet single-pane-of-glass) is served by
``python3 -m portal.server.main`` and it **fails closed**: with no auth-gate JWKS
mirror configured it refuses every session, so a reviewer who follows the
"run the portal" instructions gets a page whose every API call answers 401 —
with no visible reason. The minting recipe already existed, but only *inside*
tests (``portal/tests/conftest.py``) and one e2e driver
(``e2e/workbook11_portal.py``): a reviewer had no way to obtain a session by
hand. This helper is that way, and nothing more.

WHAT IT MINTS, offline, in one run:

* the **JWKS mirror** file the running server verifies against — the public half
  only, exactly the payload the shared-frontend OS auth gate publishes at
  ``GET /auth/.well-known/jwks.json``;
* the matching **``os-session-token``** the server accepts, minted with the
  private half held in memory and **never written anywhere**;
* a **promoted copy of the surface registry** (``surfaces.fleet_projection`` set
  to ``on``), because the surface ships feature-flag-gated OFF (GR-5) and
  otherwise every ``/api/fleet/*`` read answers ``404 feature_disabled``;
* the ``AO_SURFACE_STATE`` path to *this run's* rollback overlay, so a
  ``.rollout/surface-state.json`` left behind in the checkout cannot silently
  take the surface dark again (``portal/server/surface_state.py``).

It then prints the exact ``export``/``curl`` lines the doc uses (and, with
``--json``, the same values as one JSON object, so a gate can read them without
parsing prose).

WHAT IT REFUSES — because this is a **dev stopgap, never the production
surface** (issue #732), it refuses to be used as one:

* a **production-shaped identity**: ``--email`` must be under a reserved domain
  (RFC 2606: ``example.com``/``.org``/``.net``/``.edu``, ``.invalid``,
  ``.test``, ``localhost``). A real address is refused by name.
* a **deployed console configuration in the ambient environment**
  (``PORTAL_AUTH_GATE_JWKS``, ``PORTAL_AUTH_GATE_JWKS_FILE``,
  ``ROOT_ADMIN_EMAILS``): those are the env vars the console is deployed with, so
  the helper refuses rather than silently minting against a real one — or being
  silently *shadowed* by one (the inline JWKS takes precedence over the file).
* a **runtime directory inside the repository**: every file it writes must live
  outside the checkout, so nothing it mints can be committed. Each target is
  checked against the runtime dir and the repository root before it is written.

Self-check: it validates its own mirror with the repository's own validator,
``python3 infra/portal/auth_env.py check-jwks --root <repo>`` (which imports the
CONSOLE's own ``trusted_keys_from_jwks`` predicate), and it proves that check can
fail — an empty key set must be REFUSED (rc 1). Both outputs are printed. The
validator's verdict is read by *capturing* it and testing containment in Python;
a piped ``grep -q`` would be able to kill its own producer (#852).

Exit-code contract (the repo's honesty tri-state): 0 OK / 1 NOT-OK (a file could
not be written) / 2 CANNOT-ASSESS (a precondition or refusal: no ``python3``
module, no PyYAML, no ``cryptography``, an unknown surface, a production-shaped
identity, an enclosed runtime dir).

Usage:
  python3 scripts/portal-dev-session.py                       # the doc's step 2
  python3 scripts/portal-dev-session.py --json                # for a gate
  python3 scripts/portal-dev-session.py --runtime-dir DIR --port 9999 --email a@b.example.com
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Optional

#: The checkout this helper belongs to (``<repo>/scripts/portal-dev-session.py``).
REPO_ROOT = Path(__file__).resolve().parents[1]

#: The surface the SPoG needs promoted, and where its declaration lives.
SURFACE = "fleet_projection"
REGISTRY_REL = Path("infra") / "feature-flags" / "registry.yaml"
VALIDATOR_REL = Path("infra") / "portal" / "auth_env.py"
MAIN_REL = Path("portal") / "server" / "main.py"

#: Defaults. The email is the identity the offline seed directory (`server/state.py`)
#: binds to every tenant, so a root_admin session sees the whole projection.
DEFAULT_EMAIL = "root@platform.example.com"
DEFAULT_TENANT = "platform"
DEFAULT_TTL = 3600
DEFAULT_PORT = 8787

#: RFC 2606 reserved domains (plus the repo's own placeholder convention,
#: ``scripts/check-secrets.sh``): an identity under one of these cannot be real.
RESERVED_DOMAINS = (
    "example.com",
    "example.org",
    "example.net",
    "example.edu",
    "example.invalid",
    "invalid",
    "test",
    "localhost",
)

#: The env vars the console is DEPLOYED with (``infra/portal/auth_env.py``). Set
#: in the ambient environment they mean a real deployment: refused, never
#: shadowed. The inline JWKS wins over the file in ``portal/server/sso.py``, so
#: minting "around" one would produce a session the server never verifies.
DEPLOYED_ENVS = ("PORTAL_AUTH_GATE_JWKS", "PORTAL_AUTH_GATE_JWKS_FILE", "ROOT_ADMIN_EMAILS")


class DevSessionError(Exception):
    """A precondition or a refusal: CANNOT-ASSESS (exit 2), never worked around."""


def reserved_identity(email: str) -> bool:
    """True when ``email`` sits under a reserved domain (so it cannot be real)."""
    domain = email.rpartition("@")[2].strip().lower()
    if not domain:
        return False
    return any(domain == item or domain.endswith("." + item) for item in RESERVED_DOMAINS)


def default_runtime_dir() -> Path:
    """The runtime dir when none is named: outside the repo, stable, per-user."""
    configured = (os.environ.get("AO_PORTAL_DEV_RUNTIME") or "").strip()
    if configured:
        return Path(configured)
    base = (os.environ.get("XDG_RUNTIME_DIR") or "").strip() or "/tmp"
    return Path(base) / "ao-portal-dev-session"


def resolve_runtime_dir(explicit: Optional[str]) -> Path:
    """The runtime dir, refused when it would live inside the checkout."""
    runtime = Path(explicit).expanduser().resolve() if explicit else default_runtime_dir()
    runtime = runtime.resolve()
    repo = REPO_ROOT.resolve()
    if runtime == repo or repo in runtime.parents:
        raise DevSessionError(
            f"the runtime dir {runtime} is inside the repository ({repo}): every file this "
            "helper mints is dev-only runtime state and must never be a committed path"
        )
    return runtime


def refuse_deployed_env() -> None:
    """Refuse to run against (or be shadowed by) a deployed console config."""
    found = [name for name in DEPLOYED_ENVS if (os.environ.get(name) or "").strip()]
    if found:
        raise DevSessionError(
            "the ambient environment carries a DEPLOYED console configuration "
            f"({', '.join(found)}): this is a dev stopgap and must not mint against, or be "
            "shadowed by, a real one. Re-run with them unset, e.g. "
            + " ".join(f"-u {name}" for name in found)
        )


def write_promoted_registry(runtime: Path, *, surface: str = SURFACE) -> Path:
    """A copy of the committed registry with ``surfaces.<surface>.default: on``.

    The same shape ``scripts/check-operator-terminal.sh`` builds for its own
    probe, and the reason the seam is exercised rather than invented: the
    declaration is read through ``portal/server/fleet.py`` (``AO_SURFACE_REGISTRY``),
    so the committed declaration itself is never edited.
    """
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - exercised only without PyYAML
        raise DevSessionError(
            f"PyYAML is required to promote surfaces.{surface} and is not installed ({exc})"
        ) from exc

    source = REPO_ROOT / REGISTRY_REL
    try:
        document = yaml.safe_load(source.read_text(encoding="utf-8"))
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise DevSessionError(f"cannot read {source}: {exc}") from exc
    surfaces = document.get("surfaces") if isinstance(document, dict) else None
    if not isinstance(surfaces, dict):
        raise DevSessionError(f"{source} carries no surfaces mapping")
    if surface not in surfaces:
        raise DevSessionError(f"{source} declares no surfaces.{surface}: nothing to promote")

    promoted = dict(document)
    promoted["surfaces"] = {name: dict(entry) if isinstance(entry, dict) else entry for name, entry in surfaces.items()}
    entry = promoted["surfaces"][surface]
    promoted["surfaces"][surface] = dict(entry) if isinstance(entry, dict) else {}
    promoted["surfaces"][surface]["default"] = "on"

    target = runtime / "registry-promoted.yaml"
    runtime.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(promoted, sort_keys=False), encoding="utf-8")
    return target


def mint_session(
    runtime: Path, *, email: str, tenant: str, ttl: int, surface: str = SURFACE
) -> dict[str, Any]:
    """Mint the mirror + the token. The private key never leaves this process."""
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    try:
        from identity.sso.jose import generate_rsa_keypair, jwks_for_keys
        from identity.sso.tokens import console_kid_for, issue_console_session_token
        from portal.server.sso import SESSION_COOKIE
    except ImportError as exc:
        raise DevSessionError(
            f"cannot import the minting recipe from {REPO_ROOT} ({exc}); the console needs its "
            "runtime deps (PyYAML + cryptography) and must be run against a full checkout"
        ) from exc

    private_key, public_key = generate_rsa_keypair()
    kid = console_kid_for(public_key)
    jwks = jwks_for_keys([(kid, public_key)])
    token, _claims = issue_console_session_token(
        private_key,
        kid=kid,
        tenant_id=tenant,
        subject_id=email,
        email=email,
        name=email.split("@", 1)[0],
        role="root_admin",
        now=int(time.time()),
        ttl=int(ttl),
    )

    runtime.mkdir(parents=True, exist_ok=True)
    mirror = runtime / "auth-gate-jwks.json"
    mirror.write_text(json.dumps(jwks, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    return {
        "runtime_dir": str(runtime),
        "jwks_file": str(mirror),
        "jwks": jwks,
        "kid": kid,
        "cookie_name": SESSION_COOKIE,
        "cookie_value": token,
        "email": email,
        "tenant": tenant,
        "ttl": int(ttl),
        "surface": surface,
        "registry_file": str(runtime / "registry-promoted.yaml"),
        "state_file": str(runtime / "surface-state.json"),
        "port": DEFAULT_PORT,
    }


def validator_verdict(payload: Any) -> tuple[int, str, str]:
    """Run the repo's own JWKS validator over ``payload`` on stdin."""
    validator = REPO_ROOT / VALIDATOR_REL
    if not validator.is_file():
        raise DevSessionError(f"the mirror validator is missing: {validator}")
    try:
        proc = subprocess.run(
            [sys.executable, str(validator), "check-jwks", "--root", str(REPO_ROOT)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=180,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise DevSessionError(f"cannot run {validator}: {exc}") from exc
    return proc.returncode, proc.stdout, proc.stderr


def self_check(jwks: dict[str, Any]) -> str:
    """Prove the minted mirror is one the console can verify — and that the check can fail.

    Both verdicts are read by CAPTURING the validator's output and testing
    containment here: a piped ``grep -q`` exits on its first match and can kill
    the producer (SIGPIPE), which is the defect ``scripts/check-verdict-contains.sh``
    exists for (#852).
    """
    rc, out, err = validator_verdict(jwks)
    summary = out.strip()
    if rc != 0 or "keys=" not in summary:
        raise DevSessionError(
            "the repo's own JWKS validator refused the minted mirror "
            f"(rc {rc}): {err.strip() or summary or 'no output'}"
        )

    rc_neg, out_neg, err_neg = validator_verdict({"keys": []})
    if rc_neg != 1 or "REFUSED" not in err_neg:
        raise DevSessionError(
            "the validator's negative control did not hold: an empty key set answered "
            f"rc {rc_neg} (expected 1 REFUSED), so 'the mirror validates' proves nothing "
            f"— stdout {out_neg.strip()!r} stderr {err_neg.strip()!r}"
        )
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="portal-dev-session.py",
        description="mint the offline console (fleet SPoG) dev session — issue #732",
    )
    parser.add_argument("--runtime-dir", default="", help="where the minted files go (never the repo)")
    parser.add_argument("--email", default=DEFAULT_EMAIL, help="the session identity (reserved domain only)")
    parser.add_argument("--tenant", default=DEFAULT_TENANT, help="the tenant id the session is scoped to")
    parser.add_argument("--ttl", type=int, default=DEFAULT_TTL, help="session lifetime in seconds")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="the port the printed curl lines use")
    parser.add_argument("--surface", default=SURFACE, help="the surface to promote in the registry copy")
    parser.add_argument("--json", action="store_true", help="print one JSON object on stdout (for a gate)")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    email = args.email.strip().lower()

    try:
        refuse_deployed_env()
        if not reserved_identity(email):
            raise DevSessionError(
                f"--email {args.email!r} is not under a reserved RFC 2606 domain "
                f"({', '.join(RESERVED_DOMAINS)}): this helper mints a DEV session and refuses "
                "to mint a production-shaped identity"
            )
        if args.ttl <= 0:
            raise DevSessionError(f"--ttl must be positive, got {args.ttl}")
        if not (REPO_ROOT / MAIN_REL).is_file():
            raise DevSessionError(
                f"{REPO_ROOT} does not look like the agent-orchestrator checkout "
                f"(no {MAIN_REL})"
            )
        runtime = resolve_runtime_dir(args.runtime_dir or None)
        registry = write_promoted_registry(runtime, surface=args.surface)
        session = mint_session(runtime, email=email, tenant=args.tenant, ttl=args.ttl, surface=args.surface)
        session["registry_file"] = str(registry)
        session["port"] = int(args.port)
        validator = self_check(session["jwks"])
    except DevSessionError as exc:
        print(f"portal-dev-session: CANNOT-ASSESS — {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"portal-dev-session: NOT-OK — {exc}", file=sys.stderr)
        return 1

    cookie = f"{session['cookie_name']}={session['cookie_value']}"
    if args.json:
        payload = {key: value for key, value in session.items() if key != "jwks"}
        payload["validator"] = validator
        print(json.dumps(payload, sort_keys=True))
        return 0

    port = session["port"]
    print("== portal offline dev session — DEV STOPGAP, NOT the production surface (#732) ==")
    print(f"runtime dir   {session['runtime_dir']}")
    print(f"identity      {session['email']}  (ROOT_ADMIN_EMAILS; the only allowlisted session)")
    print(f"tenant        {session['tenant']}   ttl {session['ttl']}s")
    print(f"registry      {REGISTRY_REL} -> {session['registry_file']}")
    print(f"              surfaces.{session['surface']}.default: on")
    print(f"jwks mirror   {session['jwks_file']}  kid={session['kid']}")
    print(f"validator     OK  {validator}  ({VALIDATOR_REL} check-jwks)")
    print("validator neg an empty key set is REFUSED (rc 1) — the check above can fail")
    print(f"cookie        {session['cookie_name']}")
    print("private key   never written: held in memory for the mint, then dropped")
    print("")
    print("# 1. run the console FROM THE REPO ROOT (the module resolves `portal` against $PWD)")
    print(f"export PORTAL_AUTH_GATE_JWKS_FILE={session['jwks_file']}")
    print(f"export ROOT_ADMIN_EMAILS={session['email']}")
    print(f"export AO_SURFACE_REGISTRY={session['registry_file']}")
    print(f"export AO_SURFACE_STATE={session['state_file']}")
    print(f"python3 -m portal.server.main --host 127.0.0.1 --port {port}")
    print("")
    print("# 2. from a second shell: the page and the API, with the session the server accepts")
    print(f"curl -i -H 'Cookie: {cookie}' http://127.0.0.1:{port}/views/fleet.html")
    print(f"curl -sS -H 'Cookie: {cookie}' http://127.0.0.1:{port}/api/fleet/snapshot")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
