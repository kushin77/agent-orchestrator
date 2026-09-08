"""Corpus tests (issue #28, acceptance criterion 5).

The anti-formality analyzer is tuned and validated against REAL captured
artifacts, never only invented examples:

  * every corpus/fail artifact (a formality found in a real incident) must
    trip the analyzer;
  * every corpus/pass artifact (a real honest guard) must clear it.

Both directions are asserted.  Provenance is recorded in corpus/README.md.
"""

from __future__ import annotations

import os

from honesty.analyzer import analyze

# Which rule(s) each real fail artifact must trip.
FAIL_EXPECTED_RULES = {
    "check_all_read.sh": {"uncounted_skip"},
    "check_results_present.sh": {"never_fails_script"},
    "check_provider_health.sh": {"never_fails_script", "skip_counted_as_pass"},
    "check_everything_clean.sh": {"never_fails_function"},
    "scan_service_log.sh": {"self_match"},
}


def _sh_files(directory: str) -> list[str]:
    return sorted(
        name for name in os.listdir(directory) if name.endswith(".sh")
    )


class TestFailCorpus:
    def test_fail_corpus_is_real_and_nonempty(self, corpus_dir: str) -> None:
        files = _sh_files(os.path.join(corpus_dir, "fail"))
        assert len(files) == 5
        assert set(FAIL_EXPECTED_RULES) == set(files)

    def test_every_fail_artifact_trips_the_analyzer(self, corpus_dir: str) -> None:
        fail_dir = os.path.join(corpus_dir, "fail")
        for name in _sh_files(fail_dir):
            result = analyze([os.path.join(fail_dir, name)])
            assert result.findings, f"{name} is a real formality and must be caught"
            rules = {f.rule for f in result.findings}
            expected = FAIL_EXPECTED_RULES[name]
            assert rules & expected, (
                f"{name}: expected one of {expected}, got {rules}"
            )


class TestPassCorpus:
    def test_pass_corpus_is_real_and_nonempty(self, corpus_dir: str) -> None:
        files = _sh_files(os.path.join(corpus_dir, "pass"))
        assert len(files) == 3

    def test_every_pass_artifact_is_clean(self, corpus_dir: str) -> None:
        # No false positives on real honest guards.
        pass_dir = os.path.join(corpus_dir, "pass")
        for name in _sh_files(pass_dir):
            result = analyze([os.path.join(pass_dir, name)])
            assert result.is_clean, (
                name,
                [f.to_dict() for f in result.findings],
            )


class TestWholeCorpusDiscriminates:
    def test_fail_and_pass_sets_are_distinguishable(self, corpus_dir: str) -> None:
        # The corpus discriminates: every fail artifact is flagged and no pass
        # artifact is flagged.
        fail_dir = os.path.join(corpus_dir, "fail")
        pass_dir = os.path.join(corpus_dir, "pass")
        flagged = {
            name
            for name in _sh_files(fail_dir)
            if analyze([os.path.join(fail_dir, name)]).findings
        }
        clean = {
            name
            for name in _sh_files(pass_dir)
            if analyze([os.path.join(pass_dir, name)]).is_clean
        }
        assert flagged == set(FAIL_EXPECTED_RULES)
        assert clean == set(_sh_files(pass_dir))
