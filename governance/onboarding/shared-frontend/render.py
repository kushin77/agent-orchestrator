#!/usr/bin/env python3
"""Renderer + gate for the shared-frontend mandatory onboarding (issue #703).

WHAT THIS IS
------------
One parameterized template (``template.yaml``) renders the two mandatory
consumer assets a governed repo carries for the ``shared-frontend`` module (GR-18
/ CMR:ONBOARD-0003) — the ``--os-`` design-token twin ``tokens.json`` and the
spoke declaration ``gdc-manifest.yaml`` that pins its module — from ONE instance
file (``instance.yaml``) that names the tenant / org / domain and the repo slug.
Nothing about a committed asset is hand-edited: if a root asset and a fresh
render of the instance disagree, that is drift and ``check`` says NOT-OK.

Three properties this module is responsible for, measured by ``tests/`` rather
than asserted:

1. **Templated, not copied.** Changing a parameter (the tenant, the domain, the
   repo) changes the rendered ``gdc-manifest.yaml``; ``tokens.json`` is
   tenant-invariant BY CONSTRUCTION (the upstream twin rule) so its render is a
   byte-identity assertion against the pinned digest rather than a substitution.
2. **Closed vocabulary.** An org, tenant or domain that ``vocabulary.yaml`` does
   not declare is REFUSED BY NAME — never guessed, never defaulted.
3. **Byte-stable.** For a fixed (org, tenant, domain, repo) tuple the render is
   byte-identical on every run: rendering is a pure function of the template
   bytes, the instance bytes, the vocabulary bytes and the seeds. No network, no
   clock, no environment, no randomness.

TWO REFUSAL FAMILIES
--------------------
* CANNOT-ASSESS (rc 2) — the contract itself cannot be read or is violated: a
  lane input is missing/unparseable, a seed is missing, or a document does not
  match ``schema.yaml``. Never reported as a pass.
* NOT-OK (rc 1) — a real defect, named on stderr: a missing root asset, a token
  set that drifted from the pinned rev, a mandatory pin that is missing or whose
  version/update policy moved, a committed asset that is not the render of the
  instance, or an unknown/absent tenant/org/domain.

DEPENDENCIES
------------
Python standard library + PyYAML only. The ``jsonschema`` library is NOT
required: this file owns the checks, and ``tests/`` cross-checks ``schema.yaml``
with the real ``jsonschema`` library when it is installed, so the schema's claim
to be a real JSON Schema is measured too.

EXIT-CODE CONTRACT (guardrails/honesty tri-state, issue #28)
------------------------------------------------------------
=====  =============  ======================================================
code   status         meaning
=====  =============  ======================================================
0      OK             every committed asset is the render of the instance
1      NOT-OK         a real defect, named on stderr
2      CANNOT-ASSESS  the contract could not be assessed — never a pass
=====  =============  ======================================================

Usage:
    python3 governance/onboarding/shared-frontend/render.py check
    python3 governance/onboarding/shared-frontend/render.py check --repo-root D
    python3 governance/onboarding/shared-frontend/render.py render [--out DIR]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import yaml
except Exception as exc:  # pragma: no cover - pre-flighted by the bash gate
    sys.stderr.write(
        "shared-frontend-onboarding: CANNOT-ASSESS - PyYAML unavailable: %r\n" % (exc,)
    )
    raise SystemExit(2)

EXIT_OK = 0
EXIT_NOT_OK = 1
EXIT_CANNOT_ASSESS = 2

API_VERSION = "ao.shared-frontend-onboarding/v1"
INSTANCE_SCHEMA = "ao.shared-frontend-onboarding/instance/v1"
VOCABULARY_SCHEMA = "ao.shared-frontend-onboarding/vocabulary/v1"

LANE_REL = "governance/onboarding/shared-frontend"
TEMPLATE_FILE = "template.yaml"
INSTANCE_FILE = "instance.yaml"
VOCABULARY_FILE = "vocabulary.yaml"
SCHEMA_FILE = "schema.yaml"
SEED_TOKENS_REL = "seeds/tokens.json"

TOKENS_ASSET = "tokens.json"
MANIFEST_ASSET = "gdc-manifest.yaml"

MANIFEST_SCHEMA = "cmr.gdc-manifest/v1"

# The three mandatory module pins the spoke manifest must carry (GR-18): the
# indexer KB (#464/#475), the diagrams blueprint (#464) and the shared-frontend
# `--os-` twin (#703). NONE may be removed — the hub catalog's
# `catalog/mandatory.tsv` declares all three as mandatory consumer assets.
MANDATORY_PINS: Tuple[str, ...] = (
    "code-indexing.mcp",
    "diagrams.blueprint",
    "shared-frontend.tokens",
)
PIN_VERSION = "~0.1"
PIN_UPDATES = "pr"

# The immutable pin ledger for the vendored `--os-` twin. `template.yaml`
# declares the SAME facts under `provenance:`; the two are kept in lockstep and a
# divergence is REFUSED BY NAME (the identity/edges parity discipline: the
# declarative file must equal the code spec, never approximate it). The digest is
# the sha256 of `vendor/CMR/templates/frontend/shared/design-tokens/tokens.json`
# at rev `ace748f4` (tag `v0.2.0`), i.e. of
# `kushin77/shared-frontend@shared/design-tokens/tokens.json`.
PINNED: Dict[str, str] = {
    "source_repo": "kushin77/shared-frontend",
    "source_path": "shared/design-tokens/tokens.json",
    "rev": "ace748f4",
    "tag": "v0.2.0",
    "license": "MIT",
    "tokens_sha256": "679ffaf89f6476832d663f3879aa4f90ebdce64021d0f20e0b11ffe0703fe188",
}

VENDORED_TOKENS_REL = "vendor/CMR/templates/frontend/shared/design-tokens/tokens.json"
VENDORED_GDC_TEMPLATE_REL = "vendor/CMR/templates/module/gdc-manifest.yaml"
HUB_MANDATORY_REL = "vendor/CMR/catalog/mandatory.tsv"

PLACEHOLDER = re.compile(r"\$\{([A-Za-z0-9_.]+)\}")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
REPO_SLUG = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
HOSTNAME = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+$"
)
REQUIRED_PARAMS: Tuple[str, ...] = ("org", "tenant", "domain", "repo")


class CannotAssess(Exception):
    """The contract could not be assessed (rc 2) — never a pass."""


# --- primitives --------------------------------------------------------------


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_bytes(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise CannotAssess("%s: cannot be read (%s)" % (path, exc))


def load_yaml(path: Path) -> Any:
    if not path.is_file():
        raise CannotAssess("%s: missing" % (path.name,))
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise CannotAssess("%s: unreadable/unparseable YAML (%s)" % (path.name, exc))


def default_repo_root() -> Path:
    """The repo root this renderer lives in: <repo>/governance/onboarding/<lane>."""
    return Path(__file__).resolve().parents[3]


# --- contract validation (schema.yaml, enforced here) ------------------------


def validate_template(tpl: Any) -> List[str]:
    errs: List[str] = []
    if not isinstance(tpl, dict):
        return ["template.yaml: root is not a mapping"]
    if tpl.get("apiVersion") != API_VERSION:
        errs.append(
            "template.yaml: apiVersion: %r != %r" % (tpl.get("apiVersion"), API_VERSION)
        )
    params = tpl.get("parameters")
    if not isinstance(params, list) or not params:
        errs.append("template.yaml: parameters[] must be a non-empty list")
    else:
        names = [
            p.get("name") for p in params if isinstance(p, dict) and isinstance(p.get("name"), str)
        ]
        for want in REQUIRED_PARAMS:
            if want not in names:
                errs.append(
                    "template.yaml: parameters[] declares no %r parameter" % want
                )
        for entry in params:
            if not isinstance(entry, dict):
                errs.append("template.yaml: parameters[]: entry is not a mapping")
                continue
            for key in ("name", "type", "required", "description"):
                if key not in entry:
                    errs.append(
                        "template.yaml: parameters[%s]: missing %r" % (entry.get("name", "?"), key)
                    )
    prov = tpl.get("provenance")
    if not isinstance(prov, dict):
        errs.append("template.yaml: provenance: must be a mapping")
    else:
        for key in sorted(PINNED):
            if not isinstance(prov.get(key), str) or not prov.get(key):
                errs.append("template.yaml: provenance.%s: missing or not a string" % key)
        digest = prov.get("tokens_sha256")
        if isinstance(digest, str) and not HEX64.match(digest):
            errs.append(
                "template.yaml: provenance.tokens_sha256: %r is not a 64-hex digest" % digest
            )
    assets = tpl.get("assets")
    if not isinstance(assets, list) or not assets:
        errs.append("template.yaml: assets[] must be a non-empty list")
    else:
        for asset in assets:
            if not isinstance(asset, dict):
                errs.append("template.yaml: assets[]: entry is not a mapping")
                continue
            if not isinstance(asset.get("path"), str) or not asset.get("path"):
                errs.append("template.yaml: assets[]: entry has no `path`")
            if asset.get("kind") not in ("verbatim", "interpolate"):
                errs.append(
                    "template.yaml: assets[%s].kind: %r is not verbatim|interpolate"
                    % (asset.get("path", "?"), asset.get("kind"))
                )
    return errs


def validate_instance(inst: Any) -> List[str]:
    errs: List[str] = []
    if not isinstance(inst, dict):
        return ["instance.yaml: root is not a mapping"]
    if inst.get("schema") != INSTANCE_SCHEMA:
        errs.append(
            "instance.yaml: schema: %r != %r" % (inst.get("schema"), INSTANCE_SCHEMA)
        )
    for key in REQUIRED_PARAMS:
        value = inst.get(key)
        if not isinstance(value, str) or not value.strip():
            errs.append("instance.yaml: %s: missing or empty" % key)
    return errs


def validate_vocabulary(vocab: Any) -> List[str]:
    errs: List[str] = []
    if not isinstance(vocab, dict):
        return ["vocabulary.yaml: root is not a mapping"]
    if vocab.get("schema") != VOCABULARY_SCHEMA:
        errs.append(
            "vocabulary.yaml: schema: %r != %r" % (vocab.get("schema"), VOCABULARY_SCHEMA)
        )
    orgs = vocab.get("orgs")
    if not isinstance(orgs, list) or not orgs:
        errs.append("vocabulary.yaml: orgs[] must be a non-empty list")
    else:
        for org in orgs:
            if not isinstance(org, dict) or not isinstance(org.get("id"), str):
                errs.append("vocabulary.yaml: orgs[]: entry has no string `id`")
    tenants = vocab.get("tenants")
    if not isinstance(tenants, list) or not tenants:
        errs.append("vocabulary.yaml: tenants[] must be a non-empty list")
    else:
        for tenant in tenants:
            if not isinstance(tenant, dict) or not isinstance(tenant.get("id"), str):
                errs.append("vocabulary.yaml: tenants[]: entry has no string `id`")
                continue
            if not isinstance(tenant.get("org"), str):
                errs.append(
                    "vocabulary.yaml: tenants[%s].org: missing" % tenant.get("id")
                )
            if not isinstance(tenant.get("domains"), list):
                errs.append(
                    "vocabulary.yaml: tenants[%s].domains[]: must be a list"
                    % tenant.get("id")
                )
    return errs


# --- parameter resolution (the closed vocabulary) ----------------------------


def resolve_params(inst: Dict[str, Any], vocab: Dict[str, Any]) -> Tuple[Dict[str, str], List[str]]:
    """Resolve the instance against the vocabulary. Errors are NOT-OK, by name."""
    errs: List[str] = []
    org = inst.get("org")
    tenant = inst.get("tenant")
    domain = inst.get("domain")
    repo = inst.get("repo")

    orgs = {
        o["id"]: o
        for o in vocab.get("orgs") or []
        if isinstance(o, dict) and isinstance(o.get("id"), str)
    }
    tenants = {
        t["id"]: t
        for t in vocab.get("tenants") or []
        if isinstance(t, dict) and isinstance(t.get("id"), str)
    }

    if not isinstance(org, str) or not SLUG.match(org):
        errs.append("instance.yaml: org: %r is not a lowercase slug" % (org,))
    elif org not in orgs:
        errs.append(
            "instance.yaml: org: unknown org %r (declared: %s)"
            % (org, ", ".join(sorted(orgs)) or "none")
        )

    if not isinstance(tenant, str) or not SLUG.match(tenant):
        errs.append("instance.yaml: tenant: %r is not a lowercase slug" % (tenant,))
    elif tenant not in tenants:
        errs.append(
            "instance.yaml: tenant: unknown tenant %r (declared: %s)"
            % (tenant, ", ".join(sorted(tenants)) or "none")
        )
    else:
        row = tenants[tenant]
        if isinstance(org, str) and row.get("org") != org:
            errs.append(
                "instance.yaml: tenant: tenant %r is not under org %r (it declares %r)"
                % (tenant, org, row.get("org"))
            )
        declared_domains = [
            d for d in (row.get("domains") or []) if isinstance(d, str)
        ]
        if not isinstance(domain, str) or not HOSTNAME.match(domain):
            errs.append(
                "instance.yaml: domain: %r is not a lowercase hostname" % (domain,)
            )
        elif domain not in declared_domains:
            errs.append(
                "instance.yaml: domain: %r is not declared for tenant %r (declared: %s)"
                % (domain, tenant, ", ".join(declared_domains) or "none")
            )

    if not isinstance(repo, str) or not REPO_SLUG.match(repo):
        errs.append("instance.yaml: repo: %r is not an owner/name slug" % (repo,))

    params = {
        "org": org if isinstance(org, str) else "",
        "tenant": tenant if isinstance(tenant, str) else "",
        "domain": domain if isinstance(domain, str) else "",
        "repo": repo if isinstance(repo, str) else "",
    }
    return params, errs


def provenance_findings(tpl: Dict[str, Any]) -> List[str]:
    """The template's declared provenance must equal the immutable pin ledger."""
    prov = tpl.get("provenance") if isinstance(tpl.get("provenance"), dict) else {}
    errs: List[str] = []
    for key in sorted(PINNED):
        got = prov.get(key)
        if got != PINNED[key]:
            errs.append(
                "template.yaml: provenance.%s: %r != pinned %r (the --os- twin pin "
                "is a fact; a re-pin is a reviewed edit of render.py's PINNED and "
                "this block together)" % (key, got, PINNED[key])
            )
    return errs


# --- rendering ---------------------------------------------------------------


def placeholder_values(tpl: Dict[str, Any], params: Dict[str, str]) -> Dict[str, str]:
    values: Dict[str, str] = dict(params)
    prov = tpl.get("provenance") if isinstance(tpl.get("provenance"), dict) else {}
    for key, value in prov.items():
        if isinstance(value, str):
            values["provenance.%s" % key] = value
    return values


def interpolate(text: str, values: Dict[str, str], label: str) -> Tuple[str, List[str]]:
    """Substitute `${name}`; an unknown name is refused by name."""
    errs: List[str] = []

    def replace(match: "re.Match[str]") -> str:
        key = match.group(1)
        if key not in values:
            errs.append("%s: unknown placeholder ${%s}" % (label, key))
            return match.group(0)
        return values[key]

    return PLACEHOLDER.sub(replace, text), errs


def render_assets(
    lane: Path, tpl: Dict[str, Any], values: Dict[str, str]
) -> Tuple[Dict[str, bytes], List[str]]:
    """Render every declared asset. Missing seed -> CANNOT-ASSESS."""
    rendered: Dict[str, bytes] = {}
    errs: List[str] = []
    for asset in tpl.get("assets") or []:
        if not isinstance(asset, dict):
            continue
        path = asset.get("path")
        kind = asset.get("kind")
        if not isinstance(path, str) or not path:
            continue
        if kind == "verbatim":
            seed_rel = asset.get("seed")
            if not isinstance(seed_rel, str) or not seed_rel:
                raise CannotAssess("template.yaml %s: verbatim asset has no `seed`" % path)
            seed_path = lane / seed_rel
            if not seed_path.is_file():
                raise CannotAssess(
                    "template.yaml %s: seed %s is missing (nothing to render from)"
                    % (path, seed_rel)
                )
            rendered[path] = seed_path.read_bytes()
        elif kind == "interpolate":
            document = asset.get("document")
            if not isinstance(document, str):
                raise CannotAssess(
                    "template.yaml %s: interpolate asset has no `document`" % path
                )
            text, perrs = interpolate(document, values, "template.yaml %s" % path)
            errs.extend(perrs)
            rendered[path] = text.encode("utf-8")
    return rendered, errs


# --- manifest assertions -----------------------------------------------------


def manifest_findings(text: str, params: Dict[str, str]) -> List[str]:
    """Structural assertions on a gdc-manifest document, named one by one."""
    errs: List[str] = []
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return ["%s: unparseable YAML (%s)" % (MANIFEST_ASSET, exc)]
    if not isinstance(doc, dict):
        return ["%s: root is not a mapping" % MANIFEST_ASSET]

    if doc.get("schema") != MANIFEST_SCHEMA:
        errs.append(
            "%s: schema: %r != %r" % (MANIFEST_ASSET, doc.get("schema"), MANIFEST_SCHEMA)
        )
    if doc.get("repo") != params["repo"]:
        errs.append(
            "%s: repo: %r != the instance repo %r"
            % (MANIFEST_ASSET, doc.get("repo"), params["repo"])
        )

    pins: Dict[str, Dict[str, Any]] = {}
    for pin in doc.get("modules") or []:
        if isinstance(pin, dict) and isinstance(pin.get("module"), str):
            pins[pin["module"]] = pin
    for module in MANDATORY_PINS:
        pin = pins.get(module)
        if pin is None:
            errs.append(
                "%s: modules[]: the mandatory pin %r is MISSING (the shared-frontend "
                "module declares three pins — %s — and none may be removed)"
                % (MANIFEST_ASSET, module, ", ".join(MANDATORY_PINS))
            )
            continue
        if pin.get("version") != PIN_VERSION:
            errs.append(
                "%s: modules[%s].version: %r != %r"
                % (MANIFEST_ASSET, module, pin.get("version"), PIN_VERSION)
            )
        if pin.get("updates") != PIN_UPDATES:
            errs.append(
                "%s: modules[%s].updates: %r != %r"
                % (MANIFEST_ASSET, module, pin.get("updates"), PIN_UPDATES)
            )
    if doc.get("updates") != PIN_UPDATES:
        errs.append(
            "%s: updates: %r != %r" % (MANIFEST_ASSET, doc.get("updates"), PIN_UPDATES)
        )

    onboarding = doc.get("x-onboarding")
    if not isinstance(onboarding, dict):
        errs.append(
            "%s: x-onboarding: missing — the tenant/org/domain binding is not "
            "declared, so this manifest cannot be the render of an instance"
            % MANIFEST_ASSET
        )
    else:
        for key in ("org", "tenant", "domain"):
            if onboarding.get(key) != params[key]:
                errs.append(
                    "%s: x-onboarding.%s: %r != the instance %s %r"
                    % (MANIFEST_ASSET, key, onboarding.get(key), key, params[key])
                )
    return errs


# --- the check ---------------------------------------------------------------


def check(repo_root: Path) -> Tuple[int, List[str], Dict[str, Any], List[str]]:
    """Assess the repo tree. Returns (rc, findings, detail, notes).

    Raises CannotAssess (rc 2) when the contract itself cannot be read.
    """
    lane = repo_root / LANE_REL
    if not lane.is_dir():
        raise CannotAssess("lane directory %s is missing" % LANE_REL)

    tpl = load_yaml(lane / TEMPLATE_FILE)
    inst = load_yaml(lane / INSTANCE_FILE)
    vocab = load_yaml(lane / VOCABULARY_FILE)
    if not (lane / SCHEMA_FILE).is_file():
        raise CannotAssess("%s/%s is missing (the contract cannot be assessed)" % (LANE_REL, SCHEMA_FILE))

    contract = (
        validate_template(tpl) + validate_instance(inst) + validate_vocabulary(vocab)
    )
    if contract:
        raise CannotAssess("lane contract violation: " + "; ".join(contract))

    findings: List[str] = list(provenance_findings(tpl))
    notes: List[str] = []

    params, param_errs = resolve_params(inst, vocab)
    findings.extend(param_errs)
    if param_errs:
        return EXIT_NOT_OK, findings, {"params": params}, notes

    values = placeholder_values(tpl, params)
    rendered, render_errs = render_assets(lane, tpl, values)
    findings.extend(render_errs)
    if render_errs:
        return EXIT_NOT_OK, findings, {"params": params}, notes

    detail: Dict[str, Any] = {
        "params": params,
        "assets": {name: sha256_bytes(data) for name, data in sorted(rendered.items())},
        "pinned": dict(PINNED),
    }

    # --- tokens.json, the vendored `--os-` design-token twin ------------------
    root_tokens = repo_root / TOKENS_ASSET
    expected_tokens = rendered.get(TOKENS_ASSET)
    if expected_tokens is None:
        findings.append(
            "template.yaml: assets[] renders no %s (the twin cannot be onboarded)"
            % TOKENS_ASSET
        )
    elif not root_tokens.is_file():
        findings.append(
            "%s: MISSING at the repo root — the shared-frontend mandatory consumer "
            "asset (the --os- design-token twin) is not onboarded" % TOKENS_ASSET
        )
    else:
        data = read_bytes(root_tokens)
        digest = sha256_bytes(data)
        if digest != PINNED["tokens_sha256"]:
            findings.append(
                "%s: sha256 %s != the pinned %s (rev %s, tag %s) — the vendored "
                "--os- twin drifted from the pinned rev"
                % (
                    TOKENS_ASSET,
                    digest,
                    PINNED["tokens_sha256"],
                    PINNED["rev"],
                    PINNED["tag"],
                )
            )
        if data != expected_tokens:
            findings.append(
                "%s: not the render of the pinned seed (expected sha256 %s, found %s)"
                % (TOKENS_ASSET, sha256_bytes(expected_tokens), digest)
            )
        seed_path = lane / SEED_TOKENS_REL
        if seed_path.is_file() and data != seed_path.read_bytes():
            findings.append(
                "%s: differs from the lane's byte-twin %s/%s"
                % (TOKENS_ASSET, LANE_REL, SEED_TOKENS_REL)
            )

    # The vendored seed is the upstream authority; when the submodule is
    # initialised the committed asset must be byte-identical to it. When it is
    # not initialised (the normal state of a fresh worktree) the pinned digest is
    # still the authority, so this is a NOTE and not a skip.
    vendored = repo_root / VENDORED_TOKENS_REL
    if vendored.is_file():
        vendored_bytes = read_bytes(vendored)
        vendored_digest = sha256_bytes(vendored_bytes)
        if vendored_digest != PINNED["tokens_sha256"]:
            findings.append(
                "%s: sha256 %s != the pinned %s — the VENDORED seed itself drifted "
                "(re-pin render.py's PINNED and template.yaml provenance together)"
                % (VENDORED_TOKENS_REL, vendored_digest, PINNED["tokens_sha256"])
            )
        if root_tokens.is_file() and read_bytes(root_tokens) != vendored_bytes:
            findings.append(
                "%s: not byte-identical to the vendored seed %s"
                % (TOKENS_ASSET, VENDORED_TOKENS_REL)
            )
    else:
        notes.append(
            "%s is not initialised in this worktree; the byte-identity cross-check "
            "against the vendored seed did not run (the pinned digest remains the "
            "authority)" % VENDORED_TOKENS_REL
        )

    if not (repo_root / VENDORED_GDC_TEMPLATE_REL).is_file():
        notes.append(
            "%s is not initialised in this worktree; the pin values were checked "
            "against render.py's PIN_VERSION/PIN_UPDATES constants only"
            % VENDORED_GDC_TEMPLATE_REL
        )

    # --- gdc-manifest.yaml, the spoke declaration + module pin ----------------
    root_manifest = repo_root / MANIFEST_ASSET
    expected_manifest = rendered.get(MANIFEST_ASSET)
    if expected_manifest is None:
        findings.append(
            "template.yaml: assets[] renders no %s (the module pin cannot be "
            "declared)" % MANIFEST_ASSET
        )
    elif not root_manifest.is_file():
        findings.append(
            "%s: MISSING at the repo root — the shared-frontend module pin is not "
            "declared" % MANIFEST_ASSET
        )
    else:
        manifest_bytes = read_bytes(root_manifest)
        if manifest_bytes != expected_manifest:
            findings.append(
                "%s: not the render of the instance (org %s / tenant %s / domain %s "
                "/ repo %s) — re-render with render.py"
                % (
                    MANIFEST_ASSET,
                    params["org"],
                    params["tenant"],
                    params["domain"],
                    params["repo"],
                )
            )
        findings.extend(
            manifest_findings(manifest_bytes.decode("utf-8", "replace"), params)
        )

    rc = EXIT_NOT_OK if findings else EXIT_OK
    return rc, findings, detail, notes


# --- CLI ---------------------------------------------------------------------


def _emit(rc: int, findings: List[str], detail: Dict[str, Any], notes: List[str], as_json: bool) -> int:
    if as_json:
        print(
            json.dumps(
                {"rc": rc, "findings": findings, "notes": notes, "detail": detail},
                indent=2,
                sort_keys=True,
            )
        )
        return rc
    for note in notes:
        sys.stderr.write("shared-frontend-onboarding: NOTE - %s\n" % note)
    if rc == EXIT_OK:
        params = detail.get("params") or {}
        print(
            "shared-frontend-onboarding: OK - the root %s (byte-identical to the "
            "pinned seed, rev %s) and %s (three mandatory pins intact) are the "
            "render of instance org=%s tenant=%s domain=%s repo=%s"
            % (
                TOKENS_ASSET,
                PINNED["rev"],
                MANIFEST_ASSET,
                params.get("org", "?"),
                params.get("tenant", "?"),
                params.get("domain", "?"),
                params.get("repo", "?"),
            )
        )
        return EXIT_OK
    for finding in findings:
        sys.stderr.write("  FAIL  %s\n" % finding)
    sys.stderr.write(
        "shared-frontend-onboarding: NOT-OK - %d finding(s)\n" % len(findings)
    )
    return EXIT_NOT_OK


def cmd_check(repo_root: Path, as_json: bool) -> int:
    try:
        rc, findings, detail, notes = check(repo_root)
    except CannotAssess as exc:
        if as_json:
            print(json.dumps({"rc": EXIT_CANNOT_ASSESS, "findings": [str(exc)]}, indent=2))
        else:
            sys.stderr.write("shared-frontend-onboarding: CANNOT-ASSESS - %s\n" % exc)
        return EXIT_CANNOT_ASSESS
    return _emit(rc, findings, detail, notes, as_json)


def cmd_render(repo_root: Path, out_dir: Path) -> int:
    lane = repo_root / LANE_REL
    tpl = load_yaml(lane / TEMPLATE_FILE)
    inst = load_yaml(lane / INSTANCE_FILE)
    vocab = load_yaml(lane / VOCABULARY_FILE)
    params, errs = resolve_params(inst, vocab)
    if errs:
        for err in errs:
            sys.stderr.write("  FAIL  %s\n" % err)
        return EXIT_NOT_OK
    rendered, render_errs = render_assets(lane, tpl, placeholder_values(tpl, params))
    if render_errs:
        for err in render_errs:
            sys.stderr.write("  FAIL  %s\n" % err)
        return EXIT_NOT_OK
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, data in sorted(rendered.items()):
        target = out_dir / name
        target.write_bytes(data)
        print("rendered %s (%d bytes, sha256 %s)" % (target, len(data), sha256_bytes(data)))
    return EXIT_OK


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="render.py",
        description="Render / check the shared-frontend mandatory onboarding (issue #703).",
    )
    parser.add_argument("--repo-root", default=None, help="repo root (default: derived from this file)")
    parser.add_argument("--json", action="store_true", help="machine-readable report (check only)")
    sub = parser.add_subparsers(dest="command", required=True)
    check_parser = sub.add_parser("check", help="assess the committed assets against the render (0/1/2)")
    render_parser = sub.add_parser("render", help="write the rendered assets")
    # `--repo-root` is accepted on either side of the subcommand: a caller that
    # writes `check --repo-root D` must not be a usage error (SUPPRESS keeps the
    # subparser's default from clobbering a value given before the subcommand).
    for sub_parser in (check_parser, render_parser):
        sub_parser.add_argument(
            "--repo-root",
            default=argparse.SUPPRESS,
            help="repo root (accepted before or after the subcommand)",
        )
    render_parser.add_argument("--out", default=None, help="output directory (default: repo root)")
    args = parser.parse_args(argv)

    repo_root_arg = getattr(args, "repo_root", None)
    repo_root = Path(repo_root_arg).resolve() if repo_root_arg else default_repo_root()

    if args.command == "check":
        return cmd_check(repo_root, args.json)
    if args.command == "render":
        out_dir = Path(args.out).resolve() if args.out else repo_root
        return cmd_render(repo_root, out_dir)
    parser.error("unknown command")
    return EXIT_CANNOT_ASSESS


if __name__ == "__main__":
    sys.exit(main())
