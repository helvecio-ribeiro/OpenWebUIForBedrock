from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

DEFAULT_DATA_DIR = Path(__file__).resolve().parents[2] / 'data'
PROJECT_DIR = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class RuntimeSettings:
    registry_path: Path
    package_roots: list[Path]
    token: str
    host: str = '127.0.0.1'
    port: int = 9090
    privilege_policy_path: Path | None = None
    allow_root_runtime: bool = False
    unprivileged_uid: int | None = None
    unprivileged_gid: int | None = None

    @classmethod
    def from_environment(cls):
        load_dotenv(PROJECT_DIR / '.env', override=False)
        roots = [
            Path(value).expanduser().resolve()
            for value in os.getenv('MANAGED_MCP_PACKAGE_ROOTS', '').split(os.pathsep)
            if value.strip()
        ]
        token = os.getenv('MANAGED_MCP_RUNTIME_TOKEN', '')
        token_file = os.getenv('MANAGED_MCP_RUNTIME_TOKEN_FILE', '')
        if not token and token_file:
            token = Path(token_file).read_text(encoding='utf-8').strip()
        if not token:
            raise RuntimeError('MANAGED_MCP_RUNTIME_TOKEN or MANAGED_MCP_RUNTIME_TOKEN_FILE is required')
        if not roots:
            raise RuntimeError('MANAGED_MCP_PACKAGE_ROOTS must contain at least one directory')
        policy = os.getenv('MANAGED_MCP_PRIVILEGE_POLICY_FILE', '')
        return cls(
            registry_path=Path(
                os.getenv(
                    'MANAGED_MCP_REGISTRY_PATH',
                    DEFAULT_DATA_DIR / 'managed-mcp' / 'registry.json',
                )
            ).resolve(),
            package_roots=roots,
            token=token,
            host=os.getenv('MANAGED_MCP_RUNTIME_HOST', '127.0.0.1'),
            port=int(os.getenv('MANAGED_MCP_RUNTIME_PORT', '9090')),
            privilege_policy_path=Path(policy).resolve() if policy else None,
            allow_root_runtime=os.getenv('MANAGED_MCP_ALLOW_ROOT_RUNTIME', 'false').lower() == 'true',
            unprivileged_uid=(
                int(os.environ['MANAGED_MCP_UNPRIVILEGED_UID']) if os.getenv('MANAGED_MCP_UNPRIVILEGED_UID') else None
            ),
            unprivileged_gid=(
                int(os.environ['MANAGED_MCP_UNPRIVILEGED_GID']) if os.getenv('MANAGED_MCP_UNPRIVILEGED_GID') else None
            ),
        )

    def load_privilege_policy(self) -> dict:
        if not self.privilege_policy_path:
            return {'schema_version': 1, 'servers': {}}
        data = json.loads(self.privilege_policy_path.read_text(encoding='utf-8'))
        if data.get('schema_version') != 1 or not isinstance(data.get('servers'), dict):
            raise RuntimeError('invalid managed MCP privilege policy')
        return data
