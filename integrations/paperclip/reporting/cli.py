#!/usr/bin/env python3
"""``integrations/paperclip/reporting/cli.py`` — the module-brief machine surface.

    python3 integrations/paperclip/reporting/cli.py compose            # the brief, to stdout
    python3 integrations/paperclip/reporting/cli.py compose --out FILE # … frozen (refuses while a finding stands)
    python3 integrations/paperclip/reporting/cli.py check              # artifact vs a fresh composition
    python3 integrations/paperclip/reporting/cli.py capability         # the persona's declaration vs the tools it needs
    python3 integrations/paperclip/reporting/cli.py claims             # every claim resolves?
    python3 integrations/paperclip/reporting/cli.py audit              # the append-only trail of composed runs

Exit codes are the repository's tri-state convention: **0** OK, **1** NOT-OK (a
refusal), **2** CANNOT-ASSESS — the hub catalog is absent, or the registry could
not be built. CANNOT-ASSESS is never reported as a pass.

``--registry FILE`` composes from a registry document already on disk instead of
building one: that is how a caller (or the gate) proves the composer refuses a
document that reports a pending module as shipped, without touching the tree.

Every command that composes a brief appends **exactly one** record to the audit
trail (``--audit``, default ``<repo>/.verify/module-brief-audit.jsonl``, which is
gitignored runtime state): the run's resolved / unresolved claim counts and the
finding lines it produced. ``compose`` keeps stdout byte-for-byte the brief, so
the trail never contaminates the artifact it records.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import List, Optional

sys.dont_write_bytecode = True

EXIT_OK = 0
EXIT_NOT_OK = 1
EXIT_CANNOT_ASSESS = 2

#: Default hub root, repository-relative (the pinned submodule). Declared here as
#: a constant rather than imported: the parser is built before ``--repo`` has put
#: the repository under test on ``sys.path``.
DEFAULT_HUB_REL = "vendor/CMR"


def _bootstrap(repo: Path) -> None:
    """Import ``governance.modules`` from *this* repository, not from a sibling.

    The repository root goes to the front of ``sys.path`` so a caller pointing
    ``--repo`` at a scratch tree composes from the scratch tree — otherwise a
    provocation would silently read the lane's own registry and prove nothing.
    """
    root = str(Path(repo).resolve())
    if root in sys.path:
        sys.path.remove(root)
    sys.path.insert(0, root)


def _findings_rc(findings, stream=None) -> int:
    target = sys.stdout if stream is None else stream
    for finding in findings:
        print("  FAIL  {}".format(finding.render()), file=target)
    return len(findings)


def _build_registry(repo: Path, hub: Optional[str], targets: Optional[str]):
    from governance.modules import registry

    return registry.build(
        Path(repo),
        Path(hub) if hub else Path(registry.hub.DEFAULT_HUB),
        Path(targets) if targets else None,
    )


def _load_registry_document(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        from governance.modules.model import CannotAssess

        raise CannotAssess("registry document unreadable: {} ({})".format(path, exc))


def _trail(args):
    """Where this run's single audit record goes (issue #592).

    Always constructed — every command that composes a brief records the run —
    and always the same path for a given caller, so a run is never silently
    unrecorded.
    """
    from integrations.paperclip.reporting import audit

    return audit.Trail(audit.trail_path(Path(args.repo), getattr(args, "audit", None)))


def _cmd_compose(args) -> int:
    from integrations.paperclip.reporting import composer
    from governance.modules.model import CannotAssess

    try:
        document = (
            _load_registry_document(Path(args.registry))
            if args.registry
            else _build_registry(Path(args.repo), args.hub, args.targets)
        )
        composition = composer.compose(
            document, Path(args.repo), args.hub_rel, audit_trail=_trail(args)
        )
    except CannotAssess as exc:
        print("module-brief: CANNOT-ASSESS — {}".format(exc), file=sys.stderr)
        return EXIT_CANNOT_ASSESS

    findings = list(composition.findings)
    if args.out:
        if findings:
            print(
                "module-brief: REFUSED to freeze {} — {} finding(s) stand".format(
                    args.out, len(findings)
                ),
                file=sys.stderr,
            )
            _findings_rc(findings, sys.stderr)
            return EXIT_NOT_OK
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(composition.text, encoding="utf-8")
        print(
            "module-brief: wrote {} ({} bytes, {} claims); one audit record appended to {}".format(
                args.out,
                len(composition.text.encode("utf-8")),
                len(composition.claims),
                _trail(args).path,
            )
        )
        return EXIT_OK

    sys.stdout.write(composition.text)
    if findings:
        _findings_rc(findings, sys.stderr)
        return EXIT_NOT_OK
    return EXIT_OK


def _cmd_capability(args) -> int:
    from integrations.paperclip.reporting import capability
    from governance.modules.model import CannotAssess

    try:
        findings = capability.check(Path(args.repo), Path(args.repo) / args.hub_rel)
    except CannotAssess as exc:
        print("module-brief: CANNOT-ASSESS — {}".format(exc), file=sys.stderr)
        return EXIT_CANNOT_ASSESS
    if findings:
        _findings_rc(findings, sys.stderr)
        return EXIT_NOT_OK
    contract = capability.as_dict()
    print(
        "  OK    {} declares {} with {} (artifact home: {})".format(
            contract["card"],
            contract["capability"],
            ", ".join(contract["required_tools"]),
            contract["artifact_home"],
        )
    )
    return EXIT_OK


def _cmd_claims(args) -> int:
    from integrations.paperclip.reporting import composer
    from governance.modules.model import CannotAssess

    try:
        document = (
            _load_registry_document(Path(args.registry))
            if args.registry
            else _build_registry(Path(args.repo), args.hub, args.targets)
        )
        composition = composer.compose(
            document, Path(args.repo), args.hub_rel, audit_trail=_trail(args)
        )
    except CannotAssess as exc:
        print("module-brief: CANNOT-ASSESS — {}".format(exc), file=sys.stderr)
        return EXIT_CANNOT_ASSESS

    unresolved = [f for f in composition.findings if f.code == "BRIEF-CLAIM-UNRESOLVED"]
    if unresolved:
        _findings_rc(unresolved, sys.stderr)
        return EXIT_NOT_OK
    if not composition.claims:
        print("module-brief: FAIL — the composition made no claim at all", file=sys.stderr)
        return EXIT_NOT_OK
    print(
        "  OK    {} claim(s) resolve to a registry row or a cited path".format(
            len(composition.claims)
        )
    )
    return EXIT_OK


def _cmd_audit(args) -> int:
    """Report the append-only trail of composed brief runs (issue #592)."""

    trail = _trail(args)
    records = trail.records()
    if not records:
        print(
            "module-brief: CANNOT-ASSESS — no audit trail at {} (no brief has been "
            "composed against this tree yet)".format(trail.path),
            file=sys.stderr,
        )
        return EXIT_CANNOT_ASSESS
    last = records[-1]
    print(
        "  OK    {} record(s) in {}; the last run resolved {} of {} claim(s) "
        "({} unresolved)".format(
            len(records),
            trail.path,
            last["resolved"],
            last["claims"],
            last["unresolved"],
        )
    )
    for line in last["findings"]:
        print("        {}".format(line))
    return EXIT_OK


def _cmd_check(args) -> int:
    from integrations.paperclip.reporting import capability, composer, model
    from governance.modules.model import CannotAssess

    repo = Path(args.repo)
    findings = []
    try:
        findings.extend(capability.check(repo, repo / args.hub_rel))
        document = (
            _load_registry_document(Path(args.registry))
            if args.registry
            else _build_registry(repo, args.hub, args.targets)
        )
        composition = composer.compose(
            document, repo, args.hub_rel, audit_trail=_trail(args)
        )
    except CannotAssess as exc:
        print("module-brief: CANNOT-ASSESS — {}".format(exc), file=sys.stderr)
        return EXIT_CANNOT_ASSESS

    findings.extend(composition.findings)
    artifact = repo / model.ARTIFACT
    try:
        committed = artifact.read_text(encoding="utf-8")
    except OSError as exc:
        from governance.modules.model import Refusal

        findings.append(
            Refusal(
                "BRIEF-STALE",
                model.ARTIFACT,
                "the frozen artifact is missing or unreadable ({})".format(exc),
                model.ARTIFACT,
            )
        )
    else:
        if committed != composition.text:
            from governance.modules.model import Refusal

            findings.append(
                Refusal(
                    "BRIEF-STALE",
                    model.ARTIFACT,
                    "the committed brief differs from a fresh composition over this revision "
                    "(committed {:d} bytes, sha256 {}; fresh {:d} bytes, sha256 {}) — regenerate "
                    "it rather than editing it by hand".format(
                        len(committed.encode("utf-8")),
                        hashlib.sha256(committed.encode("utf-8")).hexdigest(),
                        len(composition.text.encode("utf-8")),
                        hashlib.sha256(composition.text.encode("utf-8")).hexdigest(),
                    ),
                    model.ARTIFACT,
                )
            )

    if findings:
        _findings_rc(findings, sys.stderr)
        return EXIT_NOT_OK
    print(
        "  OK    {} claim(s) resolve; {} is byte-identical to a fresh composition".format(
            len(composition.claims), model.ARTIFACT
        )
    )
    print(
        "  OK    the persona declaration grants what the capability uses ({} requires {})".format(
            capability.CAPABILITY_ID, ", ".join(capability.REQUIRED_TOOLS)
        )
    )
    return EXIT_OK
def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="integrations/paperclip/reporting/cli.py",
        description="The module brief — what every repo must carry, at which pin, and whether it is current (issue #447).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def common(target: argparse.ArgumentParser) -> None:
        target.add_argument("--repo", default=None, help="repository root (default: this checkout)")
        target.add_argument("--hub", default=None, help="hub root (default: <repo>/vendor/CMR)")
        target.add_argument("--hub-rel", default=DEFAULT_HUB_REL, help="hub root, repository-relative")
        target.add_argument("--targets", default=None, help="declared target set JSON")
        target.add_argument(
            "--registry",
            default=None,
            help="compose from a registry document on disk instead of building one",
        )
        target.add_argument(
            "--audit",
            default=None,
            help="audit trail to append this run's one record to (default: <repo>/.verify/module-brief-audit.jsonl)",
        )

    compose = sub.add_parser("compose", help="emit the brief")
    common(compose)
    compose.add_argument("--out", default=None, help="write here (refused while a finding stands)")
    compose.set_defaults(handler=_cmd_compose)

    check = sub.add_parser("check", help="the contract, the composition and the frozen artifact")
    common(check)
    check.set_defaults(handler=_cmd_check)

    claims = sub.add_parser("claims", help="every claim resolves?")
    common(claims)
    claims.set_defaults(handler=_cmd_claims)

    cap = sub.add_parser("capability", help="the persona declaration vs the tools the capability needs")
    cap.add_argument("--repo", default=None, help="repository root (default: this checkout)")
    cap.add_argument("--hub-rel", default=DEFAULT_HUB_REL, help="hub root, repository-relative")
    cap.set_defaults(handler=_cmd_capability)

    trail = sub.add_parser(
        "audit", help="the append-only trail of composed brief runs (resolved / unresolved counts)"
    )
    trail.add_argument("--repo", default=None, help="repository root (default: this checkout)")
    trail.add_argument(
        "--audit",
        default=None,
        help="audit trail to read (default: <repo>/.verify/module-brief-audit.jsonl)",
    )
    trail.set_defaults(handler=_cmd_audit)
    return parser


def _default_repo() -> str:
    return str(Path(__file__).resolve().parents[3])


def main(argv: Optional[List[str]] = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if not getattr(args, "repo", None):
        args.repo = _default_repo()
    _bootstrap(Path(args.repo))
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
