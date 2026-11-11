"""Audit log: every request, LLM call, action and confirmation is appended as one JSON line."""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger("studypilot.audit")

_MAX_STR = 300


def _shorten(value: Any) -> Any:
    if isinstance(value, str):
        return value if len(value) <= _MAX_STR else value[:_MAX_STR] + f"... ({len(value)} chars)"
    if isinstance(value, dict):
        return {k: _shorten(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_shorten(v) for v in value[:20]]
    return value


class AuditLog:
    def __init__(self, data_dir: Path):
        self.global_path = Path(data_dir) / "audit.jsonl"
        self.global_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def record(self, session_id: str, session_log: Path | None, kind: str, **fields: Any) -> dict:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "session": session_id[:8],
            "kind": kind,
            **_shorten(fields),
        }
        line = json.dumps(entry, ensure_ascii=False, default=str)
        with self._lock:
            for path in (self.global_path, session_log):
                if path is None:
                    continue
                try:
                    with path.open("a", encoding="utf-8") as fh:
                        fh.write(line + "\n")
                except OSError:
                    log.exception("Couldn't write audit log %s", path)
        log.info(line)
        return entry

    @staticmethod
    def read(path: Path, limit: int = 300) -> list[dict]:
        if not path.exists():
            return []
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]
        out = []
        for line in lines:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out
