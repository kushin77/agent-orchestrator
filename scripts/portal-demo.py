#!/usr/bin/env python3
"""portal-demo.py — `make portal-demo` (issue #1771): start the real console
server with a demo flag-set forced on, so the merged workbook-11 portal work
is actually viewable, never touching infra/feature-flags/registry.yaml
defaults or any terraform file (GR-5 production posture unchanged).

Forces ON, purely in this process's env (local runtime override only):
  * task_board, org_chart, skill_studio  — via AO_PORTAL_DEMO=1
    (portal/server/config_flags.py), read from the committed
    portal/config/feature-flags.yaml only when that override is unset.
  * fleet_projection, operator_terminal  — via AO_SURFACE_REGISTRY pointed at
    an on-disk COPY of infra/feature-flags/registry.yaml with those two
    surfaces' `default` set to on; the committed file is never written to.

Session auth is minted offline by the existing scripts/portal-dev-session.py
(issue #732) — this script does not reimplement that.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PORT = 8799
DEMO_SURFACES = ("task_board", "org_chart", "skill_studio", "fleet_projection", "operator_terminal")

#: Best-known on-disk root each demo surface renders from — reported in the
#: banner so a missing root (e.g. `.fleet/`, absent on this box) is named
#: rather than silently served as fake-looking "empty".
SURFACE_ROOTS = {
    "task_board": REPO_ROOT / "engine" / "core" / "tickets",
    "org_chart": REPO_ROOT / "registry" / "personas" / "org-chart.yaml",
    "skill_studio": REPO_ROOT / "registry" / "packs" / "pack-catalog.yaml",
    "fleet_projection": REPO_ROOT / ".board" / "snapshot.json",
    "operator_terminal": REPO_ROOT / ".board" / "snapshot.json",
}


def promote_registry(runtime: Path) -> Path:
    """A demo copy of the registry with fleet_projection/operator_terminal on.

    Same technique as scripts/portal-dev-session.py's write_promoted_registry,
    inlined here because that helper promotes exactly one surface and its
    contract (issue #732) is pinned by tests; this demo needs two.
    """
    import yaml

    source = REPO_ROOT / "infra" / "feature-flags" / "registry.yaml"
    document = yaml.safe_load(source.read_text(encoding="utf-8"))
    surfaces = document["surfaces"]
    for name in ("fleet_projection", "operator_terminal"):
        surfaces[name] = dict(surfaces[name])
        surfaces[name]["default"] = "on"
    target = runtime / "registry-demo.yaml"
    runtime.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return target


def wait_for(url: str, timeout: float = 15.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1)
            return
        except urllib.error.HTTPError:
            return  # server answered (redirect/401/etc. — it's up)
        except (urllib.error.URLError, ConnectionError):
            time.sleep(0.3)
    raise TimeoutError(f"server never answered {url}")


def main() -> int:
    session = json.loads(
        subprocess.check_output(
            [sys.executable, str(REPO_ROOT / "scripts" / "portal-dev-session.py"), "--json", "--port", str(PORT)],
            cwd=REPO_ROOT,
        )
    )
    runtime = Path(session["runtime_dir"])
    registry = promote_registry(runtime)

    env = dict(os.environ)
    env["PORTAL_AUTH_GATE_JWKS_FILE"] = session["jwks_file"]
    env["ROOT_ADMIN_EMAILS"] = session["email"]
    env["AO_SURFACE_REGISTRY"] = str(registry)
    env["AO_SURFACE_STATE"] = session["state_file"]
    env["AO_PORTAL_DEMO"] = "1"

    proc = subprocess.Popen(
        [sys.executable, "-m", "portal.server.main", "--host", "127.0.0.1", "--port", str(PORT)],
        cwd=REPO_ROOT,
        env=env,
    )
    try:
        wait_for(f"http://127.0.0.1:{PORT}/")

        print("== portal-demo (issue #1771) — DEV STOPGAP, NOT the production surface ==")
        print(f"url           http://127.0.0.1:{PORT}/")
        print(f"session       {session['email']} (cookie {session['cookie_name']}, see below)")
        print("forced on     " + ", ".join(DEMO_SURFACES) + "  (AO_PORTAL_DEMO=1 + demo registry copy)")
        print("")
        for name in DEMO_SURFACES:
            root = SURFACE_ROOTS[name]
            status = "real data" if root.exists() else f"NOT REPORTING (missing root: {root})"
            print(f"  {name:<18} {status}")
        print("")
        cookie = f"{session['cookie_name']}={session['cookie_value']}"
        print(f"curl -i -H 'Cookie: {cookie}' http://127.0.0.1:{PORT}/views/fleet.html")
        print(f"curl -sS -H 'Cookie: {cookie}' http://127.0.0.1:{PORT}/api/fleet/snapshot")
        print("")
        print("Ctrl-C to stop.")

        proc.wait()
        return proc.returncode or 0
    except KeyboardInterrupt:
        print("\nstopping...")
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        return 0
    finally:
        if proc.poll() is None:
            proc.terminate()


if __name__ == "__main__":
    raise SystemExit(main())
