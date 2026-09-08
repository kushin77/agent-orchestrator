"""Anti-formality analyzer tests (issue #28, acceptance criterion 3).

Both directions are asserted (negative-tested):
  * honest guards and honest shapes -> ZERO findings (no false positives);
  * formality fixtures -> the exact expected rule fires (no false negatives);
  * reviewed lines carrying `# formality-ok: <reason>` are suppressed.
"""

from __future__ import annotations

import os

from honesty.analyzer import Finding, HonestyAnalyzer, analyze


def rules_for(path: str) -> list[str]:
    result = analyze([path])
    return [f.rule for f in result.findings]


def finding_with(result, rule: str) -> Finding | None:
    for finding in result.findings:
        if finding.rule == rule:
            return finding
    return None


class TestHonestFixturesAreClean:
    def test_all_honest_fixtures_are_clean(self, fixtures_dir: str) -> None:
        honest = os.path.join(fixtures_dir, "honest")
        result = analyze([honest])
        assert result.is_clean, [f.to_dict() for f in result.findings]

    def test_each_honest_fixture_clean_individual(self, fixtures_dir: str) -> None:
        honest = os.path.join(fixtures_dir, "honest")
        for name in os.listdir(honest):
            if name.endswith(".sh"):
                assert analyze([os.path.join(honest, name)]).is_clean, name


class TestFormalityFixturesAreCaught:
    def test_never_fails_function_detected(self, fixtures_dir: str) -> None:
        # Found and not-found paths both return 0 -> identical exit codes.
        path = os.path.join(fixtures_dir, "formality", "check_never_fails.sh")
        rules = rules_for(path)
        assert "never_fails_function" in rules, rules

    def test_uncounted_skip_detected(self, fixtures_dir: str) -> None:
        path = os.path.join(fixtures_dir, "formality", "check_skips_uncounted.sh")
        rules = rules_for(path)
        assert "uncounted_skip" in rules, rules

    def test_optional_absence_detected(self, fixtures_dir: str) -> None:
        path = os.path.join(fixtures_dir, "formality", "check_optional_absence.sh")
        result = analyze([path])
        assert not result.is_clean
        finding = finding_with(result, "never_fails_script")
        assert finding is not None
        assert "optional" in finding.detail

    def test_skip_counted_as_pass_detected(self, fixtures_dir: str) -> None:
        path = os.path.join(fixtures_dir, "formality", "check_skip_is_pass.sh")
        rules = rules_for(path)
        assert "skip_counted_as_pass" in rules, rules

    def test_self_match_detected(self, fixtures_dir: str) -> None:
        path = os.path.join(fixtures_dir, "selfmatch", "scan_self_match.sh")
        rules = rules_for(path)
        assert "self_match" in rules, rules

    def test_never_fails_fixture_not_misreported_as_uncounted_skip(
        self, fixtures_dir: str
    ) -> None:
        # A never-fail function contains no `|| continue`, so it must not be
        # reported under the uncounted_skip rule -- mechanism-anchored.
        path = os.path.join(fixtures_dir, "formality", "check_never_fails.sh")
        rules = rules_for(path)
        assert "uncounted_skip" not in rules, rules


class TestReviewAidSemantics:
    def test_never_fails_script_is_guard_name_scoped(self, tmp_path) -> None:
        # The never-fails-script rule is scoped to guard-named files so
        # ordinary helper scripts are not flagged.
        helper = tmp_path / "build_context.sh"
        helper.write_text(
            "#!/usr/bin/env bash\necho \"building context\"\nexit 0\n",
            encoding="utf-8",
        )
        assert analyze([str(helper)]).is_clean

    def test_formality_ok_suppresses_reviewed_line(self, tmp_path) -> None:
        reviewed = tmp_path / "lib_scan.sh"
        reviewed.write_text(
            "#!/usr/bin/env bash\n"
            "for f in \"$@\"; do\n"
            "  [ -e \"$f\" ] || continue   # formality-ok: entries tallied below\n"
            "done\n"
            "exit 0\n",
            encoding="utf-8",
        )
        result = analyze([str(reviewed)])
        assert result.is_clean
        assert result.suppressed == 1

    def test_unreviewed_skip_is_still_caught(self, tmp_path) -> None:
        unreviewed = tmp_path / "lib_scan.sh"
        unreviewed.write_text(
            "#!/usr/bin/env bash\n"
            "for f in \"$@\"; do\n"
            "  [ -e \"$f\" ] || continue\n"
            "done\n"
            "exit 0\n",
            encoding="utf-8",
        )
        result = analyze([str(unreviewed)])
        assert finding_with(result, "uncounted_skip") is not None

    def test_heredoc_bodies_are_data_not_greps(self, tmp_path) -> None:
        # A grep-like line embedded in a heredoc is fixture data, not a live
        # grep the script performs -- it must not self-match against the
        # comment that documents the fixture.
        script = tmp_path / "load_fixtures.sh"
        script.write_text(
            "#!/usr/bin/env bash\n"
            "# documents the marker-xyz fixture used by the test suite\n"
            "cat <<'EOF' > /tmp/fixture.txt\n"
            "if grep -q \"marker-xyz\" file; then exit 1; fi\n"
            "EOF\n"
            "exit 0\n",
            encoding="utf-8",
        )
        result = analyze([str(script)])
        assert result.is_clean

    def test_unreadable_file_is_never_clean(self, tmp_path) -> None:
        # An unreadable guard attests nothing: it is surfaced as a finding so
        # it can never read as green.
        broken = tmp_path / "check_unreadable.sh"
        broken.write_text("#!/usr/bin/env bash\necho hi\n", encoding="utf-8")
        os.chmod(broken, 0o000)
        try:
            result = analyze([str(broken)])
            assert not result.is_clean
            assert finding_with(result, "unreadable") is not None
        finally:
            os.chmod(broken, 0o644)


class TestAnalyzerAggregation:
    def test_result_counts_files_and_lines(self, fixtures_dir: str) -> None:
        honest = os.path.join(fixtures_dir, "honest")
        result = HonestyAnalyzer().run([honest])
        assert result.files == 3  # the three honest fixture guards
        assert result.lines > 0
