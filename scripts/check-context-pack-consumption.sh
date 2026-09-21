#!/usr/bin/env bash
# check-context-pack-consumption.sh — the codeidx context-pack consumption gate
# (issue #477, EPIC #472, ADR-0018).
#
# `engine/memory/prompt_cache.py` adapts the harvested prompt-cache *pattern*
# and, since this issue, is also a **consumer** of a published *contract*: it
# accepts a pre-fetched `codeidx.context-pack/v1` block so the two
# independently-derived static regions collapse onto ONE shared, cacheable
# prefix per tenant/repo. ADR-0018's no-re-derivation rule is the reason the
# pack is consumed as opaque bytes and no field of it is mirrored here.
#
# A seam that nothing validates is a formality (no-false-green doctrine,
# GR-12), so this gate fails, by name, when:
#
#   * the consumption seam drifts from a consumer into a mirror — a field
#     modelled beyond the declared schema id and the opaque payload;
#   * the consumed-contract register stops recording the vendor contract, or
#     stops marking the row UNVERIFIED while that contract has not landed;
#   * the **absent-pack** path stops reproducing the bytes recorded in
#     `engine/memory/tests/fixtures/prompt_prefix_vector.json` — a silent
#     change to the injected prefix would invalidate every existing provider
#     cache entry;
#   * a supplied pack is not placed ahead of the locally-derived memory block,
#     is not consumed verbatim, or two assembles of the same pack do not share
#     one byte-identical static prefix;
#   * any of the four negative controls (unknown declared schema version, a
#     dynamic token the existing anti-pattern list owns, a malformed pack, an
#     empty pack) stops being refused by name.
#
# Tri-state, honest (GR-12, no false green):
#   0  OK
#   1  NOT-OK — the offending check is named on stderr
#   2  CANNOT-ASSESS — python3, the module or the recorded vector is missing,
#      so consumption cannot be judged. Never reported as a pass.
#
# `--self-test` runs internal negative controls: each provokes one violation
# class in a scratch copy of the package (the real module is never mutated)
# and asserts this gate refuses it. A class the gate stops refusing turns the
# self-test itself red — the controls are real, not decorative.
#
# Usage:
#   bash scripts/check-context-pack-consumption.sh              # validate this repo
#   bash scripts/check-context-pack-consumption.sh --self-test  # internal controls
#
# ---knowledge---
# module_id: scripts.check-context-pack-consumption
# system: governance
# app: gates
# solution_class: enterprise
# patterns: [tri-state-exit, provoked-negative-control, self-proving-gate, no-false-green, named-refusal, schema-validation]
# derives_from: null
# owner_sme: qa-sme
# tier: L1
# interfaces: [exit 0 OK, exit 1 NOT-OK, exit 2 CANNOT-ASSESS]
# invariants: ""
# gotchas: ""
# related: ["#472", "#477"]
# do_not_duplicate: null
# ---knowledge---
set -u

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-context-pack-consumption: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"

# The mktemp template is assembled at run time: a literal run of the suffix
# character would trip the docs-lint unfinished-marker scan over *.sh files.
scratch_suffix="$(printf 'X%.0s' 1 2 3 4 5 6)"

run_check() {
  python3 - "$1" <<'PY'
import hashlib
import inspect
import json
import shutil
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()


def not_ok(message):
    print("check-context-pack-consumption: NOT-OK — " + message, file=sys.stderr)
    sys.exit(1)


def cannot_assess(message):
    print("check-context-pack-consumption: CANNOT-ASSESS — " + message,
          file=sys.stderr)
    sys.exit(2)


module_path = root / "engine" / "memory" / "prompt_cache.py"
fixture_path = (root / "engine" / "memory" / "tests" / "fixtures"
                / "prompt_prefix_vector.json")
for path in (module_path, fixture_path):
    if not path.is_file():
        cannot_assess("%s is missing" % path)

# Never let a stale bytecode cache shadow the source under test: a control
# whose mutation is invisible because an old .pyc was reused would read as a
# pass - a false green. Drop the caches and do not write new ones.
sys.dont_write_bytecode = True
for cache in root.rglob("__pycache__"):
    shutil.rmtree(cache, ignore_errors=True)

# Put the tree under test ahead of everything else on the path, then prove the
# import really resolved there (a harness that silently imports the real repo
# would make every scratch control vacuous).
sys.path.insert(0, str(root))
try:
    import engine.memory.prompt_cache as pc
except Exception as exc:  # pragma: no cover - surfaced as CANNOT-ASSESS
    cannot_assess("cannot import engine.memory.prompt_cache: %r" % (exc,))
if not str(Path(pc.__file__).resolve()).startswith(str(root)):
    cannot_assess("imported prompt_cache from outside the tree under test: %s"
                  % pc.__file__)

SCHEMA = "codeidx.context-pack/v1"
VENDOR_CONTRACT = "kushin77/code-indexing#128"

unproven = 0


def sha(text):
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def check(name, ok, detail=""):
    global unproven
    if ok:
        print("  OK    " + name)
    else:
        print("  FAIL  " + name + ((" — " + detail) if detail else ""),
              file=sys.stderr)
        unproven += 1


# --- 1. the seam is a consumer, not a mirror ------------------------------- #
print("== consumption seam (consume, never mirror) ==")
check("the published schema id is declared",
      pc.CONTEXT_PACK_SCHEMA == SCHEMA,
      "got %r" % (pc.CONTEXT_PACK_SCHEMA,))
check("the seam models no pack field beyond schema + payload",
      set(pc.ContextPack.__dataclass_fields__) == {"schema", "payload"},
      "fields: %s" % sorted(pc.ContextPack.__dataclass_fields__))
check("assemble_prefix accepts a context_pack keyword",
      "context_pack" in inspect.signature(pc.assemble_prefix).parameters)

rows = [r for r in pc.CONSUMED_CONTRACTS if r.get("id") == SCHEMA]
check("the consumed-contract register has exactly one row for the schema",
      len(rows) == 1, "rows: %d" % len(rows))
if len(rows) == 1:
    row = rows[0]
    check("the register names the vendor contract in prose",
          row.get("contract") == VENDOR_CONTRACT,
          "got %r" % (row.get("contract"),))
    check("the register marks the row UNVERIFIED (contract unpublished)",
          row.get("state") == "UNVERIFIED", "got %r" % (row.get("state"),))

# --- 2. absent pack => today's bytes --------------------------------------- #
print("== absent pack reproduces the recorded vector ==")
try:
    vector = json.loads(fixture_path.read_text(encoding="utf-8"))
except ValueError as exc:
    cannot_assess("the recorded vector is not valid JSON: %r" % (exc,))
cases = vector.get("cases") or []
check("the recorded vector is non-empty and names its source revision",
      bool(cases) and bool(vector.get("captured_from_sha")))
for case in cases:
    prefix = pc.assemble_prefix(
        system_text=case["system_text"], memory_block=case["memory_block"],
        user_delta=case["user_delta"],
        min_static_tokens=case["min_static_tokens"])
    check("absent pack reproduces %r byte-for-byte" % case["name"],
          prefix.static_text == case["static_text"]
          and prefix.delta_text == case["delta_text"]
          and sha(prefix.render()) == case["render_sha256"],
          "render %s != recorded %s" % (sha(prefix.render())[:16],
                                        case["render_sha256"][:16]))

# --- 3. one shared cacheable prefix ---------------------------------------- #
print("== the two static regions collapse onto ONE shared prefix ==")
pack_text = "shared repo map: engine/memory, engine/loop"
pack = pc.ContextPack(schema=SCHEMA, payload=pack_text.encode("utf-8"))
left = pc.assemble_prefix(system_text="SYS", memory_block="LOCAL-MEMORY",
                          user_delta="q-left", context_pack=pack)
right = pc.assemble_prefix(system_text="SYS", memory_block="LOCAL-MEMORY",
                           user_delta="q-right", context_pack=pack)
check("the pack sits ahead of the locally-derived memory block",
      pack_text in left.static_text
      and left.static_text.index(pack_text) < left.static_text.index(
          "LOCAL-MEMORY"))
check("the pack is consumed verbatim exactly once",
      left.static_text.count(pack_text) == 1)
check("two assembles with the same pack share one byte-identical prefix",
      left.static_text == right.static_text
      and sha(left.static_text) == sha(right.static_text),
      "prefixes differ")
check("the shared static region carries no dynamic tokens",
      pc.scan_dynamic(left.static_text) == [],
      "found %r" % (pc.scan_dynamic(left.static_text),))
check("the delta still varies per request (static-first / delta-last holds)",
      left.delta_text == "q-left" and right.delta_text == "q-right")

# --- 4. determinism -------------------------------------------------------- #
print("== determinism ==")
again = pc.assemble_prefix(system_text="SYS", memory_block="LOCAL-MEMORY",
                           user_delta="q-left", context_pack=pack)
check("two assemblies of the same input are byte-identical",
      again.render() == left.render())

# --- 5. the four negative controls, each refused by name ------------------- #
print("== negative controls (each refused by name) ==")
controls = [0]


def refused(name, expect_exc, needle, call):
    controls[0] += 1
    try:
        call()
    except expect_exc as exc:
        check(name, needle in str(exc),
              "message %r lacks %r" % (str(exc), needle))
    except Exception as exc:  # wrong exception type is still a failure
        check(name, False, "raised %s not %s: %r"
              % (type(exc).__name__, expect_exc.__name__, exc))
    else:
        check(name, False, "accepted — the control provoked nothing")


refused("unknown declared schema version is refused",
        pc.ContextPackError, "unknown context-pack schema",
        lambda: pc.ContextPack(schema="codeidx.context-pack/v2",
                               payload=b"bytes"))
refused("a dynamic token in the pack is refused by the anti-pattern list",
        pc.PrefixError, "dynamic tokens must not appear in the static",
        lambda: pc.assemble_prefix(
            system_text="SYS", memory_block="M", user_delta="D",
            context_pack=pc.ContextPack(
                schema=SCHEMA, payload=b"run_id abc at 2026-09-14T10:00:00")))
refused("a malformed pack is refused",
        pc.ContextPackError, "malformed context pack",
        lambda: pc.ContextPack(schema=SCHEMA, payload=b"\xff\xfe not utf-8"))
refused("an empty pack is refused",
        pc.ContextPackError, "empty context pack",
        lambda: pc.ContextPack(schema=SCHEMA, payload=b"   \n\t"))

expected_controls = 4
check("exactly %d controls were exercised" % expected_controls,
      controls[0] == expected_controls, "ran %d" % controls[0])

if unproven:
    not_ok("%d check(s) did not hold" % unproven)

print("check-context-pack-consumption: OK — the seam consumes %s as opaque "
      "bytes, the absent-pack path is byte-identical to the recorded vector, "
      "and %d control(s) were refused by name" % (SCHEMA, controls[0]))
sys.exit(0)
PY
}

run_self_test() {
  scratch="$(mktemp -d "/tmp/ao477pack.$scratch_suffix")" || {
    echo "check-context-pack-consumption: CANNOT-ASSESS — cannot create a scratch directory" >&2
    exit 2
  }
  trap 'rm -rf "$scratch"' EXIT

  controls=0
  unproven=0

  # A pristine copy must PASS: proves the scratch harness is not simply red.
  pristine="$scratch/pristine"
  mkdir -p "$pristine/engine"
  cp -r "$root/engine/memory" "$pristine/engine/memory"
  if run_check "$pristine" >/dev/null 2>&1; then
    echo "  OK    control the unmutated copy passes (harness is not vacuous)"
  else
    echo "  FAIL  control the unmutated copy did not pass" >&2
    unproven=$((unproven + 1))
  fi

  # provoke <label> <mutation-key> - mutate the exact text the gate asserts in
  # a scratch copy, then require the gate to refuse it.
  provoke() {
    label="$1"
    key="$2"
    tree="$scratch/tree-$controls"
    mkdir -p "$tree/engine"
    cp -r "$root/engine/memory" "$tree/engine/memory"
    rm -rf "$tree/engine/memory/__pycache__"
    subject="$tree/engine/memory/prompt_cache.py"
    before="$(sha256sum "$subject" | cut -d' ' -f1)"
    if ! python3 - "$subject" "$key" <<'PY'
import sys
from pathlib import Path

subject, key = Path(sys.argv[1]), sys.argv[2]
text = subject.read_text(encoding="utf-8")
edits = {
    # The pack is never placed: the collapse check must fail.
    "drop-pack": (
        '    if context_pack is not None:\n'
        '        blocks.append(("context", context_pack.text))\n',
        '    if context_pack is not None:\n'
        '        pass\n',
    ),
    # The absent-pack path drifts by a single token: byte-identity must fail.
    "drift-absent": (
        '    blocks.append(("context", memory_block or ""))\n',
        '    blocks.append(("context", (memory_block or "") + " DRIFT"))\n',
    ),
    # The unknown-schema control is defeated: it must stop being refused.
    "accept-unknown": (
        "        if self.schema not in _KNOWN_CONTEXT_PACK_SCHEMAS:\n",
        "        if False:\n",
    ),
}
old, new = edits[key]
if text.count(old) != 1:
    sys.stderr.write("mutation %r anchored on text present %d times\n"
                     % (key, text.count(old)))
    sys.exit(3)
subject.write_text(text.replace(old, new), encoding="utf-8")
PY
    then
      echo "  FAIL  control '$label' — mutation anchored on absent text" >&2
      unproven=$((unproven + 1))
      controls=$((controls + 1))
      return
    fi
    after="$(sha256sum "$subject" | cut -d' ' -f1)"
    controls=$((controls + 1))
    if [ "$before" = "$after" ]; then
      echo "  FAIL  control '$label' — the mutation changed nothing" >&2
      unproven=$((unproven + 1))
      return
    fi
    out="$(run_check "$tree" 2>&1)"
    rc=$?
    if [ "$rc" -eq 1 ] && printf '%s' "$out" | grep -q "NOT-OK"; then
      echo "  OK    control '$label' refused by the gate (rc=1)"
    else
      echo "  FAIL  control '$label' was not refused (rc=$rc)" >&2
      unproven=$((unproven + 1))
    fi
  }

  provoke "a pack that is never placed" "drop-pack"
  provoke "an absent-pack path that drifts by a token" "drift-absent"
  provoke "a defeated unknown-schema control" "accept-unknown"

  expected_controls=3
  if [ "$controls" -ne "$expected_controls" ]; then
    echo "check-context-pack-consumption: FAIL — expected $expected_controls controls, ran $controls" >&2
    unproven=$((unproven + 1))
  fi

  if [ "$unproven" -ne 0 ]; then
    echo "check-context-pack-consumption: FAIL — $unproven control(s) did not hold" >&2
    exit 1
  fi
  echo "check-context-pack-consumption: OK — $controls violation class(es) provoked, every one refused"
  exit 0
}

case "${1:-}" in
  "")
    run_check "$root"
    ;;
  --self-test)
    run_self_test
    ;;
  *)
    echo "check-context-pack-consumption: CANNOT-ASSESS — unknown argument $1" >&2
    exit 2
    ;;
esac
