import json
import urllib.error

import pytest

from scanner.api_client import WorkerAPIClient, WorkerAPIError


class Response:
    status = 200
    headers = {}
    def __enter__(self): return self
    def __exit__(self, *_): pass
    def read(self): return b'{"ok":true}'


def test_client_auth_and_json_request():
    seen = {}
    def opener(request, timeout):
        seen["auth"] = request.get_header("Authorization")
        seen["timeout"] = timeout
        seen["body"] = json.loads(request.data)
        return Response()
    result = WorkerAPIClient("http://127.0.0.1:8787", "local-test-secret", opener=opener).request("POST", "/api/v1/jobs", {"id": "a"})
    assert result == {"ok": True}
    assert seen == {"auth": "Bearer local-test-secret", "timeout": 10.0, "body": {"id": "a"}}


def test_secret_missing_is_explicit_without_leak():
    with pytest.raises(WorkerAPIError, match="not configured"):
        WorkerAPIClient("http://localhost").request("GET", "/api/v1/channels")


def test_rate_limit_retry_honors_bounded_retry_after():
    calls, waits = 0, []
    def opener(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise urllib.error.HTTPError("url", 429, "rate limit", {"Retry-After": "2"}, None)
        return Response()
    result = WorkerAPIClient("https://example.invalid", "secret", opener=opener, sleep=waits.append).request("GET", "/api/v1/channels")
    assert result == {"ok": True}
    assert calls == 2 and waits == [2.0]


def test_nonretryable_http_error_is_safe():
    def opener(*_args, **_kwargs):
        raise urllib.error.HTTPError("url", 401, "unauthorized", {}, None)
    with pytest.raises(WorkerAPIError, match="HTTP 401") as err:
        WorkerAPIClient("https://example.invalid", "do-not-leak", opener=opener).request("GET", "/api/v1/channels")
    assert "do-not-leak" not in str(err.value)


def test_production_client_requires_https(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(ValueError, match="HTTPS"):
        WorkerAPIClient("http://worker.example", "secret")


def test_find_job_encodes_lookup_and_maps_not_found_to_none():
    seen = []
    def opener(request, timeout):
        seen.append(request.full_url)
        raise urllib.error.HTTPError(request.full_url, 404, "not found", {}, None)
    result = WorkerAPIClient("https://worker.example", "secret", opener=opener, retries=0).find_job(
        company="Acme & Sons", role="Software Engineer", normalized_application_url=None,
        requisition_id=None, content_hash="abc123")
    assert result is None
    assert "company=Acme+%26+Sons" in seen[0]
    assert "content_hash=abc123" in seen[0]
