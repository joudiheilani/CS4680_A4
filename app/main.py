"""FastAPI web server: serves the UI and the agent's JSON API."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .agent import Agent, AgentBusy
from .audit import AuditLog
from .config import Settings
from .llm import ChatClient
from .mock_llm import MockLLM
from .prompts import tool_catalog
from .sessions import SESSION_ID, SessionStore
from .tools import ToolFailure
from .tools.files import safe_path
from .tools.tasks import TaskEdit, remove_task, update_task

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
COOKIE = "sp_session"

settings = Settings()
store = SessionStore(settings)
audit = AuditLog(settings.data_dir)
llm = MockLLM() if settings.mock else ChatClient(settings)
http = httpx.AsyncClient(headers={"User-Agent": "StudyPilot/1.0 (student project)"}, follow_redirects=True)
agent = Agent(llm, settings, audit, http)
run_slots = asyncio.Semaphore(settings.max_concurrent_runs)


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    await http.aclose()
    if isinstance(llm, ChatClient):
        await llm.aclose()


app = FastAPI(title="StudyPilot", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.middleware("http")
async def session_cookie(request: Request, call_next):
    sid = request.cookies.get(COOKIE, "")
    is_new = not SESSION_ID.match(sid)
    if is_new:
        sid = store.new_id()
    request.state.sid = sid
    response = await call_next(request)
    if is_new:
        secure = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
        response.set_cookie(COOKIE, sid, max_age=86_400, httponly=True, samesite="lax", secure=secure)
    return response


def _session(request: Request):
    return store.get(request.state.sid)


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"ok": False, "error": message}, status_code=status)


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=1500)


class ConfirmIn(BaseModel):
    pending_id: str = Field(min_length=1, max_length=40)
    approve: bool


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
async def health():
    return {"ok": True, "mock": settings.mock, "model": llm.name}


@app.get("/api/state")
async def state(request: Request):
    sess = _session(request)
    return {
        "ok": True,
        "model": llm.name,
        "mock": settings.mock,
        "workspace": agent.workspace(sess),
        "transcript": sess.transcript,
        "limits": {"per_minute": settings.rate_per_minute, "per_day": settings.rate_per_day},
    }


@app.post("/api/chat")
async def chat(body: ChatIn, request: Request):
    sess = _session(request)
    if sess.lock.locked():
        return _error(409, "I'm still working on your previous request.")
    async with sess.lock:
        limited = store.check_rate(sess, _client_ip(request))
        if limited:
            audit.record(sess.id, sess.log_path, "rate_limited", reason=limited)
            return _error(429, limited)
        try:
            async with run_slots:
                return await agent.start(sess, body.message.strip())
        except AgentBusy as exc:
            return _error(409, str(exc))


@app.post("/api/confirm")
async def confirm(body: ConfirmIn, request: Request):
    sess = _session(request)
    if sess.lock.locked():
        return _error(409, "I'm still working on your previous request.")
    async with sess.lock:
        try:
            async with run_slots:
                return await agent.confirm(sess, body.pending_id, body.approve)
        except AgentBusy as exc:
            return _error(409, str(exc))


@app.get("/api/file")
async def read_file(path: str, request: Request):
    sess = _session(request)
    try:
        target = safe_path(sess.files_dir, path)
    except ToolFailure as exc:
        return _error(400, str(exc))
    if not target.is_file():
        return _error(404, "File not found.")
    return PlainTextResponse(target.read_text(encoding="utf-8", errors="replace"))


@app.get("/api/log")
async def read_log(request: Request, download: bool = False):
    sess = _session(request)
    entries = AuditLog.read(sess.log_path)
    if download:
        return JSONResponse(entries, headers={"Content-Disposition": 'attachment; filename="studypilot-audit-log.json"'})
    return {"ok": True, "entries": entries}


@app.get("/api/tools")
async def tools():
    return PlainTextResponse(tool_catalog())


@app.patch("/api/tasks/{task_id}")
async def edit_task(task_id: int, body: TaskEdit, request: Request):
    """Let the user fix a task by hand (e.g. when the agent got a date wrong)."""
    sess = _session(request)
    if sess.lock.locked():
        return _error(409, "Wait until the agent finishes before editing tasks.")
    async with sess.lock:
        try:
            before, after = update_task(sess.db_path, task_id, body)
        except ToolFailure as exc:
            return _error(404 if "no task" in str(exc) else 400, str(exc))
        changes = {k: [before[k], after[k]] for k in after if before[k] != after[k]}
        audit.record(sess.id, sess.log_path, "user_edit", task_id=task_id, changes=changes)
    return {"ok": True, "task": after, "workspace": agent.workspace(sess)}


@app.delete("/api/tasks/{task_id}")
async def delete_task(task_id: int, request: Request):
    sess = _session(request)
    if sess.lock.locked():
        return _error(409, "Wait until the agent finishes before editing tasks.")
    async with sess.lock:
        try:
            row = remove_task(sess.db_path, task_id)
        except ToolFailure as exc:
            return _error(404, str(exc))
        audit.record(sess.id, sess.log_path, "user_delete", task_id=task_id, title=row["title"])
    return {"ok": True, "workspace": agent.workspace(sess)}


@app.post("/api/reset")
async def reset(request: Request):
    sess = _session(request)
    async with sess.lock:
        store.reset(sess)
        audit.record(sess.id, sess.log_path, "workspace_reset")
    return {"ok": True, "workspace": agent.workspace(sess)}
