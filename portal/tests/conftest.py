"""Shared pytest fixtures for the portal console suite (issue #39).

Bootstraps the repo root so the portal server can import the merged
``identity/sso`` lane (issue #35) and exercises the console through an
in-process API client over ``ConsoleApplication.handle`` (no sockets).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from portal.server.app import build_app  # noqa: E402


class ApiClient:
    """In-process console client with a cookie jar."""

    def __init__(self, app) -> None:
        self.app = app
        self.cookies: dict[str, str] = {}

    def _request(self, method: str, path: str, query=None, body=None):
        response = self.app.handle(
            method, path, query=query or {}, body=body or {}, cookies=self.cookies
        )
        for name, value in response.headers:
            if name.lower() != "set-cookie":
                continue
            pair, _, rest = value.partition(";")
            cookie_name, _, cookie_value = pair.partition("=")
            if "Max-Age=0" in rest or not cookie_value:
                self.cookies.pop(cookie_name.strip(), None)
            else:
                self.cookies[cookie_name.strip()] = cookie_value
        raw = response.as_bytes()
        payload = None
        if response.is_json:
            try:
                payload = json.loads(raw.decode("utf-8")) if raw else None
            except ValueError:
                payload = raw.decode("utf-8", "replace")
        else:
            payload = raw
        return response.status, payload

    def get(self, path: str, query=None):
        return self._request("GET", path, query=query)

    def post(self, path: str, body=None):
        return self._request("POST", path, body=body)

    def login(self, email: str, tenant_id: str):
        status, payload = self.get("/api/console/relay-state")
        assert status == 200
        state = payload["data"]["state"]
        return self.post(
            "/api/console/login",
            {"email": email, "tenantId": tenant_id, "state": state},
        )


@pytest.fixture
def app():
    return build_app()


@pytest.fixture
def client(app):
    return ApiClient(app)


@pytest.fixture
def super_client(app):
    api = ApiClient(app)
    status, payload = api.login("root@platform.example.com", "acme")
    assert status == 200, payload
    return api


def login_as(app, email: str, tenant_id: str) -> ApiClient:
    api = ApiClient(app)
    status, payload = api.login(email, tenant_id)
    assert status == 200, payload
    return api
