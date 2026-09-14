#!/usr/bin/env bash
# codeidx-surface gate (issue #475, EPIC #472 / GR-17 / GR-18).
#
# Proves this repo carries the `code-indexing` mandatory consumer surface:
#
#   * `.mcp.json` (root) -> declares the indexer MCP server surface in the
#     SHAPE of the vendored mandatory seed
#     `vendor/CMR/templates/module/.mcp.json`. The seed's server name and its
#     entry shape (type / command / args) are read from the vendored contract,
#     never hard-coded here: a `.mcp.json` that omits the indexer entry — or a
#     server entry that has drifted from the seed — is refused by name.
#   * `gdc-manifest.yaml` (root) -> still carries the `code-indexing.mcp`
#     mandatory pin (`modules[]`). The pin is owned by #464; this gate only
#     READS it and refuses its absence.
#
# Tri-state, honest (GR-12, no false green):
#   0  conformant
#   1  violation — the offending file and the fault are named on stderr
#   2  CANNOT-ASSESS — the vendored mandatory seed is unreadable (e.g. the
#      vendor/CMR submodule is not initialised in this worktree) or PyYAML is
#      unavailable, so conformance to the mandatory contract cannot be judged.
#      An unreadable contract is NEVER reported as a pass.
#
# Offline and deterministic; no network, no containers. `--self-test` runs
# internal negative controls: each constructs one violation class in a scratch
# tree (the real subjects are never mutated) and asserts the gate refuses it,
# naming the fault. A class the gate stops refusing turns the self-test itself
# red — the controls are real, not decorative.
#
# Usage:
#   scripts/check-codeidx-surface.sh              # validate this repo
#   scripts/check-codeidx-surface.sh --self-test  # internal controls
set -u

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-codeidx-surface: CANNOT-ASSESS - python3 unavailable" >&2
  exit 2
fi

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 - "$root" "$@" <<'PY'
import json
import os
import shutil
import sys
import tempfile

try:
    import yaml
except Exception as exc:
    sys.stderr.write(
        "check-codeidx-surface: CANNOT-ASSESS - PyYAML unavailable: %r\n" % (exc,)
    )
    sys.exit(2)

MCP_SUBJECT = ".mcp.json"
GDC_SUBJECT = "gdc-manifest.yaml"
PIN_MODULE = "code-indexing.mcp"
SEED_REL = os.path.join("templates", "module", ".mcp.json")
SEED_LABEL = os.path.join("vendor", "CMR", SEED_REL)
REQUIRED_ENTRY_KEYS = ("type", "command", "args")


# --- contract ---------------------------------------------------------------

def load_seed(contract_dir):
    """Return (seed_servers, None) or (None, reason).

    The expected `.mcp.json` shape is DERIVED from the vendored mandatory seed,
    never restated here: if the seed cannot be read we cannot judge conformance
    and the gate is CANNOT-ASSESS.
    """
    path = os.path.join(contract_dir, SEED_REL)
    if not os.path.isfile(path):
        return None, (
            "vendored mandatory seed unreadable: %s (initialise vendor/CMR: "
            "git submodule update --init vendor/CMR)" % SEED_LABEL
        )
    try:
        with open(path, encoding="utf-8") as fh:
            seed = json.load(fh)
    except Exception as exc:
        return None, "vendored mandatory seed is not valid JSON: %r" % (exc,)
    servers = seed.get("mcpServers") if isinstance(seed, dict) else None
    if not isinstance(servers, dict) or not servers:
        return None, (
            "vendored mandatory seed declares no mcpServers entry - the "
            "mandatory shape cannot be derived from it"
        )
    return servers, None


# --- subjects ---------------------------------------------------------------

def mcp_errors(mcp_path, seed_servers):
    """Violations in the root `.mcp.json`, each naming the file and the fault."""
    if not os.path.isfile(mcp_path):
        return [
            "%s: MISSING - the code-indexing mandatory consumer surface declares "
            "no MCP server surface (expected in the shape of %s)"
            % (MCP_SUBJECT, SEED_LABEL)
        ]
    try:
        with open(mcp_path, encoding="utf-8") as fh:
            obj = json.load(fh)
    except Exception as exc:
        return ["%s: invalid JSON - %r" % (MCP_SUBJECT, exc)]

    if not isinstance(obj, dict) or not isinstance(obj.get("mcpServers"), dict):
        return [
            "%s: mcpServers: MISSING - no MCP server registrations object "
            "(expected as in %s)" % (MCP_SUBJECT, SEED_LABEL)
        ]

    servers = obj["mcpServers"]
    errs = []
    for name in sorted(seed_servers):
        seed_entry = seed_servers[name]
        entry = servers.get(name)
        if entry is None:
            errs.append(
                "%s: mcpServers.%s: MISSING - the mandatory indexer server entry "
                "is not declared" % (MCP_SUBJECT, name)
            )
            continue
        if not isinstance(entry, dict):
            errs.append(
                "%s: mcpServers.%s: not an object - the indexer server entry must "
                "carry the vendored seed's shape" % (MCP_SUBJECT, name)
            )
            continue
        for key in REQUIRED_ENTRY_KEYS:
            if key not in entry:
                errs.append(
                    "%s: mcpServers.%s.%s: MISSING - the indexer server entry is "
                    "not in the vendored seed's shape"
                    % (MCP_SUBJECT, name, key)
                )
        if isinstance(seed_entry, dict) and "type" in seed_entry and "type" in entry:
            if entry.get("type") != seed_entry.get("type"):
                errs.append(
                    "%s: mcpServers.%s.type: %r != vendored %r"
                    % (MCP_SUBJECT, name, entry.get("type"), seed_entry.get("type"))
                )
    return errs


def gdc_errors(gdc_path):
    """Violations in the root `gdc-manifest.yaml` (read-only subject)."""
    if not os.path.isfile(gdc_path):
        return [
            "%s: MISSING - the %s mandatory pin lives here"
            % (GDC_SUBJECT, PIN_MODULE)
        ]
    try:
        with open(gdc_path, encoding="utf-8") as fh:
            gdc = yaml.safe_load(fh)
    except Exception as exc:
        return ["%s: yaml parse error: %r" % (GDC_SUBJECT, exc)]
    if not isinstance(gdc, dict):
        return [
            "%s: not a mapping - the %s pin cannot be read"
            % (GDC_SUBJECT, PIN_MODULE)
        ]
    pins = gdc.get("modules") or []
    if not any(isinstance(p, dict) and p.get("module") == PIN_MODULE for p in pins):
        return [
            "%s: modules[]: MISSING - mandatory pin %r is not declared (the "
            "code-indexing mandatory consumer surface requires it)"
            % (GDC_SUBJECT, PIN_MODULE)
        ]
    return []


def evaluate(subject_dir, contract_dir):
    """Return (rc, messages). rc: 0 conformant, 1 violation, 2 cannot-assess."""
    seed_servers, seed_err = load_seed(contract_dir)
    if seed_err is not None:
        return 2, ["check-codeidx-surface: CANNOT-ASSESS - %s" % seed_err]
    msgs = []
    msgs.extend(mcp_errors(os.path.join(subject_dir, MCP_SUBJECT), seed_servers))
    msgs.extend(gdc_errors(os.path.join(subject_dir, GDC_SUBJECT)))
    msgs = sorted(set(msgs))
    return (1 if msgs else 0), msgs


# --- modes ------------------------------------------------------------------

def cmd_check(root, contract_dir):
    rc, msgs = evaluate(root, contract_dir)
    if rc == 0:
        print(
            "check-codeidx-surface: OK - .mcp.json declares the indexer MCP "
            "surface in %s's shape and gdc-manifest.yaml carries the %s pin"
            % (SEED_LABEL, PIN_MODULE)
        )
        return 0
    for m in msgs:
        sys.stderr.write(m + "\n")
    if rc == 2:
        sys.stderr.write("check-codeidx-surface: CANNOT-ASSESS\n")
    else:
        sys.stderr.write(
            "check-codeidx-surface: FAIL - %d violation(s)\n" % len(msgs)
        )
    return rc


def _drop_module_pin(gdc_text):
    """Remove the code-indexing.mcp pin from a gdc-manifest.yaml document."""
    doc = yaml.safe_load(gdc_text)
    pins = doc.get("modules") or []
    doc["modules"] = [
        p for p in pins
        if not (isinstance(p, dict) and p.get("module") == PIN_MODULE)
    ]
    return yaml.safe_dump(doc, allow_unicode=True, sort_keys=False)


def _entry_without(key, mcp_text):
    doc = json.loads(mcp_text)
    servers = doc["mcpServers"]
    for entry in servers.values():
        entry.pop(key, None)
    return json.dumps(doc, indent=2)


def _cases():
    """(name, token, builder) - one control per violation class.

    builder(mcp_text, gdc_text) -> (mcp_text_or_None, gdc_text_or_None);
    None deletes that subject in the scratch tree.
    """
    pin_drop = lambda m, g: (m, _drop_module_pin(g))  # noqa: E731
    return [
        # (a) missing / renamed .mcp.json
        ("missing-mcp-file", MCP_SUBJECT, lambda m, g: (None, g)),
        ("renamed-mcp-file", MCP_SUBJECT, lambda m, g: (None, g)),
        # (b) invalid JSON
        ("invalid-json", "invalid JSON", lambda m, g: ("{ not valid json", g)),
        # (c) missing server entry / drifted entry shape
        ("missing-server-entry", "cmr-indexer",
         lambda m, g: (json.dumps({"mcpServers": {}}), g)),
        ("entry-missing-command", "command",
         lambda m, g: (_entry_without("command", m), g)),
        ("entry-wrong-type", "type",
         lambda m, g: (
             json.dumps({"mcpServers": {"cmr-indexer": {
                 "type": "http", "command": "python3",
                 "args": ["catalog/indexer/mcp_server.py"]}}}, indent=2),
             g)),
        # (d) missing code-indexing.mcp pin in gdc-manifest.yaml
        ("missing-gdc-pin", PIN_MODULE, pin_drop),
        ("missing-gdc-file", GDC_SUBJECT, lambda m, g: (m, None)),
    ]


def _read(path, stream):
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def cmd_self_test(root, contract_dir):
    base_mcp = _read(os.path.join(root, MCP_SUBJECT), "mcp")
    base_gdc = _read(os.path.join(root, GDC_SUBJECT), "gdc")
    if base_mcp is None or base_gdc is None:
        sys.stderr.write(
            "check-codeidx-surface: CANNOT-ASSESS - self-test base subjects "
            "unreadable (.mcp.json present=%s, gdc-manifest.yaml present=%s)\n"
            % (base_mcp is not None, base_gdc is not None)
        )
        return 2

    # A control only proves anything if the un-mutated baseline is green.
    base_rc, base_msgs = evaluate(root, contract_dir)
    if base_rc != 0:
        sys.stderr.write(
            "check-codeidx-surface: CANNOT-ASSESS - baseline subject is already "
            "non-conformant (rc=%d); controls would be meaningless\n" % base_rc
        )
        for m in base_msgs:
            sys.stderr.write("    baseline: %s\n" % m)
        return 2

    print("== codeidx-surface self-test ==")
    failures = 0

    # rc 2 reachability: a genuinely unreadable contract is CANNOT-ASSESS,
    # distinct from a violation (rc 1). Proved here, not asserted in prose.
    empty_contract = tempfile.mkdtemp(prefix="codeidx-contract-")
    try:
        rc2, msgs2 = evaluate(root, empty_contract)
        ok2 = rc2 == 2
        print("  %s  %-26s rc=%d expects CANNOT-ASSESS" %
              ("OK  " if ok2 else "FAIL", "contract-unreadable", rc2))
        if not ok2:
            failures += 1
            for m in msgs2:
                sys.stderr.write("      got: %s\n" % m)
    finally:
        shutil.rmtree(empty_contract, ignore_errors=True)

    for name, token, build in _cases():
        work = tempfile.mkdtemp(prefix="codeidx-self-")
        try:
            mcp_text, gdc_text = build(base_mcp, base_gdc)
            if name == "renamed-mcp-file":
                # a renamed file leaves the declared path absent: the gate must
                # still name .mcp.json as the missing subject.
                with open(os.path.join(work, "mcp.json"), "w", encoding="utf-8") as fh:
                    fh.write(mcp_text if mcp_text is not None else base_mcp)
            if mcp_text is not None:
                with open(os.path.join(work, MCP_SUBJECT), "w", encoding="utf-8") as fh:
                    fh.write(mcp_text)
            if gdc_text is not None:
                with open(os.path.join(work, GDC_SUBJECT), "w", encoding="utf-8") as fh:
                    fh.write(gdc_text)
            rc, msgs = evaluate(work, contract_dir)
            named = [m for m in msgs if token in m]
            ok = rc == 1 and bool(named)
            print("  %s  %-26s rc=%d expects %r" %
                  ("OK  " if ok else "FAIL", name, rc, token))
            if not ok:
                failures += 1
                for m in msgs:
                    sys.stderr.write("      got: %s\n" % m)
        finally:
            shutil.rmtree(work, ignore_errors=True)

    total = len(_cases()) + 1
    if failures:
        sys.stderr.write(
            "check-codeidx-surface self-test: FAIL - %d of %d control(s) not "
            "refused\n" % (failures, total)
        )
        return 1
    print("check-codeidx-surface self-test: OK - %d/%d controls proven "
          "(incl. rc 2 CANNOT-ASSESS)" % (total, total))
    return 0


def main():
    argv = sys.argv[1:]
    if not argv:
        sys.stderr.write(
            "usage: check-codeidx-surface.sh [--self-test] [--contract-dir DIR]\n"
        )
        return 2
    root = argv[0]
    rest = argv[1:]
    contract_dir = os.path.join(root, "vendor", "CMR")
    mode = "check"
    i = 0
    while i < len(rest):
        arg = rest[i]
        if arg == "--self-test":
            mode = "self-test"
        elif arg == "--contract-dir":
            i += 1
            if i >= len(rest):
                sys.stderr.write(
                    "check-codeidx-surface: --contract-dir needs an argument\n"
                )
                return 2
            contract_dir = rest[i]
        elif arg in ("-h", "--help"):
            print("usage: check-codeidx-surface.sh [--self-test] [--contract-dir DIR]")
            return 0
        else:
            sys.stderr.write(
                "check-codeidx-surface: unknown argument %r\n" % arg
            )
            return 2
        i += 1

    if mode == "self-test":
        return cmd_self_test(root, contract_dir)
    return cmd_check(root, contract_dir)


if __name__ == "__main__":
    sys.exit(main())
PY
