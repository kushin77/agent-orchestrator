#!/usr/bin/env python3
"""Offline CLI for the gateway/health model-health layer (issue #18).

Runs directly (no network, no server, deterministic fake clock):

    python3 gateway/health/cli.py config                # effective config + chains
    python3 gateway/health/cli.py status deepseek deepseek-chat
    python3 gateway/health/cli.py demo                  # end-to-end walk-through

The package is importable as ``health`` when ``gateway/`` is on sys.path; this
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

from health import (  # noqa: E402  (import after sys.path bootstrap)
    DEFAULT_LOCAL_RUNG,
    HealthMonitor,
    ListHealthSink,
    load_config,
    load_default_chains,
)


class FakeClock:
    """Deterministic monotonic clock for the offline demo."""

    def __init__(self, start: float = 1_000_000.0) -> None:
        self._now = start

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


def _demo_config():
    """A tuned-down config so the demo reaches every state offline."""
    from health import HealthConfig

    return HealthConfig(
        window_size=12,
        min_samples=3,
        degrade_failure_pct=20.0,
        trip_failure_pct=50.0,
        recover_failure_pct=10.0,
        cool_off_seconds=30.0,
        probe_success_threshold=2,
        slow_threshold_ms=2000.0,
    )


def _print_step(label: str, status: dict) -> None:
    print(f"[{label}] state={status['state']} verdict={status['verdict']} "
          f"is_healthy={status['is_healthy']} "
          f"fail%={status['window']['failure_rate_pct']} "
          f"samples={status['window']['total']}")


def cmd_config(args: argparse.Namespace) -> int:
    config = load_config()
    print(json.dumps({"monitor": config.to_dict()}, indent=2, sort_keys=True))
    registry = load_default_chains()
    table = {
        key: [r.to_dict() for r in chain.rungs]
        for key, chain in sorted(registry.chains().items())
    }
    print(json.dumps({"chains": table, "localLastResort": DEFAULT_LOCAL_RUNG},
                     indent=2, sort_keys=True))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    monitor = HealthMonitor(config=load_config())
    print(json.dumps(monitor.health_status(args.provider, args.model),
                     indent=2, sort_keys=True))
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    """Offline walk-through: healthy -> degrade -> quarantine -> fallback ->
    cool-off -> recovery probe -> recovered, with events on audit/alert."""
    clock = FakeClock()
    audit = ListHealthSink()
    alerts = ListHealthSink()
    monitor = HealthMonitor(
        config=_demo_config(), now=clock, audit_sinks=[audit], alert_sinks=[alerts]
    )
    registry = load_default_chains()

    primary = ("deepseek", "deepseek-chat")
    print("== model-health demo (offline, fake clock) ==")
    _print_step("boot (healthy)", monitor.health_status(*primary))
    print("audit events:", audit.kinds())

    # A few successes establish a healthy baseline.
    for _ in range(5):
        monitor.record_success(*primary, latency_ms=400.0)
    _print_step("baseline", monitor.health_status(*primary))

    # Failures past the degrade threshold -> degraded (route away).
    for _ in range(4):
        monitor.record_failure(*primary, latency_ms=30000.0, error_class="timeout")
    _print_step("degraded", monitor.health_status(*primary))
    print("routed to:", registry.resolve(*primary, monitor.is_healthy))

    # Failures past the trip threshold -> quarantined (hard stop).
    for _ in range(3):
        monitor.record_failure(*primary, latency_ms=30000.0, error_class="timeout")
    _print_step("quarantined", monitor.health_status(*primary))
    print("routed to:", registry.resolve(*primary, monitor.is_healthy))
    print("audit events:", audit.kinds())
    print("alert events (severity>=warning):", alerts.kinds())

    # While quarantined, a stray production success cannot clear it.
    monitor.record_success(*primary, latency_ms=400.0)
    _print_step("stray success (still quarantined)", monitor.health_status(*primary))

    # Cool-off elapses -> probe authorized.
    clock.advance(31.0)
    print("may_probe:", monitor.may_probe(*primary))
    _print_step("probing", monitor.health_status(*primary))

    # Probe failures keep it down; probe successes restore it.
    monitor.record_probe_failure(*primary, error_class="unavailable")
    _print_step("probe failed (quarantined again)", monitor.health_status(*primary))
    clock.advance(31.0)
    monitor.may_probe(*primary)
    monitor.record_probe_success(*primary)
    monitor.record_probe_success(*primary)
    _print_step("recovered", monitor.health_status(*primary))
    print("routed to:", registry.resolve(*primary, monitor.is_healthy))

    # Ollama as a provider that can be marked unhealthy is the last resort.
    print("== ollama local last resort ==")
    monitor.mark_unhealthy("deepseek", "deepseek-chat", detail="cloud down")
    monitor.mark_unhealthy("anthropic", "claude-haiku-4-5", detail="alternate down")
    monitor.mark_unhealthy("ollama", "llama3.2", detail="ollama daemon down")
    print("cloud + alternate + local all down -> routed to:",
          registry.resolve(*primary, monitor.is_healthy))
    monitor.mark_healthy("ollama", "llama3.2")
    rung = registry.resolve(*primary, monitor.is_healthy)
    print("ollama restored -> routed to:", rung)
    print("audit events:", audit.kinds())
    print("alert events:", alerts.kinds())
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="gateway/health/cli.py",
        description="Offline model-health CLI (issue #18).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("config", help="print effective monitor config + fallback chains")
    p_status = sub.add_parser("status", help="print one model's health status")
    p_status.add_argument("provider")
    p_status.add_argument("model")
    sub.add_parser("demo", help="end-to-end offline walk-through")

    args = parser.parse_args(argv)
    if args.command == "config":
        return cmd_config(args)
    if args.command == "status":
        return cmd_status(args)
    if args.command == "demo":
        return cmd_demo(args)
    parser.error(f"unknown command {args.command!r}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
