from __future__ import annotations

import re
from enum import Enum
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SERVER_ID_RE = re.compile(r'^[a-z0-9][a-z0-9._-]{0,63}$')


class EnvironmentSetting(BaseModel):
    default: str | None = None
    description: str = ''
    secret: bool = False


class RuntimeSpec(BaseModel):
    type: Literal['python'] = 'python'
    command: str
    args: list[str] = Field(default_factory=list)
    working_directory: str = '.'

    @field_validator('command')
    @classmethod
    def command_is_a_program(cls, value: str) -> str:
        if not value or any(char.isspace() for char in value):
            raise ValueError('runtime.command must be one executable, not a shell command')
        return value


class TransportSpec(BaseModel):
    type: Literal['stdio'] = 'stdio'


class LimitsSpec(BaseModel):
    startup_timeout_seconds: float = Field(default=30, gt=0, le=300)
    call_timeout_seconds: float = Field(default=30, gt=0, le=3600)
    memory_mb: int = Field(default=256, ge=32, le=65536)
    restart: Literal['never', 'on-failure'] = 'on-failure'
    max_restarts: int = Field(default=3, ge=0, le=20)


class SecuritySpec(BaseModel):
    profile: Literal['confined', 'system-admin'] = 'confined'
    filesystem_roots: list[str] = Field(default_factory=list)
    read_only: bool = True
    root_write: bool = False

    @model_validator(mode='after')
    def validate_capabilities(self):
        if self.root_write and (self.profile != 'system-admin' or self.read_only):
            raise ValueError('root_write requires system-admin with read_only=false')
        if self.profile == 'system-admin' and not self.read_only and not self.root_write:
            raise ValueError('writable system-admin requires root_write=true')
        return self


class MCPManifest(BaseModel):
    model_config = ConfigDict(extra='forbid')

    schema_version: Literal[1]
    id: str
    name: str
    description: str = ''
    version: str
    runtime: RuntimeSpec
    transport: TransportSpec = Field(default_factory=TransportSpec)
    environment: dict[str, EnvironmentSetting] = Field(default_factory=dict)
    limits: LimitsSpec = Field(default_factory=LimitsSpec)
    security: SecuritySpec = Field(default_factory=SecuritySpec)

    @field_validator('id')
    @classmethod
    def valid_id(cls, value: str) -> str:
        if not SERVER_ID_RE.fullmatch(value):
            raise ValueError('id must be 1-64 lowercase letters, digits, dot, underscore, or hyphen')
        return value

    @field_validator('environment')
    @classmethod
    def valid_environment_names(cls, value: dict[str, EnvironmentSetting]):
        for name in value:
            if not re.fullmatch(r'[A-Z_][A-Z0-9_]*', name):
                raise ValueError(f'invalid environment variable name: {name}')
        return value


class AccessGrant(BaseModel):
    principal_type: Literal['user', 'group']
    principal_id: str = Field(min_length=1)
    permission: Literal['read'] = 'read'


class ServerState(str, Enum):
    stopped = 'stopped'
    starting = 'starting'
    ready = 'ready'
    failed = 'failed'
    stopping = 'stopping'


class ManagedServerCreate(BaseModel):
    package_path: str
    environment: dict[str, str] = Field(default_factory=dict)
    secret_environment: dict[str, str] = Field(default_factory=dict)
    access_grants: list[AccessGrant] = Field(default_factory=list)
    enabled: bool = False


class ManagedServerUpdate(BaseModel):
    environment: dict[str, str] | None = None
    secret_environment: dict[str, str] | None = None
    access_grants: list[AccessGrant] | None = None
    enabled: bool | None = None


class ManagedServer(BaseModel):
    id: str
    package_path: str
    package_digest: str
    manifest: MCPManifest
    environment: dict[str, str] = Field(default_factory=dict)
    secret_environment: dict[str, str] = Field(default_factory=dict)
    access_grants: list[AccessGrant] = Field(default_factory=list)
    enabled: bool = False
    created_by: str
    created_at: int
    updated_at: int


class ManagedServerView(BaseModel):
    id: str
    name: str
    description: str
    version: str
    package_path: str
    package_digest: str
    enabled: bool
    state: ServerState = ServerState.stopped
    last_error: str | None = None
    tools: list[dict] = Field(default_factory=list)
    access_grants: list[AccessGrant] = Field(default_factory=list)
    security: SecuritySpec
    created_by: str
    created_at: int
    updated_at: int


def load_manifest(path: Path) -> MCPManifest:
    try:
        data = yaml.safe_load(path.read_text(encoding='utf-8'))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f'cannot read manifest: {exc}') from exc
    if not isinstance(data, dict):
        raise ValueError('manifest must contain a YAML object')
    return MCPManifest.model_validate(data)
