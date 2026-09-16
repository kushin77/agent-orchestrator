"""Client tests: the offline transport seam and the declared endpoint shape."""

from __future__ import annotations

from integrations.hermes import client as client_mod

FIXTURE = {
    "responses": [
        {"method": "GET", "path": "/health", "status": 200, "body": {"status": "ok"}},
        {
            "method": "GET",
            "path": "/api/capabilities",
            "status": 200,
            "body": {"capabilities": ["code-author"]},
        },
        {
            "method": "GET",
            "path": "/api/router",
            "status": 200,
            "body": {"route": "hermes"},
        },
        {
            "method": "GET",
            "path": "/api/tiering",
            "status": 200,
            "body": {"tier": "MED"},
        },
    ]
}


def test_fixture_transport_replays_the_declared_endpoints_offline():
    transport = client_mod.FixtureTransport(FIXTURE)
    hermes = client_mod.HermesClient(transport=transport)
    assert hermes.health().body == {"status": "ok"}
    assert hermes.capabilities().body == {"capabilities": ["code-author"]}
    assert hermes.router().body == {"route": "hermes"}
    assert hermes.tiering().body == {"tier": "MED"}

    paths = [req["path"] for req in transport.requests]
    assert paths == ["/health", "/api/capabilities", "/api/router", "/api/tiering"]
    assert all(req["method"] == "GET" for req in transport.requests)


def test_client_is_read_only_and_names_only_the_declared_endpoints():
    transport = client_mod.FixtureTransport(FIXTURE)
    hermes = client_mod.HermesClient(transport=transport)
    hermes.health()
    assert client_mod.HEALTH_PATH == "/health"
    assert client_mod.CAPABILITIES_PATH == "/api/capabilities"
    assert client_mod.ROUTER_PATH == "/api/router"
    assert client_mod.TIERING_PATH == "/api/tiering"
    assert client_mod.SERVICE_PORT == 9501
