#!/usr/bin/env bash
# check-edge-cutover.sh — the fronting of the web hostname is DECLARED, the
# retired GCP edge route is not created by default, and neither can regress
# silently (issue #731, parent EPIC #607).
#
# THE DEFECT THIS EXISTS FOR
#   `ai.purebliss.app` was claimed by two declarations that met nowhere: the
#   Terraform module `infra/terraform/modules/web-surface` declared a GCP edge
#   route (a Cloud DNS CNAME to ghs.googlehosted.com plus a Cloud Run domain
#   mapping), while the live zone is served by Cloudflare. Nothing reconciled
#   them and nothing enforced either, so whichever declaration the next deploy
#   ran would silently win. `docs/EDGE-CUTOVER.md` decides it — the fronting is a
#   Cloudflare tunnel to the shared-services run half, and the GCP route is
#   RETIRED — and this gate is the mechanical half of that decision.
#
# WHAT IS MEASURED
#   Text properties (offline, deterministic, always assessed):
#     * the declaration exists and still names the decision, the retirement, the
#       retired target and the code symbols that implement it;
#     * `create_gcp_edge_route` is declared and DEFAULTS TO FALSE;
#     * BOTH halves of the retired route (the CNAME record and the domain
#       mapping) have a `count` that RESOLVES to that variable — directly or
#       through a local — so neither can be created unconditionally again;
#     * the pre-existing-zone path is RESOLVED (`data "google_dns_managed_zone"`)
#       rather than named and hoped for;
#     * the named refusal of the incoherent combination (`create-dns-zone-orphan`)
#       still exists;
#     * no output indexes a resource the route gate can empty (`web[0]`), which
#       fails at plan time instead of disappearing.
#
#   Behavioural properties (a real `terraform plan`, offline — see DEGRADE):
#     * the committed DEFAULT posture creates the surface and NO part of the edge
#       route — the plan is required to contain zero `google_dns_record_set`,
#       zero `google_cloud_run_domain_mapping` and zero `google_dns_managed_zone`,
#       AND to create at least one resource (an empty plan would satisfy "no edge
#       route" vacuously);
#     * the incoherent combination (zone declared, route retired) is REFUSED BY
#       NAME, and the refusal names `create-dns-zone-orphan`.
#
# HOW IT PROVES ITSELF (a control that cannot fail is a formality — GR-12)
#   `--self-test` runs on EVERY invocation, over a scratch copy of the shipped
#   files, in both directions:
#     1. the UNMUTATED copy produces NO finding — a rule that matches everything
#        cannot pass this half;
#     2. one mutation per property must move the verdict, and must do so BY NAME —
#        the property under test is the property that fired;
#     3. a mutation that changes no bytes is reported NOOP and fails the gate, so
#        a mutation that never landed cannot be read as a passing control;
#     4. the terraform mutant (the `create-dns-zone-orphan` refusal deleted) must
#        STOP being refused, or the behavioural half proves nothing.
#   The provocation drives the SAME analysis function the repository run uses.
#
# DEGRADE CONTRACT (chosen deliberately — see scripts/check-terraform.sh:5-7)
#   The text properties always assess, so the exit code is always driven by real
#   assertions. The BEHAVIOURAL probes need `terraform` plus the local provider
#   cache; when either is absent they print a VISIBLE `SKIP` naming what was not
#   assessed and why, exactly as `scripts/check-terraform.sh` does — never a
#   silent pass, and never a false red on a box without the cache. A genuine
#   inability to assess (no scratch dir, no python3, a probe whose staging fails,
#   or a refusal that cannot be ATTRIBUTED to the rule under test) is rc 2,
#   CANNOT-ASSESS, never 0.
#
# EVIDENCE SELF-CONTAINMENT (issue #936)
#   The scratch dir is removed by `trap cleanup EXIT`, so a message that names a
#   path INSIDE it points at evidence that is gone before the reader can look, and
#   the cause cannot be established from the report alone. Every probe failure
#   therefore (a) INLINES the terraform log's CAUSE-NAMING TAIL into the report, so
#   the report stands alone, and (b) copies the full log OUT of the scratch dir to
#   a sibling `<scratch>.evidence/` directory whose name carries this run's pid and
#   clock — never a fixed `/tmp/<name>`, which two lanes on one box would share.
#
#   And the two failure MEANINGS are NAMED rather than conflated:
#     * `mutant-not-accepted` (rc 1, NOT-OK) — the probe COMPLETED and the refusal
#       SURVIVED with the rule deleted, so it does not come from the rule under
#       test: a real finding about the rule;
#     * `probe-incomplete` (rc 2, CANNOT-ASSESS) — the probe DID NOT COMPLETE, so
#       the mutant was neither ACCEPTED nor refused and the gate proves nothing.
#       That is not a finding about the rule and must never read as one.
#
# THE DEFAULT PATH IS OFFLINE. There is no network call anywhere on it. The live
# probe is opt-in (`--live`, or `AO_EDGE_HOST`) and is never on the `make verify`
# path.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# Usage:
#   bash scripts/check-edge-cutover.sh              # gate (self-test + tree)
#   bash scripts/check-edge-cutover.sh --self-test  # the provocation alone
#   bash scripts/check-edge-cutover.sh --live       # measure the LIVE host
#   bash scripts/check-edge-cutover.sh --root DIR   # analyse another tree
set -u

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)" || exit 2

MODULE_REL="infra/terraform/modules/web-surface"
DECL_REL="docs/EDGE-CUTOVER.md"
GATE_VAR="create_gcp_edge_route"
VALIDATION_TOKEN="create-dns-zone-orphan"

# The live probe's host. Overridable, and never used by the default path.
EDGE_HOST="${AO_EDGE_HOST:-ai.purebliss.app}"

# Scratch lives on /tmp and is built from the pid plus the clock: a literal
# template would carry a run of capital X, which this repository's docs gate
# refuses as an unfinished marker (issue #804). One global variable, one EXIT
# trap, `|| true`-safe so a cleanup failure cannot mask the gate's own code.
SCRATCH=""
cleanup() { [ -n "$SCRATCH" ] && rm -rf "$SCRATCH" || true; }
trap cleanup EXIT

# The evidence dir lives BESIDE the scratch dir so `cleanup` does not take it: it
# is the one place a failed probe can leave the FULL terraform log. Its name
# carries this run's pid and clock because two lanes on one box must never share a
# path (issue #936). Empty until a probe failure needs it.
EVIDENCE_DIR=""

# tf_log_tail <log> [limit] : the CAUSE-NAMING tail of a terraform log, on ONE
# line, so it can be embedded in a single report line. The report must stand alone
# (#936): the log itself lives in a scratch dir the EXIT trap removes.
tf_log_tail() {
  local log="$1" limit="${2:-12}"
  if [ ! -f "$log" ]; then
    printf '<no terraform log was written at %s>' "$log"
    return 0
  fi
  awk -v limit="$limit" '
    NF { line[++n] = $0 }
    END {
      start = (n > limit) ? n - limit + 1 : 1
      out = ""
      for (i = start; i <= n; i++) out = (out == "" ? line[i] : out " | " line[i])
      printf "%s", (out == "" ? "<log is empty>" : out)
    }' "$log" 2>/dev/null || printf '<log unreadable at %s>' "$log"
}

# tf_preserve <log> : copy a log OUT of the scratch dir and print where it went.
# Prints `<not preserved>` when the copy cannot be made — never fatal, because the
# INLINED tail from tf_log_tail is the primary evidence and the report never
# depends on this succeeding.
tf_preserve() {
  local log="$1" dest
  if [ ! -f "$log" ]; then
    printf '<no terraform log to preserve>'
    return 0
  fi
  if [ -z "$EVIDENCE_DIR" ]; then
    EVIDENCE_DIR="$(dirname "$SCRATCH")/$(basename "$SCRATCH").evidence"
    if ! mkdir -p "$EVIDENCE_DIR" 2>/dev/null; then
      EVIDENCE_DIR=""
      printf '<not preserved>'
      return 0
    fi
  fi
  # The probe's own scratch subdir is part of the name (`tf-mutant-plan-default.log`),
  # so two probes failing in one run cannot overwrite each other's evidence.
  dest="$EVIDENCE_DIR/$(basename "$(dirname "$log")")-$(basename "$log")"
  if cp -f "$log" "$dest" 2>/dev/null; then
    printf '%s' "$dest"
  else
    printf '<not preserved>'
  fi
}

usage() {
  cat <<'TEXT'
check-edge-cutover.sh — the web hostname's fronting is declared and the retired
GCP edge route is not created by default (issue #731).

Usage:
  bash scripts/check-edge-cutover.sh              the gate (self-test + tree)
  bash scripts/check-edge-cutover.sh --self-test  the provocation alone
  bash scripts/check-edge-cutover.sh --live       measure the LIVE host
  bash scripts/check-edge-cutover.sh --root DIR   analyse another tree

Environment:
  AO_EDGE_HOST   host the live probe measures (default ai.purebliss.app)

Exit codes: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
TEXT
}

# --- the analysis: ONE implementation, driven by both paths -----------------
# analyse <root> : prints one `FINDING <name> <detail>` line per broken property
# and nothing when every property holds. rc 2 when it cannot assess at all.
analyse() {
  python3 - "$1" <<'PY'
import os
import re
import sys

MODULE = "infra/terraform/modules/web-surface"
DECL = "docs/EDGE-CUTOVER.md"
GATE_VAR = "create_gcp_edge_route"
VALIDATION_TOKEN = "create-dns-zone-orphan"

root = sys.argv[1]
if not os.path.isdir(root):
    print("CANNOT the analysis root %s is not a directory" % root)
    sys.exit(2)

findings = []


def refuse(name, detail):
    findings.append((name, detail))


def read(rel):
    """The file's text, or None when it is absent."""
    path = os.path.join(root, rel)
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        return None


def hcl_block(text, header_re):
    """The `{...}` block whose header line matches, by brace depth.

    terraform fmt puts every closing brace of a top-level block at column 0, so
    a depth walk that ends at the first line bringing depth back to zero is the
    block. Returns None when no header matches.
    """
    lines = text.splitlines()
    for start, line in enumerate(lines):
        if not re.match(header_re, line.strip()):
            continue
        depth = 0
        for end in range(start, len(lines)):
            depth += lines[end].count("{") - lines[end].count("}")
            if depth <= 0 and end > start:
                return "\n".join(lines[start:end + 1])
        return None
    return None


# --- 1. the declaration -----------------------------------------------------
doc = read(DECL)
if doc is None:
    refuse("declaration-missing",
           "%s is absent — the fronting of the web hostname has no declaration, "
           "so the conflict it ends is unstated again" % DECL)
else:
    # Each probe is one phrase the page must keep saying, and every one of them
    # is a different fact: the DECISION, the DISPOSITION of the GCP route, the
    # retired TARGET (so the retirement is specific rather than general), and the
    # two CODE symbols that implement it (so the page and the module stay
    # re-readable together). Keep each phrase on one source line in the doc.
    probes = (
        ("Cloudflare tunnel to the shared-services run half",
         "the fronting decision (Cloudflare tunnel to the shared-services run half)"),
        ("RETIRED",
         "the disposition of the GCP edge route (RETIRED)"),
        ("ghs.googlehosted.com",
         "the retired GCP target (ghs.googlehosted.com)"),
        (GATE_VAR,
         "the variable that implements the retirement (%s)" % GATE_VAR),
        ("google_cloud_run_domain_mapping",
         "the second retired resource (google_cloud_run_domain_mapping)"),
    )
    for phrase, what in probes:
        if phrase not in doc:
            refuse("declaration-silent",
                   "%s no longer declares %s — missing: %r" % (DECL, what, phrase))

# --- 2. the module ----------------------------------------------------------
variables = read(MODULE + "/variables.tf")
main = read(MODULE + "/main.tf")
outputs = read(MODULE + "/outputs.tf")
for rel, text in ((MODULE + "/variables.tf", variables),
                  (MODULE + "/main.tf", main),
                  (MODULE + "/outputs.tf", outputs)):
    if text is None:
        refuse("module-missing", "%s is absent — the retirement cannot be assessed" % rel)

# 2a. the variable exists and DEFAULTS TO FALSE. This is the load-bearing half:
# with the default back at true, a default deploy re-creates the retired route.
var_block = hcl_block(variables, r'variable\s+"%s"\s*\{' % GATE_VAR) if variables else None
if variables is None:
    pass
elif var_block is None:
    refuse("variable-missing",
           "%s/variables.tf no longer declares variable %r, so the retired route "
           "is not gated by anything" % (MODULE, GATE_VAR))
else:
    default = re.search(r"(?m)^\s*default\s*=\s*(\S+)", var_block)
    if default is None:
        refuse("edge-route-default-on",
               "variable %r declares no default; it must default to false" % GATE_VAR)
    elif default.group(1) != "false":
        refuse("edge-route-default-on",
               "variable %r defaults to %s, not false — a default deploy would "
               "create the RETIRED GCP edge route again" % (GATE_VAR, default.group(1)))

# 2b. BOTH halves of the route resolve to that gate. A `count` may name the
# variable directly or a local that is itself defined in terms of it; anything
# else means the resource is created unconditionally.
locals_map = {}
locals_block = hcl_block(main, r"locals\s*\{") if main else None
if locals_block:
    for match in re.finditer(r"(?m)^\s*([a-z_][a-z0-9_]*)\s*=\s*(.+)$", locals_block):
        locals_map[match.group(1)] = match.group(2)


def resolves_to_gate(expr):
    """True when the expression is gated on the retired-route variable."""
    if GATE_VAR in expr:
        return True
    for name in re.findall(r"local\.([a-z_][a-z0-9_]*)", expr):
        if GATE_VAR in locals_map.get(name, ""):
            return True
    return False


if main is not None:
    for res_type, finding in (("google_dns_record_set", "dns-record-ungated"),
                              ("google_cloud_run_domain_mapping", "domain-mapping-ungated")):
        block = hcl_block(main, r'resource\s+"%s"\s+"web"\s*\{' % res_type)
        if block is None:
            refuse(finding,
                   "%s/main.tf no longer declares resource %r \"web\"" % (MODULE, res_type))
            continue
        count = re.search(r"(?m)^\s*count\s*=\s*(.+)$", block)
        if count is None:
            refuse(finding,
                   "%s \"web\" declares no count, so it is created unconditionally — "
                   "the RETIRED route would be created by a default deploy" % res_type)
        elif not resolves_to_gate(count.group(1)):
            refuse(finding,
                   "%s \"web\" count = %r does not resolve to %s (or to a local that "
                   "does), so the RETIRED route is created by a default deploy"
                   % (res_type, count.group(1).strip(), GATE_VAR))

    # 2c. the pre-existing-zone path is RESOLVED, not assumed: without the lookup
    # `create_dns_zone = false` named a zone nothing ever checked.
    if re.search(r'(?m)^\s*data\s+"google_dns_managed_zone"\s+"', main) is None:
        refuse("existing-zone-unresolved",
               "%s/main.tf has no `data \"google_dns_managed_zone\"` lookup, so the "
               "pre-existing-zone path names a zone it never resolves" % MODULE)

# 2d. the incoherent combination is refused BY NAME.
if variables is not None and VALIDATION_TOKEN not in variables:
    refuse("zone-validation-missing",
           "%s/variables.tf no longer carries the %r refusal, so "
           "create_dns_zone = true with the route retired is accepted silently"
           % (MODULE, VALIDATION_TOKEN))

# 2e. no output indexes a resource the gate can empty: `web[0]` is an INVALID
# INDEX at count 0, so the promoted posture would fail at plan time rather than
# simply not create the retired route.
if outputs is not None:
    for match in re.finditer(r"google_(?:cloud_run_domain_mapping|dns_record_set)"
                             r"\.web\s*\[\s*[0-9]+\s*\]", outputs):
        refuse("output-indexes-gated-resource",
               "%s/outputs.tf indexes a gated resource by position (%r); at count 0 "
               "that is an invalid index, so a promoted surface with the route "
               "retired fails at plan time" % (MODULE, match.group(0)))

for name, detail in findings:
    print("FINDING %s %s" % (name, detail))
sys.exit(0)
PY
}

# --- the mutations, ONE per property, applied to a scratch copy -------------
# mutate <fixture_root> <mutation> : rewrites the fixture in place and asserts the
# bytes CHANGED (a mutation that did not land proves nothing). rc 1 = NOOP/unknown.
mutate() {
  python3 - "$1" "$2" <<'PY'
import os
import re
import sys

fixture = sys.argv[1]
mutation = sys.argv[2]
MODULE = "infra/terraform/modules/web-surface"


def path(rel):
    return os.path.join(fixture, rel)


def edit(rel, transform):
    """Apply the transform, asserting it changed the bytes."""
    target = path(rel)
    with open(target, encoding="utf-8") as handle:
        before = handle.read()
    after = transform(before)
    if after == before:
        print("NOOP %s changed nothing in %s" % (mutation, rel))
        sys.exit(1)
    with open(target, "w", encoding="utf-8") as handle:
        handle.write(after)
    sys.exit(0)


def drop(rel):
    target = path(rel)
    if not os.path.exists(target):
        print("NOOP %s removed nothing: %s was already absent" % (mutation, rel))
        sys.exit(1)
    os.remove(target)
    sys.exit(0)


def var_default_true(text):
    """Flip the retired-route variable's default, anchored to ITS OWN block."""
    out = []
    inside = False
    for line in text.splitlines(True):
        if re.match(r'variable\s+"create_gcp_edge_route"', line):
            inside = True
        elif inside and line.startswith("}"):
            inside = False
        elif inside and re.match(r"\s*default\s*=", line):
            line = line.replace("false", "true")
        out.append(line)
    return "".join(out)


def inside_block(text, header_re, transform):
    """Apply the transform to ONE named `{...}` block and nothing else.

    A bare `count = 1` over the whole file is not enough here (measured): the
    file's FIRST `validation` block belongs to a different variable, so an
    unscoped deletion would mutate the wrong rule and the control would claim a
    proof it never had.
    """
    lines = text.splitlines(True)
    start = None
    for index, line in enumerate(lines):
        if re.match(header_re, line):
            start = index
            break
    if start is None:
        return text
    depth = 0
    end = len(lines) - 1
    for index in range(start, len(lines)):
        depth += lines[index].count("{") - lines[index].count("}")
        if depth <= 0 and index > start:
            end = index
            break
    block = "".join(lines[start:end + 1])
    return "".join(lines[:start]) + transform(block) + "".join(lines[end + 1:])


def ungated(res_type):
    """Point one resource's count at a local that is NOT gated."""
    def transform(text):
        out = []
        inside = False
        for line in text.splitlines(True):
            if re.match(r'resource\s+"%s"\s+"web"' % res_type, line):
                inside = True
            elif inside and line.startswith("}"):
                inside = False
            elif inside and re.match(r"\s*count\s*=", line):
                line = re.sub(r"count\s*=\s*.*", "count        = local.create", line)
            out.append(line)
        return "".join(out)
    return transform


MUTATIONS = {
    "declaration-missing": lambda: drop("docs/EDGE-CUTOVER.md"),
    "declaration-silent": lambda: edit(
        "docs/EDGE-CUTOVER.md", lambda text: text.replace("RETIRED", "still live")),
    "edge-route-default-on": lambda: edit(
        MODULE + "/variables.tf", var_default_true),
    "dns-record-ungated": lambda: edit(
        MODULE + "/main.tf", ungated("google_dns_record_set")),
    "domain-mapping-ungated": lambda: edit(
        MODULE + "/main.tf", ungated("google_cloud_run_domain_mapping")),
    "existing-zone-unresolved": lambda: edit(
        MODULE + "/main.tf",
        # The LOOKUP is dropped, not merely renamed: the resource type stops being
        # a DNS-zone lookup, which is what "the pre-existing zone is resolved"
        # means. Renaming only the label would leave the lookup in place and the
        # mutation would prove nothing.
        lambda text: text.replace('data "google_dns_managed_zone" "existing" {',
                                  'data "google_dns_managed_zone_elsewhere" "existing" {')),
    "zone-validation-missing": lambda: edit(
        MODULE + "/variables.tf", lambda text: text.replace("create-dns-zone-orphan", "x")),
    "output-indexes-gated-resource": lambda: edit(
        MODULE + "/outputs.tf",
        lambda text: text.replace("one(google_cloud_run_domain_mapping.web[*].name)",
                                  "google_cloud_run_domain_mapping.web[0].name")),
    # The behavioural mutant: the whole precondition RULE is deleted, so the
    # incoherent combination must STOP being refused. Removing only its token
    # would leave the rule firing with an unattributed message, which is a
    # different assertion (CANNOT-ASSESS) rather than a proof that the rule is
    # what refuses it. The deletion is scoped to the
    # create_dns_zone_orphan_guard resource — a `lifecycle.precondition` on an
    # unconditional resource, not a `variable` validation block (a variable's
    # own validation may only reference itself; this rule spans two
    # variables, issue #411/#1136).
    "incoherent-combination-accepted": lambda: edit(
        MODULE + "/variables.tf",
        lambda text: inside_block(
            text,
            r'resource\s+"terraform_data"\s+"create_dns_zone_orphan_guard"\s*\{',
            lambda block: re.sub(r"(?s)\n\s*precondition\s*\{.*?\n\s*\}", "", block, count=1))),
}

handler = MUTATIONS.get(mutation)
if handler is None:
    print("NOOP unknown mutation %r" % mutation)
    sys.exit(1)
handler()
PY
}

# --- the behavioural probe: a real, offline plan ----------------------------
tf_cache() {
  if [ -n "${TF_PLUGIN_CACHE_DIR:-}" ]; then
    printf '%s' "$TF_PLUGIN_CACHE_DIR"
  elif [ -d "$HOME/.terraform.d/plugin-cache" ]; then
    printf '%s' "$HOME/.terraform.d/plugin-cache"
  fi
}

# tf_write_root <scratch> <module_rel> <create_dns_zone> <create_gcp_edge_route>
tf_write_root() {
  cat > "$1/main.tf" <<TF
terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }
}

module "web_surface" {
  source                = "$2"
  enabled               = true
  name                  = "edge-probe-web"
  image                 = "us-central1-docker.pkg.dev/ao-probe/ao-images/portal:probe"
  project_id            = "ao-probe"
  create_dns_zone       = $3
  create_gcp_edge_route = $4
}
TF
}

# tf_plan <scratch> <log> : rc 0 when the plan completed offline, rc 1 otherwise.
tf_plan() {
  if ( cd "$1" && TF_DATA_DIR="$1/.tfd" terraform plan -no-color -input=false -refresh=false ) > "$2" 2>&1; then
    return 0
  fi
  return 1
}

# tf_probe <module_dir> <scratch> : prints FINDING/SKIP/CANNOT lines, rc 0/2.
tf_probe() {
  local module_dir="$1" scratch="$2"
  local cache
  cache="$(tf_cache)"
  if [ -z "$cache" ]; then
    echo "SKIP terraform plan probe (no local provider cache; offline plan unavailable)"
    return 0
  fi

  local rel
  rel="$(python3 -c 'import os,sys; print(os.path.relpath(sys.argv[1], sys.argv[2]))' "$module_dir" "$scratch")" || {
    echo "CANNOT the probe's module path could not be relativised"
    return 2
  }

  # The scratch root is emptied between probes so a stale lock or plan cannot
  # be read as this run's evidence.
  mkdir -p "$scratch" || {
    echo "CANNOT no scratch directory for the plan probe"
    return 2
  }
  rm -f "$scratch/main.tf" "$scratch/plan-default.log" "$scratch/plan-incoherent.log"

  # 1. The committed DEFAULT posture: surface ON, retired route OFF.
  tf_write_root "$scratch" "$rel" false false
  if ! ( cd "$scratch" && TF_DATA_DIR="$scratch/.tfd" terraform init -backend=false -plugin-dir="$cache" -input=false ) > "$scratch/init.log" 2>&1; then
    echo "CANNOT terraform init failed offline in the probe scratch (cause tail: $(tf_log_tail "$scratch/init.log"); full log: $(tf_preserve "$scratch/init.log"))"
    return 2
  fi
  if ! tf_plan "$scratch" "$scratch/plan-default.log"; then
    echo "CANNOT the default-posture plan did not complete offline (cause tail: $(tf_log_tail "$scratch/plan-default.log"); full log: $(tf_preserve "$scratch/plan-default.log"))"
    return 2
  fi

  # Vacuity guard: an empty plan would satisfy "no edge route" for the wrong
  # reason, so the surface itself must be planned.
  local adds
  adds="$(awk '/^Plan: /{print $2; exit}' "$scratch/plan-default.log")"
  if [ -z "$adds" ] || [ "$adds" -le 0 ]; then
    echo "CANNOT the default posture planned no resource at all (Plan: ${adds:-none}), so 'no edge route' is vacuous (cause tail: $(tf_log_tail "$scratch/plan-default.log"); full log: $(tf_preserve "$scratch/plan-default.log"))"
    return 2
  fi

  local res n
  for res in google_dns_record_set google_cloud_run_domain_mapping google_dns_managed_zone; do
    n="$(grep -c "$res" "$scratch/plan-default.log" || true)"
    if [ "${n:-0}" -ne 0 ]; then
      echo "FINDING edge-route-created-by-default the default posture (create_gcp_edge_route = false) plans $res — the RETIRED GCP edge route is created by a default deploy (${adds} resource(s) planned in total)"
    fi
  done

  # 2. The incoherent combination must be REFUSED, and the refusal must be
  #    ATTRIBUTABLE to the named rule rather than to any other error.
  tf_write_root "$scratch" "$rel" true false
  if tf_plan "$scratch" "$scratch/plan-incoherent.log"; then
    echo "FINDING incoherent-combination-accepted terraform plan accepted create_dns_zone = true with create_gcp_edge_route = false; the named refusal $VALIDATION_TOKEN no longer fires"
    return 0
  fi
  local incoherent
  incoherent="$(cat "$scratch/plan-incoherent.log")"
  case "$incoherent" in
    *"$VALIDATION_TOKEN"*) : ;;
    *)
      echo "CANNOT the incoherent combination was refused but NOT by name — $VALIDATION_TOKEN is absent from the refusal, so this probe cannot attribute it to the rule under test (refusal tail: $(tf_log_tail "$scratch/plan-incoherent.log"); full log: $(tf_preserve "$scratch/plan-incoherent.log"))"
      return 2
      ;;
  esac
  return 0
}

# --- reporting --------------------------------------------------------------
FOUND=0
CANNOT=0

report_stream() {
  local line name detail
  while IFS= read -r line; do
    case "$line" in
      FINDING\ *)
        name="${line#FINDING }"
        detail="${name#* }"
        name="${name%% *}"
        printf '  REFUSED  %s\n' "$name" >&2
        printf '           %s\n' "$detail" >&2
        FOUND=$((FOUND + 1))
        ;;
      SKIP\ *)
        printf '  SKIP  %s\n' "${line#SKIP }"
        ;;
      CANNOT\ *)
        printf '  CANNOT-ASSESS  %s\n' "${line#CANNOT }" >&2
        CANNOT=$((CANNOT + 1))
        ;;
      "")
        ;;
      *)
        printf '  %s\n' "$line"
        ;;
    esac
  done
}

# --- the provocation --------------------------------------------------------
# self_test : rc 0 when every property is provably load-bearing.
self_test() {
  local base="$SCRATCH/fixtures" pristine="$SCRATCH/pristine"
  local rc=0 mut out fixture missing probe_rc

  echo "== the properties, provoked =="
  mkdir -p "$pristine/$MODULE_REL" "$pristine/docs" || return 2
  cp -a "$root/$MODULE_REL/." "$pristine/$MODULE_REL/" || return 2
  cp -a "$root/$DECL_REL" "$pristine/$DECL_REL" || return 2

  # Half 1 — vacuity: the shipped files, unmutated, must produce NO finding. A
  # rule that matches everything cannot pass this half.
  out="$(analyse "$pristine")" || return 2
  if [ -n "$out" ]; then
    printf 'check-edge-cutover: FAIL — the UNMUTATED shipped files are refused, so a mutation cannot be told apart from the shipping state:\n%s\n' "$out" >&2
    rc=1
  else
    echo "  OK    the unmutated declaration and module produce no finding (vacuity)"
  fi

  # Half 2 — every property, one mutation at a time, refused BY NAME.
  local mutations="declaration-missing declaration-silent edge-route-default-on dns-record-ungated domain-mapping-ungated existing-zone-unresolved zone-validation-missing output-indexes-gated-resource"
  for mut in $mutations; do
    fixture="$base/$mut"
    mkdir -p "$fixture/$MODULE_REL" "$fixture/docs" || return 2
    cp -a "$pristine/$MODULE_REL/." "$fixture/$MODULE_REL/" || return 2
    cp -a "$pristine/$DECL_REL" "$fixture/$DECL_REL" || return 2

    mutate "$fixture" "$mut" > "$SCRATCH/mutate.log" 2>&1
    if [ $? -ne 0 ]; then
      printf 'check-edge-cutover: FAIL — the mutation for %s did not land:\n' "$mut" >&2
      cat "$SCRATCH/mutate.log" >&2
      rc=1
      continue
    fi

    out="$(analyse "$fixture")" || return 2
    case "$out" in
      *"FINDING $mut "*) echo "  OK    $mut is refused by name when mutated" ;;
      *)
        printf 'check-edge-cutover: FAIL — the mutation for %s was NOT refused by name; analysis said:\n%s\n' "$mut" "$out" >&2
        rc=1
        ;;
    esac
  done

  # Half 3 — the behavioural mutant: with the named refusal deleted, the
  # incoherent combination must STOP being refused.
  if [ -n "$(tf_cache)" ] && command -v terraform >/dev/null 2>&1; then
    fixture="$base/incoherent-combination-accepted"
    mkdir -p "$fixture/$MODULE_REL" "$fixture/docs" || return 2
    cp -a "$pristine/$MODULE_REL/." "$fixture/$MODULE_REL/" || return 2
    cp -a "$pristine/$DECL_REL" "$fixture/$DECL_REL" || return 2
    mkdir -p "$SCRATCH/tf-mutant" || return 2
    mutate "$fixture" "incoherent-combination-accepted" > "$SCRATCH/mutate.log" 2>&1
    if [ $? -ne 0 ]; then
      printf 'check-edge-cutover: FAIL — the behavioural mutation did not land:\n' >&2
      cat "$SCRATCH/mutate.log" >&2
      rc=1
    else
      out="$(tf_probe "$fixture/$MODULE_REL" "$SCRATCH/tf-mutant")"
      probe_rc=$?
      # Three MEANINGS, NAMED (#936). "the mutant was ACCEPTED", "the probe did
      # not complete", and "the probe completed and the mutant was still refused"
      # are three different facts: collapsing them into one rc 1 both deleted the
      # cause and reported a cannot-assess as if it were a finding about the rule.
      # The probe's OWN exit code decides which, because tf_probe returns 0 only
      # when it ran to a verdict (an EMPTY result with rc 0 means it ran and found
      # nothing, not that it failed to run).
      if [ "$probe_rc" -ne 0 ]; then
        printf 'check-edge-cutover: CANNOT-ASSESS [probe-incomplete] — the behavioural PROBE DID NOT COMPLETE (rc=%s), so the mutant was neither ACCEPTED nor refused: the gate proves nothing here and this is NOT a finding about the rule under test. The probe said (self-contained; the terraform log tail is inlined):\n%s\n' "$probe_rc" "${out:-<no output from the probe>}" >&2
        if [ "$rc" -eq 0 ]; then rc=2; fi
      elif [[ "$out" == *"FINDING incoherent-combination-accepted "* ]]; then
        echo "  OK    the mutant was ACCEPTED — with the refusal deleted the incoherent combination is accepted, so the behavioural half is load-bearing"
      else
        printf 'check-edge-cutover: NOT-OK [mutant-not-accepted] — the probe COMPLETED and the refusal SURVIVED with the rule deleted, so it does not come from %s: a real finding about the rule under test. The probe said:\n%s\n' "$VALIDATION_TOKEN" "${out:-<no output from the probe>}" >&2
        rc=1
      fi
    fi
  else
    echo "  SKIP  the behavioural mutant (no terraform or no local provider cache)"
  fi

  if [ "$rc" -eq 0 ]; then
    echo "check-edge-cutover: self-test OK — every property is refused by name, and the shipped state is not"
  fi
  return "$rc"
}

# --- the live probe (never on the default path) -----------------------------
live_probe() {
  if ! command -v dig >/dev/null 2>&1 || ! command -v curl >/dev/null 2>&1; then
    echo "check-edge-cutover: CANNOT-ASSESS — the live probe needs dig and curl on PATH" >&2
    return 2
  fi

  local zone="${EDGE_HOST#*.}"
  local ns a cname code server fronting acceptance

  a="$(dig +short "$EDGE_HOST" 2>/dev/null || true)"
  cname="$(dig +short CNAME "$EDGE_HOST" 2>/dev/null || true)"
  # An NS record set belongs to the ZONE, not to the hostname: asking for NS at
  # `ai.purebliss.app` is NODATA by construction (measured), which is exactly why
  # this probe derives the zone by walking up one label and never refuses on the
  # hostname's own NS answer. Resolution is judged on the A/CNAME answer.
  ns="$(dig +short NS "$zone" 2>/dev/null || true)"

  echo "== live probe: $EDGE_HOST ($(date -u +%FT%TZ)) =="
  printf '  A      : %s\n' "$(printf '%s' "$a" | tr '\n' ' ')"
  printf '  CNAME  : %s\n' "${cname:-<none>}"
  printf '  NS(%s): %s\n' "$zone" "$(printf '%s' "$ns" | tr '\n' ' ')"

  if [ -z "$a" ] && [ -z "$cname" ]; then
    echo "check-edge-cutover: CANNOT-ASSESS — $EDGE_HOST resolved to neither an A nor a CNAME record, so no fronting can be measured" >&2
    return 2
  fi

  # The headers go to a file rather than into a pipeline: a `grep -q` downstream
  # of a producer is the shape this repository refuses (#843, check-verdict-contains).
  curl -sS -I --max-time 20 "https://$EDGE_HOST/" > "$SCRATCH/live.headers" 2>&1 || true
  code="$(awk 'NR==1 && /^HTTP/{print $2; exit}' "$SCRATCH/live.headers")"
  # The header NAME is stripped so the reported value is the value, not the line
  # (`server: server: cloudflare` reads as a defect in the probe rather than in
  # the answer). The trailing CR of an HTTP header line goes with it.
  server="$(grep -i -m1 '^server:' "$SCRATCH/live.headers" || true)"
  server="${server%$'\r'}"
  server="${server#*: }"
  printf '  status : %s\n' "${code:-<none>}"
  printf '  server : %s\n' "${server:-<absent>}"

  # Two independent signals for the fronting, and the edge's own header wins:
  # the `server:` the hostname actually answered with, else the nameservers that
  # serve its zone. Neither is available => CANNOT-ASSESS, never a pass.
  fronting="unknown"
  case "$server" in
    *[Cc]loudflare*) fronting="cloudflare" ;;
  esac
  if [ "$fronting" = "unknown" ]; then
    case "$ns" in
      *cloudflare.com.*) fronting="cloudflare" ;;
      "") ;;
      *) fronting="other" ;;
    esac
  fi

  # The acceptance body: fleet JSON (met) versus anything else (unmet).
  curl -sS --max-time 20 "https://$EDGE_HOST/api/fleet/snapshot" > "$SCRATCH/live.body" 2>&1 || true
  printf '  body   : %s\n' "$(head -c 120 "$SCRATCH/live.body")"
  if python3 -c 'import json, sys
try:
    json.load(open(sys.argv[1], encoding="utf-8"))
except Exception:
    sys.exit(1)' "$SCRATCH/live.body"; then
    acceptance="met"
  else
    acceptance="unmet"
  fi
  printf '  fronting=%s (declared: cloudflare)  acceptance(/api/fleet/snapshot JSON)=%s\n' "$fronting" "$acceptance"

  if [ "$fronting" != "cloudflare" ]; then
    echo "check-edge-cutover: NOT-OK — the measured fronting ($fronting) contradicts the declaration (cloudflare)" >&2
    return 1
  fi
  if [ "$acceptance" != "met" ]; then
    echo "check-edge-cutover: NOT-OK — the fronting matches the declaration, but the hostname does not yet serve the SPoG fleet JSON. This is the RUN HALF's obligation (docs/EDGE-CUTOVER.md section 5), not a defect in the declaration. No cutover has happened." >&2
    return 1
  fi
  echo "check-edge-cutover: OK — the fronting matches the declaration and the SPoG answers"
  return 0
}

# --- argument handling ------------------------------------------------------
if ! command -v python3 >/dev/null 2>&1; then
  echo "check-edge-cutover: CANNOT-ASSESS — python3 is not on PATH" >&2
  exit 2
fi

mode="gate"
while [ $# -gt 0 ]; do
  case "$1" in
    --self-test) mode="self-test"; shift ;;
    --live) mode="live"; shift ;;
    --root) root="${2:?--root needs a directory}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'check-edge-cutover: unknown argument: %s (see --help)\n' "$1" >&2; exit 2 ;;
  esac
done

SCRATCH="/tmp/ao731-edge-cutover.$$.$(date +%s)"
if ! mkdir -p "$SCRATCH"; then
  echo "check-edge-cutover: CANNOT-ASSESS — no scratch directory for the provocation" >&2
  exit 2
fi

case "$mode" in
  self-test)
    self_test
    exit $?
    ;;
  live)
    live_probe
    exit $?
    ;;
esac

echo "== edge cutover (issue #731) =="
echo "  declaration : $DECL_REL"
echo "  module      : $MODULE_REL"
echo "  host        : $EDGE_HOST (live probe only; the default path is offline)"

self_test
self_test_rc=$?
if [ "$self_test_rc" -eq 2 ]; then
  echo "check-edge-cutover: CANNOT-ASSESS — the gate could not prove itself and could not COMPLETE (the named CANNOT-ASSESS reason is above), so its verdict means nothing" >&2
  exit 2
fi
if [ "$self_test_rc" -ne 0 ]; then
  echo "check-edge-cutover: FAILED — the gate could not prove itself, so its verdict means nothing" >&2
  exit 1
fi

echo "== the retired route, behaviourally =="
probe_out="$(tf_probe "$root/$MODULE_REL" "$SCRATCH/tf")"
probe_rc=$?
report_stream <<< "$probe_out"
if [ "$probe_rc" -eq 2 ]; then
  CANNOT=$((CANNOT + 1))
fi

echo "== the declaration and the module =="
analysis_out="$(analyse "$root")"
analysis_rc=$?
if [ "$analysis_rc" -eq 2 ]; then
  report_stream <<< "$analysis_out"
  CANNOT=$((CANNOT + 1))
elif [ "$analysis_rc" -ne 0 ]; then
  echo "check-edge-cutover: CANNOT-ASSESS — the analysis did not run (rc=$analysis_rc)" >&2
  exit 2
else
  if [ -z "$analysis_out" ]; then
    echo "  OK    the declaration, the route gate and both gated resources are coherent"
  else
    report_stream <<< "$analysis_out"
  fi
fi

echo "--"
if [ "$FOUND" -ne 0 ]; then
  echo "check-edge-cutover: FAILED — $FOUND refused property(ies); see the names above" >&2
  exit 1
fi
if [ "$CANNOT" -ne 0 ]; then
  echo "check-edge-cutover: CANNOT-ASSESS — $CANNOT property(ies) could not be assessed; this is not a pass" >&2
  exit 2
fi
echo "check-edge-cutover: OK — the fronting is declared, the RETIRED GCP edge route is not created by default, and every property is provably load-bearing"
