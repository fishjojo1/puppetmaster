from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

TERMINAL_STATUSES = {"completed", "failed", "blocked", "stopped", "killed", "dead"}
VALID_STATUSES = {
    "starting",
    "running",
    "idle",
    "awaiting_input",
    "completed",
    "failed",
    "blocked",
    "stopped",
    "killed",
    "dead",
    "unknown",
}
COMPLETION_STATUSES = {"success", "failed", "blocked", "cancelled"}


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def json_dumps(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def json_loads(value: str | None) -> object:
    if not value:
        return {}
    return json.loads(value)


def validate_cwd(cwd: str) -> str:
    path = Path(cwd).expanduser()
    if not path.is_absolute():
        from .errors import PuppetError

        raise PuppetError("invalid_cwd", f"cwd must be absolute: {cwd}", "Pass an existing absolute directory.")
    if not path.exists():
        from .errors import PuppetError

        raise PuppetError("invalid_cwd", f"cwd does not exist: {cwd}", "Create the directory or pass a valid cwd.")
    if not path.is_dir():
        from .errors import PuppetError

        raise PuppetError("invalid_cwd", f"cwd is not a directory: {cwd}", "Pass a directory, not a file.")
    return str(path.resolve())

