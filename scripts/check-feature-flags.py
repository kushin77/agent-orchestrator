#!/usr/bin/env python3
"""Feature-flag registry gate for `make verify` (issue #6 / IaC mandate).

Validates `infra/feature-flags/registry.yaml`, keeps it in lock-step with
`infra/terraform/variables.tf`, and cross-validates it against the portal's
own `portal/config/feature-flags.yaml` declaration. Policy reversed by
explicit owner decision
(2026-09-21, single-developer environment; policy-gr5-enabled-by-default PR):
new capabilities ship ENABLED by default once merged and tested — a
capability is either fully built and ON, or not yet merged. There is no more
"built but off" state, so:

  (1) the registry parses and carries `default_policy: on`,
  (2) every declared `services:`/`surfaces:` entry defaults to ON,
  (3) the seven canonical control-plane services are present in the registry,
  (4) the CI/CD trigger flags (verify_trigger, apply_trigger) remain a
      separate pipeline-mechanics concern and still default to OFF (they are
      not "new capabilities" in the reversed policy's sense),
  (5) every terraform `enable_*` flag in variables.tf has an explicit
      `default = true`, and the `enable_<service>` flags match the registry
      service names 1:1 (a flag without a registry entry — or vice versa — is a
      drift finding),
  (6) every not-yet-promoted `services:`/`surfaces:` entry still carries a
      promotion owner — a non-empty `promotion_issue:` or `posture: hold`
      (issue #1618) — so a live-but-not-yet-fully-promoted surface cannot
      drift with nobody responsible,
  (7) every surface declared in BOTH this registry's `surfaces:` map and
      `portal/config/feature-flags.yaml`'s `surfaces:` map carries the same
      `default` — the two files drifted silently once (issue #1941: three
      surfaces flipped ON here but stayed OFF in the portal's own reader),
      so this is an intersection check, refusing by name on any mismatch
      (a portal-only surface with no registry counterpart is not compared,
      and a surface in `PORTAL_DRIFT_EXEMPT` — currently only erp_module,
      which carries its own dedicated promotion gate — is not compared
      either).

Every branch above can genuinely fail; nothing here is a formality.

---knowledge---
module_id: scripts.check-feature-flags
system: governance
app: gates
solution_class: enterprise
patterns: [provoked-negative-control, self-proving-gate, named-refusal, feature-flag-gated-off]
derives_from: null
owner_sme: qa-sme
tier: L1
interfaces: [exit 0 OK, exit 1 NOT-OK]
invariants: ""
gotchas: ""
related: ["#6", "#1618", "#1941"]
do_not_duplicate: null
---knowledge---
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
PORTAL_FLAGS = os.path.join(ROOT, "portal", "config", "feature-flags.yaml")

# Flags this checker accepts at "off" under policy-gr5-enabled-by-default: the
# owner's default is ON, not a prohibition on off, so an entry here still
# needs a cited decision (checked by the reader modules, not mechanically
# here). enable_paperclip's 2026-09-20 NO-GO (issue #1515) was superseded by
# explicit owner call on 2026-09-21 (see infra/terraform/variables.tf and
# infra/feature-flags/registry.yaml) — it defaults on like everything else,
# so this set is currently empty.
OFF_BY_EXPLICIT_DECISION: set[str] = set()

# Surfaces exempt from the portal-drift cross-check (issue #1941), because
# their portal declaration is deliberately NOT the registry's default: the
# surface carries its own dedicated promotion gate with its own reason to
# stay off. A name goes here only with a cited gate, never to silence a
# genuine drift.
PORTAL_DRIFT_EXEMPT: dict[str, str] = {
    "erp_module": (
        "own promotion gate (scripts/check-erp-portal.sh, "
        "e2e/erp/golden_path.py, e2e/erp/negative_controls.py) requires the "
        "shipped default stay off; promoted only through a scratch config"
    ),
}

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


def _owner_errors(reg: dict) -> list[str]:
    """Promotion-owner findings (issue #1618): a declared-off surface with no
    owner. Returns the error strings; pure (never exits, never prints)."""
    findings: list[str] = []
    for section in ("services", "surfaces"):
        entries = reg.get(section)
        if not isinstance(entries, dict):
            continue
        for name, entry in entries.items():
            if not isinstance(entry, dict):
                continue
            if entry.get("promoted"):
                continue  # already shipped; promotion recorded by `promoted: true`
            has_owner = bool(entry.get("promotion_issue"))
            is_hold = entry.get("posture") == "hold"
            if not has_owner and not is_hold:
                findings.append(
                    f"{section}.{name} is not yet promoted and carries neither "
                    "promotion_issue nor posture: hold"
                )
    return findings


def _normalize_default(value) -> Optional[bool]:
    """PyYAML's `off`/`on` and the string spellings both mean OFF/ON."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        low = value.strip().lower()
        if low == "on":
            return True
        if low == "off":
            return False
    return None


def _portal_drift_errors(reg: dict, portal_doc) -> list[str]:
    """Cross-check issue #1941: every surface declared in BOTH
    `infra/feature-flags/registry.yaml` (surfaces:) and
    `portal/config/feature-flags.yaml` (surfaces:) must carry the same
    `default`. A portal-only surface (no registry counterpart — e.g.
    fleet_board, sessions, settings) is not compared; this is an
    intersection check, not a "every portal surface must be registered" rule.
    Pure (never exits, never prints) — returns the error strings.
    """
    findings: list[str] = []
    reg_surfaces = reg.get("surfaces")
    if not isinstance(reg_surfaces, dict):
        reg_surfaces = {}
    portal_surfaces = (portal_doc or {}).get("surfaces")
    if not isinstance(portal_surfaces, dict):
        portal_surfaces = {}

    for name in sorted(set(reg_surfaces) & set(portal_surfaces)):
        if name in PORTAL_DRIFT_EXEMPT:
            continue
        reg_entry = reg_surfaces.get(name) or {}
        portal_entry = portal_surfaces.get(name) or {}
        reg_default = _normalize_default(reg_entry.get("default"))
        portal_default = _normalize_default(portal_entry.get("default"))
        if reg_default is None or portal_default is None:
            findings.append(
                f"surfaces.{name}: unparseable default "
                f"(registry={reg_entry.get('default')!r}, "
                f"portal={portal_entry.get('default')!r})"
            )
            continue
        if reg_default != portal_default:
            findings.append(
                f"surfaces.{name}: registry.yaml default="
                f"{reg_entry.get('default')!r} but portal/config/"
                f"feature-flags.yaml default={portal_entry.get('default')!r} "
                "(drifted — issue #1941)"
            )
    return findings


def _run_self_test() -> int:
    """Prove both directions of the promotion-owner rule (issue #1618).

    A missing owner is refused by name; a present owner (either `promotion_issue`
    or `posture: hold`) passes; and the real registry is fully owned/held. A
    probe that does not behave as asserted fails, so the arm cannot pass vacuously.
    """
    print("== check-feature-flags self-test (promotion-owner rule) ==")
    failed = 0

    def probe(name: str, want_errors: bool, reg: dict, needle: str = "") -> None:
        nonlocal failed
        errs = _owner_errors(reg)
        ok = bool(errs) if want_errors else not errs
        if ok and (not want_errors or not needle or any(needle in e for e in errs)):
            detail = f" (correctly failed: {errs[0]})" if want_errors and errs else ""
            print(f"  OK    {name}{detail}")
        else:
            print(f"  FAIL  {name}: errors={errs!r} (wanted {'a failure' if want_errors else 'clean'})", file=sys.stderr)
            failed += 1

    # (a) a non-promoted entry with neither field must be refused, by name.
    # NOTE (policy-gr5-enabled-by-default): these probes hardcoded the OLD
    # "default: off" policy value. The promotion-owner rule (#1618) is about
    # who owns the go-live, not the flag's on/off value, so the probes are
    # updated to use "default: on" (the new correct default) rather than
    # silently keeping the stale "off" fixture.
    probe(
        "missing owner refused",
        True,
        {"services": {"probe_svc": {"default": "on", "promoted": False}}, "surfaces": {}},
        needle="services.probe_svc",
    )
    # (b) the same entry with `posture: hold` passes.
    probe(
        "posture: hold accepted",
        False,
        {"services": {"probe_svc": {"default": "on", "promoted": False, "posture": "hold"}}, "surfaces": {}},
    )
    # (c) the same entry with a `promotion_issue` passes.
    probe(
        "promotion_issue accepted",
        False,
        {"services": {"probe_svc": {"default": "on", "promoted": False, "promotion_issue": "#1"}}, "surfaces": {}},
    )
    # (d) a promoted entry is exempt (already shipped).
    probe(
        "promoted entry exempt",
        False,
        {"services": {"probe_svc": {"default": "on", "promoted": True}}, "surfaces": {}},
    )
    # (e) the real registry is fully owned or held.
    probe("real registry fully owned/held", False, load_registry())

    # (f)-(i): the portal-drift cross-check (issue #1941).
    def probe_drift(name: str, want_errors: bool, reg: dict, portal: dict, needle: str = "") -> None:
        nonlocal failed
        errs = _portal_drift_errors(reg, portal)
        ok = bool(errs) if want_errors else not errs
        if ok and (not want_errors or not needle or any(needle in e for e in errs)):
            print(f"  OK    {name}")
        else:
            print(f"  FAIL  {name}: errors={errs!r} (wanted {'a failure' if want_errors else 'clean'})", file=sys.stderr)
            failed += 1

    # (f) a mismatched pair is refused, by name.
    probe_drift(
        "mismatched surface refused",
        True,
        {"surfaces": {"probe_surface": {"default": "on"}}},
        {"surfaces": {"probe_surface": {"default": "off"}}},
        needle="surfaces.probe_surface",
    )
    # (g) a matched pair (both on) passes.
    probe_drift(
        "matched surface (on) accepted",
        False,
        {"surfaces": {"probe_surface": {"default": "on"}}},
        {"surfaces": {"probe_surface": {"default": "on"}}},
    )
    # (h) a portal-only surface (no registry counterpart) is not compared.
    probe_drift(
        "portal-only surface ignored",
        False,
        {"surfaces": {}},
        {"surfaces": {"portal_only_surface": {"default": "off"}}},
    )
    # (j) an exempt surface's mismatch is not flagged.
    probe_drift(
        "exempt surface mismatch ignored",
        False,
        {"surfaces": {"erp_module": {"default": "on"}}},
        {"surfaces": {"erp_module": {"default": "off"}}},
    )
    # (i) the real registry and the real portal declaration agree.
    portal_path = PORTAL_FLAGS
    if os.path.isfile(portal_path):
        with open(portal_path, encoding="utf-8") as fh:
            real_portal = yaml.safe_load(fh)
        probe_drift("real registry/portal surfaces agree", False, load_registry(), real_portal)

    if failed:
        print(f"check-feature-flags self-test: {failed} probe(s) failed", file=sys.stderr)
        return 1
    print("check-feature-flags self-test: OK")
    return 0


def main(argv=None) -> int:
    if "--self-test" in (argv or sys.argv[1:]):
        return _run_self_test()

    reg = load_registry()

    # (1) default_policy
    if not isinstance(reg, dict):
        fail("registry top-level is not a mapping")
        finish()
    # PyYAML parses the bare YAML 1.1 scalar `off` as boolean False; both
    # forms mean OFF and are accepted.
    if reg.get("default_policy") not in (True, "on"):
        fail("registry default_policy must be 'on' (policy-gr5-enabled-by-default: new flags default ON)")

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
        if f"enable_{svc}" in OFF_BY_EXPLICIT_DECISION:
            if default not in (False, "off"):
                fail(f"services.{svc} has an explicit NO-GO decision; expected default: off")
            continue
        if default not in (True, "on"):
            fail(f"services.{svc}.default must be on (got {default!r})")

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
        if name in OFF_BY_EXPLICIT_DECISION:
            if default is not False:
                fail(f"terraform variable {name} has an explicit NO-GO decision; expected default = false")
            continue
        if default is None:
            fail(f"terraform variable {name} has no explicit default (must be true)")
        elif not default:
            fail(f"terraform variable {name} must default to true (got false)")

    tf_service_flags = {name[len("enable_"):] for name in tf_defaults}
    registry_services = set(services)
    if tf_service_flags != registry_services:
        only_tf = sorted(tf_service_flags - registry_services)
        only_reg = sorted(registry_services - tf_service_flags)
        if only_tf:
            fail(f"terraform flags without registry entries: {', '.join(only_tf)}")
        if only_reg:
            fail(f"registry services without terraform enable_ flags: {', '.join(only_reg)}")

    # (6) promotion owners: a declared-off surface with no owner is refused.
    for finding in _owner_errors(reg):
        fail(finding)

    # (7) portal/config/feature-flags.yaml drift (issue #1941): every surface
    # declared in both files must agree on `default`.
    if os.path.isfile(PORTAL_FLAGS):
        with open(PORTAL_FLAGS, encoding="utf-8") as fh:
            portal_doc = yaml.safe_load(fh)
        for finding in _portal_drift_errors(reg, portal_doc):
            fail(finding)

    finish()
    return 0


if __name__ == "__main__":
    sys.exit(main())
