"""Small HTTP client with timeouts, bounded backoff, and safe error messages."""

from __future__ import annotations

import json
import logging
import random
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

LOGGER = logging.getLogger(__name__)
RETRYABLE = {408, 429, 500, 502, 503, 504, 529}


class HttpError(RuntimeError):
    """An external endpoint failed; request headers and bodies are never exposed."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class HttpClient:
    def __init__(self, *, timeout: float = 45, retries: int = 3,
                 user_agent: str = "WeeklyLiteratureBrief/1.0", max_bytes: int = 25_000_000):
        self.timeout = timeout
        self.retries = retries
        self.user_agent = user_agent
        self.max_bytes = max_bytes

    def _delay(self, retry: int, retry_after: str | None) -> float:
        try:
            delay = float(retry_after) if retry_after else 0
        except ValueError:
            try:
                date = parsedate_to_datetime(retry_after)
                delay = (date - datetime.now(timezone.utc)).total_seconds()
            except (TypeError, ValueError, OverflowError):
                delay = 0
        return min(60.0, max(delay, 2 ** retry + random.random()))

    def _request(self, method: str, url: str, *, headers=None, payload=None, params=None) -> str:
        parts = urlsplit(url)
        if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
            raise HttpError("External API requests must use an HTTPS URL without credentials.")
        if params:
            url += ("&" if "?" in url else "?") + urlencode(params, doseq=True)
        request_headers = {"User-Agent": self.user_agent, "Accept": "application/json"}
        request_headers.update(headers or {})
        body = None
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        request = Request(url, data=body, headers=request_headers, method=method)
        endpoint = parts.hostname  # Do not include queries, headers, or response bodies in logs.
        for attempt in range(self.retries + 1):
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    data = response.read(self.max_bytes + 1)
                    if len(data) > self.max_bytes:
                        raise HttpError(f"Response from {endpoint} exceeded the size limit.")
                    return data.decode("utf-8")
            except HTTPError as exc:
                status = exc.code
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                exc.close()
                if status not in RETRYABLE or attempt == self.retries:
                    raise HttpError(f"HTTP {status} from {endpoint}.", status) from None
            except (URLError, TimeoutError, OSError):
                retry_after = None
                if attempt == self.retries:
                    raise HttpError(f"Connection to {endpoint} failed after retries.") from None
            delay = self._delay(attempt, retry_after)
            LOGGER.warning("Retrying %s after %.1f seconds (attempt %s).", endpoint, delay, attempt + 1)
            time.sleep(delay)
        raise HttpError(f"Request to {endpoint} failed.")

    def request_json(self, method: str, url: str, *, headers=None, payload=None, params=None) -> dict:
        raw = self._request(method, url, headers=headers, payload=payload, params=params)
        try:
            value = json.loads(raw)
        except (ValueError, UnicodeError):
            raise HttpError(f"Invalid JSON from {urlsplit(url).hostname}.") from None
        if not isinstance(value, dict):
            raise HttpError(f"Expected a JSON object from {urlsplit(url).hostname}.")
        return value

    def get_json(self, url: str, params=None, headers=None) -> dict:
        return self.request_json("GET", url, params=params, headers=headers)

    def get_text(self, url: str, params=None, headers=None) -> str:
        return self._request("GET", url, params=params, headers=headers)
