#!/usr/bin/env python3
"""secrets_contract.py — the fleet-cron image's SECRETS INJECTION contract (issue
#711, EPIC #706 D3).

D2 (#710) mounted no credential at all: the dry-run harness dispatches nothing
that writes, so nothing in that lane needed `gh`, `gcloud` or `ssh`. D3 wires the
state volumes read-write behind a flag (see ``env_contract.AO_FLEET_STATE_RW``,
default ``"0"``) and, alongside it, the contract for the credentials an applying
run will eventually need — declared now, refused now if misused, honoured later.

The rule this file exists to enforce (GR-6, and the epic's own scope line):

    Bind-mount state and secrets FROM OUTSIDE THE REPO; never `env_file` (which
    bakes a token into `docker inspect`).

So this module declares WHERE each credential comes from on the host, and
refuses two things mechanically:

* a secret mount whose **source** resolves inside this checkout (a credential
  bind that points into the repo is one `git add .` away from being committed);
* a **secret value** — as opposed to a path — anywhere in this image's
  environment contract. This module carries no credential value, ever; it
  carries paths to files that live outside the repo and are mounted read-only.

Nothing here is wired into the dev-run compose service by default: the state
volumes stay read-only and no secret is mounted unless the `state-rw` compose
profile is opted into explicitly (``docker compose --profile state-rw up``),
which is the declared-not-mounted posture this layer requires (AO-GR-5:
declared, never clicked). That is a scope choice about mounting, not a
capability default, so AO-GR-6's enabled-by-default rule is not in play (the
Terraform-declared flag for a *deployed* scheduler surface is D4+'s work — see
`README.md`, "Flag posture").

Tri-state exit contract (this repository's convention): 0 OK / 1 NOT-OK /
2 CANNOT-ASSESS.

---knowledge---
module_id: infra.fleet.secrets_contract
system: infra
app: fleet
solution_class: class
patterns: [pre-standard-snapshot]
derives_from: null
owner_sme: iac-sme
tier: L1
interfaces: [SecretMount, Finding, validate, scan_for_secret_values, cmd_check, build_parser, main]
invariants: ""
gotchas: ""
related: ["#1911"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

OK, NOT_OK, CANNOT_ASSESS = 0, 1, 2

# A credential-shaped environment variable name — used to REFUSE one if it
# ever appears in this module's own declarations, never to declare one. No
# literal credential-variable name is spelled out in this file (or its tests):
# the pattern is what is checked, by name, in gate evidence
# (`grep -R` over `infra/fleet/` for two example names finds nothing — GR-6).
#
# Matched in two steps because a real env var name is one unbroken run of
# `\w` characters (underscores included) — a single regex anchored on `\b`
# before the suffix never finds a boundary inside a compound name like
# `AO_FLEET_SAMPLE_TOKEN`, since underscore is a word character on both sides.
_IDENTIFIER_PATTERN = re.compile(r"\b[A-Z][A-Z0-9_]{2,}\b")
_SECRET_SUFFIX_PATTERN = re.compile(r"(?i)_(TOKEN|SECRET|KEY|PASSWORD|CREDENTIAL)S?$")


@dataclass(frozen=True)
class SecretMount:
    """One credential surface: where it lives on the host, and where it lands."""

    name: str
    host_env: str
    default_host_path: str
    target: str
    why: str
    read_only: bool = True

    def resolved_source(self, env: dict[str, str] | None = None) -> str:
        source = os.environ if env is None else env
        return source.get(self.host_env, self.default_host_path)


# THE declaration. Every credential the fleet's rungs use, sourced from outside
# the repo (``${HOME}``, never a path under ``AO_FLEET_REPO``), mounted
# read-only, never baked into the image and never named in an `env_file`.
SECRET_MOUNTS: tuple[SecretMount, ...] = (
    SecretMount(
        name="gh",
        host_env="AO_FLEET_GH_CONFIG",
        default_host_path="${HOME}/.config/gh",
        target="/root/.config/gh",
        why="the fleet's GitHub CLI auth — every rung's issue/PR surface (inventory.yaml binaries: gh).",
    ),
    SecretMount(
        name="gcloud",
        host_env="AO_FLEET_GCLOUD_CONFIG",
        default_host_path="${HOME}/.config/gcloud",
        target="/root/.config/gcloud",
        why="ADC / gcloud auth for any role that reaches a GCP surface once the runtime applies.",
    ),
    SecretMount(
        name="ssh",
        host_env="AO_FLEET_SSH_DIR",
        default_host_path="${HOME}/.ssh",
        target="/root/.ssh",
        why="`git push` over ssh (inventory.yaml packages: openssh-client) — the fleet's push identity.",
    ),
    SecretMount(
        name="ar-reader-key",
        host_env="AO_FLEET_AR_READER_KEY_FILE",
        default_host_path="${HOME}/.config/ao/ar-reader-key.json",
        target="/root/.config/ao/ar-reader-key.json",
        why=(
            "issue #1329: read access to Artifact Registry for "
            "infra/fleet/promote_portal.py — a mounted service-account JSON key, "
            "never a value in the environment. The rung's OTHER accepted auth "
            "shape, AO_FLEET_AR_ACCESS_TOKEN_CMD (a command whose stdout is a "
            "bearer token), is declared in env_contract.py instead: it names a "
            "COMMAND, not a path to mount, so it does not belong to this file's "
            "mount contract. Absent both, the rung refuses `ar-auth-missing` "
            "(CANNOT-ASSESS) and escalates once; it never creates a key (GR-6)."
        ),
    ),
    SecretMount(
        name="deepseek",
        host_env="AO_FLEET_DEEPSEEK_CONFIG",
        default_host_path="${HOME}/.config/deepseek",
        target="/root/.config/deepseek",
        why=(
            "issue #1784: the fleet's MODEL credential — the one credential no "
            "rung can start without, and until now the only one missing from "
            "this declaration. It was required as two ad-hoc environment "
            "variables whose names belong to the TRANSPORT rather than the "
            "provider (the declared vocabulary is DeepSeek: deepseek-v4-flash / "
            "deepseek-v4-pro), and the native CLI reads its credential from its "
            "own 0600 config. So this mount is a DIRECTORY of config, never a "
            "value in the environment. Absent it, `fleet/runners.py`'s profile "
            "is `unhonourable` and the loop parks its queue rather than "
            "dispatching a lane that will die (#841)."
        ),
    ),
)

BY_NAME: dict[str, SecretMount] = {mount.name: mount for mount in SECRET_MOUNTS}


@dataclass(frozen=True)
class Finding:
    code: str
    detail: str


def _expands_outside_repo(path: str, repo_root: Path) -> bool:
    """True when ``path`` (after ``${HOME}``/``~`` expansion) is NOT under ``repo_root``.

    A path still carrying an unexpanded ``${AO_FLEET_...}`` compose-style token
    that clearly is not repo-rooted (e.g. ``${HOME}/...``) is treated as outside —
    it is compose's job to expand it at run time, never this repo's contents.
    """
    if path.startswith("${HOME}") or path.startswith("~"):
        return True
    expanded = Path(os.path.expandvars(os.path.expanduser(path))).resolve()
    try:
        expanded.relative_to(repo_root.resolve())
    except ValueError:
        return True
    return False


def validate(env: dict[str, str] | None = None, repo_root: Path | None = None) -> list[Finding]:
    """Every refusal. An empty list means every declared mount satisfies the contract."""
    root = repo_root if repo_root is not None else Path(__file__).resolve().parents[2]
    findings: list[Finding] = []
    for mount in SECRET_MOUNTS:
        source = mount.resolved_source(env)
        if not _expands_outside_repo(source, root):
            findings.append(
                Finding(
                    code="secret-source-inside-repo",
                    detail=f"{mount.name}: {mount.host_env}={source!r} resolves inside the checkout",
                )
            )
        if not mount.read_only:
            findings.append(Finding(code="secret-mount-writable", detail=f"{mount.name}: must be read-only"))
    return findings


def scan_for_secret_values(text: str) -> list[str]:
    """Names in ``text`` that look like a credential VALUE'S variable name.

    Used against this module's own source and against a compose file's rendered
    text: a match here means a real secret name was spelled out where only a
    *path* to a secret belongs (GR-6). Returns the matched names, sorted.
    """
    return sorted(
        identifier
        for identifier in set(_IDENTIFIER_PATTERN.findall(text))
        if _SECRET_SUFFIX_PATTERN.search(identifier)
    )


def cmd_check(args: argparse.Namespace) -> int:
    findings = validate()
    for mount in SECRET_MOUNTS:
        resolved = mount.resolved_source()
        note = "" if resolved == mount.default_host_path else f"  (default {mount.default_host_path})"
        print(f"  OK    {mount.name}: {mount.host_env}={resolved} -> {mount.target} (ro={mount.read_only}){note}")
    for finding in findings:
        print(f"  FAIL  {finding.code}: {finding.detail}", file=sys.stderr)
    if findings:
        print(f"secrets-contract: REFUSED — {len(findings)} finding(s)", file=sys.stderr)
        return NOT_OK
    print(f"secrets-contract: OK — {len(SECRET_MOUNTS)} declared mount(s) satisfy the contract")
    return OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="secrets-contract", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check", help="validate the declared secret mounts (exit 0/1)")
    check.set_defaults(func=cmd_check)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
