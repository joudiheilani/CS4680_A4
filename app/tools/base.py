"""Tool registry: the only operations the agent is allowed to execute.

Every tool declares a pydantic model for its arguments (``extra="forbid"`` so
hallucinated arguments are rejected), the domain it belongs to, and whether it
needs the user's confirmation before running.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx
from pydantic import BaseModel, ValidationError


class ToolFailure(Exception):
    """An expected failure (bad path, missing file...). The message is shown to the LLM and the user."""


class ValidationFailure(Exception):
    """The LLM asked for something that doesn't pass validation, so it is never executed."""


@dataclass
class ToolContext:
    files_dir: Path
    db_path: Path
    http: httpx.AsyncClient | None = None


@dataclass
class ToolResult:
    ok: bool
    summary: str
    data: Any = None


ToolFunc = Callable[[ToolContext, Any], Awaitable[ToolResult]]
ConfirmCheck = Callable[[ToolContext, Any], "str | None"]


@dataclass
class Tool:
    name: str
    domain: str
    description: str
    args_model: type[BaseModel]
    func: ToolFunc
    confirm: ConfirmCheck | None = None
    always_confirm: bool = False
    confirm_note: str = ""  # shown to the LLM in the tool catalog

    def needs_confirmation(self, ctx: ToolContext, args: BaseModel) -> str | None:
        """Return a human-readable reason when this call must be approved first.

        May raise ToolFailure when the call is invalid (e.g. a path outside the workspace),
        so the user is never asked to approve something that would fail anyway.
        """
        if self.confirm:
            reason = self.confirm(ctx, args)
            if reason:
                return reason
        if self.always_confirm:
            return f"{self.name} is a destructive action."
        return None

    def signature(self) -> str:
        schema = self.args_model.model_json_schema()
        props: dict = schema.get("properties", {})
        required = set(schema.get("required", []))
        parts = []
        for arg, spec in props.items():
            text = f"{arg}: {_type_name(spec)}"
            if arg not in required:
                text += f" = {json.dumps(spec.get('default'))}"
            parts.append(text)
        return f"{self.name}({', '.join(parts)})"


_TYPES = {"string": "str", "integer": "int", "number": "float", "boolean": "bool", "array": "list", "object": "dict"}


def _type_name(spec: dict) -> str:
    if "enum" in spec:
        return "|".join(json.dumps(v) for v in spec["enum"])
    if "anyOf" in spec:
        names = [_type_name(s) for s in spec["anyOf"] if s.get("type") != "null"]
        return "|".join(names) + "|null"
    return _TYPES.get(spec.get("type", ""), "any")


REGISTRY: dict[str, Tool] = {}

DOMAIN_LABELS = {
    "files": "File operations",
    "database": "Database (SQLite)",
    "web": "External APIs",
    "system": "System commands",
}


def register(
    name: str,
    domain: str,
    description: str,
    args_model: type[BaseModel],
    *,
    confirm: ConfirmCheck | None = None,
    always_confirm: bool = False,
    confirm_note: str = "",
) -> Callable[[ToolFunc], ToolFunc]:
    if always_confirm and not confirm_note:
        confirm_note = "asks the user to confirm"

    def decorator(func: ToolFunc) -> ToolFunc:
        REGISTRY[name] = Tool(name, domain, description, args_model, func, confirm, always_confirm, confirm_note)
        return func

    return decorator


def format_validation_error(err: ValidationError) -> str:
    problems = []
    for e in err.errors():
        loc = ".".join(str(p) for p in e.get("loc", ())) or "args"
        msg = e.get("msg", "invalid")
        if e.get("type") == "extra_forbidden":
            msg = "is not a valid argument"
        problems.append(f"'{loc}' {msg}")
    return "; ".join(problems)


def validate_action(name: str, args: dict) -> tuple[Tool, BaseModel]:
    tool = REGISTRY.get(name)
    if tool is None:
        raise ValidationFailure(f"Unknown tool '{name}'. Use one of: {', '.join(sorted(REGISTRY))}.")
    if not isinstance(args, dict):
        raise ValidationFailure(f"Arguments for {name} must be a JSON object.")
    try:
        parsed = tool.args_model.model_validate(args)
    except ValidationError as err:
        raise ValidationFailure(
            f"Invalid arguments for {name}: {format_validation_error(err)}. Expected {tool.signature()}."
        ) from None
    return tool, parsed
