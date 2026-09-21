"""``clusters`` — the optional, read-only ``dispatch --by-cluster`` input."""

from __future__ import annotations

import json

import pytest

import clusters as clusters_mod
from graph import CannotAssess


def _good_document():
    return {
        "generated_at": "2026-09-20T00:00:00Z",
        "source_repos": ["kushin77/agent-orchestrator"],
        "clusters": [
            {
                "id": "c-rca-backfill-1",
                "family": "RCA backfill",
                "title": "Backfill RCA docs for the September incidents",
                "recipe": "apply the standard RCA template",
                "sme": "sniper-generic",
                "tier": "L0",
                "batchable": True,
                "wave": 1,
                "priority_rank": 1,
                "evidence": "2 open issues matched family 'rca' by label scan",
                "issues": [
                    {
                        "repo": "kushin77/agent-orchestrator",
                        "number": 100,
                        "title": "rca 100",
                        "labels": ["type:task"],
                        "age_days": 10,
                        "parent": None,
                    },
                    {
                        "repo": "kushin77/agent-orchestrator",
                        "number": 101,
                        "title": "rca 101",
                        "labels": ["type:task"],
                        "age_days": 12,
                        "parent": None,
                    },
                ],
            }
        ],
        "unclustered": [
            {"repo": "kushin77/agent-orchestrator", "number": 200, "title": "issue 200", "reason": "no matching family"}
        ],
        "hygiene": {"duplicates": [], "orphan_children": [], "empty_epics": [], "template_gaps": []},
    }


def test_no_clusters_json_is_none_not_an_error(root):
    assert clusters_mod.load(root) is None


def test_a_valid_clusters_json_loads(root):
    target = root / "governance" / "pmo"
    target.mkdir(parents=True, exist_ok=True)
    (target / "clusters.json").write_text(json.dumps(_good_document()), encoding="utf-8")
    loaded = clusters_mod.load(root)
    assert loaded is not None
    assert loaded.clusters[0].id == "c-rca-backfill-1"
    assert len(loaded.clusters[0].issues) == 2
    assert loaded.clusters[0].evidence
    assert loaded.unclustered[0]["number"] == 200
    assert loaded.hygiene["duplicates"] == []


def test_a_schema_invalid_clusters_json_is_cannot_assess(root):
    target = root / "governance" / "pmo"
    target.mkdir(parents=True, exist_ok=True)
    document = _good_document()
    del document["clusters"][0]["recipe"]  # required key missing
    (target / "clusters.json").write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(CannotAssess):
        clusters_mod.load(root)


def test_duplicate_cluster_ids_are_cannot_assess(root):
    target = root / "governance" / "pmo"
    target.mkdir(parents=True, exist_ok=True)
    document = _good_document()
    document["clusters"].append(dict(document["clusters"][0]))
    (target / "clusters.json").write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(CannotAssess):
        clusters_mod.load(root)


def test_malformed_json_is_cannot_assess(root):
    target = root / "governance" / "pmo"
    target.mkdir(parents=True, exist_ok=True)
    (target / "clusters.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(CannotAssess):
        clusters_mod.load(root)
