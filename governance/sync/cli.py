#!/usr/bin/env python3
"""Offline operator CLI for the governance/sync engine (issue #44).

Everything runs offline (stdlib + optional PyYAML for spec files). Honest exit
codes (GR-12 / no-false-green): ``0`` all good, ``1`` findings (drift /
missing pin / validation error), ``2`` usage or a malformed input.

Subcommands:

  provenance generate  build a provenance manifest from an asset inventory
  provenance validate  validate a manifest (flags an entry missing its pin)
  drift                drift-check a manifest (tri-state CLEAN/DRIFT/CANNOT-ASSESS)
  blast                blast-radius report for a proposed shared-asset change
  sync plan            reconcile plan (dry-run default; --apply materializes)
  sync check           provenance + drift in one gate (rc 0 clean / 1 drift)

Run ``python3 governance/sync/cli.py <subcommand> --help`` for details.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

# Self-locating import bootstrap: this file may be run from the repo root or
# from inside governance/sync; sibling modules import each other plainly.
_SYNC_DIR = os.path.dirname(os.path.abspath(__file__))
if _SYNC_DIR not in sys.path:
    sys.path.insert(0, _SYNC_DIR)

import blast_radius  # noqa: E402
import drift  # noqa: E402
import provenance  # noqa: E402
import sync_plan  # noqa: E402


def _load_json(path, label="input"):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as exc:
        raise SystemExit("cli: %s %s is not readable JSON: %s"
                         % (label, path, exc))


def _dumps(obj):
    return json.dumps(obj, indent=2, sort_keys=True)


def cmd_provenance_generate(args):
    inventory = _load_json(args.inventory, "inventory")
    assets = inventory.get("assets")
    if not isinstance(assets, list):
        raise SystemExit("cli: inventory must be a JSON object with an "
                         "'assets' list")
    consumer = {"id": args.consumer,
                "repo": args.consumer_repo or args.consumer}
    manifest, findings = provenance.generate_manifest(
        consumer, assets, local_root=args.local_root,
        generated=args.generated)
    errors = [f for f in findings if f.get("severity") == "error"]
    if errors:
        for f in errors:
            print("cli: %s %s: %s" % (f.get("asset"), f.get("code"),
                                      f.get("message")))
        raise SystemExit("cli: provenance generate refused %d unpinnable/"
                         "invalid asset(s)" % len(errors))
    manifest.save(args.out)
    print("cli: wrote %s (%d assets, consumer %s)"
          % (args.out, len(assets), args.consumer))
    for finding in findings:
        if finding.get("severity") == "warning":
            print("cli: warning %s %s: %s"
                  % (finding.get("asset"), finding.get("code"),
                     finding.get("message")))
    return 0


def cmd_provenance_validate(args):
    manifest = provenance.ProvenanceManifest.load(args.manifest)
    findings, errors = provenance.validate_manifest(manifest)
    for f in findings:
        print("%s\t%s\t%s\t%s" % (f.get("asset"), f.get("severity"),
                                  f.get("code"), f.get("message")))
    print("cli: %d finding(s), %d error(s) in %s"
          % (len(findings), errors, args.manifest))
    return 0 if errors == 0 else 1


def cmd_drift(args):
    manifest = provenance.ProvenanceManifest.load(args.manifest)
    desired = _load_json(args.desired, "desired") if args.desired else None
    report = drift.check_manifest(manifest, local_root=args.local_root,
                                  canonical_root=args.canonical_root,
                                  desired=desired)
    if args.json:
        print(_dumps(report))
    else:
        summary = report["summary"]
        print("drift check: %s (%d clean / %d drift / %d cannot-assess)"
              % (report["verdict"].value, summary["clean"],
                 summary["drift"], summary["cannot_assess"]))
        for row in report["assets"]:
            print("  %-12s %-16s %s"
                  % (row["asset"], row["state"].value, row["reason"]))
    if report["verdict"] is drift.DriftState.CLEAN:
        return 0
    if report["verdict"] is drift.DriftState.DRIFT:
        return 1
    return 2  # CANNOT_ASSESS is never a pass (honesty tri-state)


def cmd_blast(args):
    manifests = []
    for path in args.manifests:
        if os.path.isdir(path):
            for name in sorted(os.listdir(path)):
                full = os.path.join(path, name)
                if name.endswith(".json") and os.path.isfile(full):
                    manifests.append(provenance.ProvenanceManifest.load(full))
        else:
            manifests.append(provenance.ProvenanceManifest.load(path))
    if not manifests:
        raise SystemExit("cli: blast needs at least one consumer manifest "
                         "(file or directory of *.json)")
    catalog = _load_json(args.catalog, "catalog") if args.catalog else None
    catalog_assets = {}
    if catalog is not None:
        nodes = catalog.get("assets") if isinstance(catalog, dict) else catalog
        if not isinstance(nodes, dict):
            raise SystemExit("cli: catalog must map asset_id -> {dependencies}")
        catalog_assets = nodes
    engine = blast_radius.BlastRadiusEngine(manifests,
                                            catalog=catalog_assets)
    try:
        report = engine.compute(args.asset, ref=args.ref)
    except blast_radius.BlastRadiusError as exc:
        raise SystemExit("cli: %s" % exc)
    if args.json:
        print(_dumps(report))
    else:
        summary = report["summary"]
        print("blast radius: change to %s affects %d asset(s) and %d "
              "consumer(s)"
              % (args.asset, summary["assets_affected"],
                 summary["consumers_affected"]))
        print("  closure: %s" % ", ".join(report["closure"]))
        for consumer in report["consumers"]:
            print("  %-16s %s" % (consumer["consumer"],
                                  ", ".join(consumer["affected_assets"])))
    return 0


def _manifest_from_args(args):
    manifest = provenance.ProvenanceManifest.load(args.manifest)
    findings, errors = provenance.validate_manifest(manifest)
    if errors:
        for f in findings:
            print("%s\t%s\t%s\t%s" % (f.get("asset"), f.get("severity"),
                                      f.get("code"), f.get("message")))
        raise SystemExit("cli: manifest %s is invalid (%d error(s))"
                         % (args.manifest, errors))
    return manifest


def cmd_sync_plan(args):
    manifest = _manifest_from_args(args)
    desired = _load_json(args.desired, "desired")
    desired_assets = desired.get("assets") if isinstance(desired, dict) \
        else desired
    if not isinstance(desired_assets, dict):
        raise SystemExit("cli: desired must map asset_id -> target record")
    actions = sync_plan.plan_provenance_sync(manifest, desired_assets)
    if args.json:
        print(_dumps({"manifest": manifest.consumer_id(),
                      "actions": actions,
                      "dry_run": not args.apply}))
        return 0
    for action in actions:
        print("  %-8s %-20s v%-8s %s"
              % (action["op"], action.get("asset"),
                 action.get("version"), action.get("detail")))
    if not args.apply:
        print("cli: dry-run plan for %s (%d action(s)); pass --apply to "
              "materialize" % (manifest.consumer_id(), len(actions)))
        return 0
    materializer = sync_plan.AssetMaterializer(
        local_root=args.local_root, canonical_root=args.canonical_root)
    engine = sync_plan.SyncEngine(executor=materializer.execute,
                                  rollback_executor=materializer.rollback)
    report = engine.run(actions, apply=True)
    for outcome in report["actions"]:
        print("  %-8s %-20s %s"
              % (outcome.get("status"), outcome.get("asset"),
                 outcome.get("error") or outcome.get("detail")))
    if report["failed"] or report["rolled_back"]:
        print("cli: apply finished with %d applied, %d rolled-back, %d failed"
              % (report["applied"], report["rolled_back"], report["failed"]))
        return 1
    print("cli: apply finished with %d applied (0 failed)"
          % report["applied"])
    return 0


def cmd_sync_check(args):
    """One-shot gate: provenance valid AND drift tri-state (rc parity)."""
    manifest = _manifest_from_args(args)
    desired = _load_json(args.desired, "desired") if args.desired else None
    report = drift.check_manifest(manifest, local_root=args.local_root,
                                  canonical_root=args.canonical_root,
                                  desired=desired)
    summary = report["summary"]
    print("sync check: %s (%d clean / %d drift / %d cannot-assess) — "
          "consumer %s"
          % (report["verdict"].value, summary["clean"], summary["drift"],
             summary["cannot_assess"], manifest.consumer_id()))
    for row in report["assets"]:
        if row["state"] is not drift.DriftState.CLEAN:
            print("  %-12s %-16s %s"
                  % (row["asset"], row["state"].value, row["reason"]))
    if report["verdict"] is drift.DriftState.CLEAN:
        return 0
    if report["verdict"] is drift.DriftState.DRIFT:
        return 1
    return 2


def build_parser():
    parser = argparse.ArgumentParser(
        prog="ao-sync", description=__doc__.splitlines()[1])
    sub = parser.add_subparsers(dest="command", required=True)

    prov = sub.add_parser("provenance", help="provenance manifest tooling")
    prov_sub = prov.add_subparsers(dest="prov_command", required=True)

    gen = prov_sub.add_parser("generate", help="generate a manifest")
    gen.add_argument("--inventory", required=True,
                     help="JSON inventory of consumed assets")
    gen.add_argument("--consumer", required=True, help="consumer id")
    gen.add_argument("--consumer-repo", default=None, help="consumer repo")
    gen.add_argument("--local-root", default=None,
                     help="local root to hash content pins from")
    gen.add_argument("--out", required=True, help="output manifest path")
    gen.add_argument("--generated", default=None, help="ISO timestamp")
    gen.set_defaults(func=cmd_provenance_generate)

    val = prov_sub.add_parser("validate", help="validate a manifest")
    val.add_argument("manifest", help="provenance manifest JSON")
    val.set_defaults(func=cmd_provenance_validate)

    dr = sub.add_parser("drift", help="drift-check a consumer manifest")
    dr.add_argument("manifest", help="provenance manifest JSON")
    dr.add_argument("--local-root", default=".",
                    help="consumer local root (default cwd)")
    dr.add_argument("--canonical-root", default=None,
                    help="canonical source mirror root")
    dr.add_argument("--desired", default=None,
                    help="JSON desired {asset: version} (version drift)")
    dr.add_argument("--json", action="store_true")
    dr.set_defaults(func=cmd_drift)

    bl = sub.add_parser("blast", help="blast-radius for a shared-asset change")
    bl.add_argument("asset", help="shared asset id being changed")
    bl.add_argument("--ref", default=None, help="proposed ref/tag")
    bl.add_argument("--manifests", nargs="+", required=True,
                    help="consumer manifest file(s) or a directory of *.json")
    bl.add_argument("--catalog", default=None,
                    help="JSON dependency catalog (asset -> dependencies)")
    bl.add_argument("--json", action="store_true")
    bl.set_defaults(func=cmd_blast)

    sync = sub.add_parser("sync", help="reconcile plan / gate")
    sync_sub = sync.add_subparsers(dest="sync_command", required=True)

    sp = sync_sub.add_parser("plan", help="plan (dry-run) or apply a sync")
    sp.add_argument("manifest", help="current provenance manifest JSON")
    sp.add_argument("--desired", required=True,
                    help="JSON desired state (asset -> target record)")
    sp.add_argument("--local-root", default=".",
                    help="consumer local root (default cwd)")
    sp.add_argument("--canonical-root", default=None,
                    help="canonical source mirror root (apply mode)")
    sp.add_argument("--apply", action="store_true",
                    help="materialize instead of dry-run")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_sync_plan)

    sc = sync_sub.add_parser("check", help="provenance + drift one-shot gate")
    sc.add_argument("manifest", help="provenance manifest JSON")
    sc.add_argument("--local-root", default=".",
                    help="consumer local root (default cwd)")
    sc.add_argument("--canonical-root", default=None,
                    help="canonical source mirror root")
    sc.add_argument("--desired", default=None,
                    help="JSON desired {asset: version} (version drift)")
    sc.set_defaults(func=cmd_sync_check)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (provenance.ProvenanceError,
            sync_plan.SyncPlanError,
            blast_radius.BlastRadiusError,
            OSError, ValueError) as exc:
        print("cli: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
