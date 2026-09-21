"""portal.server.settings contract tests (issue #1756, spog settings v1).

Proves against fixture files, no live data:

* full aggregation across every domain when all sources exist;
* per-domain refusal-by-name (a ``status`` row naming the absent source
  file) when that domain's source is missing — never a silently empty
  domain;
* no secret VALUE ever reaches a row, checked two ways: against a poisoned
  fixture that carries a "value" field (must not appear anywhere in the
  projected row), and against the real
  ``infra/terraform/provider-credentials.json`` shape (only the allowed key
  set is ever projected).
"""

from __future__ import annotations

import json

import yaml
from conftest import REPO_ROOT

from portal.server.settings import SettingsAggregator, SettingsRow

REGISTRY_YAML = """
services:
  paperclip:
    default: off
  hermes:
    default: "on"
"""

FLEET_JOBS = {
    "jobs": [
        {"name": "watchdog", "interval": 2, "enabled": True},
        {"name": "prune", "schedule": "23 4 * * *", "enabled": True},
    ]
}

TIER_POLICY = {
    "default_provider": "deepseek",
    "tiers": {"L0": {"claude": "haiku"}, "L1": {"claude": "sonnet"}},
}

SKIP_BUDGET = {
    "schema": "ao.verify.skip-budget/v1",
    "why": "prose",
    "tracking-lease": {"stuff": "prose"},
    "entry-a": {"rows": []},
    "entry-b": {"rows": []},
}

NOUS_CREDENTIALS = {
    "schema": "provider-credentials/v1",
    "secrets": [
        {
            "service": "gateway",
            "env": "NOUS_API_KEY",
            "secret_id": "ao-nous-api-key",
            "gate": "enable_hermes",
            "delivery": "value",
            "source": "operator-supplied",
        }
    ],
}

POISONED_CREDENTIALS = {
    "schema": "provider-credentials/v1",
    "secrets": [
        {
            "service": "gateway",
            "env": "NOUS_API_KEY",
            "secret_id": "ao-nous-api-key",
            "gate": "enable_hermes",
            "value": "sk-fake-should-never-leak",
            "api_key": "sk-fake-also-should-never-leak",
        }
    ],
}


def _write_fixtures(root, *, nous_doc=NOUS_CREDENTIALS):
    (root / "portal" / "config").mkdir(parents=True)
    (root / "portal" / "config" / "feature-flags.yaml").write_text(
        yaml.safe_dump({"surfaces": {"sessions": {"default": "off"}}}),
        encoding="utf-8",
    )
    (root / "config").mkdir()
    (root / "config" / "fleet-jobs.json").write_text(
        json.dumps(FLEET_JOBS), encoding="utf-8"
    )
    (root / "governance" / "dispatch").mkdir(parents=True)
    (root / "governance" / "dispatch" / "tier-policy.json").write_text(
        json.dumps(TIER_POLICY), encoding="utf-8"
    )
    (root / "scripts").mkdir()
    (root / "scripts" / "skip-budget.json").write_text(
        json.dumps(SKIP_BUDGET), encoding="utf-8"
    )
    (root / "infra" / "feature-flags").mkdir(parents=True)
    (root / "infra" / "feature-flags" / "registry.yaml").write_text(
        REGISTRY_YAML, encoding="utf-8"
    )
    (root / "infra" / "terraform").mkdir(parents=True)
    (root / "infra" / "terraform" / "provider-credentials.json").write_text(
        json.dumps(nous_doc), encoding="utf-8"
    )


def test_full_aggregation_projects_every_domain(tmp_path):
    _write_fixtures(tmp_path)
    rows = SettingsAggregator(repo_root=tmp_path).aggregate()
    assert rows, "aggregate() must not be empty when every source exists"
    assert all(isinstance(row, SettingsRow) for row in rows)
    assert all(row.editable is False for row in rows)

    domains = {row.domain for row in rows}
    assert domains == {
        "portal_surfaces",
        "fleet_jobs",
        "dispatch_tier_policy",
        "gate_skip_budget",
        "provider_flags",
        "nous_secret",
    }

    by_domain = {}
    for row in rows:
        by_domain.setdefault(row.domain, []).append(row)

    assert {r.key for r in by_domain["fleet_jobs"]} == {"watchdog", "prune"}
    assert {r.key for r in by_domain["provider_flags"]} == {
        "enable_hermes",
        "enable_paperclip",
    }
    hermes_row = next(
        r for r in by_domain["provider_flags"] if r.key == "enable_hermes"
    )
    paperclip_row = next(
        r for r in by_domain["provider_flags"] if r.key == "enable_paperclip"
    )
    assert hermes_row.value == "on"
    assert paperclip_row.value == "off"

    tier_keys = {r.key for r in by_domain["dispatch_tier_policy"]}
    assert tier_keys == {"default_provider", "tier.L0", "tier.L1"}

    assert {r.key for r in by_domain["gate_skip_budget"]} == {
        "schema",
        "section_count",
    }
    section_count = next(
        r.value for r in by_domain["gate_skip_budget"] if r.key == "section_count"
    )
    assert section_count == 2  # entry-a, entry-b (schema/why/tracking-lease excluded)


def test_each_domain_refuses_by_name_when_its_source_is_absent(tmp_path):
    # No fixtures written at all — every source is absent.
    rows = SettingsAggregator(repo_root=tmp_path).aggregate()
    assert len(rows) == 6
    for row in rows:
        assert row.key == "status"
        assert row.value.startswith("not reporting: ")
        assert row.source_file in row.value
        assert row.editable is False


def test_missing_provider_credentials_refuses_by_name(tmp_path):
    _write_fixtures(tmp_path)
    (tmp_path / "infra" / "terraform" / "provider-credentials.json").unlink()
    rows = SettingsAggregator(repo_root=tmp_path).aggregate()
    nous_rows = [r for r in rows if r.domain == "nous_secret"]
    assert len(nous_rows) == 1
    assert nous_rows[0].key == "status"
    assert "provider-credentials.json" in nous_rows[0].value


def test_no_secret_value_ever_reaches_a_row_against_poisoned_fixture(tmp_path):
    _write_fixtures(tmp_path, nous_doc=POISONED_CREDENTIALS)
    rows = SettingsAggregator(repo_root=tmp_path).aggregate()
    nous_rows = [r for r in rows if r.domain == "nous_secret"]
    assert nous_rows
    for row in nous_rows:
        assert "sk-fake-should-never-leak" not in row.value
        assert "sk-fake-also-should-never-leak" not in row.value
        assert "api_key" not in row.value
        projected = json.loads(row.value)
        assert "value" not in projected
        assert "api_key" not in projected


def test_nous_secret_row_projects_only_the_allowed_key_set_against_real_file():
    real_path = REPO_ROOT / "infra" / "terraform" / "provider-credentials.json"
    document = json.loads(real_path.read_text(encoding="utf-8"))
    assert document["secrets"], "fixture assumption: the real file declares secrets"

    rows = SettingsAggregator(repo_root=REPO_ROOT).aggregate()
    nous_rows = [r for r in rows if r.domain == "nous_secret"]
    assert nous_rows, "the real provider-credentials.json must aggregate"
    allowed = {"secret_id", "env", "gate", "service"}
    for row in nous_rows:
        projected = json.loads(row.value)
        assert set(projected.keys()) <= allowed
        assert "value" not in projected
        assert "api_key" not in projected
        assert "delivery" not in projected
