"""No GET route reaches outside fin: a rate (or anything else) is fetched only
when the operator clicks, through a POST. Every figure here is invented."""

import socket
import urllib.request

import pytest

import app as fin_app


@pytest.fixture
def no_network(monkeypatch):
    reached = []

    def refuse(*args, **kwargs):
        reached.append(args[0] if args else kwargs)
        raise OSError("no network in this test")

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    return reached


def get_routes() -> list[str]:
    """Every GET route, with 1 for each part of its path."""
    urls = []
    for rule in fin_app.app.url_map.iter_rules():
        if "GET" not in rule.methods or rule.endpoint == "static":
            continue
        urls.append(rule.build({name: 1 for name in rule.arguments}, append_unknown=False)[1]
                    if rule.arguments else rule.rule)
    return sorted(set(urls))


def test_no_get_route_fetches(client, no_network, monkeypatch):
    monkeypatch.setitem(fin_app._fx_cache, "fetched_at", None)
    urls = get_routes()
    assert "/api/subscriptions" in urls and "/api/fx-rate" in urls

    for url in urls:
        client.get(url)

    assert no_network == []


def test_the_rate_is_the_held_one_until_a_click_fetches(client, monkeypatch):
    monkeypatch.setitem(fin_app._fx_cache, "rate", 1.35)
    monkeypatch.setitem(fin_app._fx_cache, "fetched_at", None)
    asked = []

    class Reply:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b'{"rates": {"SGD": 1.29}}'

    def answer(url, timeout):
        asked.append(url)
        return Reply()

    monkeypatch.setattr(urllib.request, "urlopen", answer)

    assert client.get("/api/fx-rate").get_json() == {"usd_sgd": 1.35, "fetched": False}
    assert client.get("/api/subscriptions").status_code == 200
    assert asked == []

    assert client.post("/api/fx-rate").get_json() == {"usd_sgd": 1.29, "fetched": True}
    assert len(asked) == 1
    assert client.get("/api/fx-rate").get_json()["usd_sgd"] == 1.29
