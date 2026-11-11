"""System tools: a safe calculator and an allow-listed, sandboxed command runner."""

from __future__ import annotations

import ast
import asyncio
import fnmatch
import math
import operator
import os
import shlex
import shutil
import subprocess

from pydantic import BaseModel, ConfigDict, Field

from .base import ToolContext, ToolFailure, ToolResult, register
from .files import safe_path

# ----------------------------------------------------------------- calculator

_BIN_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow,
}
_UNARY_OPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_FUNCS = {
    "sqrt": math.sqrt, "sin": math.sin, "cos": math.cos, "tan": math.tan, "log": math.log,
    "log10": math.log10, "exp": math.exp, "abs": abs, "round": round, "min": min, "max": max,
    "floor": math.floor, "ceil": math.ceil,
}
_NAMES = {"pi": math.pi, "e": math.e}


def safe_eval(expression: str) -> float | int:
    """Evaluate arithmetic without eval(): only numbers, operators and a few math functions."""
    try:
        tree = ast.parse(expression.replace("^", "**"), mode="eval")
    except SyntaxError:
        raise ToolFailure(f"'{expression}' isn't a valid math expression.") from None

    def ev(node: ast.AST):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
            left, right = ev(node.left), ev(node.right)
            if isinstance(node.op, ast.Pow) and (abs(right) > 1000 or abs(left) > 1e6):
                raise ToolFailure("That exponent is too large.")
            try:
                return _BIN_OPS[type(node.op)](left, right)
            except ZeroDivisionError:
                raise ToolFailure("Division by zero.") from None
        if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
            return _UNARY_OPS[type(node.op)](ev(node.operand))
        if isinstance(node, ast.Name) and node.id in _NAMES:
            return _NAMES[node.id]
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in _FUNCS
            and not node.keywords
        ):
            try:
                return _FUNCS[node.func.id](*[ev(a) for a in node.args])
            except (ValueError, TypeError) as exc:
                raise ToolFailure(f"Math error: {exc}.") from None
        raise ToolFailure("Only numbers, + - * / // % ** and functions like sqrt(), log(), round() are allowed.")

    result = ev(tree)
    if isinstance(result, float) and result.is_integer() and abs(result) < 1e15:
        result = int(result)
    return result


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CalcArgs(_Args):
    expression: str = Field(min_length=1, max_length=200)


@register("calculate", "system", "Evaluate a math expression exactly, e.g. '(88*0.4)+(92*0.6)'.", CalcArgs)
async def calculate(ctx: ToolContext, args: CalcArgs) -> ToolResult:
    value = safe_eval(args.expression)
    return ToolResult(True, f"{args.expression} = {value}", {"expression": args.expression, "result": value})


# ------------------------------------------------------------ command runner

ALLOWED_COMMANDS = {
    "ls": "list files",
    "cat": "print a file",
    "wc": "count lines/words/characters",
    "head": "first lines of a file",
    "tail": "last lines of a file",
    "grep": "search text in files",
    "sort": "sort lines",
    "uniq": "remove duplicate lines",
    "date": "current date and time",
    "echo": "print text",
}
# Short flags that could write files, read outside the sandbox or change the system.
BLOCKED_FLAGS = {"sort": set("oT"), "date": set("sf"), "grep": set("f"), "tail": set("f")}
FORBIDDEN_CHARS = set("|;&><`$(){}\n\r")
COMMAND_TIMEOUT = 5
OUTPUT_LIMIT = 3_000


class CommandArgs(_Args):
    command: str = Field(min_length=1, max_length=300)


def build_argv(command: str, files_dir) -> list[str]:
    """Validate a command line and return the argv to run. Raises ToolFailure on anything unsafe."""
    bad = sorted(FORBIDDEN_CHARS.intersection(command))
    if bad:
        shown = " ".join(repr(c) for c in bad)
        raise ToolFailure(f"Shell operators aren't allowed ({shown}). Run one simple command at a time.")
    try:
        argv = shlex.split(command)
    except ValueError as exc:
        raise ToolFailure(f"Couldn't parse the command: {exc}.") from None
    if not argv:
        raise ToolFailure("The command is empty.")

    program, rest = argv[0], argv[1:]
    if program not in ALLOWED_COMMANDS:
        allowed = ", ".join(sorted(ALLOWED_COMMANDS))
        raise ToolFailure(f"'{program}' isn't on the allowlist. Allowed commands: {allowed}.")

    out = [program]
    blocked = BLOCKED_FLAGS.get(program, set())
    for arg in rest:
        if arg.startswith("-") and arg != "-":
            if arg.startswith("--") or "=" in arg:
                raise ToolFailure(f"Long options like '{arg}' aren't allowed; use short flags such as -l or -n.")
            letters = arg[1:]
            if not letters.isalnum():
                raise ToolFailure(f"'{arg}' isn't a valid flag.")
            hit = blocked.intersection(letters)
            if hit:
                raise ToolFailure(f"The -{''.join(sorted(hit))} flag isn't allowed for {program}.")
            out.append(arg)
            continue
        if program == "echo":
            out.append(arg)
            continue
        if any(ch in arg for ch in "*?["):
            # Expand globs ourselves, inside the workspace only (there is no shell).
            safe_path(files_dir, arg.replace("*", "x").replace("?", "x").replace("[", "x").replace("]", "x"))
            matches = sorted(
                p.relative_to(files_dir).as_posix()
                for p in files_dir.rglob("*")
                if p.is_file() and fnmatch.fnmatch(p.relative_to(files_dir).as_posix(), arg)
            )
            out.extend(matches or [arg])
            continue
        if "/" in arg or "\\" in arg or arg.startswith(("~", ".")):
            if arg not in (".", "./"):
                safe_path(files_dir, arg)  # raises if it escapes the workspace
        out.append(arg)
    return out


def _precheck(ctx: ToolContext, args: CommandArgs) -> None:
    """Validate before execution so unsafe commands show up as blocked, not as failed runs."""
    build_argv(args.command, ctx.files_dir)
    return None


@register(
    "run_command",
    "system",
    "Run ONE read-only shell command inside the workspace folder. Allowed: "
    + ", ".join(f"{k} ({v})" for k, v in ALLOWED_COMMANDS.items())
    + ". No pipes, redirects or long --options.",
    CommandArgs,
    confirm=_precheck,
)
async def run_command(ctx: ToolContext, args: CommandArgs) -> ToolResult:
    argv = build_argv(args.command, ctx.files_dir)
    exe = shutil.which(argv[0])
    if exe is None:
        raise ToolFailure(f"'{argv[0]}' isn't installed on this server.")
    env = {"PATH": os.environ.get("PATH", ""), "LC_ALL": "C.UTF-8", "TZ": os.environ.get("TZ", "UTC")}
    if os.name == "nt":
        env["SYSTEMROOT"] = os.environ.get("SYSTEMROOT", "")
    try:
        proc = await asyncio.to_thread(
            subprocess.run,
            [exe, *argv[1:]],
            cwd=ctx.files_dir,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=COMMAND_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        raise ToolFailure(f"The command took longer than {COMMAND_TIMEOUT}s and was stopped.") from None

    stdout = proc.stdout.decode("utf-8", errors="replace")
    stderr = proc.stderr.decode("utf-8", errors="replace")
    truncated = len(stdout) > OUTPUT_LIMIT
    data = {
        "command": " ".join(shlex.quote(a) for a in argv),
        "exit_code": proc.returncode,
        "stdout": stdout[:OUTPUT_LIMIT],
        "stderr": stderr[:500],
        "truncated": truncated,
    }
    if proc.returncode != 0 and not stdout:
        raise ToolFailure(f"'{data['command']}' exited with code {proc.returncode}: {stderr.strip()[:300]}")
    lines = stdout.count("\n")
    return ToolResult(True, f"Ran `{data['command']}` (exit {proc.returncode}, {lines} line(s) of output).", data)
