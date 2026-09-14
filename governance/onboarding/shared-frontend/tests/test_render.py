"""Renderer behaviour: parameterization, determinism, byte-identity, refusals."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

LANE_REL = Path("governance/onboarding/shared-frontend")
REPO_ROOT_FROM_LANE = Path(__file__).resolve().parents[4]

ASSETS = ("tokens.json", "gdc-manifest.yaml")


def _render(renderer, repo_root):
    lane = Path(repo_root) / LANE_REL
    tpl = renderer.load_yaml(lane / "template.yaml")
    inst = renderer.load_yaml(lane / "instance.yaml")
    vocab = renderer.load_yaml(lane / "vocabulary.yaml")
    params, errs = renderer.resolve_params(inst, vocab)
    assert errs == [], errs
    rendered, render_errs = renderer.render_assets(
        lane, tpl, renderer.placeholder_values(tpl, params)
    )
    assert render_errs == [], render_errs
    return rendered


def _findings_text(findings):
    return "\n".join(findings)


def _write_instance(lane, **overrides):
    doc = (lane / "instance.yaml").read_text(encoding="utf-8")
    for key, value in overrides.items():
        lines = []
        for line in doc.splitlines():
            if line.startswith("%s:" % key):
                lines.append("%s: %s" % (key, value))
            else:
                lines.append(line)
        doc = "\n".join(lines) + "\n"
    (lane / "instance.yaml").write_text(doc, encoding="utf-8")


# --- the happy path ----------------------------------------------------------


def test_committed_assets_are_the_render(renderer, clean_repo):
    rc, findings, detail, _notes = renderer.check(clean_repo)
    assert rc == renderer.EXIT_OK, _findings_text(findings)
    assert findings == []
    assert detail["params"] == {
        "org": "elevatediq",
        "tenant": "kushin77-platform",
        "domain": "ai.purebliss.app",
        "repo": "kushin77/agent-orchestrator",
    }


def test_render_is_deterministic(renderer, clean_repo, tmp_path):
    first = _render(renderer, clean_repo)
    second = _render(renderer, clean_repo)
    assert first == second

    out_a = tmp_path / "a"
    out_b = tmp_path / "b"
    for out in (out_a, out_b):
        proc = subprocess.run(
            [
                sys.executable,
                str(clean_repo / LANE_REL / "render.py"),
                "--repo-root",
                str(clean_repo),
                "render",
                "--out",
                str(out),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        assert "rendered" in proc.stdout
    for name in ASSETS:
        assert (out_a / name).read_bytes() == (out_b / name).read_bytes()
        assert (out_a / name).read_bytes() == (clean_repo / name).read_bytes()


def test_parameterization_moves_the_manifest(renderer, clean_repo):
    before = _render(renderer, clean_repo)
    _write_instance(clean_repo / LANE_REL, repo="kushin77/other-repo")
    after = _render(renderer, clean_repo)
    assert after["gdc-manifest.yaml"] != before["gdc-manifest.yaml"]
    assert b"repo: kushin77/other-repo" in after["gdc-manifest.yaml"]
    # The twin is tenant-INVARIANT by the upstream twin rule: changing the
    # instance parameters must not move a single of its bytes.
    assert after["tokens.json"] == before["tokens.json"]


def test_root_tokens_is_byte_identical_to_the_pinned_seed(renderer, clean_repo):
    committed = (clean_repo / "tokens.json").read_bytes()
    digest = renderer.sha256_bytes(committed)
    assert digest == renderer.PINNED["tokens_sha256"]
    assert committed == (clean_repo / LANE_REL / "seeds/tokens.json").read_bytes()


def test_root_tokens_matches_the_vendored_seed_when_present(renderer):
    vendored = REPO_ROOT_FROM_LANE / "vendor/CMR/templates/frontend/shared/design-tokens/tokens.json"
    if not vendored.is_file():
        pytest.skip("vendor/CMR is not initialised in this worktree")
    committed = (REPO_ROOT_FROM_LANE / "tokens.json").read_bytes()
    assert renderer.sha256_bytes(committed) == renderer.sha256_bytes(vendored.read_bytes())
    assert committed == vendored.read_bytes()


# --- refusals, each named ----------------------------------------------------


def test_missing_tokens_is_named(renderer, clean_repo):
    (clean_repo / "tokens.json").unlink()
    rc, findings, _detail, _notes = renderer.check(clean_repo)
    assert rc == renderer.EXIT_NOT_OK
    assert "tokens.json" in _findings_text(findings)
    assert "MISSING" in _findings_text(findings)


def test_tokens_drift_from_the_pinned_rev_is_named(renderer, clean_repo):
    path = clean_repo / "tokens.json"
    path.write_bytes(path.read_bytes() + b" ")
    rc, findings, _detail, _notes = renderer.check(clean_repo)
    assert rc == renderer.EXIT_NOT_OK
    text = _findings_text(findings)
    assert "drifted from the pinned rev" in text
    assert renderer.PINNED["tokens_sha256"] in text


def test_dropped_mandatory_pin_is_named(renderer, clean_repo):
    manifest = clean_repo / "gdc-manifest.yaml"
    doc = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    dropped = {"shared-frontend.tokens", "code-indexing.mcp"}
    doc["modules"] = [
        pin for pin in doc["modules"] if pin.get("module") not in dropped
    ]
    manifest.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    rc, findings, _detail, _notes = renderer.check(clean_repo)
    assert rc == renderer.EXIT_NOT_OK
    text = _findings_text(findings)
    assert "mandatory pin 'shared-frontend.tokens' is MISSING" in text
    assert "mandatory pin 'code-indexing.mcp' is MISSING" in text
    assert "mandatory pin 'diagrams.blueprint' is MISSING" not in text
    assert "not the render of the instance" in text


def test_manifest_not_the_render_is_named(renderer, clean_repo):
    manifest = clean_repo / "gdc-manifest.yaml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8") + "# hand edit\n", encoding="utf-8"
    )
    rc, findings, _detail, _notes = renderer.check(clean_repo)
    assert rc == renderer.EXIT_NOT_OK
    assert "not the render of the instance" in _findings_text(findings)


def test_unknown_tenant_is_named(renderer, clean_repo):
    _write_instance(clean_repo / LANE_REL, tenant="kushin77-typo")
    rc, findings, _detail, _notes = renderer.check(clean_repo)
    assert rc == renderer.EXIT_NOT_OK
    text = _findings_text(findings)
    assert "unknown tenant 'kushin77-typo'" in text
    assert "kushin77-platform" in text


def test_unknown_domain_is_named(renderer, clean_repo):
    _write_instance(clean_repo / LANE_REL, domain="typo.example.com")
    rc, findings, _detail, _notes = renderer.check(clean_repo)
    assert rc == renderer.EXIT_NOT_OK
    assert "is not declared for tenant 'kushin77-platform'" in _findings_text(findings)


def test_tenant_under_the_wrong_org_is_named(renderer, clean_repo):
    lane = clean_repo / LANE_REL
    vocab = yaml.safe_load((lane / "vocabulary.yaml").read_text(encoding="utf-8"))
    vocab["orgs"].append({"id": "other-org", "name": "Another org"})
    vocab["tenants"].append({"id": "other-tenant", "org": "other-org", "domains": []})
    (lane / "vocabulary.yaml").write_text(
        yaml.safe_dump(vocab, sort_keys=False), encoding="utf-8"
    )
    # `other-tenant` is declared, but under `other-org`, not `elevatediq`.
    _write_instance(lane, tenant="other-tenant")
    rc, findings, _detail, _notes = renderer.check(clean_repo)
    assert rc == renderer.EXIT_NOT_OK
    assert "is not under org 'elevatediq'" in _findings_text(findings)


def test_tenant_with_no_evidenced_domain_is_refused(renderer, clean_repo):
    # kushin77-ops IS declared, but no host is evidenced for it: naming one is
    # refused rather than guessed.
    _write_instance(
        clean_repo / LANE_REL, tenant="kushin77-ops", domain="ops.purebliss.app"
    )
    rc, findings, _detail, _notes = renderer.check(clean_repo)
    assert rc == renderer.EXIT_NOT_OK
    assert "is not declared for tenant 'kushin77-ops' (declared: none)" in _findings_text(
        findings
    )


def test_missing_parameter_is_cannot_assess(renderer, clean_repo):
    lane = clean_repo / LANE_REL
    text = (lane / "instance.yaml").read_text(encoding="utf-8")
    text = "\n".join(line for line in text.splitlines() if not line.startswith("domain:"))
    (lane / "instance.yaml").write_text(text + "\n", encoding="utf-8")
    with pytest.raises(renderer.CannotAssess) as excinfo:
        renderer.check(clean_repo)
    assert "domain" in str(excinfo.value)


def test_provenance_parity_is_enforced(renderer, clean_repo):
    lane = clean_repo / LANE_REL
    text = (lane / "template.yaml").read_text(encoding="utf-8")
    (lane / "template.yaml").write_text(
        text.replace("rev: ace748f4", "rev: deadbeef"), encoding="utf-8"
    )
    rc, findings, _detail, _notes = renderer.check(clean_repo)
    assert rc == renderer.EXIT_NOT_OK
    assert "provenance.rev" in _findings_text(findings)


def test_unknown_placeholder_is_named(renderer, clean_repo):
    lane = clean_repo / LANE_REL
    text = (lane / "template.yaml").read_text(encoding="utf-8")
    (lane / "template.yaml").write_text(
        text.replace("repo: ${repo}", "repo: ${nope}"), encoding="utf-8"
    )
    rc, findings, _detail, _notes = renderer.check(clean_repo)
    assert rc == renderer.EXIT_NOT_OK
    assert "unknown placeholder ${nope}" in _findings_text(findings)


def test_unparseable_lane_input_is_cannot_assess(renderer, clean_repo):
    lane = clean_repo / LANE_REL
    (lane / "instance.yaml").write_text("org: [unclosed\n", encoding="utf-8")
    with pytest.raises(renderer.CannotAssess):
        renderer.check(clean_repo)


# --- the schema bundle is a real JSON Schema ---------------------------------


def test_schema_bundle_is_real_json_schema(renderer, clean_repo):
    jsonschema = pytest.importorskip("jsonschema")
    lane = clean_repo / LANE_REL
    bundle = renderer.load_yaml(lane / "schema.yaml")
    assert bundle["$schema"].startswith("http://json-schema.org/draft-07/schema#")
    import yaml

    for document, key in (
        ("instance.yaml", "instance"),
        ("vocabulary.yaml", "vocabulary"),
        ("template.yaml", "template"),
    ):
        sub = {"$ref": "#/$defs/%s" % key, "$defs": bundle["$defs"]}
        payload = yaml.safe_load((lane / document).read_text(encoding="utf-8"))
        jsonschema.Draft7Validator(sub).validate(payload)


def test_check_json_report_is_machine_readable(renderer, clean_repo):
    proc = subprocess.run(
        [
            sys.executable,
            str(clean_repo / LANE_REL / "render.py"),
            "--repo-root",
            str(clean_repo),
            "--json",
            "check",
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    report = json.loads(proc.stdout)
    assert report["rc"] == 0
    assert report["findings"] == []


def test_lane_is_self_contained(renderer, clean_repo):
    """The render must not need the vendored submodule: it works from seeds/."""
    shutil.rmtree(clean_repo / "vendor", ignore_errors=True)
    rc, findings, _detail, _notes = renderer.check(clean_repo)
    assert rc == renderer.EXIT_OK, _findings_text(findings)
