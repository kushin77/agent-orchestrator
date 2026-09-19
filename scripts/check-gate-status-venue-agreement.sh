#!/usr/bin/env bash
# check-gate-status-venue-agreement.sh — prove a green cannot be published for a
# commit whose own Cloud Build run is red or still running (issue #1400).
#
# THE DEFECT THIS EXISTS FOR (measured, not hypothesised)
#   PR #1398 ("train: land 18 verified PRs") merged as `b8ad98f4` and posted
#   `ao/gate-of-record = success` on the train head `f300954d`. The ONLY Cloud
#   Build run for that sha existed from 03:51:19Z and concluded FAILURE at
#   04:19:01Z (build 32cb10f7, `check-reconcile: FAIL (1 violation)`), while the
#   standing status was posted at 03:53:43Z -- DURING that run, from outside the
#   CI venue (`target_url: null`, no check-run behind it). `ao/gate-of-record`
#   is the required check on `master` and the only evidence `scripts/pr-queue.sh`
#   reads, so the merged-tree evidence read green for a tree CI had failed.
#
#   Two producers of one required context, and the guard reads the STATUS: it
#   never sees the run. A gate that fails open is worse than no gate.
#
# THE RULE (implemented in scripts/gate-status.sh, not re-implemented here)
#   A post that is not the venue reporting its own verdict must not publish
#   `success` for a commit whose venue run is RED or UN-CONCLUDED; an unreadable
#   venue verdict is CANNOT-ASSESS, never an agreement; and a RED gate is never
#   blocked. `reconcile` is the other half, which exists because the halves are
#   not symmetric in time: a green published before the venue produced any run
#   cannot be refused at post time, so an after-the-fact withdrawal is needed.
#
# HOW IT PROVES ITSELF
#   Every arm drives the REAL poster (never a copy) against a stubbed `gh` on
#   PATH: the fixture supplies the venue's check-runs, the published status, and
#   a record of every POST the poster attempts. So each arm asserts BOTH the exit
#   code AND its side effect -- a refusal that still posted would otherwise read
#   as a pass.
#
#   The venue of record's OWN record is stubbed the same way (`gcloud`, issue
#   #1467). It is stubbed rather than read for real because the alternative is a
#   gate whose verdict depends on whether a REMOTE build's log still exists and
#   still has the poster step in its last bytes -- measured: the real build this
#   fixture names was readable one minute and its tail carried a check's output
#   the next, which is a gate that answers differently on the same tree.
#
#   Arms 1-2 are the measured case and its red twin: green + the venue's run
#   red/un-concluded => REFUSED by name, and NOTHING posted. Arm 3 is the
#   healthy path (venue concluded success => the green is published). Arm 4 is
#   vacuity: a refusal must not come from a blanket refusal, so the same poster
#   with no venue run at all still publishes. Arm 5 keeps the CI venue alive
#   (its own in-flight run must not refuse it -- without this the guard would
#   silently kill the only producer the required check has). Arm 6 proves the
#   guard never suppresses a RED. Arm 7 is fail-closed on an unreadable venue.
#   Arm 8 is fail-CLOSED on a conclusion nobody enumerated. Arm 9 is the
#   MUTANT: the guard removed => the refusal arm must disappear, so the control
#   is measuring the guard and not something else. Arms 10-13 provoke
#   `reconcile`, which may only ever withdraw a green -- never publish one.
#   Arms 14-15 are issue #1467's scope, in both directions: a red the venue
#   MEASURED it cannot deliver loses its precedence (the deadlock), and a venue
#   record that could not be READ keeps it (fail closed).
#
# Exit codes: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# Usage: bash scripts/check-gate-status-venue-agreement.sh
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)" || exit 2
cd "$root" || exit 2

POSTER="scripts/gate-status.sh"
fail=0
arm_count=0

ok()  { printf '  OK    %s\n' "$1"; }
bad() { printf '  FAIL  %s\n' "$1" >&2; fail=1; }

command -v python3 >/dev/null 2>&1 || { echo "check-gate-status-venue-agreement: CANNOT-ASSESS — python3 not found" >&2; exit 2; }
[ -f "$POSTER" ] || { echo "check-gate-status-venue-agreement: CANNOT-ASSESS — the poster is missing: $POSTER" >&2; exit 2; }
bash -n "$POSTER" || { echo "check-gate-status-venue-agreement: FAIL — the poster does not parse" >&2; exit 1; }

scratch="$(mktemp -d /tmp/ao-gs-venue.XXXXXX)" || { echo "check-gate-status-venue-agreement: CANNOT-ASSESS — no scratch directory" >&2; exit 2; }
trap 'rm -rf "$scratch"' EXIT

SHA="f300954d8a5c9cfa5646669f8e60596cabe88afa"
BUILD="32cb10f7-f4c2-4522-87f9-84d9da1d56aa"
BUILD_URL="https://console.cloud.google.com/cloud-build/builds;region=us-central1/$BUILD?project=1056038104733"

# --- the stubbed `gh` --------------------------------------------------------
# The boundary that is stubbed is the API, never the poster: the poster's own
# parsing, decision and refusal all run.
mkdir -p "$scratch/bin"
cat > "$scratch/bin/gh" <<'STUB'
#!/usr/bin/env bash
# Fixture stand-in for `gh api`. Records its argv, then answers from the fixture.
set -u
{ printf '%s\n' "$*" >> "${STUB_EVENTS:?}"; } 2>/dev/null || true
case "$*" in
  *"-X POST"*) exit 0 ;;
  *"/check-runs"*)
    if [ "${STUB_READ_RC:-0}" != "0" ]; then
      echo "stub gh: the venue read failed" >&2
      exit "${STUB_READ_RC}"
    fi
    cat "${STUB_CHECKRUNS:?}"
    exit 0
    ;;
  *"/commits/"*"/status")
    cat "${STUB_STATUS:?}"
    exit 0
    ;;
esac
exit 0
STUB
chmod +x "$scratch/bin/gh"

# --- the stubbed `gcloud` (issue #1467) --------------------------------------
# The venue of record's OWN record of whether its poster step could reach the
# API. `STUB_VENUE_LOG=none` makes the read fail, which is how an unreadable
# venue record is provoked. Default: the venue DELIVERED its red -- so every arm
# that predates #1467 keeps refusing for exactly the reason it always asserted.
cat > "$scratch/bin/gcloud" <<'STUB'
#!/usr/bin/env bash
# Fixture stand-in for `gcloud builds log`. Records its argv, then serves the
# venue record `STUB_VENUE_LOG` names; `none` fails the read.
set -u
{ printf 'gcloud %s\n' "$*" >> "${STUB_EVENTS:?}"; } 2>/dev/null || true
case "${1:-} ${2:-}" in
  "builds log")
    [ "${STUB_VENUE_LOG:-none}" != "none" ] || exit 1
    cat "$STUB_VENUE_LOG"; exit 0 ;;
esac
exit 1
STUB
chmod +x "$scratch/bin/gcloud"
printf 'gate-status: posted ao/gate-of-record=failure for f300954d8a5c (make verify: FAIL)\n' \
  > "$scratch/venue-delivered.log"
# The venue's own words, quoted from its real build: BOTH producers inside the
# venue say so, which is why the classifier quotes the LAST refusal rather than
# the first.
cat > "$scratch/venue-unable.log" <<'LOG'
verify: NOTE -- the gate of record was NOT published for f300954d8a5c (the poster exited 2): gate-status: CANNOT-ASSESS - gh not found and no GH_TOKEN/GITHUB_TOKEN in env; status not posted
gate-status: SKIPPED -- this runner image carries no gcloud, so the token cannot be read
  here at all: creating ao-gate-status-token is NOT sufficient for this venue. The gate
  verdict is NOT posted (issue #1350; the venue shape is #1361).
LOG

# --- fixtures ---------------------------------------------------------------
write_runs() { # <file> <status> <conclusion>
  python3 - "$1" "$2" "$3" "$BUILD" "$BUILD_URL" <<'PY'
import json
import sys

path, status, conclusion, build, url = sys.argv[1:6]
runs = [] if status == "none" else [{
    "id": 105834977659,
    "name": "control-plane-verify (purebliss-ghl)",
    "status": status,
    "conclusion": None if conclusion == "none" else conclusion,
    "started_at": "2026-09-19T03:51:19Z",
    "completed_at": None if status != "completed" else "2026-09-19T04:19:01Z",
    "details_url": url,
    "app": {"slug": "google-cloud-build"},
}]
with open(path, "w") as fh:
    json.dump({"check_runs": runs}, fh)
PY
}

write_status() { # <file> <state-or-none> <description>
  python3 - "$1" "$2" "$3" <<'PY'
import json
import sys

path, state, description = sys.argv[1:4]
statuses = [] if state == "none" else [{
    "context": "ao/gate-of-record",
    "state": state,
    "description": description,
    "created_at": "2026-09-19T03:53:43Z",
}]
with open(path, "w") as fh:
    json.dump({"state": statuses[0]["state"] if statuses else "pending", "statuses": statuses}, fh)
PY
}

write_runs "$scratch/live.json"     in_progress none
write_runs "$scratch/red.json"      completed   failure
write_runs "$scratch/green.json"    completed   success
write_runs "$scratch/none.json"     none        none
write_runs "$scratch/cancel.json"   completed   cancelled
write_runs "$scratch/mystery.json"  completed   mystery-conclusion
write_status "$scratch/green-status.json" success "make verify: PASS"
write_status "$scratch/no-status.json"    none    ""

EVENTS="$scratch/events.log"

# run_poster <label> <runs-fixture> <status-fixture> <expect-rc> <assert-expr>
# Every arm runs the REAL poster with the fixture on PATH.
poster_run() { # <runs-fixture> <status-fixture> <extra...>
  local runs="$1" status="$2"; shift 2
  : > "$EVENTS"
  PATH="$scratch/bin:$PATH" \
  STUB_EVENTS="$EVENTS" STUB_CHECKRUNS="$scratch/$runs" STUB_STATUS="$scratch/$status" \
  STUB_READ_RC="${STUB_READ_RC_ARM:-0}" \
  STUB_VENUE_LOG="${STUB_VENUE_LOG_ARM:-$scratch/venue-delivered.log}" \
    bash "$POSTER" "$@" >"$scratch/out.txt" 2>&1
}

posted() { # <state> -- was a POST attempted with that state?
  local needle="state=$1"
  case "$(cat "$EVENTS")" in
    *"$needle"*) return 0 ;;
  esac
  return 1
}

any_post() {
  case "$(cat "$EVENTS")" in
    *"-X POST"*) return 0 ;;
  esac
  return 1
}

echo "== 1. the measured case: a green posted while the venue's run is in flight =="
poster_run live.json no-status.json post --sha "$SHA" --rc 0
rc=$?
arm_count=$((arm_count + 1))
if [ "$rc" -ne 2 ]; then
  bad "the measured case was NOT refused (expected rc 2, got rc $rc): $(cat "$scratch/out.txt")"
elif ! grep -qF "REFUSED" "$scratch/out.txt"; then
  bad "the measured case refused with the wrong verdict: $(cat "$scratch/out.txt")"
elif ! grep -qF "has NOT concluded" "$scratch/out.txt"; then
  bad "the refusal does not name the un-concluded run: $(cat "$scratch/out.txt")"
elif ! grep -qF "control-plane-verify" "$scratch/out.txt"; then
  bad "the refusal does not name the venue's check-run: $(cat "$scratch/out.txt")"
elif any_post; then
  bad "the measured case was refused but a POST was still attempted: $(cat "$EVENTS")"
else
  ok "the measured case is REFUSED by name, and nothing was posted (rc 2)"
fi

echo "== 2. a contradicting red: the venue's run for this commit concluded failure =="
poster_run red.json no-status.json post --sha "$SHA" --rc 0
rc=$?
arm_count=$((arm_count + 1))
if [ "$rc" -ne 2 ]; then
  bad "a green alongside a terminal red was NOT refused (expected rc 2, got rc $rc): $(cat "$scratch/out.txt")"
elif ! grep -qF "concluded 'failure'" "$scratch/out.txt"; then
  bad "the refusal does not name the conclusion: $(cat "$scratch/out.txt")"
elif ! grep -qF "$BUILD" "$scratch/out.txt"; then
  bad "the refusal does not name the build that is the evidence: $(cat "$scratch/out.txt")"
elif any_post; then
  bad "the refusal still posted: $(cat "$EVENTS")"
else
  ok "a green alongside a red run is REFUSED by name, naming the build, and nothing is posted"
fi

echo "== 3. the healthy path: the venue concluded success =="
poster_run green.json no-status.json post --sha "$SHA" --rc 0
rc=$?
arm_count=$((arm_count + 1))
if [ "$rc" -ne 0 ]; then
  bad "the healthy path did not publish (expected rc 0, got rc $rc): $(cat "$scratch/out.txt")"
elif ! posted success; then
  bad "the healthy path posted no success: $(cat "$EVENTS")"
elif ! grep -qF "context=ao/gate-of-record" "$EVENTS"; then
  bad "the healthy path posted the wrong context: $(cat "$EVENTS")"
elif ! grep -qF "target_url=$BUILD_URL" "$EVENTS"; then
  bad "the healthy path did not carry the venue's evidence: $(cat "$EVENTS")"
else
  ok "a green is published when the venue agrees, carrying the venue's own evidence URL"
fi

echo "== 4. vacuity: no venue run at all is not a refusal (a blanket refusal proves nothing) =="
poster_run none.json no-status.json post --sha "$SHA" --rc 0
rc=$?
arm_count=$((arm_count + 1))
if [ "$rc" -ne 0 ]; then
  bad "a commit the venue never ran is REFUSED (expected rc 0, got rc $rc): $(cat "$scratch/out.txt")"
elif ! posted success; then
  bad "a commit the venue never ran published nothing: $(cat "$EVENTS")"
else
  ok "when the venue produced no run for the commit, the box-side producer still publishes (rc 0)"
fi

echo "== 5. the venue of record reports its own verdict (--venue-run <BUILD_ID>) =="
poster_run live.json no-status.json post --sha "$SHA" --rc 0 --venue-run "$BUILD"
rc=$?
arm_count=$((arm_count + 1))
if [ "$rc" -ne 0 ]; then
  bad "the venue's own report was refused by its own in-flight run (expected rc 0, got rc $rc): $(cat "$scratch/out.txt")"
elif ! posted success; then
  bad "the venue's own report posted nothing: $(cat "$EVENTS")"
else
  ok "the CI venue's own report is not refused by its own in-flight run (the producer survives)"
fi

poster_run live.json no-status.json post --sha "$SHA" --rc 0 --venue-run "not-a-build-id"
rc=$?
arm_count=$((arm_count + 1))
if [ "$rc" -ne 2 ]; then
  bad "--venue-run accepted a non-build-id (expected rc 2, got rc $rc): $(cat "$scratch/out.txt")"
elif any_post; then
  bad "the refused marker still posted: $(cat "$EVENTS")"
else
  ok "the venue's marker is a claim with a shape: a non-build-id is REFUSED"
fi

echo "== 6. a red gate is NEVER blocked: reporting a failure is always allowed =="
poster_run live.json no-status.json post --sha "$SHA" --rc 1
rc=$?
arm_count=$((arm_count + 1))
if [ "$rc" -ne 0 ]; then
  bad "reporting a failure was refused (expected rc 0, got rc $rc): $(cat "$scratch/out.txt")"
elif ! posted failure; then
  bad "the failure was not published: $(cat "$EVENTS")"
else
  ok "a red gate still reports itself as a failure while the venue is in flight"
fi

echo "== 7. fail-closed: an unreadable venue verdict is CANNOT-ASSESS, never an agreement =="
STUB_READ_RC_ARM=1 poster_run live.json no-status.json post --sha "$SHA" --rc 0
rc=$?
arm_count=$((arm_count + 1))
if [ "$rc" -ne 2 ]; then
  bad "an unreadable venue verdict did NOT fail closed (expected rc 2, got rc $rc): $(cat "$scratch/out.txt")"
elif ! grep -qF "CANNOT-ASSESS" "$scratch/out.txt"; then
  bad "the unreadable read did not report CANNOT-ASSESS: $(cat "$scratch/out.txt")"
elif any_post; then
  bad "an unreadable verdict still posted a green: $(cat "$EVENTS")"
else
  ok "an unreadable venue verdict is CANNOT-ASSESS, and nothing is posted"
fi

echo "== 8. an unnamed conclusion is a contradiction, not an agreement =="
for fixture in cancel.json mystery.json; do
  poster_run "$fixture" no-status.json post --sha "$SHA" --rc 0
  rc=$?
  arm_count=$((arm_count + 1))
  if [ "$rc" -ne 2 ] || any_post; then
    bad "$fixture was read as agreement (rc $rc): $(cat "$scratch/out.txt")"
  else
    ok "the conclusions in $fixture are refused (an unnamed conclusion is not a pass)"
  fi
done

echo "== 9. MUTANT: the guard removed => the refusal must disappear =="
# A refusal arm proves the control only if the control is what produced it. The
# mutant is a COPY (the real tree is never touched) with the guard's condition
# made unsatisfiable, and the measured case must then PUBLISH.
mkdir -p "$scratch/mutant/scripts"
cp "$POSTER" scripts/gate-status-map.py "$scratch/mutant/scripts/"
sed -i 's/    if \[ "\$state" = "success" \]; then/    if [ "\$state" = "success" ] \&\& [ -n "\${AO_MUTANT_GUARD:-}" ]; then/' \
  "$scratch/mutant/scripts/gate-status.sh"
if cmp -s "$POSTER" "$scratch/mutant/scripts/gate-status.sh"; then
  bad "MUTANT guard-removed: the mutation was a no-op, so it proves nothing"
else
  : > "$EVENTS"
  PATH="$scratch/bin:$PATH" \
  STUB_EVENTS="$EVENTS" STUB_CHECKRUNS="$scratch/live.json" STUB_STATUS="$scratch/no-status.json" \
  STUB_READ_RC=0 bash "$scratch/mutant/scripts/gate-status.sh" post --sha "$SHA" --rc 0 \
    >"$scratch/mutant-out.txt" 2>&1
  rc=$?
  arm_count=$((arm_count + 1))
  if [ "$rc" -ne 0 ]; then
    bad "MUTANT guard-removed still refused (rc $rc) — the refusal is NOT coming from the guard: $(cat "$scratch/mutant-out.txt")"
  elif ! posted success; then
    bad "MUTANT guard-removed published nothing: $(cat "$EVENTS")"
  else
    ok "with the guard removed the same case publishes the green, so arm 1 measures the guard"
  fi
fi

echo "== 10-13. reconcile: the published context is brought back into agreement =="
poster_run red.json green-status.json reconcile --sha "$SHA"
rc=$?
arm_count=$((arm_count + 1))
if [ "$rc" -ne 0 ]; then
  bad "reconcile against a contradicting red did not succeed (expected rc 0, got rc $rc): $(cat "$scratch/out.txt")"
elif ! posted error; then
  bad "reconcile did not withdraw the standing green: $(cat "$EVENTS")"
elif ! grep -qF "target_url=$BUILD_URL" "$EVENTS"; then
  bad "the withdrawal does not carry the evidence URL: $(cat "$EVENTS")"
else
  ok "a standing green is WITHDRAWN (state=error) when the venue's run for the same commit concluded red"
fi

poster_run green.json green-status.json reconcile --sha "$SHA"
rc=$?
arm_count=$((arm_count + 1))
if [ "$rc" -ne 0 ] || any_post; then
  bad "reconcile posted although the green already agrees with the venue (rc $rc, events: $(cat "$EVENTS"))"
elif ! grep -qF "agrees with the venue of record" "$scratch/out.txt"; then
  bad "reconcile did not read the published status it was supposed to judge: $(cat "$scratch/out.txt")"
else
  ok "reconcile posts NOTHING when the published green agrees with the venue (and says so)"
fi

poster_run live.json green-status.json reconcile --sha "$SHA"
rc=$?
arm_count=$((arm_count + 1))
if [ "$rc" -ne 0 ] || any_post; then
  bad "reconcile withdrew while the venue was merely running (rc $rc): $(cat "$EVENTS")"
elif ! grep -qF "nothing is withdrawn while the venue is still running" "$scratch/out.txt"; then
  bad "reconcile did not report the unsettled-venue reason: $(cat "$scratch/out.txt")"
else
  ok "reconcile withdraws nothing while the venue is still running (unsettled is not a contradiction)"
fi

poster_run red.json no-status.json reconcile --sha "$SHA"
rc=$?
arm_count=$((arm_count + 1))
if [ "$rc" -ne 0 ] || any_post; then
  bad "reconcile posted although nothing was published under the context (rc $rc): $(cat "$EVENTS")"
elif ! grep -qF "nothing is published under" "$scratch/out.txt"; then
  bad "reconcile did not read the published status it was supposed to judge: $(cat "$scratch/out.txt")"
else
  ok "reconcile posts NOTHING when the context has no published status at all (and says so)"
fi

# The final vacuity guard: reconcile may only ever publish a NON-success.
poster_run red.json green-status.json reconcile --sha "$SHA"
arm_count=$((arm_count + 1))
if posted success; then
  bad "reconcile published a SUCCESS — the withdrawal path must never be a second way to satisfy the context"
elif posted error; then
  ok "reconcile can only ever publish a non-success (it withdrew with state=error)"
else
  bad "reconcile neither withdrew nor published anything unexpected: $(cat "$EVENTS")"
fi

echo "== 14. issue #1467: a red the venue MEASURED it cannot deliver loses its precedence =="
# The measured deadlock: the venue of record's runner image carries neither `gh`
# nor `gcloud`, so its own poster step exits 2 for want of a credential and
# publishes NOTHING -- not even the red. The ordered rule then had no satisfier at
# either end, and every PR head stayed BLOCKED behind the admin bypass. The
# venue's OWN record is what scopes the guard: its red governs while it can
# deliver one, and the red still travels -- in target_url.
STUB_VENUE_LOG_ARM="$scratch/venue-unable.log" poster_run red.json no-status.json post --sha "$SHA" --rc 0
rc=$?
arm_count=$((arm_count + 1))
if [ "$rc" -ne 0 ]; then
  bad "a red venue that MEASURED it cannot deliver still deadlocked the required context (expected rc 0, got rc $rc): $(cat "$scratch/out.txt")"
elif ! posted success; then
  bad "the undeliverable red published nothing: $(cat "$EVENTS")"
elif ! grep -qF "target_url=$BUILD_URL" "$EVENTS"; then
  bad "the waiver did not carry the venue's red as evidence: $(cat "$EVENTS")"
elif ! grep -qF "VENUE UNABLE TO DELIVER" "$scratch/out.txt"; then
  bad "the waiver was not named: $(cat "$scratch/out.txt")"
else
  ok "a red the venue could not DELIVER no longer blocks: this run's green is published, the red linked"
fi

echo "== 15. fail-closed: an unreadable venue RECORD is never a waiver =="
# The direction that would make arm 14 dishonest. "I could not read the venue"
# must never become "therefore the venue does not matter".
STUB_VENUE_LOG_ARM=none poster_run red.json no-status.json post --sha "$SHA" --rc 0
rc=$?
arm_count=$((arm_count + 1))
if [ "$rc" -ne 2 ]; then
  bad "an unreadable venue record was treated as a waiver (expected rc 2, got rc $rc): $(cat "$scratch/out.txt")"
elif any_post; then
  bad "the unreadable record still posted a green: $(cat "$EVENTS")"
elif ! grep -qF "concluded 'failure'" "$scratch/out.txt"; then
  bad "the refusal no longer names the conclusion: $(cat "$scratch/out.txt")"
else
  ok "an unreadable venue record keeps the guard standing (CANNOT-ASSESS, nothing posted)"
fi

echo
if [ "$fail" -ne 0 ]; then
  echo "check-gate-status-venue-agreement: FAIL — $arm_count arm(s) run, at least one is not OK (see above)" >&2
  exit 1
fi
echo "check-gate-status-venue-agreement: OK — $arm_count arm(s) provoked: a green is refused while the venue of record's own run for the same commit is red or still running, the refusal names the run, the venue's own report and the healthy and un-run paths still publish, a red is never blocked, an unreadable verdict fails closed, a red the venue MEASURED it cannot deliver no longer deadlocks the required context while an unreadable record keeps it blocked, removing the guard restores the false green, and reconcile can only withdraw"
exit 0
