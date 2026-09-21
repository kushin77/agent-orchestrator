#!/usr/bin/env bash
# track-codeidx-capabilities.sh — keep the code-indexing capability register
# honest against the board it is grounded on (issue #479, EPIC #473).
#
# docs/CODEIDX-CAPABILITY-REGISTER.md declares, row by row, what the fleet needs
# from kushin77/code-indexing and where each need stands. A register is only as
# good as the day it was written unless something reconciles it, because an open
# vendor issue is not a shipped capability and a closed vendor issue is not
# evidence either. This tracker reads every row's `ref`, reports the referenced
# issue's state next to the row's declared status, and NAMES every disagreement:
#
#   * SHIPPED-BUT-OPEN       the row claims `shipped`, the issue it cites is open;
#   * SHIPPED-NO-SIGNAL      the row claims `shipped`, the issue is closed, and the
#                            row cites no completion signal (a closed issue is not
#                            one — the register says so in its own rule 4);
#   * UNVERIFIED             the row claims `in-flight`/`gap`, the issue is closed
#                            and no completion signal is cited, so what the
#                            capability now is cannot be said from here. This is a
#                            finding about the row, not a silence;
#   * STALE-DECLARED         the row claims `in-flight`/`gap`, the issue is closed
#                            and a completion signal IS cited: the direction
#                            landed, so the row must be re-grounded;
#   * UNVERIFIED-BUT-CLOSED  the row claims `UNVERIFIED` and its issue is closed:
#                            the provider may have published the contract;
#   * REF-NOT-AN-ISSUE       the `ref` resolves to a pull request, which is not
#                            the issue link the register's ref grammar requires.
#
# Every `ref` cell is reconciled, whichever board it names — the vendor's, CMR's,
# or this repository's own board (`us`-owned rows). A ref the tracker skipped
# would be exactly the staleness it exists to catch, so none is skipped: the
# board breakdown is reported, not assumed.
#
# A `ref` of the literal `GAP`, a row this tracker cannot parse, an absent or
# incomplete recording, and an unreadable board are all CANNOT-ASSESS (2) — never
# a pass: with no issue to read there is nothing to reconcile, and the tri-state
# contract must not collapse that into "ok". The tracker fails closed.
#
# Offline and deterministic by default: it reconciles a RECORDED board state
# (scripts/track-codeidx-capabilities.snapshot.tsv) so it runs with the network
# blocked and byte-identically on two consecutive runs. The live path (`--live`)
# reads the boards through the GitHub API and is OPT-IN — no test here and no
# gate in `make verify` needs it, and nothing in this script's default path
# reaches the network.
#
# Exit-code contract: 0 OK / 1 NOT-OK (at least one named mismatch) /
# 2 CANNOT-ASSESS (an absent register, an unparsable row or ref, an absent or
# incomplete recording, or a board that could not be read).
#
# It runs its own negative control: it flips one row's declared status on a
# scratch copy of the register and requires that mutation to be refused, naming
# the row and the mismatch class the flip must produce. A tracker that cannot
# fail is a formality (no-false-green doctrine, GR-12), so if the mutant passes,
# this tracker reports FAIL and exits 1.
#
# No scheduled automation is added: GitHub Actions is disabled fleet-wide (GR-15),
# so a re-run belongs to the ops runner or cron, not to a workflow file.
#
# Usage:
#   bash scripts/track-codeidx-capabilities.sh                    # offline report
#   bash scripts/track-codeidx-capabilities.sh --emit-doc-section  # doc section
#   bash scripts/track-codeidx-capabilities.sh --snapshot FILE     # other recording
#   bash scripts/track-codeidx-capabilities.sh --live              # opt-in network
set -uo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"
cd "$root" || exit 2

register="docs/CODEIDX-CAPABILITY-REGISTER.md"
snapshot="scripts/track-codeidx-capabilities.snapshot.tsv"
mode="report"
live=0
snapshot_given=0
work=""

usage() {
  cat <<'USAGE'
track-codeidx-capabilities — reconcile the codeidx capability register against
the board states it cites (issue #479).

  bash scripts/track-codeidx-capabilities.sh
      Offline reconciliation against the recorded snapshot. Exit 0 when every
      row agrees with the board, 1 when a row disagrees (each is named), 2 when
      the register or the recording cannot be read.

  --snapshot FILE    reconcile a different recorded board state
  --live             read the boards through the GitHub API (opt-in; network)
  --emit-doc-section print the register's generated reconciliation section
  --help             this text
USAGE
}

say_cannot_assess() {
  echo "track-codeidx-capabilities: CANNOT-ASSESS — $*" >&2
  exit 2
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --snapshot)
      if [ "$#" -lt 2 ]; then
        say_cannot_assess "the --snapshot option needs a file"
      fi
      snapshot="$2"
      snapshot_given=1
      shift 2
      ;;
    --live)
      live=1
      shift
      ;;
    --emit-doc-section)
      mode="markdown"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      say_cannot_assess "unknown argument '$1'"
      ;;
  esac
done

if ! command -v python3 >/dev/null 2>&1; then
  say_cannot_assess "python3 not found"
fi

cleanup() {
  if [ -n "$work" ]; then
    rm -rf "$work"
  fi
}

# reconcile <root> <register> <snapshot> <mode> [extra]
#
# mode `report`   — the reconciliation report (stdout)
# mode `markdown` — the register's generated reconciliation section (stdout)
# mode `refs`     — the distinct refs the register cites, one per line (stdout)
# mode `mutate`   — write a one-row-flipped copy of the register to `extra` and
#                   print "<row-id>\t<class>" for the mismatch it must produce
reconcile() {
  python3 - "$@" <<'PY'
import pathlib
import re
import sys

HEADER = "| capability | needed-for | owner | ref | status | acceptance | verify |"
DELIMITER = re.compile(r"^\|[-:\s|]+\|$")
ROW_ID = re.compile(r"\*\*(C-\d+)\*\*")
LINK = re.compile(r"https://github\.com/([A-Za-z0-9._-]+/[A-Za-z0-9._-]+)/issues/(\d+)")
KEY = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+#[0-9]+$")
STATUSES = ("shipped", "in-flight", "gap", "UNVERIFIED")
STATES = ("open", "closed")
KINDS = ("issue", "pull-request")

CLASS_MEANING = (
    (
        "SHIPPED-BUT-OPEN",
        "a row declares `shipped` and the issue it cites is **open**",
        "the completion claim is ahead of the board: either the issue is open and the row "
        "overstates, or the row's `ref` is the wrong issue",
    ),
    (
        "SHIPPED-NO-SIGNAL",
        "a row declares `shipped`, its issue is closed, and the row cites no `signal=`",
        "a closed issue is not a completion signal, so the row is misclassified and belongs "
        "in `UNVERIFIED`",
    ),
    (
        "UNVERIFIED",
        "a row declares `in-flight`/`gap`, its issue is closed, and no `signal=` is cited",
        "the direction's outcome is unknown from here: the row can no longer be called a gap "
        "and cannot be called shipped either",
    ),
    (
        "STALE-DECLARED",
        "a row declares `in-flight`/`gap`, its issue is closed, and a `signal=` **is** cited",
        "the direction landed: the row is stale and must be re-grounded against the cited signal",
    ),
    (
        "UNVERIFIED-BUT-CLOSED",
        "a row declares `UNVERIFIED` and its issue is closed",
        "the provider may have published the contract after all, so the row's honest state must "
        "be re-checked",
    ),
    (
        "REF-NOT-AN-ISSUE",
        "the `ref` resolves to a pull request",
        "the register's `ref` grammar requires an issue link, and a board numbers issues and pull "
        "requests in one sequence, so a pull request is not a row's grounding",
    ),
)

rows = None
snapshot = None
recorded = None
states = {}
kinds = {}


def split_row(line):
    text = line.strip()
    if not (text.startswith("|") and text.endswith("|")):
        return None
    return [cell.strip() for cell in text[1:-1].split("|")]


def parse_register(path):
    """-> (rows, error). A row is (line number, row id, declared, ref cell)."""
    file = pathlib.Path(path)
    if not file.is_file():
        return None, f"the register {path} is absent"
    try:
        text = file.read_text(encoding="utf-8")
    except OSError as exc:
        return None, f"the register {path} is unreadable ({exc})"
    lines = text.split("\n")
    try:
        header = lines.index(HEADER)
    except ValueError:
        return None, "the register's table header line was not found"
    if header + 1 >= len(lines) or not DELIMITER.match(lines[header + 1].strip()):
        return None, "the register's table has no delimiter line under its header"
    parsed = []
    index = header + 2
    while index < len(lines) and lines[index].startswith("|"):
        cells = split_row(lines[index])
        if cells is None or len(cells) != 7:
            return None, f"register line {index + 1} is not a 7-cell row"
        match = ROW_ID.search(cells[0])
        if match is None:
            return None, f"register line {index + 1} carries no C-nn row id"
        declared = cells[4]
        if declared not in STATUSES:
            return None, (
                f"row {match.group(1)} (register line {index + 1}) declares "
                f"{declared!r}, which is not one of {'/'.join(STATUSES)}"
            )
        parsed.append((index + 1, match.group(1), declared, cells[3]))
        index += 1
    if not parsed:
        return None, "the register table has no body rows"
    return parsed, None


def parse_ref(cell):
    """-> (key, has_signal, error)."""
    if cell.strip() == "GAP":
        return None, False, (
            "a row's `ref` is the literal GAP, so there is no issue to read and staleness "
            "cannot be judged"
        )
    links = LINK.findall(cell)
    if not links:
        return None, False, f"a row's `ref` cell ({cell.strip()[:60]!r}) carries no issue link"
    if len(links) > 1:
        return None, False, "a row's `ref` cell carries more than one issue link, so its grounding is ambiguous"
    owner, number = links[0]
    return f"{owner}#{number}", "signal=" in cell, None


def parse_snapshot(path):
    """-> (error). Fills `recorded`, `states`, `kinds`."""
    global recorded, states, kinds
    file = pathlib.Path(path)
    if not file.is_file():
        return f"the recorded snapshot {path} is absent"
    try:
        text = file.read_text(encoding="utf-8")
    except OSError as exc:
        return f"the recorded snapshot {path} is unreadable ({exc})"
    for number, raw in enumerate(text.split("\n"), 1):
        line = raw.rstrip("\r")
        if line.startswith("#"):
            stamp = re.match(r"#\s*recorded:\s*(\S+)", line)
            if stamp is not None:
                recorded = stamp.group(1)
            continue
        if not line.strip():
            continue
        fields = line.split("\t")
        if len(fields) != 3:
            return f"snapshot line {number} is not 3 tab-separated fields"
        key, state, kind = (field.strip() for field in fields)
        if not KEY.match(key):
            return f"snapshot line {number} has no <owner>/<repo>#<number> key"
        if state not in STATES:
            return f"snapshot line {number} states {state!r}, which is not open/closed"
        if kind not in KINDS:
            return f"snapshot line {number} names kind {kind!r}, which is not issue/pull-request"
        if key in states:
            return f"snapshot line {number} repeats {key}"
        states[key] = state
        kinds[key] = kind
    if not states:
        return "the recorded snapshot names no issue, so nothing can be reconciled"
    return None


def classify(declared, state, kind, has_signal):
    """-> (class, note). Class `ok` means the row agrees with the board."""
    if kind == "pull-request":
        return "REF-NOT-AN-ISSUE", "the ref resolves to a pull request, not the issue link the register's ref grammar requires"
    if declared == "shipped":
        if state == "open":
            return "SHIPPED-BUT-OPEN", "the row is declared shipped and the issue it cites is still open"
        if not has_signal:
            return "SHIPPED-NO-SIGNAL", "the issue is closed but the row cites no completion signal, and a closed issue is not one"
        return "ok", "shipped, closed, with a cited completion signal"
    if declared == "in-flight" or declared == "gap":
        if state == "open":
            return "ok", f"{declared}, and the issue it cites is still open"
        if has_signal:
            return "STALE-DECLARED", "the issue is closed and a completion signal is cited, so the direction landed and the row must be re-grounded"
        return "UNVERIFIED", "the issue is closed and no completion signal is cited, so the capability is unverified from here"
    if declared == "UNVERIFIED":
        if state == "closed":
            return "UNVERIFIED-BUT-CLOSED", "the provider may have published the contract after all, so the row must be re-checked"
        return "ok", "UNVERIFIED, and the provider's issue is still open"
    return None, f"declared status {declared!r} is not reconcilable"


def build(root, register_path, snapshot_path, need_snapshot):
    """-> (error). Fills `rows` and the resolved refs."""
    global rows
    parsed, error = parse_register(register_path)
    if error is not None:
        return error
    resolved = []
    for line, row_id, declared, cell in parsed:
        key, has_signal, error = parse_ref(cell)
        if error is not None:
            return f"row {row_id} (register line {line}): {error}"
        resolved.append((row_id, declared, key, has_signal))
    rows = resolved
    if not need_snapshot:
        return None
    error = parse_snapshot(snapshot_path)
    if error is not None:
        return error
    missing = [key for _, _, key, _ in rows if key not in states]
    if missing:
        return (
            "the recording is incomplete: it names no state for "
            + ", ".join(sorted(set(missing)))
            + " — an absent entry is CANNOT-ASSESS, never a pass"
        )
    return None


def board_breakdown(resolved):
    order = []
    counts = {}
    for _, _, key, _ in resolved:
        board = key.split("#", 1)[0].split("/", 1)[1]
        if board not in counts:
            order.append(board)
            counts[board] = 0
        counts[board] += 1
    return [(board, counts[board]) for board in order]


def label(root, path):
    """A path as the tracker was invoked with, when it is inside the checkout."""
    try:
        return str(pathlib.Path(path).resolve().relative_to(pathlib.Path(root).resolve()))
    except (ValueError, OSError):
        return str(path)


def main():
    if len(sys.argv) < 5:
        print("  CANNOT-ASSESS  the tracker engine needs <root> <register> <snapshot> <mode>", file=sys.stderr)
        return 2
    root, register_path, snapshot_path, mode = sys.argv[1:5]
    extra = sys.argv[5] if len(sys.argv) > 5 else None

    if mode == "refs":
        error = build(root, register_path, snapshot_path, need_snapshot=False)
        if error is not None:
            print(f"  CANNOT-ASSESS  {error}", file=sys.stderr)
            return 2
        seen = []
        for _, _, key, _ in rows:
            if key not in seen:
                seen.append(key)
        for key in seen:
            print(key)
        return 0

    if mode == "mutate":
        if extra is None:
            print("  CANNOT-ASSESS  the mutate mode needs an output path", file=sys.stderr)
            return 2
        error = build(root, register_path, snapshot_path, need_snapshot=True)
        if error is not None:
            print(f"  CANNOT-ASSESS  {error}", file=sys.stderr)
            return 2
        # Flip the first row's declared status to the pole its live state
        # contradicts: an open issue cannot be `shipped`, and a closed issue
        # cannot stay a `gap`. Either flip must be refused by name.
        row_id, declared, key, _ = rows[0]
        state = states[key]
        flipped = "shipped" if state == "open" else "gap"
        expected = "SHIPPED-BUT-OPEN" if state == "open" else (
            "STALE-DECLARED" if rows[0][3] else "UNVERIFIED"
        )
        lines = pathlib.Path(register_path).read_text(encoding="utf-8").split("\n")
        header = lines.index(HEADER)
        index = header + 2
        changed = False
        while index < len(lines) and lines[index].startswith("|"):
            if not changed:
                cells = split_row(lines[index])
                if cells is not None and len(cells) == 7 and row_id in cells[0]:
                    cells[4] = flipped
                    lines[index] = "| " + " | ".join(cells) + " |"
                    changed = True
            index += 1
        if not changed:
            print(f"  CANNOT-ASSESS  the control could not flip row {row_id}", file=sys.stderr)
            return 2
        pathlib.Path(extra).write_text("\n".join(lines), encoding="utf-8")
        print(f"{row_id}\t{expected}")
        return 0

    error = build(root, register_path, snapshot_path, need_snapshot=True)
    if error is not None:
        print(f"  CANNOT-ASSESS  {error}", file=sys.stderr)
        return 2

    results = []
    for row_id, declared, key, has_signal in rows:
        state = states[key]
        kind = kinds[key]
        name, note = classify(declared, state, kind, has_signal)
        if name is None:
            print(f"  CANNOT-ASSESS  {note}", file=sys.stderr)
            return 2
        results.append((row_id, declared, key, state, kind, has_signal, name, note))

    mismatches = [item for item in results if item[6] != "ok"]
    unverified = [item for item in mismatches if item[6] == "UNVERIFIED"]
    boards = board_breakdown(rows)
    stamp = recorded or "unstamped"

    if mode == "markdown":
        out = []
        breakdown = ", ".join(f"`{board}` {count}" for board, count in boards)
        out.append("## Reconciliation — the register against the board it cites (`agent-orchestrator#479`)")
        out.append("")
        out.append("This section is **generated** by `scripts/track-codeidx-capabilities.sh --emit-doc-section`;")
        out.append("run the tracker rather than editing it by hand. It is not the register: the register's own rows")
        out.append("are the table under [the register](#the-register), and this is that table's `ref` column")
        out.append("reconciled against a recorded board state — the two are read together and are never confused")
        out.append("for one another.")
        out.append("")
        out.append("**Recorded board state:** `" + label(root, snapshot_path) + "`, recorded " + stamp)
        out.append("across the boards the register cites. The tracker reconciles that recording **offline and")
        out.append("deterministically** — `--live` reads the boards through the GitHub API and is opt-in, so nothing")
        out.append("here, and nothing in `make verify`, needs the network.")
        out.append("")
        out.append("Every `ref` is reconciled, on whichever board it lives — the vendor's, CMR's, or this")
        out.append("repository's own board. A `ref` this tracker skipped would be the staleness it exists to catch,")
        out.append("so the board breakdown is reported rather than assumed: **" + str(len(rows)) + " row(s), "
                   + str(len(rows)) + " ref(s)** — " + breakdown + ".")
        out.append("")
        out.append("| Row | Declared | Referenced issue | Live | Reconciliation |")
        out.append("|---|---|---|---|---|")
        for row_id, declared, key, state, _kind, _signal, name, note in results:
            verdict = "`ok`" if name == "ok" else "**`" + name + "`**"
            out.append(f"| `{row_id}` | `{declared}` | `{key}` | `{state}` | {verdict} — {note} |")
        out.append("")
        out.append("**Totals:** " + str(len(results)) + " row(s), " + str(len(results) - len(mismatches))
                   + " matching, " + str(len(mismatches)) + " mismatching (" + str(len(unverified))
                   + " of them `UNVERIFIED`). A mismatch is exit 1 and every one is named above; a row")
        out.append("whose `ref` cannot be read at all is exit 2 and is never reported as `ok` — the tracker fails")
        out.append("closed.")
        out.append("")
        out.append("### The mismatch classes this tracker names")
        out.append("")
        out.append("| Class | The disagreement | What it means for the register |")
        out.append("|---|---|---|")
        for name, disagreement, meaning in CLASS_MEANING:
            out.append(f"| `{name}` | {disagreement} | {meaning} |")
        print("\n".join(out))
        return 1 if mismatches else 0

    width_row = max([len("ROW")] + [len(item[0]) for item in results])
    width_declared = max([len("DECLARED")] + [len(item[1]) for item in results])
    width_key = max([len("REF")] + [len(item[2]) for item in results])
    width_state = max([len("LIVE")] + [len(item[3]) for item in results])
    width_class = max([len("RECONCILIATION")] + [len(item[6]) for item in results])

    print("codeidx capability tracker — the register against the board it cites (issue #479)")
    print("  register  " + label(root, register_path))
    print("  snapshot  " + label(root, snapshot_path) + " — recorded " + stamp
          + " (offline; the live path is opt-in)")
    print("  rows      " + str(len(rows)) + " — " + str(len(rows)) + " ref(s): "
          + ", ".join(f"{board} {count}" for board, count in boards))
    print("")
    print("  " + "ROW".ljust(width_row) + "  " + "DECLARED".ljust(width_declared) + "  "
          + "REF".ljust(width_key) + "  " + "LIVE".ljust(width_state) + "  RECONCILIATION")
    for row_id, declared, key, state, _kind, _signal, name, _note in results:
        print("  " + row_id.ljust(width_row) + "  " + declared.ljust(width_declared) + "  "
              + key.ljust(width_key) + "  " + state.ljust(width_state) + "  " + name.ljust(width_class))
    print("")
    if mismatches:
        print("  findings:")
        for row_id, declared, key, state, _kind, _signal, name, note in mismatches:
            print(f"    MISMATCH  {row_id}  declared={declared}  live={state}  class={name}  ref={key}  — {note}")
        print("")
        print("track-codeidx-capabilities: NOT-OK — " + str(len(mismatches)) + " of " + str(len(results))
              + " row(s) disagree with the issue they cite"
              + (" (" + str(len(unverified)) + " of them UNVERIFIED)" if unverified else ""))
        return 1
    print("track-codeidx-capabilities: OK — all " + str(len(results))
          + " row(s) reconcile with the issue each one cites")
    return 0


raise SystemExit(main())
PY
}

say_ok() {
  if [ "$mode" = "markdown" ]; then
    printf '  OK    %s\n' "$*" >&2
  else
    printf '  OK    %s\n' "$*"
  fi
}

say_flag() {
  printf '  FAIL  %s\n' "$*" >&2
}

trap cleanup EXIT

if [ "$live" -eq 1 ]; then
  # The live path is opt-in and shares the whole reconciliation below: it only
  # replaces the recording. It is never reached by a test or by `make verify`.
  if [ "$snapshot_given" -eq 1 ]; then
    say_cannot_assess "--live and --snapshot are mutually exclusive; recording and live reading are two sources, not one"
  fi
  if ! command -v gh >/dev/null 2>&1; then
    say_cannot_assess "the --live option needs the GitHub CLI, which is not installed"
  fi
  work="/tmp/ao479-track-codeidx.$$.$(date +%s)"
  if ! mkdir -p "$work"; then
    say_cannot_assess "could not create a scratch directory"
  fi
  refs="$(reconcile "$root" "$register" "$snapshot" refs)"
  refs_rc=$?
  if [ "$refs_rc" -ne 0 ]; then
    exit 2
  fi
  live_snapshot="$work/live.snapshot.tsv"
  {
    printf '# recorded: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf '# source: gh api repos/<owner>/<repo>/issues/<n> (live path of issue #479)\n'
    printf '# format: <owner>/<repo>#<number>\t<open|closed>\t<issue|pull-request>\n'
    while IFS= read -r key; do
      if [ -z "$key" ]; then
        continue
      fi
      repo="${key%%#*}"
      number="${key##*#}"
      if ! row="$(gh api "repos/$repo/issues/$number" --jq '[.state, (if .pull_request then "pull-request" else "issue" end)] | @tsv' 2>&1)"; then
        printf '  CANNOT-ASSESS  %s could not be read (%s)\n' "$key" "$row" >&2
        exit 2
      fi
      printf '%s\t%s\n' "$key" "$row"
    done <<< "$refs"
  } > "$live_snapshot"
  if [ ! -s "$live_snapshot" ]; then
    say_cannot_assess "the live recording came back empty"
  fi
  snapshot="$live_snapshot"
fi

report="$(reconcile "$root" "$register" "$snapshot" "$mode")"
report_rc=$?
printf '%s\n' "$report"

if [ "$report_rc" -eq 2 ]; then
  exit 2
fi

# --- internal negative control ----------------------------------------------
# Flip one row's declared status on a scratch COPY of the register and require
# the tracker to refuse the mutant, naming the row and the class the flip must
# produce. A tracker that cannot fail is a formality (GR-12).
if [ -z "$work" ]; then
  work="/tmp/ao479-track-codeidx.$$.$(date +%s)"
  if ! mkdir -p "$work"; then
    say_cannot_assess "could not create a scratch directory"
  fi
fi
mutant_register="$work/register.mutant.md"

if ! control="$(reconcile "$root" "$register" "$snapshot" mutate "$mutant_register")"; then
  say_flag "negative control: the mutant register could not be built"
  exit 2
fi
control_row="$(printf '%s' "$control" | cut -f1)"
control_class="$(printf '%s' "$control" | cut -f2)"

if ! cmp -s "$register" "$mutant_register"; then
  mutant_out="$(reconcile "$root" "$mutant_register" "$snapshot" report 2>&1)"
  mutant_rc=$?
  if [ "$mutant_rc" -eq 1 ] && printf '%s\n' "$mutant_out" | grep -qE "^    MISMATCH  ${control_row}  declared=.*  class=${control_class}  "; then
    say_ok "negative control: flipping $control_row to a status its live state contradicts is refused as $control_class"
  else
    say_flag "negative control passed: flipping $control_row was not refused by name (rc $mutant_rc)"
    printf '%s\n' "$mutant_out" >&2
    exit 1
  fi
else
  say_flag "negative control: the mutation changed nothing, so it proved nothing"
  exit 1
fi

if [ "$report_rc" -eq 1 ]; then
  exit 1
fi
exit 0
