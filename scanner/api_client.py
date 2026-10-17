"""Small authenticated HTTP client for the local/hosted Worker persistence API."""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import quote, urlencode, urlsplit


class WorkerAPIError(RuntimeError):
    """Safe API failure; messages never include request headers or secret values."""

    def __init__(self, message: str, *, status: int | None = None, retryable: bool = False) -> None:
        super().__init__(message)
        self.status = status
        self.retryable = retryable


@dataclass(slots=True)
class WorkerAPIClient:
    base_url: str
    secret: str | None = None
    timeout: float = 10.0
    ai_timeout: float = 8.0
    retries: int = 2
    opener: Any = urllib.request.urlopen
    sleep: Any = time.sleep

    def __post_init__(self) -> None:
        self.base_url = self.base_url.rstrip("/")
        self.secret = self.secret or os.getenv("SCANNER_API_SECRET")
        parsed_base = urlsplit(self.base_url)
        if parsed_base.scheme not in {"http", "https"} or not parsed_base.netloc:
            raise ValueError("base_url must be an HTTP(S) URL")
        if os.getenv("APP_ENV", "development").lower() == "production" and parsed_base.scheme != "https":
            raise ValueError("Production Worker API URLs must use HTTPS")
        if self.timeout <= 0 or not 1 <= self.ai_timeout <= 15 or not 0 <= self.retries <= 5:
            raise ValueError("timeout must be positive and retries must be between 0 and 5")

    def request(self, method: str, path: str, payload: Mapping[str, Any] | None = None, *,
                retry_limit: int | None = None, timeout: float | None = None) -> Any:
        if not self.secret:
            raise WorkerAPIError("SCANNER_API_SECRET is not configured")
        if not path.startswith("/") or path.startswith("//") or "://" in path:
            raise ValueError("path must start with /")
        body = json.dumps(dict(payload)).encode() if payload is not None else None
        req = urllib.request.Request(self.base_url + path, data=body, method=method.upper(), headers={
            "Authorization": f"Bearer {self.secret}", "Accept": "application/json",
            # Python's default Python-urllib user agent was rejected by the
            # Cloudflare edge before the Worker could handle authenticated calls.
            "User-Agent": "fresher-job-radar/1.0",
            **({"Content-Type": "application/json"} if body is not None else {}),
        })
        retries = self.retries if retry_limit is None else retry_limit
        request_timeout = self.timeout if timeout is None else timeout
        for attempt in range(retries + 1):
            try:
                with self.opener(req, timeout=request_timeout) as response:
                    raw = response.read()
                    status = response.status
                    headers = response.headers
                if status >= 400:
                    raise WorkerAPIError(f"Worker API returned HTTP {status}", status=status, retryable=status >= 500)
                try:
                    return json.loads(raw.decode("utf-8")) if raw else None
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise WorkerAPIError("Worker API returned invalid JSON") from exc
            except urllib.error.HTTPError as exc:
                retry_after = _retry_after(exc.headers.get("Retry-After"))
                retryable = exc.code == 429 or exc.code >= 500
                if retryable and attempt < retries:
                    self.sleep(min(retry_after if retry_after is not None else 0.25 * (2 ** attempt), 10.0))
                    continue
                raise WorkerAPIError(f"Worker API returned HTTP {exc.code}", status=exc.code, retryable=retryable) from None
            except (TimeoutError, OSError, urllib.error.URLError) as exc:
                if attempt < retries:
                    self.sleep(min(0.25 * (2 ** attempt), 5.0)); continue
                raise WorkerAPIError("Worker API network request failed", retryable=True) from None
        raise WorkerAPIError("Worker API request failed", retryable=True)

    def list_channels(self, status: str = "ACTIVE", *, limit: int = 100, before_id: int | None = None) -> list[dict[str, Any]]:
        params = {"status": status, "limit": str(max(1, min(200, limit)))}
        if before_id is not None:
            params["before_id"] = str(max(1, before_id))
        return self.request("GET", f"/api/v1/channels?{urlencode(params)}")

    def list_pending_channels(self) -> list[dict[str, Any]]:
        return self.list_channels("PENDING_VERIFICATION")

    def get_monitoring_state(self) -> dict[str, Any]:
        return self.request("GET", "/api/v1/settings/monitoring")

    def create_channel(self, original_reference: str, **fields: Any) -> dict[str, Any]:
        return self.request("POST", "/api/v1/channels", {"original_reference": original_reference, **fields})

    def update_channel(self, channel_id: int, **fields: Any) -> dict[str, Any]:
        return self.request("PATCH", f"/api/v1/channels/{channel_id}", fields)

    def get_channel(self, channel_id: int) -> dict[str, Any]:
        return self.request("GET", f"/api/v1/channels/{channel_id}")

    def save_message(self, **record: Any) -> dict[str, Any]:
        return self.request("POST", "/api/v1/messages", record)

    def get_message(self, channel_id: int, message_id: int) -> dict[str, Any]:
        return self.request("GET", f"/api/v1/messages/{channel_id}/{message_id}")

    def save_job(self, **record: Any) -> dict[str, Any]:
        return self.request("POST", "/api/v1/jobs", record)

    def classify_ambiguous(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Ask the authenticated Worker to classify one bounded ambiguous post.

        Inference calls are never automatically retried: the Worker caches successful
        results and applies its own free-tier request cap.
        """
        return self.request("POST", "/api/v1/ai/classify", payload, retry_limit=0, timeout=self.ai_timeout)

    def ai_assessment_summary(self) -> dict[str, Any]:
        return self.request("GET", "/api/v1/ai/summary")

    def list_ai_assessments(self, *, limit: int = 20) -> list[dict[str, Any]]:
        return self.request("GET", f"/api/v1/ai/assessments?{urlencode({'limit': max(1, min(100, limit))})}")

    def get_job(self, job_id: str) -> dict[str, Any]:
        return self.request("GET", f"/api/v1/jobs/{quote(job_id, safe='')}")

    def find_job(self, *, company: str | None, role: str | None, normalized_application_url: str | None, requisition_id: str | None, content_hash: str | None) -> dict[str, Any] | None:
        params = {k: v for k, v in {"company": company, "role": role, "url": normalized_application_url, "requisition_id": requisition_id, "content_hash": content_hash}.items() if v}
        if not params:
            return None
        try:
            return self.request("GET", f"/api/v1/jobs/lookup?{urlencode(params)}")
        except WorkerAPIError as exc:
            if exc.status == 404:
                return None
            raise

    def get_checkpoint(self, channel_id: int) -> int:
        return int(self.request("GET", f"/api/v1/channels/{channel_id}/checkpoint")["last_message_id"])

    def advance_checkpoint(self, channel_id: int, message_id: int, processed_message_ids: list[int]) -> dict[str, Any]:
        return self.request("POST", f"/api/v1/channels/{channel_id}/checkpoint", {"last_message_id": message_id, "processed_message_ids": processed_message_ids})

    def create_notification(self, **intent: Any) -> dict[str, Any]:
        return self.request("POST", "/api/v1/notifications", intent)

    def pending_notifications(self) -> list[dict[str, Any]]:
        return self.request("GET", "/api/v1/notifications/pending")

    def claim_notification(self, notification_id: str, owner: str) -> dict[str, Any]:
        return self.request("POST", f"/api/v1/notifications/{notification_id}/claim", {"owner": owner})

    def mark_notification_sent(self, notification_id: str) -> dict[str, Any]:
        return self.request("POST", f"/api/v1/notifications/{notification_id}/sent", {})

    def mark_notification_failed(self, notification_id: str, error: str, retry_at: str | None = None, *, permanent: bool = False) -> dict[str, Any]:
        return self.request("POST", f"/api/v1/notifications/{notification_id}/failed", {"error": error[:1000], "retry_at": retry_at, "permanent": permanent})

    def record_scan_run(self, run_id: str, **metrics: Any) -> dict[str, Any]:
        return self.request("POST", "/api/v1/scan-runs", {"id": run_id, **metrics})

    def list_scan_runs(self) -> list[dict[str, Any]]:
        return self.request("GET", "/api/v1/scan-runs")

    def acquire_lease(self, name: str, owner: str, ttl_seconds: int = 300) -> bool:
        result = self.request("POST", "/api/v1/leases/acquire", {"name": name, "owner": owner, "ttl_seconds": ttl_seconds})
        return bool(result["acquired"])

    def release_lease(self, name: str, owner: str) -> bool:
        result = self.request("POST", "/api/v1/leases/release", {"name": name, "owner": owner})
        return bool(result["released"])


def _retry_after(value: str | None) -> float | None:
    try:
        return max(0.0, float(value)) if value else None
    except ValueError:
        return None
