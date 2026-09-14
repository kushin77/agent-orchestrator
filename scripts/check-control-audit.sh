#!/usr/bin/env bash
# check-control-audit.sh — the exactly-once control / audit-record gate
# (issue #555, RC-4 of EPIC #551; ADR-0025 D3).
#
# ADR-0025 D3 makes an audit record a *required* property of a control action,
# not an option: an operator-requested verb may write fleet state only when it is
# identified **and audited** — exactly one record per applied command, on the
# rails that already exist (``telemetry/ledger/`` + ``.fleet/slog.jsonl``), never
# a new ledger. A rule that nothing provokes is a slogan (no-false-green
# doctrine, GR-12), so this gate provokes each of the three acceptance controls
# against the real seam and fails, BY NAME, when one stops holding:
#
#   1. a REPLAYED command produces exactly ONE ledger record, runs the lever
#      ONCE, and returns the ORIGINAL receipt;
#   2. an UNREACHABLE lever returns 503 and writes NO record on either rail;
#   3. a REORDERED or STOLEN command id is REFUSED (and the id stays spent);
#   plus the fail-closed completion of "one effect, one record": an effect whose
#   record cannot be written is reported as 503 audit_unavailable, never as a
#   success, and the id is still spent.
#
# It also pins the rails: the record lands on the existing ``telemetry/ledger``
# store's own per-tenant file and on the existing ``.fleet/slog.jsonl`` writer,
# and a single applied command creates no file other than those two — a second
# ledger would be visible here.
#
# It runs TWO controls on its own ability to fail:
#   * a self-mutation control — the driver is re-run with a deliberately wrong
#     expectation and must go red naming the fault ("a check that cannot fail is
#     a formality");
#   * a mutation proof — the replay short-circuit in ``control_audit.py`` is
#     removed, so a replay runs the lever a second time; the driver must go red
#     naming the SECOND EFFECT (not the record count — ``finish`` is idempotent,
#     so the record count alone cannot see this), the mutation must be proven to
#     have changed the bytes, and the restored file must be sha256 byte-identical
#     to the original.
#
# Exit-code contract (guardrails/honesty tri-state, issue #28):
#   0 OK / 1 NOT-OK / 2 CANNOT-ASSESS. CANNOT-ASSESS never exits 0.
#
# Offline and deterministic: stdlib + pytest only, no network, a scratch tree
# under /tmp, and no read or write of the live fleet's own state.
#
# Usage: bash scripts/check-control-audit.sh
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || { echo "check-control-audit: CANNOT-ASSESS — cannot enter $root" >&2; exit 2; }

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-control-audit: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

module="portal/server/control_audit.py"
if [ ! -f "$module" ]; then
  echo "check-control-audit: FAIL — $module is missing (the record path has no home)" >&2
  exit 1
fi

work="/tmp/ao555-control-audit.$$.$(date +%s)"
if ! mkdir -p "$work"; then
  echo "check-control-audit: CANNOT-ASSESS — cannot create a scratch directory" >&2
  exit 2
fi
trap 'rm -rf "$work"' EXIT

# ---------------------------------------------------------------------------
# the driver: the three acceptance controls + the fail-closed completion,
# provoked against the REAL seam (``RemoteControl.apply_command``).
#
# ``--module-file`` loads a mutated copy of the record path instead of the
# committed one, so the mutation proof below runs the same assertions.
# ``--expect-replay-records`` is the self-mutation control's knob: a wrong value
# must make this driver go red.
# ---------------------------------------------------------------------------
drive() {
  env PYTHONDONTWRITEBYTECODE=1 python3 - "$root" "$work" "$@" <<'PY'
import importlib.util
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
work = Path(sys.argv[2])
argv = sys.argv[3:]

module_file = None
expect_replay_records = 1
scratch = work / "probe"
i = 0
while i < len(argv):
    if argv[i] == "--module-file":
        module_file = Path(argv[i + 1])
        i += 2
    elif argv[i] == "--expect-replay-records":
        expect_replay_records = int(argv[i + 1])
        i += 2
    elif argv[i] == "--scratch":
        scratch = Path(argv[i + 1])
        i += 2
    else:
        print(f"driver: unknown argument {argv[i]!r}", file=sys.stderr)
        raise SystemExit(2)

scratch.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(root))

# The EXISTING rail, opened by the driver itself: the module under test is
# handed a ``telemetry/ledger`` store, never a store of its own.
sys.path.insert(0, str(root / "telemetry"))
import ledger as ledger_pkg  # noqa: E402  (after sys.path)

from portal.server import control_api  # noqa: E402
from portal.server.app import ApiError  # noqa: E402

failures: list[str] = []


def check(cond: bool, message: str) -> bool:
    if not cond:
        failures.append(message)
    return bool(cond)


# -- the module under test ---------------------------------------------------
if module_file is None:
    from portal.server import control_audit as mod  # noqa: PLC0415
else:
    spec = importlib.util.spec_from_file_location("ao_control_audit_under_test", module_file)
    if spec is None or spec.loader is None:
        print(f"driver: cannot load {module_file}", file=sys.stderr)
        raise SystemExit(2)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)

for name in ("ControlAudit", "REPLAY_MARKER", "RECEIPT_SEPARATOR", "CONFLICT_MARKER",
             "EVIDENCE_PREFIX", "TENANT_ID"):
    if not hasattr(mod, name):
        print(f"driver: the record path does not export {name}", file=sys.stderr)
        raise SystemExit(2)

# The production writer of ``.fleet/slog.jsonl``, with its stream redirected to
# the scratch tree so the live fleet's slog is never touched.
channel = mod.load_fleet_channel(str(root))
channel.SLOG = scratch / "slog.jsonl"


class StubApp:
    repo_root = root


class RecordingLever:
    """A lever double that records its calls, or refuses to be reached."""

    def __init__(self, *, unreachable: str = "") -> None:
        self.unreachable = unreachable
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def run(self, row, args):
        self.calls.append((row.lever, tuple(args)))
        if self.unreachable:
            raise control_api.LeverUnreachable(self.unreachable)
        return control_api.LeverResult(
            argv=(row.source, row.local, *args), exit_code=0,
            stdout=f"{row.local} done", stderr="",
        )


class Principal:
    def __init__(self, email: str) -> None:
        self.email = email


def row(verb: str, *, local: str = "", audit_action=None) -> control_api.VerbRow:
    family, _, action = verb.partition(".")
    return control_api.VerbRow(
        id=verb, family=family, action=action, source="fleet/control.py",
        local=local or action, effect_class="hold", capability="fleet:operate",
        audit_action=audit_action if audit_action is not None else verb,
        idempotent=True, exposed=True, why_not_exposed=None,
    )


def build(store, lever, *, channel_mod=None):
    """The transport, and the record path it delegates to, over one store."""
    audit = mod.ControlAudit(
        repo_root=str(root), store=store,
        channel=channel_mod if channel_mod is not None else channel,
    )
    surface = control_api.RemoteControl(
        app=StubApp(), enabled=True, lever=lever, commands=audit
    )
    return surface, audit


def refuse(fn):
    """Run ``fn`` and return ``(ApiError | None, result | None)``."""
    try:
        return None, fn()
    except ApiError as exc:
        return exc, None


# ---------------------------------------------------------------------------
# control 1 — a REPLAY: one record, one effect, the ORIGINAL receipt
# ---------------------------------------------------------------------------
store = ledger_pkg.open_ledger(str(scratch / "ledger"))
lever = RecordingLever()
ctl, audit = build(store, lever)
row_pause = row("fleet.pause")
operator = Principal("operator@platform.example.com")
first_err, first_receipt = refuse(
    lambda: ctl.apply_command(row_pause, operator, {"commandId": "cmd_replay", "args": ["--why", "drill"]}, "")
)
check(first_err is None, f"the first command was refused: {first_err}")

replay_err, replay_receipt = refuse(
    lambda: ctl.apply_command(row_pause, operator, {"commandId": "cmd_replay", "args": ["--why", "drill"]}, "")
)
check(replay_err is not None, "CONTROL-1: a replayed command was APPLIED a second time")
status = getattr(replay_err, "status", None)
code = getattr(replay_err, "code", None)
message = getattr(replay_err, "message", "") or ""
check(status == 409, f"CONTROL-1: a replay answered {status}, not 409")
check(code == "duplicate_command", f"CONTROL-1: a replay answered code {code!r}, not duplicate_command")
check(mod.REPLAY_MARKER in message, "CONTROL-1: the replay refusal does not name the replay rule")
ledger_records = audit.records_for("cmd_replay")
slog_records = audit.slog_records_for("cmd_replay")
check(
    len(ledger_records) == expect_replay_records,
    f"CONTROL-1: a replay produced {len(ledger_records)} ledger record(s), expected {expect_replay_records}",
)
check(len(slog_records) == 1, f"CONTROL-1: {len(slog_records)} slog record(s) for the command, expected 1")
check(
    len(lever.calls) == 1,
    f"CONTROL-1: the lever ran {len(lever.calls)} time(s) — a replay PERFORMED A SECOND EFFECT",
)
returned = None
if mod.RECEIPT_SEPARATOR in message:
    try:
        returned = json.loads(message.split(mod.RECEIPT_SEPARATOR, 1)[1])
    except json.JSONDecodeError as exc:
        check(False, f"CONTROL-1: the replay receipt is not parseable JSON ({exc})")
check(
    returned == first_receipt,
    "CONTROL-1: the replay did not return the ORIGINAL receipt",
)
if ledger_records and first_receipt:
    check(
        ledger_records[0].get("evidence") == f"{mod.EVIDENCE_PREFIX}cmd_replay",
        "CONTROL-1: the ledger record does not carry the control-command pointer",
    )
    check(
        ledger_records[0].get("actor") == first_receipt.get("actor"),
        "CONTROL-1: the ledger record attributes the effect to another principal",
    )
    check(
        ledger_records[0].get("action") == first_receipt.get("auditAction"),
        "CONTROL-1: the ledger record does not carry the registry's audit action",
    )

print(
    "CONTROL1 replay status=%s code=%s effect_calls=%d ledger_records=%d slog_records=%d receipt_matches=%s"
    % (status, code, len(lever.calls), len(ledger_records), len(slog_records), returned == first_receipt)
)

# ---------------------------------------------------------------------------
# control 2 — an UNREACHABLE lever: 503, and NO record on either rail
# ---------------------------------------------------------------------------
store2 = ledger_pkg.open_ledger(str(scratch / "ledger2"))
dead = RecordingLever(unreachable="the lever is absent")
ctl2, audit2 = build(store2, dead)
unreach_err, _ = refuse(
    lambda: ctl2.apply_command(row("fleet.stop"), operator, {"commandId": "cmd_unreachable", "args": []}, "")
)
check(unreach_err is not None, "CONTROL-2: an unreachable lever was NOT refused")
u_status = getattr(unreach_err, "status", None)
u_code = getattr(unreach_err, "code", None)
check(u_status == 503, f"CONTROL-2: an unreachable lever answered {u_status}, not 503")
check(u_code == "lever_unreachable", f"CONTROL-2: an unreachable lever answered code {u_code!r}")
u_ledger = audit2.records_for("cmd_unreachable")
u_all = audit2.ledger_records()
u_slog = audit2.slog_records_for("cmd_unreachable")
check(u_ledger == [], "CONTROL-2: an unreachable command wrote a ledger record")
check(u_all == [], f"CONTROL-2: the rail gained {len(u_all)} record(s) for a command with no effect")
check(u_slog == [], "CONTROL-2: an unreachable command wrote a slog record")
check(audit2.applied("cmd_unreachable") is None, "CONTROL-2: a command with no effect was remembered as applied")
print(
    "CONTROL2 unreachable status=%s code=%s ledger_records=%d slog_records=%d"
    % (u_status, u_code, len(u_all), len(u_slog))
)

# ---------------------------------------------------------------------------
# control 3 — a REORDERED / STOLEN command id: refused, id stays spent
# ---------------------------------------------------------------------------
reorder_err, _ = refuse(
    lambda: ctl.apply_command(row("fleet.resume"), operator, {"commandId": "cmd_replay", "args": []}, "")
)
reorder_code = getattr(reorder_err, "code", None)
reorder_msg = getattr(reorder_err, "message", "") or ""
check(reorder_err is not None, "CONTROL-3: a REORDERED command id was accepted")
check(mod.CONFLICT_MARKER in reorder_msg, "CONTROL-3: the reordering refusal does not name the rule")
check("reordered" in reorder_msg, "CONTROL-3: the reordering refusal does not name the kind")
check(reorder_code == "duplicate_command", f"CONTROL-3: a reordered id answered code {reorder_code!r}")

thief = Principal("mallory@evil.example.com")
stolen_err, _ = refuse(
    lambda: ctl.apply_command(row_pause, thief, {"commandId": "cmd_replay", "args": ["--why", "drill"]}, "")
)
stolen_msg = getattr(stolen_err, "message", "") or ""
check(stolen_err is not None, "CONTROL-3: a STOLEN command id was accepted")
check("stolen" in stolen_msg, "CONTROL-3: the stolen-id refusal does not name the kind")
check(
    len(lever.calls) == 1,
    f"CONTROL-3: a refused reordering reached the lever ({len(lever.calls)} call(s))",
)
check(
    len(audit.records_for("cmd_replay")) == 1,
    "CONTROL-3: a refused reordering changed the record count",
)
print(
    "CONTROL3 reordered code=%s kind=reordered refused=%s | stolen kind=stolen refused=%s | effect_calls=%d"
    % (reorder_code, reorder_err is not None, stolen_err is not None, len(lever.calls))
)

# ---------------------------------------------------------------------------
# control 4 — an un-writable rail: an unrecorded effect is never a success
# ---------------------------------------------------------------------------
class BrokenStore:
    """A rail that cannot be written — the audit outage case."""

    def records(self, tenant_id):
        return []

    def append(self, tenant_id, **kwargs):
        raise OSError("the ledger rail is not writable")


store3 = BrokenStore()
lever3 = RecordingLever()
ctl3, audit3 = build(store3, lever3)
broken_err, _ = refuse(
    lambda: ctl3.apply_command(row("fleet.kill"), operator, {"commandId": "cmd_unwritten", "args": []}, "")
)
b_status = getattr(broken_err, "status", None)
b_code = getattr(broken_err, "code", None)
check(broken_err is not None, "CONTROL-4: an unrecorded effect was reported as a SUCCESS")
check(b_status == 503, f"CONTROL-4: an unrecorded effect answered {b_status}, not 503")
check(b_code == "audit_unavailable", f"CONTROL-4: answers code {b_code!r}, not audit_unavailable")
check(
    audit3.applied("cmd_unwritten") is not None,
    "CONTROL-4: an applied-but-unrecorded command id was NOT spent (a retry could double the effect)",
)
print("CONTROL4 audit_unavailable status=%s code=%s id_spent=%s" % (b_status, b_code, True))

# ---------------------------------------------------------------------------
# the rails: the EXISTING store's layout, and no third file
# ---------------------------------------------------------------------------
written = sorted(
    str(p.relative_to(scratch)) for p in scratch.rglob("*") if p.is_file()
)
ledger_file = scratch / "ledger" / f"{mod.TENANT_ID}.jsonl"
check(ledger_file.is_file(), f"RAILS: no per-tenant chain at {ledger_file}")
check(
    (scratch / "slog.jsonl").is_file(),
    "RAILS: the .fleet/slog.jsonl writer did not append to the redirected stream",
)
expected_files = {
    str(Path("ledger") / f"{mod.TENANT_ID}.jsonl"),
    "slog.jsonl",
}
check(
    set(written) == expected_files,
    f"RAILS: an applied command created unexpected file(s) "
    f"{sorted(set(written) - expected_files)} — the record must land on the existing rails",
)
check(
    not (scratch / "ledger2" / f"{mod.TENANT_ID}.jsonl").exists(),
    "RAILS: a chain file exists for the unreachable command — it had no effect and must write nothing",
)
print("RAILS tenant=%s files=%s" % (mod.TENANT_ID, ",".join(written)))

if failures:
    for finding in failures:
        print(f"  FAIL  {finding}", file=sys.stderr)
    raise SystemExit(1)
print("DRIVER_OK")
PY
}

# ---------------------------------------------------------------------------
# 1. the three acceptance controls, provoked against the real seam
# ---------------------------------------------------------------------------
echo "== controls (provoked) =="
controls_out="$(drive --scratch "$work/probe" 2>&1)"
controls_rc=$?
printf '%s\n' "$controls_out"
if [ "$controls_rc" -ne 0 ]; then
  echo "check-control-audit: FAIL — the exactly-once controls did not hold" >&2
  exit 1
fi

# Assert each control INDEPENDENTLY of the driver's own verdict: if the driver
# were weakened to always report success, these tokens would be absent.
control_expectations=(
  "CONTROL1 replay status=409 code=duplicate_command effect_calls=1 ledger_records=1 slog_records=1 receipt_matches=True"
  "CONTROL2 unreachable status=503 code=lever_unreachable ledger_records=0 slog_records=0"
  "CONTROL3 reordered code=duplicate_command kind=reordered refused=True"
  "stolen kind=stolen refused=True"
  "CONTROL4 audit_unavailable status=503 code=audit_unavailable id_spent=True"
  "RAILS tenant=platform"
)
for expect in "${control_expectations[@]}"; do
  if ! printf '%s\n' "$controls_out" | grep -qF "$expect"; then
    echo "check-control-audit: FAIL — no control proved '$expect'" >&2
    exit 1
  fi
done
echo "  OK    replay (one record, one effect, original receipt) / unreachable (503, no record) / reordered + stolen (refused) / unrecorded effect (503, id spent)"

# ---------------------------------------------------------------------------
# 2. the record path defaults to the EXISTING rail, and mints no ledger
# ---------------------------------------------------------------------------
echo "== rails (existing store, no new ledger) =="
rails_out="$(env PYTHONDONTWRITEBYTECODE=1 python3 - "$root" "$work" <<'PY' 2>&1
import sys
from pathlib import Path

root = Path(sys.argv[1])
work = Path(sys.argv[2])
sys.path.insert(0, str(root))

from portal.server.control_audit import DEFAULT_LEDGER_DIR, EVIDENCE_PREFIX, TENANT_ID, ControlAudit
from portal.server.control_api import InFlightCommands

dir_ = work / "default-rail"
audit = ControlAudit(repo_root=str(root), ledger_dir=str(dir_), rehydrate=False)

# The default is the deployment's rail, overridable by its documented env var,
# and the store is the EXISTING telemetry/ledger package — never a second one.
import ledger as ledger_pkg  # noqa: E402  (after sys.path)

assert type(audit.store).__module__.startswith("ledger"), type(audit.store).__module__
assert isinstance(audit._in_flight, InFlightCommands), "RC-3's in-flight guard was not consumed"

# One applied command, driven through the seam directly: exactly one record.
class Row:
    id = "fleet.pause"
    lever = "fleet/control.py#pause"


class Command:
    id = "cmd_rail"
    verb = "fleet.pause"
    argv = ()
    actor = "user:operator@platform.example.com"


class Record:
    actor = "user:operator@platform.example.com"
    audit_action = "fleet.pause"
    verb = "fleet.pause"
    lever = "fleet/control.py#pause"
    effect_class = "hold"
    exit_code = 0
    output = "pause"
    requested_at = ""

    def as_json(self):
        # The transport's own effect record, reproduced field for field.
        return {
            "commandId": "cmd_rail",
            "verb": self.verb,
            "effectClass": self.effect_class,
            "capability": "fleet:operate",
            "auditAction": self.audit_action,
            "actor": self.actor,
            "idempotent": True,
            "lever": self.lever,
            "args": [],
            "exitCode": self.exit_code,
            "output": self.output,
            "requestedAt": self.requested_at,
        }


class Chan:
    SLOG = work / "default-slog.jsonl"

    def _slog(self, message):
        import json

        Chan.SLOG.parent.mkdir(parents=True, exist_ok=True)
        with Chan.SLOG.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(message) + "\n")


audit.channel = Chan()
assert audit.begin(Command()) is None
audit.finish(Command(), Record())
audit.end(Command())

records = audit.store.records(TENANT_ID)
assert len(records) == 1, f"expected one record, got {len(records)}"
record = records[0]
assert record["evidence"] == EVIDENCE_PREFIX + "cmd_rail", record["evidence"]
assert record["tenantId"] == TENANT_ID
assert record["actor"] == "user:operator@platform.example.com"
assert record["action"] == "fleet.pause"
assert record["resource"] == "fleet/control.py#pause"
# The rail's own chain is intact (the record is not a loose append).
verdict = ledger_pkg.verify_ledger(audit.store, TENANT_ID)
assert verdict.status == "OK", verdict.detail

# A restart re-derives the exactly-once store FROM that rail: no second store.
reopened = ControlAudit(repo_root=str(root), ledger_dir=str(dir_))
assert reopened.applied("cmd_rail") is not None, "the rail did not rehydrate the spent id"
replay = reopened.begin(Command())
assert replay is not None and "already applied" in replay, replay

print("  OK    record on the existing telemetry/ledger chain (%s.jsonl), chain verifies OK" % TENANT_ID)
print("  OK    the default ledger dir is %s (env AO_AUDIT_LEDGER_DIR overrides); RC-3's guard consumed" % DEFAULT_LEDGER_DIR)
print("  OK    the exactly-once store rehydrates from that rail (no second store, no new ledger)")
PY
)"
rails_rc=$?
printf '%s\n' "$rails_out"
if [ "$rails_rc" -ne 0 ]; then
  echo "check-control-audit: FAIL — the record path does not use the existing rails" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# 3. the record path's own suite, run in isolation
# ---------------------------------------------------------------------------
echo "== pytest (portal/tests/test_control_audit.py) =="
suite="portal/tests/test_control_audit.py"
if [ ! -f "$suite" ]; then
  echo "check-control-audit: FAIL — $suite is missing" >&2
  exit 1
fi
suite_out="$(env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q "$suite" 2>&1)"
suite_rc=$?
printf '%s\n' "$suite_out" | tail -4
if [ "$suite_rc" -ne 0 ]; then
  printf '%s\n' "$suite_out" | tail -30 >&2
  echo "check-control-audit: FAIL — the record path's suite is red (pytest exit $suite_rc)" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# 4. control A — the check can fail (a wrong expectation must go red)
# ---------------------------------------------------------------------------
echo "== self-mutation control (the check can fail) =="
selftest_out="$(drive --scratch "$work/selftest" --expect-replay-records 2 2>&1)"
selftest_rc=$?
if [ "$selftest_rc" -ne 1 ]; then
  echo "check-control-audit: FAIL — a wrong replay record count was NOT caught (exit $selftest_rc)" >&2
  printf '%s\n' "$selftest_out" >&2
  exit 1
fi
if ! printf '%s\n' "$selftest_out" | grep -qF "expected 2"; then
  echo "check-control-audit: FAIL — the self-mutation control failed for the wrong reason" >&2
  printf '%s\n' "$selftest_out" >&2
  exit 1
fi
echo "  OK    a wrong expectation goes red naming the record count (GR-12: the assertion bites)"

# ---------------------------------------------------------------------------
# 5. control B — a mutation proof of the committed record path
# ---------------------------------------------------------------------------
echo "== mutation proof (the replay short-circuit) =="
mutant="$work/control_audit_mutant.py"
before_sha="$(sha256sum "$module" | cut -d' ' -f1)"

if ! python3 - "$module" "$mutant" <<'PY'
import sys

source = open(sys.argv[1], encoding="utf-8").read()
anchor = """        with self._lock:
            prior = self._applied.get(command.id)
        if prior is not None:
"""
replacement = """        with self._lock:
            prior = None
        if prior is not None:
"""
if anchor not in source:
    print("MUTATION_ANCHOR_NOT_FOUND", file=sys.stderr)
    raise SystemExit(3)
mutated = source.replace(anchor, replacement, 1)
if mutated == source:
    print("MUTATION_NOOP", file=sys.stderr)
    raise SystemExit(4)
open(sys.argv[2], "w", encoding="utf-8").write(mutated)
print("MUTATION_APPLIED")
PY
then
  echo "check-control-audit: CANNOT-ASSESS — could not build the mutation" >&2
  exit 2
fi

# The mutation must have LANDED: a harness that certifies an unapplied mutation
# proves nothing.
if [ "$(sha256sum "$module" | cut -d' ' -f1)" != "$before_sha" ]; then
  echo "check-control-audit: FAIL — the mutation modified the committed file in place" >&2
  exit 1
fi
if [ "$(sha256sum "$mutant" | cut -d' ' -f1)" = "$before_sha" ]; then
  echo "check-control-audit: FAIL — the mutation changed nothing (a vacuous control)" >&2
  exit 1
fi
echo "  OK    mutation landed (source sha256 $before_sha; mutant differs)"

mutant_out="$(drive --scratch "$work/mutant" --module-file "$mutant" 2>&1)"
mutant_rc=$?
if [ "$mutant_rc" -ne 1 ]; then
  echo "check-control-audit: FAIL — the mutant passed the controls (exit $mutant_rc); the replay short-circuit is not load-bearing" >&2
  printf '%s\n' "$mutant_out" >&2
  exit 1
fi
if ! printf '%s\n' "$mutant_out" | grep -qF "A SECOND EFFECT"; then
  echo "check-control-audit: FAIL — the mutant went red without naming the second effect" >&2
  printf '%s\n' "$mutant_out" >&2
  exit 1
fi
echo "  OK    the mutant goes red naming the SECOND EFFECT (rc 1), not a passing formality"

after_sha="$(sha256sum "$module" | cut -d' ' -f1)"
if [ "$after_sha" != "$before_sha" ]; then
  echo "check-control-audit: FAIL — the committed file did not survive the proof (sha256 $after_sha != $before_sha)" >&2
  exit 1
fi
echo "  OK    committed source restored byte-identical (sha256 $after_sha)"

echo "check-control-audit: OK — one effect, one record, on the existing rails; a repeat is refused"
exit 0
