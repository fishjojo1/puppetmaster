from __future__ import annotations

import json
from typing import Any

from .config import Config
from .model import now


def log(config: Config, level: str, event: str, message: str, **fields: Any) -> None:
    config.state_dir.mkdir(parents=True, exist_ok=True)
    record = {"ts": now(), "level": level, "event": event, "message": message}
    record.update({k: v for k, v in fields.items() if v is not None})
    with config.log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")

