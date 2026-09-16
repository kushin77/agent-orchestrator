"""The console's auth-gate env, supplied the way the deploy supplies it (#730).

Issue #730's post-deploy criterion is "a signed-in session reaches
``/api/console/me`` (HTTP 200)". Nothing can be deployed from a test, so this
reproduces the whole supply chain **offline**, in the order the platform performs
it, and asserts that criterion at the end of it:

1. the OS auth gate publishes its key set — the suite's :class:`FakeAuthGate`,
   the same stand-in whose tokens every console in this suite verifies;
2. ``infra/portal/mirror-auth-gate-jwks.sh`` publishes those bytes into Secret
   Manager — here through a stub ``gcloud`` that records argv and stdin, so the
   publish path is measured rather than assumed (the payload must arrive on
   STDIN: a ``--data-file=<payload>`` form is indistinguishable, to a mechanical
   scan, from a hardcoded credential);
3. the platform materialises the secret as the volume the Terraform module
   declares, at the path ``infra/terraform/modules/web-surface/auth-env.json``
   names;
4. the console boots with **only** the declaration's env, reading the mirror from
   that mounted file;
5. a real ``os-session-token`` reaches ``GET /api/console/me`` with HTTP 200,
   and the allowlist secret — not the token's ``role`` claim — decides
   ``superAdmin``.

One thing cannot be reproduced here: the declaration mounts the mirror at
``/etc/ao/auth-gate-jwks.json`` *inside the container*, and a test cannot write
there. The mount is therefore rebased under ``tmp_path`` while the declaration's
path SHAPE (``mount_dir`` / ``filename``, and the env that must equal their
concatenation) is preserved — and ``scripts/check-portal-auth-env.sh`` proves the
Terraform projection that puts the file there in a real deployment.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from conftest import AUTH_GATE, login_as

from infra.portal import auth_env
from portal.server.app import build_app
from portal.server.sso import ConsoleAuthError, ConsoleSso

REPO_ROOT = Path(__file__).resolve().parents[2]

#: The super-admin the allowlist SECRET names. A placeholder domain (RFC 2606):
#: the test publishes this value through the same stub the mirror uses, so the
#: allowlist is supplied, never baked in.
ALLOWLISTED_EMAIL = "root@example.com"

#: The member the allowlist secret does NOT name (it must not be super_admin).
UNLISTED_EMAIL = "alice@acme.example.com"

_MIRROR = REPO_ROOT / "infra" / "portal" / "mirror-auth-gate-jwks.sh"

#: A stub `gcloud`: records argv and stdin, and answers the three calls the
#: mirror makes. It reads stdin for `versions add` only, so nothing blocks.
_STUB_GCLOUD = """#!/usr/bin/env bash
printf '%s\\n' "$*" >>"$STUB_RECORD/argv.log"
if [ "$1" = "secrets" ] && [ "$2" = "versions" ] && [ "$3" = "add" ]; then
  cat >"$STUB_RECORD/stdin.log"
  printf 'projects/stub/secrets/%s/versions/7\\n' "$4"
  exit 0
fi
if [ "$1" = "secrets" ] && [ "$2" = "describe" ]; then
  printf 'name: projects/stub/secrets/%s\\n' "$3"
  exit 0
fi
echo "stub gcloud: unexpected invocation: $*" >&2
exit 1
"""


class SecretManager:
    """A stub Secret Manager: one directory per secret, one file per version."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.bin = root / "bin"
        self.bin.mkdir(exist_ok=True)
        stub = self.bin / "gcloud"
        stub.write_text(_STUB_GCLOUD, encoding="utf-8")
        stub.chmod(0o755)

    def env(self, record: Path) -> dict[str, str]:
        record.mkdir(parents=True, exist_ok=True)
        environ = dict(os.environ)
        environ["PATH"] = f"{self.bin}:{environ.get('PATH', '')}"
        environ["STUB_RECORD"] = str(record)
        return environ

    def publish(self, secret_id: str, payload: str) -> None:
        """Store a version exactly as the mirror's publish path does (stdin)."""
        record = self.root / "records" / secret_id
        subprocess.run(
            ["gcloud", "secrets", "versions", "add", secret_id, "--data-file=-"],
            input=payload,
            text=True,
            env=self.env(record),
            check=True,
            capture_output=True,
        )
        self.store(secret_id, payload)

    def store(self, secret_id: str, payload: str) -> None:
        """Write the version file the platform reads back."""
        versions = self.root / "secrets" / secret_id
        versions.mkdir(parents=True, exist_ok=True)
        (versions / "7").write_text(payload, encoding="utf-8")

    def access(self, secret_id: str) -> str:
        """Read a version back — what the platform mounts/injects."""
        return (self.root / "secrets" / secret_id / "7").read_text(encoding="utf-8")


def declaration() -> dict:
    return dict(auth_env.load_declaration(REPO_ROOT))


def entry(env_name: str) -> dict:
    return dict(auth_env.entry_for(declaration(), env_name))


def _mount(secret_manager: SecretManager, tmp_path: Path, env_name: str) -> Path:
    """Materialise a file-delivered secret where the declaration mounts it."""
    declared = entry(env_name)
    container = tmp_path / "container"
    mount_dir = container / str(declared["mount_dir"]).lstrip("/")
    mount_dir.mkdir(parents=True, exist_ok=True)
    mounted = mount_dir / str(declared["filename"])
    mounted.write_text(secret_manager.access(str(declared["secret_id"])), encoding="utf-8")
    # The env must be the concatenation the Terraform projects, shape preserved
    # from the container's absolute path.
    assert str(mounted).endswith(auth_env.resolve_mount_path(declared))
    return mounted


def test_the_declaration_names_the_env_the_console_reads() -> None:
    """The declaration and the code cannot drift apart silently."""
    from portal.server.sso import JWKS_FILE_ENV, ROOT_ADMIN_ENV

    jwks = entry("PORTAL_AUTH_GATE_JWKS_FILE")
    allowlist = entry("ROOT_ADMIN_EMAILS")
    assert jwks["env"] == JWKS_FILE_ENV
    assert allowlist["env"] == ROOT_ADMIN_ENV
    # The mirror is delivered as a FILE: the console opens that variable.
    assert jwks["delivery"] == "file"
    assert auth_env.resolve_mount_path(jwks) == "/etc/ao/auth-gate-jwks.json"
    assert jwks["version"] == "latest"


def test_the_mirror_reaches_the_console_and_a_session_reaches_console_me(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The issue's post-deploy criterion: a signed-in session gets HTTP 200."""
    secrets = SecretManager(tmp_path / "gsm")
    jwks = entry("PORTAL_AUTH_GATE_JWKS_FILE")
    allowlist = entry("ROOT_ADMIN_EMAILS")

    # (1) the gate's published key set, to a file (offline).
    published = tmp_path / "jwks.json"
    published.write_text(json.dumps(AUTH_GATE.jwks), encoding="utf-8")

    # (2) the mirror publishes it — the real script, the REAL declaration's
    # secret id, a stub gcloud.
    record = tmp_path / "record"
    result = subprocess.run(
        [
            "bash", str(_MIRROR),
            "--jwks-file", str(published),
            "--project", "stub-project",
            "--apply",
        ],
        text=True,
        env=secrets.env(record),
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert "keys=1" in result.stdout
    argv = (record / "argv.log").read_text(encoding="utf-8")
    assert str(jwks["secret_id"]) in argv
    assert "--data-file=-" in argv
    # The payload reached Secret Manager on stdin, and never on argv.
    assert '"kty"' in (record / "stdin.log").read_text(encoding="utf-8")
    assert "kty" not in argv
    assert "kty" not in result.stdout
    secrets.store(str(jwks["secret_id"]), (record / "stdin.log").read_text(encoding="utf-8"))

    # (3) the second secret, published through the same stub: the allowlist.
    secrets.publish(str(allowlist["secret_id"]), ALLOWLISTED_EMAIL)

    # (4) the platform's delivery: a mounted file for the mirror, an injected
    # value for the allowlist — exactly what the Terraform declares.
    mounted = _mount(secrets, tmp_path, "PORTAL_AUTH_GATE_JWKS_FILE")
    monkeypatch.setenv(str(jwks["env"]), str(mounted))
    monkeypatch.setenv(str(allowlist["env"]), secrets.access(str(allowlist["secret_id"])))
    monkeypatch.delenv("PORTAL_AUTH_GATE_JWKS", raising=False)

    app = build_app(sso=ConsoleSso())

    # (5) a real auth-gate session reaches the console.
    api = login_as(app, UNLISTED_EMAIL, "acme")
    status, payload = api.get("/api/console/me")
    assert status == 200, payload
    assert payload["data"]["email"] == UNLISTED_EMAIL
    assert payload["data"]["superAdmin"] is False


def test_the_allowlist_secret_decides_super_admin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """super_admin comes from the supplied allowlist, never from the token."""
    secrets = SecretManager(tmp_path / "gsm")
    jwks = entry("PORTAL_AUTH_GATE_JWKS_FILE")
    allowlist = entry("ROOT_ADMIN_EMAILS")
    published = tmp_path / "jwks.json"
    published.write_text(json.dumps(AUTH_GATE.jwks), encoding="utf-8")
    secrets.publish(str(jwks["secret_id"]), published.read_text(encoding="utf-8"))
    secrets.publish(str(allowlist["secret_id"]), ALLOWLISTED_EMAIL)

    # The token claims role: user (conftest's mint defaults) and the SECRET
    # decides super_admin — the allowlist is the only source of that role.
    monkeypatch.setenv(str(jwks["env"]), str(_mount(secrets, tmp_path, "PORTAL_AUTH_GATE_JWKS_FILE")))
    monkeypatch.setenv(str(allowlist["env"]), secrets.access(str(allowlist["secret_id"])))
    app = build_app(sso=ConsoleSso())

    api = login_as(app, ALLOWLISTED_EMAIL, "acme", role="user")
    status, payload = api.get("/api/console/me")
    assert status == 200, payload
    assert payload["data"]["superAdmin"] is True


def test_without_the_mounted_mirror_the_same_session_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The wiring is load-bearing: with no mirror in place, the console serves none.

    This is the twin of the test above — same key set, same secret, same
    session — and it is what makes that one evidence rather than decoration.
    """
    jwks = entry("PORTAL_AUTH_GATE_JWKS_FILE")
    monkeypatch.setenv(str(jwks["env"]), str(tmp_path / "not-mounted.json"))
    with pytest.raises(ConsoleAuthError) as refusal:
        ConsoleSso()
    assert "cannot be read" in str(refusal.value)


def test_a_payload_delivered_as_a_value_is_refused_at_boot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The failure the delivery shape prevents: JSON where a path belongs."""
    jwks = entry("PORTAL_AUTH_GATE_JWKS_FILE")
    wrong = tmp_path / "mirror-as-value.json"
    wrong.write_text(json.dumps(AUTH_GATE.jwks), encoding="utf-8")
    # The file exists, but the env must point at the MOUNT; pointing the inline
    # variable at a path is the other half of the same confusion.
    monkeypatch.setenv("PORTAL_AUTH_GATE_JWKS", str(wrong))
    with pytest.raises(ConsoleAuthError) as refusal:
        ConsoleSso()
    assert "not valid JSON" in str(refusal.value)


def test_the_declaration_carries_no_value() -> None:
    """Wiring only (GR-6): the declaration names ids, never a key or an allowlist."""
    # The path is spelled out rather than read from auth_env.DECLARATION_REL: a
    # test that follows the constant cannot catch the constant pointing at a file
    # that is not there, and that is exactly the defect this lane shipped once.
    raw = (
        REPO_ROOT / "infra" / "terraform" / "modules" / "web-surface" / "auth-env.json"
    ).read_text(encoding="utf-8")
    assert "kty" not in raw
    assert "@" not in raw
    for declared in auth_env.declaration_entries(declaration()):
        assert str(declared["secret_id"])
        assert str(declared["version"]) == "latest"
