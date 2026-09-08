"""Anti-formality honesty analyzer (issue #28, acceptance criterion 3).

A guard whose success path and gave-up path produce the same exit code is a
*formality*: it reads as coverage while guaranteeing none, which is worse than
no check at all (no-false-green, AO-GR-4; AO-GR-19).  This module statically
inspects shell guard scripts and functions and flags the documented formality
SHAPES:

===================  ======================================================
rule                 shape detected
===================  ======================================================
never_fails_function a ``check_*``/``verify_*``/... function that can
                     ``return 0`` but has no failing exit anywhere in its
                     body -- success and failure share one exit code
never_fails_script   a guard-named script that ``exit 0``s but never exits
                     nonzero anywhere -- no input can make it fail
skip_counted_as_pass a check function whose SKIP path ``return 0``s and that
                     has no failing exit at all -- a skip that reads as a
                     pass on a surface where a skip is a failure
uncounted_skip       a ``|| continue`` that skips unresolvable entries while
                     nothing in the file counts what was actually examined
self_match           a grep pattern that also appears in a nearby comment
                     explaining that very grep -- the check can match its own
                     documentation
===================  ======================================================

Shapes are REVIEW AID findings, not verdicts: a guarded ``continue`` beside a
counter is usually correct, and this scanner cannot always see the counter.
Every finding answers one question -- "run it against the thing it is
supposed to catch: what is the exit code?" -- and a line that has been
reviewed carries a required reason: ``# formality-ok: <reason>`` and is then
suppressed.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Set, Tuple

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Guard-named functions: by their name they assert something, so a body that
# cannot fail makes the name a lie.
_FN_PREFIXES = ("check", "verify", "validate", "assert", "ensure", "require", "guard")
_FN_RE = re.compile(
    r"^\s*(?:function\s+)?(?P<name>(?:"
    + "|".join(_FN_PREFIXES)
    + r")_[a-z0-9_]+)\s*\(\s*\)\s*\{"
)
_FILE_RE = re.compile(
    r"^(?:"
    + "|".join(_FN_PREFIXES + ("scan",))
    + r")_[a-z0-9_]+\.sh$"
)

_SUCCESS_EXIT_RE = re.compile(r"\b(?:return|exit)\s+0\b")
_FAIL_EXIT_RE = re.compile(
    r"\b(?:return|exit)\s+(?:\$\?|\"\$\?\"|\$[a-zA-Z_][a-zA-Z0-9_]*|[1-9][0-9]*)\b"
)
_SKIP_MARKER_RE = re.compile(r"\b(?:SKIP|skip)\b")
# An uncounted skip: `[ -e x ] || continue` etc.  A counter elsewhere in the
# file is what keeps this from firing on every loop.
_CONTINUE_SKIP_RE = re.compile(r"^\s*[^#]*\|\|\s*continue\b")
# A genuine counter looks like `missing=$((missing + 1))`, `n=$((n+1))`,
# `count=$((count + 1))`, `ok=$((ok + 1))` or a bare `((counter++))`.  Prose
# that merely mentions "counted" is not a counter.
_COUNTER_RE = re.compile(
    r"\b(?:checked|counted|examined|missing|found|seen|processed|scanned|"
    r"matched|tallied|total|ok|errors|failures)\s*(?:=|\+=|\+\+)"
    r"|\(\(\s*[a-zA-Z_][a-zA-Z0-9_]*\s*\+\s*1\s*\)\)"
    r"|\+\+"
)

# Self-match detector knobs (mirror the leaderboard guard-self-match scanner).
_SELF_MATCH_MIN_LEN = 8
_SELF_MATCH_PROXIMITY = 15
_SELF_MATCH_NEVER_EXEMPT = re.compile(
    r"^(eval|sudo|curl|wget|chmod|chown|rm -rf|--force|--no-verify)$"
)
_GREP_QUOTED_RE = re.compile(r'\bgrep\b[^\n"]*"([^"\n]{3,})"')
# A pattern containing these is a regex, not a literal the self-match claim
# can be made about (after splitting on `|`).
_REGEX_META_RE = re.compile(r"[\\\[\](){}*+?.^$]")

_SKIP_DIRS = {".git", ".research", "vendor", "__pycache__", ".pytest_cache"}


@dataclass
class Finding:
    """One formality-shape finding on one line of one file."""

    rule: str
    path: str
    line: int
    text: str
    detail: str = ""

    def to_dict(self) -> dict:
        return {
            "rule": self.rule,
            "path": self.path,
            "line": self.line,
            "text": self.text,
            "detail": self.detail,
        }


@dataclass
class AnalyzerResult:
    """Aggregate result of an analyzer run over one or more paths."""

    findings: List[Finding] = field(default_factory=list)
    files: int = 0
    lines: int = 0
    suppressed: int = 0

    @property
    def is_clean(self) -> bool:
        return not self.findings


def _iter_sh(paths: Iterable[str]) -> Iterable[str]:
    """Yield shell files under the given paths (files or directories)."""
    for path in paths:
        if os.path.isdir(path):
            for root, dirs, files in os.walk(path):
                dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
                for name in files:
                    if name.endswith(".sh"):
                        yield os.path.join(root, name)
        elif os.path.isfile(path):
            yield path


def _split_lines(content: str) -> List[str]:
    return content.splitlines()


def _heredoc_lines(lines: List[str]) -> Set[int]:
    """Line numbers that fall inside a heredoc body (fixture data, not code)."""
    inside: Set[int] = set()
    tag: Optional[str] = None
    for idx, line in enumerate(lines):
        if tag is not None:
            if re.match(r"^\s*" + re.escape(tag) + r"\s*$", line):
                tag = None
            else:
                inside.add(idx + 1)
            continue
        m = re.search(r"<<-?\s*['\"]?([A-Za-z_][A-Za-z0-9_]*)['\"]?", line)
        if m and not line.lstrip().startswith("#"):
            tag = m.group(1)
    return inside


def _function_body(lines: List[str], start: int) -> Tuple[str, int, int]:
    """Return (body_text, body_first_line, body_last_line) of the function
    that starts at 0-based ``start`` (its ``{`` line).  Body runs until the
    first line whose first non-space character is ``}`` (approximate --
    function bodies in this codebase do not nest standalone braces)."""
    end = start + 1
    while end < len(lines) and not lines[end].lstrip().startswith("}"):
        end += 1
    body = "\n".join(lines[start + 1 : end])
    return body, start + 1, min(end, len(lines) - 1)


class HonestyAnalyzer:
    """Static honesty analyzer for guard scripts (review aid + strict gate)."""

    def __init__(self) -> None:
        self._findings: List[Finding] = []
        self._suppressed = 0
        self._files_scanned = 0
        self._lines_scanned = 0

    # -- public API -------------------------------------------------------
    def run(self, paths: Iterable[str]) -> AnalyzerResult:
        for path in _iter_sh(paths):
            self._analyze_file(path)
        return AnalyzerResult(
            findings=list(self._findings),
            files=self._files_scanned,
            lines=self._lines_scanned,
            suppressed=self._suppressed,
        )

    def analyze_file(self, path: str) -> AnalyzerResult:
        """Analyze a single file and return only that file's findings."""
        return self.run([path])

    # -- per-file dispatch ------------------------------------------------
    def _analyze_file(self, path: str) -> None:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                content = fh.read()
        except (OSError, UnicodeDecodeError):
            # An unreadable file is not "clean" and not "formal": it is
            # unknown.  It is surfaced as a finding so it can never read as
            # green (an unreadable guard attests nothing).
            self._add(
                Finding(
                    rule="unreadable",
                    path=path,
                    line=0,
                    text="",
                    detail="guard file could not be read -- CANNOT-ASSESS, not a pass",
                )
            )
            return

        lines = _split_lines(content)
        self._files_scanned += 1
        self._lines_scanned += len(lines)
        base = os.path.basename(path)
        guard_named_file = bool(_FILE_RE.match(base))

        # 1. never-fail functions (identical-exit-code paths), incl. the
        #    SKIP-counted-as-PASS variant.
        for fn_line, name in self._iter_functions(lines):
            body, body_first, _ = _function_body(lines, fn_line)
            has_success = bool(_SUCCESS_EXIT_RE.search(body))
            has_fail = bool(_FAIL_EXIT_RE.search(body))
            if has_success and not has_fail:
                is_skip = bool(_SKIP_MARKER_RE.search(body))
                rule = "skip_counted_as_pass" if is_skip else "never_fails_function"
                detail = (
                    "SKIP path returns 0 and the function has no failing exit "
                    "-- on the authoritative surface a skip is a FAIL"
                    if is_skip
                    else "function can return 0 but has no failing exit -- "
                    "success and failure share one exit code"
                )
                self._report(
                    Finding(
                        rule=rule,
                        path=path,
                        line=fn_line + 1,
                        text=lines[fn_line].strip(),
                        detail=detail,
                    ),
                    anchor=lines[body_first] if body_first < len(lines) else "",
                )

        # 2. never-fail guard script (file level).
        if guard_named_file:
            self._check_script_never_fails(path, lines, content)

        # 3. uncounted `|| continue`.
        if not _COUNTER_RE.search(content):
            for idx, line in enumerate(lines):
                if _CONTINUE_SKIP_RE.search(line):
                    self._report(
                        Finding(
                            rule="uncounted_skip",
                            path=path,
                            line=idx + 1,
                            text=line.strip(),
                            detail="skips silently and nothing in this file "
                            "counts what was actually examined",
                        ),
                        anchor=line,
                    )

        # 4. self-matching grep patterns.
        self._check_self_match(path, lines)

    def _check_script_never_fails(
        self, path: str, lines: List[str], content: str
    ) -> None:
        has_exit_success = bool(re.search(r"\bexit\s+0\b", content))
        has_exit_fail = bool(
            re.search(r"\bexit\s+(?:\$\?|\"\$\?\"|\$[a-zA-Z_]|[1-9][0-9]*)", content)
        )
        if not (has_exit_success and not has_exit_fail):
            return
        anchor_line = ""
        detail = "script exits 0 but never exits nonzero anywhere -- no input can make it fail"
        if self._has_optional_else(content):
            detail += "; absence-gated else reports 'optional' instead of failing"
        # Report once, anchored at the first top-level `exit 0`.
        anchor_idx = 0
        for idx, line in enumerate(lines):
            if re.search(r"\bexit\s+0\b", line):
                anchor_idx = idx
                anchor_line = line
                break
        self._report(
            Finding(
                rule="never_fails_script",
                path=path,
                line=anchor_idx + 1,
                text=lines[anchor_idx].strip(),
                detail=detail,
            ),
            anchor=anchor_line,
        )

    # -- shape helpers ----------------------------------------------------
    def _iter_functions(self, lines: List[str]) -> Iterable[Tuple[int, str]]:
        for idx, line in enumerate(lines):
            m = _FN_RE.match(line)
            if m:
                yield idx, m.group("name")

    @staticmethod
    def _has_optional_else(content: str) -> bool:
        """Absence-gated else branch whose body cannot fail (echo/colon)."""
        return bool(
            re.search(
                r"^\s*else\b[^\n]*\n\s*(?:echo|printf|:)\b",
                content,
                re.MULTILINE,
            )
        )

    # -- self-match ---------------------------------------------------------
    def _check_self_match(self, path: str, lines: List[str]) -> None:
        heredocs = _heredoc_lines(lines)
        comment_re = re.compile(r"^\s*#")
        for idx, line in enumerate(lines):
            if idx + 1 in heredocs:
                continue
            if comment_re.match(line):
                continue
            m = _GREP_QUOTED_RE.search(line)
            if not m:
                continue
            quoted = m.group(1)
            if quoted.startswith(("$", "`")):
                continue
            for alt in quoted.split("|"):
                alt = alt.strip()
                if not alt:
                    continue
                if len(alt) < _SELF_MATCH_MIN_LEN and not _SELF_MATCH_NEVER_EXEMPT.match(
                    alt
                ):
                    continue
                if _REGEX_META_RE.search(alt):
                    continue
                hit = self._pattern_in_nearby_comment(
                    lines, idx, alt, comment_re, heredocs
                )
                if hit is not None:
                    self._report(
                        Finding(
                            rule="self_match",
                            path=path,
                            line=idx + 1,
                            text=line.strip(),
                            detail=(
                                f"grep pattern {alt!r} also appears in the "
                                f"comment on line {hit + 1} -- the check can "
                                "match its own documentation"
                            ),
                        ),
                        anchor=line,
                    )
                    break  # one finding per grep line is enough

    @staticmethod
    def _pattern_in_nearby_comment(
        lines: List[str],
        grep_idx: int,
        pattern: str,
        comment_re: "re.Pattern[str]",
        heredocs: Set[int],
    ) -> Optional[int]:
        needle = re.compile(r"\b" + re.escape(pattern) + r"\b")
        lo = max(0, grep_idx - _SELF_MATCH_PROXIMITY)
        hi = min(len(lines), grep_idx + _SELF_MATCH_PROXIMITY + 1)
        for c in range(lo, hi):
            if c == grep_idx or (c + 1) in heredocs:
                continue
            if comment_re.match(lines[c]) and needle.search(lines[c]):
                return c
        return None

    # -- reporting -----------------------------------------------------------
    def _add(self, finding: Finding) -> None:
        self._findings.append(finding)

    def _report(self, finding: Finding, anchor: str = "") -> None:
        # A reviewed line carries its reason inline and is suppressed.
        if "formality-ok:" in (finding.text or "") or "formality-ok:" in anchor:
            self._suppressed += 1
            return
        self._add(finding)


def analyze(paths: Iterable[str]) -> AnalyzerResult:
    """Convenience wrapper: run a fresh analyzer over the given paths."""
    return HonestyAnalyzer().run(paths)
