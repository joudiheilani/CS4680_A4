"""File-operation tools. Everything is confined to the visitor's own workspace folder."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from .base import ToolContext, ToolFailure, ToolResult, register

ALLOWED_EXTENSIONS = {".md", ".txt", ".csv", ".json"}
MAX_FILE_CHARS = 50_000
MAX_FILES = 40
MAX_DEPTH = 3
READ_LIMIT = 4_000  # characters returned to the LLM (keeps us inside free-tier token limits)
_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.\-]{0,63}$")


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NoArgs(_Args):
    pass


class PathArgs(_Args):
    path: str = Field(min_length=1, max_length=200)


class WriteArgs(_Args):
    path: str = Field(min_length=1, max_length=200)
    content: str = Field(max_length=MAX_FILE_CHARS)


def safe_path(base: Path, rel: str) -> Path:
    """Resolve a workspace-relative path, refusing anything that could escape the sandbox."""
    rel = rel.strip().replace("\\", "/")
    if not rel:
        raise ToolFailure("The path is empty.")
    if rel.startswith(("/", "~")) or re.match(r"^[A-Za-z]:", rel):
        raise ToolFailure(f"'{rel}' is an absolute path. Use a path inside the workspace, like 'notes/week1.md'.")
    parts = [p for p in rel.split("/") if p not in ("", ".")]
    if ".." in parts:
        raise ToolFailure("'..' isn't allowed. Files must stay inside the workspace.")
    if not parts:
        raise ToolFailure("The path is empty.")
    if len(parts) > MAX_DEPTH:
        raise ToolFailure(f"Folders can be at most {MAX_DEPTH} levels deep.")
    for part in parts:
        if not _SEGMENT.match(part):
            raise ToolFailure(
                f"'{part}' isn't a valid file or folder name (letters, numbers, spaces, '-', '_', '.'; "
                "no leading dot)."
            )
    base = base.resolve()
    target = (base / Path(*parts)).resolve()
    if target != base and base not in target.parents:
        raise ToolFailure("That path points outside the workspace.")
    return target


def rel_name(base: Path, path: Path) -> str:
    return path.resolve().relative_to(base.resolve()).as_posix()


def _check_extension(path: Path) -> None:
    if path.suffix.lower() not in ALLOWED_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_EXTENSIONS))
        raise ToolFailure(f"Only text files are allowed ({allowed}); got '{path.name}'.")


def _file_count(base: Path) -> int:
    return sum(1 for p in base.rglob("*") if p.is_file())


def list_workspace(base: Path) -> list[dict]:
    files = []
    for p in sorted(base.rglob("*")):
        if p.is_file():
            stat = p.stat()
            files.append(
                {
                    "path": rel_name(base, p),
                    "size": stat.st_size,
                    "modified": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(timespec="seconds"),
                }
            )
    return files


@register("list_files", "files", "List every file in the workspace with its size.", NoArgs)
async def list_files(ctx: ToolContext, args: NoArgs) -> ToolResult:
    files = list_workspace(ctx.files_dir)
    return ToolResult(True, f"Found {len(files)} file(s).", files)


@register("read_file", "files", f"Read a text file (first {READ_LIMIT} characters).", PathArgs)
async def read_file(ctx: ToolContext, args: PathArgs) -> ToolResult:
    path = safe_path(ctx.files_dir, args.path)
    if not path.is_file():
        raise ToolFailure(f"'{args.path}' doesn't exist. Check the workspace file list.")
    text = path.read_text(encoding="utf-8", errors="replace")
    truncated = len(text) > READ_LIMIT
    content = text[:READ_LIMIT]
    name = rel_name(ctx.files_dir, path)
    summary = f"Read {name} ({len(text)} characters{', truncated' if truncated else ''})."
    return ToolResult(True, summary, {"path": name, "content": content, "truncated": truncated})


def _confirm_overwrite(ctx: ToolContext, args: WriteArgs) -> str | None:
    path = safe_path(ctx.files_dir, args.path)
    _check_extension(path)
    if path.exists():
        return f"'{rel_name(ctx.files_dir, path)}' already exists and will be overwritten."
    return None


@register(
    "write_file",
    "files",
    "Create a text file, or replace an existing one. Parent folders are created automatically.",
    WriteArgs,
    confirm=_confirm_overwrite,
    confirm_note="asks the user to confirm when overwriting",
)
async def write_file(ctx: ToolContext, args: WriteArgs) -> ToolResult:
    path = safe_path(ctx.files_dir, args.path)
    _check_extension(path)
    existed = path.exists()
    if not existed and _file_count(ctx.files_dir) >= MAX_FILES:
        raise ToolFailure(f"The workspace is full ({MAX_FILES} files). Delete something first.")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(args.content, encoding="utf-8")
    name = rel_name(ctx.files_dir, path)
    verb = "Overwrote" if existed else "Created"
    return ToolResult(True, f"{verb} {name} ({len(args.content)} characters).", {"path": name, "created": not existed})


@register("append_file", "files", "Add text to the end of a file (creates it if missing).", WriteArgs)
async def append_file(ctx: ToolContext, args: WriteArgs) -> ToolResult:
    path = safe_path(ctx.files_dir, args.path)
    _check_extension(path)
    existing = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
    if not path.exists() and _file_count(ctx.files_dir) >= MAX_FILES:
        raise ToolFailure(f"The workspace is full ({MAX_FILES} files). Delete something first.")
    if len(existing) + len(args.content) > MAX_FILE_CHARS:
        raise ToolFailure(f"That would make the file larger than {MAX_FILE_CHARS} characters.")
    path.parent.mkdir(parents=True, exist_ok=True)
    sep = "" if not existing or existing.endswith("\n") else "\n"
    path.write_text(existing + sep + args.content, encoding="utf-8")
    name = rel_name(ctx.files_dir, path)
    return ToolResult(True, f"Appended {len(args.content)} characters to {name}.", {"path": name})


def _confirm_delete(ctx: ToolContext, args: PathArgs) -> str | None:
    path = safe_path(ctx.files_dir, args.path)
    if not path.is_file():
        raise ToolFailure(f"'{args.path}' doesn't exist, so there is nothing to delete.")
    return f"Permanently deletes '{rel_name(ctx.files_dir, path)}'. This can't be undone."


@register("delete_file", "files", "Delete a file.", PathArgs, confirm=_confirm_delete, always_confirm=True)
async def delete_file(ctx: ToolContext, args: PathArgs) -> ToolResult:
    path = safe_path(ctx.files_dir, args.path)
    if not path.is_file():
        raise ToolFailure(f"'{args.path}' doesn't exist.")
    name = rel_name(ctx.files_dir, path)
    path.unlink()
    # Tidy up folders that became empty.
    parent = path.parent
    base = ctx.files_dir.resolve()
    while parent != base and parent.is_dir() and not any(parent.iterdir()):
        parent.rmdir()
        parent = parent.parent
    return ToolResult(True, f"Deleted {name}.", {"path": name})
