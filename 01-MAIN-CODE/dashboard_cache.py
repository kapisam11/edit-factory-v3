"""Small hybrid JSON cache for dashboard metadata.

Memory is always available. Redis becomes the shared cache when AIVF_REDIS_URL
is configured and the optional redis client is installed. A cache failure never
blocks the dashboard; the implementation falls back to the local TTL cache.
"""
from __future__ import annotations

import json
import logging
import threading
import os
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)


class HybridCache:
    """Thread-safe local TTL cache with optional Redis backing."""

    def __init__(self, redis_url: Optional[str] = None, *, max_entries: int | None = None):
        self._local: dict[str, tuple[float, Any]] = {}
        self._lock = threading.RLock()
        configured = os.environ.get("AIVF_CACHE_MAX_ENTRIES", "512") if max_entries is None else str(max_entries)
        try:
            self._max_entries = max(16, min(int(configured), 10000))
        except ValueError:
            self._max_entries = 512
        self._redis = None
        self._redis_url = (redis_url or "").strip()
        if self._redis_url:
            self._connect_redis()

    def _connect_redis(self) -> None:
        try:
            import redis

            client = redis.Redis.from_url(
                self._redis_url,
                decode_responses=True,
                socket_connect_timeout=1,
                socket_timeout=1,
            )
            client.ping()
            self._redis = client
            logger.info("Dashboard cache using Redis backing store")
        except Exception as exc:  # optional dependency or unavailable service
            self._redis = None
            logger.warning("Redis cache unavailable; using local cache: %s", exc)

    @staticmethod
    def _remaining_ttl(expires_at: float) -> int:
        return max(1, int(expires_at - time.monotonic()))

    def get_json(self, key: str) -> Any:
        now = time.monotonic()
        with self._lock:
            entry = self._local.get(key)
            if entry:
                expires_at, value = entry
                if expires_at > now:
                    return value
                self._local.pop(key, None)

        if self._redis is not None:
            try:
                payload = self._redis.get(key)
                if payload:
                    return json.loads(payload)
            except Exception as exc:
                logger.debug("Redis cache read failed for %s: %s", key, exc)
        return None

    def set_json(self, key: str, value: Any, ttl_seconds: float) -> None:
        ttl_seconds = max(0.1, float(ttl_seconds))
        expires_at = time.monotonic() + ttl_seconds
        with self._lock:
            self._local[key] = (expires_at, value)
            if len(self._local) > self._max_entries:
                oldest_key = min(
                    self._local,
                    key=lambda cache_key: self._local[cache_key][0],
                )
                if oldest_key != key:
                    self._local.pop(oldest_key, None)
        if self._redis is not None:
            try:
                self._redis.setex(key, self._remaining_ttl(expires_at), json.dumps(value))
            except Exception as exc:
                logger.debug("Redis cache write failed for %s: %s", key, exc)

    def delete(self, key: str) -> None:
        with self._lock:
            self._local.pop(key, None)
        if self._redis is not None:
            try:
                self._redis.delete(key)
            except Exception as exc:
                logger.debug("Redis cache delete failed for %s: %s", key, exc)

    def clear(self) -> None:
        with self._lock:
            self._local.clear()
        if self._redis is not None:
            try:
                self._redis.flushdb()
            except Exception as exc:
                logger.debug("Redis cache clear failed: %s", exc)

    def prune(self) -> int:
        """Remove expired local entries and return the number removed."""
        now = time.monotonic()
        removed = 0
        with self._lock:
            for key, (expires_at, _) in list(self._local.items()):
                if expires_at <= now:
                    self._local.pop(key, None)
                    removed += 1
        return removed
