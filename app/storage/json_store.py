"""JSON document storage used instead of a database.

Two interchangeable backends with the same small interface
(read / write / edit / delete / exists / list):

* ``JsonStore``  - JSON files on disk (your PC, a VPS, Docker with a volume).
  Atomic writes (temp file + fsync + rename), one lock per file, and a corrupt
  file is moved aside instead of crashing the app.
* ``RedisStore`` - the same documents in Upstash Redis via its REST API, for
  serverless hosting such as Vercel where the disk is not persistent.  Values
  are zlib-compressed JSON, reads are cached for a few seconds per instance,
  and cache entries can expire automatically (TTL).

``make_store()`` picks Redis when Upstash/Vercel-KV credentials are present.
"""
from __future__ import annotations

import base64
import copy
import json
import logging
import os
import re
import tempfile
import threading
import time
import zlib
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

log = logging.getLogger("betfinder.storage")

_SAFE_NAME = re.compile(r"^[A-Za-z0-9_\-]+(/[A-Za-z0-9_\-]+)*$")


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (set, tuple)):
        return list(value)
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serialisable")


def _check_name(name: str) -> str:
    if not _SAFE_NAME.match(name):
        raise ValueError(f"Unsafe storage name {name!r}")
    return name


class BaseStore:
    kind = "base"
    persistent = True

    def __init__(self) -> None:
        self._locks: dict[str, threading.RLock] = {}
        self._meta = threading.Lock()
        self._version = 0

    @property
    def version(self) -> int:
        """Changes whenever data may have changed; used to invalidate caches."""
        return self._version

    def _bump(self) -> None:
        with self._meta:
            self._version += 1

    def _lock(self, name: str) -> threading.RLock:
        with self._meta:
            lock = self._locks.get(name)
            if lock is None:
                lock = self._locks[name] = threading.RLock()
            return lock

    def read(self, name: str, default: Any = None) -> Any:
        with self._lock(name):
            return self._read(_check_name(name), default)

    def write(self, name: str, data: Any, *, pretty: bool = True, ttl: Optional[int] = None) -> None:
        with self._lock(name):
            self._write(_check_name(name), data, pretty, ttl)
            self._bump()

    @contextmanager
    def edit(self, name: str, default: Any, *, pretty: bool = True) -> Iterator[Any]:
        """Read-modify-write under the lock.  Nothing is written if the block raises."""
        with self._lock(name):
            data = self._read(_check_name(name), default)
            yield data
            self._write(name, data, pretty, None)
            self._bump()

    # backend specific
    def _read(self, name: str, default: Any) -> Any:  # pragma: no cover - interface
        raise NotImplementedError

    def _write(self, name: str, data: Any, pretty: bool, ttl: Optional[int]) -> None:  # pragma: no cover
        raise NotImplementedError

    def delete(self, name: str) -> None:  # pragma: no cover
        raise NotImplementedError

    def exists(self, name: str) -> bool:  # pragma: no cover
        raise NotImplementedError

    def list(self, prefix: str) -> list[str]:  # pragma: no cover
        raise NotImplementedError

    def cleanup(self, prefix: str, older_than_seconds: int) -> int:
        return 0


class JsonStore(BaseStore):
    """JSON files under ``root``."""
    kind = "files"

    def __init__(self, root: Path, persistent: bool = True):
        super().__init__()
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.persistent = persistent

    def path(self, name: str) -> Path:
        return self.root / f"{_check_name(name)}.json"

    def _read(self, name: str, default: Any) -> Any:
        p = self.path(name)
        if not p.exists():
            return copy.deepcopy(default)
        try:
            with p.open("r", encoding="utf-8") as fh:
                return json.load(fh)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
            backup = p.with_name(f"{p.name}.corrupt-{stamp}")
            log.error("Corrupt JSON in %s (%s); moved to %s", p, exc, backup.name)
            try:
                os.replace(p, backup)
            except OSError:
                log.exception("Could not move corrupt file %s aside", p)
            return copy.deepcopy(default)
        except OSError:
            log.exception("Could not read %s", p)
            return copy.deepcopy(default)

    def _write(self, name: str, data: Any, pretty: bool, ttl: Optional[int]) -> None:
        p = self.path(name)
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=f".{p.stem}-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                if pretty:
                    json.dump(data, fh, ensure_ascii=False, indent=2, default=_json_default)
                else:
                    json.dump(data, fh, ensure_ascii=False, separators=(",", ":"), default=_json_default)
                fh.flush()
                os.fsync(fh.fileno())
            for attempt in range(6):  # Windows can briefly lock the target (antivirus, indexer)
                try:
                    os.replace(tmp, p)
                    break
                except PermissionError:
                    if attempt == 5:
                        raise
                    time.sleep(0.05 * (attempt + 1))
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def delete(self, name: str) -> None:
        with self._lock(name):
            try:
                self.path(name).unlink()
                self._bump()
            except FileNotFoundError:
                pass

    def exists(self, name: str) -> bool:
        return self.path(name).exists()

    def list(self, prefix: str) -> list[str]:
        base = self.root
        out = []
        for p in base.rglob("*.json"):
            name = p.relative_to(base).as_posix()[:-5]
            if name.startswith(prefix):
                out.append(name)
        return sorted(out)

    def cleanup(self, prefix: str, older_than_seconds: int) -> int:
        cutoff = time.time() - older_than_seconds
        removed = 0
        for name in self.list(prefix):
            p = self.path(name)
            try:
                if p.stat().st_mtime < cutoff:
                    p.unlink()
                    removed += 1
            except OSError:
                continue
        return removed


class StorageError(RuntimeError):
    pass


class RedisStore(BaseStore):
    """Documents in Upstash Redis (REST API) - works on Vercel and other serverless hosts."""
    kind = "redis"
    CACHE_SECONDS = 4.0

    def __init__(self, url: str, token: str, prefix: str = "betfinder:"):
        super().__init__()
        import httpx  # local import keeps the file backend dependency-free
        self.url = url.rstrip("/")
        self.prefix = prefix
        self._client = httpx.Client(timeout=15, headers={"Authorization": f"Bearer {token}"})
        self._cache: dict[str, tuple[float, Any]] = {}

    @property
    def version(self) -> int:
        # other instances may have written: let caches expire every few seconds
        return self._version * 100_000 + int(time.time() // 15)

    def _cmd(self, *args) -> Any:
        last = None
        for attempt in range(3):
            try:
                resp = self._client.post(self.url, json=[str(a) if not isinstance(a, (int, float)) else a for a in args])
                if resp.status_code >= 500:
                    last = f"HTTP {resp.status_code}"
                    time.sleep(0.3 * (attempt + 1))
                    continue
                data = resp.json()
                if resp.status_code >= 400 or "error" in data:
                    raise StorageError(f"Redis error: {data.get('error', resp.status_code)}")
                return data.get("result")
            except (ValueError, OSError) as exc:  # network/JSON problems
                last = type(exc).__name__
                time.sleep(0.3 * (attempt + 1))
            except Exception as exc:  # httpx errors
                if isinstance(exc, StorageError):
                    raise
                last = type(exc).__name__
                time.sleep(0.3 * (attempt + 1))
        raise StorageError(f"Redis unavailable ({last})")

    @staticmethod
    def _encode(data: Any) -> str:
        raw = json.dumps(data, ensure_ascii=False, separators=(",", ":"), default=_json_default).encode("utf-8")
        return "z:" + base64.b64encode(zlib.compress(raw, 6)).decode("ascii")

    @staticmethod
    def _decode(value: str) -> Any:
        if value.startswith("z:"):
            return json.loads(zlib.decompress(base64.b64decode(value[2:])).decode("utf-8"))
        return json.loads(value)

    def _read(self, name: str, default: Any) -> Any:
        hit = self._cache.get(name)
        if hit and time.monotonic() - hit[0] < self.CACHE_SECONDS:
            return copy.deepcopy(hit[1])
        value = self._cmd("GET", self.prefix + name)
        if value is None:
            data = copy.deepcopy(default)
            self._cache[name] = (time.monotonic(), copy.deepcopy(default))
            return data
        try:
            data = self._decode(value)
        except (ValueError, zlib.error) as exc:
            log.error("Corrupt value in Redis key %s (%s); using default", name, exc)
            return copy.deepcopy(default)
        self._cache[name] = (time.monotonic(), copy.deepcopy(data))
        return data

    def _write(self, name: str, data: Any, pretty: bool, ttl: Optional[int]) -> None:
        args = ["SET", self.prefix + name, self._encode(data)]
        if ttl:
            args += ["EX", int(ttl)]
        self._cmd(*args)
        self._cache[name] = (time.monotonic(), copy.deepcopy(data))

    def delete(self, name: str) -> None:
        with self._lock(name):
            self._cmd("DEL", self.prefix + _check_name(name))
            self._cache.pop(name, None)
            self._bump()

    def exists(self, name: str) -> bool:
        return bool(self._cmd("EXISTS", self.prefix + _check_name(name)))

    def list(self, prefix: str) -> list[str]:
        keys, cursor = [], "0"
        while True:
            res = self._cmd("SCAN", cursor, "MATCH", f"{self.prefix}{prefix}*", "COUNT", 500)
            cursor, batch = str(res[0]), res[1]
            keys.extend(k[len(self.prefix):] for k in batch)
            if cursor == "0":
                break
        return sorted(set(keys))


def make_store(runtime_dir: Path) -> BaseStore:
    """Redis when Upstash / Vercel-KV credentials exist, else files.

    On Vercel without Redis the files go to /tmp, which is NOT persistent - the
    UI shows a warning in that case."""
    url = os.environ.get("UPSTASH_REDIS_REST_URL") or os.environ.get("KV_REST_API_URL")
    token = os.environ.get("UPSTASH_REDIS_REST_TOKEN") or os.environ.get("KV_REST_API_TOKEN")
    if url and token:
        return RedisStore(url, token)
    if os.environ.get("VERCEL"):
        return JsonStore(Path(tempfile.gettempdir()) / "betfinder-runtime", persistent=False)
    return JsonStore(runtime_dir)
