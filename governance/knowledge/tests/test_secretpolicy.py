"""Secret policy for indexed assets (issue #139).

Credential-shaped values are assembled at runtime, never written literally: the
repository's own secret scan greps tracked source text, so a literal in this file
would fail `make verify` for a reason unrelated to any real secret. That trap has
bitten this fleet before.
"""

from __future__ import annotations

from pathlib import Path

from conftest import credential_assignment, credential_line, credentialed_literal
from secretpolicy import scan_file, scan_text


def test_openai_style_key_is_detected():
    findings = scan_text(credential_line(), "x.md")
    assert [f.rule for f in findings] == ["openai-style-key"]
    assert findings[0].line == 1


def test_github_token_is_detected():
    value = "gh" + "p_" + ("A" * 40)
    findings = scan_text("GITHUB_TOKEN=" + value)
    assert findings and findings[0].rule == "github-token"


def test_aws_access_key_id_is_detected():
    value = "AKIA" + ("0" * 16)
    findings = scan_text("aws_access_key_id = " + value)
    assert findings and findings[0].rule == "aws-access-key-id"


def test_private_key_block_is_detected():
    value = "-----BEGIN " + "RSA PRIVATE KEY" + "-----"
    findings = scan_text(value + "\nMIIEow==\n")
    assert findings and findings[0].rule == "private-key-block"


def test_credential_assignment_is_detected():
    findings = scan_text(credential_assignment())
    assert findings and findings[0].rule == "credential-assignment"


def test_finding_never_echoes_the_value():
    """A finding must not become the leak it exists to report."""
    secret = credentialed_literal()
    findings = scan_text(credential_line("key"))
    assert findings
    assert secret not in findings[0].describe()
    assert secret not in str(findings[0].as_dict())
    assert "redacted" in findings[0].describe()


def test_documented_placeholder_is_not_flagged():
    """The index must not flag the examples that teach the pattern."""
    line = "api" + "_" + "key" + "=" + "changeme" + ("0" * 24)
    assert scan_text(line) == []


def test_ellipsis_style_placeholder_is_not_flagged():
    assert scan_text("api_key = sk-...") == []


def test_ordinary_prose_is_clean():
    text = (
        "# Governance\n\nThe gate is fail-closed and refuses to merge failing "
        "work. Run `make verify` before every PR.\n"
    )
    assert scan_text(text) == []


def test_multiple_lines_report_their_own_line_numbers():
    text = "\n".join(
        [
            "clean line",
            credential_line(),
            "another clean line",
            credential_assignment(),
        ]
    )
    findings = scan_text(text)
    assert [f.line for f in findings] == [2, 4]


def test_binary_content_does_not_raise():
    assert scan_text("\x00\x01\x02 not utf-8-ish") == []


def test_scan_file_uses_the_reader_seam(tmp_path: Path):
    path = tmp_path / "asset.md"
    path.write_text("clean content\n", encoding="utf-8")
    findings = scan_file(path, reader=lambda _p: "key = " + credentialed_literal())
    assert findings and findings[0].path == str(path)


def test_scan_file_tolerates_a_missing_file(tmp_path: Path):
    assert scan_file(tmp_path / "absent.md") == []
