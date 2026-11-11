"""Database tools: a per-visitor SQLite to-do list. All queries are parameterised."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .base import ToolContext, ToolFailure, ToolResult, register

MAX_TASKS = 200

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    due TEXT,
    priority TEXT NOT NULL DEFAULT 'medium',
    done INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    completed_at TEXT
)
"""


def connect(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.execute(_SCHEMA)
    return con


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _row(r: sqlite3.Row) -> dict:
    return {"id": r["id"], "title": r["title"], "due": r["due"], "priority": r["priority"], "done": bool(r["done"])}


def fetch_tasks(db_path: Path, status: str = "all") -> list[dict]:
    where = {"open": "WHERE done = 0", "done": "WHERE done = 1", "all": ""}[status]
    order = (
        "ORDER BY done, CASE WHEN due IS NULL THEN 1 ELSE 0 END, due, "
        "CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, id"
    )
    with closing(connect(db_path)) as con:
        return [_row(r) for r in con.execute(f"SELECT * FROM tasks {where} {order}")]


def insert_task(db_path: Path, title: str, due: str | None = None, priority: str = "medium") -> dict:
    with closing(connect(db_path)) as con:
        count = con.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        if count >= MAX_TASKS:
            raise ToolFailure(f"The task list is full ({MAX_TASKS} tasks).")
        cur = con.execute(
            "INSERT INTO tasks (title, due, priority, created_at) VALUES (?, ?, ?, ?)",
            (title, due, priority, _now()),
        )
        con.commit()
        row = con.execute("SELECT * FROM tasks WHERE id = ?", (cur.lastrowid,)).fetchone()
        return _row(row)


def _get(con: sqlite3.Connection, task_id: int) -> sqlite3.Row:
    row = con.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        raise ToolFailure(f"There is no task #{task_id}. List the tasks to see valid ids.")
    return row


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AddTaskArgs(_Args):
    title: str = Field(min_length=1, max_length=200)
    due: str | None = Field(default=None, description="YYYY-MM-DD")
    priority: Literal["low", "medium", "high"] = "medium"

    @field_validator("title")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("title can't be blank")
        return v

    @field_validator("due")
    @classmethod
    def _iso_date(cls, v: str | None) -> str | None:
        if v in (None, ""):
            return None
        try:
            return date.fromisoformat(v.strip()).isoformat()
        except ValueError:
            raise ValueError("must be a date in YYYY-MM-DD format") from None


class ListTasksArgs(_Args):
    status: Literal["open", "done", "all"] = "open"


class TaskIdArgs(_Args):
    task_id: int = Field(ge=1)


@register("add_task", "database", "Add a to-do task. due is YYYY-MM-DD (optional).", AddTaskArgs)
async def add_task(ctx: ToolContext, args: AddTaskArgs) -> ToolResult:
    task = insert_task(ctx.db_path, args.title, args.due, args.priority)
    due = f", due {task['due']}" if task["due"] else ""
    return ToolResult(True, f"Added task #{task['id']} \"{task['title']}\" ({task['priority']}{due}).", task)


@register("list_tasks", "database", "List tasks, sorted by due date then priority.", ListTasksArgs)
async def list_tasks(ctx: ToolContext, args: ListTasksArgs) -> ToolResult:
    tasks = fetch_tasks(ctx.db_path, args.status)
    return ToolResult(True, f"Found {len(tasks)} {args.status} task(s).", tasks)


@register("complete_task", "database", "Mark a task as done.", TaskIdArgs)
async def complete_task(ctx: ToolContext, args: TaskIdArgs) -> ToolResult:
    with closing(connect(ctx.db_path)) as con:
        row = _get(con, args.task_id)
        if row["done"]:
            raise ToolFailure(f"Task #{args.task_id} is already done.")
        con.execute("UPDATE tasks SET done = 1, completed_at = ? WHERE id = ?", (_now(), args.task_id))
        con.commit()
    return ToolResult(True, f"Completed task #{args.task_id} \"{row['title']}\".", {"id": args.task_id})


def _confirm_delete(ctx: ToolContext, args: TaskIdArgs) -> str:
    with closing(connect(ctx.db_path)) as con:
        row = _get(con, args.task_id)
    return f"Permanently deletes task #{args.task_id} \"{row['title']}\"."


@register(
    "delete_task", "database", "Delete a task permanently.", TaskIdArgs, confirm=_confirm_delete, always_confirm=True
)
async def delete_task(ctx: ToolContext, args: TaskIdArgs) -> ToolResult:
    with closing(connect(ctx.db_path)) as con:
        row = _get(con, args.task_id)
        con.execute("DELETE FROM tasks WHERE id = ?", (args.task_id,))
        con.commit()
    return ToolResult(True, f"Deleted task #{args.task_id} \"{row['title']}\".", {"id": args.task_id})
