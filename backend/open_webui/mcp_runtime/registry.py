from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import tempfile
import time
from pathlib import Path

from pydantic import BaseModel, Field

from .schemas import ManagedServer, ManagedServerCreate, ManagedServerUpdate, load_manifest


class RegistryDocument(BaseModel):
    schema_version: int = 1
    revision: int = 0
    servers: dict[str, ManagedServer] = Field(default_factory=dict)


class RegistryError(RuntimeError):
    pass


def package_digest(package_path: Path) -> str:
    digest = hashlib.sha256()
    excluded_directories = {'.git', '.venv', '__pycache__', 'data', 'sandbox'}
    files = []
    for file_path in package_path.rglob('*'):
        relative = file_path.relative_to(package_path)
        if any(part in excluded_directories for part in relative.parts):
            continue
        if file_path.is_symlink():
            raise RegistryError(f'package source may not contain symlinks: {relative}')
        if file_path.is_file():
            files.append((relative, file_path))
    for relative, file_path in sorted(files, key=lambda item: item[0].as_posix()):
        digest.update(relative.as_posix().encode())
        digest.update(b'\0')
        with file_path.open('rb') as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b''):
                digest.update(chunk)
    return digest.hexdigest()


class JSONRegistry:
    def __init__(self, path: Path, package_roots: list[Path]):
        self.path = path
        self.backup_path = path.with_suffix(path.suffix + '.bak')
        self.package_roots = [root.resolve() for root in package_roots]
        self._lock = asyncio.Lock()
        self._document: RegistryDocument | None = None

    async def load(self) -> RegistryDocument:
        async with self._lock:
            self._document = self._load_from_disk()
            return self._document.model_copy(deep=True)

    def _load_from_disk(self) -> RegistryDocument:
        if not self.path.exists():
            return RegistryDocument()
        try:
            return RegistryDocument.model_validate_json(self.path.read_text(encoding='utf-8'))
        except Exception as primary:
            if self.backup_path.exists():
                try:
                    return RegistryDocument.model_validate_json(self.backup_path.read_text(encoding='utf-8'))
                except Exception:
                    pass
            raise RegistryError(f'registry is invalid and no valid backup exists: {primary}') from primary

    async def snapshot(self) -> RegistryDocument:
        async with self._lock:
            if self._document is None:
                self._document = self._load_from_disk()
            return self._document.model_copy(deep=True)

    async def discover(self) -> dict:
        """Find manifest packages below configured roots without mutating the registry."""
        document = await self.snapshot()
        services = []
        errors = []
        seen_paths: set[Path] = set()

        for root in self.package_roots:
            if not root.is_dir():
                errors.append({'package_path': str(root), 'error': 'package root is not a directory'})
                continue

            candidates = [root] if (root / 'mcp.yaml').is_file() else []
            # A package root is a catalogue directory: each immediate child is
            # one service. Do not crawl package internals or virtualenvs.
            candidates.extend(path for path in root.iterdir() if path.is_dir() and (path / 'mcp.yaml').is_file())
            for candidate in sorted(set(candidates), key=lambda path: str(path)):
                try:
                    package = self.resolve_package(str(candidate))
                    if package in seen_paths:
                        continue
                    seen_paths.add(package)
                    manifest = load_manifest(package / 'mcp.yaml')
                    registered = document.servers.get(manifest.id)
                    if registered and Path(registered.package_path) == package:
                        discovery_state = 'registered'
                    elif registered:
                        discovery_state = 'conflict'
                    else:
                        discovery_state = 'available'
                    services.append(
                        {
                            'id': manifest.id,
                            'name': manifest.name,
                            'description': manifest.description,
                            'version': manifest.version,
                            'package_path': str(package),
                            'package_digest': package_digest(package),
                            'discovery_state': discovery_state,
                            'enabled': registered.enabled if registered else False,
                            'runtime_state': None,
                            'environment': {
                                name: definition.model_dump(mode='json')
                                for name, definition in manifest.environment.items()
                            },
                            'security': manifest.security.model_dump(mode='json'),
                        }
                    )
                except (OSError, RegistryError, ValueError) as exc:
                    errors.append({'package_path': str(candidate), 'error': str(exc)})

        return {
            'roots': [str(root) for root in self.package_roots],
            'services': services,
            'errors': errors,
        }

    def resolve_package(self, value: str) -> Path:
        candidate = Path(value).expanduser().resolve()
        if not any(candidate == root or candidate.is_relative_to(root) for root in self.package_roots):
            raise RegistryError('package path is outside MANAGED_MCP_PACKAGE_ROOTS')
        if not candidate.is_dir():
            raise RegistryError('package path is not a directory')
        for required in ('mcp.yaml', 'pyproject.toml', 'uv.lock'):
            if not (candidate / required).is_file():
                raise RegistryError(f'package is missing {required}')
        package_digest(candidate)
        return candidate

    async def create(self, form: ManagedServerCreate, actor_id: str) -> ManagedServer:
        package = self.resolve_package(form.package_path)
        manifest = load_manifest(package / 'mcp.yaml')
        self._validate_environment(manifest, package, form.environment, form.secret_environment)
        now = int(time.time())
        server = ManagedServer(
            id=manifest.id,
            package_path=str(package),
            package_digest=package_digest(package),
            manifest=manifest,
            environment=form.environment,
            secret_environment=form.secret_environment,
            access_grants=form.access_grants,
            enabled=form.enabled,
            created_by=actor_id,
            created_at=now,
            updated_at=now,
        )
        async with self._lock:
            document = self._document or self._load_from_disk()
            if server.id in document.servers:
                raise RegistryError(f'server {server.id} is already registered')
            document.servers[server.id] = server
            document.revision += 1
            self._write(document)
            self._document = document
        return server.model_copy(deep=True)

    async def update(self, server_id: str, form: ManagedServerUpdate) -> ManagedServer:
        async with self._lock:
            document = self._document or self._load_from_disk()
            server = document.servers.get(server_id)
            if not server:
                raise RegistryError(f'server {server_id} not found')
            updates = form.model_dump(exclude_none=True)
            values = server.model_dump()
            values.update(updates)
            values['updated_at'] = int(time.time())
            updated = ManagedServer.model_validate(values)
            self._validate_environment(
                updated.manifest,
                Path(updated.package_path),
                updated.environment,
                updated.secret_environment,
            )
            document.servers[server_id] = updated
            document.revision += 1
            self._write(document)
            self._document = document
            return updated.model_copy(deep=True)

    async def delete(self, server_id: str) -> None:
        async with self._lock:
            document = self._document or self._load_from_disk()
            if server_id not in document.servers:
                raise RegistryError(f'server {server_id} not found')
            del document.servers[server_id]
            document.revision += 1
            self._write(document)
            self._document = document

    @staticmethod
    def _validate_environment(manifest, package_path, environment, secret_environment):
        definitions = manifest.environment
        unknown = (set(environment) | set(secret_environment)) - set(definitions)
        if unknown:
            raise RegistryError(f'undeclared environment variables: {", ".join(sorted(unknown))}')
        overlap = set(environment) & set(secret_environment)
        if overlap:
            raise RegistryError(
                f'environment variables cannot be both regular and secret: {", ".join(sorted(overlap))}'
            )
        for name, definition in definitions.items():
            if definition.secret and name in environment:
                raise RegistryError(f'{name} must be supplied as a secret environment reference')
        allowed_roots = manifest.security.filesystem_roots
        if allowed_roots and 'MCP_FILESYSTEM_ROOT' in definitions:
            configured = environment.get('MCP_FILESYSTEM_ROOT', definitions['MCP_FILESYSTEM_ROOT'].default)
            if configured:
                configured_path = Path(configured)
                if not configured_path.is_absolute():
                    configured_path = package_path / configured_path
                configured_path = configured_path.resolve()
                resolved_roots = []
                for root in allowed_roots:
                    root_path = Path(root)
                    if not root_path.is_absolute():
                        root_path = package_path / root_path
                    resolved_roots.append(root_path.resolve())
                if not any(configured_path == root or configured_path.is_relative_to(root) for root in resolved_roots):
                    raise RegistryError('MCP_FILESYSTEM_ROOT is outside manifest filesystem_roots')

    def _write(self, document: RegistryDocument) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = document.model_dump_json(indent=2).encode()
        fd, temporary_name = tempfile.mkstemp(prefix=f'.{self.path.name}.', dir=self.path.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, 'wb') as output:
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            if self.path.exists():
                shutil.copy2(self.path, self.backup_path)
            os.replace(temporary, self.path)
            directory_fd = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            temporary.unlink(missing_ok=True)
