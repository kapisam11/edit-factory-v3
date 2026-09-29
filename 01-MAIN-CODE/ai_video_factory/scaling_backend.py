"""Queue backends for local SQLite and optional Redis horizontal scaling."""
from __future__ import annotations

import json
import time
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


class RedisJobBackend:
    """Optional Redis backend. Importing this module does not require redis-py."""

    def __init__(self, url: str, *, queue_name: str = "aivf:queue", visibility_timeout: float = 3600.0) -> None:
        try:
            import redis
        except ImportError as exc:
            raise RuntimeError("Redis backend requires the optional redis package") from exc
        if visibility_timeout <= 0:
            raise ValueError("visibility_timeout must be positive")
        self.client = redis.Redis.from_url(url, decode_responses=True)
        self.queue_name = str(queue_name)
        self.visibility_timeout = float(visibility_timeout)

    def enqueue(self, job_id: str, payload: dict[str, Any]) -> None:
        body = json.dumps({"job_id": job_id, "payload": payload, "created": time.time()})
        self.client.rpush(self.queue_name, body)

    def claim(self) -> ClaimedJob | None:
        raw = self.client.lpop(self.queue_name)
        if not raw:
            return None
        payload = json.loads(raw)
        import uuid
        token = uuid.uuid4().hex
        self.client.hset(f"aivf:job:{payload['job_id']}", mapping={
            "payload": json.dumps(payload["payload"]),
            "worker_token": token,
            "claimed": str(time.time()),
        })
        return ClaimedJob(str(payload["job_id"]), dict(payload["payload"]), token)

    def heartbeat(self, job_id: str, worker_token: str) -> bool:
        key = f"aivf:job:{job_id}"
        stored = self.client.hget(key, "worker_token")
        if stored != str(worker_token):
            return False
        self.client.hset(key, "claimed", str(time.time()))
        return True

    def complete(self, job_id: str, worker_token: str) -> bool:
        key = f"aivf:job:{job_id}"
        stored = self.client.hget(key, "worker_token")
        if stored != str(worker_token):
            return False
        self.client.delete(key)
        return True


__all__ = ["ClaimedJob", "QueueBackend", "RedisJobBackend"]
