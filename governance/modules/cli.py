#!/usr/bin/env python3
"""``governance/modules/cli.py`` — the ecosystem module registry's machine surface.

    python3 governance/modules/cli.py build                 # canonical registry JSON
    python3 governance/modules/cli.py build --out FILE      # … written instead of printed
    python3 governance/modules/cli.py verify                # drift + refusals (gate core)
    python3 governance/modules/cli.py membership pmo        # one name -> a state
    python3 governance/modules/cli.py membership hermes-agents   # -> refused, by name
    python3 governance/modules/cli.py vendoring             # the no-vendoring scan
    python3 governance/modules/cli.py probe --live          # pin health probes

Every subcommand runs offline by default and exits tri-state (the repository
convention): ``0`` OK, ``1`` NOT-OK (a refusal was found, or membership was
refused), ``2`` CANNOT-ASSESS — the hub catalog is absent or unreadable, so the
registry cannot be built at all. **CANNOT-ASSESS is never reported as a pass.**
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from governance.modules import registry  # noqa: E402
from governance.modules import vendoring  # noqa: E402
from governance.modules.hub import DEFAULT_HUB, load as load_hub  # noqa: E402
from governance.modules.model import (  # noqa: E402
    NOT_A_MODULE,
    PROBE_NO_TAG,
    PROBE_OK,
    REGISTERED_MANDATORY,
    CannotAssess,
    Refusal,
)

EXIT_OK = 0
EXIT_NOT_OK = 1
EXIT_CANNOT_ASSESS = 2


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repo", default=str(ROOT), help="repository root (default: this checkout)")
    parser.add_argument(
        "--hub",
        default=None,
        help="hub root, resolved against --repo when relative "
        "(default: <repo>/%s — the pinned, read-only submodule)" % DEFAULT_HUB,
    )
    parser.add_argument(
        "--targets",
        default=None,
        help="declared target set (default: the package's targets.json)",
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="governance/modules/cli.py",
        description="Ecosystem module registry — one honest view of every module (issue #445).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="emit the canonical registry document")
    _add_common(build)
    build.add_argument("--out", default=None, help="write the document here instead of stdout")
    build.add_argument("--live", action="store_true", help="run the pin probes (needs network)")
    build.set_defaults(handler=_cmd_build)

    verify = sub.add_parser("verify", help="report the three states and every refusal")
    _add_common(verify)
    verify.set_defaults(handler=_cmd_verify)

    member = sub.add_parser("membership", help="resolve one name: a state, or a refusal")
    _add_common(member)
    member.add_argument("name", help="the module / repo name to resolve")
    member.set_defaults(handler=_cmd_membership)

    vendor = sub.add_parser("vendoring", help="the no-vendoring scan (references, not copies)")
    _add_common(vendor)
    vendor.add_argument(
        "--registry",
        default=None,
        help="also check the hub references of a previously built registry document",
    )
    vendor.set_defaults(handler=_cmd_vendoring)

    probe = sub.add_parser("probe", help="the per-module health probes")
    _add_common(probe)
    probe.add_argument("--live", action="store_true", help="run the probes (needs network)")
    probe.set_defaults(handler=_cmd_probe)

    return parser


def _hub_for(args: argparse.Namespace) -> Path:
    return Path(args.hub) if args.hub else Path(DEFAULT_HUB)


def _registry_for(args: argparse.Namespace, live: bool = False):
    repo = Path(args.repo)
    targets = Path(args.targets) if args.targets else None
    return registry.build(repo, _hub_for(args), targets, live=live)


def _print_findings(refusals, stream=None) -> int:
    """Render every refusal. ``stream`` is resolved late, so a captured stdout
    (a test, a pipe) sees the findings and not the interpreter's original one."""
    target = sys.stdout if stream is None else stream
    for finding in refusals:
        print("  FAIL  {}".format(finding.render()), file=target)
    return len(refusals)


def _print_states(doc) -> None:
    for state in doc["states"]:
        print("  {:<32} {:>3}".format(state, doc["summary"].get(state, 0)))
    print(
        "  {:<32} {:>3}".format(
            doc["membership_refusal"] + " (refused)",
            doc["summary"].get(doc["membership_refusal"], 0),
        )
    )
    print("  {:<32} {:>3}".format("total names", sum(doc["summary"].values())))


def _cmd_build(args: argparse.Namespace) -> int:
    doc = _registry_for(args, live=args.live)
    text = registry.render(doc)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print("module-registry: wrote {} ({} bytes)".format(args.out, len(text.encode("utf-8"))))
    else:
        sys.stdout.write(text)
    refusals = registry.findings(doc)
    if refusals:
        _print_findings(refusals, stream=sys.stderr)
        print(
            "module-registry: NOT-OK — {} refusal(s) in the emitted document".format(len(refusals)),
            file=sys.stderr,
        )
        return EXIT_NOT_OK
    return EXIT_OK


def _cmd_verify(args: argparse.Namespace) -> int:
    doc = _registry_for(args)
    health = doc["hub"]
    print("== hub ==")
    print("  {:<10} {}".format("root", health["root"]))
    print("  {:<10} {} ({})".format("revision", health["revision"] or "unavailable", health["revision_source"]))
    print("  {:<10} {} — {} mandatory row(s)".format("registry", health["mandatory_registry"], health["mandatory_count"]))
    print("  {:<10} {} module(s); declared targets: {}".format("catalog", health["module_count"], doc["declared"]["targets"]))
    print("== registry ==")
    _print_states(doc)
    refusals = registry.findings(doc)
    print("== refusals ({}) ==".format(len(refusals)))
    _print_findings(refusals)
    if refusals:
        print("module-registry: NOT-OK — {} refusal(s)".format(len(refusals)))
        return EXIT_NOT_OK
    print(
        "module-registry: OK — {} name(s), {} state(s), 0 refusal(s)".format(
            sum(doc["summary"].values()), len(doc["states"])
        )
    )
    return EXIT_OK


def _cmd_membership(args: argparse.Namespace) -> int:
    doc = _registry_for(args)
    state, entry = registry.membership(doc, args.name)
    if state == NOT_A_MODULE:
        print("not-a-module: {} — {}".format(args.name, entry.get("detail", registry.MEMBERSHIP_NOTE)))
        claim = entry.get("claim")
        if claim:
            print(
                "  claim   admission={!r} request={!r} — a claim is not membership".format(
                    claim.get("admission"), claim.get("request")
                )
            )
        print("module-registry: membership refused for {!r}".format(args.name))
        return EXIT_NOT_OK
    print("{}: {}".format(state, args.name))
    print("  owning_repo  {}".format(entry.get("owning_repo")))
    print("  pin          {} @ {}".format(entry.get("pin"), entry.get("rev") or entry.get("board_ref")))
    print("  assets       {}".format(", ".join(entry.get("consumer_assets") or []) or "-"))
    print("  blocking     {}".format(", ".join(entry.get("blocking") or []) or "-"))
    return EXIT_OK


def _cmd_vendoring(args: argparse.Namespace) -> int:
    repo = Path(args.repo)
    hub_root = _hub_for(args)
    catalog = load_hub(hub_root, repo, recorded_root=str(hub_root))
    entries = None
    if args.registry:
        doc = json.loads(Path(args.registry).read_text(encoding="utf-8"))
        entries = doc.get("modules") or []
    refusals = vendoring.scan(repo, catalog, entries)
    print(
        "== no vendoring ({} module id(s) known, hub {}) ==".format(
            len(catalog.ids), catalog.root
        )
    )
    _print_findings(refusals)
    if refusals:
        print("module-registry: NOT-OK — {} vendoring refusal(s)".format(len(refusals)))
        return EXIT_NOT_OK
    print("module-registry: OK — references only; {} is the only vendor path".format("vendor/CMR"))
    return EXIT_OK


def _cmd_probe(args: argparse.Namespace) -> int:
    doc = _registry_for(args, live=args.live)
    print("== health probes ({} mode) ==".format("live" if args.live else "offline"))
    statuses = []
    for entry in doc["modules"]:
        spec = entry["health"]
        marker = "" if entry["state"] == REGISTERED_MANDATORY else " (not registered)"
        print(
            "  {:<10} {:<22} {}{}".format(
                spec["status"], entry["id"], spec.get("target") or "-", marker
            )
        )
        if entry["state"] == REGISTERED_MANDATORY:
            statuses.append(spec["status"])
    if not statuses:
        print("module-registry: CANNOT-ASSESS — no registered mandatory module to probe")
        return EXIT_CANNOT_ASSESS
    if PROBE_NO_TAG in statuses:
        print("module-registry: NOT-OK — a registered pin has no matching remote tag")
        return EXIT_NOT_OK
    if all(status == PROBE_OK for status in statuses):
        print("module-registry: OK — {} pin(s) verified".format(len(statuses)))
        return EXIT_OK
    print("module-registry: CANNOT-ASSESS — {} of {} pin(s) could not be verified".format(
        sum(1 for status in statuses if status != PROBE_OK), len(statuses)
    ))
    return EXIT_CANNOT_ASSESS


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    try:
        return args.handler(args)
    except CannotAssess as exc:
        print("module-registry: CANNOT-ASSESS — {}".format(exc), file=sys.stderr)
        return EXIT_CANNOT_ASSESS


if __name__ == "__main__":  # pragma: no cover - process entry point
    raise SystemExit(main())
