#!/usr/bin/env python3
"""Lane isolation command line — mint, provision, export, audit, landed, close (#263).

---knowledge---
module_id: governance.isolation.cli
system: governance
app: isolation
solution_class: enterprise
patterns: [no-false-green, honesty-tri-state, declared-authority, lane-isolation, commit-trailer]
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [lane_base, lane_own_commits, foreign_authored_commits, audit_lane_full, unregistered_runtime, cmd_open, cmd_env, cmd_audit, cmd_list, cmd_landed, (+5 more)]
invariants: ""
gotchas: ""
related: ["#263", "#287", "#516", "#699", "#739", "#834"]
do_not_duplicate: null
---knowledge---

Typical use, from the execution loop:

    python3 governance/isolation/cli.py open --issue 263 --agent copilot-brain --lane governance-isolation
    eval "$(python3 governance/isolation/cli.py env --issue 263 --agent copilot-brain)"
    python3 governance/isolation/cli.py audit --all
    python3 governance/isolation/cli.py landed --commit <sha>      # real landed history
    python3 governance/isolation/cli.py enforce --range HEAD      # enforced over real history

The identity has two halves, and they are not interchangeable (issue #934):

* ``AO_*`` says *who the session is*. Export it anywhere, including a shell several
  lanes share.
* the git signature says *who signs*. ``open`` already wrote it into the lane's own
  worktree config (``git config --worktree``), which is the mechanism that survives a
  shared shell — so a plain ``git commit`` in the lane is correctly signed. Do NOT
  export ``GIT_AUTHOR_*``/``GIT_COMMITTER_*`` into a shared shell: they outrank the
  worktree config *and* a ``git -c user.email=`` override, so they author whichever
  lane commits next as this session. When a shell has to carry a signature anyway,
  export the ``AO_*`` half (``env --shared-shell``) and prefix the commit with
  ``commit_form``'s command, which scopes the pair to that one process.

Exit-code contract (repo tri-state convention): 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
An audit that assessed nothing is CANNOT-ASSESS, never OK: ``audit`` with no lane
records and ``enforce`` with no commits in range both report that they could not
assess the rule rather than reporting it satisfied (issue #287, GR-12).
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import shlex
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from governance.isolation import journal, live, runtimes, session, speculative  # noqa: E402
from governance.isolation.audit import Violation, audit_lane  # noqa: E402
from governance.isolation.identity import (  # noqa: E402
    IDENTITY_DOMAIN,
    IdentityRefused,
    SessionIdentity,
    mint,
)
from governance.isolation.landed import (  # noqa: E402
    BASELINE_PATH,
    DEFAULT_RANGE,
    EXIT_CODES,
    VERDICT_OK,
    assess,
)
from governance.isolation.trailer import (  # noqa: E402
    PredicateUnavailable,
    classify_commit,
    run_landed,
)
from governance.isolation.worktree import (  # noqa: E402
    ProvisionRefused,
    close,
    default_worktree_root,
    git,
    is_linked_worktree,
    list_records,
    machine_managed_uncommitted,
    main_repo_root,
    provision,
    read_record,
    record_dir,
    write_record,
)

EXIT_OK = 0
EXIT_NOT_OK = 1
EXIT_CANNOT_ASSESS = 2

#: Refs tried, in order, when deriving the commits a lane ADDED. ``origin/master``
#: first and deliberately: the local ``master`` can be stale, and a stale base puts
#: already-landed commits inside the lane's own range — the false-positive shape
#: that gets a gate disabled rather than obeyed (AO-GR-25 measured the same trap
#: for drift detection, #739). ``open`` branches from ``origin/master`` by default,
#: so this is the ref a lane was actually cut from.
LANE_BASE_CANDIDATES = ("origin/master", "master", "origin/HEAD")

#: `git log --format` separator — no address in this doctrine contains it.
_FIELD = "\x1f"


def lane_base(worktree: Path) -> str:
    """The ref this lane was cut from, or ``""`` when none resolves."""
    for candidate in LANE_BASE_CANDIDATES:
        if git(
            worktree, "rev-parse", "--verify", "--quiet", f"{candidate}^{{commit}}"
        ).returncode == 0:
            return candidate
    return ""


def lane_own_commits(worktree: Path) -> list[tuple[str, str]] | None:
    """``(sha, author_email)`` for every non-merge commit this lane ADDED.

    "Added" is scoped to the lane's own range — ``merge-base(HEAD, <base>)..HEAD``
    — so the commits the base branch already carried (this repository's other
    sessions' landed work, merged in to stay current) are not this lane's history
    and are never attributed to it. Merges are excluded for the same reason the
    shared predicate excludes them: git generates a merge message, not the session.

    ``None`` means the range itself could not be derived, which is NOT the same
    answer as "this lane added nothing": an unmeasurable rule is reported as
    unproven rather than as satisfied (GR-12).
    """
    base = lane_base(worktree)
    if not base:
        return None
    merge_base = git(worktree, "merge-base", "HEAD", base)
    if merge_base.returncode != 0:
        return None
    log = git(
        worktree,
        "log",
        "--no-merges",
        "--format=%H" + _FIELD + "%ae",
        f"{merge_base.stdout.strip()}..HEAD",
    )
    if log.returncode != 0:
        return None
    commits: list[tuple[str, str]] = []
    for line in log.stdout.splitlines():
        sha, _, email = line.strip().partition(_FIELD)
        if sha and email:
            commits.append((sha, email.strip()))
    return commits


def foreign_authored_commits(identity: SessionIdentity, main: Path | str) -> list[Violation]:
    """Commits this lane added that ANOTHER session's identity authored.

    This is the rule the ambient ``GIT_*`` pair would otherwise hide.
    :func:`~governance.isolation.audit.audit_lane` selects a lane's commits *by
    author address*, so a commit authored by a different agent is not merely
    unchecked — it is invisible, and the lane reports as isolated. Measured (issue
    #934): with rule 15's identity env exported, a lane whose only commit was
    authored by ``agent+subagent-366@agents.invalid`` audited clean. The gate was
    not red, it was **blinded** — and that is the part that must not recur.

    A commit authored by a non-agent identity is deliberately not flagged: what is
    enforced is that a *session*'s own range belongs to that session, not that
    nobody else may ever touch a lane branch. ``main`` is accepted for symmetry with
    ``audit_lane`` and for the callers that have only the main repository to hand.
    """
    del main  # the rule is about this lane's own worktree and its own range
    worktree = identity.worktree
    if not worktree.exists() or not is_linked_worktree(worktree):
        # `audit_lane` already refuses these by name; there is no range to read.
        return []

    own = lane_own_commits(worktree)
    if own is None:
        return [
            Violation(
                "commit-authorship-unmeasurable",
                f"the commits {worktree} added could not be derived — none of "
                f"{' or '.join(LANE_BASE_CANDIDATES)} resolves, or git could not read the "
                "range — so this lane's authorship rule is unproven rather than satisfied",
            )
        ]

    foreign = [
        (sha, email)
        for sha, email in own
        if email != identity.author_email and email.endswith(f"@{IDENTITY_DOMAIN}")
    ]
    if not foreign:
        return []
    named = ", ".join(f"{sha[:8]} (authored by {email})" for sha, email in foreign[:5])
    if len(foreign) > 5:
        named += f", +{len(foreign) - 5} more"
    return [
        Violation(
            "commit-authored-by-another-session",
            f"{len(foreign)} of the {len(own)} commit(s) this lane added were authored by a "
            f"different agent session, not by {identity.author_name} <{identity.author_email}>: "
            f"{named}. Every commit in a lane's own range belongs to the lane session — an "
            "exported GIT_AUTHOR_*/GIT_COMMITTER_* pair outranks the worktree signature and "
            "silently re-attributes them (issue #934)",
        )
    ]


def audit_lane_full(identity: SessionIdentity, main: Path | str) -> list[Violation]:
    """``audit_lane`` plus the rules the audit surface owns: authorship-ownership
    (#934) and the session rule (#917 — a session-minted lane whose session is
    gone is refused by name, ``lane-session-gone``)."""
    return [
        *audit_lane(identity, main),
        *foreign_authored_commits(identity, main),
        *session.session_gone(identity, main),
        *unregistered_runtime(identity, main),
    ]


def unregistered_runtime(identity: SessionIdentity, main: Path | str) -> list[Violation]:
    """``runtime-unregistered`` for a record naming a runtime the registry lacks (#1301).

    A record written by hand, or by a runtime whose row was since removed, is
    refused here the same way ``open`` refuses it up front — the registry is
    the vocabulary for both, so the two can never disagree. An unreadable
    registry is unproven, never satisfied.
    """
    try:
        refusal = runtimes.unregistered(identity.runtime, main)
    except runtimes.RegistryUnreadable as exc:
        return [Violation("runtime-unregistered", f"the runtime registry could not be read, so {identity.runtime!r} is unproven: {exc}")]
    if not refusal:
        return []
    return [Violation("runtime-unregistered", refusal.split(": ", 1)[-1] if ": " in refusal else refusal)]


def _mint(args: argparse.Namespace) -> SessionIdentity:
    root = Path(args.root) if args.root else default_worktree_root()
    return mint(
        issue=args.issue,
        agent_id=args.agent,
        lane=args.lane or "",
        suffix=args.suffix or "",
        worktree_root=root,
    )


def _utc_now() -> str:
    import time

    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def cmd_open(args: argparse.Namespace) -> int:
    """Mint the identity and create the lane — worktree, branch, signature, session."""
    identity = _mint(args)
    main = Path(args.main)
    if not main.exists():
        print(f"open: CANNOT-ASSESS — {main} does not exist", file=sys.stderr)
        return EXIT_CANNOT_ASSESS
    # #1301: the binding fields are validated BEFORE anything is created. A
    # runtime the registry does not carry is refused by name and nothing is
    # provisioned; an absent runtime is named in the payload, not refused (the
    # dispatchers that mint lanes today do not pass one yet — see runtimes.py).
    runtime = (args.runtime or "").strip()
    try:
        refusal = runtimes.unregistered(runtime, main)
    except runtimes.RegistryUnreadable as exc:
        print(f"open: CANNOT-ASSESS — {exc}", file=sys.stderr)
        return EXIT_CANNOT_ASSESS
    if refusal:
        print(f"open: NOT-OK — {refusal}", file=sys.stderr)
        return EXIT_NOT_OK
    brief_hash = (args.brief_hash or "").strip()
    if args.brief:
        try:
            brief_hash = hashlib.sha256(Path(args.brief).read_bytes()).hexdigest()
        except OSError as exc:
            print(f"open: CANNOT-ASSESS — the brief {args.brief} could not be read: {exc}", file=sys.stderr)
            return EXIT_CANNOT_ASSESS
    try:
        result = provision(
            identity,
            main,
            base=args.base,
            fetch_remote="origin" if args.fetch else "",
            allow_tmpfs=args.allow_tmpfs_root,
        )
    except ProvisionRefused as refused:
        # A refusal is a decision, not a crash. Nothing was created, and the
        # reason names itself — e.g. lane-worktree-on-tmpfs (issue #516).
        print(f"open: NOT-OK — {refused}", file=sys.stderr)
        return EXIT_NOT_OK
    # #917: the lane's session is stamped by the mint itself, in the sweeper's
    # own vocabulary, so `.fleet/sessions/` is populated the moment a lane
    # exists rather than only when a runtime remembers to beat. A re-open of an
    # existing lane keeps its original `opened_at` and refreshes the beat.
    existing = read_record(identity.session_id, main)
    opened_at = existing.opened_at if existing is not None and existing.opened_at else _utc_now()
    identity = dataclasses.replace(
        identity,
        opened_at=opened_at,
        runtime=runtime or (existing.runtime if existing is not None else ""),
        actor=(args.actor or "").strip() or (existing.actor if existing is not None else ""),
        brief_hash=brief_hash or (existing.brief_hash if existing is not None else ""),
    )
    write_record(identity, main)
    beat = session.stamp_for(identity, main, pid=args.pid)
    if args.speculative_base:
        # DG-3 (#699): the lane was cut from an upstream LANE'S BRANCH instead of
        # waiting for its squash-merge. Recording the claim here — not as a
        # separate verb — is what "claim --base <upstream-branch>" reduces to on
        # the isolation side: open the lane exactly as any other, but also
        # attest what it was actually cut from, so the re-verify gate
        # (governance/isolation/speculative.py) has something to check before
        # this lane is allowed to open a PR.
        speculative.claim(main, identity, args.speculative_base, base=speculative.DEFAULT_BASE)
    problems = audit_lane_full(identity, main) if result.ok else []
    payload = {
        "identity": identity.to_json(),
        "created": result.created,
        # The identity as an ENVIRONMENT is the AO_* half ONLY. Printing the git
        # pair here is what made rule 15's "export the identity" a trap in a shared
        # shell: those four variables outrank every config-based signature, so
        # `eval`ing a block that contains them arms whichever lane commits next with
        # THIS session's signature (issue #934). The pair is printed as what it is —
        # a per-commit argument, with the exact command that applies it.
        "env": identity.shared_shell_env(),
        "git_signature": identity.git_signature(),
        "commit_form": identity.commit_form(),
        # #917: the session beat this mint wrote, or why it could not. Never
        # silently absent — a caller that needs the sweeper to see this lane
        # can read the answer here.
        "session": (
            {"stamped": True, "pid": beat.pid, "at": beat.to_json()["at_iso"]}
            if beat is not None
            else {"stamped": False, "reason": "unstamped: governance.reconcile is not importable from this root"}
        ),
        "problems": [str(problem) for problem in problems],
        # #1301: what the lane is bound to. `runtime-unrecorded` is a NOTE, never
        # a refusal — see runtimes.py for why.
        "binding": {
            "lane_id": identity.lane_id,
            "runtime": identity.runtime or None,
            "actor": identity.actor or None,
            "brief_hash": identity.brief_hash or None,
            "notes": [] if identity.runtime else [f"{runtimes.RUNTIME_UNRECORDED}: pass --runtime <id> so the lane is bound to a registered runtime"],
        },
    }
    print(json.dumps(payload, indent=2))
    if problems:
        print(f"open: NOT-OK — lane provisioned but {len(problems)} isolation problem(s)", file=sys.stderr)
        return EXIT_NOT_OK
    print(f"open: OK — lane {identity.session_id} on {identity.branch} at {identity.worktree}", file=sys.stderr)
    return EXIT_OK


def cmd_env(args: argparse.Namespace) -> int:
    """The identity as environment: the session's own id and signature.

    ``--shared-shell`` withholds the four ``GIT_*`` variables and prints only the
    ``AO_*`` half, which is the part safe to export where another lane may commit
    (issue #934). The default is unchanged because the spawned-process caller wants
    the signature too — it is the *shared shell* that must not receive it.
    """
    identity = _mint(args)
    environment = identity.shared_shell_env() if args.shared_shell else identity.env()
    if args.json:
        print(json.dumps(environment, indent=2))
    else:
        print(
            "\n".join(
                f"export {name}={shlex.quote(value)}"
                for name, value in sorted(environment.items())
            )
        )
    return EXIT_OK


def cmd_audit(args: argparse.Namespace) -> int:
    """Re-derive isolation from the real worktrees and refuse when it is broken."""
    main = Path(args.main)
    if not main.exists():
        print(f"audit: CANNOT-ASSESS — {main} does not exist", file=sys.stderr)
        return EXIT_CANNOT_ASSESS

    if args.session:
        identity = read_record(args.session, main)
        if identity is None:
            print(f"audit: CANNOT-ASSESS — no lane record for session {args.session}", file=sys.stderr)
            return EXIT_CANNOT_ASSESS
        if args.reverify_speculative_base:
            try:
                attestation = speculative.reverify(main, identity)
            except speculative.SpeculationRefused as refused:
                print(f"audit: CANNOT-ASSESS — {refused}", file=sys.stderr)
                return EXIT_CANNOT_ASSESS
            print(
                f"  re-verified lane {identity.session_id}: merge_base={attestation.merge_base[:8]} "
                f"git_sha={attestation.git_sha[:8]}",
                file=sys.stderr,
            )
        results = {identity.session_id: audit_lane_full(identity, main)}
    else:
        results = {
            identity.session_id: audit_lane_full(identity, main)
            for identity in list_records(main)
        }

    if not results:
        # An empty audit is not a pass (issue #287): with no lane records there is
        # nothing to re-derive, so the honest answer is CANNOT-ASSESS. Reporting OK
        # here is exactly the vacuous green the no-false-green doctrine rejects —
        # and it is indistinguishable from a machine where lanes were never
        # provisioned, which is the failure the rule exists to catch.
        print(
            f"session-isolation: CANNOT-ASSESS — no lane records under {record_dir(main)}, so no lane "
            "was audited; an empty audit is not a pass",
            file=sys.stderr,
        )
        return EXIT_CANNOT_ASSESS

    failed = 0
    for session_id, problems in sorted(results.items()):
        # Every verdict is appended to the durable trail before it is reported,
        # so a refusal (or a clean pass) this run prints is also a record a
        # later run — or a human — can read back without having re-run the
        # audit (governance/isolation/journal.py, issue #885).
        journal.append(main, session_id, problems)
        if problems:
            failed += 1
            print(f"  FAIL  lane {session_id}", file=sys.stderr)
            for problem in problems:
                print(f"          {problem}", file=sys.stderr)
        else:
            print(f"  OK    lane {session_id}")

    if args.live:
        print("  -- live projection (governance/isolation/live.py) --")
        rendered = live.render(main)
        print(rendered if rendered else "  (no recorded lanes to project)")

    if failed:
        print(f"session-isolation: FAIL ({failed} of {len(results)} lane(s) not isolated)", file=sys.stderr)
        return EXIT_NOT_OK
    print(f"session-isolation: OK ({len(results)} lane(s) isolated)")
    return EXIT_OK


def cmd_list(args: argparse.Namespace) -> int:
    main = Path(args.main)
    identities = list_records(main)
    if args.json:
        print(json.dumps([identity.to_json() for identity in identities], indent=2))
        return EXIT_OK
    for identity in identities:
        print(f"{identity.session_id}  #{identity.issue}  {identity.branch}  {identity.agent_id}  {identity.worktree}")
    return EXIT_OK


def cmd_landed(args: argparse.Namespace) -> int:
    """Re-check the ticket-trailer rule against real landed history.

    The lane audit re-derives isolation from the *live* worktrees, so history
    that already landed on the default branch is never examined — the second
    gap #287 names. This mode points the same rule at commits that have landed:
    one named commit (``--commit``), or a whole range. Either way the verdict is
    the shared predicate's (``scripts/check-pr-contract.sh``), so this is a
    second *surface* for the rule and never a second rule.
    """
    main = Path(args.main)
    if not main.exists():
        print(f"landed: CANNOT-ASSESS — {main} does not exist", file=sys.stderr)
        return EXIT_CANNOT_ASSESS

    if args.commit:
        resolved = git(main, "rev-parse", "--verify", f"{args.commit}^{{commit}}")
        if resolved.returncode != 0:
            print(f"landed: CANNOT-ASSESS — {args.commit} does not resolve to a commit in {main}", file=sys.stderr)
            return EXIT_CANNOT_ASSESS
        sha = resolved.stdout.strip()
        author = git(main, "log", "-1", "--format=%an <%ae>", sha).stdout.strip()
        try:
            finding = classify_commit(main, sha)
        except PredicateUnavailable as exc:
            print(f"landed: CANNOT-ASSESS — {exc}", file=sys.stderr)
            return EXIT_CANNOT_ASSESS
        if finding:
            print(f"  FAIL  {finding}:{sha[:12]} (authored by {author})", file=sys.stderr)
            print(
                "isolation-landed: FAIL — a landed commit does not carry `Refs <slug>#<n>` "
                "in its trailing trailer block",
                file=sys.stderr,
            )
            return EXIT_NOT_OK
        print(f"  OK    {sha[:12]} carries the ticket trailer in its trailing block (authored by {author})")
        return EXIT_OK

    try:
        result = run_landed(main, args.range_, args.gate)
    except PredicateUnavailable as exc:
        print(f"landed: CANNOT-ASSESS — {exc}", file=sys.stderr)
        return EXIT_CANNOT_ASSESS
    # The shared gate's own output, verbatim: the verdict is delegated, not
    # re-interpreted. The grandfathered boundary it reports is part of that
    # verdict.
    if result.output.strip():
        print(result.output.rstrip(), file=sys.stderr)
    if result.returncode == 0:
        print(
            f"isolation-landed: OK — no landed commit in {args.range_} lacks the ticket trailer "
            "where the shared predicate requires it"
        )
        return EXIT_OK
    if result.returncode == EXIT_CANNOT_ASSESS:
        return EXIT_CANNOT_ASSESS
    print(f"isolation-landed: FAIL — the shared predicate named landed commit(s) in {args.range_}", file=sys.stderr)
    return EXIT_NOT_OK


def cmd_enforce(args: argparse.Namespace) -> int:
    """Enforce the ticket-trailer rule over landed history, with recorded legacy.

    ``landed`` answers "what does the shared predicate say about this history";
    ``enforce`` answers "does this history satisfy the rule, given the legacy that
    was measured before the rule existed" — the surface issue #287 found missing.
    The quarantine is keyed by commit and can only shrink: a recorded entry that
    now complies is a failure, and an entry outside the range leaves the rule
    unproven rather than satisfied.
    """
    main = Path(args.main)
    if not main.exists():
        print(f"enforce: CANNOT-ASSESS — {main} does not exist", file=sys.stderr)
        return EXIT_CANNOT_ASSESS
    result = assess(main, args.range_, args.baseline, args.gate)
    stream = sys.stderr if result.verdict != VERDICT_OK else sys.stdout
    for line in result.lines():
        print(line, file=stream)
    return EXIT_CODES[result.verdict]


def cmd_close(args: argparse.Namespace) -> int:
    """Remove a lane's worktree — never discarding the lane's own work silently.

    What was *ignored* is reported, not hidden: a reclaim that proceeded past
    machine-managed board state says which paths it passed over (#834), so
    "the worktree looked clean" and "the worktree was dirty only in state the
    fleet regenerates" are distinguishable in the output.
    """
    main = Path(args.main)
    identity = read_record(args.session, main)
    if identity is None:
        print(f"close: CANNOT-ASSESS — no lane record for session {args.session}", file=sys.stderr)
        return EXIT_CANNOT_ASSESS
    ignored = machine_managed_uncommitted(identity.worktree) if identity.worktree.exists() and not args.force else []
    kept = close(identity, main, force=args.force)
    if kept:
        for reason in kept:
            print(f"close: NOT-OK — {reason}", file=sys.stderr)
        return EXIT_NOT_OK
    # #917: the session ends with the lane. The beat is cleared only once the
    # worktree is actually gone, so a kept lane keeps its session too.
    session.clear_for(identity, main)
    note = f" (ignored machine-managed state: {', '.join(ignored)})" if ignored else ""
    print(f"close: OK — lane {identity.session_id} removed{note}")
    return EXIT_OK


def default_main() -> str:
    """The repository the caller is working in (its own main checkout)."""
    try:
        return str(main_repo_root(Path.cwd()))
    except ProvisionRefused:
        return "."


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="session", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    def add_mint_args(target: argparse.ArgumentParser) -> None:
        target.add_argument("--issue", type=int, required=True)
        target.add_argument("--agent", required=True, help="agent slug, never an email address")
        target.add_argument("--lane", default="", help="the lane that owns the issue")
        target.add_argument("--suffix", default="", help="disambiguates a second session on one issue")
        target.add_argument("--root", default="", help="worktree root (default $AO_WORKTREE_ROOT or ~/ao-worktrees)")

    open_cmd = sub.add_parser("open", help="mint the identity and create the lane")
    add_mint_args(open_cmd)
    open_cmd.add_argument("--main", default=default_main(), help="the repository to add the worktree to")
    open_cmd.add_argument("--base", default="origin/master", help="the commit the lane branches from")
    open_cmd.add_argument("--fetch", action="store_true", help="fetch origin/master first")
    open_cmd.add_argument(
        "--runtime",
        default="",
        help=(
            "the registry id of the runtime opening this lane (#1301) — one of fleet/runtimes.yaml's "
            "rows, or until that file lands: " + ", ".join(runtimes.FALLBACK_RUNTIME_IDS) + ". An "
            "unregistered id is refused by name (runtime-unregistered) before anything is created"
        ),
    )
    open_cmd.add_argument("--actor", default="", help="the identity the runtime acts as (resolved by #1275); recorded, not validated here")
    open_cmd.add_argument("--brief", default="", help="the brief file this lane was dispatched with; its sha256 is recorded as brief_hash")
    open_cmd.add_argument("--brief-hash", dest="brief_hash", default="", help="the brief's sha256, when the caller already has it")
    open_cmd.add_argument(
        "--pid",
        type=int,
        default=None,
        help=(
            "the process that OWNS the lane, recorded in its session beat (#917); default: the "
            "parent of this mint, because the mint exits as soon as it has printed the identity"
        ),
    )
    open_cmd.add_argument(
        "--allow-tmpfs-root",
        action="store_true",
        help="accept a RAM-backed worktree root — throwaway gate scratch only; a lane on tmpfs is lost on reboot (#516)",
    )
    open_cmd.add_argument(
        "--speculative-base",
        default="",
        help=(
            "the upstream LANE's branch this lane was actually cut from (DG-3, #699), when it differs "
            "from --base. Recording this is what distinguishes a speculative claim from an out-of-order "
            "one; governance/isolation/speculative.py's re-verify gate refuses this lane's PR until the "
            "claim is re-verified against master (see `audit --reverify-speculative-base`)."
        ),
    )
    open_cmd.set_defaults(func=cmd_open)

    env_cmd = sub.add_parser("env", help="print the session environment (id + signature)")
    add_mint_args(env_cmd)
    env_cmd.add_argument("--json", action="store_true")
    env_cmd.add_argument(
        "--shared-shell",
        action="store_true",
        help=(
            "print only the AO_* half — the part safe to export where another lane may "
            "commit; the GIT_* pair outranks `git config --worktree` and a `git -c "
            "user.email=` override, so exporting it re-signs the next commit in that "
            "shell as this session (issue #934)"
        ),
    )
    env_cmd.set_defaults(func=cmd_env)

    audit_cmd = sub.add_parser("audit", help="verify lanes satisfy the isolation contract")
    audit_cmd.add_argument("--main", default=default_main())
    audit_cmd.add_argument("--session", default="", help="audit one lane (default: every recorded lane)")
    audit_cmd.add_argument("--all", action="store_true", help="accepted for symmetry with --session")
    audit_cmd.add_argument(
        "--reverify-speculative-base",
        action="store_true",
        help=(
            "before auditing, re-verify this lane's speculative-base claim (#699): re-derive merge_base "
            "and git_sha from git right now and re-record them. Requires --session; refuses (CANNOT-ASSESS) "
            "a lane with no prior claim. Run this once, right before opening the PR."
        ),
    )
    audit_cmd.add_argument(
        "--live",
        action="store_true",
        help=(
            "also print a live projection of recorded lane identities and speculative "
            "attestations against the real worktrees/git right now (governance/isolation/live.py), "
            "and report drift by name. Does not replace the audit verdict above it."
        ),
    )
    audit_cmd.set_defaults(func=cmd_audit)

    list_cmd = sub.add_parser("list", help="list provisioned lanes")
    list_cmd.add_argument("--main", default=default_main())
    list_cmd.add_argument("--json", action="store_true")
    list_cmd.set_defaults(func=cmd_list)

    landed_cmd = sub.add_parser("landed", help="re-check the ticket-trailer rule against landed history")
    landed_cmd.add_argument("--main", default=default_main(), help="the repository whose landed history is re-checked")
    landed_cmd.add_argument("--commit", default="", help="classify one landed commit (any rev)")
    landed_cmd.add_argument("--range", dest="range_", default="HEAD", help="the landed range to re-check (default HEAD)")
    landed_cmd.add_argument("--gate", default="", help="the enforcement boundary commit (default: the shared gate's own)")
    landed_cmd.set_defaults(func=cmd_landed)

    enforce_cmd = sub.add_parser(
        "enforce",
        help="enforce the ticket-trailer rule over landed history against recorded legacy (#287)",
    )
    enforce_cmd.add_argument("--main", default=default_main(), help="the repository whose landed history is enforced")
    enforce_cmd.add_argument(
        "--range",
        dest="range_",
        default=DEFAULT_RANGE,
        help=f"the landed range to enforce over (default {DEFAULT_RANGE}: everything reachable from the branch)",
    )
    enforce_cmd.add_argument(
        "--baseline",
        default=str(BASELINE_PATH),
        help="the recorded-legacy baseline (default: governance/isolation/landed-baseline.json)",
    )
    enforce_cmd.add_argument("--gate", default="", help="the enforcement boundary commit (default: the shared gate's own)")
    enforce_cmd.set_defaults(func=cmd_enforce)

    close_cmd = sub.add_parser("close", help="remove a lane worktree")
    close_cmd.add_argument("--session", required=True)
    close_cmd.add_argument("--main", default=default_main())
    close_cmd.add_argument("--force", action="store_true", help="remove even with uncommitted work")
    close_cmd.set_defaults(func=cmd_close)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (IdentityRefused, ProvisionRefused) as exc:
        print(f"{args.command}: REFUSED — {exc}", file=sys.stderr)
        return EXIT_NOT_OK


if __name__ == "__main__":
    raise SystemExit(main())
