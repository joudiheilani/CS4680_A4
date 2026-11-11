"""Terminal interface for StudyPilot: same agent, same tools, same safety checks.

    python cli.py
"""

from __future__ import annotations

import asyncio
import json
import sys

import httpx

from app.agent import Agent
from app.audit import AuditLog
from app.config import Settings
from app.llm import ChatClient
from app.mock_llm import MockLLM
from app.sessions import SessionStore

COLORS = {"dim": "\033[2m", "red": "\033[31m", "green": "\033[32m", "yellow": "\033[33m", "cyan": "\033[36m", "bold": "\033[1m"}
RESET = "\033[0m"
ICONS = {"success": "✓", "error": "✕", "blocked": "⛔", "pending": "⏸", "approved": "✓", "rejected": "–"}
STATUS_COLOR = {"success": "green", "approved": "green", "error": "red", "blocked": "red", "pending": "yellow", "rejected": "dim"}

if sys.platform == "win32":
    import os

    os.system("")  # enables ANSI colours in the Windows console
    sys.stdout.reconfigure(encoding="utf-8")


def c(color: str, text: str) -> str:
    return f"{COLORS[color]}{text}{RESET}"


def show(res: dict) -> None:
    for ev in res.get("events", []):
        if ev["type"] == "thought":
            print(c("dim", f"  plan: {ev['text']}"))
        elif ev["type"] == "notice":
            print(c("yellow", f"  ! {ev['text']}"))
        elif ev["type"] == "error":
            print(c("red", f"  error: {ev['text']}"))
        elif ev["type"] == "action":
            status = ev["status"]
            args = json.dumps(ev.get("args", {}), ensure_ascii=False)
            if len(args) > 80:
                args = args[:77] + "...}"
            print(f"  {c(STATUS_COLOR.get(status, 'dim'), ICONS.get(status, '?'))} [{ev['domain']}] {c('bold', ev['tool'])} {c('dim', args)}")
            if status != "pending":
                print(f"      {ev.get('summary', '')}")
    if res.get("reply"):
        print("\n" + c("cyan", "StudyPilot: ") + res["reply"] + "\n")


async def main() -> None:
    settings = Settings()
    store = SessionStore(settings)
    audit = AuditLog(settings.data_dir)
    llm = MockLLM() if settings.mock else ChatClient(settings)
    async with httpx.AsyncClient(headers={"User-Agent": "StudyPilot/1.0 (student project)"}) as http:
        agent = Agent(llm, settings, audit, http)
        session = store.get("c" * 32)  # one fixed local workspace: data/sessions/ccc.../files
        print(c("bold", "StudyPilot (terminal)") + c("dim", f"  model: {llm.name}  workspace: {session.files_dir}"))
        print(c("dim", "Type a request, or 'quit' to exit.\n"))
        while True:
            try:
                text = input(c("bold", "you> ")).strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if text.lower() in ("quit", "exit", "q"):
                break
            if not text:
                continue
            res = await agent.start(session, text)
            show(res)
            while res.get("pending"):
                pending = res["pending"]
                answer = input(c("yellow", f"  Approve? {pending['reason']} [y/N] ")).strip().lower()
                res = await agent.confirm(session, pending["id"], answer in ("y", "yes"))
                show(res)
    if isinstance(llm, ChatClient):
        await llm.aclose()


if __name__ == "__main__":
    asyncio.run(main())
