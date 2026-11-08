"""Small, robust JSON-file storage used instead of a database.

Guarantees:
* Atomic writes: data is written to a temp file, fsync'ed and moved into
  place with ``os.replace`` so a crash never leaves a half-written file.
* Thread safety: one re-entrant lock per file; ``edit()`` holds it for the
  whole read-modify-write cycle.
* Corruption tolerance: an unreadable file is moved aside to
  ``<name>.corrupt-<timestamp>`` and the default value is returned, so the
  app keeps running and the bad file can be inspected later.

It is designed for a single server process (the default ``uvicorn main:app``).
Running several worker processes against the same data folder is not
supported.
"""
from __future__ import annotations

import copy
import json
import logging
import os
import re
import tempfile
import threading
import time
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterator

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


class JsonStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._locks: dict[str, threading.RLock] = {}
        self._meta = threading.Lock()
        self._version = 0

    # -- helpers -----------------------------------------------------------
    @property
    def version(self) -> int:
        """Increments on every successful write; used to invalidate caches."""
        return self._version

    def _lock(self, name: str) -> threading.RLock:
        with self._meta:
            lock = self._locks.get(name)
            if lock is None:
                lock = self._locks[name] = threading.RLock()
            return lock

    def path(self, name: str) -> Path:
        if not _SAFE_NAME.match(name):
            raise ValueError(f"Unsafe storage name {name!r}")
        return self.root / f"{name}.json"

    # -- public API --------------------------------------------------------
    def read(self, name: str, default: Any = None) -> Any:
        with self._lock(name):
            return self._read(name, default)

    def write(self, name: str, data: Any, *, pretty: bool = True) -> None:
        with self._lock(name):
            self._write(name, data, pretty)

    @contextmanager
    def edit(self, name: str, default: Any, *, pretty: bool = True) -> Iterator[Any]:
        """Read-modify-write under the file lock.  Nothing is written if the
        block raises."""
        with self._lock(name):
            data = self._read(name, default)
            yield data
            self._write(name, data, pretty)

    def delete(self, name: str) -> None:
        with self._lock(name):
            try:
                self.path(name).unlink()
                self._bump()
            except FileNotFoundError:
                pass

    def exists(self, name: str) -> bool:
        return self.path(name).exists()

    # -- internals ---------------------------------------------------------
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

    def _write(self, name: str, data: Any, pretty: bool) -> None:
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
            # Windows can briefly lock the target (antivirus, indexer): retry.
            for attempt in range(6):
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
        self._bump()

    def _bump(self) -> None:
        with self._meta:
            self._version += 1
