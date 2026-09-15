#!/usr/bin/env python3
"""The portal console's auth-gate environment, as a validated declaration (#730).

The console fails **closed**: ``portal/server/sso.py`` refuses every session
until a mirror of the OS auth gate's published JWKS is present, so a deploy that
never supplies that mirror serves nothing, however green its healthcheck is. This
module is the deploy side of that contract — it reads the one declaration
(``infra/portal/auth-env.json``) and refuses, by name, every way the declaration
can stop matching the code it feeds:

* ``declaration-schema``          — a required field is absent, a delivery is
  unknown, or one env/secret is declared twice;
* ``env-not-read-by-the-console`` — the declaration names an env the console
  never reads, so it would be supplied to no effect;
* ``constant-drift``              — the env and the code constant disagree;
* ``undeclared-console-env``      — ``sso.py`` reads an env that is neither
  declared here nor exempted with a reason, so a new variable cannot ship
  silently unsupplied;
* ``delivery-shape``              — a ``*_FILE`` env not delivered as a mounted
  file (the console OPENS that variable as a pathname, so a payload delivered as
  its value makes the console exit 1 at boot), or a non-``*_FILE`` env delivered
  as one;
* ``pinned-secret-version``       — a mirror pinned to a version number, which
  turns a key rollover into a redeploy;
* ``mount-dir-not-absolute`` / ``mount-dir-reserved`` / ``filename-not-bare``  —
  a mount the container runtime cannot honour;
* ``projection-missing`` / ``env-name-restated`` — the Terraform module restates
  the env names instead of projecting the declaration, which is exactly the drift
  the declaration exists to prevent;
* ``secret-key-ref-missing`` / ``volume-mount-missing`` /
  ``secret-accessor-missing``    — a projection that would not reach the
  container, or a runtime identity that could not read the secret;
* ``secret-material``             — a JWKS payload or a private key under
  ``infra/`` or ``portal/`` (GR-6);
* ``secret-version-resource``     — Terraform owning a secret *version*: the
  value would then live in this repository's state;
* ``literal-value-for-declared-env`` — a value (an allowlist, a payload) written
  where the declaration promises a reference.

Nothing here reads, prints or writes a secret: the declaration carries secret
IDS, and the only values this module looks for are the ones it must refuse.

CLI: ``python3 infra/portal/auth_env.py check [--root DIR]``
Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

SCHEMA = "portal-auth-env/v1"

#: The declaration, the module that projects it, and the code it must match.
DECLARATION_REL = "infra/portal/auth-env.json"
MODULE_REL = "infra/terraform/modules/web-surface/main.tf"
SSO_REL = "portal/server/sso.py"

#: The one expression proving the module READS the declaration rather than
#: restating it. `path.module`, not a repo-relative path: the module must load
#: the file that ships beside it, wherever the root module is invoked from.
PROJECTION_READ = 'file("${path.module}/auth-env.json")'

#: The two trees the acceptance criterion scopes (`git grep ... -- infra/ portal/`),
#: and the two names it greps for: the file-delivered mirror and the allowlist.
SCAN_DIRS = ("infra", "portal")
ACCEPTANCE_NAMES = ("PORTAL_AUTH_GATE_JWKS", "ROOT_ADMIN_EMAILS")

#: Where a declaration may not mount. The repository root and the pseudo
#: filesystems the kernel owns cannot hold an app mount at all, and a writable
#: scratch path is not a home for a trust set (the workload could replace it).
RESERVED_MOUNT_TREES = ("/dev", "/proc", "/sys", "/boot", "/tmp")
RESERVED_MOUNT_EXACT = ("/",)

#: The two delivery shapes: a mounted file, or an injected value.
DELIVERIES = ("file", "value")

#: Domains and markers that make a value visibly a placeholder (RFC 2606 and the
#: repository's own convention in scripts/check-secrets.sh). A placeholder is
#: documentation; anything else is a value, and a value is the finding.
PLACEHOLDER_MARKERS = (
    "example.com",
    "example.org",
    "example.net",
    "example.invalid",
    "localhost",
    "placeholder",
    "dummy",
    "sample",
    "changeme",
    "redacted",
    "<",
)

#: Shell/compose/compose-style indirection: the value comes from the environment
#: or a secret reference, so the file carries no value of its own.
INDIRECTION_MARKERS = ("${", "$(", "--data-file=-", "value_source", "secret_key_ref", "item.value")

#: Material that must never be in this repository, with the reason it is refused.
#: The Terraform shapes are spelled in two pieces so this detector cannot match
#: its own source: the pattern text is exactly what it refuses in a tree.
SECRET_MATERIAL = (
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY"), "a private key"),
    (re.compile(r'"kty"\s*:'), "JWKS key material (a payload, not a reference)"),
    (re.compile(r"\bkty\s*=\s*[\"']RSA[\"']"), "JWKS key material (a payload, not a reference)"),
    (
        re.compile(r'resource\s+"google_secret_manager_' + 'secret_version"'),
        "a Terraform secret VERSION (the value would live in state)",
    ),
    (re.compile(r"\bsecret_" + r"data\b\s*="), "a Terraform secret payload"),
)

_ENV_ASSIGN = re.compile(r"([A-Z][A-Z0-9_]{2,})\s*([:=])\s*(\S*)")


class AuthEnvError(Exception):
    """The declaration (or the tree it describes) cannot be read at all."""


# --- reading ----------------------------------------------------------------


def _text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise AuthEnvError(f"{path} cannot be read: {exc}") from exc


def load_declaration(root: Path) -> Mapping[str, Any]:
    """The declaration at ``<root>/infra/portal/auth-env.json``."""
    path = Path(root) / DECLARATION_REL
    try:
        document = json.loads(_text(path))
    except ValueError as exc:
        raise AuthEnvError(f"{DECLARATION_REL} is not valid JSON: {exc}") from exc
    if not isinstance(document, Mapping):
        raise AuthEnvError(f"{DECLARATION_REL} is not a JSON object")
    return document


def declaration_entries(declaration: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    entries = declaration.get("secrets")
    if not isinstance(entries, list):
        return []
    return [entry for entry in entries if isinstance(entry, Mapping)]


def entry_for(declaration: Mapping[str, Any], env: str) -> Mapping[str, Any]:
    """The declared entry for ``env`` (raises rather than guessing)."""
    for entry in declaration_entries(declaration):
        if str(entry.get("env") or "") == env:
            return entry
    raise AuthEnvError(f"{DECLARATION_REL} declares no entry for {env}")


def resolve_mount_path(entry: Mapping[str, Any]) -> str:
    """Where the platform places a file-delivered secret (one value, derived)."""
    mount_dir = str(entry.get("mount_dir") or "").rstrip("/")
    return f"{mount_dir}/{entry.get('filename')}"


def sso_env_constants(root: Path) -> dict[str, str]:
    """The env names ``portal/server/sso.py`` reads, parsed from the source.

    Read from the module (not restated here) so a rename in the console is a
    finding rather than a silent mismatch: the console is the authority on what
    it reads, and this declaration must follow it.
    """
    source = _text(Path(root) / SSO_REL)
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise AuthEnvError(f"{SSO_REL} does not parse: {exc}") from exc
    constants: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id.endswith("_ENV"):
                value = node.value
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    constants[target.id] = value.value
    return constants


def scanned_files(root: Path) -> list[str]:
    """The repo's content under the scanned trees — committed AND about to be.

    ``git ls-files --cached --others --exclude-standard`` is exactly "what this
    checkout would commit": a lane is measured before it commits, and a
    gitignored scratch file (a local ``.terraform``, a ``__pycache__``) is not
    mistaken for repository content. Outside a git worktree the filesystem is
    walked instead, so a scratch copy is still measured rather than silently
    passing as empty.
    """
    try:
        out = subprocess.run(
            [
                "git", "-C", str(root), "ls-files",
                "--cached", "--others", "--exclude-standard", "--",
                *SCAN_DIRS,
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return _walk_scanned(root)
    files = [line for line in out.splitlines() if line.strip()]
    return files if files else _walk_scanned(root)


def _walk_scanned(root: Path) -> list[str]:
    """A filesystem walk of the scanned trees (the fallback for a non-repo copy)."""
    found: list[str] = []
    for top in SCAN_DIRS:
        base = Path(root) / top
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(root).as_posix()
            if any(part in (".git", "vendor", "__pycache__", ".terraform") for part in rel.split("/")):
                continue
            found.append(rel)
    return found


# --- validation -------------------------------------------------------------


def _require(entry: Mapping[str, Any], key: str, where: str, findings: list[str]) -> str:
    value = str(entry.get(key) or "")
    if not value:
        findings.append(f"declaration-schema: {where} declares no {key}")
    return value


def validate_declaration(
    declaration: Mapping[str, Any], constants: Mapping[str, str]
) -> list[str]:
    """Every way the declaration can stop matching the console it feeds."""
    findings: list[str] = []

    if declaration.get("schema") != SCHEMA:
        findings.append(
            f"declaration-schema: schema is {declaration.get('schema')!r}, expected {SCHEMA!r}"
        )

    entries = declaration_entries(declaration)
    if not entries:
        findings.append(f"declaration-schema: {DECLARATION_REL} declares no secrets[]")
        return findings

    seen_envs: set[str] = set()
    seen_ids: dict[str, str] = {}
    for index, entry in enumerate(entries):
        where = f"secrets[{index}]"
        env = str(entry.get("env") or "")
        if not env:
            findings.append(f"declaration-schema: {where} names no env")
            continue
        where = f"{where} ({env})"

        for key in ("code_constant", "delivery", "secret_id", "version", "source"):
            _require(entry, key, where, findings)

        delivery = str(entry.get("delivery") or "")
        if delivery not in DELIVERIES:
            findings.append(
                f"declaration-schema: {where} delivery {delivery!r} is not one of {DELIVERIES}"
            )

        if env in seen_envs:
            findings.append(f"duplicate-env: {where} is declared twice")
        seen_envs.add(env)

        secret_id = str(entry.get("secret_id") or "")
        if secret_id:
            if secret_id in seen_ids:
                findings.append(
                    f"duplicate-secret-id: {secret_id} supplies both {seen_ids[secret_id]} and {env} "
                    "(one secret per env: a shared secret widens access)"
                )
            seen_ids[secret_id] = env

        version = str(entry.get("version") or "")
        if version and version != "latest":
            findings.append(
                f"pinned-secret-version: {where} pins version {version!r}; the mirror rotates on key "
                "rollover, so the reference must be latest"
            )

        # The name shape and the delivery must agree: the console OPENS a
        # `*_FILE` variable as a pathname, so a payload delivered as its value is
        # not a mirror the console can read.
        if env.endswith("_FILE") and delivery != "file":
            findings.append(
                f"delivery-shape: {where} ends in _FILE but is delivered as {delivery!r}; the console "
                "opens that variable as a PATH, so the payload must arrive as a mounted file"
            )
        if not env.endswith("_FILE") and delivery == "file":
            findings.append(
                f"delivery-shape: {where} is a mounted file but its name does not end in _FILE; a "
                "mount the code never opens supplies nothing"
            )

        if delivery == "file":
            mount_dir = str(entry.get("mount_dir") or "")
            filename = str(entry.get("filename") or "")
            for key in ("volume", "mount_dir", "filename"):
                _require(entry, key, where, findings)
            if mount_dir and not mount_dir.startswith("/"):
                findings.append(f"mount-dir-not-absolute: {where} mounts at {mount_dir!r}")
            else:
                trimmed = mount_dir.rstrip("/")
                if trimmed in RESERVED_MOUNT_EXACT or any(
                    trimmed == tree or trimmed.startswith(f"{tree}/")
                    for tree in RESERVED_MOUNT_TREES
                ):
                    findings.append(
                        f"mount-dir-reserved: {where} mounts at {mount_dir!r}, which the runtime owns; "
                        "the mount would not appear where the env points"
                    )
            if filename and ("/" in filename or filename in (".", "..")):
                findings.append(
                    f"filename-not-bare: {where} names {filename!r}; a volume item is a bare filename "
                    "inside the mount directory"
                )

        constant = str(entry.get("code_constant") or "")
        if constant:
            actual = constants.get(constant)
            if actual is None:
                findings.append(
                    f"env-not-read-by-the-console: {where} names code constant {constant}, which "
                    f"{SSO_REL} does not define"
                )
            elif actual != env:
                findings.append(
                    f"constant-drift: {where} declares {env} for {constant}, but {SSO_REL} defines "
                    f"{constant} = {actual!r}"
                )

    # The closed world: every env the console reads is declared or exempted WITH
    # A REASON, so adding a variable to the console cannot ship unsupplied.
    declared_constants = {str(entry.get("code_constant") or "") for entry in entries}
    exempt: dict[str, str] = {}
    for entry in declaration.get("not_supplied") or []:
        if not isinstance(entry, Mapping):
            findings.append("declaration-schema: not_supplied[] holds something that is not an object")
            continue
        constant = str(entry.get("code_constant") or "")
        reason = str(entry.get("reason") or "")
        if not constant or constant not in constants:
            findings.append(
                f"declaration-schema: not_supplied[] exempts {constant!r}, which {SSO_REL} does not define"
            )
            continue
        if len(reason) < 40:
            findings.append(
                f"declaration-schema: not_supplied[] exempts {constant} without a reason"
            )
        exempt[constant] = reason
    for name in sorted(constants):
        if name in declared_constants or name in exempt:
            continue
        findings.append(
            f"undeclared-console-env: {SSO_REL} reads {name} ({constants[name]!r}); every variable the "
            f"console reads must be declared in {DECLARATION_REL} or exempted in not_supplied[] with a reason"
        )
    for constant in sorted(set(declared_constants) & set(exempt)):
        findings.append(
            f"declaration-schema: {constant} is both declared and exempted (one or the other)"
        )

    return findings


def validate_projection(module_text: str, declared_envs: Sequence[str]) -> list[str]:
    """The Terraform module must PROJECT the declaration, never restate it."""
    findings: list[str] = []

    if PROJECTION_READ not in module_text:
        findings.append(
            f"projection-missing: {MODULE_REL} does not read the declaration ({PROJECTION_READ}); the "
            "env names would then have to be written twice, which is the drift the declaration prevents"
        )

    for env in sorted(declared_envs):
        if re.search(rf'name\s*=\s*"{re.escape(env)}"', module_text):
            findings.append(
                f"env-name-restated: {MODULE_REL} assigns {env} literally; the module must project the "
                "declaration (name = item.value.env)"
            )

    for needle, finding in (
        (
            r"\bsecret_key_ref\"?\s*\{",
            "secret-key-ref-missing: the module injects no secret version (no value_source/secret_key_ref block)",
        ),
        (
            r"\bvolume_mounts\"?\s*\{",
            "volume-mount-missing: the module mounts no secret, so a *_FILE env would point at nothing",
        ),
        (
            r"\bvolumes\"?\s*\{",
            "volume-mount-missing: the module declares no secret volume, so the mirror never reaches the container",
        ),
        (
            r'"roles/secretmanager\.secretAccessor"',
            "secret-accessor-missing: the runtime identity is granted no read on the secrets; the revision would fail with PERMISSION_DENIED",
        ),
    ):
        if not re.search(needle, module_text):
            findings.append(finding)

    return findings


def _expectations(
    entries: Sequence[Mapping[str, Any]], extra_envs: Sequence[str]
) -> tuple[dict[str, Mapping[str, Any]], list[str]]:
    """The declared entry per env, plus the names the acceptance criterion greps."""
    by_env: dict[str, Mapping[str, Any]] = {
        str(entry.get("env") or ""): entry for entry in entries if entry.get("env")
    }
    envs = [*by_env, *[env for env in extra_envs if env not in by_env]]
    return by_env, envs


def _scan_lines(
    rel: str, text: str, by_env: Mapping[str, Mapping[str, Any]], envs: Sequence[str]
) -> list[str]:
    """The material findings for one file's text (shared by both scan verbs)."""
    findings: list[str] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if len(line) > 4000:
            continue
        for pattern, why in SECRET_MATERIAL:
            if pattern.search(line):
                findings.append(f"secret-material: {rel}:{lineno} carries {why} (GR-6)")
        for env in envs:
            if env not in line:
                continue
            entry = by_env.get(env)
            declared_path = (
                resolve_mount_path(entry)
                if entry is not None and entry.get("delivery") == "file"
                else ""
            )
            for match in _ENV_ASSIGN.finditer(line):
                if match.group(1) != env:
                    continue
                value = match.group(3)
                if not value:
                    continue
                if any(marker in value for marker in INDIRECTION_MARKERS):
                    continue
                if value.startswith("/"):
                    # The file env carries the mount path, and nothing else: a
                    # different path is a mirror the console cannot open.
                    if declared_path and value != declared_path:
                        findings.append(
                            f"mount-path-drift: {rel}:{lineno} points {env} at {value!r}, but "
                            f"{DECLARATION_REL} mounts it at {declared_path!r} — the console would "
                            "fail to read the mirror at boot"
                        )
                    continue
                lowered = value.lower()
                if any(marker in lowered for marker in PLACEHOLDER_MARKERS):
                    continue
                if any(marker in lowered for marker in ("{", "keys", "kty", "eyj")):
                    findings.append(
                        f"literal-value-for-declared-env: {rel}:{lineno} assigns a PAYLOAD to {env}; "
                        "the declaration promises a mounted mirror, never a value (GR-6)"
                    )
                    continue
                if entry is not None and entry.get("delivery") == "value":
                    findings.append(
                        f"literal-value-for-declared-env: {rel}:{lineno} assigns a value to {env}; "
                        "the declaration promises a secret reference, never a value (GR-6)"
                    )
    return findings


def scan_secret_material(
    root: Path, entries: Sequence[Mapping[str, Any]], extra_envs: Sequence[str]
) -> tuple[list[str], int]:
    """Refuse a value where the tree promises a reference (GR-6).

    Three shapes are refused: material that must never be here at all (a private
    key, a JWKS payload, a Terraform secret version); a value written where the
    declaration promises a secret reference; and a ``*_FILE`` variable pointed at
    a path the declaration does not mount — the drift that makes the console exit
    1 at boot while every other check reads green. A placeholder or an
    indirection (``${VAR}``, a secret reference) is wiring, which is what the
    docs and the compose route legitimately carry.
    """
    by_env, envs = _expectations(entries, extra_envs)
    findings: list[str] = []
    scanned = 0
    for rel in scanned_files(root):
        path = Path(root) / rel
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        scanned += 1
        findings.extend(_scan_lines(rel, text, by_env, envs))
    return findings, scanned


def scan_one(root: Path, rel: str) -> tuple[list[str], int]:
    """The material findings for ONE file, measured against the real declaration.

    The negative controls drive this: a single line changed in a copy, refused by
    name, with its unmodified twin accepted by the same invocation.
    """
    declaration = load_declaration(root)
    by_env, envs = _expectations(declaration_entries(declaration), ACCEPTANCE_NAMES)
    path = Path(rel)
    if not path.is_absolute():
        path = Path(root) / rel
    if not path.is_file():
        raise AuthEnvError(f"{rel} does not exist")
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise AuthEnvError(f"{rel} cannot be read: {exc}") from exc
    return _scan_lines(rel, text, by_env, envs), 1


def check_pair(root: Path, declaration_path: Path, module_path: Path) -> list[str]:
    """Validate a declaration/module PAIR, against the real console's constants.

    ``root`` supplies the console source (the authority on which env names are
    read); the declaration and the module come from wherever the caller points,
    which is how a control measures one mutated file without copying a tree.
    """
    try:
        declaration = json.loads(_text(declaration_path))
    except ValueError as exc:
        return [f"declaration-schema: {declaration_path} is not valid JSON: {exc}"]
    if not isinstance(declaration, Mapping):
        return [f"declaration-schema: {declaration_path} is not a JSON object"]

    findings = validate_declaration(declaration, sso_env_constants(root))
    envs = [str(entry.get("env") or "") for entry in declaration_entries(declaration) if entry.get("env")]
    findings.extend(validate_projection(_text(module_path), envs))
    return findings


def env_name_hits(root: Path, names: Sequence[str]) -> list[str]:
    """The acceptance criterion's own command, over ``infra/`` + ``portal/``.

    ``git grep -nE "<names>" -- infra/ portal/`` — run for real (untracked files
    included, so a lane is measured before it commits), and its hit count
    reported as evidence that the wiring, and only the wiring, names them.
    """
    pattern = "|".join(re.escape(name) for name in names)
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "grep", "--untracked", "-nE", pattern, "--", *SCAN_DIRS],
            capture_output=True,
            text=True,
        ).stdout
    except OSError:
        return []
    return [line for line in out.splitlines() if line.strip()]


def check_tree(root: Path) -> tuple[list[str], list[str]]:
    """Every finding for one tree, plus the evidence lines for the clean case."""
    findings: list[str] = []

    declaration = load_declaration(root)
    constants = sso_env_constants(root)
    findings.extend(validate_declaration(declaration, constants))

    entries = declaration_entries(declaration)
    envs = [str(entry.get("env") or "") for entry in entries if entry.get("env")]
    module = Path(root) / MODULE_REL
    if module.is_file():
        findings.extend(validate_projection(_text(module), envs))
    else:
        findings.append(f"projection-missing: {MODULE_REL} is absent")

    material, scanned = scan_secret_material(root, entries, ACCEPTANCE_NAMES)
    findings.extend(material)

    # The acceptance criterion greps both names, including the exempted inline
    # one, so the gate measures the same string.
    hits = env_name_hits(root, ACCEPTANCE_NAMES)

    file_entries = [entry for entry in entries if entry.get("delivery") == "file"]
    evidence = [
        f"declaration: {len(entries)} secret(s) — "
        f"{len(file_entries)} mounted file(s), {len(entries) - len(file_entries)} injected value(s)",
        f"the console's environment is closed: {SSO_REL} reads {len(constants)} variable(s), "
        f"{len(entries)} declared + {len(constants) - len(entries)} exempted with a reason",
        f"no value where a reference is promised: {scanned} file(s) under "
        f"{'/ + '.join(SCAN_DIRS)}/ scanned, no secret material",
        f"the acceptance grep names them {len(hits)} time(s) in infra/ + portal/, all wiring",
    ]
    for entry in file_entries:
        evidence.append(
            f"{entry.get('env')} <- projects to {resolve_mount_path(entry)} from secret "
            f"{entry.get('secret_id')} (version {entry.get('version')})"
        )
    return findings, evidence


def describe(root: Path) -> list[str]:
    """The wiring, one line per declared secret (evidence without a value)."""
    declaration = load_declaration(root)
    lines: list[str] = []
    for entry in declaration_entries(declaration):
        env = str(entry.get("env") or "")
        if entry.get("delivery") == "file":
            target = resolve_mount_path(entry)
        else:
            target = "the container environment (injected value)"
        lines.append(
            f"{env} <- secret {entry.get('secret_id')} (version {entry.get('version')}) -> {target}"
        )
    return lines


def check_jwks(root: Path) -> int:
    """Validate a key set read on STDIN with the CONSOLE'S own predicate.

    The mirror job calls this before publishing, so the bytes that reach Secret
    Manager are bytes the console can actually verify against: a key set with no
    usable RSA signing key is refused here, by name, instead of being published
    and then refusing every session. Only the key ids and a digest are printed —
    never the payload.
    """
    sys.path.insert(0, str(root))
    from portal.server.sso import trusted_keys_from_jwks

    raw = sys.stdin.read()
    try:
        document = json.loads(raw)
    except ValueError as exc:
        print(f"REFUSED — the payload is not valid JSON: {exc}", file=sys.stderr)
        return 1
    if not isinstance(document, Mapping):
        print("REFUSED — the payload is not a JSON object", file=sys.stderr)
        return 1
    usable = trusted_keys_from_jwks(document)
    if not usable:
        print(
            "REFUSED — the key set carries no usable RSA signing key "
            f"({len(document.get('keys') or [])} key(s) present); the console would refuse every session",
            file=sys.stderr,
        )
        return 1
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    print(f"keys={len(usable)} kids={','.join(sorted(usable))} sha256={digest}")
    return 0


# --- CLI --------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="the portal auth-gate env declaration (#730)")
    parser.add_argument(
        "command",
        choices=("check", "check-jwks", "check-declaration", "scan-file", "describe"),
        help="check the tree; check a declaration/module pair; scan one file; "
        "validate a key set on stdin; print the wiring",
    )
    parser.add_argument("--root", default=".", help="the tree the console source is read from")
    parser.add_argument("--declaration", default="", help="check-declaration: the declaration to validate")
    parser.add_argument("--module", default="", help="check-declaration: the Terraform module to validate")
    parser.add_argument("--file", default="", help="scan-file: the single file to scan")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    if args.command == "check-jwks":
        try:
            return check_jwks(root)
        except (AuthEnvError, ImportError) as exc:
            print(f"  CANNOT-ASSESS  {exc}", file=sys.stderr)
            return 2

    try:
        if args.command == "describe":
            for line in describe(root):
                print(f"  {line}")
            return 0

        if args.command == "check-declaration":
            if not args.declaration or not args.module:
                print("  CANNOT-ASSESS  --declaration and --module are both required", file=sys.stderr)
                return 2
            findings = check_pair(root, Path(args.declaration), Path(args.module))
        elif args.command == "scan-file":
            if not args.file:
                print("  CANNOT-ASSESS  --file is required", file=sys.stderr)
                return 2
            findings, _ = scan_one(root, args.file)
        else:
            findings, evidence = check_tree(root)
    except AuthEnvError as exc:
        print(f"  CANNOT-ASSESS  {exc}", file=sys.stderr)
        return 2

    if findings:
        for finding in findings:
            print(f"  FAIL  {finding}", file=sys.stderr)
        print(f"portal-auth-env: {len(findings)} finding(s)", file=sys.stderr)
        return 1

    if args.command == "check":
        for line in evidence:
            print(f"  OK    {line}")
    else:
        print("  OK    nothing refused")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
