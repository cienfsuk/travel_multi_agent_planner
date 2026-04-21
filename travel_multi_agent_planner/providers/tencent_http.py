from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path

import requests


_CACHE_LOCK = threading.Lock()
_GLOBAL_RESPONSE_CACHE: dict[tuple[str, tuple[tuple[str, str], ...]], dict] = {}
_LAST_REQUEST_AT_BY_KEY: dict[str, float] = {}
_CACHE_DIR = Path(__file__).resolve().parent.parent / "_http_cache"


class TencentRequestHelper:
    def __init__(
        self,
        *,
        api_key: str | None,
        session: requests.Session,
        timeout: int,
        service_name: str,
        min_interval: float = 0.72,
        max_retries: int = 6,
    ) -> None:
        self.api_key = api_key
        self.session = session
        self.timeout = timeout
        self.service_name = service_name
        self.min_interval = min_interval
        self.max_retries = max_retries

    def get(self, url: str, params: dict) -> dict:
        normalized_params = tuple(sorted((str(key), str(value)) for key, value in params.items()))
        cache_key = (url, normalized_params)
        with _CACHE_LOCK:
            cached = _GLOBAL_RESPONSE_CACHE.get(cache_key)
        if cached is not None:
            return cached
        cached = self._load_disk_cache(cache_key)
        if cached is not None:
            with _CACHE_LOCK:
                _GLOBAL_RESPONSE_CACHE[cache_key] = cached
            return cached

        last_error = f"{self.service_name}请求失败"
        for attempt in range(self.max_retries):
            self._throttle()
            response = self.session.get(url, params=params, timeout=self.timeout)
            if response.status_code == 429:
                last_error = f"{self.service_name}请求过于频繁"
                if attempt < self.max_retries - 1:
                    time.sleep(self._retry_delay(attempt))
                    continue
                raise RuntimeError(last_error)
            response.raise_for_status()
            payload = response.json()
            status = int(payload.get("status", -1))
            if status == 0:
                with _CACHE_LOCK:
                    _GLOBAL_RESPONSE_CACHE[cache_key] = payload
                self._save_disk_cache(cache_key, payload)
                return payload

            message = str(payload.get("message") or payload.get("msg") or last_error)
            last_error = message
            if self._is_rate_limited(message) and attempt < self.max_retries - 1:
                time.sleep(self._retry_delay(attempt))
                continue
            raise RuntimeError(message)
        raise RuntimeError(last_error)

    def _throttle(self) -> None:
        if not self.api_key:
            return
        while True:
            wait_seconds = 0.0
            now = time.perf_counter()
            with _CACHE_LOCK:
                last_request_at = _LAST_REQUEST_AT_BY_KEY.get(self.api_key, 0.0)
                wait_seconds = self.min_interval - (now - last_request_at)
                if wait_seconds <= 0:
                    _LAST_REQUEST_AT_BY_KEY[self.api_key] = now
                    return
            time.sleep(wait_seconds)

    def _retry_delay(self, attempt: int) -> float:
        return min(3.6, 0.9 + attempt * 0.7)

    def _is_rate_limited(self, message: str) -> bool:
        return "每秒请求量已达到上限" in message or "请求过于频繁" in message or "QPS" in message

    def _cache_path(self, cache_key: tuple[str, tuple[tuple[str, str], ...]]) -> Path:
        digest = hashlib.sha1(repr(cache_key).encode("utf-8")).hexdigest()
        return _CACHE_DIR / f"{digest}.json"

    def _load_disk_cache(self, cache_key: tuple[str, tuple[tuple[str, str], ...]]) -> dict | None:
        path = self._cache_path(cache_key)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def _save_disk_cache(self, cache_key: tuple[str, tuple[tuple[str, str], ...]], payload: dict) -> None:
        try:
            _CACHE_DIR.mkdir(parents=True, exist_ok=True)
            self._cache_path(cache_key).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        except Exception:
            return
