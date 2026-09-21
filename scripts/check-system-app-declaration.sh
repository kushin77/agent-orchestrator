#!/usr/bin/env bash
# check-system-app-declaration.sh — this repo's OS-app/addon exemption is
# DECLARED, and the declaration is measured against the tree (issue #945).
#
# THE DEFECT THIS EXISTS FOR
#   This repository carries no `addons/`, no `apps/` and no `category: "system"`
#   app anywhere outside `vendor/`. That absence is very likely CORRECT BY
#   DESIGN — the OS app/addon model (`addons/<id>/`, `mount.type: native`,
#   `category: "system"` apps) belongs to the OS portal host, not to this
#   service module — but nothing said so. A reader (or an agent) asking "where
#   is the system-app structure?" got the same empty result for "by design" and
#   for "forgotten", and there was no statement of how the surface this epic is
#   about (the fleet SPoG) becomes reachable in the OS at all. An undeclared
#   exemption is not an exemption: it is an absence.
#
# WHAT IS CHECKED (all offline, deterministic, no network)
#   1. `module.json` carries the `os_apps` declaration: `hosts` is exactly
#      `false`, `model_owner` is a non-empty repo string that is NOT this module
#      itself (a module cannot be its own app model), and `declaration` names a
#      file that exists.
#   2. The declaration document carries the machine-readable
#      `system-app-declaration` marker and its values AGREE with `module.json`.
#      The value is deliberately asserted in two places and the gate is what
#      proves they cannot drift: `module.json` is read by tooling, the doc is
#      read by people, and either one alone rots.
#   3. The tree still matches the claim: no `addons/` or `apps/` directory, and
#      no `category: "system"` app declaration, anywhere outside the excluded
#      trees — in ANY of the DECLARED shapes (`SCANNED_SUFFIXES`: `.json`
#      `.jsonc` `.json5` `.yaml` `.yml` `.toml` `.ts` `.tsx` `.js` `.jsx`
#      `.mjs` `.cjs`), each matched with the declaration shape its file type can
#      express. If the structure appears, the exemption no longer holds and the
#      gate refuses BY NAME, so a lane that lands an app here must update the
#      declaration instead of leaving the doc lying.
#      The breadth is DECLARED, and it is the same list in three places — this
#      tuple, the `OK` line, and `docs/MODULE-ADMISSION.md` §9 — because a gate
#      that reads only three extensions while printing "the tree matches the
#      claim" is blind to every other shape (issue #1161). That is not a
#      hypothetical shape: the app model this exemption names is owned by
#      `kushin77/shared-frontend`, which registers its apps in
#      `shell/src/addons.ts` — a `.ts` file.
#   4. The SPoG route the declaration names RESOLVES: the declared view file
#      exists, the declared route ends in that view's basename, and the declared
#      surface flag is really declared in `infra/feature-flags/registry.yaml`.
#      A declaration naming a surface nobody serves is a claim, not a fact.
#   5. `docs/ARCHITECTURE.md` points a reader at both the model owner and the
#      contract, so the answer is where an architect looks first.
#
# HOW IT PROVES ITSELF (a control that cannot fail is a formality)
#   On every run it stages scratch copies of the SAME inputs the run just read
#   and provokes each refusal against a mutated copy, on the same code path:
#   (a) the `os_apps` block deleted, (b) `hosts` flipped to true, (c) the marker
#   deleted from the document, (d) the document's owner made to disagree with
#   `module.json`, (e) an `addons/` directory added, (f) a `category: "system"`
#   app planted in EACH declared suffix — so a suffix dropped from the allowlist
#   turns its own control red instead of silently narrowing the observation
#   again — and (g) the negative half: prose ABOUT the declaration, and a
#   commented-out app block, must NOT be refused, so the widened scan cannot
#   pass by matching everything. Each MUST be rc 1 AND name what it refused.
#   The unmutated staged copy must produce the SAME rc as the real root — so
#   the controls cannot pass by the gate refusing everything.
#   `--no-controls` skips them, for driving the check against a deliberately
#   damaged scratch tree (the external provocation).
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS. CANNOT-ASSESS is an
# unreadable subject (a missing or unparseable input), never a pass.
#
# Usage: bash scripts/check-system-app-declaration.sh [--root DIR] [--no-controls]
#
# ---knowledge---
# module_id: scripts.check-system-app-declaration
# system: governance
# app: gates
# solution_class: enterprise
# patterns: [tri-state-exit, provoked-negative-control, no-false-green, offline-hermetic, declared-authority, lane-isolation, deterministic]
# derives_from: null
# owner_sme: qa-sme
# tier: L1
# interfaces: [exit 0 OK, exit 1 NOT-OK, exit 2 CANNOT-ASSESS]
# invariants: ""
# gotchas: ""
# related: ["#945", "#1161"]
# do_not_duplicate: null
# ---knowledge---
set -uo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)" || exit 2
controls="controls"

while [ "$#" -gt 0 ]; do
  case "$1" in
    --root) root="${2:?--root needs a directory}"; shift 2 ;;
    --no-controls) controls="no-controls"; shift ;;
    *) printf 'check-system-app-declaration: unknown argument: %s\n' "$1" >&2; exit 2 ;;
  esac
done

if [ ! -d "$root" ]; then
  printf 'check-system-app-declaration: CANNOT-ASSESS — %s is not a directory\n' "$root" >&2
  exit 2
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-system-app-declaration: CANNOT-ASSESS — python3 is not on PATH" >&2
  exit 2
fi

python3 - "$root" "$controls" <<'PY'
import json
import os
import re
import shutil
import sys
import tempfile

ROOT, RUN_CONTROLS = sys.argv[1], sys.argv[2] == "controls"

MANIFEST = "module.json"
DOC = "docs/MODULE-ADMISSION.md"
ARCH = "docs/ARCHITECTURE.md"
FLAGS = "infra/feature-flags/registry.yaml"
INPUTS = (MANIFEST, DOC, ARCH, FLAGS)

MARKER_TAG = "system-app-declaration"
MARKER_KEYS = (
    "hosts_os_apps",
    "app_model_owner",
    "app_model_contract",
    "spog_view",
    "spog_route",
    "spog_surface_flag",
    "spog_registered_by",
    "spog_fronted_by",
)

# Trees excluded from the "no app structure" measurement, each for a stated
# reason: `vendor/` is the pinned CMR submodule (not this repo's tree),
# `.research/` is the gitignored harvest clone area, `.claude/` holds peer lane
# worktrees, `.board/`/`.fleet/`/`.verify/` are runtime state, and the rest are
# build/dependency caches.
EXCLUDED = {
    ".git", ".claude", ".research", ".board", ".fleet", ".portal", ".verify",
    ".telemetry", "vendor", "node_modules", "__pycache__", ".venv", "venv",
    "dist", "build", ".mypy_cache", ".pytest_cache",
}

# THE BREADTH OF THE OBSERVATION, DECLARED (issue #1161).
#
# This scan used to read only `.json`/`.yaml`/`.yml`, and said so only in its own
# code, so a `category: "system"` app declared in ANY other file type was
# invisible to it while the gate printed "the tree matches the claim". The shape
# a gate cannot see is the shape the app model's owner actually writes: the
# model this exemption names is owned by `kushin77/shared-frontend`, whose
# native-app registration lives in `shell/src/addons.ts`.
#
# So the breadth is a DECLARED allowlist, and the same tuple drives all three
# places that state it: this scan, the `OK` line, and `docs/MODULE-ADMISSION.md`
# §9. A declaration in TypeScript source now counts exactly as one in JSON.
SCANNED_SUFFIXES = (
    # data shapes
    ".json", ".jsonc", ".json5", ".yaml", ".yml", ".toml",
    # source shapes — the OS host and its shell declare the SAME app here
    ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
)
DATA_SUFFIXES = (".json", ".jsonc", ".json5", ".yaml", ".yml", ".toml")
CODE_SUFFIXES = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")

# The DECLARATION shape, per file type. A single "search for the substring
# anywhere" pattern would fire on prose and comments, so each pattern is
# anchored on the structural punctuation of the shape it belongs to — the member
# separator, the closing brace/bracket, or the end of the line. Prose ABOUT the
# declaration ("an OS function's app carries category: system") therefore does
# not fire; `comment_lines` below skips commented-out code on top of that.
#
# JSON, JSONC and a quoted key in source: `"category": "system",` or `}`.
JSON_SYSTEM_APP = re.compile(r'"category"\s*:\s*"system"\s*[,\}]')
# YAML, and a JSONC/TOML line: the pair and (as in TOML) an optional trailing
# comment — a declaration carrying a note is still a declaration.
YAML_SYSTEM_APP = re.compile(
    r"(?m)^[ \t]*category[ \t]*:[ \t]*[\"']?system[\"']?[ \t]*(?:#.*)?$")
# TOML: `category = "system"` (optionally followed by a comment).
TOML_SYSTEM_APP = re.compile(
    r"(?m)^[ \t]*category[ \t]*=[ \t]*[\"']?system[\"']?[ \t]*(?:#.*)?$")
# Source (`.ts`/`.js`/…): an object member, quoted or bare — the shape
# shared-frontend uses in `shell/src/addons.ts` — at a line start or after the
# opening brace / a separator, and followed by a separator, a closer, or the end
# of the line.
CODE_SYSTEM_APP = re.compile(
    r"""(?m)
    (?: ^[ \t]* | [{,][ \t]* )
    ["']?category["']? [ \t]*: [ \t]*
    ["']system["']
    [ \t]*
    (?: [,}\];] | $ )
    """,
    re.VERBOSE,
)

# Which shapes each suffix is measured with. A source file also gets the JSON
# pair (a `.ts` file can carry one), but never the reverse: the line-anchored
# YAML/TOML pair would fire on an indented `category: "system"` line in a `.ts`
# file that is prose or a commented-out block, and the code shape was not written
# for YAML.
SHAPES = {suffix: ((JSON_SYSTEM_APP, YAML_SYSTEM_APP, TOML_SYSTEM_APP)
                   if suffix in DATA_SUFFIXES
                   else (JSON_SYSTEM_APP, CODE_SYSTEM_APP))
          for suffix in SCANNED_SUFFIXES}


def read(path):
    with open(path, encoding="utf-8", errors="replace") as handle:
        return handle.read()


def parse_marker(text):
    """The declaration marker: a `key: value` list inside an HTML comment.

    Returns (values, problem). `values` is None when the marker is absent or
    malformed, and `problem` names which half is wrong.
    """
    start = text.find("<!-- " + MARKER_TAG)
    if start < 0:
        return None, "the `%s` marker is absent" % MARKER_TAG
    end = text.find("-->", start)
    if end < 0:
        return None, "the `%s` marker is never closed" % MARKER_TAG
    block = text[start:end]
    values = {}
    for line in block.splitlines():
        line = line.strip()
        if not line or line.startswith("<!--") or ": " not in line:
            continue
        key, _, value = line.partition(": ")
        values[key.strip()] = value.strip()
    missing = [key for key in MARKER_KEYS if not values.get(key)]
    if missing:
        return None, "the `%s` marker omits %s" % (MARKER_TAG, ", ".join(missing))
    return values, None


def comment_lines(text):
    """The 0-based lines whose content is inside a comment.

    Conservative in exactly ONE direction: a marker counts only when it is the
    line's FIRST non-space token, so a `//` inside a URL, or a `#` inside a
    colour or a shell parameter, can never hide a real declaration — a false
    negative here would be a gate passing a tree it cannot see, which is the
    defect this check exists for. What it does catch is a commented-out app
    block: that is prose about the declaration, not a declaration.
    """
    inside, lines = False, set()
    for index, line in enumerate(text.splitlines()):
        stripped = line.lstrip()
        if inside:
            lines.add(index)
            if "*/" in stripped:
                inside = False
            continue
        if stripped.startswith("/*"):
            lines.add(index)
            if "*/" not in stripped[2:]:
                inside = True
        elif stripped.startswith(("//", "#", "*", "--")):
            lines.add(index)
    return lines


def scan_tree(root):
    """Measured app structure: (root-level addon dirs, category:"system" hits).

    The breadth is the DECLARED `SCANNED_SUFFIXES` allowlist, and each suffix is
    matched with the shapes its file type can express (`SHAPES`). A hit is
    reported as `path:line`, so the refusal names what it found.
    """
    stray_dirs = [n for n in ("addons", "apps") if os.path.isdir(os.path.join(root, n))]
    hits = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in EXCLUDED)
        for name in sorted(filenames):
            patterns = SHAPES.get(os.path.splitext(name)[1].lower())
            if not patterns:
                continue
            path = os.path.join(dirpath, name)
            try:
                text = read(path)
            except OSError:
                continue
            commented = comment_lines(text)
            for pattern in patterns:
                # The first match of this shape that is NOT inside a comment: a
                # commented-out declaration must not mask a live one further
                # down the same file.
                found = None
                for match in pattern.finditer(text):
                    line = text.count("\n", 0, match.start()) + 1
                    if line - 1 not in commented:
                        found = line
                        break
                if found is None:
                    continue
                hits.append("%s:%d" % (os.path.relpath(path, root), found))
                break
    return stray_dirs, hits


def evaluate(root):
    """The one implementation of the rule. Returns (rc, findings, notes)."""
    findings, cannot, notes = [], [], []

    manifest_path = os.path.join(root, MANIFEST)
    doc_path = os.path.join(root, DOC)
    arch_path = os.path.join(root, ARCH)
    flags_path = os.path.join(root, FLAGS)

    manifest = None
    if not os.path.isfile(manifest_path):
        cannot.append("%s is absent" % MANIFEST)
    else:
        try:
            manifest = json.loads(read(manifest_path))
        except (OSError, json.JSONDecodeError) as exc:
            cannot.append("%s is unreadable (%s)" % (MANIFEST, exc))

    doc_text = None
    if not os.path.isfile(doc_path):
        cannot.append("%s is absent" % DOC)
    else:
        doc_text = read(doc_path)

    if cannot:
        return 2, findings, cannot

    # 1. module.json carries the declaration.
    own_repo = ""
    if isinstance(manifest.get("source"), dict):
        own_repo = str(manifest["source"].get("repo") or "")
    os_apps = manifest.get("os_apps")
    declared = {}
    if not isinstance(os_apps, dict):
        findings.append("%s (the `os_apps` block is absent — this repo's OS-app "
                        "exemption is undeclared)" % MANIFEST)
    else:
        if os_apps.get("hosts") is not False:
            findings.append("%s (`os_apps.hosts` is %r, not false — this module "
                            "hosts no OS apps; a host must declare the app model "
                            "it owns)" % (MANIFEST, os_apps.get("hosts")))
        owner = os_apps.get("model_owner")
        if not isinstance(owner, str) or not owner.strip():
            findings.append("%s (`os_apps.model_owner` does not name the repo that "
                            "owns the app/addon model)" % MANIFEST)
        elif own_repo and owner.strip() == own_repo:
            findings.append("%s (`os_apps.model_owner` names this module itself — a "
                            "module cannot be its own app model)" % MANIFEST)
        contract = os_apps.get("model_contract")
        if not isinstance(contract, str) or not contract.strip():
            findings.append("%s (`os_apps.model_contract` does not name the app model's "
                            "contract file)" % MANIFEST)
        declaration = os_apps.get("declaration")
        if not isinstance(declaration, str) or not declaration.strip():
            findings.append("%s (`os_apps.declaration` does not name the document that "
                            "carries the declaration)" % MANIFEST)
        elif not os.path.isfile(os.path.join(root, declaration.strip())):
            findings.append("%s (`os_apps.declaration` names %s, which is not a file "
                            "in this tree)" % (MANIFEST, declaration.strip()))
        declared = {k: str(v).strip() for k, v in os_apps.items() if isinstance(v, str)}

    # 2. the document's marker agrees with module.json.
    values, problem = parse_marker(doc_text)
    if problem:
        findings.append("%s (%s)" % (DOC, problem))
    else:
        if values["hosts_os_apps"].lower() != "false":
            findings.append("%s (the marker declares hosts_os_apps: %s, not false)"
                            % (DOC, values["hosts_os_apps"]))
        for key, manifest_key in (("app_model_owner", "model_owner"),
                                  ("app_model_contract", "model_contract")):
            if declared.get(manifest_key) and values[key] != declared[manifest_key]:
                findings.append("%s (marker %s=%s disagrees with %s os_apps.%s=%s)"
                                % (DOC, key, values[key], MANIFEST, manifest_key,
                                   declared[manifest_key]))

    # 3. the tree still matches the claim.
    stray_dirs, hits = scan_tree(root)
    for name in stray_dirs:
        findings.append("the tree carries a %s/ directory at the repository root, so "
                        "this repo now hosts OS apps — the exemption no longer holds "
                        "(it is declared in %s)" % (name, DOC))
    for hit in hits:
        findings.append("an OS app is declared (category: \"system\") at %s, so this "
                        "repo now hosts OS apps — the exemption no longer holds" % hit)

    # 4. the declared SPoG surface resolves.
    if values:
        view = values["spog_view"]
        route = values["spog_route"]
        flag = values["spog_surface_flag"]
        if not os.path.isfile(os.path.join(root, view)):
            findings.append("%s (the declaration names the SPoG view %s, which is not "
                            "a file in this tree)" % (DOC, view))
        elif not route.rstrip("/").endswith("/" + os.path.basename(view)):
            findings.append("%s (the declared route %r does not resolve to the declared "
                            "view %s)" % (DOC, route, view))
        surface = flag.split(".", 1)[1] if "." in flag else flag
        if not os.path.isfile(flags_path):
            cannot.append("%s is absent, so the declared surface %s cannot be assessed"
                          % (FLAGS, flag))
        elif not re.search(r"(?m)^[ \t]*%s[ \t]*:" % re.escape(surface), read(flags_path)):
            findings.append("%s (the declaration names the surface %s, which "
                            "%s does not declare)" % (DOC, flag, FLAGS))
        else:
            notes.append("the declared surface %s is declared in %s" % (flag, FLAGS))
        if values["spog_registered_by"] == declared.get("model_owner"):
            notes.append("the in-OS registration owner is the app-model owner (%s)"
                         % values["spog_registered_by"])

    if cannot:
        return 2, findings, cannot

    # 5. the architecture doc points a reader at the answer.
    if not os.path.isfile(arch_path):
        cannot.append("%s is absent" % ARCH)
        return 2, findings, cannot
    arch_text = read(arch_path)
    if DOC not in arch_text:
        findings.append("%s (does not point at %s, where the app/addon exemption is "
                        "declared)" % (ARCH, DOC))
    if declared.get("model_owner") and declared["model_owner"] not in arch_text:
        findings.append("%s (does not name the app/addon model owner %s)"
                        % (ARCH, declared["model_owner"]))

    return (1 if findings else 0), findings, notes


# The same app declaration, ONE FILE TYPE AT A TIME — the point of issue #1161
# made mechanical. Each plant is built from the suffix's own shape family (so a
# suffix cannot be added to `SCANNED_SUFFIXES` without its plant following), and
# `plant_for` returns the line the declaration lands on so the control can assert
# the refusal NAMES it rather than merely counting a failure.
DATA_PLANT = '{\n  "id": "sys-monitor",\n  "category": "system"\n}\n'
YAML_PLANT = ('apps:\n  - id: sys-monitor\n'
              '    category: "system" # the OS app marker\n')
TOML_PLANT = '[app]\nid = "sys-monitor"\ncategory = "system"\n'
CODE_PLANT = ('export const ADDONS = [\n'
              '  { id: "sys-monitor", category: "system", component: App },\n'
              '];\n')
PLANTS = {suffix: (TOML_PLANT if suffix == ".toml" else
                   YAML_PLANT if suffix in (".yaml", ".yml") else
                   CODE_PLANT if suffix in CODE_SUFFIXES else
                   DATA_PLANT)
          for suffix in SCANNED_SUFFIXES}

# The negative half: this is what a declaration looks like when it is prose or
# commented out. Neither may be refused.
PROSE_SOURCE = '''// An OS function's app is marked with `category: "system"` in the host's
// `registry/modules.json`; this module hosts none, so it declares the exemption
// instead. A commented-out app block is prose about the declaration, not one:
// export const ADDONS = [{ category: "system", component: App }];
/*
export const ADDONS = [
  { id: "sys-monitor", category: "system", component: App },
];
*/
const NOTE = "an OS function's app carries category: system, not a manifest";
'''
PROSE_YAML = ('# category: "system" marks an OS function\'s app in the host\n'
              '# registry/modules.json; this module hosts none.\n'
              'notes: []\n')


def plant_for(suffix):
    """(text, line) — a `category: "system"` app declared in that suffix.

    `line` is computed with the SAME patterns `scan_tree` matches with, so the
    control asserts the file:line the gate will actually report. A line of 0
    means no pattern of that suffix can see the plant, which the control treats
    as a failed precondition rather than a silent pass.
    """
    text = PLANTS[suffix]
    for pattern in SHAPES[suffix]:
        match = pattern.search(text)
        if match:
            return text, text.count("\n", 0, match.start()) + 1
    return text, 0


def stage(root, scratch):
    """A scratch copy of exactly the inputs the rule reads.

    That includes the view the declaration names, read out of the declaration
    itself, so the staged subject is the same SUBJECT the real run assessed —
    otherwise the unmutated control would fail on a file it never copied and the
    provocation would prove nothing about the mutation.
    """
    rels = list(INPUTS)
    try:
        values, problem = parse_marker(read(os.path.join(root, DOC)))
        if not problem and values["spog_view"] not in rels:
            rels.append(values["spog_view"])
    except OSError:
        pass
    for rel in rels:
        src = os.path.join(root, rel)
        if os.path.isfile(src):
            dst = os.path.join(scratch, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
    return scratch


def provoke(base, real_rc):
    """Every refusal, provoked on a mutated copy through the same code path.

    The staged base is restored from the REAL root before each mutation, so each
    provocation changes exactly one thing and the verdict it produces is about
    that thing.
    """
    results = []

    def check(label, needle, changed=True):
        """A refusal counts only if the MUTATION took and the refusal names it.

        `changed` is the mutant's own precondition: a control whose mutation
        silently did nothing would otherwise pass on the unmutated subject and
        prove nothing (measured while writing this: the doc mutation truncated
        the file before reading it, so every doc control "passed" against an
        empty file).
        """
        rc, findings = evaluate(base)[:2]
        ok = changed and rc == 1 and any(needle in f for f in findings)
        detail = "rc=%d, mutation-took=%s, findings=%d, first=%s" % (
            rc, changed, len(findings), findings[0][:80] if findings else "-")
        results.append((label, ok, detail))

    rc = evaluate(base)[0]
    results.append(("the unmutated staged copy behaves like the root",
                    rc == real_rc, "staged rc=%d, root rc=%d" % (rc, real_rc)))

    def rebuild(mutate_manifest=None, mutate_doc=None):
        """Restore the staged subject from the real root, then mutate it once.

        The staged tree is REMOVED first, not copied over: `stage` only writes the
        inputs it knows about, so without this a plant left behind by the
        previous provocation stays in the subject and the next verdict is about
        two mutations at once. Measured while adding the breadth controls — 12
        accumulated plants turned the prose negative control red with 13 findings
        that named none of the prose.

        Returns whether the mutation actually changed the file — read BEFORE the
        write, because opening for write truncates.
        """
        shutil.rmtree(base, ignore_errors=True)
        stage(ROOT, base)
        if mutate_manifest is not None:
            path = os.path.join(base, MANIFEST)
            before = read(path)
            data = json.loads(before)
            mutate_manifest(data)
            after = json.dumps(data, indent=2)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(after)
            return before != after
        if mutate_doc is not None:
            path = os.path.join(base, DOC)
            before = read(path)
            after = mutate_doc(before)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(after)
            return before != after
        return False

    def drop_os_apps(data):
        data.pop("os_apps", None)

    check("a deleted `os_apps` block is refused, by name", "`os_apps` block is absent",
          changed=rebuild(mutate_manifest=drop_os_apps))

    def hosts_true(data):
        data["os_apps"]["hosts"] = True

    check("`hosts: true` is refused, by name", "os_apps.hosts` is True",
          changed=rebuild(mutate_manifest=hosts_true))

    def strip_marker(text):
        start = text.find("<!-- " + MARKER_TAG)
        end = text.find("-->", start) + 3
        return text[:start] + text[end:]

    check("a deleted declaration marker is refused, by name", "marker is absent",
          changed=rebuild(mutate_doc=strip_marker))

    def owner_disagrees(text):
        return text.replace("app_model_owner: ",
                            "app_model_owner: kushin77/somewhere-else #", 1)

    check("a marker that disagrees with module.json is refused, by name",
          "disagrees with", changed=rebuild(mutate_doc=owner_disagrees))

    rebuild()
    os.makedirs(os.path.join(base, "addons", "some-app"), exist_ok=True)
    check("an addons/ directory is refused, by name", "addons/ directory")

    # (f) The BREADTH, provoked: one plant per declared suffix, each in the shape
    # that suffix can express. A suffix dropped from `SCANNED_SUFFIXES` — the
    # defect this issue is about, arriving from the other side — turns its own
    # control red instead of silently narrowing the observation again while the
    # `OK` line keeps its wording.
    for suffix in SCANNED_SUFFIXES:
        rebuild()
        rel = "shell/src/sys-monitor%s" % suffix
        text, line = plant_for(suffix)
        path = os.path.join(base, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        check("a `category: \"system\"` app in a %s file is refused, by name"
              % suffix, "%s:%d" % (rel, line), changed=line > 0)

    # (g) The negative half — it must not match everything. Prose ABOUT the
    # declaration, and a commented-out app block, are not declarations; the
    # verdict must be the same as the clean staged copy, and neither plant may be
    # named in a finding.
    rebuild()
    negative = {
        "shell/src/prose-about-the-declaration.ts": PROSE_SOURCE,
        "config/notes.yaml": PROSE_YAML,
    }
    for rel, text in negative.items():
        path = os.path.join(base, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
    rc_prose, prose_findings = evaluate(base)[:2]
    named = [f for f in prose_findings if any(rel in f for rel in negative)]
    results.append(("prose about the declaration, and a commented-out app block, "
                    "are NOT refused",
                    rc_prose == real_rc and not named,
                    "rc=%d (root rc=%d), findings=%d, names-a-plant=%s"
                    % (rc_prose, real_rc, len(prose_findings), bool(named))))

    return results


rc, findings, notes = evaluate(ROOT)

for line in notes:
    print("  OK    %s" % line)
for line in findings:
    print("  FAIL  %s" % line)

if rc == 2:
    print("check-system-app-declaration: CANNOT-ASSESS — %s" % notes[0])
    sys.exit(2)

if rc == 0:
    print("  OK    module.json declares the app/addon exemption (hosts=false, "
          "model_owner names the OS host that owns the model)")
    print("  OK    the declaration document agrees with module.json")
    print("  OK    the tree matches the claim: no addons/, no apps/, and no "
          "category: \"system\" declaration in any scanned shape (%s) outside "
          "vendor/ and the other excluded trees (runtime state, peer lane "
          "worktrees, build caches)" % " ".join(SCANNED_SUFFIXES))

if RUN_CONTROLS:
    print("== the refusals, provoked ==")
    bad = 0
    if rc == 2:
        print("  OK    skipped: the subject is unreadable, so there is no shape "
              "to mutate")
    else:
        scratch = tempfile.mkdtemp(prefix="ao945-system-app-", dir="/tmp")
        try:
            base = stage(ROOT, os.path.join(scratch, "base"))
            for label, ok, detail in provoke(base, rc):
                if ok:
                    print("  OK    %s" % label)
                else:
                    bad += 1
                    print("check-system-app-declaration: FAIL — %s was NOT refused "
                          "(%s)" % (label, detail))
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
    if bad:
        rc = 1

if rc == 0:
    print("check-system-app-declaration: OK — the OS-app exemption is declared, the "
          "declaration agrees with module.json, the tree matches the claim, and the "
          "declared SPoG surface resolves")
else:
    print("check-system-app-declaration: NOT-OK — %d finding(s); the declaration and "
          "the tree disagree" % len(findings))
sys.exit(rc)
PY
