#!/usr/bin/env python3
"""Feature-flag registry gate for `make verify` (issue #6 / IaC mandate).

Validates `infra/feature-flags/registry.yaml` and keeps it in lock-step with
`infra/terraform/variables.tf` so every control-plane surface ships OFF until
promoted:

  (1) the registry parses and carries `default_policy: off`,
  (2) every declared service entry defaults to OFF,
  (3) the seven canonical control-plane services are present in the registry,
  (4) the CI/CD trigger flags (verify_trigger, apply_trigger) default to OFF,
  (5) every terraform `enable_*` flag in variables.tf has an explicit
      `default = false`, and the `enable_<service>` flags match the registry
      service names 1:1 (a flag without a registry entry — or vice versa — is a
      drift finding).

Every branch above can genuinely fail; nothing here is a formality.
"""

from __future__ import annotations

import os
import re
import sys

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    print(f"check-feature-flags: PyYAML not installed ({exc})", file=sys.stderr)
    sys.exit(1)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REGISTRY = os.path.join(ROOT, "infra", "feature-flags", "registry.yaml")
TF_VARS = os.path.join(ROOT, "infra", "terraform", "variables.tf")

CANONICAL_SERVICES = [
    "registry",
    "gateway",
    "engine",
    "guardrails",
    "telemetry",
    "identity",
    "portal",
]

errors: list[str] = []


def fail(message: str) -> None:
    errors.append(message)
    print(f"  FAIL  {message}", file=sys.stderr)


def finish() -> None:
    if errors:
        print(f"feature-flags: {len(errors)} problem(s)", file=sys.stderr)
        sys.exit(1)
    print("feature-flags: OK")


def load_registry():
    if not os.path.isfile(REGISTRY):
        fail(f"{os.path.relpath(REGISTRY, ROOT)} missing")
        finish()
    with open(REGISTRY, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def main() -> None:
    reg = load_registry()

    # (1) default_policy
    if not isinstance(reg, dict):
        fail("registry top-level is not a mapping")
        finish()
    # PyYAML parses the bare YAML 1.1 scalar `off` as boolean False; both
    # forms mean OFF and are accepted.
    if reg.get("default_policy") not in (False, "off"):
        fail("registry default_policy must be 'off' (new flags default OFF)")

    # apply path must be declared and point at the automated route only.
    apply_path = reg.get("apply_path")
    if not isinstance(apply_path, dict) or not apply_path.get("only"):
        fail("registry apply_path.only must declare the flag-gated automated apply route")

    # (2)(3) services
    services = reg.get("services")
    if not isinstance(services, dict) or not services:
        fail("registry services must be a non-empty mapping")
        services = {}

    for svc, entry in services.items():
        if not isinstance(entry, dict):
            fail(f"services.{svc} is not a mapping")
            continue
        default = entry.get("default")
        if default not in (False, "off"):
            fail(f"services.{svc}.default must be off (got {default!r})")
        promoted = entry.get("promoted", False)
        if promoted:
            fail(f"services.{svc}.promoted must be false while default is off")

    for svc in CANONICAL_SERVICES:
        if svc not in services:
            fail(f"registry missing canonical service '{svc}'")

    # (4) CI/CD trigger flags
    ci_cd = reg.get("ci_cd")
    if not isinstance(ci_cd, dict):
        fail("registry ci_cd must be a mapping with verify_trigger and apply_trigger")
        ci_cd = {}
    for flag in ("verify_trigger", "apply_trigger"):
        entry = ci_cd.get(flag)
        if not isinstance(entry, dict) or entry.get("default") not in (False, "off"):
            fail(f"registry ci_cd.{flag} must default to off")

    # (5) terraform enable_* flags
    if not os.path.isfile(TF_VARS):
        fail(f"{os.path.relpath(TF_VARS, ROOT)} missing")
        finish()
    with open(TF_VARS, encoding="utf-8") as fh:
        tf_text = fh.read()

    blocks = re.findall(r'variable\s+"([^"]+)"\s*\{(.*?)\n\}', tf_text, re.S)
    tf_defaults: dict[str, bool | None] = {}
    for name, body in blocks:
        if not name.startswith("enable_"):
            continue
        match = re.search(r"default\s*=\s*(true|false)", body)
        tf_defaults[name] = (match.group(1) == "true") if match else None

    for name, default in sorted(tf_defaults.items()):
        if default is None:
            fail(f"terraform variable {name} has no explicit default (must be false)")
        elif default:
            fail(f"terraform variable {name} must default to false (got true)")

    tf_service_flags = {name[len("enable_"):] for name in tf_defaults}
    registry_services = set(services)
    if tf_service_flags != registry_services:
        only_tf = sorted(tf_service_flags - registry_services)
        only_reg = sorted(registry_services - tf_service_flags)
        if only_tf:
            fail(f"terraform flags without registry entries: {', '.join(only_tf)}")
        if only_reg:
            fail(f"registry services without terraform enable_ flags: {', '.join(only_reg)}")

    finish()


if __name__ == "__main__":
    main()
