"""Action interpreter: turn raw LLM text into a validated AgentTurn.

LLMs don't always follow the format exactly, so the parser is strict about
*meaning* but forgiving about *shape*: it strips markdown fences, finds the
first balanced JSON object in surrounding prose, and accepts common aliases
("arguments" for "args", "name" for "tool", "final" for "reply"...).
Anything still wrong raises ParseError, whose message is sent back to the model
so it can correct itself.
"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

MAX_ACTIONS_PER_TURN = 5


class ParseError(Exception):
    pass


class ActionCall(BaseModel):
    model_config = ConfigDict(extra="ignore")
    tool: str = Field(min_length=1)
    args: dict[str, Any] = Field(default_factory=dict)

    @field_validator("args", mode="before")
    @classmethod
    def _args_from_string(cls, v: Any) -> Any:
        if v is None:
            return {}
        if isinstance(v, str):  # some models double-encode the arguments
            try:
                return json.loads(v) if v.strip() else {}
            except json.JSONDecodeError:
                raise ValueError("args must be a JSON object") from None
        return v


class AgentTurn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    thought: str = ""
    actions: list[ActionCall] = Field(default_factory=list)
    done: bool = False
    reply: str | None = None


def _balanced_object(text: str, start: int) -> str | None:
    depth, in_string, escape = 0, False, False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _scan(text: str) -> Any:
    start = text.find("{")
    while start != -1:
        candidate = _balanced_object(text, start)
        if candidate:
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                pass
        start = text.find("{", start + 1)
    return None


def extract_json(text: str) -> dict:
    cleaned = (text or "").strip()
    obj: Any = None
    try:
        obj = json.loads(cleaned)  # the normal case: the whole reply is the JSON object
    except json.JSONDecodeError:
        # Only now look for a ```json fence: a valid reply may itself contain ``` inside a string.
        fence = re.match(r"^\s*```(?:json)?\s*(.*?)```\s*$", cleaned, re.S | re.I)
        if fence:
            try:
                obj = json.loads(fence.group(1))
            except json.JSONDecodeError:
                obj = None
        if obj is None:
            obj = _scan(cleaned)
        if obj is None:
            raise ParseError("Your reply did not contain a valid JSON object.") from None
    if not isinstance(obj, dict):
        raise ParseError("The top level of your reply must be a JSON object, not a list or a value.")
    return obj


def _normalise(obj: dict) -> dict:
    if "actions" not in obj:
        for key in ("action", "tool_calls", "calls"):
            value = obj.get(key)
            if isinstance(value, dict):
                obj["actions"] = [value]
                break
            if isinstance(value, list):
                obj["actions"] = value
                break
        else:
            if isinstance(obj.get("tool"), str):
                obj["actions"] = [{"tool": obj["tool"], "args": obj.get("args", {})}]
    if obj.get("actions") is None:
        obj["actions"] = []
    if obj.get("reply") is None:
        for key in ("final", "response", "message", "answer", "final_answer"):
            if isinstance(obj.get(key), str):
                obj["reply"] = obj[key]
                break
    if isinstance(obj.get("actions"), list):
        for action in obj["actions"]:
            if not isinstance(action, dict):
                continue
            if "tool" not in action:
                for key in ("name", "function", "tool_name"):
                    if isinstance(action.get(key), str):
                        action["tool"] = action[key]
                        break
            if "args" not in action:
                for key in ("arguments", "parameters", "input"):
                    if key in action:
                        action["args"] = action[key]
                        break
    if isinstance(obj.get("reply"), str) and not obj["reply"].strip():
        obj["reply"] = None
    return obj


def parse_turn(text: str) -> AgentTurn:
    obj = _normalise(extract_json(text))
    try:
        turn = AgentTurn.model_validate(obj)
    except ValidationError as err:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in e['loc']) or 'reply'}: {e['msg']}" for e in err.errors()
        )
        raise ParseError(f"Your JSON didn't match the required format ({problems}).") from None
    if not turn.actions and not turn.reply:
        raise ParseError('Your reply had no actions and no "reply" text. Provide one or the other.')
    if len(turn.actions) > MAX_ACTIONS_PER_TURN:
        raise ParseError(f"Use at most {MAX_ACTIONS_PER_TURN} actions per reply.")
    return turn
