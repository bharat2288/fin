"""The one request fin sends outside: the reference-rate fetch.

The source (api.frankfurter.dev) answers 403 to Python's default user agent,
"Python-urllib/3.x", so every fetch failed and the operator could only enter a
rate by hand. The request names fin instead. Nothing here reaches the network:
urlopen is replaced and the request it is handed is read.
"""

import io
import urllib.request

import rates


def test_the_rate_request_names_fin_not_pythons_default_agent(monkeypatch):
    seen = []

    def urlopen(request, timeout=None):
        seen.append((request, timeout))
        return io.BytesIO(b'{"amount":1.0,"base":"INR","date":"2026-10-19","rates":{"SGD":0.01347}}')

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)

    body = rates._http_get("https://api.frankfurter.dev/v1/2026-10-20?base=INR&symbols=SGD")

    assert '"SGD":0.01347' in body
    (request, timeout), = seen
    assert isinstance(request, urllib.request.Request)
    agent = request.get_header("User-agent")
    assert agent and agent.startswith("fin/") and "Python-urllib" not in agent
    assert request.full_url == "https://api.frankfurter.dev/v1/2026-10-20?base=INR&symbols=SGD"
    assert timeout == rates.TIMEOUT_SECONDS
