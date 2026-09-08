#!/usr/bin/env python3
"""Offline CLI for the gateway/limits cost/capacity control layer (issue #19).

Runs directly (no network, no server):

    python3 gateway/limits/cli.py config            # effective configuration
    python3 gateway/limits/cli.py cache status      # cache stats
    python3 gateway/limits/cli.py budget decide acme coder LOW 500 --mode enforce
    python3 gateway/limits/cli.py rate check acme::coder::LOW
    python3 gateway/limits/cli.py throttle cap summarize
    python3 gateway/limits/cli.py throttle enforce boolean --text '...'
    python3 gateway/limits/cli.py backpressure handle acme coder LOW
    python3 gateway/limits/cli.py demo              # end-to-end walk-through

The package is importable as ``limits`` when ``gateway/`` is on sys.path; this
script arranges that itself so it can run from any cwd.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_GATEWAY_DIR = os.path.dirname(_HERE)  # gateway/
if _GATEWAY_DIR not in sys.path:
    sys.path.insert(0, _GATEWAY_DIR)

from limits import model  # noqa: E402
from limits.backpressure import BackpressureController  # noqa: E402
from limits.budget import BudgetController, BudgetMode, BudgetPolicy  # noqa: E402
from limits.config import LimitsConfig, load_config  # noqa: E402
from limits.fingerprint import cache_key, prompt_fingerprint  # noqa: E402
from limits.limiter import build_engine  # noqa: E402
from limits.ratelimit import RateLimiter, RateLimitPolicy, rate_scope  # noqa: E402
from limits.throttle import OutputThrottle  # noqa: E402


def _engine(cfg_path: str | None):
    cfg = load_config(cfg_path) if cfg_path else load_config()
    return build_engine(cfg), cfg


def cmd_config(args: argparse.Namespace) -> int:
    cfg: LimitsConfig = load_config(args.config)
    print("gateway/limits effective configuration")
    print(f"  cache:        enabled={cfg.cache.enabled} store={cfg.cache.store} "
          f"ttl={cfg.cache.default_ttl_seconds}s max_entries={cfg.cache.max_entries}")
    print(f"  budget:       default_cap={cfg.budget.default_cap_tokens} "
          f"window={cfg.budget.default_window_seconds}s mode={cfg.budget.default_mode} "
          f"scope_overrides={len(cfg.budget.scope_overrides)} "
          f"tenant_overrides={len(cfg.budget.tenant_overrides)}")
    print(f"  rate:         limit={cfg.rate.default_limit}/"
          f"{cfg.rate.default_window_seconds}s burst={cfg.rate.default_burst} "
          f"scope_overrides={len(cfg.rate.scope_overrides)}")
    print(f"  throttle:     default_cap={cfg.throttle.default_cap} mode={cfg.throttle.mode} "
          f"task_types_capped={len(cfg.throttle.caps)}")
    print(f"  backpressure: strategy={cfg.backpressure.strategy} "
          f"queue_capacity={cfg.backpressure.queue_capacity}")
    return 0


def cmd_cache(args: argparse.Namespace) -> int:
    engine, _ = _engine(args.config)
    if engine.cache is None:
        print("cache: disabled (cache.enabled=false)")
        return 0
    stats = engine.cache.stats()
    print(f"cache stats: hits={stats['hits']} misses={stats['misses']} "
          f"sets={stats['sets']} evictions={stats['evictions']} "
          f"entries={stats['entries']} hit_rate={stats['hit_rate_pct']}%")
    return 0


def cmd_fingerprint(args: argparse.Namespace) -> int:
    print(f"prompt_fingerprint: {prompt_fingerprint(args.prompt)}")
    print(f"cache_key(tenant={args.tenant}, tier={args.tier}, task_type={args.task_type}): "
          f"{cache_key(args.tenant, args.tier, args.prompt, task_type=args.task_type)}")
    return 0


def cmd_budget(args: argparse.Namespace) -> int:
    if args.action == "decide":
        mode = getattr(args, "mode", None)
        policy = BudgetPolicy(
            cap_tokens=args.cap,
            window_seconds=args.window,
            mode=mode or BudgetMode.OBSERVE,
        )
        ctl = BudgetController(default_policy=policy)
        decision = ctl.decide(args.tenant, args.agent, args.tier, args.tokens)
        print(
            f"scope={decision.scope} mode={decision.mode} used={decision.used_in_window} "
            f"requested={decision.requested_tokens} cap={decision.cap_tokens} "
            f"allowed={decision.allowed} would_block={decision.would_block} "
            f"reason={decision.reason}"
        )
    else:  # record
        mode = getattr(args, "mode", None) or BudgetMode.ENFORCE
        policy = BudgetPolicy(cap_tokens=args.cap, window_seconds=args.window, mode=mode)
        ctl = BudgetController(default_policy=policy)
        ctl.record(args.tenant, args.agent, args.tier, args.tokens)
        decision = ctl.decide(args.tenant, args.agent, args.tier, 0)
        print(f"recorded {args.tokens} tokens for {decision.scope}; window used now "
              f"{decision.used_in_window}/{decision.cap_tokens}")
    return 0


def cmd_rate(args: argparse.Namespace) -> int:
    if args.scope:
        scope = args.scope
    elif args.tenant and args.agent and args.tier:
        scope = rate_scope(args.tenant, args.agent, args.tier)
    else:
        print("rate: provide a positional scope (e.g. acme::coder::LOW) or "
              "--tenant/--agent/--tier", file=sys.stderr)
        return 2
    policy = RateLimitPolicy(limit=args.limit, window_seconds=args.window, burst=args.burst)
    limiter = RateLimiter(default_policy=policy)
    if args.action == "status":
        print(json.dumps(limiter.status(scope), sort_keys=True))
    else:  # check
        decision = limiter.check(scope)
        print(
            f"scope={decision.scope} allowed={decision.allowed} "
            f"remaining={decision.tokens_remaining:.3f} wait={decision.wait_seconds:.3f}s "
            f"reason={decision.reason}"
        )
    return 0


def cmd_throttle(args: argparse.Namespace) -> int:
    throttle = OutputThrottle(mode=args.mode)
    if args.action == "cap":
        print(f"task_type={args.task_type or '(default)'} max_tokens={throttle.max_tokens(args.task_type)}")
        return 0
    text = sys.stdin.read() if args.text == "-" else args.text
    verdict = throttle.enforce(args.task_type, text)
    print(
        f"task_type={args.task_type or '(default)'} cap={verdict.cap} "
        f"output_tokens={verdict.output_tokens} action={verdict.action} "
        f"allowed={verdict.allowed} trimmed={verdict.trimmed} refused={verdict.refused}"
    )
    if verdict.output is not None:
        print(f"effective_output_len={len(verdict.output)}")
    return 0


def cmd_backpressure(args: argparse.Namespace) -> int:
    ctl = BackpressureController(strategy=args.strategy)
    decision = ctl.handle(
        tenant=args.tenant,
        agent=args.agent,
        model_tier=args.tier,
        task_type=args.task_type,
        reason=args.reason,
    )
    print(
        f"action={decision.action} queued={decision.queued} degraded={decision.degraded} "
        f"reason={decision.reason} job_id={decision.job_id} message={decision.message}"
    )
    print(f"succeeded={decision.succeeded}  (a backpressure decision NEVER succeeds)")
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    """End-to-end offline walk-through used as verification evidence."""
    print("gateway/limits offline demo (issue #19)")
    print("=" * 60)
    engine, _cfg = _engine(args.config)

    prompt = "Explain how a token budget prevents runaway spend in two sentences."

    def provider(_req: model.ModelCallRequest):
        # A stand-in provider; returns (text, usage) like a real adapter would.
        return (
            "A token budget caps the tokens a tenant/agent may spend in a "
            "rolling window; an enforce-mode budget blocks further calls once "
            "the cap is reached, and the gateway applies backpressure instead "
            "of silently succeeding.",
            {"input_tokens": 14, "output_tokens": 32},
        )

    def unreachable_provider(_req: model.ModelCallRequest):
        raise AssertionError("provider must not be called on a cache hit")

    # 1. First call -> provider, output throttled to the task cap.
    req = model.ModelCallRequest(
        tenant="acme", agent="coder", model_tier="LOW",
        task_type="summarize", prompt=prompt,
    )
    decision, result = engine.execute(req, provider)
    print(f"[1] first call:      kind={decision.kind} served={decision.served()} "
          f"refused={result.refused} trimmed={result.trimmed}")
    print(f"    metering:        outcome={result.metering.outcome} "
          f"input={result.metering.input_tokens} output={result.metering.output_tokens} "
          f"zero_cost={result.metering.zero_cost}")

    # 2. Identical call -> cache hit (zero-cost, accounted); provider skipped.
    decision2, _ = engine.execute(
        model.ModelCallRequest(
            tenant="acme", agent="coder", model_tier="LOW",
            task_type="summarize", prompt=prompt,
        ),
        unreachable_provider,
    )
    m2 = decision2.metering
    print(f"[2] identical call:  kind={decision2.kind} outcome={m2.outcome} "
          f"cached={m2.cached} zero_cost={m2.zero_cost} "
          f"response={decision2.response!r}")

    # 3. Observe vs enforce budget: observe logs and never blocks.
    observe_budget = BudgetController(
        default_policy=BudgetPolicy(cap_tokens=10, mode=BudgetMode.OBSERVE)
    )
    obs = observe_budget.decide("acme", "coder", "LOW", 500)
    enforce_budget = BudgetController(
        default_policy=BudgetPolicy(cap_tokens=10, mode=BudgetMode.ENFORCE)
    )
    enf = enforce_budget.decide("acme", "coder", "LOW", 500)
    print(f"[3] budget observe:  allowed={obs.allowed} would_block={obs.would_block} "
          f"(logs, never blocks)")
    print(f"    budget enforce:  allowed={enf.allowed} reason={enf.reason} "
          f"(blocks over-cap calls)")

    # 4. Rate limiter burst window.
    rl = RateLimiter(default_policy=RateLimitPolicy(limit=60, window_seconds=60, burst=3))
    burst = [rl.check("acme::coder::LOW").allowed for _ in range(4)]
    print(f"[4] rate burst:      burst=3 then deny -> {burst}")

    # 5. Output throttle.
    th = OutputThrottle()
    v = th.enforce("boolean", "yes " * 400)  # ~1600 chars /4 = 400 > cap 100
    print(f"[5] throttle:        cap(boolean)={th.max_tokens('boolean')} "
          f"estimated={v.output_tokens} action={v.action} trimmed={v.trimmed}")

    # 6. NEGATIVE: budget exhausted in enforce mode -> explicit decision.
    exhausted_engine = build_engine()
    exhausted_engine.budget = enforce_budget  # cap 10 -> any real call is over
    big = model.ModelCallRequest(
        tenant="broker", agent="worker", model_tier="HIGH",
        task_type="architecture", prompt="spend 9001 tokens please",
    )
    neg = exhausted_engine.guard(big, requested_tokens=9001)
    bp = neg.backpressure
    print(f"[6] negative:        kind={neg.kind} served={neg.served()} "
          f"outcome={neg.metering.outcome} backpressure={bp.action} "
          f"reason={neg.metering.reason} (never a silent success)")
    assert not neg.served()
    assert bp is not None and not bp.succeeded
    print("demo: OK (all assertions passed)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="limits",
        description="gateway/limits cost/capacity control layer (offline CLI).",
    )
    parser.add_argument("--config", default=None, help="path to a limits YAML config override")
    sub = parser.add_subparsers(dest="command", required=True)

    p_cfg = sub.add_parser("config", help="show effective configuration")
    p_cfg.set_defaults(func=cmd_config)

    p_fp = sub.add_parser("fingerprint", help="print prompt fingerprint / cache key")
    p_fp.add_argument("prompt")
    p_fp.add_argument("--tenant", default="acme")
    p_fp.add_argument("--tier", default="LOW")
    p_fp.add_argument("--task-type", dest="task_type", default=None)
    p_fp.set_defaults(func=cmd_fingerprint)

    p_cache = sub.add_parser("cache", help="cache status")
    p_cache.add_argument("action", choices=["status"], default="status", nargs="?")
    p_cache.set_defaults(func=cmd_cache)

    p_budget = sub.add_parser("budget", help="token-budget decide/record")
    p_budget.add_argument("action", choices=["decide", "record"])
    p_budget.add_argument("tenant")
    p_budget.add_argument("agent")
    p_budget.add_argument("tier")
    p_budget.add_argument("tokens", type=int)
    p_budget.add_argument("--cap", type=int, default=1000)
    p_budget.add_argument("--window", type=int, default=86400)
    p_budget.add_argument("--mode", choices=["observe", "enforce"], default=None)
    p_budget.set_defaults(func=cmd_budget)

    p_rate = sub.add_parser("rate", help="rate-limit check/status")
    p_rate.add_argument("action", nargs="?", choices=["check", "status"], default="check")
    p_rate.add_argument("scope", nargs="?", default=None,
                        help="scope key, e.g. acme::coder::LOW")
    p_rate.add_argument("--tenant", default=None)
    p_rate.add_argument("--agent", default=None)
    p_rate.add_argument("--tier", default=None)
    p_rate.add_argument("--limit", type=int, default=60)
    p_rate.add_argument("--window", type=float, default=60.0)
    p_rate.add_argument("--burst", type=int, default=3)
    p_rate.set_defaults(func=cmd_rate)

    p_throttle = sub.add_parser("throttle", help="output-throttle cap/enforce")
    p_throttle.add_argument("action", choices=["cap", "enforce"])
    p_throttle.add_argument("task_type", nargs="?", default=None)
    p_throttle.add_argument("--text", default=None,
                            help="text to enforce (or '-' to read stdin)")
    p_throttle.add_argument("--mode", choices=["trim", "refuse"], default="trim")
    p_throttle.set_defaults(func=cmd_throttle)

    p_bp = sub.add_parser("backpressure", help="backpressure handle (never succeeds)")
    p_bp.add_argument("verb", nargs="?", choices=["handle"], default="handle")
    p_bp.add_argument("tenant")
    p_bp.add_argument("agent")
    p_bp.add_argument("tier")
    p_bp.add_argument("--task-type", dest="task_type", default=None)
    p_bp.add_argument("--strategy", choices=["queue", "degrade"], default="degrade")
    p_bp.add_argument("--reason", default="budget_exceeded")
    p_bp.set_defaults(func=cmd_backpressure)

    p_demo = sub.add_parser("demo", help="end-to-end offline walk-through")
    p_demo.set_defaults(func=cmd_demo)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
