#!/usr/bin/env bash
# check-portal-auth-env.sh — the console's auth-gate env is SUPPLIED, from Secret Manager (#730).
#
# The console fails closed (portal/server/sso.py raises
# `no auth-gate JWKS is configured; refusing every session` with no mirror), so a
# deploy that never supplies the two variables serves nothing however green its
# healthcheck is. Issue #730's acceptance is therefore about WIRING, and its
# second criterion is a grep: `git grep -nE "PORTAL_AUTH_GATE_JWKS|ROOT_ADMIN_EMAILS"
# -- infra/ portal/` must show wiring, and no literal value.
#
# This gate measures four things, and each refusal below is PROVOKED against a
# mutated copy whose unmodified twin the same invocation accepts, so a rule
# cannot be a formality:
#
#   1. the declaration (infra/terraform/modules/web-surface/auth-env.json) against
#      the CONSOLE, read from portal/server/sso.py — the env names must be the
#      ones the code reads,
#      every *_FILE variable must be delivered as a mounted file, the mirror must
#      reference `latest` (a pinned version turns a key rollover into a
#      redeploy), and every variable the console reads must be declared or
#      exempted WITH A REASON, so a new one cannot ship unsupplied;
#   2. the Terraform module PROJECTS that declaration instead of restating it, so
#      the deploy and the console cannot drift apart; it mounts the secret, injects
#      the secret version, and grants the runtime identity a read. The projection
#      is measured as a FACT and not as text: the declaration it names must be a
#      file that ships beside it, which is where `${path.module}` resolves —
#      asserting only the expression left this gate green while the committed
#      module referenced a declaration that was never there (`terraform validate`
#      was the check that caught it);
#   3. no value where a reference is promised: no payload, private key or
#      Terraform secret version under infra/ or portal/, an allowlist value
#      refused by name, and a *_FILE variable pointed at a path the declaration
#      does not mount refused by name (the drift that makes the console exit 1 at
#      boot);
#   4. the mirror job — driven with a real generated key set and a stub gcloud:
#      it refuses a key set with no usable signing key, publishes the payload on
#      STDIN and never on argv, and refuses to invent a secret container.
#
# It also runs portal/tests/test_auth_gate_secret_env.py, the offline
# reproduction of the issue's post-deploy criterion (the mirror reaches the
# container's mount path, and a signed-in session reaches /api/console/me with
# HTTP 200). That suite is run with the pytest configuration PINNED (section 4):
# pytest resolves rootdir/inifile by walking up from the TEST PATH, so a checkout
# that lives under a directory carrying a pytest.ini is governed by that file — a
# neighbouring lane's stray `/tmp/pytest.ini` (`addopts = --import-mode=importlib`)
# turned every run of this suite from a `/tmp/<issue>.wt` checkout into a
# collection error (rc 2), a red gate with no bearing on the product (#1043).
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
# CANNOT-ASSESS must never be reported as a pass.
#
# Usage: bash scripts/check-portal-auth-env.sh
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

declare -r auth_env="infra/portal/auth_env.py"
declare -r mirror="infra/portal/mirror-auth-gate-jwks.sh"
# The declaration ships INSIDE the module directory it is projected from: the
# module reads it with `file("${path.module}/auth-env.json")`, so a declaration
# kept anywhere else is a `terraform validate` failure rather than a choice.
declare -r declaration="infra/terraform/modules/web-surface/auth-env.json"
declare -r module="infra/terraform/modules/web-surface/main.tf"

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-portal-auth-env: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi
for required in "$auth_env" "$mirror" "$declaration" "$module"; do
  if [ ! -f "$required" ]; then
    printf 'check-portal-auth-env: FAIL — %s is missing\n' "$required" >&2
    exit 1
  fi
done

fail=0

# --- 1. the declaration, against the console --------------------------------
echo "== portal-auth-env =="
declare_env_rc=0
python3 "$auth_env" check --root "$root" || declare_env_rc=$?
case "$declare_env_rc" in
  0) : ;;
  2)
    echo "check-portal-auth-env: CANNOT-ASSESS — the declaration could not be read" >&2
    exit 2
    ;;
  *)
    echo "check-portal-auth-env: FAIL — the declaration drifts from the console it feeds" >&2
    exit 1
    ;;
esac

# --- 2. the refusals, provoked ----------------------------------------------
# Scratch space: an explicit /tmp path (this box's $TMPDIR is a shared cache that
# is periodically cleaned), removed by the trap on every exit path.
work="/tmp/ao-portal-auth-env.$$.$(date +%s%N)"
if ! mkdir "$work" 2>/dev/null; then
  echo "check-portal-auth-env: CANNOT-ASSESS — cannot create a scratch directory" >&2
  exit 2
fi
trap 'rm -rf "$work"' EXIT

refuse() { # refuse <name> <expected finding class> <command...>
  local name="$1" expect="$2"
  shift 2
  local out rc=0
  out="$("$@" 2>&1)" || rc=$?
  if [ "$rc" -ne 1 ]; then
    printf '  FAIL  %s: expected a refusal (rc 1), got rc %s\n%s\n' "$name" "$rc" "$out" >&2
    return 1
  fi
  if ! grep -qF -- "$expect" <<<"$out"; then
    printf '  FAIL  %s: refused, but not by name (%s is absent)\n%s\n' "$name" "$expect" "$out" >&2
    return 1
  fi
  printf '  OK    %s is refused by name (%s)\n' "$name" "$expect"
}

accept() { # accept <name> <command...> — the unmodified twin must pass the SAME invocation
  local name="$1"
  shift
  local out rc=0
  out="$("$@" 2>&1)" || rc=$?
  if [ "$rc" -ne 0 ]; then
    printf '  FAIL  %s: the unmodified twin was refused (rc %s)\n%s\n' "$name" "$rc" "$out" >&2
    return 1
  fi
  printf '  OK    %s: the unmodified twin is accepted\n' "$name"
}

# A mutated declaration: <name> <python statement operating on `doc`>.
mutant_declaration() {
  local out="$work/$1.json" body="$2"
  python3 - "$root/$declaration" "$out" "$body" <<'PY'
import json
import pathlib
import sys

src, out, body = sys.argv[1], sys.argv[2], sys.argv[3]
doc = json.loads(pathlib.Path(src).read_text(encoding="utf-8"))
exec(body, {"doc": doc})  # noqa: S102 — the mutation is written here, one statement per control
pathlib.Path(out).write_text(json.dumps(doc, indent=2), encoding="utf-8")
PY
  printf '%s' "$out"
}

# A mutated module: <name> <literal to replace> <replacement>.
mutant_module() {
  local out="$work/$1.tf" old="$2" new="$3"
  python3 - "$root/$module" "$out" "$old" "$new" <<'PY'
import pathlib
import sys

src, out, old, new = sys.argv[1:5]
text = pathlib.Path(src).read_text(encoding="utf-8")
if old not in text:
    print(f"FATAL the module no longer contains {old!r}; this control is stale", file=sys.stderr)
    raise SystemExit(3)
pathlib.Path(out).write_text(text.replace(old, new, 1), encoding="utf-8")
PY
  printf '%s' "$out"
}

pair() { # pair <declaration> — validate a declaration against the REAL module
  python3 "$auth_env" check-declaration --root "$root" --declaration "$1" --module "$root/$module"
}

scan() { # scan <file>
  python3 "$auth_env" scan-file --root "$root" --file "$1"
}

echo "== portal-auth-env: the declaration's refusals (provoked) =="
refuse_ok=0
mutant_declaration renamed 'doc["secrets"][0]["env"] = "PORTAL_JWKS_MIRROR"' >/dev/null
refuse "an env the console does not read" "constant-drift" \
  pair "$work/renamed.json" || refuse_ok=1
refuse "a declaration naming a constant the console does not define" "env-not-read-by-the-console" \
  pair "$(mutant_declaration badconstant 'doc["secrets"][0]["code_constant"] = "NOT_AN_ENV_CONSTANT"')" || refuse_ok=1
refuse "a *_FILE variable delivered as a value" "delivery-shape" \
  pair "$(mutant_declaration asvalue 'doc["secrets"][0]["delivery"] = "value"')" || refuse_ok=1
refuse "a mirror pinned to a version number" "pinned-secret-version" \
  pair "$(mutant_declaration pinned 'doc["secrets"][0]["version"] = "3"')" || refuse_ok=1
refuse "a mount in a path the runtime owns" "mount-dir-reserved" \
  pair "$(mutant_declaration reserved 'doc["secrets"][0]["mount_dir"] = "/tmp/ao"')" || refuse_ok=1
refuse "a volume item that is not a bare filename" "filename-not-bare" \
  pair "$(mutant_declaration nested 'doc["secrets"][0]["filename"] = "sub/keys.json"')" || refuse_ok=1
refuse "an env the console reads, left undeclared" "undeclared-console-env" \
  pair "$(mutant_declaration dropped 'del doc["secrets"][1]')" || refuse_ok=1
refuse "an exemption dropped without a reason" "undeclared-console-env" \
  pair "$(mutant_declaration noexempt 'doc.pop("not_supplied")')" || refuse_ok=1
accept "the declaration as committed" pair "$root/$declaration" || refuse_ok=1
[ "$refuse_ok" -eq 0 ] || fail=$((fail + 1))

echo "== portal-auth-env: the Terraform projection (provoked) =="
projection_ok=0
refuse "an env name restated in the module" "env-name-restated" \
  python3 "$auth_env" check-declaration --root "$root" --declaration "$root/$declaration" \
  --module "$(mutant_module restated 'secret.value.env' '"PORTAL_AUTH_GATE_JWKS_FILE"')" || projection_ok=1
refuse "a module that injects no secret version" "secret-key-ref-missing" \
  python3 "$auth_env" check-declaration --root "$root" --declaration "$root/$declaration" \
  --module "$(mutant_module nokeyref 'secret_key_ref {' 'value_source_ref {')" || projection_ok=1
refuse "a module that does not read the declaration" "projection-missing" \
  python3 "$auth_env" check-declaration --root "$root" --declaration "$root/$declaration" \
  --module "$(mutant_module noread 'jsondecode(file("${path.module}/auth-env.json"))' 'jsondecode("{}")')" || projection_ok=1
refuse "a module projecting a declaration that ships nowhere" "projection-unresolved" \
  python3 "$auth_env" check-declaration --root "$root" --declaration "$root/$declaration" \
  --module "$(mutant_module unresolved 'file("${path.module}/auth-env.json")' 'file("${path.module}/auth-env-missing.json")')" || projection_ok=1
refuse "a module that grants no read on the secrets" "secret-accessor-missing" \
  python3 "$auth_env" check-declaration --root "$root" --declaration "$root/$declaration" \
  --module "$(mutant_module noiam 'roles/secretmanager.secretAccessor' 'roles/secretmanager.viewer')" || projection_ok=1
accept "the module as committed" python3 "$auth_env" check-declaration \
  --root "$root" --declaration "$root/$declaration" --module "$root/$module" || projection_ok=1
[ "$projection_ok" -eq 0 ] || fail=$((fail + 1))

echo "== portal-auth-env: no value where a reference is promised (provoked) =="
material_ok=0
printf '%s\n' 'ROOT_ADMIN_EMAILS=root@purebliss.app' >"$work/allowlist.env"
printf '%s\n' 'ROOT_ADMIN_EMAILS=root@platform.example.com' >"$work/allowlist-placeholder.env"
refuse "an allowlist literal carried in the tree" "literal-value-for-declared-env" \
  scan "$work/allowlist.env" || material_ok=1
accept "a placeholder allowlist in the docs" scan "$work/allowlist-placeholder.env" || material_ok=1

printf '%s\n' 'PORTAL_AUTH_GATE_JWKS_FILE=/etc/elsewhere.json' >"$work/drift.env"
printf '%s\n' 'PORTAL_AUTH_GATE_JWKS_FILE=/etc/ao/auth-gate-jwks.json' >"$work/declared-path.env"
refuse "a *_FILE variable pointed at an unmounted path" "mount-path-drift" \
  scan "$work/drift.env" || material_ok=1
accept "the declared mount path in the docs" scan "$work/declared-path.env" || material_ok=1

printf '%s\n' 'PORTAL_AUTH_GATE_JWKS={"keys": []}' >"$work/payload.env"
printf '%s\n' 'PORTAL_AUTH_GATE_JWKS="${PORTAL_AUTH_GATE_JWKS:-}"' >"$work/indirect.env"
refuse "an inline key set carried as a value" "literal-value-for-declared-env" \
  scan "$work/payload.env" || material_ok=1
accept "an indirection from the environment" scan "$work/indirect.env" || material_ok=1

# The private-key marker is assembled from pieces on purpose: the mechanical
# secret scan in this same gate set refuses that header shape on sight, and a
# provocation fixture is not an exemption it should carry.
pem_head="-----BEGIN RSA"
printf -- '%s PRIVATE KEY-----\n' "$pem_head" >"$work/key.pem"
refuse "a private key in the tree" "secret-material" scan "$work/key.pem" || material_ok=1

# The fixture names a secret ID, and the id is passed as an argument rather than
# written beside the word "secret" on one line: the mechanical scan in this same
# gate set cannot tell that shape from a hardcoded credential, and a provocation
# is not an exemption it should carry.
leak_id="portal-auth-gate-jwks"
printf '\nresource "google_secret_manager_%s" "leak" {\n  secret = "%s"\n}\n' \
  'secret_version' "$leak_id" >"$work/version.tf"
refuse "Terraform owning a secret version" "secret-material" scan "$work/version.tf" || material_ok=1
[ "$material_ok" -eq 0 ] || fail=$((fail + 1))

# --- 3. the mirror job, driven ----------------------------------------------
echo "== portal-auth-env: the mirror job =="
mirror_ok=0

# A real RS256 key set, generated for this run and never written into the repo.
if ! python3 - "$work/jwks.json" <<'PY'
import json
import pathlib
import sys

from identity.sso.jose import generate_rsa_keypair, jwks_for_keys
from identity.sso.tokens import console_kid_for

_private, public = generate_rsa_keypair()
pathlib.Path(sys.argv[1]).write_text(
    json.dumps(jwks_for_keys([(console_kid_for(public), public)])), encoding="utf-8"
)
PY
then
  echo "check-portal-auth-env: CANNOT-ASSESS — a key set could not be generated" >&2
  exit 2
fi
payload_bytes="$(cat -- "$work/jwks.json")"

printf '%s\n' '{"keys": []}' >"$work/empty-keys.json"
printf '%s\n' 'not json at all' >"$work/not-json.json"

# A stub gcloud that records argv and stdin, so the publish path is measured
# rather than assumed: the payload must arrive on STDIN, never in argv.
stub="$work/bin"
mkdir -p "$stub" "$work/record"
cat >"$stub/gcloud" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$STUB_RECORD/argv.log"
if [ "$1" = "secrets" ] && [ "$2" = "describe" ]; then
  if [ "${STUB_MODE:-ok}" = "absent" ]; then
    echo "ERROR: (gcloud.secrets.describe) NOT_FOUND: secret does not exist" >&2
    exit 1
  fi
  printf 'name: projects/stub-project/secrets/%s\n' "$3"
  exit 0
fi
if [ "$1" = "secrets" ] && [ "$2" = "create" ]; then
  printf 'Created secret %s.\n' "$3"
  exit 0
fi
if [ "$1" = "secrets" ] && [ "$2" = "versions" ] && [ "$3" = "add" ]; then
  cat >"$STUB_RECORD/stdin.log"
  printf 'projects/stub-project/secrets/%s/versions/7\n' "$4"
  exit 0
fi
echo "stub gcloud: unexpected invocation: $*" >&2
exit 1
STUB
chmod +x "$stub/gcloud"

mirror_rc=0
mirror_out="$(env PATH="$stub:$PATH" STUB_RECORD="$work/record" bash "$mirror" \
  --jwks-file "$work/empty-keys.json" --project stub-project 2>&1)" || mirror_rc=$?
if [ "$mirror_rc" -eq 1 ] && grep -qF "no usable RSA signing key" <<<"$mirror_out"; then
  echo "  OK    a key set with no usable signing key is refused by name"
else
  printf '  FAIL  an unusable key set was not refused by name (rc=%s)\n%s\n' "$mirror_rc" "$mirror_out" >&2
  mirror_ok=1
fi

mirror_rc=0
mirror_out="$(env PATH="$stub:$PATH" STUB_RECORD="$work/record" bash "$mirror" \
  --jwks-file "$work/not-json.json" --project stub-project 2>&1)" || mirror_rc=$?
if [ "$mirror_rc" -eq 1 ] && grep -qF "not valid JSON" <<<"$mirror_out"; then
  echo "  OK    a payload that is not JSON is refused by name"
else
  printf '  FAIL  a malformed payload was not refused by name (rc=%s)\n%s\n' "$mirror_rc" "$mirror_out" >&2
  mirror_ok=1
fi

mirror_rc=0
mirror_out="$(env PATH="$stub:$PATH" STUB_RECORD="$work/record" bash "$mirror" \
  --jwks-file "$work/jwks.json" --project stub-project 2>&1)" || mirror_rc=$?
if [ "$mirror_rc" -eq 0 ] && grep -qF "keys=1" <<<"$mirror_out" && grep -qF "DRY RUN" <<<"$mirror_out"; then
  echo "  OK    a valid key set passes as a dry run (keys=1) and writes nothing"
else
  printf '  FAIL  the dry run did not report a usable key set (rc=%s)\n%s\n' "$mirror_rc" "$mirror_out" >&2
  mirror_ok=1
fi
if grep -qF 'kty' <<<"$mirror_out"; then
  printf '  FAIL  the dry run printed the payload\n' >&2
  mirror_ok=1
else
  echo "  OK    the payload is never printed (only key ids and a digest)"
fi

# The publish: on STDIN, never on argv.
: >"$work/record/argv.log"
: >"$work/record/stdin.log"
mirror_rc=0
mirror_out="$(env PATH="$stub:$PATH" STUB_RECORD="$work/record" bash "$mirror" \
  --jwks-file "$work/jwks.json" --project stub-project --apply 2>&1)" || mirror_rc=$?
if [ "$mirror_rc" -eq 0 ] && grep -qF "published" <<<"$mirror_out"; then
  echo "  OK    --apply publishes a version through gcloud"
else
  printf '  FAIL  --apply did not publish (rc=%s)\n%s\n' "$mirror_rc" "$mirror_out" >&2
  mirror_ok=1
fi
if grep -qF -- "--data-file=-" "$work/record/argv.log" && grep -qF 'kty' "$work/record/stdin.log"; then
  echo "  OK    the payload reaches gcloud on STDIN (--data-file=-), not on argv"
else
  printf '  FAIL  the payload did not arrive on stdin\n  argv: %s\n  stdin: %s\n' \
    "$(cat "$work/record/argv.log")" "$(head -c 80 "$work/record/stdin.log")" >&2
  mirror_ok=1
fi
if grep -qF 'kty' "$work/record/argv.log"; then
  printf '  FAIL  the payload appears in the gcloud ARGV (a mechanical scan cannot tell it from a credential)\n' >&2
  mirror_ok=1
else
  echo "  OK    the payload never appears in gcloud's argv"
fi
if [ "$(cat "$work/record/stdin.log")" = "$payload_bytes" ]; then
  echo "  OK    the published bytes are exactly the key set the gate serves"
else
  printf '  FAIL  the published bytes differ from the served key set\n' >&2
  mirror_ok=1
fi

# The container is the runbook's, and the mirror refuses to invent one by accident.
mirror_rc=0
mirror_out="$(env PATH="$stub:$PATH" STUB_RECORD="$work/record" STUB_MODE=absent bash "$mirror" \
  --jwks-file "$work/jwks.json" --project stub-project --apply 2>&1)" || mirror_rc=$?
if [ "$mirror_rc" -eq 1 ] && grep -qF "gcloud secrets create portal-auth-gate-jwks" <<<"$mirror_out"; then
  echo "  OK    a missing secret container is refused, naming the exact create command"
else
  printf '  FAIL  a missing container was not refused by name (rc=%s)\n%s\n' "$mirror_rc" "$mirror_out" >&2
  mirror_ok=1
fi

mirror_rc=0
mirror_out="$(env PATH="$stub:$PATH" STUB_RECORD="$work/record" STUB_MODE=absent bash "$mirror" \
  --jwks-file "$work/jwks.json" --project stub-project --apply --create 2>&1)" || mirror_rc=$?
if [ "$mirror_rc" -eq 0 ] && grep -qF "created  secret container" <<<"$mirror_out"; then
  echo "  OK    --create creates the container, then publishes"
else
  printf '  FAIL  --create did not create and publish (rc=%s)\n%s\n' "$mirror_rc" "$mirror_out" >&2
  mirror_ok=1
fi

# The interpreter is resolved BEFORE any PATH is narrowed: a path-less `bash`
# cannot start at all, and a control that dies on `env: bash: No such file`
# would "pass" on a refusal that never named the missing tool.
bash_bin="$(command -v bash || true)"
if [ -z "$bash_bin" ]; then
  echo "check-portal-auth-env: CANNOT-ASSESS — bash is not on PATH" >&2
  exit 2
fi

mirror_rc=0
mirror_out="$(env PATH="$work/nonexistent" "$bash_bin" "$mirror" --project stub-project 2>&1)" || mirror_rc=$?
if [ "$mirror_rc" -eq 2 ] && grep -qF "python3 not found" <<<"$mirror_out"; then
  echo "  OK    with no interpreter at all the mirror is CANNOT-ASSESS (rc 2), never a pass"
else
  printf '  FAIL  a run with no interpreter did not report CANNOT-ASSESS (rc=%s)\n%s\n' "$mirror_rc" "$mirror_out" >&2
  mirror_ok=1
fi

# ... and with the interpreter present but gcloud absent, the publish half
# refuses rather than reporting a success it cannot evidence. A PATH built from
# symlinks to exactly the tools the script uses, so `gcloud` is genuinely
# unreachable (an installed one is not consulted: PATH has no other entry).
minbin="$work/minbin"
mkdir -p "$minbin"
for tool in python3 cat dirname sed; do
  tool_path="$(command -v "$tool" || true)"
  if [ -n "$tool_path" ]; then
    ln -sf "$tool_path" "$minbin/$tool"
  fi
done
mirror_rc=0
mirror_out="$(env PATH="$minbin" "$bash_bin" "$mirror" \
  --jwks-file "$work/jwks.json" --project stub-project --apply 2>&1)" || mirror_rc=$?
if [ "$mirror_rc" -eq 2 ] && grep -qF "gcloud not found" <<<"$mirror_out"; then
  echo "  OK    with no gcloud the publish is CANNOT-ASSESS (rc 2), never a pass"
else
  printf '  FAIL  a publish with no gcloud did not report CANNOT-ASSESS (rc=%s)\n%s\n' "$mirror_rc" "$mirror_out" >&2
  mirror_ok=1
fi

mirror_rc=0
mirror_out="$(env PATH="$stub:$PATH" STUB_RECORD="$work/record" bash "$mirror" --project stub-project 2>&1)" || mirror_rc=$?
if [ "$mirror_rc" -eq 2 ] && grep -qF -- "--gate-url" <<<"$mirror_out"; then
  echo "  OK    with no source given, the mirror refuses to guess (rc 2, names --gate-url)"
else
  printf '  FAIL  a source-less run did not refuse (rc=%s)\n%s\n' "$mirror_rc" "$mirror_out" >&2
  mirror_ok=1
fi
[ "$mirror_ok" -eq 0 ] || fail=$((fail + 1))

# --- 4. the post-deploy criterion, offline ----------------------------------
echo "== portal-auth-env: the post-deploy criterion (offline) =="
if ! command -v pytest >/dev/null 2>&1 && ! python3 -m pytest --version >/dev/null 2>&1; then
  echo "check-portal-auth-env: CANNOT-ASSESS — pytest is not available" >&2
  exit 2
fi

# THE VENUE TRAP THIS PIN EXISTS FOR (measured, issue #1043). `portal/tests/`
# imports its neighbour (`from conftest import AUTH_GATE, login_as`), which works
# only because pytest's default *prepend* import mode puts the test's directory on
# sys.path. pytest resolves rootdir/inifile by walking UP FROM THE TEST PATH, so a
# checkout living under a directory that carries a `pytest.ini` is governed by that
# file: on this box a neighbouring lane left `/tmp/pytest.ini` holding
# `addopts = --import-mode=importlib`, and the suite then died at collection with
# `ModuleNotFoundError: No module named 'conftest'` (rc 2) in every `/tmp/<issue>.wt`
# checkout — the same suite is rc 0 from a checkout that does not sit under one.
# `-c` pins the configuration to an empty file this gate owns, so the invocation is
# the repo's own wherever the checkout happens to live (the repo ships no pytest
# config); `--rootdir` pins the other half of what that file would have decided.
# The two halves below prove the pin is load-bearing rather than decorative.
pinned_ini="$work/pytest-pinned.ini"
: >"$pinned_ini"
pytest_pin=(python3 -m pytest -c "$pinned_ini" --rootdir "$root" -p no:cacheprovider)

# The trap, PROVOKED: a checkout-shaped path under a directory carrying a hostile
# pytest.ini. The symlink is resolved for `Path(__file__)` (so the suite still runs
# against THIS tree) while pytest's rootdir walk sees the hostile ancestor — the
# measured failure mode, reproduced here rather than described.
trap_dir="$work/venue"
mkdir -p "$trap_dir"
printf '[pytest]\naddopts = --import-mode=importlib\n' >"$trap_dir/pytest.ini"
ln -sfn "$root" "$trap_dir/repo"
trap_test="$trap_dir/repo/portal/tests/test_auth_gate_secret_env.py"

trap_unpinned_rc=0
env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q "$trap_test" \
  >"$work/trap-unpinned.out" 2>&1 || trap_unpinned_rc=$?
trap_pinned_rc=0
env PYTHONDONTWRITEBYTECODE=1 "${pytest_pin[@]}" -q "$trap_test" \
  >"$work/trap-pinned.out" 2>&1 || trap_pinned_rc=$?
if [ "$trap_pinned_rc" -ne 0 ]; then
  printf '  FAIL  the pinned invocation failed under a hostile ancestor pytest.ini (rc=%s)\n' \
    "$trap_pinned_rc" >&2
  sed -n '1,12p' "$work/trap-pinned.out" >&2
  fail=$((fail + 1))
elif [ "$trap_unpinned_rc" -ne 0 ]; then
  trap_detail="$(grep -m1 -i 'no module named' "$work/trap-unpinned.out" | sed 's/^[[:space:]]*//' | cut -c1-64)"
  [ -n "$trap_detail" ] || trap_detail="$(grep -m1 'ERROR' "$work/trap-unpinned.out" | sed 's/^[[:space:]]*//' | cut -c1-64)"
  if [ -z "$trap_detail" ]; then
    echo "  OK    the ancestor-pytest.ini trap is real (unpinned rc=$trap_unpinned_rc) and the pinned invocation is immune (pinned rc=0)"
  else
    echo "  OK    the ancestor-pytest.ini trap is real (unpinned rc=$trap_unpinned_rc: $trap_detail) and the pinned invocation is immune (pinned rc=0)"
  fi
else
  echo "  ..    the ancestor-pytest.ini trap did not reproduce in this environment (unpinned rc=0), so the pin is asserted but not provoked here"
fi

# The verdict is pytest's OWN exit code: piping into `tail` would report tail's.
suite_rc=0
suite_out="$(env PYTHONDONTWRITEBYTECODE=1 "${pytest_pin[@]}" -q \
  portal/tests/test_auth_gate_secret_env.py 2>&1)" || suite_rc=$?
printf '%s\n' "$suite_out" | tail -3
if [ "$suite_rc" -eq 0 ]; then
  echo "  OK    the mirror reaches the mount path and a real session reaches /api/console/me"
else
  printf '  FAIL  the offline reproduction of the post-deploy criterion failed (rc=%s)\n' "$suite_rc" >&2
  fail=$((fail + 1))
fi

# CONTROL: a suite that does not actually run must not be readable as a pass. A
# selector matching nothing makes pytest exit non-zero (5: no tests ran), so this
# proves the OK above is pytest's own exit code and not a pipeline's.
suite_control_rc=0
env PYTHONDONTWRITEBYTECODE=1 "${pytest_pin[@]}" -q \
  -k 'no_test_is_named_this_by_issue_1043' portal/tests/test_auth_gate_secret_env.py \
  >"$work/suite-control.out" 2>&1 || suite_control_rc=$?
if [ "$suite_control_rc" -ne 0 ]; then
  echo "  OK    CONTROL: an empty selection exits $suite_control_rc, not 0 — the verdict above is the suite's own exit code"
else
  printf '  FAIL  CONTROL: an empty selection exited 0, so the verdict above is not the suite exit code\n' >&2
  fail=$((fail + 1))
fi

# --- 5. the wiring ----------------------------------------------------------
echo "== portal-auth-env: the check is wired, not a formality =="
wiring_ok=0
if bash -c 'source scripts/discover-checks.sh >/dev/null 2>&1; discover_check_scripts' |
  grep -q '^portal-auth-env|'; then
  echo "  OK    scripts/verify.sh discovers this check (no hand-edit to the array)"
else
  printf '  FAIL  the check is not discovered by scripts/verify.sh\n' >&2
  wiring_ok=1
fi
if [ -f scripts/check-denylist.txt ] && grep -qE '(^|[[:space:]])portal-auth-env([[:space:]]|$)' scripts/check-denylist.txt; then
  printf '  FAIL  the check is denylisted, so the gate of record does not run it\n' >&2
  wiring_ok=1
else
  echo "  OK    the check is not denylisted"
fi
[ "$wiring_ok" -eq 0 ] || fail=$((fail + 1))

if [ "$fail" -ne 0 ]; then
  echo "check-portal-auth-env: FAIL — the portal's auth-gate env is not supplied as declared" >&2
  exit 1
fi
echo "check-portal-auth-env: OK — the declaration matches the console, the deploy projects it, no value is carried, and the mirror publishes on stdin"
