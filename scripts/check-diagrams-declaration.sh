#!/usr/bin/env bash
# diagrams-declaration gate (issue #464, EPIC #461 / GR-18 / CMR:ONBOARD-0005).
#
# Proves this repo carries the `diagrams` mandatory consumer surface and that
# both declaration seeds conform to the VENDORED CMR contract:
#
#   * architecture.yaml  -> schema `cmr.architecture-manifest/v1`, validated
#     against vendor/CMR/catalog/schemas/architecture-manifest.schema.json,
#     plus semantic checks the schema cannot express (the `generated` block
#     must stay null until a sync runs; any declared generator entrypoint must
#     actually exist on disk).
#   * gdc-manifest.yaml   -> schema `cmr.gdc-manifest/v1` (validator.ts) and,
#     above all, the `diagrams.blueprint` pin held in lockstep with the
#     vendored templates/module/gdc-manifest.yaml (module id, version range,
#     update policy). A pin that drifts from the vendor template is refused.
#
# Tri-state, honest (GR-12, no false green):
#   0  conformant
#   1  violation — every offending field is named on stderr
#   2  CANNOT-ASSESS — the vendored schema/template is unreadable (e.g. the
#      vendor/CMR submodule is not initialised in this worktree) or no
#      JSON-Schema engine is available. An unreadable contract is NEVER
#      reported as a pass.
#
# Offline and deterministic. `--self-test` runs internal negative controls:
# each one constructs a single violation class in a scratch directory (the
# real subjects are never mutated) and asserts the gate refuses it, naming the
# field. The controls are real — if the gate stops refusing a class, the
# self-test itself goes red.
#
# Usage:
#   scripts/check-diagrams-declaration.sh              # validate this repo
#   scripts/check-diagrams-declaration.sh --self-test  # internal controls
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 - "$root" "$@" <<'PY'
import copy
import json
import os
import re
import shutil
import sys
import tempfile

try:
    import yaml
except Exception as exc:
    sys.stderr.write(
        "check-diagrams-declaration: CANNOT-ASSESS - PyYAML unavailable: %r\n" % (exc,)
    )
    sys.exit(2)

try:
    from jsonschema import Draft7Validator

    _ENGINE_ERROR = None
except Exception as exc:
    Draft7Validator = None
    _ENGINE_ERROR = exc

ARCH_SCHEMA_REL = "catalog/schemas/architecture-manifest.schema.json"
GDC_SCHEMA_REL = "catalog/schemas/gdc-manifest.schema.json"
GDC_TEMPLATE_REL = "templates/module/gdc-manifest.yaml"

ARCH_SUBJECT = "architecture.yaml"
GDC_SUBJECT = "gdc-manifest.yaml"
DIAGRAMS_MODULE = "diagrams.blueprint"
GENERATED_NULL_KEYS = ("last_synced", "synced_by", "content_hash")


# --- helpers ----------------------------------------------------------------

def load_yaml(path):
    """Return (obj, None) or (None, error)."""
    try:
        with open(path, encoding="utf-8") as fh:
            return yaml.safe_load(fh), None
    except Exception as exc:
        return None, exc


def fmt_error(err, label):
    """Render one JSON-Schema error, naming the offending field."""
    path = ".".join(str(p) for p in err.absolute_path)
    if err.validator == "required":
        m = re.search(r"'([^']+)' is a required property", err.message)
        key = m.group(1) if m else "?"
        loc = "%s.%s" % (path, key) if path else key
        return "%s: %s: missing required key" % (label, loc)
    if err.validator == "additionalProperties":
        m = re.search(r"\('([^']+)' was unexpected\)", err.message)
        key = m.group(1) if m else "?"
        loc = "%s.%s" % (path, key) if path else key
        return "%s: %s: unexpected key" % (label, loc)
    loc = path or "<root>"
    return "%s: %s: %s" % (label, loc, err.message)


def schema_errors(instance, schema, label):
    validator = Draft7Validator(schema)
    errors = sorted(
        validator.iter_errors(instance),
        key=lambda e: (list(e.absolute_path), e.message),
    )
    return [fmt_error(e, label) for e in errors]


def arch_semantic_errors(arch, subject_dir):
    """Checks the vendored schema cannot express."""
    errs = []
    if not isinstance(arch, dict):
        return errs

    gen = arch.get("generated")
    if isinstance(gen, dict):
        for key in GENERATED_NULL_KEYS:
            if key in gen and gen[key] is not None:
                errs.append(
                    "%s: generated.%s: %r - the sync-populated block must stay "
                    "null until a sync pass runs (never hand-filled)"
                    % (ARCH_SUBJECT, key, gen[key])
                )

    gen_block = arch.get("generator")
    if isinstance(gen_block, dict):
        kind = gen_block.get("kind")
        entry = gen_block.get("entrypoint")
        if kind == "manual" and entry not in (None, ""):
            errs.append(
                "%s: generator.entrypoint: %r declared while generator.kind is "
                "'manual'" % (ARCH_SUBJECT, entry)
            )
        if isinstance(entry, str) and entry:
            if not os.path.exists(os.path.join(subject_dir, entry)):
                errs.append(
                    "%s: generator.entrypoint: %r does not exist in this repo"
                    % (ARCH_SUBJECT, entry)
                )
    return errs


def gdc_semantic_errors(gdc, template):
    """Mandatory pins held in lockstep with the vendored template."""
    errs = []
    if not isinstance(gdc, dict):
        return errs

    def index(pins):
        out = {}
        for pin in pins or []:
            if isinstance(pin, dict) and isinstance(pin.get("module"), str):
                out[pin["module"]] = pin
        return out

    subject_pins = index(gdc.get("modules"))
    template_pins = index(template.get("modules"))

    if DIAGRAMS_MODULE not in template_pins:
        errs.append(
            "%s: vendored template declares no %r pin (vendor drift - cannot "
            "be in lockstep with a template that lacks it)"
            % (GDC_TEMPLATE_REL, DIAGRAMS_MODULE)
        )

    for module in sorted(template_pins):
        tpin = template_pins[module]
        s_pin = subject_pins.get(module)
        if s_pin is None:
            errs.append(
                "%s: modules[]: missing mandatory pin %r (the vendored template "
                "declares it; mandatory pins must not be removed)"
                % (GDC_SUBJECT, module)
            )
            continue
        if s_pin.get("version") != tpin.get("version"):
            errs.append(
                "%s: modules[%s].version: %r != vendored %r"
                % (GDC_SUBJECT, module, s_pin.get("version"), tpin.get("version"))
            )
        if s_pin.get("updates") != tpin.get("updates"):
            errs.append(
                "%s: modules[%s].updates: %r != vendored %r"
                % (GDC_SUBJECT, module, s_pin.get("updates"), tpin.get("updates"))
            )

    if DIAGRAMS_MODULE not in subject_pins:
        errs.append(
            "%s: modules[]: missing mandatory pin %r (feature-qualified, "
            "updates: pr)" % (GDC_SUBJECT, DIAGRAMS_MODULE)
        )
    return errs


# --- core -------------------------------------------------------------------

def evaluate(subject_dir, schema_dir):
    """Return (rc, messages). rc: 0 conformant, 1 violation, 2 cannot-assess."""
    arch_schema_path = os.path.join(schema_dir, ARCH_SCHEMA_REL)
    gdc_schema_path = os.path.join(schema_dir, GDC_SCHEMA_REL)
    gdc_template_path = os.path.join(schema_dir, GDC_TEMPLATE_REL)

    unreadable = [
        p for p in (arch_schema_path, gdc_schema_path, gdc_template_path)
        if not os.path.isfile(p)
    ]
    if unreadable:
        return 2, [
            "check-diagrams-declaration: CANNOT-ASSESS - vendored contract "
            "unreadable: %s (initialise vendor/CMR: git submodule update "
            "--init vendor/CMR)" % ", ".join(unreadable)
        ]
    if Draft7Validator is None:
        return 2, [
            "check-diagrams-declaration: CANNOT-ASSESS - no JSON-Schema engine "
            "available: %r" % (_ENGINE_ERROR,)
        ]

    try:
        with open(arch_schema_path, encoding="utf-8") as fh:
            arch_schema = json.load(fh)
        with open(gdc_schema_path, encoding="utf-8") as fh:
            gdc_schema = json.load(fh)
    except Exception as exc:
        return 2, [
            "check-diagrams-declaration: CANNOT-ASSESS - unreadable vendored "
            "schema JSON: %r" % (exc,)
        ]

    template, t_err = load_yaml(gdc_template_path)
    if t_err is not None or not isinstance(template, dict):
        return 2, [
            "check-diagrams-declaration: CANNOT-ASSESS - unreadable vendored "
            "gdc template: %r" % (t_err,)
        ]

    msgs = []

    arch_path = os.path.join(subject_dir, ARCH_SUBJECT)
    if not os.path.isfile(arch_path):
        msgs.append(
            "%s: MISSING - the diagrams mandatory consumer surface declares no "
            "architecture manifest" % ARCH_SUBJECT
        )
    else:
        arch, a_err = load_yaml(arch_path)
        if a_err is not None:
            msgs.append("%s: yaml parse error: %r" % (ARCH_SUBJECT, a_err))
        else:
            msgs.extend(schema_errors(arch, arch_schema, ARCH_SUBJECT))
            msgs.extend(arch_semantic_errors(arch, subject_dir))

    gdc_path = os.path.join(subject_dir, GDC_SUBJECT)
    if not os.path.isfile(gdc_path):
        msgs.append(
            "%s: MISSING - the diagrams mandatory consumer surface declares no "
            "gdc manifest (the %s pin lives here)" % (GDC_SUBJECT, DIAGRAMS_MODULE)
        )
    else:
        gdc, g_err = load_yaml(gdc_path)
        if g_err is not None:
            msgs.append("%s: yaml parse error: %r" % (GDC_SUBJECT, g_err))
        else:
            msgs.extend(schema_errors(gdc, gdc_schema, GDC_SUBJECT))
            msgs.extend(gdc_semantic_errors(gdc, template))

    # deterministic order + no duplicate complaints
    msgs = sorted(set(msgs))
    return (1 if msgs else 0), msgs


# --- modes ------------------------------------------------------------------

def cmd_check(root, schema_dir):
    rc, msgs = evaluate(root, schema_dir)
    if rc == 0:
        print(
            "check-diagrams-declaration: OK - architecture.yaml + "
            "gdc-manifest.yaml conform to the vendored contract; the "
            "%s pin is in lockstep" % DIAGRAMS_MODULE
        )
        return 0
    for m in msgs:
        sys.stderr.write(m + "\n")
    if rc == 2:
        sys.stderr.write("check-diagrams-declaration: CANNOT-ASSESS\n")
    else:
        sys.stderr.write(
            "check-diagrams-declaration: FAIL - %d violation(s)\n" % len(msgs)
        )
    return rc


def _write_yaml(path, obj):
    with open(path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(obj, fh, allow_unicode=True, sort_keys=False)


def _drop_key(key):
    def mutate(arch, gdc):
        arch.pop(key, None)
    return mutate


def _rename_key(old, new):
    def mutate(arch, gdc):
        if old in arch:
            arch[new] = arch.pop(old)
    return mutate


def _set_nested(path, value):
    def mutate(arch, gdc):
        node = arch
        for part in path[:-1]:
            node = node[part]
        node[path[-1]] = value
    return mutate


def _set_generated_null_violation(key):
    def mutate(arch, gdc):
        arch["generated"][key] = "2026-09-14T00:00:00Z"
    return mutate


def _set_generator(block):
    def mutate(arch, gdc):
        arch["generator"] = dict(block)
    return mutate


def _drop_module_pin(module):
    def mutate(arch, gdc):
        gdc["modules"] = [
            p for p in gdc.get("modules", [])
            if not (isinstance(p, dict) and p.get("module") == module)
        ]
    return mutate


def _set_module_field(module, field, value):
    def mutate(arch, gdc):
        for p in gdc.get("modules", []):
            if isinstance(p, dict) and p.get("module") == module:
                p[field] = value
    return mutate


def _cases():
    """(name, mutator, expected field token) - one per violation class."""
    return [
        ("missing-required-key", _drop_key("generator"), "generator"),
        ("renamed-key", _rename_key("live_resources", "live_resources_x"),
         "live_resources"),
        ("schema-const", _set_nested(["schema"], "cmr.architecture-manifest/v2"),
         ARCH_SUBJECT + ": schema"),
        ("schema-enum", _set_nested(["generator", "kind"], "bogus"),
         "generator.kind"),
        ("schema-minlength", _set_nested(["terraform_state"], [{"root": "", "backend": "local"}]),
         "terraform_state.0.root"),
        ("schema-required-item", _set_nested(["terraform_state"], [{"backend": "local"}]),
         "terraform_state.0.root"),
        ("generated-not-null", _set_generated_null_violation("last_synced"),
         "generated.last_synced"),
        ("generator-entrypoint-absent",
         _set_generator({"kind": "python-module", "entrypoint": "no/such/gen.py"}),
         "generator.entrypoint"),
        ("missing-diagrams-pin", _drop_module_pin(DIAGRAMS_MODULE),
         DIAGRAMS_MODULE),
        ("pin-not-lockstep", _set_module_field(DIAGRAMS_MODULE, "updates", "manual"),
         DIAGRAMS_MODULE),
    ]


def cmd_self_test(root, schema_dir):
    base_arch, a_err = load_yaml(os.path.join(root, ARCH_SUBJECT))
    base_gdc, g_err = load_yaml(os.path.join(root, GDC_SUBJECT))
    if a_err is not None or g_err is not None or not isinstance(base_arch, dict) \
            or not isinstance(base_gdc, dict):
        sys.stderr.write(
            "check-diagrams-declaration: CANNOT-ASSESS - self-test base "
            "subjects unreadable (architecture.yaml %r, gdc-manifest.yaml %r)\n"
            % (a_err, g_err)
        )
        return 2

    # A control only proves anything if the un-mutated baseline is green.
    base_rc, base_msgs = evaluate(root, schema_dir)
    if base_rc != 0:
        sys.stderr.write(
            "check-diagrams-declaration: CANNOT-ASSESS - baseline subject is "
            "already non-conformant (rc=%d); controls would be meaningless\n"
            % base_rc
        )
        for m in base_msgs:
            sys.stderr.write("    baseline: %s\n" % m)
        return 2

    failures = 0
    print("== diagrams-declaration self-test ==")
    for name, mutate, expect in _cases():
        work = tempfile.mkdtemp(prefix="diagrams-decl-self-")
        try:
            arch = copy.deepcopy(base_arch)
            gdc = copy.deepcopy(base_gdc)
            mutate(arch, gdc)
            _write_yaml(os.path.join(work, ARCH_SUBJECT), arch)
            _write_yaml(os.path.join(work, GDC_SUBJECT), gdc)
            rc, msgs = evaluate(work, schema_dir)
            named = [m for m in msgs if expect in m]
            ok = rc == 1 and bool(named)
            print("  %s  %-26s rc=%d expects %r" %
                  ("OK  " if ok else "FAIL", name, rc, expect))
            if not ok:
                failures += 1
                for m in msgs:
                    sys.stderr.write("      got: %s\n" % m)
        finally:
            shutil.rmtree(work, ignore_errors=True)

    total = len(_cases())
    if failures:
        sys.stderr.write(
            "check-diagrams-declaration self-test: FAIL - %d of %d control(s) "
            "not refused\n" % (failures, total)
        )
        return 1
    print("check-diagrams-declaration self-test: OK - %d/%d controls refused "
          "by field name" % (total, total))
    return 0


def main():
    argv = sys.argv[1:]
    if not argv:
        sys.stderr.write("usage: check-diagrams-declaration.sh [--self-test] "
                         "[--schema-dir DIR]\n")
        return 2
    root = argv[0]
    rest = argv[1:]
    schema_dir = os.path.join(root, "vendor", "CMR")
    mode = "check"
    i = 0
    while i < len(rest):
        arg = rest[i]
        if arg == "--self-test":
            mode = "self-test"
        elif arg == "--schema-dir":
            i += 1
            if i >= len(rest):
                sys.stderr.write("check-diagrams-declaration: --schema-dir "
                                 "needs an argument\n")
                return 2
            schema_dir = rest[i]
        elif arg in ("-h", "--help"):
            print("usage: check-diagrams-declaration.sh [--self-test] "
                  "[--schema-dir DIR]")
            return 0
        else:
            sys.stderr.write("check-diagrams-declaration: unknown argument "
                             "%r\n" % arg)
            return 2
        i += 1

    if mode == "self-test":
        return cmd_self_test(root, schema_dir)
    return cmd_check(root, schema_dir)


if __name__ == "__main__":
    sys.exit(main())
PY
