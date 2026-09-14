"""Cross-repo execution-boundary detector (issue #125).

This module is the **local signal for a signal that is not measurable here**.

Issue #125 is filed in this repo but its backlog is *cross-repo*: it asks this
repo to remediate branch protection, Dependabot, guardrail files and dependency
pins inside a dozen *other* kushin77 repos. That backlog has no local signal —
nothing in this checkout moves when a foreign repo stays non-compliant — so a
drift gate can report PASS for months while the epic sits silently idle
(measured: 11 of the 12 children #126-#137 were still open when this was
written). The doctrine fixes this by making the *boundary violation itself*
measurable locally: a backlog item that names a foreign repo, filed on this
repo's board, is a finding, and the honest fix is a direction issue on the
owning repo's board rather than an edit made from here.

Three checks, none of which may be vacuous (GR-12):

``self-parent``
    The body carries a marker declaring the backlog is owned elsewhere.

``foreign-repo-issue``
    The body references a foreign repo in a ``Closes``/``Refs``/``Parent:``
    form.

``foreign-repo-declaration``
    The body *declares* its repo with the board's own ``## Repo`` convention —
    the pattern the live #126-#137 children actually use — and that repo is not
    this one. This is the check that reads the boundary the way the board
    writes it, rather than the way the epic's first draft happened to mark it:
    it needs no legacy marker, it names the foreign repo, and it ignores
    children that have since closed, because those are resolved history rather
    than a live finding. Its state rule is fail-closed: only a literal
    ``closed`` state suppresses a finding, so a snapshot that omits ``state``
    altogether (as this repo's own ``.board/snapshot.json`` export shape does
    for every other field) can never turn a real violation into a silent pass.

The detector is deliberately **pure, offline and stdlib-only** (``yaml`` is an
optional convenience, already a repo dependency; it is not required): it reads a
JSON snapshot of already-fetched issues and never calls the network, never runs
``gh``, and never writes. That keeps it runnable inside a sandboxed gate and
keeps the finding reproducible from an archived snapshot.

Exit-code contract (tri-state, GR-28 — ``cannot-assess`` is never a pass)::

    0  OK            — the snapshot was read and no boundary violation remains
    1  NOT-OK        — the snapshot was read and violations were found
    2  CANNOT-ASSESS — the snapshot was missing, unreadable, or empty

See ``docs/CROSS-REPO-EXECUTION-BOUNDARY.md`` for the contract this enforces.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

__all__ = [
    "BoundaryPolicy",
    "Finding",
    "EXIT_OK",
    "EXIT_NOT_OK",
    "EXIT_CANNOT_ASSESS",
    "FINDING_SELF_PARENT",
    "FINDING_FOREIGN_REPO_ISSUE",
    "FINDING_FOREIGN_REPO_DECLARATION",
    "load_issues",
    "check_issues",
    "main",
]

# --- exit-code contract -----------------------------------------------------
EXIT_OK = 0
EXIT_NOT_OK = 1
EXIT_CANNOT_ASSESS = 2

# --- finding kinds ----------------------------------------------------------
FINDING_SELF_PARENT = "self-parent"
FINDING_FOREIGN_REPO_ISSUE = "foreign-repo-issue"
FINDING_FOREIGN_REPO_DECLARATION = "foreign-repo-declaration"

# ``Closes owner/repo#N`` / ``Refs owner/repo#N`` / ``Parent: owner/repo#N``.
# Case sensitive on the keyword (``Closes``), permissive on spacing, and it
# requires the ``owner/repo#N`` shape — a bare ``Closes #12`` is same-repo and
# must never be flagged. ``_OWNER_REPO`` is kept permissive (dots, dashes,
# underscores) because kushin77 repos use all of them (``ERP-CRM``,
# ``Shared_Integrations``, ``saas-rbac``).
_OWNER_REPO = r"[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*"
_FOREIGN_REF_RE = re.compile(
    r"\b(?:Closes|Refs|Parent:)\s+(%s)#(\d+)\b" % _OWNER_REPO
)

# --- the ``## Repo`` declaration convention ---------------------------------
# The pattern every live #126-#137 child actually carries (measured on the
# board, 2026-09-13):
#
#     ## Repo
#     saas-rbac
#
# Accepted variants, all case-insensitive: a heading (``## Repo``, ``### Repo:``)
# or a label (``Repo:``, ``**Repo**:``, ``**Repo**``), with the value either
# after a colon on the same line or on the next non-blank line. The value is a
# bare repo name (``saas-rbac``) or ``owner/name`` (``kushin77/saas-rbac``),
# stripped of backticks/quotes/emphasis and reduced to its first token.
#
# The marker must be the *whole* heading or label: a heading that merely starts
# with the word (``## Repository layout``) or a plural label (``Repos:``) is
# prose, not a declaration. Widening that boundary is what would make the
# finding unusable — every issue with a "Repository" heading would light up.
_REPO_WORD = r"(?:\*\*|__)?Repo(?:\*\*|__)?"
_DECL_HEADING_COLON_RE = re.compile(
    r"^ {0,3}#{1,6}[ \t]+" + _REPO_WORD + r"[ \t]*:[ \t]*(?P<value>.*?)[ \t]*$",
    re.IGNORECASE,
)
_DECL_HEADING_BARE_RE = re.compile(
    r"^ {0,3}#{1,6}[ \t]+" + _REPO_WORD + r"[ \t]*$", re.IGNORECASE
)
_DECL_LABEL_COLON_RE = re.compile(
    r"^ {0,3}" + _REPO_WORD + r"[ \t]*:[ \t]*(?P<value>.*?)[ \t]*$", re.IGNORECASE
)
_DECL_LABEL_BARE_RE = re.compile(
    r"^ {0,3}(?:\*\*|__)Repo(?:\*\*|__)[ \t]*$", re.IGNORECASE
)
_REPO_NAME_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]*(?:/[A-Za-z0-9][A-Za-z0-9._-]*)?$"
)
# Markdown wrappers that may surround a repo name: backticks, straight/curly
# quotes, emphasis. None of them is a legal repo-name character, so stripping
# them can never damage a real name.
_DECL_STRIP_CHARS = "`'\"\u2018\u2019\u201c\u201d*"


@dataclass(frozen=True)
class BoundaryPolicy:
    """What this repo may remediate, and which markers mark a foreign backlog.

    ``own_repo`` is ``owner/name`` (e.g. ``kushin77/agent-orchestrator``). A
    reference to any *other* repo from an issue that lives in ``own_repo`` is a
    boundary violation: per AGENTS.md, direction/needs go to that repo's board.

    ``out_of_scope_markers`` are body markers that declare the issue's payload
    belongs to a backlog owned elsewhere. The default ``("Parent: #125",)``
    marks the #126-#137 children: an issue carrying it is scoped to a foreign
    repo's compliance work even though it is filed here.
    """

    own_repo: str
    out_of_scope_markers: Tuple[str, ...] = ("Parent: #125",)


@dataclass(frozen=True)
class Finding:
    """One boundary violation.

    ``finding`` is one of the three kind constants. ``repos`` names the foreign
    repo(s) the finding is about — empty for kinds whose detail already carries
    the reference — so a caller can group findings per repo without parsing the
    human-readable ``detail``.
    """

    issue: int
    title: str
    finding: str
    detail: str
    repos: Tuple[str, ...] = ()


def load_issues(path: Any) -> List[Dict[str, Any]]:
    """Load a JSON list of issues, tolerating a ``{"items": [...]}`` wrapper.

    Raises ``ValueError`` (never exits) when the payload is not an issue list —
    the caller maps that to CANNOT-ASSESS. Accepts ``str`` or ``os.PathLike``.
    """

    raw = Path(path).read_text(encoding="utf-8")
    data = json.loads(raw)
    if isinstance(data, dict):
        data = data.get("items")
    if not isinstance(data, list):
        raise ValueError("issue snapshot is not a list (nor an {'items': [...]} wrapper)")
    issues: List[Dict[str, Any]] = []
    for entry in data:
        if not isinstance(entry, dict):
            raise ValueError("issue snapshot contains a non-object entry")
        issues.append(entry)
    return issues


def _as_int(value: Any) -> Optional[int]:
    """Coerce an issue number; ``None`` when it is not an integer."""

    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _normalise_repo(text: str) -> Optional[str]:
    """Normalise a declaration value to a repo name, or ``None`` if it is not one.

    Whitespace is collapsed, markdown wrappers are stripped, and the first
    token is taken (``saas-rbac (see report)`` → ``saas-rbac``). Anything that
    is not a bare repo name or ``owner/name`` is rejected — an unparseable
    value is *not* a declaration, so it can never manufacture a finding.
    """

    value = str(text or "").strip().strip(_DECL_STRIP_CHARS).strip()
    if not value:
        return None
    token = value.split()[0].strip(_DECL_STRIP_CHARS).rstrip(".,;:!")
    if _REPO_NAME_RE.match(token):
        return token
    return None


def _declared_repos(body: str) -> List[str]:
    """Return every repo named by a ``Repo`` declaration in ``body``, in order.

    Deduplicated case-insensitively, so repeating the same heading twice names
    one repo. A heading or label whose value is missing or unparseable yields
    no declaration at all (see ``_normalise_repo``).
    """

    lines = body.splitlines()
    repos: List[str] = []
    seen: set = set()
    index = 0
    while index < len(lines):
        line = lines[index]
        value: Optional[str] = None
        match = _DECL_HEADING_COLON_RE.match(line)
        if match is None:
            match = _DECL_LABEL_COLON_RE.match(line)
        if match is not None:
            value = match.group("value")
        elif _DECL_HEADING_BARE_RE.match(line) or _DECL_LABEL_BARE_RE.match(line):
            value = ""
        if value is not None:
            normalised = _normalise_repo(value)
            if normalised is None:
                # The value may sit on the next non-blank line. When it does
                # not, the line is left to be parsed on its own — it is often
                # the next heading.
                probe = index + 1
                while probe < len(lines) and not lines[probe].strip():
                    probe += 1
                if probe < len(lines):
                    candidate = _normalise_repo(lines[probe])
                    if candidate is not None:
                        normalised = candidate
                        index = probe
            if normalised is not None and normalised.lower() not in seen:
                seen.add(normalised.lower())
                repos.append(normalised)
        index += 1
    return repos


def _is_own_repo(declared: str, own: str) -> bool:
    """Is ``declared`` the policy's own repo? Compares name and ``owner/name``.

    Case-insensitive, per the board's own usage: ``kushin77/agent-orchestrator``
    and ``agent-orchestrator`` are the same repo, so either spelling is self.
    """

    declared_lower = declared.strip().lower()
    own_lower = own.strip().lower()
    if "/" in declared_lower:
        return declared_lower == own_lower
    own_name = own_lower.rsplit("/", 1)[-1]
    return declared_lower == own_name


def _is_open(issue: Dict[str, Any]) -> bool:
    """Is the issue open? Absent or unreadable state counts as OPEN.

    Fail-closed by contract: only a literal ``closed`` (case-insensitive)
    suppresses a finding. A snapshot that omits ``state`` — or carries
    ``OPEN``/``open``/anything else — is treated as open, so a missing field
    can never silently produce a pass for a real violation.
    """

    state = issue.get("state")
    if state is None or not str(state).strip():
        return True
    return str(state).strip().lower() != "closed"


def check_issues(
    policy: BoundaryPolicy,
    issues: Sequence[Dict[str, Any]],
) -> List[Finding]:
    """Return every boundary violation in ``issues`` ([] when clean).

    Three real checks, all of which must be able to fail:

    ``self-parent``
        The issue's body carries one of ``policy.out_of_scope_markers`` — the
        marker declares the work belongs to a backlog owned elsewhere — yet the
        issue lives on *this* repo's board. The marker found is named in the
        detail so the finding is actionable without re-reading the body.

    ``foreign-repo-issue``
        The issue's body references a repo other than ``policy.own_repo`` in a
        ``Closes owner/repo#N`` / ``Refs owner/repo#N`` / ``Parent:
        owner/repo#N`` form, while the issue itself lives in ``own_repo``.
        A same-repo ``Closes #N`` (no ``owner/repo``) is compliant and is never
        flagged.

    ``foreign-repo-declaration``
        The issue's body declares its repo with the board's own ``## Repo``
        convention and that repo is not ``policy.own_repo`` — a foreign repo's
        backlog item filed on this board. Only **open** issues are flagged (a
        closed child is resolved history, not a live finding) and a missing
        ``state`` counts as open, so the check fails closed. The finding names
        the foreign repo in ``repos`` and in the detail.

    The reference check is case-sensitive on the keyword, as documented: the
    repo's own conventions write them capitalised, so ``refs #12`` is not
    treated as a reference. That is pinned by a test rather than left implicit.
    The declaration check is case-insensitive on the marker and on the repo
    comparison, because the board writes both freely (``## Repo`` vs
    ``## repo``, ``ERP-CRM`` vs ``erp-crm``).

    Marker matching is a deliberate plain-substring test, not a word-boundary
    regex: the detector errs toward over-reporting and a human quarantines the
    false positive by name. A cleverer pattern would silently miss a real child
    issue, which is the failure mode this gate exists to prevent. The
    declaration check is the one deliberate exception — it must be the *whole*
    heading or label — because over-reporting every ``## Repository`` heading
    would drown the finding that matters.
    """

    own = policy.own_repo.strip()
    findings: List[Finding] = []
    for issue in issues:
        number = _as_int(issue.get("number"))
        if number is None:
            continue
        title = str(issue.get("title") or "")
        body = str(issue.get("body") or "")

        for marker in policy.out_of_scope_markers:
            if marker and marker in body:
                findings.append(
                    Finding(
                        issue=number,
                        title=title,
                        finding=FINDING_SELF_PARENT,
                        detail=(
                            "body carries out-of-scope marker %r: the marked "
                            "backlog is owned by another repo, not %s" % (marker, own)
                        ),
                    )
                )

        if _is_open(issue):
            for declared in _declared_repos(body):
                if _is_own_repo(declared, own):
                    continue
                findings.append(
                    Finding(
                        issue=number,
                        title=title,
                        finding=FINDING_FOREIGN_REPO_DECLARATION,
                        detail=(
                            "body declares repo %s, not %s: a foreign repo's "
                            "backlog filed on this board; file a direction "
                            "issue on %s instead" % (declared, own, declared)
                        ),
                        repos=(declared,),
                    )
                )

        seen: set = set()
        for match in _FOREIGN_REF_RE.finditer(body):
            repo, ref_number = match.group(1), match.group(2)
            if repo == own:
                continue
            key = (repo, ref_number)
            if key in seen:
                continue
            seen.add(key)
            findings.append(
                Finding(
                    issue=number,
                    title=title,
                    finding=FINDING_FOREIGN_REPO_ISSUE,
                    detail=(
                        "body references foreign repo %s#%s; file a direction "
                        "issue on %s instead of remediating it from %s"
                        % (repo, ref_number, repo, own)
                    ),
                )
            )
    return findings


_USAGE = "usage: python3 boundary.py --policy-own-repo OWNER/REPO [--issues PATH]"


def _parse_argv(argv: Sequence[str]) -> Dict[str, str]:
    """Minimal ``--flag value`` parser (no argparse dependency surface)."""

    opts: Dict[str, str] = {}
    index = 0
    while index < len(argv):
        token = argv[index]
        if not token.startswith("--"):
            raise ValueError("unexpected argument %r; %s" % (token, _USAGE))
        if index + 1 >= len(argv):
            raise ValueError("flag %s requires a value; %s" % (token, _USAGE))
        opts[token[2:].replace("-", "_")] = argv[index + 1]
        index += 2
    return opts


def main(argv: Optional[Iterable[str]] = None) -> int:
    """CLI entry point. Returns 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.

    CANNOT-ASSESS covers every reason the check could not actually run: missing
    policy, missing snapshot file, unreadable JSON, or an empty issue list. It
    never returns 0 for any of those, because a check that did not run is not a
    check that passed.
    """

    args = list(sys.argv[1:] if argv is None else argv)
    try:
        opts = _parse_argv(args)
    except ValueError as exc:
        print("boundary: CANNOT-ASSESS — %s" % exc, file=sys.stderr)
        return EXIT_CANNOT_ASSESS

    own_repo = opts.get("policy_own_repo", "").strip()
    if not own_repo:
        print(
            "boundary: CANNOT-ASSESS — --policy-own-repo is required; %s" % _USAGE,
            file=sys.stderr,
        )
        return EXIT_CANNOT_ASSESS

    issues_path = opts.get("issues") or str(Path(__file__).with_name("issues.json"))
    try:
        issues = load_issues(issues_path)
    except FileNotFoundError:
        print(
            "boundary: CANNOT-ASSESS — issue snapshot not found: %s" % issues_path,
            file=sys.stderr,
        )
        return EXIT_CANNOT_ASSESS
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(
            "boundary: CANNOT-ASSESS — issue snapshot unreadable (%s): %s"
            % (issues_path, exc),
            file=sys.stderr,
        )
        return EXIT_CANNOT_ASSESS

    if not issues:
        print(
            "boundary: CANNOT-ASSESS — issue snapshot is empty: %s" % issues_path,
            file=sys.stderr,
        )
        return EXIT_CANNOT_ASSESS

    policy = BoundaryPolicy(own_repo=own_repo)
    findings = check_issues(policy, issues)
    if not findings:
        print("boundary: OK — %d issue(s), no boundary violation" % len(issues))
        return EXIT_OK

    for item in findings:
        print(
            "boundary: NOT-OK — #%d [%s] %s" % (item.issue, item.finding, item.detail),
            file=sys.stderr,
        )
    print(
        "boundary: NOT-OK — %d boundary violation(s) across %d issue(s)"
        % (len(findings), len(issues)),
        file=sys.stderr,
    )
    return EXIT_NOT_OK


if __name__ == "__main__":  # pragma: no cover - exercised via main() in tests
    raise SystemExit(main())
