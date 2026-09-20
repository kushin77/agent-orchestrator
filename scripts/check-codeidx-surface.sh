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
#   * the DECLARED indexer server, alive (issue #1526) -> once the shape above
#     is conformant, the entry point `.mcp.json` actually names is SPAWNED and
#     asked to answer one JSON-RPC `initialize` over stdio, under a 5s deadline.
#     A shape-only gate passed every run while the declared entry point did not
#     exist at all (INDEXER-REVIEW-2026-09-20.md §3): the declaration LOOKED
#     like the seed, so nothing noticed the server behind it could never start.
#     `result.serverInfo.name` is required in the reply, so a process that
#     answers with JSON but names no server is refused by the same fault.
#     A venue whose vendored contract is unreadable (a fresh CI checkout) is
#     already CANNOT-ASSESS before this step, so it is the DEVELOPER venue --
#     which is where the entry point is exercised -- that carries the verdict.
#
# Tri-state, honest (GR-12, no false green):
#   0  conformant
#   1  violation — the offending file and the fault are named on stderr
#   2  CANNOT-ASSESS — the vendored mandatory seed is unreadable (e.g. the
#      vendor/CMR submodule is not initialised in this worktree) or PyYAML is
#      unavailable, so conformance to the mandatory contract cannot be judged.
#      An unreadable contract is NEVER reported as a pass.
#     Liveness is deliberately NOT a fourth CANNOT-ASSESS state: by the time it
#     runs the contract is readable, the exchange is local and needs no network,
#     and every refusal it can produce is a real defect in `.mcp.json` or in the
#     server that entry names — so all of them are rc 1, named on stderr.
#
# Offline and deterministic; no network, no containers. The liveness step is the
# one thing here that EXECUTES something: proving a declared server is reachable
# requires starting it, which is exactly the proof the shape check could not give.
# The declared entry point is the repo's own `.mcp.json`; nothing else is run.
#
# `--self-test` runs internal negative controls: each constructs one violation
# class in a scratch tree (the real subjects are never mutated) and asserts the
# gate refuses it, naming the fault. A class the gate stops refusing turns the
# self-test itself red — the controls are real, not decorative. The liveness
# controls spawn a synthetic stdio server written into the scratch tree, so a
# gate that stopped spawning anything — or one that could no longer reach a
# server that DOES answer — turns them red too.
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
import selectors
import shutil
import subprocess
import sys
import tempfile
import time

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


# --- liveness: the DECLARED server must actually answer (issue #1526) -------
#
# Everything above judges `.mcp.json` as TEXT. It passed every run while the
# indexer it names could not start at all (INDEXER-REVIEW-2026-09-20.md §3),
# because a declaration that LOOKS like the vendored seed is indistinguishable
# from a working one until something tries to run it. This step tries.
#
# The exchange is deliberately the smallest one an MCP host performs: one
# JSON-RPC `initialize` written to the server's stdin, its stdout read back
# under a deadline. The deadline bounds the EXCHANGE, not the process's
# lifetime — a long-lived stdio server that answers and then keeps running is
# reachable and must PASS, so the reader never waits for the process to exit.

LIVENESS_TIMEOUT_SECONDS = 5.0
LIVENESS_FAULT = "FAIL: codeidx-surface-liveness: server unreachable"
LIVENESS_PASS = "PASS: codeidx-surface-liveness"
INITIALIZE_REQUEST = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {},
}


def _liveness_fault(reason):
    """One liveness fault: the fixed name first, then the measured reason."""
    return "%s - %s" % (LIVENESS_FAULT, reason)


def _terminate(proc):
    """Kill a server whose reply we already have, or never will. Never raises."""
    try:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=LIVENESS_TIMEOUT_SECONDS)
    except Exception:
        pass
    for stream in (proc.stdin, proc.stdout, proc.stderr):
        try:
            if stream is not None:
                stream.close()
        except Exception:
            pass


def _first_json_line(buf):
    """(obj, None) for the first complete JSON line, else (None, reason|None).

    A line that is present but not JSON is a fault, not a reason to keep
    reading: this surface speaks JSON-RPC, so banner text on stdout means the
    declared entry point is not the server the entry claims it is.
    """
    while b"\n" in buf:
        line, buf = buf.split(b"\n", 1)
        line = line.strip()
        if not line:
            continue
        try:
            return json.loads(line.decode("utf-8", "replace")), None
        except Exception:
            return None, "the initialize response is not JSON: %r" % (line[:160],)
    return None, None


def _server_name(reply):
    """The reply's `result.serverInfo.name`, or None when it is not carried."""
    if not isinstance(reply, dict):
        return None
    result = reply.get("result")
    if not isinstance(result, dict):
        return None
    info = result.get("serverInfo")
    if not isinstance(info, dict):
        return None
    name = info.get("name")
    if isinstance(name, str) and name.strip():
        return name
    return None


def _exchange(root, argv):
    """Speak one stdio `initialize` to `argv`, launched with cwd `root`.

    Returns (reply, reason) — exactly one of them is None — bounded by
    LIVENESS_TIMEOUT_SECONDS for the whole exchange, never for the process's
    lifetime.
    """
    try:
        proc = subprocess.Popen(
            argv,
            cwd=root,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except Exception as exc:
        return None, "cannot start %r: %r" % (argv[0], exc)

    try:
        try:
            proc.stdin.write(
                (json.dumps(INITIALIZE_REQUEST) + "\n").encode("utf-8")
            )
            proc.stdin.flush()
            proc.stdin.close()
        except Exception:
            # A server that closes its own stdin early is judged on its stdout.
            pass

        fd = proc.stdout.fileno()
        sel = selectors.DefaultSelector()
        sel.register(fd, selectors.EVENT_READ)
        buf = b""
        deadline = time.monotonic() + LIVENESS_TIMEOUT_SECONDS
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not sel.select(remaining):
                    return None, "no initialize response within %gs" % (
                        LIVENESS_TIMEOUT_SECONDS,
                    )
                chunk = os.read(fd, 65536)
                if not chunk:
                    break
                buf += chunk
                reply, reason = _first_json_line(buf)
                if reply is not None or reason is not None:
                    return reply, reason
        finally:
            sel.close()

        # stdout closed: a final unterminated object still counts as an answer.
        reply, reason = _first_json_line(buf + b"\n")
        if reply is not None or reason is not None:
            return reply, reason
        return None, "the server closed stdout without an initialize response"
    finally:
        _terminate(proc)


def _declared_indexers(root, seed_servers):
    """[(name, command, args)] for the mandatory servers, as DECLARED."""
    with open(os.path.join(root, MCP_SUBJECT), encoding="utf-8") as fh:
        obj = json.load(fh)
    servers = obj.get("mcpServers") if isinstance(obj, dict) else None
    servers = servers if isinstance(servers, dict) else {}
    declared = []
    for name in sorted(seed_servers):
        entry = servers.get(name)
        entry = entry if isinstance(entry, dict) else {}
        command = entry.get("command")
        command = (
            command
            if isinstance(command, str) and command.strip()
            else "python3"
        )
        args = entry.get("args")
        args = (
            [a for a in args if isinstance(a, str)]
            if isinstance(args, list)
            else []
        )
        declared.append((name, command, args))
    return declared


def liveness_faults(root, seed_servers):
    """Named faults proving the DECLARED indexer server actually answers."""
    faults = []
    for name, command, args in _declared_indexers(root, seed_servers):
        label = "%s: mcpServers.%s" % (MCP_SUBJECT, name)
        if not args:
            faults.append(_liveness_fault(
                "%s.args is empty - the entry declares no server to start"
                % (label,)
            ))
            continue
        # The MCP host launches the server with cwd = the repo root, so a
        # relative entry point resolves there; an absolute one is used as given.
        entry = args[0]
        resolved = entry if os.path.isabs(entry) else os.path.join(root, entry)
        if not os.path.isfile(resolved):
            faults.append(_liveness_fault(
                "%s.args[0] %r resolves to %r, which does not exist: the server "
                "is launched with cwd = the repo root, so the declaration must "
                "name an entry point that resolves from there"
                % (label, entry, resolved)
            ))
            continue
        reply, reason = _exchange(root, [command] + args)
        if reason is not None:
            faults.append(_liveness_fault("%s: %s" % (label, reason)))
            continue
        if _server_name(reply) is None:
            faults.append(_liveness_fault(
                "%s answered %s with no result.serverInfo.name"
                % (label, json.dumps(reply)[:200])
            ))
    return faults


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
        # The shape is conformant, so the declaration and the vendored contract
        # AGREE. Only now is liveness the next question: the entry point that
        # shape names must actually start and answer.
        seed_servers, seed_err = load_seed(contract_dir)
        if seed_err is not None:
            # Unreachable by construction: `evaluate` above read the same
            # contract a moment ago, so its absence here is not a pass.
            sys.stderr.write(
                "check-codeidx-surface: CANNOT-ASSESS - %s\n" % seed_err
            )
            return 2
        msgs = liveness_faults(root, seed_servers)
        rc = 1 if msgs else 0
    if rc == 0:
        print(
            "check-codeidx-surface: OK - .mcp.json declares the indexer MCP "
            "surface in %s's shape and gdc-manifest.yaml carries the %s pin"
            % (SEED_LABEL, PIN_MODULE)
        )
        print(LIVENESS_PASS)
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


# --- liveness controls (issue #1526) ----------------------------------------
#
# Synthetic stdio servers, written into a SCRATCH tree and never into the repo.
# `live-answers` is the positive control: a control that cannot pass proves
# nothing, so the gate's PASS path is asserted before the refusals are.
_LIVENESS_STUBS = {
    "answers": (
        "import json, sys\n"
        "sys.stdin.readline()\n"
        "print(json.dumps({'jsonrpc': '2.0', 'id': 1, 'result': {\n"
        "    'protocolVersion': '2024-11-05', 'capabilities': {},\n"
        "    'serverInfo': {'name': 'cmr-indexer', 'version': '0.0.0'}}}),\n"
        "      flush=True)\n"
    ),
    "not-json": (
        "import sys\n"
        "sys.stdin.readline()\n"
        "print('cmr-indexer: warming up, this is not JSON at all', flush=True)\n"
    ),
    "no-serverinfo": (
        "import json, sys\n"
        "sys.stdin.readline()\n"
        "print(json.dumps({'jsonrpc': '2.0', 'id': 1,\n"
        "                  'result': {'capabilities': {}}}), flush=True)\n"
    ),
    "never-answers": (
        "import sys, time\n"
        "sys.stdin.readline()\n"
        "time.sleep(600)\n"
    ),
}

_LIVENESS_CASES = (
    # (control, stub written into the scratch tree or None, declared args,
    #  expect a reachable server?, token the refusal must carry)
    ("live-answers", "answers", ["stub_server.py"], True, LIVENESS_PASS),
    ("live-not-json", "not-json", ["stub_server.py"], False, "not JSON"),
    ("live-no-serverinfo", "no-serverinfo", ["stub_server.py"], False,
     "no result.serverInfo.name"),
    ("live-never-answers", "never-answers", ["stub_server.py"], False,
     "no initialize response within"),
    ("live-unresolvable-entry", None, ["catalog/indexer/mcp_server.py"], False,
     "catalog/indexer/mcp_server.py"),
)


def _liveness_controls(contract_dir):
    """Spawn every liveness control; return the number that failed (None: cannot assess).

    Each control declares its server in its OWN scratch `.mcp.json` and writes
    the stub beside it, so the gate reaches the liveness step exactly the way it
    does in production and starts a real process. The mandatory server NAMES
    come from the vendored seed, as they do on the real subject.
    """
    seed_servers, seed_err = load_seed(contract_dir)
    if seed_err is not None:
        sys.stderr.write(
            "check-codeidx-surface: CANNOT-ASSESS - liveness controls need the "
            "vendored seed: %s\n" % seed_err
        )
        return None
    print("== codeidx-surface liveness (scratch servers, real spawn) ==")
    bad = 0
    for control, stub, args, expect_reachable, token in _LIVENESS_CASES:
        work = tempfile.mkdtemp(prefix="codeidx-live-")
        try:
            if stub is not None:
                with open(os.path.join(work, "stub_server.py"), "w",
                          encoding="utf-8") as fh:
                    fh.write(_LIVENESS_STUBS[stub])
            with open(os.path.join(work, MCP_SUBJECT), "w", encoding="utf-8") as fh:
                json.dump({"mcpServers": {"cmr-indexer": {
                    "type": "stdio", "command": "python3", "args": args}}}, fh)
            faults = liveness_faults(work, seed_servers)
            if expect_reachable:
                ok = not faults
                want = "reachable"
            else:
                ok = bool(faults) and all(token in f for f in faults)
                want = "refused (%r)" % token
            print("  %s  %-24s expects %s" %
                  ("OK  " if ok else "FAIL", control, want))
            if not ok:
                bad += 1
                for f in faults:
                    sys.stderr.write("      got: %s\n" % f)
        finally:
            shutil.rmtree(work, ignore_errors=True)
    return bad


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

    live_bad = _liveness_controls(contract_dir)
    if live_bad is None:
        return 2
    failures += live_bad

    total = len(_cases()) + len(_LIVENESS_CASES) + 1
    if failures:
        sys.stderr.write(
            "check-codeidx-surface self-test: FAIL - %d of %d control(s) not "
            "refused\n" % (failures, total)
        )
        return 1
    print("check-codeidx-surface self-test: OK - %d/%d controls proven "
          "(incl. rc 2 CANNOT-ASSESS and the liveness spawn)" % (total, total))
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
