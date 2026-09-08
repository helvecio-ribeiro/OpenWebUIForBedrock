"""Redis-backed distributed data structures for WebSocket state management."""

from __future__ import annotations

import hashlib
import uuid

from open_webui.env import REDIS_KEY_PREFIX
from open_webui.utils.json_codec import JSONCodec
from open_webui.utils.redis import get_redis_connection



class RedisLock:
    """Distributed lock backed by a Redis SET with NX/EX semantics."""

    _RENEW_SCRIPT = """
    if redis.call('get', KEYS[1]) == ARGV[1] then
        return redis.call('expire', KEYS[1], ARGV[2])
    end
    return 0
    """
    _RELEASE_SCRIPT = """
    if redis.call('get', KEYS[1]) == ARGV[1] then
        return redis.call('del', KEYS[1])
    end
    return 0
    """

    def __init__(
        self,
        redis_url,
        lock_name,
        timeout_secs,
        redis_sentinels=[],
        redis_cluster=False,
    ):
        self.lock_name = lock_name
        self.lock_id = str(uuid.uuid4())
        self.timeout_secs = timeout_secs
        self.lock_obtained = False
        self.redis = get_redis_connection(
            redis_url,
            redis_sentinels,
            redis_cluster=redis_cluster,
            decode_responses=True,
        )

    def aquire_lock(self):
        # nx=True will only set this key if it _hasn't_ already been set
        self.lock_obtained = self.redis.set(self.lock_name, self.lock_id, nx=True, ex=self.timeout_secs)
        return self.lock_obtained

    def renew_lock(self):
        return bool(self.redis.eval(self._RENEW_SCRIPT, 1, self.lock_name, self.lock_id, self.timeout_secs))

    def release_lock(self):
        self.redis.eval(self._RELEASE_SCRIPT, 1, self.lock_name, self.lock_id)


class RedisDict:
    def __init__(self, name, redis_url, redis_sentinels=[], redis_cluster=False):
        self.name = name
        # Per-process cache of the last payload fingerprint written by set().
        # Used to skip redundant HSET round-trips when the model list hasn't
        # changed — the dominant Redis write source on busy multi-pod setups.
        self._last_signature: str | None = None
        self.redis = get_redis_connection(
            redis_url,
            redis_sentinels,
            redis_cluster=redis_cluster,
            decode_responses=True,
        )

    def __setitem__(self, key, value):
        serialized_value = JSONCodec.dumps(value)
        self.redis.hset(self.name, key, serialized_value)

    def __getitem__(self, key):
        value = self.redis.hget(self.name, key)
        if value is None:
            raise KeyError(key)
        return JSONCodec.loads(value)

    def __delitem__(self, key):
        result = self.redis.hdel(self.name, key)
        if result == 0:
            raise KeyError(key)

    def __contains__(self, key):
        return self.redis.hexists(self.name, key)

    def __len__(self):
        return self.redis.hlen(self.name)

    def keys(self):
        return self.redis.hkeys(self.name)

    def values(self):
        return [JSONCodec.loads(v) for v in self.redis.hvals(self.name)]

    def items(self):
        return [(k, JSONCodec.loads(v)) for k, v in self.redis.hgetall(self.name).items()]

    def set(self, mapping: dict):
        if not mapping:
            self.redis.delete(self.name)
            self._last_signature = None
            return

        # Serialize values once — reused for both the fingerprint and the write.
        serialized = {k: JSONCodec.dumps(v) for k, v in mapping.items()}
        digest = hashlib.sha256()
        for key in sorted(serialized):
            digest.update(key.encode())
            digest.update(b'\0')
            digest.update(serialized[key].encode())
            digest.update(b'\0')
        signature = digest.hexdigest()

        # Skip the write when the prepared mapping is identical to the last one
        # this process wrote.  The check is per-instance (not distributed), but
        # still eliminates the majority of redundant writes because each pod
        # typically produces the same model list on consecutive refreshes.
        if signature == self._last_signature:
            return

        # Fetch existing keys before writing so we know which ones to remove.
        # HKEYS is cheap — it transfers only short key strings, not large JSON values.
        existing_keys = set(self.redis.hkeys(self.name))
        new_keys = set(mapping.keys())
        keys_to_remove = existing_keys - new_keys

        # HSET first (add/update all new values), then HDEL (remove stale keys).
        # We never DELETE the whole hash — this eliminates the race window
        # where concurrent readers would see an empty models dict.
        self.redis.hset(self.name, mapping=serialized)
        if keys_to_remove:
            self.redis.hdel(self.name, *keys_to_remove)

        self._last_signature = signature

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default

    def clear(self):
        self.redis.delete(self.name)
        self._last_signature = None

    def update(self, other=None, **kwargs):
        if other is not None:
            for k, v in other.items() if hasattr(other, 'items') else other:
                self[k] = v
        for k, v in kwargs.items():
            self[k] = v

    def setdefault(self, key, default=None):
        if key not in self:
            self[key] = default
        return self[key]
