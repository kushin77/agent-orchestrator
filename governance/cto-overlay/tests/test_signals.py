"""Non-negotiable signals: always run, whatever the tier, and able to fail (#147)."""

from __future__ import annotations

from conftest import commit_all, engine, git, state_of, verdicts_for

# Assembled at run time on purpose: a literal key shape in this file would make
# the repository's own secret gate (scripts/check-secrets.sh) fail for the
# wrong reason — the fixture is the thing under test, not this source.
PLANTED_KEY = "AKIA" + "IOSFODNN7EXAMPLE"
PLANTED_SECRET = "-----BEGIN RSA " + "PRIVATE KEY-----\n" + "aws_key = " + PLANTED_KEY + "\n"
BROKEN_SCRIPT = "#!/usr/bin/env bash\nif [ 1 -eq 1 ]; then\n"


def test_every_signal_runs_even_when_its_layer_is_not_selected(make_repo, assess):
    root = make_repo("signals")
    config = engine.load_config(root)
    report = assess(root, layers=["executive"])
    signals = verdicts_for(report, "non-negotiable")
    assert [verdict.check for verdict in signals] == list(config.non_negotiable)
    assert all(verdict.state == engine.STATE_PASS for verdict in signals)
    assert state_of(report, "engineering", "syntax") == engine.STATE_SKIP


def test_shell_syntax_signal_fails_on_a_broken_script(make_repo, assess):
    root = make_repo("broken-sh", extra_files={"scripts/oops.sh": BROKEN_SCRIPT})
    report = assess(root, layers=["executive"])
    assert state_of(report, "engineering", "syntax") == engine.STATE_SKIP
    assert state_of(report, "non-negotiable", "shell_syntax") == engine.STATE_FAIL
    detail = next(
        verdict.detail
        for verdict in verdicts_for(report, "non-negotiable")
        if verdict.check == "shell_syntax"
    )
    assert "oops.sh" in detail
    assert report.exit_code == engine.EXIT_NOT_OK


def test_secret_scan_signal_fails_on_a_planted_key(make_repo, assess):
    root = make_repo("planted", extra_files={"notes/credentials.txt": PLANTED_SECRET})
    report = assess(root, layers=["support"])
    assert state_of(report, "non-negotiable", "secret_scan") == engine.STATE_FAIL
    assert report.exit_code == engine.EXIT_NOT_OK
    assert report.not_ok


def test_secret_scan_passes_on_a_clean_checkout(make_repo, assess):
    root = make_repo("clean-secrets")
    report = assess(root)
    assert state_of(report, "non-negotiable", "secret_scan") == engine.STATE_PASS


def test_protected_files_detects_an_unreviewed_change(make_repo, assess):
    root = make_repo("protected")
    makefile = root / "Makefile"
    makefile.write_text("verify:\n\t@false\n", encoding="utf-8")
    report = assess(root)
    assert state_of(report, "non-negotiable", "protected_files") == engine.STATE_FAIL
    assert report.exit_code == engine.EXIT_NOT_OK

    git(root, "checkout", "--", "Makefile")
    restored = assess(root)
    assert state_of(restored, "non-negotiable", "protected_files") == engine.STATE_PASS
    assert restored.exit_code == engine.EXIT_OK


def test_protected_files_compares_against_a_diff_base(make_repo, assess):
    root = make_repo("protected-base")
    base = git(root, "rev-parse", "HEAD").stdout.strip()
    (root / "Makefile").write_text("verify:\n\t@echo changed\n", encoding="utf-8")
    commit_all(root, "change the protected Makefile")

    report = assess(root, diff_base=base)
    assert state_of(report, "non-negotiable", "protected_files") == engine.STATE_FAIL
    assert report.exit_code == engine.EXIT_NOT_OK

    clean = assess(root, diff_base="HEAD")
    assert state_of(clean, "non-negotiable", "protected_files") == engine.STATE_PASS


def test_protected_files_reports_a_missing_path(make_repo, assess):
    def mutate(document):
        document["signals"]["protected_files"]["paths"].append("docs/NO-SUCH-FILE.md")

    root = make_repo("missing-protected", mutate=mutate)
    report = assess(root)
    assert state_of(report, "non-negotiable", "protected_files") == engine.STATE_FAIL
    assert "NO-SUCH-FILE.md" in next(
        verdict.detail
        for verdict in verdicts_for(report, "non-negotiable")
        if verdict.check == "protected_files"
    )


def test_path_integrity_rejects_a_traversal_path(make_repo, assess):
    def mutate(document):
        document["signals"]["protected_files"]["paths"] = ["../outside-the-repo"]

    root = make_repo("traversal", mutate=mutate)
    report = assess(root)
    assert state_of(report, "non-negotiable", "path_integrity") == engine.STATE_FAIL
    assert report.exit_code == engine.EXIT_NOT_OK


def test_path_integrity_rejects_an_absolute_path(make_repo, assess):
    def mutate(document):
        document["signals"]["protected_files"]["paths"] = ["/etc/passwd"]

    root = make_repo("absolute", mutate=mutate)
    report = assess(root)
    assert state_of(report, "non-negotiable", "path_integrity") == engine.STATE_FAIL


def test_path_integrity_passes_on_a_clean_checkout(make_repo, assess):
    root = make_repo("paths")
    report = assess(root)
    assert state_of(report, "non-negotiable", "path_integrity") == engine.STATE_PASS


def test_path_problem_tells_absent_from_unsafe():
    assert engine.path_problem("docs/README.md") is None
    assert engine.path_problem("") == "empty path"
    assert engine.path_problem("/abs") == "absolute path"
    assert engine.path_problem("a/../b") == "parent-directory traversal"
    assert engine.path_problem("a\\b") == "backslash in path"
    assert engine.path_problem("a//b") == "empty path segment"
