"""Operator CLI for tenant provisioning (issue #14, work item 10).

Operator path only - deliberately NOT an HTTP endpoint. Provisioning a tenant
writes control-plane state (tenant rows, RBAC org/roles, seed packs); exposing
it anonymously would mean an unauthenticated write, and an authenticated HTTP
route would need a permission inside a tenant that does not exist yet. Later
identity phases (#35-#38) own an authenticated control-plane surface; this CLI
is the offline/operator entry point, mirroring saas-rbac's ``provision-cli.ts``
(``--dry-run`` included) adapted to Python.

Usage (from the repo root):

    python3 -m identity.onboarding.cli provision --slug acme --name "Acme Corp" \
        --owner-email admin@acme.example [--tenant-type platform] \
        [--idp-tenant-id idp-acme] [--domain acme.example.com] \
        [--dry-run] [--store PATH]

    python3 -m identity.onboarding.cli status  --slug acme [--store PATH]
    python3 -m identity.onboarding.cli ready   --slug acme [--store PATH]
    python3 -m identity.onboarding.cli jobs    --slug acme [--store PATH]
    python3 -m identity.onboarding.cli retry   --slug acme [--store PATH]
    python3 -m identity.onboarding.cli customize --slug acme --overlay FILE.yaml \
        [--store PATH]
    python3 -m identity.onboarding.cli render  --slug acme [--store PATH]

State persists to ``--store PATH`` (or ``$AO_ONBOARDING_STORE``); without it
the run is in-memory only. Exit codes: 0 ok, 1 runtime/provision failure,
2 usage error.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from identity.onboarding import customization, jobs, provisioning, ready as ready_mod
from identity.onboarding.customization import OverlayValidationError
from identity.onboarding.model import (
    BUILTIN_TENANT_TYPES,
    JOB_STATUS_COMPLETED,
    JOB_STATUS_FAILED,
    ProvisionValidationError,
)
from identity.onboarding.provisioning import ProvisionSpec
from identity.onboarding.store import FileStore, InMemoryStore

_USAGE_ERROR = 2


class _CliError(RuntimeError):
    pass


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="identity.onboarding.cli",
        description="Operator CLI for idempotent tenant provisioning (issue #14).",
    )
    # Shared option, available on the top level AND after every subcommand.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--store",
        default=None,
        help="JSON store path (or $AO_ONBOARDING_STORE); in-memory when omitted",
    )
    parser.add_argument(
        "--store",
        default=None,
        help="JSON store path (or $AO_ONBOARDING_STORE); in-memory when omitted",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_provision = sub.add_parser("provision", parents=[common], help="provision (or converge) a tenant")
    p_provision.add_argument("--slug", "--tenant", dest="slug", required=True)
    p_provision.add_argument("--name", required=True)
    p_provision.add_argument(
        "--tenant-type", default="platform", choices=list(BUILTIN_TENANT_TYPES)
    )
    p_provision.add_argument("--idp-tenant-id", default=None)
    p_provision.add_argument("--domain", default=None)
    p_provision.add_argument("--owner-email", default=None)
    p_provision.add_argument("--owner-name", default=None)
    p_provision.add_argument("--owner-role", default="owner")
    p_provision.add_argument("--role-admin-permission", default="roles:manage")
    p_provision.add_argument("--max-attempts", type=int, default=3)
    p_provision.add_argument("--dry-run", action="store_true")
    p_provision.set_defaults(func=_cmd_provision)

    for name in ("status", "ready", "retry"):
        p = sub.add_parser(name, parents=[common], help=f"{name} for a tenant")
        p.add_argument("--slug", "--tenant", dest="slug", required=True)
        p.set_defaults(func=_cmd_tenant(name))

    p_jobs = sub.add_parser("jobs", parents=[common], help="list a tenant's provisioning jobs")
    p_jobs.add_argument("--slug", "--tenant", dest="slug", required=True)
    p_jobs.set_defaults(func=_cmd_jobs)

    p_customize = sub.add_parser(
        "customize", parents=[common], help="apply a validated customization overlay"
    )
    p_customize.add_argument("--slug", "--tenant", dest="slug", required=True)
    p_customize.add_argument("--overlay", required=True, help="overlay YAML file")
    p_customize.set_defaults(func=_cmd_customize)

    p_render = sub.add_parser("render", parents=[common], help="render a tenant's instruction layers")
    p_render.add_argument("--slug", "--tenant", dest="slug", required=True)
    p_render.set_defaults(func=_cmd_render)
    return parser


def _open_store(path: str | None):
    if path:
        store = FileStore(path).load_file()
    else:
        store = InMemoryStore()
    rbac_store = provisioning.materialize_rbac(store)
    return store, rbac_store


def _cmd_provision(args: argparse.Namespace, store, rbac_store, out) -> int:
    spec = ProvisionSpec(
        slug=args.slug,
        name=args.name,
        tenant_type=args.tenant_type,
        idp_tenant_id=args.idp_tenant_id,
        domain=args.domain,
        owner_email=args.owner_email,
        owner_name=args.owner_name,
        owner_role=args.owner_role,
        role_admin_permission=args.role_admin_permission,
        max_attempts=args.max_attempts,
    )
    if args.dry_run:
        result = provisioning.provision(
            store, rbac_store, spec, dry_run=True,
        )
        out.write(_format_result(result, dry_run=True))
        return 0

    job = jobs.submit_and_run(store, rbac_store, spec)
    _persist(args.store, store)
    if job.status == JOB_STATUS_COMPLETED:
        tenant = store.get_tenant(spec.slug)
        # reconstruct a result view for readable output
        roles = sorted(role.key for role in rbac_store.roles_in_org(spec.slug))
        _print_job(out, job, tenant.name if tenant else spec.name, spec.slug, roles)
        return 0
    _print_job(out, job, spec.name, spec.slug, [])
    return 1


def _cmd_tenant(command: str):
    def _run(args: argparse.Namespace, store, rbac_store, out) -> int:
        tenant = store.get_tenant(args.slug)
        if tenant is None:
            out.write(f"tenant {args.slug!r} not found\n")
            return 1
        if command == "status":
            _print_status(out, store, rbac_store, tenant)
            return 0
        if command == "ready":
            report = ready_mod.ready_check(store, rbac_store, args.slug)
            _print_ready(out, report)
            return 0 if report.ready else 1
        if command == "retry":
            jobs_for = store.jobs_for_tenant(args.slug)
            failed = [j for j in jobs_for if j.status == JOB_STATUS_FAILED and j.retryable]
            if not failed:
                out.write(
                    f"no retryable failed job for tenant {args.slug!r}\n"
                )
                return 1
            job = failed[-1]
            spec = _spec_from_tenant(tenant)
            retried = jobs.retry_job(store, rbac_store, job.id, spec)
            _persist(args.store, store)
            _print_job(
                out, retried, tenant.name, tenant.id,
                sorted(role.key for role in rbac_store.roles_in_org(tenant.id)),
            )
            return 0 if retried.status == JOB_STATUS_COMPLETED else 1
        raise _CliError(f"unhandled command {command!r}")

    return _run


def _cmd_jobs(args: argparse.Namespace, store, rbac_store, out) -> int:
    jobs_for = store.jobs_for_tenant(args.slug)
    if not jobs_for:
        out.write(f"no provisioning jobs for tenant {args.slug!r}\n")
        return 1
    for job in jobs_for:
        out.write(
            f"  {job.id}  {job.status:9s}  attempt {job.attempt}/{job.max_attempts}  "
            f"step={job.step_name or '-'}  error={job.error or '-'}\n"
        )
    return 0


def _cmd_customize(args: argparse.Namespace, store, rbac_store, out) -> int:
    overlay_path = Path(args.overlay)
    if not overlay_path.is_file():
        out.write(f"overlay file not found: {overlay_path}\n")
        return _USAGE_ERROR
    overlay = yaml.safe_load(overlay_path.read_text(encoding="utf-8"))
    try:
        applied = customization.apply_customization(store, args.slug, overlay)
    except (OverlayValidationError, KeyError) as exc:
        out.write(f"customize rejected: {exc}\n")
        return _USAGE_ERROR
    _persist(args.store, store)
    out.write(
        f"customization applied for {args.slug} at {applied.updated_at} "
        f"(fields: {', '.join(sorted(applied.overlay))})\n"
    )
    return 0


def _cmd_render(args: argparse.Namespace, store, rbac_store, out) -> int:
    manifest = customization.render_instruction_manifest(store, args.slug)
    if not manifest:
        out.write(
            f"tenant {args.slug!r}: no instruction overlay "
            "(runs the platform seed-pack underlay)\n"
        )
        return 0
    for entry in manifest:
        ref = entry.get("ref") or entry.get("inline")
        out.write(
            f"  {entry['order']:>3d}  {entry['layer']:6s}  {ref}\n"
        )
    return 0


def _spec_from_tenant(tenant) -> ProvisionSpec:
    return ProvisionSpec(
        slug=tenant.id,
        name=tenant.name,
        tenant_type=tenant.tenant_type,
        idp_tenant_id=tenant.idp_tenant_id,
        domain=tenant.domain,
        owner_email=tenant.owner_email,
        owner_name=tenant.owner_name,
        owner_role=tenant.owner_role,
        role_admin_permission=tenant.role_admin_permission,
    )


def _persist(store_path: str | None, store) -> None:
    if store_path:
        store.save_file()


def _format_result(result, dry_run: bool) -> str:
    lines = [
        "DRY RUN - nothing was written." if dry_run else
        ("Provisioned (converged)." if result.converged else "Provisioned."),
        f"  tenant  {result.tenant.name} ({result.tenant.id})",
        f"  idp     {result.tenant.idp_tenant_id or '(not linked - nobody can sign in yet)'}",
        f"  host    {result.tenant.domain or '(none - no domain claimed yet)'}",
        f"  roles   {', '.join(result.roles) or '(none)'}",
        f"  owner   {result.owner_role or '(none - tenant inert until an owner is bound)'}",
    ]
    if result.seeds:
        seed_lines = []
        for seed in result.seeds:
            seed_lines.append(f"{seed.kind} {seed.ref} ({seed.status})")
        lines.append("  seeds   " + "; ".join(seed_lines))
    lines.append(f"  status  {result.tenant.status}")
    return "\n".join(lines) + "\n"


def _print_job(out, job, name: str, slug: str, roles: list[str]) -> None:
    if job.status == JOB_STATUS_COMPLETED:
        out.write(f"Provisioned.\n  tenant  {name} ({slug})\n")
        out.write(f"  roles   {', '.join(roles)}\n")
        out.write(
            f"  job     {job.id} completed (attempt {job.attempt}/{job.max_attempts})\n"
        )
    else:
        out.write(f"Provision FAILED for {name} ({slug}).\n")
        out.write(f"  job     {job.id} {job.status}\n")
        out.write(f"  error   {job.error or 'unknown'}\n")
    if job.audit_log:
        out.write("  steps   ")
        out.write(" -> ".join(f"{e.step}:{e.status}" for e in job.audit_log))
        out.write("\n")


def _print_status(out, store, rbac_store, tenant) -> None:
    out.write(f"tenant   {tenant.name} ({tenant.id})\n")
    out.write(f"  type      {tenant.tenant_type}\n")
    out.write(f"  status    {tenant.status}\n")
    out.write(f"  idp       {tenant.idp_tenant_id or '(not linked)'}\n")
    out.write(f"  domain    {tenant.domain or '(none)'}\n")
    out.write(f"  owner     {tenant.owner_email or '(none)'} -> {tenant.owner_role}\n")
    out.write(
        "  roles     "
        + ", ".join(sorted(role.key for role in rbac_store.roles_in_org(tenant.id)))
        + "\n"
    )
    seeds = store.seeds_for_tenant(tenant.id)
    out.write("  seeds     " + "; ".join(
        f"{s.kind} {s.ref} ({s.status})" for s in seeds
    ) + "\n")


def _print_ready(out, report) -> None:
    out.write(f"ready  {report.tenant_id}  {'READY' if report.ready else 'NOT READY'}\n")
    for check in report.checks:
        marker = "ok  " if check.ok else "FAIL"
        out.write(f"  [{marker}] {check.name:16s} {check.detail}\n")


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    store_path = args.store or _env_store()
    store, rbac_store = _open_store(store_path)
    out = sys.stdout
    try:
        return int(args.func(args, store, rbac_store, out))
    except (ProvisionValidationError, _CliError, KeyError) as exc:
        out.write(f"error: {exc}\n")
        return _USAGE_ERROR
    except Exception as exc:  # noqa: BLE001 - CLI reports any pipeline failure
        out.write(f"provision failed: {exc}\n")
        return 1


def _env_store() -> str | None:
    import os

    return os.environ.get("AO_ONBOARDING_STORE")


if __name__ == "__main__":
    sys.exit(main())
