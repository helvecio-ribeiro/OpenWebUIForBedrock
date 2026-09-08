import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.server import Settings
from stdio_server import run_stdio

# MCP 1.27.x plus pydantic-settings 2.15 leaves this forward reference
# unresolved. Rebuilding prevents broken settings inspection at startup.
Settings.model_rebuild()

PACKAGE_DIR = Path(__file__).resolve().parent
ROOT = Path(os.getenv("MCP_FILESYSTEM_ROOT", "/home/helvecio"))
if not ROOT.is_absolute():
    ROOT = PACKAGE_DIR / ROOT
ROOT = ROOT.resolve()
ROOT.mkdir(parents=True, exist_ok=True)
MAX_READ_BYTES = int(os.getenv("MCP_FILESYSTEM_MAX_READ_BYTES", "1048576"))
READ_ONLY = os.getenv("OPEN_WEBUI_MCP_READ_ONLY", "false").lower() == "true"
LOCAL_TIMEZONE_NAME = os.getenv("MCP_LOCAL_TIMEZONE", "America/Mexico_City")
try:
    LOCAL_TIMEZONE = ZoneInfo(LOCAL_TIMEZONE_NAME)
except ZoneInfoNotFoundError as exc:
    raise RuntimeError(f"Unknown MCP_LOCAL_TIMEZONE: {LOCAL_TIMEZONE_NAME}") from exc

mcp = FastMCP(
    "Local System Tools",
    instructions=(
        "Provide the current local date and time, and read or write files only within "
        "the configured home-directory root. Use get_current_datetime before resolving "
        "relative date phrases such as today, tomorrow, or next Friday."
    ),
)


def _resolve(relative_path: str, *, must_exist: bool = False) -> Path:
    if not relative_path:
        relative_path = "."
    requested = Path(relative_path)
    if requested.is_absolute():
        raise ValueError("absolute paths are not allowed")
    candidate = (ROOT / requested).resolve(strict=must_exist)
    if candidate != ROOT and ROOT not in candidate.parents:
        raise ValueError("path escapes the configured filesystem root")
    return candidate


@mcp.tool()
def filesystem_info() -> dict[str, Any]:
    """Return the configured root and safety limits for this server."""
    return {"root": str(ROOT), "max_read_bytes": MAX_READ_BYTES, "read_only": READ_ONLY}


@mcp.tool()
def get_current_datetime() -> dict[str, Any]:
    """Return the current local and UTC date/time for resolving relative date references."""
    local_now = datetime.now(LOCAL_TIMEZONE)
    utc_now = local_now.astimezone(timezone.utc)
    return {
        "local_datetime": local_now.isoformat(timespec="seconds"),
        "utc_datetime": utc_now.isoformat(timespec="seconds"),
        "date": local_now.date().isoformat(),
        "time": local_now.time().isoformat(timespec="seconds"),
        "weekday": local_now.strftime("%A"),
        "timezone": LOCAL_TIMEZONE_NAME,
        "utc_offset": local_now.strftime("%z")[:3] + ":" + local_now.strftime("%z")[3:],
        "unix_timestamp": int(local_now.timestamp()),
    }


@mcp.tool()
def list_directory(path: str = ".") -> list[dict[str, Any]]:
    """List the immediate contents of a directory inside the configured root."""
    directory = _resolve(path, must_exist=True)
    if not directory.is_dir():
        raise ValueError("path is not a directory")
    entries = []
    for entry in sorted(directory.iterdir(), key=lambda item: item.name.lower()):
        resolved = entry.resolve()
        safe = resolved == ROOT or ROOT in resolved.parents
        entries.append(
            {
                "name": entry.name,
                "path": str(entry.relative_to(ROOT)),
                "type": "directory" if entry.is_dir() else "file",
                "size": entry.stat().st_size if entry.is_file() and safe else None,
                "safe": safe,
            }
        )
    return entries


@mcp.tool()
def read_text_file(path: str) -> str:
    """Read a UTF-8 text file inside the configured root, subject to the size limit."""
    file_path = _resolve(path, must_exist=True)
    if not file_path.is_file():
        raise ValueError("path is not a file")
    size = file_path.stat().st_size
    if size > MAX_READ_BYTES:
        raise ValueError(f"file exceeds the {MAX_READ_BYTES}-byte read limit")
    try:
        return file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("file is not valid UTF-8 text") from exc


@mcp.tool()
def write_text_file(path: str, content: str, overwrite: bool = False) -> dict[str, Any]:
    """Write a UTF-8 file inside the root; existing files require overwrite=true."""
    if READ_ONLY:
        raise PermissionError("filesystem server is configured read-only")
    file_path = _resolve(path)
    if file_path.exists() and not overwrite:
        raise ValueError("file already exists; set overwrite=true to replace it")
    if file_path.exists() and not file_path.is_file():
        raise ValueError("path is not a file")
    file_path.parent.mkdir(parents=True, exist_ok=True)
    # Re-resolve after creating parents to catch symlinks introduced in the path.
    file_path = _resolve(path)
    file_path.write_text(content, encoding="utf-8")
    return {"path": str(file_path.relative_to(ROOT)), "bytes": len(content.encode("utf-8"))}


@mcp.tool()
def create_directory(path: str) -> dict[str, Any]:
    """Create a directory and missing parents inside the configured root."""
    if READ_ONLY:
        raise PermissionError("filesystem server is configured read-only")
    directory = _resolve(path)
    directory.mkdir(parents=True, exist_ok=True)
    directory = _resolve(path, must_exist=True)
    return {"path": str(directory.relative_to(ROOT))}


if __name__ == "__main__":
    print(f"Local timezone: {LOCAL_TIMEZONE_NAME}; filesystem root: {ROOT}", file=sys.stderr)
    run_stdio(mcp)
