#!/usr/bin/env python3
"""CLI for the paperclip skills adapter (issue #419).

Subcommands (exit-code contract 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS):

  check                       registry + projection + provenance + no-vendoring
  project [--write|--check]   regenerate / compare the derived MCP projection
  list                        the declared, loadable skills
  show --skill ID             one declaration's origin, provenance and requires
  load --skill ID --profile P evaluate the load gate and print the verdict
  status                      counts for the registry, the projection and profiles

Usage (from the repo root):

    python3 -m integrations.paperclip.adapters.skills.cli check
    python3 -m integrations.paperclip.adapters.skills.cli project --check
    python3 -m integrations.paperclip.adapters.skills.cli load --skill ticket-contract-read --profile paperclip

---knowledge---
module_id: integrations.paperclip.adapters.skills.cli
system: integrations
app: paperclip
solution_class: enterprise
patterns: []
derives_from: null
owner_sme: paperclip
tier: L1
interfaces: [cmd_check, cmd_project, cmd_list, cmd_show, cmd_load, cmd_status, build_parser, main]
invariants: ""
gotchas: ""
related: ["#1910"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in (None, ""):  # pragma: no cover - direct script execution
    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from integrations.paperclip.adapters.skills import loader, projection, registry  # noqa: E402
from integrations.paperclip.adapters.skills.model import CannotAssess, SkillRefused  # noqa: E402


def _repo_root(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).resolve()
    return Path(__file__).resolve().parents[4]


def cmd_check(root: Path) -> int:
    findings = []
    findings.extend(registry.registry_findings(root))
    findings.extend(projection.projection_findings(root))
    if findings:
        for finding in findings:
            print("  FAIL  %s" % finding, file=sys.stderr)
        print("skills: FAIL — %d finding(s)" % len(findings), file=sys.stderr)
        return 1
    declared = registry.declared_entries(root)
    tools = projection.build_projection(root).tools
    print(
        "  OK    registry closed: %d declared declaration(s); projection derived "
        "from %s: %d tool(s); every declaration carries provenance (GR-10) and "
        "vendors nothing"
        % (len(declared), projection.MCP_SOURCE_FUNCTION, len(tools))
    )
    return 0


def cmd_project(root: Path, write: bool, check: bool) -> int:
    if write:
        projection.write_projection(root)
        print("  OK    wrote %s" % projection.PROJECTION_PATH)
        return 0
    findings = projection.projection_findings(root)
    if check:
        if findings:
            for finding in findings:
                print("  FAIL  %s" % finding, file=sys.stderr)
            return 1
        print("  OK    the projection is byte-identical to the derived view")
        return 0
    print(projection.render_projection(projection.build_projection(root)), end="")
    return 0


def cmd_list(root: Path) -> int:
    declarations = registry.load_declarations(root)
    for skill_id in sorted(declarations):
        declaration = declarations[skill_id]
        print(
            "  %-22s %-6s  %s/%s (%s)"
            % (
                declaration.id,
                declaration.kind,
                declaration.provenance.repo,
                declaration.provenance.path,
                declaration.provenance.license,
            )
        )
    print("skills: %d declared declaration(s)" % len(declarations))
    return 0


def cmd_show(root: Path, skill_id: str) -> int:
    declaration = registry.resolve(root, skill_id)
    print("id:          %s" % declaration.id)
    print("kind:        %s" % declaration.kind)
    print("name:        %s" % declaration.name)
    print("description: %s" % declaration.description)
    print("declaration: %s" % declaration.declaration_path)
    print("origin:      %s/%s" % (declaration.provenance.repo, declaration.provenance.path))
    print("license:     %s" % declaration.provenance.license)
    print("verdict:     %s" % declaration.provenance.verdict)
    print("requires:    %s" % declaration.requires.as_dict())
    return 0


def cmd_load(root: Path, skill_id: str, profile_id: str) -> int:
    decision = loader.check_load(root, skill_id, profile_id)
    if decision.refused:
        for reason in decision.reasons:
            print("  REFUSED  %s" % reason, file=sys.stderr)
        print(
            "skills: REFUSED — %r is not loadable as profile %r" % (skill_id, profile_id),
            file=sys.stderr,
        )
        return 1
    print(
        "  OK    %r is loadable as profile %r; effective capabilities: %s"
        % (skill_id, profile_id, list(loader.effective_capabilities(decision)))
    )
    return 0


def cmd_status(root: Path) -> int:
    declared = registry.declared_entries(root)
    discovered = registry.discover_declarations(root)
    profile_seeds = sorted((root / loader.PROFILE_SEED_DIR).glob("*.yaml"))
    tools = projection.build_projection(root)
    print("registry:    %d declared / %d discovered declaration(s)" % (len(declared), len(discovered)))
    print("projection:  %d tool(s), derived from %s" % (len(tools.tools), tools.source_function))
    print("profiles:    %d seed(s) under %s" % (len(profile_seeds), loader.PROFILE_SEED_DIR))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="paperclip-skills", description=__doc__)
    parser.add_argument("--root", default=None, help="repo root (defaults to the package's repo)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("check", help="structural checks: registry, projection, provenance, vendoring")
    sub.add_parser("status", help="counts for the registry, projection and profiles")
    sub.add_parser("list", help="the declared, loadable declarations")

    project = sub.add_parser("project", help="regenerate or compare the MCP projection")
    project.add_argument("--write", action="store_true", help="write the derived view")
    project.add_argument("--check", action="store_true", help="compare the view with the derived view")

    show = sub.add_parser("show", help="one declaration")
    show.add_argument("--skill", required=True)

    load = sub.add_parser("load", help="evaluate the load gate for a skill and a profile")
    load.add_argument("--skill", required=True)
    load.add_argument("--profile", required=True)

    return parser


def main(argv: list | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = _repo_root(args.root)
    try:
        if args.command == "check":
            return cmd_check(root)
        if args.command == "status":
            return cmd_status(root)
        if args.command == "list":
            return cmd_list(root)
        if args.command == "project":
            return cmd_project(root, write=args.write, check=args.check)
        if args.command == "show":
            return cmd_show(root, args.skill)
        if args.command == "load":
            return cmd_load(root, args.skill, args.profile)
    except CannotAssess as exc:
        print("skills: CANNOT-ASSESS — %s" % exc, file=sys.stderr)
        return 2
    except SkillRefused as exc:
        print("skills: REFUSED — %s" % exc, file=sys.stderr)
        return 1
    print("skills: CANNOT-ASSESS — unhandled command %r" % args.command, file=sys.stderr)
    return 2


if __name__ == "__main__":  # pragma: no cover - entry point
    raise SystemExit(main())
