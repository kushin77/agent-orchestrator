"""governance/lifecycle/controls.yaml + policy.py (issue #885).

Two disciplines are pinned:

* the closed vocabulary declared in ``controls.yaml`` matches ``model.INVARIANTS``
  in both directions, and this is enforced by ``model.py`` at import time — a
  mutation test proves changing/removing a declared code is refused by name;
* ``directive.retire`` reads the retirement thresholds from ``policy.py``
  instead of a hard-coded check, so lowering the bar in code alone is no
  longer possible.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from governance.lifecycle import policy
from governance.lifecycle.model import INVARIANTS


def _model_codes() -> dict[str, str]:
    return {inv.code: inv.subject_kind for inv in INVARIANTS}


def test_the_packaged_controls_file_matches_model_in_both_directions():
    loaded = policy.load(model_codes=_model_codes())
    assert set(loaded.code_names()) == set(_model_codes())


def test_load_for_model_reads_the_packaged_controls_file():
    loaded = policy.load_for_model()
    assert loaded.retire.min_reason_length >= 1


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "controls.yaml"
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    return path


BASE = """\
schema: ao.lifecycle/controls-v1
subject: governance/lifecycle
closure_invariants:
  codes:
    - {{code: PR_NOT_MERGED, subject_kind: item}}
retire:
  min_reason_length: {min_len}
  require_superseded_by: true
quarantine:
  require_tracked_by: true
  stale_disposition: reported
"""


def test_a_control_missing_a_declared_invariant_is_refused_by_name(tmp_path):
    """Mutation test: removing a code from controls.yaml must be caught (no-false-green)."""
    path = _write(tmp_path, BASE.format(min_len=8))
    with pytest.raises(policy.PolicyUnavailable) as excinfo:
        policy.load(path, model_codes=_model_codes())
    assert "VERIFY_EVIDENCE_MISSING" in str(excinfo.value) or "declares no closure invariant" in str(excinfo.value)


def _full_codes_yaml(*, min_len: int = 8, extra_code: str = "") -> str:
    lines = ["    - {code: %s, subject_kind: %s}" % (inv.code, inv.subject_kind) for inv in INVARIANTS]
    if extra_code:
        lines.append("    - {code: %s, subject_kind: item}" % extra_code)
    codes_block = "\n".join(lines)
    return (
        "schema: ao.lifecycle/controls-v1\n"
        "subject: governance/lifecycle\n"
        "closure_invariants:\n"
        "  codes:\n"
        f"{codes_block}\n"
        "retire:\n"
        f"  min_reason_length: {min_len}\n"
        "  require_superseded_by: true\n"
        "quarantine:\n"
        "  require_tracked_by: true\n"
        "  stale_disposition: reported\n"
    )


def test_an_undeclared_extra_code_is_refused(tmp_path):
    path = tmp_path / "controls.yaml"
    path.write_text(_full_codes_yaml(extra_code="NOT_A_REAL_CODE"), encoding="utf-8")
    with pytest.raises(policy.PolicyUnavailable) as excinfo:
        policy.load(path, model_codes=_model_codes())
    assert "NOT_A_REAL_CODE" in str(excinfo.value)


def test_mutating_min_reason_length_to_zero_is_refused_at_load(tmp_path):
    path = _write(tmp_path, BASE.format(min_len=0))
    # this control alone (without the full code set) still exercises the
    # min_reason_length floor, independent of the vocabulary cross-check
    with pytest.raises(policy.PolicyUnavailable, match="min_reason_length"):
        policy.load(path)


def test_check_retire_refuses_a_reason_shorter_than_the_declared_floor():
    loaded = policy.load_for_model()
    with pytest.raises(policy.PolicyUnavailable, match="character"):
        loaded.check_retire(reason="", superseded_by=(1,))


def test_check_retire_refuses_no_superseded_by():
    loaded = policy.load_for_model()
    with pytest.raises(policy.PolicyUnavailable, match="superseded_by"):
        loaded.check_retire(reason="superseded", superseded_by=())


def test_check_retire_accepts_a_reason_meeting_the_floor():
    loaded = policy.load_for_model()
    loaded.check_retire(reason="superseded", superseded_by=(723,))  # does not raise


def test_directive_retire_reads_the_policy_threshold_not_a_hardcoded_check(tmp_path):
    """Mutation test: directive.retire's own gate is the declared floor, not a
    bare non-empty check — raising the floor in controls.yaml (via the env
    override) must change directive.retire's behaviour without a code change."""
    import os

    from governance.lifecycle import directive

    sent = tmp_path / ".fleet" / "sent"
    sent.mkdir(parents=True)
    (sent / "d-1.json").write_text(json.dumps({"id": "d-1", "task": {"issue": 42}}), encoding="utf-8")

    strict = tmp_path / "controls.yaml"
    strict.write_text(_full_codes_yaml(min_len=50), encoding="utf-8")
    old = os.environ.get(policy.CONTROLS_ENV)
    os.environ[policy.CONTROLS_ENV] = str(strict)
    try:
        with pytest.raises(directive.DirectiveRefused, match="character"):
            directive.retire(tmp_path, "d-1", closed=True, reason="too short", superseded_by=(1,))
    finally:
        if old is None:
            os.environ.pop(policy.CONTROLS_ENV, None)
        else:
            os.environ[policy.CONTROLS_ENV] = old
