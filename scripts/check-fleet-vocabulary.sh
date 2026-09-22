#!/usr/bin/env bash
# check-fleet-vocabulary.sh — the fleet role-vocabulary gate (issue #777).
#
# The fleet's role names are WIRE VALUES: the envelope carries them, and
# `fleet/channel.py` refuses any sender or recipient outside the closed set. So
# renaming them is a PROTOCOL MIGRATION, and this gate is what makes the
# migration a contract instead of a good intention:
#
#   * the GLOSSARY (`governance/vocabulary/fleet.yaml`) is the single authority,
#     and the code's constants, the JSON Schema's role patterns and the glossary
#     must still agree — a lane cannot mint a third name by editing one side;
#   * a schema-2 envelope carrying a retired (schema-1) role is REFUSED BY NAME,
#     for every retired role and every field — the mixture is the state ADR-0012
#     warns about (two vocabularies both quietly authoritative);
#   * an unrecognised `schema` version is refused, not defaulted;
#   * the WRITE seam is single-emit and NEGOTIATED: a recipient that declares
#     schema 2 in its live heartbeat gets schema-2 roles only, and one that
#     declares nothing gets the retired spelling it was built with — a downgrade
#     that is REPORTED by name, never silent. This is why the RUNNING loop
#     survives the rename, and the gate proves it rather than asserting it;
#   * a v2 emitter that still emits a v1 role is DETECTED — provoked by mutating
#     the write seam in a scratch copy, so the assertion is not self-satisfying;
#   * the normative surfaces use the current terms, with a retired term permitted
#     only inside a marked legacy-gloss region, a fenced block or an inline code
#     span (i.e. as an artifact name or a declared gloss — never as a role name);
#   * the invariants the rename must NOT lose are still declared, each on one
#     source line so a mutation can strip exactly one and be detected.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# Usage: bash scripts/check-fleet-vocabulary.sh
#
# ---knowledge---
# module_id: scripts.check-fleet-vocabulary
# system: governance
# app: gates
# solution_class: enterprise
# patterns: [tri-state-exit, provoked-negative-control, no-false-green, named-refusal, schema-validation]
# derives_from: null
# owner_sme: platform-sme
# tier: L1
# interfaces: [exit 0 OK, exit 1 NOT-OK, exit 2 CANNOT-ASSESS]
# invariants: ""
# gotchas: ""
# related: ["#777"]
# do_not_duplicate: null
# ---knowledge---
set -uo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"
cd "$root" || exit 2

glossary="governance/vocabulary/fleet.yaml"
schema="fleet/schema/message.schema.json"
contract="fleet/CONTRACT.md"
channel_py="fleet/channel.py"

fail=0
ok() { printf '  OK    %s\n' "$1"; }
bad() { printf '  FAIL  %s\n' "$1" >&2; fail=$((fail + 1)); }

# --- preflight: every absence is CANNOT-ASSESS, never a pass -----------------
if ! command -v python3 >/dev/null 2>&1; then
  echo "check-fleet-vocabulary: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi
if ! python3 -c 'import yaml' >/dev/null 2>&1; then
  echo "check-fleet-vocabulary: CANNOT-ASSESS — PyYAML is not available to read $glossary" >&2
  exit 2
fi
if [ ! -f "$glossary" ]; then
  echo "check-fleet-vocabulary: CANNOT-ASSESS — $glossary is missing: the vocabulary has no declared authority" >&2
  exit 2
fi
for required_file in "$schema" "$contract" "$channel_py"; do
  if [ ! -f "$required_file" ]; then
    echo "check-fleet-vocabulary: CANNOT-ASSESS — $required_file is missing" >&2
    exit 2
  fi
done

scratch="$(mktemp -d /tmp/ao-fleet-vocab.XXXXXX)" || {
  echo "check-fleet-vocabulary: CANNOT-ASSESS — cannot create a scratch directory" >&2
  exit 2
}
cleanup() { [ -n "$scratch" ] && rm -rf "$scratch" || true; }
trap cleanup EXIT

# --- the detector the emitter half is proved WITH ---------------------------
# Kept as a function (not inlined in the pass branch) so the same code can be run
# against a mutant and against a plant: a detector that is only ever run on clean
# input proves nothing.
single_dialect_report() { # single_dialect_report <mailbox-dir>...  (prints offenders)
  python3 - "$@" <<'PY'
import json
import pathlib
import sys

CURRENT = {"principal", "director", "dispatcher", "executor"}
RETIRED = {"operator", "brain", "sister", "subagent"}


def base(role: str) -> str:
    return role.split("-", 1)[0]


offenders = 0
for argument in sys.argv[1:]:
    for path in sorted(pathlib.Path(argument).glob("*.json")):
        try:
            message = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"    {path.name}: unreadable ({exc})")
            offenders += 1
            continue
        if message.get("schema") != 2:
            continue
        for field in ("from", "to"):
            value = message.get(field)
            if isinstance(value, str) and base(value) in RETIRED:
                print(
                    f"    {path.name}: a schema-2 envelope carries the retired role "
                    f"'{value}' in '{field}'"
                )
                offenders += 1
raise SystemExit(1 if offenders else 0)
PY
}

# --- 1. the glossary is the authority, and the code and schema agree with it -
if python3 - "$glossary" "$schema" <<'PY'
import json
import pathlib
import re
import sys

import yaml

glossary = yaml.safe_load(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
schema = json.loads(pathlib.Path(sys.argv[2]).read_text(encoding="utf-8"))
sys.path.insert(0, str(pathlib.Path("fleet").resolve()))
import channel  # noqa: E402  (repo convention: namespace module, bootstrapped above)

problems = []

names = tuple(role["name"] for role in glossary["roles"])
legacy = tuple(role["legacy_name"] for role in glossary["roles"])
if names != channel.ROLES_CURRENT:
    problems.append(
        f"roles in the glossary {names} != channel.ROLES_CURRENT {channel.ROLES_CURRENT}"
    )
if legacy != channel.ROLES_LEGACY:
    problems.append(
        f"retired names in the glossary {legacy} != channel.ROLES_LEGACY {channel.ROLES_LEGACY}"
    )
if glossary["schema"]["current"] != channel.SCHEMA_VERSION_CURRENT:
    problems.append(
        f"glossary schema.current {glossary['schema']['current']} != "
        f"channel.SCHEMA_VERSION_CURRENT {channel.SCHEMA_VERSION_CURRENT}"
    )
if glossary["schema"]["legacy"] != channel.SCHEMA_VERSION_LEGACY:
    problems.append(
        f"glossary schema.legacy {glossary['schema']['legacy']} != "
        f"channel.SCHEMA_VERSION_LEGACY {channel.SCHEMA_VERSION_LEGACY}"
    )
if glossary["schema"]["absent_means"] != channel.SCHEMA_VERSION_LEGACY:
    problems.append(
        "the glossary must declare that an absent `schema` field means "
        f"{channel.SCHEMA_VERSION_LEGACY} (the field is additive, as `nonce` was)"
    )
if glossary["schema"]["field"] != "schema":
    problems.append("the glossary's schema field name must be the envelope's own key")
mapping = {entry["name"]: entry["replaced_by"] for entry in glossary["retired"]}
if mapping != channel.ROLE_RENAME:
    problems.append(f"the glossary's retired->current mapping {mapping} != channel.ROLE_RENAME {channel.ROLE_RENAME}")
if set(glossary["retired"][0]) ^ set(glossary["retired"][-1]):
    problems.append("every retired entry must carry the same fields")
for entry in glossary["retired"]:
    for field in ("name", "replaced_by", "retired_in_schema", "gloss"):
        if not entry.get(field):
            problems.append(f"retired entry '{entry.get('name')}' is missing '{field}'")
    if entry.get("retired_in_schema") != channel.SCHEMA_VERSION_CURRENT:
        problems.append(
            f"retired entry '{entry.get('name')}' must name the schema it was retired in"
        )

# The JSON Schema must accept exactly the declared closed set, in both dialects —
# a pattern that drifted from the vocabulary would let the wire and the glossary
# disagree with no finding.
for field in ("from", "to"):
    pattern = schema["properties"][field]["pattern"]
    accepted = set(channel.ROLES_CURRENT) | set(channel.ROLES_LEGACY)
    for role in accepted:
        probe = role + ("-x" if role in channel.INSTANCED_ROLES else "")
        if not re.fullmatch(pattern, probe):
            problems.append(f"{field} pattern refuses the declared role '{probe}'")
    for probe in ("executor-x-extra", "dispatcher9", "director x", "principalx"):
        if re.fullmatch(pattern, probe):
            problems.append(f"{field} pattern accepts '{probe}', which is not a declared role")
enumerated = set(schema["properties"]["schema"]["enum"])
expected = {channel.SCHEMA_VERSION_LEGACY, channel.SCHEMA_VERSION_CURRENT}
if enumerated != expected:
    problems.append(f"the schema's `schema` enum {sorted(enumerated)} != the declared versions {sorted(expected)}")

if problems:
    for problem in problems:
        print(f"  FAIL  {problem}", file=sys.stderr)
    raise SystemExit(1)
print(
    f"  OK    {len(names)} roles + {len(glossary['retired'])} retired names agree across "
    "the glossary, the code and the envelope schema"
)
PY
then
  :
else
  fail=$((fail + 1))
fi

# --- 2. the mixture is refused, BY NAME, for every retired role --------------
# Provoked per role rather than once: a rule that only fired for one name would
# pass a single-role probe and let the other three through.
retired_names="$(python3 - "$glossary" <<'PY'
import pathlib
import sys

import yaml

glossary = yaml.safe_load(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
print("\n".join(entry["name"] for entry in glossary["retired"]))
PY
)"
if [ -z "$retired_names" ]; then
  bad "the glossary declares no retired names, so the mixture rule cannot be provoked"
fi
python3 - "$scratch" $retired_names <<'PY'
import json
import pathlib
import sys

scratch = pathlib.Path(sys.argv[1])
# BOTH fields, per role: a rule that only fired on the sender would pass every
# probe built one way and let a retired recipient through unremarked.
for role in sys.argv[2:]:
    for field in ("from", "to"):
        message = {
            "schema": 2,
            "from": "director",
            "to": "dispatcher",
            "type": "directive",
            "task": {"issue": 777},
        }
        message[field] = role
        (scratch / f"envelope.{field}.{role}.json").write_text(
            json.dumps(message), encoding="utf-8"
        )
PY
refused_all=1
probed=0
for role in $retired_names; do
  for field in from to; do
    probe="$scratch/envelope.$field.$role.json"
    probed=$((probed + 1))
    if [ ! -f "$probe" ]; then
      bad "could not build a schema-2 envelope carrying the retired role '$role' in '$field'"
      refused_all=0
      continue
    fi
    captured="$(python3 fleet/channel.py verify --message "$probe" 2>&1)"
    rc=$?
    case "$captured" in
      *"$field is '$role'"*) named=1 ;;
      *) named=0 ;;
    esac
    if [ "$rc" -eq 0 ]; then
      bad "a schema-2 envelope carrying the retired role '$role' in '$field' was ACCEPTED"
      refused_all=0
    elif [ "$named" -ne 1 ]; then
      bad "the refusal for the retired role '$role' in '$field' did not name the field and the token"
      refused_all=0
    fi
  done
done
if [ "$refused_all" -eq 1 ] && [ -n "$retired_names" ]; then
  ok "a retired role in a schema-2 envelope is refused by name (every retired name × every field: $probed probes)"
fi

# The half that stops the rule matching everything: a correct schema-2 envelope
# must be ACCEPTED, and an undeclared version must not be defaulted.
python3 - "$scratch/current.json" "$scratch/undeclared.json" <<'PY'
import json
import sys

json.dump(
    {"schema": 2, "from": "director", "to": "dispatcher", "type": "directive", "task": {"issue": 777}},
    open(sys.argv[1], "w", encoding="utf-8"),
)
json.dump({"schema": 3, "from": "director", "to": "dispatcher", "type": "halt"}, open(sys.argv[2], "w", encoding="utf-8"))
PY
if captured="$(python3 fleet/channel.py verify --message "$scratch/current.json" 2>&1)"; then
  case "$captured" in
    *OK*) ok "a current-dialect schema-2 envelope is accepted (the rule is not over-strict)" ;;
    *) bad "a valid schema-2 envelope reported success without saying OK: $captured" ;;
  esac
else
  bad "a valid schema-2 envelope was refused — the migration is over-strict"
fi
if captured="$(python3 fleet/channel.py verify --message "$scratch/undeclared.json" 2>&1)"; then
  bad "an undeclared envelope version (schema 3) was accepted"
else
  case "$captured" in
    *"schema must be one of"*) ok "an undeclared envelope version is refused by name" ;;
    *) bad "schema 3 was refused without naming the version rule: $captured" ;;
  esac
fi

# --- 3. the write seam is single-emit and negotiated ------------------------
declare_beat() { # declare_beat <fleet-dir> <state> [version]
  local dir="$1" state="$2" version="${3:-}"
  mkdir -p "$dir" || return 1
  # argv is: ['-', <heartbeat path>, <state>, <version>] — the beat is written to
  # argv[1] (the PATH), and argv[2] is the state it declares. Writing to argv[2]
  # would create a file named after the state and leave the real beat absent,
  # which is exactly the shape that makes the whole negotiation half of this gate
  # silently test the DEPRECATION path and never the current one.
  python3 - "$dir/sister.heartbeat.json" "$state" "$version" <<'PY'
import json
import sys

beat = {"pid": 4242, "state": sys.argv[2], "commit": "deadbee", "ts": "2026-09-16T00:00:00Z"}
if sys.argv[3]:
    beat["envelope_schema"] = int(sys.argv[3])
open(sys.argv[1], "w", encoding="utf-8").write(json.dumps(beat) + "\n")
PY
}

landed_dialect() { # landed_dialect <mailbox-dir> — the emitted (schema, from, to)
  python3 - "$1" <<'PY'
import json
import pathlib
import sys

paths = sorted(pathlib.Path(sys.argv[1]).glob("*.json"))
if not paths:
    print("none")
else:
    message = json.loads(paths[0].read_text(encoding="utf-8"))
    # A field-order-independent reading. The assertion is about the VALUES the
    # emitter chose; key order is json.dumps' business, not the contract's.
    print(f"schema={message.get('schema')} from={message.get('from')} to={message.get('to')}")
PY
}

emit_probe() { # emit_probe <fleet-dir> <channel.py path> — send and print the channel's own output
  local dir="$1" channel="$2"
  # PYTHONPATH carries the checkout so a copy of fleet/ in a scratch tree can
  # still resolve `governance.*` — the mutant runs the SAME code path the real
  # emitter does, just from a copy, which is what makes the provocation about the
  # seam rather than about a re-implementation of it.
  PYTHONPATH="$root" AO_FLEET_DIR="$dir" AO_FLEET_ENVELOPE_SCHEMA=auto python3 "$channel" send \
    --message '{"from":"brain","to":"sister","type":"directive","task":{"issue":777}}' 2>&1
}

# 3a. a recipient that declares schema 2 gets schema-2 roles only.
declared_dir="$scratch/declared/.fleet"
if declare_beat "$declared_dir" idle 2; then
  captured="$(emit_probe "$declared_dir" fleet/channel.py)"
  if single_dialect_report "$declared_dir/inbox"; then
    landed="$(landed_dialect "$declared_dir/inbox")"
    case "$landed" in
      "schema=2 from=director to=dispatcher")
        ok "a dispatcher declaring envelope_schema=2 receives the current role names only"
        ;;
      *)
        bad "the emitting dialect for a schema-2 recipient was not the current one: $landed ($captured)"
        ;;
    esac
  else
    bad "the emitter wrote a mixed envelope for a schema-2 recipient"
  fi
else
  bad "could not declare the recipient beat for the schema-2 case"
fi

# 3b. the deprecation window: a recipient that declares NOTHING — the RUNNING
# loop's own shape — keeps receiving the spelling it was built with, and the
# downgrade is recorded by name. This is the compat half, proved not asserted.
legacy_dir="$scratch/legacy/.fleet"
if declare_beat "$legacy_dir" idle ""; then
  captured="$(emit_probe "$legacy_dir" fleet/channel.py)"
  case "$captured" in
    *legacy-dialect*dispatcher*)
      landed="$(landed_dialect "$legacy_dir/inbox")"
      case "$landed" in
        "schema=1 from=brain to=sister")
          ok "a recipient that declares nothing keeps the retired spelling, and the downgrade is recorded"
          ;;
        *)
          bad "the downgrade did not emit the retired spelling: $landed"
          ;;
      esac
      ;;
    *)
      bad "the downgrade to the retired dialect was not recorded by name: $captured"
      ;;
  esac
  # An unreadable beat must never be read as current (AO-GR-25's rule here).
  printf '{ not json' >"$legacy_dir/sister.heartbeat.json"
  captured="$(AO_FLEET_DIR="$legacy_dir" python3 -c '
import sys
sys.path.insert(0, "fleet")
import channel
print(channel.dialect_for("dispatcher")[0], channel.declared_envelope_schema("dispatcher"))
' 2>&1)"
  case "$captured" in
    "1 None") ok "an unreadable beat is CANNOT-ASSESS, never read as schema 2" ;;
    *) bad "an unreadable beat did not degrade to the retired dialect: $captured" ;;
  esac
else
  bad "could not declare the recipient beat for the deprecation-window case"
fi

# 3c. a v2 emitter that STILL emits a v1 role must be detected. Provoked by
# mutating the write seam in a scratch copy: the translation and the mixture
# refusal are both disabled, so the mutant emits exactly the forbidden artifact.
mutant="$scratch/mutant"
if cp -R fleet "$mutant" 2>/dev/null; then
  if python3 - "$mutant/channel.py" <<'PY'
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
before = text
text = text.replace(
    "        if version == SCHEMA_VERSION_CURRENT and is_retired_role(value):",
    "        if False:  # mutation: the seam no longer translates",
)
text = text.replace(
    "    elif envelope_schema == SCHEMA_VERSION_CURRENT:",
    "    elif False:  # mutation: the mixture is no longer refused",
)
if text == before:
    raise SystemExit(1)
path.write_text(text, encoding="utf-8")
PY
  then
    mutant_dir="$scratch/mutant-fleet/.fleet"
    if declare_beat "$mutant_dir" idle 2; then
      captured="$(emit_probe "$mutant_dir" "$mutant/channel.py")"
      if single_dialect_report "$mutant_dir/inbox"; then
        bad "a v2 emitter that still emitted a v1 role went undetected (the mutant wrote no mixture)"
      else
        ok "a v2 emitter that still emits a v1 role is refused and named"
      fi
    else
      bad "could not declare the recipient beat for the mutant case"
    fi
  else
    bad "could not mutate the write seam (the provocation would prove nothing)"
  fi
else
  bad "could not copy fleet/ into a scratch tree to mutate the write seam"
fi

# --- 4. the normative surfaces use the current terms ------------------------
# A retired term is permitted only inside a marked legacy-gloss region, a fenced
# code block or an inline code span — i.e. as a declared gloss or an artifact
# name (a path, a command, a heartbeat filename). As a ROLE NAME in prose, it is
# a finding.
surfaces="$(python3 -c '
import pathlib, sys, yaml
glossary = yaml.safe_load(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
sys.stdout.write("\n".join(glossary["normative_surfaces"]))
' "$glossary")"
while read -r surface; do
  [ -n "$surface" ] || continue
  if [ ! -f "$surface" ]; then
    bad "$surface is declared a normative surface but does not exist"
  fi
done <<<"$surfaces"

surface_report() { # surface_report <file>... — retired terms used as names
  python3 - "$legacy_start" "$legacy_end" "$@" <<'PY'
import pathlib
import re
import sys

start, end = sys.argv[1], sys.argv[2]
terms = ("operator", "brain", "sister", "subagent")
findings = 0
for name in sys.argv[3:]:
    text = pathlib.Path(name).read_text(encoding="utf-8")
    text = re.sub(re.escape(start) + r".*?" + re.escape(end), "", text, flags=re.S)
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    # An ARTIFACT NAME is permitted: a path, a command, a heartbeat filename. In
    # Markdown that means an inline code span or a link TARGET — both are named
    # machinery, not prose naming a role. Link TEXT stays visible, so a link that
    # calls a role by a retired name is still a finding.
    text = re.sub(r"`[^`]*`", "", text)
    text = re.sub(r"\]\([^)]*\)", "]", text)
    # ...and the same permission has to hold OUTSIDE Markdown, where a path is
    # written bare: the standing directive is JSON, so `docs/OPERATOR-ACCESS.md`
    # there is a link in prose clothing, not a role called "operator". A path and
    # a filename are the glossary's declared ARTIFACT NAMES; stripping them is
    # this rule implementing the declaration rather than quietly failing on it.
    text = re.sub(r"\S*/\S*", " ", text)
    text = re.sub(r"[\w.-]+\.(?:py|md|json|sh|ya?ml|txt|lock|tsv|cfg|toml|ini)\b", " ", text)
    for number, line in enumerate(text.splitlines(), 1):
        for term in terms:
            if re.search(rf"\b{term}\b", line, re.IGNORECASE):
                print(f"    {name}:{number}: uses the retired term '{term}' as a name: {line.strip()[:100]}")
                findings += 1
raise SystemExit(1 if findings else 0)
PY
}

markers="$(python3 -c '
import pathlib, sys, yaml
glossary = yaml.safe_load(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
print(glossary["legacy_region_markers"]["start"])
print(glossary["legacy_region_markers"]["end"])
' "$glossary")"
legacy_start="$(printf '%s\n' "$markers" | sed -n '1p')"
legacy_end="$(printf '%s\n' "$markers" | sed -n '2p')"

if [ -z "$legacy_start" ] || [ -z "$legacy_end" ]; then
  bad "the glossary declares no legacy-region markers, so the surface rule cannot be applied"
else
  # shellcheck disable=SC2046  (word splitting is intended: a file list)
  if surface_report $(printf '%s\n' "$surfaces" | tr '\n' ' '); then
    ok "the normative surfaces use the current terms; no retired term is used as a name"
  else
    fail=$((fail + 1))
  fi
  # The half that stops the rule matching nothing: plant the retired word in
  # prose and require the detector to refuse it by name.
  plant="$scratch/plant.md"
  printf 'The sister drains the inbox and reports to the brain.\n' >"$plant"
  if surface_report "$plant" >/dev/null 2>&1; then
    bad "the surface rule accepted a planted prose use of a retired term (the rule matches nothing)"
  else
    ok "a retired term used as a NAME in prose is refused — the rule is not vacuous"
  fi
  printf '%sThe sister drains it.%s\n' "$legacy_start" "$legacy_end" >"$plant"
  if surface_report "$plant" >/dev/null 2>&1; then
    ok "a retired term inside a marked legacy-gloss region is allowed (the gloss survives)"
  else
    bad "the rule refused a declared gloss, so the glossary cannot explain the retired names"
  fi
fi

# --- 5. the invariants the rename must not have lost ------------------------
# Each invariant is one source line in the contract, so a mutation can strip
# exactly one and be detected — a rule the transport cannot be checked against is
# a formality (GR-12).
declare -a required_invariants=(
  "the dispatcher never picks work"
  "the director is the only issuer of directives"
  "one issue = one lane = one executor"
  "the principal orders the director, never the dispatcher"
)
invariant_fail=0
for invariant in "${required_invariants[@]}"; do
  # Case-insensitive: an invariant is a sentence, and which word a sentence opens
  # with is layout, not doctrine. A case-SENSITIVE probe here reports "the
  # invariant is no longer declared" for a contract that declares it verbatim
  # with a capital letter — a false failure in the one direction that matters.
  if ! grep -qiF -- "$invariant" "$contract"; then
    printf '  FAIL  %s (the invariant is no longer declared: %s)\n' "$contract" "$invariant" >&2
    invariant_fail=1
  fi
done
if [ "$invariant_fail" -eq 0 ]; then
  ok "${#required_invariants[@]} invariants survived the rename, each on one declared line"
fi
# Vacuity control: strip every invariant line and require detection. If the
# stripped contract still passes, the invariants above are not what is being read.
grep -viF -e "${required_invariants[0]}" -e "${required_invariants[1]}" \
  -e "${required_invariants[2]}" -e "${required_invariants[3]}" "$contract" >"$scratch/contract-no-invariants.md"
stripped_fail=0
for invariant in "${required_invariants[@]}"; do
  if grep -qiF -- "$invariant" "$scratch/contract-no-invariants.md"; then
    stripped_fail=1
  fi
done
if [ "$stripped_fail" -eq 0 ]; then
  ok "vacuity control: stripping the invariants is detected"
else
  bad "vacuity control: a stripped invariant survived the strip"
fi

# --- 6. code prose (comments + docstrings) in fleet/*.py --------------------
# Issue #923: #777 migrated the contract surface but declared code prose
# out-of-scope, "not silently done" (see the glossary's `out_of_scope`). This
# section makes that declaration a check that can fail: a retired role name
# used as a NAME inside a `fleet/*.py` comment or docstring is a finding, named
# by file, line and word — never an artifact identifier (a rung literal, a
# mailbox path, a printed `[brain]`/`[sister]` tag, a CLI flag value, a
# hyphenated code identifier like `brain-inbox`), which the detector never
# visits in the first place (it walks `tokenize.COMMENT` tokens and
# `ast.get_docstring` text only — see scripts/lib/fleet_code_prose.py).
#
# A STRUCTURAL IDENTIFIER is out of scope for the same reason (#1974): a
# `---knowledge---` block is machine-readable data (docs/CODE-HEADER-STANDARD.md)
# and its `module_id:` value is this file's stable identity — `fleet.brain`
# NAMES THE MODULE, it does not name a role. The detector skips that one field
# BY NAME and nothing else, so a retired term written as prose in the block's
# free text (`invariants:`, `gotchas:`) is still refused — both halves are
# provoked below.
prose_checker="scripts/lib/fleet_code_prose.py"
if [ ! -f "$prose_checker" ]; then
  bad "$prose_checker is missing: the code-prose surface has no detector"
else
  # shellcheck disable=SC2046  (word splitting is intended: a file list)
  prose_findings="$(python3 "$prose_checker" $(printf '%s\n' fleet/*.py) 2>&1)"
  prose_rc=$?
  if [ "$prose_rc" -eq 0 ]; then
    ok "no retired role name is used as a NAME in a fleet/*.py comment or docstring"
  elif [ "$prose_rc" -eq 1 ]; then
    bad "retired role name(s) found in fleet/*.py code prose:"
    printf '%s\n' "$prose_findings" | sed 's/^/        /' >&2
  else
    echo "check-fleet-vocabulary: CANNOT-ASSESS — $prose_checker exited $prose_rc: $prose_findings" >&2
    exit 2
  fi

  # Vacuity control: plant a retired name in a scratch docstring and require the
  # detector to refuse it BY NAME (file, line, word) — a rule never provoked
  # proves nothing (GR-12).
  plant_py="$scratch/plant_prose.py"
  cat >"$plant_py" <<'PYEOF'
"""A module docstring that names the sister session and reports to the brain."""


def handoff():
    # The sister drains the inbox; the brain never picks work on its own.
    return None
PYEOF
  if plant_out="$(python3 "$prose_checker" "$plant_py" 2>&1)"; then
    bad "the code-prose detector accepted a planted retired name (the rule matches nothing)"
  else
    plant_rc=$?
    if [ "$plant_rc" -eq 1 ] \
      && [[ "$plant_out" == *"$plant_py:1: retired term 'sister'"* ]] \
      && [[ "$plant_out" == *"$plant_py:5: retired term 'sister'"* ]] \
      && [[ "$plant_out" == *"$plant_py:5: retired term 'brain'"* ]]; then
      ok "a retired name planted in a docstring and a comment is refused by file, line and word"
    else
      bad "the planted retired name was refused but not named by file+line+word: $plant_out"
    fi
  fi

  # The other half: a retired name inside a marked legacy-gloss region, and one
  # used only as an ARTIFACT identifier, must both be ALLOWED — the detector is
  # not just a blanket regex over the file text.
  gloss_py="$scratch/gloss_prose.py"
  cat >"$gloss_py" <<'PYEOF'
def rung_names():
    # legacy-gloss:start
    # "sister" named the loop by its relation to another seat; retired in #777.
    # legacy-gloss:end
    CAPABILITY_RUNGS = ("brain", "sister")
    # it answers via `.fleet/brain/outbox`, the `brain-inbox` subcommand and the
    # printed [sister] tag — all artifact names, not role names.
    return CAPABILITY_RUNGS
PYEOF
  if python3 "$prose_checker" "$gloss_py" >/tmp/ao-fleet-vocab-gloss.out 2>&1; then
    ok "a legacy-gloss region and a bare artifact reference are both allowed (the rule is not over-strict)"
  else
    bad "the detector refused a declared gloss or an artifact reference: $(cat /tmp/ao-fleet-vocab-gloss.out)"
  fi
  rm -f /tmp/ao-fleet-vocab-gloss.out

  # #1974, both halves of the module_id scoping. A retired word in the STRUCTURAL
  # `module_id:` field is a machine-readable identifier, not prose, so it is
  # allowed BY NAME...
  id_py="$scratch/module_id_prose.py"
  cat >"$id_py" <<'PYEOF'
"""The fleet module — its identity lives in the knowledge block below.

---knowledge---
module_id: fleet.brain
system: fleet
app: fleet
invariants: ""
gotchas: ""
---knowledge---
"""


def dispatch():
    return None
PYEOF
  if python3 "$prose_checker" "$id_py" 2>&1; then
    ok "a retired word in the structural module_id field is allowed BY NAME (the block is data, not prose)"
  else
    bad "the detector refused a structural module_id field, which is data rather than prose"
  fi

  # ...and the free text of the SAME block is still prose: a retired term in
  # `invariants:` is refused by name, so the exemption is the one field it names
  # and not the whole block.
  free_py="$scratch/module_free_prose.py"
  cat >"$free_py" <<'PYEOF'
"""The fleet module — its identity lives in the knowledge block below.

---knowledge---
module_id: fleet.brain
system: fleet
app: fleet
invariants: "the sister never picks work; the brain only orders"
gotchas: ""
---knowledge---
"""


def dispatch():
    return None
PYEOF
  if free_out="$(python3 "$prose_checker" "$free_py" 2>&1)"; then
    bad "the detector accepted a retired term in the knowledge block's free text (the exemption leaked past module_id)"
  elif [[ "$free_out" == *"retired term 'sister'"* ]] && [[ "$free_out" == *"retired term 'brain'"* ]]; then
    ok "a retired term in the block's free text is still refused by name — the exemption is module_id only"
  else
    bad "the block's free text was refused but not by name: $free_out"
  fi
fi

# --- verdict ----------------------------------------------------------------
if [ "$fail" -gt 0 ]; then
  echo "check-fleet-vocabulary: FAIL ($fail violation(s))" >&2
  exit 1
fi
echo "check-fleet-vocabulary: OK — the glossary is the single authority, the mixture is refused by name, the write seam is negotiated single-emit, and the invariants survive"
exit 0
