from __future__ import annotations

import json
import os
import re
import secrets
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable


ARTIFACT_ID_RE = re.compile(r"^wr_[0-9a-f]{32}$")


class ArtifactError(ValueError):
    """A safe, model-facing artifact error."""


class ResearchArtifactStore:
    """Small expiring JSON artifact repository for cleaned web research text."""

    def __init__(
        self,
        root: Path,
        *,
        ttl_seconds: int = 3600,
        max_artifacts: int = 64,
        max_total_bytes: int = 32 * 1024 * 1024,
        max_artifact_bytes: int = 2 * 1024 * 1024,
        max_read_characters: int = 20_000,
        max_search_matches: int = 20,
        max_search_context_characters: int = 500,
        clock: Callable[[], float] = time.time,
    ):
        if ttl_seconds < 1:
            raise ValueError("artifact ttl must be positive")
        if min(max_artifacts, max_total_bytes, max_artifact_bytes, max_read_characters) < 1:
            raise ValueError("artifact limits must be positive")
        if min(max_search_matches, max_search_context_characters) < 1:
            raise ValueError("artifact search limits must be positive")
        self.root = root.resolve()
        self.ttl_seconds = ttl_seconds
        self.max_artifacts = max_artifacts
        self.max_total_bytes = max_total_bytes
        self.max_artifact_bytes = max_artifact_bytes
        self.max_read_characters = max_read_characters
        self.max_search_matches = max_search_matches
        self.max_search_context_characters = max_search_context_characters
        self.clock = clock
        self._lock = threading.RLock()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.root.is_symlink():
            raise ValueError("artifact directory must not be a symlink")
        try:
            self.root.chmod(0o700)
        except OSError:
            pass

    def create(self, kind: str, sources: list[dict[str, Any]]) -> dict[str, Any]:
        if kind not in {"page", "crawl"}:
            raise ArtifactError("unsupported artifact kind")
        if not sources:
            raise ArtifactError("artifact must contain at least one source")
        now = int(self.clock())
        artifact_id = f"wr_{secrets.token_hex(16)}"
        normalized_sources = []
        for index, source in enumerate(sources):
            content = source.get("content", "")
            if not isinstance(content, str):
                raise ArtifactError("artifact source content must be text")
            normalized_sources.append(
                {
                    "index": index,
                    "url": str(source.get("url") or ""),
                    "title": source.get("title") if isinstance(source.get("title"), str) else None,
                    "content": content,
                    "truncated": bool(source.get("truncated") or source.get("source_truncated")),
                }
            )
        document = {
            "schema_version": 1,
            "id": artifact_id,
            "kind": kind,
            "created_at": now,
            "expires_at": now + self.ttl_seconds,
            "sources": normalized_sources,
        }
        payload = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(payload) > self.max_artifact_bytes:
            raise ArtifactError(
                f"research result exceeds the {self.max_artifact_bytes}-byte artifact limit"
            )
        with self._lock:
            self._cleanup_locked(now)
            self._make_room_locked(len(payload))
            fd, temporary_name = tempfile.mkstemp(prefix=".artifact-", dir=self.root)
            temporary = Path(temporary_name)
            try:
                with os.fdopen(fd, "wb") as output:
                    output.write(payload)
                    output.flush()
                    os.fsync(output.fileno())
                os.chmod(temporary, 0o600)
                os.replace(temporary, self._path(artifact_id))
            finally:
                temporary.unlink(missing_ok=True)
        return self._metadata(document)

    def read(
        self,
        artifact_id: str,
        *,
        source_index: int = 0,
        offset: int = 0,
        max_characters: int | None = None,
    ) -> dict[str, Any]:
        if source_index < 0 or offset < 0:
            raise ArtifactError("source_index and offset must be non-negative")
        requested = max_characters or self.max_read_characters
        if requested < 1:
            raise ArtifactError("max_characters must be positive")
        limit = min(requested, self.max_read_characters)
        document = self._load(artifact_id)
        sources = document["sources"]
        if source_index >= len(sources):
            raise ArtifactError("source_index is outside this artifact")
        source = sources[source_index]
        content = source["content"]
        end = min(len(content), offset + limit)
        return {
            "artifact": self._metadata(document),
            "source": self._source_metadata(source),
            "offset": offset,
            "end": end,
            "content": content[offset:end] if offset < len(content) else "",
            "has_more": end < len(content),
        }

    def search(
        self,
        artifact_id: str,
        query: str,
        *,
        max_matches: int | None = None,
        context_characters: int = 160,
    ) -> dict[str, Any]:
        query = query.strip()
        if not query:
            raise ArtifactError("query is required")
        if len(query) > 500:
            raise ArtifactError("query is too long")
        requested_matches = max_matches or self.max_search_matches
        if requested_matches < 1 or context_characters < 0:
            raise ArtifactError("search limits must be non-negative and max_matches must be positive")
        match_limit = min(requested_matches, self.max_search_matches)
        context_limit = min(context_characters, self.max_search_context_characters)
        document = self._load(artifact_id)
        needle = query.casefold()
        matches = []
        total_matches = 0
        for source in document["sources"]:
            content = source["content"]
            folded = content.casefold()
            cursor = 0
            while True:
                position = folded.find(needle, cursor)
                if position < 0:
                    break
                total_matches += 1
                if len(matches) < match_limit:
                    start = max(0, position - context_limit)
                    end = min(len(content), position + len(query) + context_limit)
                    matches.append(
                        {
                            "source_index": source["index"],
                            "url": source["url"],
                            "title": source["title"],
                            "start": position,
                            "end": position + len(query),
                            "excerpt": content[start:end],
                        }
                    )
                cursor = position + max(1, len(needle))
        return {
            "artifact": self._metadata(document),
            "query": query,
            "matches": matches,
            "returned_matches": len(matches),
            "total_matches": total_matches,
            "truncated": total_matches > len(matches),
        }

    def cleanup(self) -> int:
        with self._lock:
            return self._cleanup_locked(int(self.clock()))

    def _load(self, artifact_id: str) -> dict[str, Any]:
        path = self._path(artifact_id)
        with self._lock:
            self._cleanup_locked(int(self.clock()))
            try:
                payload = path.read_bytes()
                if len(payload) > self.max_artifact_bytes:
                    raise ArtifactError("artifact is invalid or exceeds its storage limit")
                document = json.loads(payload)
            except FileNotFoundError as exc:
                raise ArtifactError("artifact was not found or has expired") from exc
            except (OSError, json.JSONDecodeError) as exc:
                raise ArtifactError("artifact could not be read") from exc
        if (
            not isinstance(document, dict)
            or document.get("schema_version") != 1
            or document.get("id") != artifact_id
            or not isinstance(document.get("sources"), list)
        ):
            raise ArtifactError("artifact has an invalid format")
        return document

    def _path(self, artifact_id: str) -> Path:
        if not isinstance(artifact_id, str) or not ARTIFACT_ID_RE.fullmatch(artifact_id):
            raise ArtifactError("invalid artifact ID")
        return self.root / f"{artifact_id}.json"

    def _files_locked(self) -> list[Path]:
        return [path for path in self.root.glob("wr_*.json") if path.is_file() and not path.is_symlink()]

    def _cleanup_locked(self, now: int) -> int:
        removed = 0
        for path in self._files_locked():
            try:
                document = json.loads(path.read_text(encoding="utf-8"))
                expired = int(document.get("expires_at", 0)) <= now
            except (OSError, ValueError, json.JSONDecodeError):
                expired = True
            if expired:
                path.unlink(missing_ok=True)
                removed += 1
        return removed

    def _make_room_locked(self, incoming_bytes: int) -> None:
        if incoming_bytes > self.max_total_bytes:
            raise ArtifactError("artifact exceeds the total storage quota")
        files = sorted(self._files_locked(), key=lambda path: (path.stat().st_mtime, path.name))
        total = sum(path.stat().st_size for path in files)
        while files and (len(files) >= self.max_artifacts or total + incoming_bytes > self.max_total_bytes):
            oldest = files.pop(0)
            total -= oldest.stat().st_size
            oldest.unlink(missing_ok=True)

    @classmethod
    def _metadata(cls, document: dict[str, Any]) -> dict[str, Any]:
        sources = [cls._source_metadata(source) for source in document["sources"]]
        return {
            "id": document["id"],
            "kind": document["kind"],
            "created_at": document["created_at"],
            "expires_at": document["expires_at"],
            "source_count": len(sources),
            "character_count": sum(source["character_count"] for source in sources),
            "sources": sources,
        }

    @staticmethod
    def _source_metadata(source: dict[str, Any]) -> dict[str, Any]:
        content = source["content"]
        excerpt = content[:240]
        if len(content) > len(excerpt):
            excerpt = excerpt.rstrip() + "…"
        return {
            "index": source["index"],
            "url": source["url"],
            "title": source["title"],
            "excerpt": excerpt,
            "character_count": len(content),
            "truncated": bool(source["truncated"]),
        }
