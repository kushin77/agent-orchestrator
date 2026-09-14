#!/usr/bin/env python3
"""CLI for the AgentIdentity parity check (issue #346).

Runs the whole check offline and reports drift with stable finding codes:

  0  OK            the shared schema's closed vocabularies equal this repo's
                   declared vocabularies (agent-profile.schema.json +
                   catalog.yaml) and every projected seed validates
  1  NOT-OK        at least one drift finding (a vocabulary/required-field
                   disagreement on either side, or a seed that does not
                   satisfy the shared schema)
  2  CANNOT-ASSESS an input the check needs is absent/unreadable, or
                   jsonschema/PyYAML is unavailable (fail closed, never OK)

Every input is overridable so a caller (scripts/check-agent-identity-parity.sh,
the test suite) can point the check at a scratch copy and PROVE it can fail.

Usage:
  python3 registry/profiles/parity/cli.py
  python3 registry/profiles/parity/cli.py --json
  python3 registry/profiles/parity/cli.py --schema F --profile-schema F \
      --catalog F --seeds-dir D
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import parity  # noqa: E402  (path bootstrap above is deliberate)


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="cli.py",
        description="shared agent-identity schema parity (issue #346)")
    parser.add_argument("--schema", default=parity.DEFAULT_SCHEMA,
                        help="the shared agent-identity schema (JSON)")
    parser.add_argument("--profile-schema",
                        default=parity.DEFAULT_PROFILE_SCHEMA,
                        help="this repo's AgentProfile schema the shared "
                             "schema's vocabularies are pinned to")
    parser.add_argument("--catalog", default=parity.DEFAULT_CATALOG,
                        help="this repo's closed platform vocabulary")
    parser.add_argument("--seeds-dir", default=parity.DEFAULT_SEEDS,
                        help="directory of AgentProfile seeds to project and "
                             "validate")
    parser.add_argument("--json", action="store_true",
                        help="emit a machine-readable report")
    args = parser.parse_args(argv)

    status, lines = parity.evaluate(
        schema_path=args.schema,
        profile_schema_path=args.profile_schema,
        catalog_path=args.catalog,
        seeds_dir=args.seeds_dir,
    )

    if args.json:
        print(json.dumps({
            "status": status,
            "result": parity.RESULT_NAMES[status],
            "lines": lines,
        }, indent=2))
    else:
        for line in lines:
            print(line)
    return status


if __name__ == "__main__":
    sys.exit(main())
