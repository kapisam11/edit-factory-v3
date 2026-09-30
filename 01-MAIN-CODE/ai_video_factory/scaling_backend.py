"""Queue backends for local SQLite and optional Redis horizontal scaling."""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class ClaimedJob:
    job_id: str
    payload: dict[str, Any]
    worker_token: str


class QueueBackend(Protocol):
    def enqueue(self, job_id: str, payload: dict[str, Any]) -> None: ...
    def claim(self) -> ClaimedJob | None: ...
    def heartbeat(self, job_id: str, worker_token: str) -> bool: ...
    def complete(self, job_id: str, worker_token: str) -> bool: ...
    def reclaim_expired(self, limit: int = 100) -> int: ...


class RedisJobBackend:
    """Redis queue with atomic leases and bounded visibility-timeout recovery."""

    _CLAIM_SCRIPT = """
local raw = redis.call('LPOP', KEYS[1])
if not raw then
  return false
end
local payload = cjson.decode(raw)
local job_id = tostring(payload['job_id'])
local job_key = KEYS[2] .. job_id
redis.call('HSET', job_key, 'raw', raw, 'worker_token', ARGV[1], 'claimed', ARGV[2])
redis.call('ZADD', KEYS[3], ARGV[3], job_id)
return raw
"""

    _HEARTBEAT_SCRIPT = """
local key = KEYS[1]
local stored = redis.call('HGET', key, 'worker_token')
if not stored or stored ~= ARGV[1] then
  return 0
end
redis.call('HSET', key, 'claimed', ARGV[2])
redis.call('ZADD', KEYS[2], ARGV[3], ARGV[4])
return 1
"""

    _COMPLETE_SCRIPT = """
local key = KEYS[1]
local stored = redis.call('HGET', key, 'worker_token')
if not stored or stored ~= ARGV[1] then
  return 0
end
redis.call('DEL', key)
redis.call('ZREM', KEYS[2], ARGV[2])
return 1
"""

    _RECLAIM_SCRIPT = """
local job_id = ARGV[1]
local now = tonumber(ARGV[2])
local deadline = redis.call('ZSCORE', KEYS[1], job_id)
if not deadline or tonumber(deadline) > now then
  return 0
end
local key = KEYS[2] .. job_id
local raw = redis.call('HGET', key, 'raw')
if not raw then
  redis.call('ZREM', KEYS[1], job_id)
  redis.call('DEL', key)
  return 0
end
redis.call('RPUSH', KEYS[3], raw)
redis.call('ZREM', KEYS[1], job_id)
redis.call('DEL', key)
return 1
"""

    def __init__(
        self,
        url: str,
        *,
        queue_name: str = "aivf:queue",
        visibility_timeout: float = 3600.0,
    ) -> None:
        try:
            import redis
        except ImportError as exc:
            raise RuntimeError("Redis backend requires the optional redis package") from exc
        if visibility_timeout <= 0:
            raise ValueError("visibility_timeout must be positive")
        self.client = redis.Redis.from_url(url, decode_responses=True)
        self.queue_name = str(queue_name)
        self.visibility_timeout = float(visibility_timeout)
        self.inflight_key = f"{self.queue_name}:inflight"
        self.job_prefix = f"{self.queue_name}:job:"

    def _job_key(self, job_id: str) -> str:
        return f"{self.job_prefix}{job_id}"

    def enqueue(self, job_id: str, payload: dict[str, Any]) -> None:
        body = json.dumps(
            {"job_id": str(job_id), "payload": dict(payload), "created": time.time()},
            separators=(",", ":"),
        )
        self.client.rpush(self.queue_name, body)

    def claim(self) -> ClaimedJob | None:
        # Recover expired leases before taking another job so crashed workers
        # become visible again without losing their original payload.
        self.reclaim_expired(limit=100)
        token = uuid.uuid4().hex
        now = time.time()
        deadline = now + self.visibility_timeout
        raw = self.client.eval(
            self._CLAIM_SCRIPT,
            3,
            self.queue_name,
            self.job_prefix,
            self.inflight_key,
            token,
            str(now),
            str(deadline),
        )
        if not raw:
            return None
        payload = json.loads(str(raw))
        return ClaimedJob(
            str(payload["job_id"]),
            dict(payload["payload"]),
            token,
        )

    def heartbeat(self, job_id: str, worker_token: str) -> bool:
        now = time.time()
        deadline = now + self.visibility_timeout
        result = self.client.eval(
            self._HEARTBEAT_SCRIPT,
            2,
            self._job_key(job_id),
            self.inflight_key,
            str(worker_token),
            str(now),
            str(deadline),
            str(job_id),
        )
        return bool(result)

    def complete(self, job_id: str, worker_token: str) -> bool:
        result = self.client.eval(
            self._COMPLETE_SCRIPT,
            2,
            self._job_key(job_id),
            self.inflight_key,
            str(worker_token),
            str(job_id),
        )
        return bool(result)

    def reclaim_expired(self, limit: int = 100) -> int:
        bounded = max(1, min(int(limit), 1000))
        now = time.time()
        expired = self.client.zrangebyscore(
            self.inflight_key,
            min="-inf",
            max=now,
            start=0,
            num=bounded,
        )
        reclaimed = 0
        for job_id in expired:
            reclaimed += int(
                self.client.eval(
                    self._RECLAIM_SCRIPT,
                    3,
                    self.inflight_key,
                    self.job_prefix,
                    self.queue_name,
                    str(job_id),
                    str(now),
                )
            )
        return reclaimed


__all__ = ["ClaimedJob", "QueueBackend", "RedisJobBackend"]
