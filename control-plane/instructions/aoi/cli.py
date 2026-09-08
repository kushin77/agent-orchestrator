"""Command-line interface for the model-agnostic instruction layer (#42).

Subcommands (all offline):

  render            render a canonical source (+ optional tenant override) into
                    per-tool mirrors + a distribution manifest
  validate-override validate a tenant override against the frozen contract
  drift             per-consumer drift check (consumer state vs distribution)
  conformance       model-agnostic conformance over a rendered mirror set
  pin               pin a compliant consumer state from a distribution manifest
  version           print the aoi package version

Exit codes: 0 pass, 1 failure (invalid override / drift / non-conformance /
byte-stable mismatch), 2 usage error.
"""

from __future__ import annotations

import argparse
import os
import sys

from . import __version__
from .conformance import HARNESS_MAP, conformance_check
from .model import load_canonical
from .override import (
    OVERRIDE_SCHEMA,
    OverrideError,
    apply_local_rules,
    validate_override,
)
from .render import MIRROR_TARGETS, distribution_manifest, render_all
from .versioning import (
    CONSUMER_SCHEMA,
    DistributionError,
    check_drift,
    load_consumer_state,
    load_json,
)


def _load_override(path: str | None) -> dict | None:
    if path is None:
        return None
    override = load_json(path)
    validate_override(override)
    return override


def _write_text(path: str, content: str) -> None:
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)


def _write_json(path: str, data: dict) -> None:
    import json

    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
        handle.write("\n")


def cmd_render(args: argparse.Namespace) -> int:
    canonical = load_canonical(args.canonical)
    override = _load_override(args.override)
    files = render_all(canonical, override)
    if args.check:
        failures = []
        for name in MIRROR_TARGETS:
            path = os.path.join(args.out, name)
            if not os.path.isfile(path):
                failures.append(f"{name}: missing from {args.out}")
                continue
            with open(path, encoding="utf-8") as handle:
                if handle.read() != files[name]:
                    failures.append(f"{name}: not byte-stable (regeneration differs — hand edit?)")
        manifest_path = os.path.join(args.out, "distribution-manifest.json")
        expected_manifest = distribution_manifest(canonical, override)
        if os.path.isfile(manifest_path):
            with open(manifest_path, encoding="utf-8") as handle:
                import json

                if json.load(handle) != expected_manifest:
                    failures.append("distribution-manifest.json: not byte-stable")
        else:
            failures.append("distribution-manifest.json: missing from the rendered set")
        if failures:
            for failure in failures:
                print(f"render: FAIL  {failure}", file=sys.stderr)
            print("render: not byte-stable — mirrors are generated, never hand-forked", file=sys.stderr)
            return 1
        print(f"render: byte-stable — {len(MIRROR_TARGETS)} mirrors match regeneration")
        return 0
    os.makedirs(args.out, exist_ok=True)
    for name, content in files.items():
        _write_text(os.path.join(args.out, name), content)
    _write_json(os.path.join(args.out, "distribution-manifest.json"), distribution_manifest(canonical, override))
    print(f"render: wrote {len(files)} mirrors + distribution-manifest.json to {args.out}")
    return 0


def cmd_validate_override(args: argparse.Namespace) -> int:
    override = load_json(args.override)
    try:
        validate_override(override)
    except OverrideError as exc:
        print(f"override: REJECTED — {exc}", file=sys.stderr)
        return 1
    if args.canonical:
        canonical = load_canonical(args.canonical)
        try:
            apply_local_rules(override, canonical)
        except OverrideError as exc:
            print(f"override: REJECTED against {args.canonical} — {exc}", file=sys.stderr)
            return 1
    print(f"override: valid ({OVERRIDE_SCHEMA})")
    return 0


def cmd_drift(args: argparse.Namespace) -> int:
    try:
        manifest = load_json(args.manifest)
        consumer = load_consumer_state(args.consumer)
    except DistributionError as exc:
        print(f"drift: {exc}", file=sys.stderr)
        return 1
    compliant, findings = check_drift(manifest, consumer)
    if compliant:
        print(f"drift: compliant — {args.consumer} matches {args.manifest}")
        return 0
    for finding in findings:
        print(f"drift: FLAG  {finding}", file=sys.stderr)
    print(f"drift: {len(findings)} finding(s) — consumer is out of date", file=sys.stderr)
    return 1


def cmd_conformance(args: argparse.Namespace) -> int:
    canonical = load_canonical(args.canonical)
    override = _load_override(args.override)
    extra_rules = apply_local_rules(override, canonical) if override else []
    compliant, findings = conformance_check(args.dir, canonical, extra_rules)
    print(f"conformance: checking {len(MIRROR_TARGETS)} mirrors in {args.dir}")
    for tool in MIRROR_TARGETS:
        print(f"  {tool:<22} -> {HARNESS_MAP[tool]}")
    if compliant:
        print("conformance: PASS — identical rules + precedence in every mirror")
        return 0
    for finding in findings:
        print(f"conformance: FAIL  {finding}", file=sys.stderr)
    print(f"conformance: {len(findings)} finding(s) — mirrors are not semantically equivalent", file=sys.stderr)
    return 1


def cmd_pin(args: argparse.Namespace) -> int:
    manifest = load_json(args.manifest)
    mirrors = {}
    for name, digest in (manifest.get("mirrors") or {}).items():
        if name in MIRROR_TARGETS:
            mirrors[name] = digest
    canonical = manifest.get("canonical") or {}
    state = {
        "schema": CONSUMER_SCHEMA,
        "consumer": args.consumer,
        "canonical": {"id": canonical.get("id"), "version": canonical.get("version")},
        "mirrors": dict(sorted(mirrors.items())),
    }
    _write_json(args.out, state)
    print(f"pin: wrote compliant consumer state for {args.consumer} to {args.out}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aoi",
        description="agent-orchestrator model-agnostic instruction layer (issue #42)",
    )
    parser.add_argument("--version", action="version", version=f"aoi {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_render = sub.add_parser("render", help="render per-tool mirrors + distribution manifest")
    p_render.add_argument("--canonical", required=True, help="canonical instruction source (YAML/JSON)")
    p_render.add_argument("--override", help="tenant override (JSON, frozen contract)")
    p_render.add_argument("--out", required=True, help="output directory for the mirrors")
    p_render.add_argument("--check", action="store_true",
                          help="verify the mirrors in --out are byte-stable regenerations (no writes)")
    p_render.set_defaults(func=cmd_render)

    p_val = sub.add_parser("validate-override", help="validate a tenant override against the frozen contract")
    p_val.add_argument("--override", required=True, help="tenant override JSON")
    p_val.add_argument("--canonical", help="canonical source to cross-check the override against")
    p_val.set_defaults(func=cmd_validate_override)

    p_drift = sub.add_parser("drift", help="per-consumer drift check")
    p_drift.add_argument("--manifest", required=True, help="distribution manifest JSON")
    p_drift.add_argument("--consumer", required=True, help="consumer state JSON")
    p_drift.set_defaults(func=cmd_drift)

    p_conf = sub.add_parser("conformance", help="model-agnostic conformance over a rendered set")
    p_conf.add_argument("--dir", required=True, help="directory containing the rendered mirrors")
    p_conf.add_argument("--canonical", required=True, help="canonical source the mirrors were rendered from")
    p_conf.add_argument("--override", help="tenant override used at render time")
    p_conf.set_defaults(func=cmd_conformance)

    p_pin = sub.add_parser("pin", help="pin a compliant consumer state from a distribution manifest")
    p_pin.add_argument("--manifest", required=True, help="distribution manifest JSON")
    p_pin.add_argument("--consumer", required=True, help="consumer/repository name")
    p_pin.add_argument("--out", required=True, help="consumer state output path")
    p_pin.set_defaults(func=cmd_pin)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (OverrideError, DistributionError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
