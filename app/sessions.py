"""Per-visitor sandboxes: each browser session gets its own folder, SQLite DB and audit log."""

from __future__ import annotations

import asyncio
import re
import secrets
import shutil
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import Settings
from .samples import SAMPLE_FILES, sample_tasks
from .tools.tasks import complete_seed_task, insert_task

SESSION_ID = re.compile(r"^[a-f0-9]{32}$")

WELCOME = """# Welcome to StudyPilot

StudyPilot is an AI agent. It reads your request, plans the steps, and then
actually does them in this workspace: files, a to-do database, web lookups
and safe shell commands.

## Things to try
- "Read my CS4680 notes and make a study task for each topic, due before the exam"
- "What's the weather in Pomona tomorrow? Should I bike to class?"
- "Make a study schedule for this week and save it as schedule.md"
- "My scores are 88 on the midterm (40%) and 92 on projects (60%). What's my grade?"
- "Run wc -w on all my markdown files"
- "Delete welcome.md" (you'll be asked to confirm)

Everything here is private to your browser session and is cleared after 24 hours.
"""

NOTES = """# CS4680 Prompt Engineering: lecture notes

## Zero-shot vs few-shot
- Zero-shot: just the instruction. Fast, but the format can drift.
- Few-shot: include 2-5 worked examples. Locks in tone and output format.

## Chain-of-thought
- Ask the model to reason step by step before answering.
- Helps with math and multi-step logic; costs more tokens.

## ReAct (Reason + Act)
- Loop: thought -> action -> observation -> thought ...
- Lets an LLM use tools and react to real results instead of guessing.

## Structured output
- Ask for JSON that matches a schema, then validate it in code.
- Never execute model output without validating it first.

## Prompt injection
- Text from tools or documents can contain instructions. Treat it as data.

Exam: Thursday Nov 19, covers everything above.
"""


@dataclass
class Session:
    id: str
    root: Path
    history: list[dict] = field(default_factory=list)
    transcript: list[dict] = field(default_factory=list)
    run: Any = None  # paused agent run waiting for a confirmation
    last_seen: float = field(default_factory=time.time)
    minute_hits: deque = field(default_factory=deque)
    day_hits: deque = field(default_factory=deque)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def files_dir(self) -> Path:
        return self.root / "files"

    @property
    def db_path(self) -> Path:
        return self.root / "tasks.db"

    @property
    def log_path(self) -> Path:
        return self.root / "audit.jsonl"


def _hit(window: deque, limit: int, seconds: float, now: float) -> bool:
    while window and now - window[0] > seconds:
        window.popleft()
    if len(window) >= limit:
        return False
    window.append(now)
    return True


class SessionStore:
    def __init__(self, settings: Settings):
        self.s = settings
        self.base = settings.data_dir / "sessions"
        self.base.mkdir(parents=True, exist_ok=True)
        self._sessions: dict[str, Session] = {}
        self._ip_minute: dict[str, deque] = {}
        self._ip_day: dict[str, deque] = {}
        self._last_sweep = 0.0

    @staticmethod
    def new_id() -> str:
        return secrets.token_hex(16)

    def get(self, session_id: str) -> Session:
        if not SESSION_ID.match(session_id or ""):
            raise ValueError("invalid session id")
        self._sweep()
        sess = self._sessions.get(session_id)
        if sess is None:
            sess = Session(id=session_id, root=self.base / session_id)
            if not sess.files_dir.exists():
                self.seed(sess)
            self._sessions[session_id] = sess
        sess.last_seen = time.time()
        return sess

    def seed(self, sess: Session) -> None:
        sess.files_dir.mkdir(parents=True, exist_ok=True)
        (sess.files_dir / "welcome.md").write_text(WELCOME, encoding="utf-8")
        (sess.files_dir / "cs4680_notes.md").write_text(NOTES, encoding="utf-8")
        for rel, text in SAMPLE_FILES.items():
            path = sess.files_dir / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        for task in sample_tasks():
            row = insert_task(sess.db_path, task["title"], task["due"], task["priority"])
            if task.get("done"):
                complete_seed_task(sess.db_path, row["id"])

    def reset(self, sess: Session) -> None:
        shutil.rmtree(sess.root, ignore_errors=True)
        sess.history.clear()
        sess.transcript.clear()
        sess.run = None
        self.seed(sess)

    def check_rate(self, sess: Session, ip: str) -> str | None:
        now = time.time()
        if not _hit(sess.minute_hits, self.s.rate_per_minute, 60, now):
            return f"Slow down a little: at most {self.s.rate_per_minute} requests per minute (this protects the free LLM quota)."
        if not _hit(sess.day_hits, self.s.rate_per_day, 86_400, now):
            return f"Daily limit reached ({self.s.rate_per_day} requests). Please come back tomorrow."
        ip_min = self._ip_minute.setdefault(ip, deque())
        ip_day = self._ip_day.setdefault(ip, deque())
        if not _hit(ip_min, self.s.rate_per_minute * 3, 60, now):
            return "Too many requests from your network. Please wait a minute."
        if not _hit(ip_day, self.s.rate_per_day * 3, 86_400, now):
            return "Daily limit for your network reached. Please come back tomorrow."
        return None

    def _sweep(self) -> None:
        """Delete sandboxes that have been idle longer than the TTL (at most once every 10 minutes)."""
        now = time.time()
        if now - self._last_sweep < 600:
            return
        self._last_sweep = now
        ttl = self.s.session_ttl_hours * 3600
        for sid, sess in list(self._sessions.items()):
            if now - sess.last_seen > ttl and not sess.lock.locked():
                del self._sessions[sid]
        for folder in self.base.iterdir():
            if folder.name in self._sessions or not folder.is_dir():
                continue
            try:
                log = folder / "audit.jsonl"
                touched = max(folder.stat().st_mtime, log.stat().st_mtime if log.exists() else 0)
                if now - touched > ttl:
                    shutil.rmtree(folder, ignore_errors=True)
            except OSError:
                pass
