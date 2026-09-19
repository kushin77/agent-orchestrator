#!/usr/bin/env python3
"""Spawn envelope command line — the LOCAL spawn path, and the refusal it shares.

Typical use, by an agent about to spawn a subagent locally:

    python3 governance/spawn/cli.py open --issue 793 --agent me --lane spawn-envelope

and, read-only, to see what a spawn would carry:

    python3 governance/spawn/cli.py build --issue 793 --lane spawn-envelope --render

Exit-code contract (repo tri-state, plus the refusal): 0 OK / 1 NOT-OK /
2 CANNOT-ASSESS / 78 REFUSED. The refusal is a DISTINCT code on purpose: a spawn
that cannot present a well-formed envelope is a decision, not a crash and not a
failure of the work, so nothing may fold it into 1. It matches
``fleet/terminal.py::RC_REFUSED`` so the loop and this CLI refuse alike.

``open`` is the local path end to end, and it is the same path the fleet loop
takes: it claims the issue (through ``governance/dispatch``, against ITS OWN
root's board), mints the lane (through ``governance/isolation``), assembles the
envelope from the same producers, writes it beside the run markers, and prints
the block the subagent is spawned with. **A refusal releases the claim**: every
artifact it created reaches a terminal state, or the refusal would itself strand
work.

`--root` scopes the whole spawn (board, ledger, fleet dir) so a caller can drive
it against a scratch tree; the default is this checkout.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from governance import spawn as envelope  # noqa: E402
from governance.spawn import model, render, sources  # noqa: E402

BOARD = Path(".board")
FLEET = Path(".fleet")
#: Where a produced envelope is kept, beside the run markers it describes.
SPAWN_DIR = FLEET / "spawn"


def _board_paths(root: Path) -> dict[str, Path]:
    """The board artifacts a spawn reads and claims against, scoped to `root`."""
    return {
        "snapshot": root / BOARD / "snapshot.json",
        "ledger": root / BOARD / "claims",
        "locks": root / BOARD / "locks",
        "focus": root / BOARD / "focus.json",
    }


def _refuse(refusals: Any) -> int:
    """Report every refusal, by name, and return the refusal exit code."""
    lines = [refusal.line() for refusal in refusals]
    for line in lines or ["envelope: malformed"]:
        print(f"spawn: REFUSED — {line}", file=sys.stderr)
    print(
        f"spawn: the spawn is refused (rc {model.EXIT_REFUSED}); nothing was spawned. "
        "Fix every field above and re-run.",
        file=sys.stderr,
    )
    return model.EXIT_REFUSED


def _claim(args: argparse.Namespace, root: Path) -> tuple[bool, str]:
    """Take the claim through the real dispatcher, against THIS root's board."""
    paths = _board_paths(root)
    command = [
        sys.executable, str(ROOT / "governance" / "dispatch" / "cli.py"), "claim",
        "--issue", str(args.issue),
        "--agent", args.agent,
        "--lane", args.lane,
        "--snapshot", str(paths["snapshot"]),
        "--ledger", str(paths["ledger"]),
        "--locks", str(paths["locks"]),
    ]
    if args.directive:
        command += ["--directive", args.directive]
    result = subprocess.run(command, cwd=str(root), capture_output=True, text=True)
    detail = (result.stdout + result.stderr).strip()
    return result.returncode == 0, detail


def _release(args: argparse.Namespace, root: Path) -> str:
    """Release a claim a refused spawn took — a refusal must strand nothing."""
    result = subprocess.run(
        [
            sys.executable, str(ROOT / "governance" / "dispatch" / "cli.py"), "release",
            "--issue", str(args.issue), "--agent", args.agent,
            "--ledger", str(_board_paths(root)["ledger"]),
        ],
        cwd=str(root),
        capture_output=True,
        text=True,
    )
    return (result.stdout + result.stderr).strip()


def _mint(args: argparse.Namespace, root: Path, record: Mapping[str, Any] | None = None) -> tuple[dict[str, str], str]:
    """Mint the lane through `governance/isolation`; return (env, worktree).

    The admission the spawn was granted travels with the mint: `isolation open`
    records the lane's `runtime` and `actor` in the lane record (#1301), so the
    four lane-binding values `{runtime, role, tier, actor}` are not only in the
    envelope but in the record every runtime's plane reads. `role` and `tier` are
    NOT isolation's fields — see the note in this module's `cmd_open`.
    """
    binding = dict(record or {})
    command = [
        sys.executable, str(ROOT / "governance" / "isolation" / "cli.py"), "open",
        "--issue", str(args.issue),
        "--agent", args.agent,
        "--lane", args.lane,
        "--main", str(root),
        "--base", args.base,
    ]
    if binding.get("runtime"):
        command += ["--runtime", str(binding["runtime"])]
    if binding.get("actor"):
        command += ["--actor", str(binding["actor"])]
    if args.fetch:
        command.append("--fetch")
    if args.worktree_root:
        command += ["--root", args.worktree_root]
    result = subprocess.run(command, cwd=str(root), capture_output=True, text=True)
    if result.returncode != 0:
        return {}, (result.stderr or result.stdout).strip()[-400:]
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}, "lane provisioning returned unreadable JSON"
    return dict(payload.get("env") or {}), ""


def _body(args: argparse.Namespace) -> str | None:
    """The issue body, when the caller supplies one.

    Offline by default: an absent body is not an error, because the envelope's
    `verify` field falls back to the gate of record — but a lane that wants the
    issue's OWN clause says so, by file or with `--from-github`.
    """
    if args.body_file:
        try:
            return Path(args.body_file).read_text(encoding="utf-8")
        except OSError as exc:
            print(f"spawn: body unreadable — {exc}", file=sys.stderr)
            return None
    if args.body:
        return args.body
    if args.from_github:
        result = subprocess.run(
            ["gh", "api", f"repos/{args.repo}/issues/{args.issue}", "--jq", ".body"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return result.stdout
        print(f"spawn: board read failed — {result.stderr.strip()}", file=sys.stderr)
    return None


def _write(document: Mapping[str, Any], root: Path) -> Path:
    """Persist the envelope beside the run markers; a spawn that wrote nothing is invisible."""
    directory = root / SPAWN_DIR
    directory.mkdir(parents=True, exist_ok=True)
    session = str(document.get("session", {}).get("id") or f"issue-{document.get('issue')}")
    target = directory / f"{session}.json"
    tmp = target.with_suffix(".tmp")
    tmp.write_text(model.dumps(document), encoding="utf-8")
    tmp.replace(target)
    return target


def _produce(args: argparse.Namespace, root: Path, env: Mapping[str, str], worktree: str) -> dict:
    return envelope.produce(
        issue=args.issue,
        lane=args.lane,
        agent_id=args.agent,
        path=args.path,
        directive_id=args.directive,
        env=env,
        worktree=worktree,
        body=_body(args),
        files=args.files,
        root=root,
        runtime=args.runtime,
        role=args.role,
        tier=args.tier,
        model=args.model,
        task_class=args.spawn_class,
        actor=args.actor,
        verbs=args.verb,
        skills=args.skill,
        secrets=args.secret,
    )


def _admission_record(args: argparse.Namespace, env: Mapping[str, str]) -> dict:
    """The spawn block the three judges read, before anything is created.

    Resolved by the same producer the envelope uses (`admission.spawn_record`), so
    the record judged here and the record the admitted envelope carries are the
    same object's two readings rather than two computations that might disagree.
    """
    from governance.spawn import admission  # noqa: PLC0415 - the admission inputs' one producer

    return admission.spawn_record(
        path=args.path,
        agent=args.agent,
        directive=args.directive,
        env=env,
        runtime=args.runtime,
        role=args.role,
        tier=args.tier,
        model=args.model,
        task_class=args.spawn_class,
        actor=args.actor,
        verbs=args.verb,
        skills=args.skill,
        secrets=args.secret,
    )


# --- verbs -------------------------------------------------------------------


def cmd_open(args: argparse.Namespace) -> int:
    """The local spawn path: admit, claim, mint, assemble, write, print — or refuse.

    Admission comes FIRST, before the claim and before the mint, because a spawn
    that is refused must not leave anything behind to unwind: no worktree may
    exist for a spawn at a forbidden tier or by an undeclared actor (issue
    #1413). The refusal prints which judge refused it; `scripts/check-spawn-envelope.sh`
    proves the ordering by driving a REFUSED spawn with minting ENABLED and
    requiring the mint never to have been attempted.

    The lane record `isolation open` writes carries `runtime` and `actor` (its own
    #1301 fields) from that same admission; `role` and `tier` are NOT isolation
    fields, so they live in the envelope this command writes beside the run
    markers (`.fleet/spawn/<session>.json`) until the isolation record declares
    them.
    """
    root = sources.env_root(args.root)
    env: dict[str, str] = dict(os.environ)
    record = _admission_record(args, env)
    refusals = model.admission_refusals({"spawn": record})
    if refusals:
        print(
            "spawn: refused at the admission point — nothing was created by this refusal "
            "(no claim was taken and no lane was provisioned)",
            file=sys.stderr,
        )
        return _refuse(refusals)
    worktree = args.worktree or ""
    claimed = False
    if not args.no_claim:
        ok, detail = _claim(args, root)
        claimed = ok
        if not ok:
            print(f"spawn: claim not taken — {detail}", file=sys.stderr)
    if not args.no_mint:
        minted, problem = _mint(args, root, record)
        if problem:
            print(f"spawn: lane not minted — {problem}", file=sys.stderr)
        else:
            env.update(minted)
            worktree = worktree or str(minted.get("AO_WORKTREE") or "")
    worktree = worktree or str(env.get("AO_WORKTREE") or "")
    try:
        document = _produce(args, root, env, worktree)
    except model.EnvelopeRefused as refused:
        if claimed:
            print(f"spawn: releasing the claim it took — {_release(args, root)}", file=sys.stderr)
        return _refuse(refused.refusals)
    target = _write(document, root)
    block = render.render(document, args.standing)
    if args.json:
        print(model.dumps(document), end="")
    else:
        print(block)
    print(f"spawn: ADMITTED {document['schema']} — wrote {target}", file=sys.stderr)
    return model.EXIT_OK


def cmd_build(args: argparse.Namespace) -> int:
    """Produce the envelope and print it (or its rendered block). Read-only."""
    root = sources.env_root(args.root)
    try:
        document = _produce(args, root, dict(os.environ), args.worktree or "")
    except model.EnvelopeRefused as refused:
        return _refuse(refused.refusals)
    print(render.render(document, args.standing) if args.render_block else model.dumps(document), end="")
    return model.EXIT_OK


def _read_document(args: argparse.Namespace) -> tuple[dict | None, int]:
    text = Path(args.file).read_text(encoding="utf-8") if args.file else sys.stdin.read()
    try:
        document = model.parse(text)
    except model.EnvelopeRefused as refused:
        return None, _refuse(refused.refusals)
    for field in args.without or []:
        document.pop(field, None)
    return document, model.EXIT_OK


def cmd_check(args: argparse.Namespace) -> int:
    """Validate a document. `--without FIELD` is the documented provocation."""
    document, code = _read_document(args)
    if document is None:
        return code
    refusals = model.validate(document)
    if refusals:
        return _refuse(refusals)
    print(
        f"spawn: OK — {document.get('schema')} for issue #{document.get('issue')} "
        f"lane {document.get('lane')!r} is well formed "
        f"({len(model.REQUIRED_FIELDS)} required fields present)"
    )
    return model.EXIT_OK


def cmd_render(args: argparse.Namespace) -> int:
    """Render an admitted document as the prompt block a subagent is spawned with."""
    document, code = _read_document(args)
    if document is None:
        return code
    refusals = model.validate(document)
    if refusals:
        return _refuse(refusals)
    print(render.render(document, args.standing), end="")
    return model.EXIT_OK


# --- the parser --------------------------------------------------------------


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--issue", type=int, required=True)
    parser.add_argument("--lane", default="")
    parser.add_argument("--agent", default="")
    parser.add_argument("--directive", default="")
    parser.add_argument("--root", default="")
    parser.add_argument("--worktree", default="")
    # The admission inputs (issue #1413), judged BEFORE anything is created. Each
    # falls back to its AO_* variable, then to the declaration the tables already
    # make — so a caller that declares nothing is still judged, on values.
    parser.add_argument(
        "--runtime", default="",
        help="the wire runtime id this spawn runs as (default $AO_RUNTIME, then claude-subagent)",
    )
    parser.add_argument(
        "--role", default="",
        help="the FinOps role the spawn asks under (default $AO_ROLE, then the role its --path speaks as)",
    )
    parser.add_argument(
        "--tier", default="",
        help="the requested model tier (L0/L1/L2, default $AO_MODEL_TIER, then the class's own defaultTier)",
    )
    parser.add_argument(
        "--model", default="",
        help="the requested MODEL id instead of a tier (its rung is read off the tiers.yaml ladder)",
    )
    parser.add_argument(
        "--class", dest="spawn_class", default="",
        help="the FinOps task class (default $AO_TASK_CLASS, then code-author)",
    )
    parser.add_argument(
        "--actor", default="",
        help="the identity acting (default $AO_ACTOR, then the runtime); resolved by the identity lane",
    )
    parser.add_argument("--verb", action="append", default=[], help="a control verb this spawn will call (repeatable)")
    parser.add_argument("--skill", action="append", default=[], help="a skill this spawn will use (repeatable)")
    parser.add_argument("--secret", action="append", default=[], help="a secret path this spawn will read (repeatable)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="spawn", description=__doc__)
    parser.add_argument("--standing", default="", help="the standing mandate body (default: the inline one)")
    sub = parser.add_subparsers(dest="command", required=True)

    opened = sub.add_parser("open", help="the local spawn path: claim, mint, assemble, print")
    _common(opened)
    opened.add_argument("--path", default="local", choices=["local", "fleet"])
    opened.add_argument("--body", default="")
    opened.add_argument("--body-file", default="")
    opened.add_argument("--from-github", action="store_true")
    opened.add_argument("--repo", default="kushin77/agent-orchestrator")
    opened.add_argument("--files", action="append", default=[])
    opened.add_argument("--json", action="store_true", help="print the document instead of the block")
    opened.add_argument("--no-claim", action="store_true", help="the caller already holds the claim")
    opened.add_argument("--no-mint", action="store_true", help="do not provision a lane")
    opened.add_argument("--fetch", action="store_true")
    opened.add_argument("--base", default="origin/master")
    opened.add_argument("--worktree-root", default="")
    opened.set_defaults(func=cmd_open)

    built = sub.add_parser("build", help="produce the envelope and print it (read-only)")
    _common(built)
    built.add_argument("--path", default="fleet", choices=["local", "fleet"])
    built.add_argument("--body", default="")
    built.add_argument("--body-file", default="")
    built.add_argument("--from-github", action="store_true")
    built.add_argument("--repo", default="kushin77/agent-orchestrator")
    built.add_argument("--files", action="append", default=[])
    built.add_argument("--render", dest="render_block", action="store_true")
    built.set_defaults(func=cmd_build)

    checked = sub.add_parser("check", help="validate a document (exit 78 when refused)")
    checked.add_argument("--file", default="")
    checked.add_argument("--without", action="append", default=[])
    checked.set_defaults(func=cmd_check)

    rendered = sub.add_parser("render", help="render an admitted document")
    rendered.add_argument("--file", default="")
    rendered.add_argument("--without", action="append", default=[])
    rendered.set_defaults(func=cmd_render)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
