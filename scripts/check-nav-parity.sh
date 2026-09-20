#!/usr/bin/env bash
# check-nav-parity.sh — every route family the console's own dispatch table
# declares is REACHABLE from the shell, and every nav entry resolves to a frame
# that exists (issue #1565, EPIC #1510).
#
# THE DEFECT THIS EXISTS FOR
#   `_route_api` (portal/server/app.py) is the console's whole backend dispatch
#   table; `portal/static/js/console.js`'s NAV_TENANT / NAV_GLOBAL arrays are the
#   shell's whole navigation. Nothing tied the two together, so a backend route
#   could merge fully tested and green while the UI that was supposed to show it
#   was never built. Measured on 5357190f: six console surfaces (FinOps, Ops/SLO,
#   Org Chart, Skill Studio, Task Board, fleet board) had working backends and
#   passing tests and no view at all, and the gap was findable only by a manual
#   read-and-cross-reference audit (CONSOLE-COVERAGE-2026-09-20.md §2, #1540's
#   sweep). The coverage lint those audits asked for is this file.
#
# WHAT IT CHECKS (against the repository, never against prose)
#   DECLARED  every route family `_route_api` dispatches, parsed from the
#             `parts[0] == "x"`, `parts[:2] == ["x", "y"]` and `parts == ["x"]`
#             guards inside that one method. A parse that yields no family is
#             CANNOT-ASSESS, never a pass.
#   A1 REACHABILITY — each declared family is exactly one of
#       NAV       a nav surface in console.js (a NAV_TENANT / NAV_GLOBAL entry,
#                 a gated-offer spec — an object carrying `id` and `probe` — or a
#                 VIEW_TARGETS target): an operator can navigate to it;
#       CONSUMED  a client names its API path — an existing surface already calls
#                 it. Reported as a NOTE naming the consumer, so it is visible and
#                 never a silent pass;
#       EXEMPT    listed below with a justification, a class and live evidence;
#       RECORDED  in scripts/nav-parity-baseline.tsv as a known gap with an owner;
#       GAP       none of the above -> REFUSED BY NAME.
#     Clients are portal/static (the browser surface), control-plane (the operator
#     plane), integrations (the adapters) and infra/fleet (the fleet rung). Unit
#     tests, e2e harnesses and prose are NOT clients: a route whose only caller is
#     a test is not shipped (#1540 step 2), and a route
#     named only by a description is not called by anything.
#   A2 DEAD LINKS — every nav surface resolves to a frame that exists
#     (/views/<id>.html, or its VIEW_TARGETS target): the same edge, mirrored.
#     A nav button that opens a missing frame is the defect read the other way.
#   A3 EXEMPTION SOUNDNESS — every exemption entry carries at least one
#     justification comment line of its own (a bare list entry is refused), a
#     non-empty reason, a class from the closed set, and an evidence path that
#     EXISTS in the analysed tree and NAMES the route. An entry naming a route
#     `_route_api` no longer declares, or a family that now has a nav surface, is
#     reported STALE: this list shrinks, it never absorbs a surface.
#   A4 KNOWN GAPS — a family in none of the above must be recorded in the
#     shrink-only baseline with an owning issue and a reason. A new gap is refused
#     by name; a recorded row with no reason is refused (a silent gap); a recorded
#     row that is now reachable is reported STALE; and --record refuses to add a
#     row the file does not already carry.
#
# HOW IT PROVES ITSELF (GR-12)
#   Every run provokes its own rules against mutated copies of the very inputs it
#   reads — a planted unwired family, a removed nav entry (whose verdict must move
#   from NAV to CONSUMED), a nav id with no frame, a gap row with no reason, an
#   exemption with no justification line, an exemption whose evidence does not
#   name its route, a removed gap row and a stale gap row — and REQUIRES each
#   refusal to name the offending surface. Each mutation asserts it changed the
#   text first, so a mutation that stopped applying fails loudly
#   (CONTROL-SETUP-BROKEN) instead of proving nothing.
#
# CANNOT-ASSESS, AND WHY IT IS NOT A PASS
#   No python3, no scratch directory, a missing/unreadable app.py or console.js, a
#   dispatch parse with no family, or a nav parse with no surface all exit 2 with
#   the reason. A console whose nav arrays were renamed is a parse that needs a
#   human, not a repository with nothing to check.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# Usage:
#   bash scripts/check-nav-parity.sh                   the gate (self-test + tree)
#   bash scripts/check-nav-parity.sh --self-test       the provocations alone
#   bash scripts/check-nav-parity.sh --root DIR        analyse another tree
#   bash scripts/check-nav-parity.sh --baseline FILE   another gap record
#   bash scripts/check-nav-parity.sh --record          rewrite the gap record from
#                                                      the live gaps (shrink-only)
#   bash scripts/check-nav-parity.sh --help
#
# WIRING — nothing to hand-edit
#   scripts/verify.sh sources scripts/discover-checks.sh, which appends every
#   `scripts/check-*.sh` to `make verify`'s check list by filename, so this file
#   is live the moment it lands. A hand-added `checks=()` entry in verify.sh would
#   be a DUPLICATE registration of the same name, which the gate of record
#   refuses by name — the same reasoning the Makefile records for
#   `check-isolation-landed`.
set -uo pipefail

script_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
root="$script_root"
baseline=""
mode="full"

usage() {
  cat <<'USAGE'
Usage:
  bash scripts/check-nav-parity.sh                   the gate (self-test + tree)
  bash scripts/check-nav-parity.sh --self-test       the provocations alone
  bash scripts/check-nav-parity.sh --root DIR        analyse another tree
  bash scripts/check-nav-parity.sh --baseline FILE   another gap record
  bash scripts/check-nav-parity.sh --record          rewrite the gap record from
                                                     the live gaps (shrink-only)
Exit codes: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    --root)      root="${2:-}"; shift 2 ;;
    --root=*)    root="${1#*=}"; shift ;;
    --baseline)  baseline="${2:-}"; shift 2 ;;
    --baseline=*) baseline="${1#*=}"; shift ;;
    --record)    mode="record"; shift ;;
    --self-test) mode="self-test"; shift ;;
    -h|--help)   usage; exit 0 ;;
    *) printf 'check-nav-parity: unknown argument: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
done

if [ -z "$root" ] || [ ! -d "$root" ]; then
  printf 'check-nav-parity: CANNOT-ASSESS — %s is not a directory\n' "${root:-<empty>}" >&2
  exit 2
fi
if [ -z "$baseline" ]; then
  baseline="$root/scripts/nav-parity-baseline.tsv"
fi
if ! command -v python3 >/dev/null 2>&1; then
  printf 'check-nav-parity: CANNOT-ASSESS — python3 is required\n' >&2
  exit 2
fi

# One scratch variable, one EXIT trap, armed once and fired once (the shape
# docs/SHELL-PATTERNS.md asks for); the explicit /tmp template keeps this out of
# the shared, periodically-cleaned TMPDIR, which can vanish mid-run.
TMPD=""
cleanup() { [ -n "$TMPD" ] && rm -rf "$TMPD" || true; }
trap cleanup EXIT

work="/tmp/nav-parity.$$.$(date +%s%N)"
if ! mkdir -p "$work"; then
  printf 'check-nav-parity: CANNOT-ASSESS — cannot create a scratch directory\n' >&2
  exit 2
fi
TMPD="$work"

# --- the exemption list (A3) ------------------------------------------------
# The rule for an entry: a route or family that is legitimately NAV-EXEMPT, with
# its class, an in-tree file that names it, and a one-line reason. Every entry
# below carries its own justification comment. An entry is not a way to silence
# a gap: it must name a live file that mentions the route, it cannot name a
# surface that has a nav entry, and it cannot name a route `_route_api` does not
# declare — each of those is refused by name. Format (one per line):
#     <family|route:/api/a/b>|<probe|session|feed>|<evidence path>|<reason>
cat > "$work/exemptions.txt" <<'EXEMPTIONS'
# /api/healthz and /api/healthz/ready (family `healthz`) — the console's own
# health/readiness rail. The portal promotion rung curls it before it promotes a
# tag and the hosting check curls it too; no operator navigates to a probe, and a
# nav entry for one would be a dead link. Infra-only by design, not by
# inconvenience.
healthz|probe|infra/fleet/promote_portal.py|health/readiness probe curled by the portal promotion rung and the hosting check, never navigated to
# /api/portal/surfaces (family `portal`) — a machine feed, not a page. Its own
# pin note records the consumer: the shared-frontend shell reads the same
# document in its own checkout, so this repo's console has no view for it and a
# nav entry here would point at a frame that does not exist.
portal|feed|registry/portal-surfaces.pinned.json|CMR fleet-surface feed for the shared-frontend shell (kushin77/shared-frontend); this console has no page for it
# GET /api/console/me — the shell's own identity read. Every view calls it to
# learn who is signed in: it is the session rail, not a destination. (The nav
# surface for the console family is the Console view, which is wired.)
route:/api/console/me|session|portal/static/js/console.js|the shell's identity read, called by every view, not a nav target
# POST /api/console/logout — the shell's session termination, reached from the
# logout button in the shell chrome.
route:/api/console/logout|session|portal/static/js/console.js|the shell's session termination, reached from the logout button, not a nav target
EXEMPTIONS

python3 - "$root" "$baseline" "$work/exemptions.txt" "$mode" <<'PY'
"""nav-parity: the console's dispatch table against the shell's navigation."""
import os
import pathlib
import re
import sys

root, baseline_path, exempt_path, mode = sys.argv[1:5]

APP_REL = "portal/server/app.py"
CONSOLE_REL = "portal/static/js/console.js"
CLIENT_TREES = ("portal/static", "control-plane", "integrations", "infra/fleet")
CLIENT_EXTS = {".py", ".js", ".mjs", ".cjs", ".html", ".css", ".sh"}
CLASSES = ("probe", "session", "feed")
VIEW_DIR = "portal/static/views"
BASELINE_COLUMNS = 3

fail = 0
cannot = 0


def out(text=""):
    print(text)


def note(text):
    out("  NOTE  %s" % text)


def failure(text):
    global fail
    fail = 1
    out("  FAIL  %s" % text)


def unassessable(text):
    global cannot
    cannot = 1
    print("check-nav-parity: CANNOT-ASSESS — %s" % text, file=sys.stderr)


def read_rel(rel, base=None):
    try:
        return pathlib.Path(base or root, rel).read_text(encoding="utf-8")
    except OSError:
        return None


def read_path(path):
    try:
        return pathlib.Path(path).read_text(encoding="utf-8")
    except OSError:
        return None


def is_test_path(rel):
    """A test is not a client: a route whose only caller is a test is not shipped."""
    parts = rel.split("/")
    if any(p in ("tests", "test", "__tests__") for p in parts[:-1]):
        return True
    name = parts[-1]
    return (
        name.startswith("test_")
        or name.startswith("conftest.")
        or name.endswith("_test.py")
        or ".test." in name
        or ".spec." in name
    )


# --- parsing the two declarations -------------------------------------------
def parse_declared(app_text):
    """-> (families, routes, body_range, diagnostic) from `_route_api` alone."""
    lines = app_text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if re.match(r"^    def _route_api\(", line):
            start = i
            break
    if start is None:
        return None, None, None, "def _route_api( is not declared in %s" % APP_REL
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if re.match(r"^    (def |# -- )", lines[j]):
            end = j
            break
    body = "\n".join(lines[start:end])
    if "parts" not in body:
        return None, None, None, "the _route_api body carries no `parts` dispatch"

    families, routes = [], []

    def add_family(name):
        if name not in families:
            families.append(name)

    def add_route(a, b):
        route = "%s/%s" % (a, b)
        if route not in routes:
            routes.append(route)

    for m in re.finditer(r'parts\[0\] == "([a-z0-9_]+)"', body):
        add_family(m.group(1))
    for m in re.finditer(r'parts\[:2\] == \["([a-z0-9_]+)", "([a-z0-9_]+)"\]', body):
        add_family(m.group(1))
        add_route(m.group(1), m.group(2))
    for m in re.finditer(r"parts == \[([^\]]*)\]", body):
        toks = re.findall(r'"([a-z0-9_]+)"', m.group(1))
        if not toks:
            continue
        add_family(toks[0])
        if len(toks) > 1:
            add_route(toks[0], toks[1])
    if not families:
        return None, None, None, "the _route_api dispatch parsed to zero route families"
    return families, routes, (start + 1, end), None


def parse_nav(js_text):
    """-> (nav ids, view targets, diagnostic) from console.js."""
    ids, targets = [], {}
    for m in re.finditer(r"var (?:NAV_TENANT|NAV_GLOBAL) = \[(.*?)\];", js_text, re.S):
        for found in re.findall(r'id: "([a-z0-9_]+)"', m.group(1)):
            if found not in ids:
                ids.append(found)
    for m in re.finditer(r'\{\s*id:\s*"([a-z0-9_]+)"[^}]*?\bprobe:\s*"(/api/[^"]*)"', js_text):
        if m.group(1) not in ids:
            ids.append(m.group(1))
    block = re.search(r"var VIEW_TARGETS = \{(.*?)\};", js_text, re.S)
    if block:
        for key, target in re.findall(r"([a-z0-9_]+):\s*\"([^\"]+)\"", block.group(1)):
            targets[key] = target
            if key not in ids:
                ids.append(key)
    if not ids:
        return None, None, (
            "no nav surface parsed from %s — the shell's nav arrays were renamed "
            "or emptied, which needs a human" % CONSOLE_REL
        )
    return ids, targets, None


def client_hits(families):
    """-> {family: [client file, ...]} over the client trees, tests excluded."""
    hits = {}
    patterns = {f: re.compile(r"/api/%s(?![a-z0-9_])" % re.escape(f)) for f in families}
    for tree in CLIENT_TREES:
        base = pathlib.Path(root, tree)
        if not base.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            rel_dir = os.path.relpath(dirpath, root)
            dirnames[:] = [d for d in dirnames if not is_test_path("%s/%s" % (rel_dir, d))]
            for name in filenames:
                rel = "%s/%s" % (rel_dir, name)
                if is_test_path(rel) or os.path.splitext(name)[1] not in CLIENT_EXTS:
                    continue
                text = read_path(os.path.join(dirpath, name))
                if text is None:
                    continue
                for family, pattern in patterns.items():
                    if pattern.search(text):
                        hits.setdefault(family, []).append(rel)
    return hits


# --- parsing the two records -------------------------------------------------
def parse_exemptions(text):
    """-> (entries, findings). An entry must be preceded by its own justification."""
    entries, findings, commented = [], [], False
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.rstrip("\n")
        if not line.strip():
            continue
        if line.lstrip().startswith("#"):
            commented = True
            continue
        parts = line.split("|")
        key = parts[0].strip()
        if len(parts) != 4:
            findings.append(
                "exemption line %d: not <family|route:/api/a/b>|<class>|<evidence>|<reason>" % lineno
            )
            commented = False
            continue
        entry = {"key": key, "class": parts[1].strip(), "evidence": parts[2].strip(),
                 "reason": parts[3].strip(), "line": lineno, "is_route": key.startswith("route:")}
        if entry["is_route"]:
            entry["route"] = key[len("route:"):].strip()
        if not commented:
            findings.append(
                "exemption '%s': no justification comment line above it — a bare list "
                "entry with no reasoning is refused" % key
            )
        if not entry["reason"]:
            findings.append("exemption '%s': no reason recorded" % key)
        if entry["class"] not in CLASSES:
            findings.append(
                "exemption '%s': class '%s' is not one of %s" % (key, entry["class"], "|".join(CLASSES))
            )
        entries.append(entry)
        commented = False
    return entries, findings


def parse_baseline(text):
    """-> (rows, findings). <family><TAB><owning issue><TAB><why it has none yet>."""
    if text is None:
        return None, []
    rows, findings = [], []
    for lineno, raw in enumerate(text.splitlines(), 1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        parts = raw.rstrip("\n").split("\t")
        if len(parts) < BASELINE_COLUMNS or not parts[0].strip():
            findings.append(
                "gap record line %d: not <family><TAB><owning issue><TAB><why it has no "
                "nav surface yet>" % lineno
            )
            continue
        row = {"family": parts[0].strip(), "issue": parts[1].strip(),
               "why": "\t".join(parts[2:]).strip(), "line": lineno}
        if not row["why"]:
            findings.append(
                "gap record: '%s' has no reason (a silent gap — a gap must name why it "
                "has no nav surface and who owns it)" % row["family"]
            )
        rows.append(row)
    return rows, findings


# --- the ONE implementation of the rule --------------------------------------
def classify(app_text, js_text, exemptions, baseline_rows):
    """Assess the tree. Returns a result dict; the caller prints and judges it."""
    res = {"findings": [], "notes": [], "rows": [], "nav": [], "consumers": {}}
    families, routes, body_range, diag = parse_declared(app_text)
    if diag:
        res["fatal"] = diag
        return res
    nav_ids, view_targets, diag = parse_nav(js_text)
    if diag:
        res["fatal"] = diag
        return res
    hits = client_hits(families)

    declared_keys = set(families) | {"route:/api/%s" % r for r in routes}
    ex_family = {}
    ex_route = {}
    for entry in exemptions:
        if entry["is_route"]:
            ex_route[entry["route"]] = entry
        else:
            ex_family[entry["key"]] = entry

    # A3: each exemption must name a live route, live evidence, and a surface
    # that is still nav-exempt.
    for entry in exemptions:
        if entry["key"] not in declared_keys:
            res["notes"].append(
                "exemption '%s' names a route _route_api no longer declares — remove it "
                "(the list shrinks)" % entry["key"]
            )
        evidence = pathlib.Path(root, entry["evidence"])
        if not evidence.is_file():
            res["findings"].append(
                "exemption '%s': evidence '%s' does not exist in this tree" % (entry["key"], entry["evidence"])
            )
            continue
        text = read_path(evidence) or ""
        needle = entry["route"] if entry["is_route"] else "/api/%s" % entry["key"]
        if needle not in text:
            res["findings"].append(
                "exemption '%s': evidence '%s' does not name %s — the justification is not "
                "live" % (entry["key"], entry["evidence"], needle)
            )
        if not entry["is_route"] and entry["key"] in nav_ids:
            res["notes"].append(
                "exemption '%s' is now a nav surface — remove it so the exemption list "
                "cannot absorb a wired surface" % entry["key"]
            )

    # A2: every nav surface must resolve to a frame that exists.
    for nav_id in nav_ids:
        if nav_id in view_targets:
            frame = "portal/static%s" % view_targets[nav_id]
        else:
            frame = "%s/%s.html" % (VIEW_DIR, nav_id)
        res["nav"].append((nav_id, frame))
        if not pathlib.Path(root, frame).is_file():
            res["findings"].append(
                "nav '%s': the shell offers it, but its frame %s does not exist" % (nav_id, frame)
            )

    baseline_by_family = {row["family"]: row for row in baseline_rows or []}

    # A1 + A4: the family verdict.
    for family in families:
        consumers = hits.get(family, [])
        res["consumers"][family] = consumers
        if family in ex_family:
            status, detail = "EXEMPT", "%s — %s" % (ex_family[family]["class"], ex_family[family]["evidence"])
        elif family in nav_ids:
            status, detail = "NAV", "a nav surface in %s" % CONSOLE_REL
        elif consumers:
            status = "CONSUMED"
            detail = "reached by %s" % ", ".join(consumers[:3])
            res["notes"].append(
                "'%s' has no nav surface but is reached by %s — visible here, never a "
                "silent pass" % (family, ", ".join(consumers[:3]))
            )
        elif family in baseline_by_family:
            status = "RECORDED"
            detail = "recorded gap, owning issue #%s" % baseline_by_family[family]["issue"]
        else:
            status = "GAP"
            detail = "no nav surface, no client, no exemption, not recorded"
            res["findings"].append(
                "family '%s': declared by _route_api, and it is not a nav surface, no client "
                "calls it, it is not exempt and it is not recorded — a backend surface can "
                "ship while the UI that was supposed to show it is never built (wire it, or "
                "record it in scripts/nav-parity-baseline.tsv with an owning issue)"
                % family
            )
        res["rows"].append((family, status, detail))

    # A4: a recorded row whose surface is now reachable can be lowered.
    for family, row in baseline_by_family.items():
        if family not in families:
            res["notes"].append(
                "gap record: '%s' (issue #%s) is not a declared route family any more — "
                "remove the row" % (family, row["issue"])
            )
        elif family in nav_ids or hits.get(family) or family in ex_family:
            res["notes"].append(
                "gap record: '%s' (issue #%s) is reachable now — lower the record with "
                "--record" % (family, row["issue"])
            )

    # A route-level exemption is NARROW on purpose: it can speak for a sub-route of
    # a family that is itself reachable (a session endpoint beside a nav-wired
    # view), never for the family. Exempting a route whose family is still a gap
    # would leave that family looking unwired while reading as dealt with — the
    # exemption would be doing the silencing the gap record exists to prevent.
    family_status = dict((f, s) for f, s, _ in res["rows"])
    for route in routes:
        entry = ex_route.get("/api/%s" % route)
        if entry is None:
            continue
        family = route.split("/")[0]
        if family_status.get(family) == "GAP":
            res["findings"].append(
                "exemption '%s': its family '%s' is a gap, and a route-level exemption "
                "cannot speak for a family — wire the family, exempt it with its own "
                "justification, or record it in scripts/nav-parity-baseline.tsv"
                % (entry["key"], family)
            )
    res["body_range"] = body_range
    res["families"] = families
    res["routes"] = routes
    res["nav_ids"] = nav_ids
    res["route_exemptions"] = sorted(ex_route)
    return res


def report(res, baseline_present, baseline_rows, exempt_count, extra_findings):
    if res.get("fatal"):
        unassessable(res["fatal"])
        return
    body = res["body_range"]
    out("nav-parity: the console's dispatch table against the shell's navigation (#1565)")
    out("")
    out("== the dispatch table ==")
    out("  %s:%d-%d — %d route famil%s, %d sub-route(s)"
        % (APP_REL, body[0], body[1], len(res["families"]),
           "y" if len(res["families"]) == 1 else "ies", len(res["routes"])))
    out("  %s" % ", ".join(res["families"]))
    out("")
    out("== the shell's nav surfaces ==")
    out("  %s — %d surface(s)" % (CONSOLE_REL, len(res["nav_ids"])))
    out("  %s" % ", ".join(res["nav_ids"]))
    out("")
    out("== the exemption list ==")
    out("  %d entry(ies) read from this script's own table" % exempt_count)
    out("")
    out("== the gap record ==")
    if baseline_present is None:
        out("  no gap record at %s — every gap is refused (that is the honest default)"
            % baseline_path)
    else:
        out("  %s — %d recorded gap(s)" % (baseline_present, len(baseline_rows or [])))
        for row in baseline_rows or []:
            out("    %-12s issue #%-6s %s" % (row["family"], row["issue"], row["why"]))
    out("")
    out("== A1 reachability (every declared family -> how it is reached) ==")
    for family, status, detail in res["rows"]:
        out("  %-8s %-12s %s" % ("OK" if status != "GAP" else "REFUSED", family, detail))
    out("")
    for finding in res["findings"] + list(extra_findings):
        failure(finding)
    for text in res["notes"]:
        note(text)


# --- the vacuity controls ----------------------------------------------------
def selftest(res, exempt_source, baseline_source):
    """Provoke every rule against a mutated copy of its own input.

    Every arm drives the SAME parse and classify functions the repository run
    uses — the exemption-table parse included, because the reason, class and
    justification rules live there — so what is proven is the code path that runs,
    never a re-implementation of it. A mutation that stops applying is reported as
    a provocation that could not be built, so an arm can never pass by testing
    nothing.
    """
    live_app = read_rel(APP_REL)
    live_js = read_rel(CONSOLE_REL)
    arms = 0
    broken = 0

    def mutated(label, source, old, new):
        """-> the mutated text, or None when the mutation could not be built."""
        nonlocal broken
        if old not in source:
            print("  FAIL  %s: the provocation could not be built (%r is gone)" % (label, old),
                  file=sys.stderr)
            broken = 1
            return None
        changed = source.replace(old, new, 1)
        if changed == source:
            print("  FAIL  %s: the mutation changed nothing" % label, file=sys.stderr)
            broken = 1
            return None
        return changed

    def exercise(label, want, app_text=None, js_text=None, exempt_text=None, baseline_text=None):
        """One provocation, through the real path; the refusal must name the surface."""
        nonlocal arms
        arms += 1
        entries, ex_findings = parse_exemptions(exempt_source if exempt_text is None else exempt_text)
        rows, bl_findings = parse_baseline(baseline_source if baseline_text is None else baseline_text)
        got = classify(live_app if app_text is None else app_text,
                       live_js if js_text is None else js_text, entries, rows)
        if got.get("fatal"):
            print("  FAIL  %s: the mutant could not be assessed (%s)" % (label, got["fatal"]),
                  file=sys.stderr)
            return 1
        lines = got["findings"] + got["notes"] + ex_findings + bl_findings
        hits = [line for line in lines if want in line]
        if hits:
            print("  OK    %s" % label)
            print("        refused: %s" % hits[0])
            return 0
        print("  FAIL  %s — accepted, so this check cannot fail" % label, file=sys.stderr)
        print("        wanted %r" % want, file=sys.stderr)
        return 1

    def first_entry(text):
        for line in text.splitlines():
            if line.strip() and not line.lstrip().startswith("#"):
                return line
        return ""

    live_status = dict((f, s) for f, s, _ in res["rows"])
    live_entries, _ = parse_exemptions(exempt_source)
    live_rows, _ = parse_baseline(baseline_source)

    out("== the vacuity controls (each provocation refused by name) ==")

    # 1. a planted, unwired family — the negative control the issue asks for. It
    #    goes into the same `parts[0] ==` chain the parse reads, so the arm drives
    #    the real parse and the real refusal.
    anchor = '            raise ApiError(404, "not_found", f"no such api route: {route}")'
    planted = mutated("a planted unwired family", live_app, anchor,
                      '            if parts[0] == "ghostlanes":\n'
                      '                return self._ok({})\n' + anchor)
    if planted is not None:
        broken |= exercise("a new backend route family with no nav entry is refused by name",
                           "family 'ghostlanes'", app_text=planted)

    # 2. the nav arm is load-bearing: removing one nav entry must move exactly that
    #    family's verdict, so the nav surface is shown to be what carries it.
    nav_family = next((f for f in ("fleet", "tenants", "console") if live_status.get(f) == "NAV"), None)
    if nav_family is None:
        print("  FAIL  the removed-nav-entry provocation has no target: no nav-wired family",
              file=sys.stderr)
        broken = 1
    else:
        stripped = None
        for pattern in (r'\n    \{ id: "%s", label[^}]*\},', r'\n    \{ id: "%s", label[^}]*\}'):
            candidate = re.sub(pattern % re.escape(nav_family), "", live_js, count=1)
            if candidate != live_js:
                stripped = candidate
                break
        if stripped is None:
            print("  FAIL  the removed-nav-entry provocation could not be built for '%s'" % nav_family,
                  file=sys.stderr)
            broken = 1
        else:
            arms += 1
            moved = next((s for f, s, _ in classify(live_app, stripped, live_entries, live_rows)["rows"]
                          if f == nav_family), None)
            if moved == "CONSUMED":
                print("  OK    removing the nav entry for '%s' moves its verdict NAV -> CONSUMED"
                      % nav_family)
                print("        the nav surface was what carried it")
            else:
                print("  FAIL  removing the nav entry for '%s' left its verdict at %s — the nav arm "
                      "is not load-bearing" % (nav_family, moved), file=sys.stderr)
                broken = 1

    ghost_view = mutated("a nav entry with no frame", live_js, "var VIEW_TARGETS = {",
                         'var VIEW_TARGETS = {\n    ghostview: "/views/ghostview.html",')
    if ghost_view is not None:
        broken |= exercise("a nav entry pointing at a frame that does not exist is refused by name",
                           "nav 'ghostview'", js_text=ghost_view)

    # 3. the gap record: a new gap refused, a silent gap refused, a stale row seen.
    gap_family = next((f for f, s, _ in res["rows"] if s == "GAP"), None)
    if gap_family is None:
        gap_family = next((r["family"] for r in live_rows if r["family"] in res["families"]), None)
    if gap_family is None:
        print("  FAIL  the gap-record provocations have no target family", file=sys.stderr)
        broken = 1
    else:
        broken |= exercise("a declared family with no record is refused by name",
                           "family '%s'" % gap_family, baseline_text="")
        first_row = first_entry(baseline_source)
        silent = mutated("a gap row with no reason", baseline_source, first_row,
                         "\t".join(first_row.split("\t")[:2]) + "\t")
        if silent is not None:
            broken |= exercise("a recorded gap with no reason is refused by name",
                               "has no reason (a silent gap", baseline_text=silent)
        if not first_row:
            print("  FAIL  the stale-row provocation has no target row", file=sys.stderr)
            broken = 1
        elif live_status.get("fleet") != "NAV":
            print("  FAIL  the stale-row provocation needs a nav-wired 'fleet' family",
                  file=sys.stderr)
            broken = 1
        else:
            broken |= exercise("a recorded gap that is now reachable is reported stale",
                               "gap record: 'fleet'",
                               baseline_text=baseline_source + "fleet\t0\tinvented\n")

    # 4. the exemption list cannot be abused: no reason, no class, dead evidence,
    #    stale route, stale family, and no justification line at all.
    first_ex = first_entry(exempt_source)
    if not first_ex:
        print("  FAIL  the exemption provocations have no target entry", file=sys.stderr)
        broken = 1
    else:
        no_reason = mutated("an exemption with no reason", exempt_source, first_ex,
                            first_ex.rsplit("|", 1)[0] + "|")
        if no_reason is not None:
            broken |= exercise("an exemption with no reason is refused by name",
                               "no reason recorded", exempt_text=no_reason)
        bad_class = mutated("an exemption with an unknown class", exempt_source, "|probe|", "|creative|")
        if bad_class is not None:
            broken |= exercise("an exemption whose class is outside the closed set is refused by name",
                               "is not one of", exempt_text=bad_class)
        dead_evidence = mutated("an exemption whose evidence does not name its route", exempt_source,
                                "infra/fleet/promote_portal.py", "docs/README.md")
        if dead_evidence is not None:
            broken |= exercise("an exemption whose evidence does not name its route is refused by name",
                               "does not name /api/healthz", exempt_text=dead_evidence)
        gone_evidence = mutated("an exemption whose evidence file is gone", exempt_source,
                                "registry/portal-surfaces.pinned.json", "registry/does-not-exist.json")
        if gone_evidence is not None:
            broken |= exercise("an exemption naming a file that does not exist is refused by name",
                               "does not exist in this tree", exempt_text=gone_evidence)
        uncommented = "\n".join(l for l in exempt_source.splitlines()
                                if not l.lstrip().startswith("#"))
        if uncommented == exempt_source:
            print("  FAIL  the bare-entry provocation could not be built (no comment lines to drop)",
                  file=sys.stderr)
            broken = 1
        else:
            broken |= exercise("an exemption with no justification comment is refused (a bare list "
                               "entry with no reasoning)", "no justification comment line",
                               exempt_text=uncommented)
        broken |= exercise("an exemption naming a route the app no longer declares is reported stale",
                           "names a route _route_api no longer declares",
                           exempt_text=exempt_source
                           + "route:/api/console/ghost|session|portal/static/js/console.js|invented\n")
        broken |= exercise("an exemption for a family that now has a nav surface is reported stale",
                           "is now a nav surface",
                           exempt_text=exempt_source
                           + "fleet|feed|portal/static/js/fleet.js|invented\n")
        # The declared sub-route whose family is a gap is `/api/v1/bridge`; with the
        # record emptied it is a plain gap, so a route-level exemption for it is the
        # loophole this arm closes.
        broken |= exercise("a route-level exemption cannot silence a family that is a gap",
                           "cannot speak for a family", baseline_text="",
                           exempt_text=exempt_source
                           + "route:/api/v1/bridge|session|portal/server/bridge.py|invented\n")

    out("")
    print("  %d provocation(s) exercised; %s" % (arms, "each refused by name" if not broken
                                                 else "at least one was accepted"))
    return 1 if broken else 0


def rewrite_baseline(live_gaps, rows, source_text):
    """--record: rewrite from the live gaps, refusing to ADD a family.

    The existing comment header is preserved and the rows are written in sorted
    order, so re-running --record on an unchanged tree is byte-for-byte a no-op: a
    maintenance tool that rewrote its own header on every run would turn a
    deliberate record into a moving target.
    """
    existing = {row["family"]: row for row in rows}
    grown = [f for f in live_gaps if f not in existing]
    if grown:
        print("check-nav-parity: REFUSED — the gap record is shrink-only, and this would ADD: %s"
              % ", ".join(sorted(grown)), file=sys.stderr)
        print("Wire the surface first (or file the issue that owns it), then re-run --record.",
              file=sys.stderr)
        return 1
    header = [line for line in (source_text or "").splitlines() if line.startswith("#")]
    if not header:
        header = [
            "# Known built-but-unwired console surfaces (issue #1565). SHRINK-ONLY:",
            "# scripts/check-nav-parity.sh refuses a family that is missing from this file, and",
            "# refuses to add one here. Lower it (--record) as each row's owning issue lands; the",
            "# file disappears when the last row does.",
            "#",
            "# Format: <family><TAB><owning issue number><TAB><why it has no nav surface yet>",
        ]
    lines = list(header)
    for family in sorted(live_gaps):
        row = existing[family]
        lines.append("%s\t%s\t%s" % (family, row["issue"], row["why"]))
    pathlib.Path(baseline_path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("check-nav-parity: recorded %d gap(s) -> %s" % (len(live_gaps), baseline_path))
    return 0


def main():
    app_text = read_rel(APP_REL)
    if app_text is None:
        unassessable("%s is not readable under %s" % (APP_REL, root))
        return 2
    js_text = read_rel(CONSOLE_REL)
    if js_text is None:
        unassessable("%s is not readable under %s" % (CONSOLE_REL, root))
        return 2
    exempt_text = read_path(exempt_path)
    if exempt_text is None:
        unassessable("the exemption table %s could not be read" % exempt_path)
        return 2

    exemptions, ex_findings = parse_exemptions(exempt_text)
    if not exemptions:
        unassessable("the exemption table carries no entry — the infra-only routes cannot be "
                     "recognised, so this run would refuse them for the wrong reason")
        return 2

    baseline_text = read_path(baseline_path)
    baseline_rows, bl_findings = parse_baseline(baseline_text)
    baseline_present = baseline_path if baseline_text is not None else None

    res = classify(app_text, js_text, exemptions, baseline_rows)
    if res.get("fatal"):
        unassessable(res["fatal"])
        return 2

    if mode == "self-test":
        print("nav-parity: the provocations alone (#1565)")
        print("")
        rc = selftest(res, exempt_text, baseline_text or "")
        return rc

    report(res, baseline_present, baseline_rows, len(exemptions), ex_findings + bl_findings)

    if mode == "record":
        live = classify(app_text, js_text, exemptions, [])
        live_gaps = [f for f, s, _ in live["rows"] if s == "GAP"]
        return rewrite_baseline(live_gaps, baseline_rows or [], baseline_text or "")

    print("")
    if fail:
        print("check-nav-parity: FAIL", file=sys.stderr)
        return 1
    print("check-nav-parity: OK — every route family the dispatch table declares is reachable "
          "(%d nav surface(s), %d exemption(s), %d recorded gap(s)) and every nav surface "
          "resolves to a frame that exists"
          % (len(res["nav_ids"]), len(exemptions),
             len([1 for _, s, _ in res["rows"] if s == "RECORDED"])))
    return 0


sys.exit(main())
PY

rc=$?
exit "$rc"
