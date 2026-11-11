"""Offline demo brain used when no API key is configured (and in tests).

It is NOT an LLM: a handful of keyword rules that emit the same JSON contract
the real model uses, so the whole parse -> validate -> confirm -> execute
pipeline can be exercised without network access or API cost.
"""

from __future__ import annotations

import json
import re

from .llm import LLMResult

HELP = (
    "I'm running in **demo mode** (no LLM API key is configured), so I only understand a few "
    "simple phrasings. Try:\n- list my files\n- read welcome.md\n- add task study for the exam\n"
    "- show my tasks\n- weather in Pomona\n- calculate 88*0.4 + 92*0.6\n- run wc -w welcome.md\n"
    "- delete welcome.md"
)


def _turn(thought: str, actions: list | None = None, done: bool = False, reply: str | None = None) -> str:
    return json.dumps({"thought": thought, "actions": actions or [], "done": done, "reply": reply})


def _plan(text: str) -> str:
    t = text.strip()
    low = t.lower()
    file_match = re.search(r"([\w\-./]+\.(?:md|txt|csv|json))", t)

    if m := re.search(r"weather (?:in|for|at) ([a-zA-Z .,'-]+)", low):
        city = m.group(1).strip(" .?!")
        return _turn(f"Look up the weather in {city}.", [{"tool": "get_weather", "args": {"city": city}}])
    if m := re.search(r"(?:calculate|compute|what is) (.+)", low):
        expr = m.group(1).strip(" ?")
        return _turn("Evaluate the expression.", [{"tool": "calculate", "args": {"expression": expr}}])
    if m := re.search(r"define (\w+)", low):
        return _turn("Look up the word.", [{"tool": "define_word", "args": {"word": m.group(1)}}])
    if m := re.search(r"^run (.+)", t, re.I):
        return _turn("Run the command.", [{"tool": "run_command", "args": {"command": m.group(1).strip()}}])
    if m := re.search(r"(?:add|create) (?:a )?task(?: to)?:? (.+)", t, re.I):
        title = m.group(1).strip(" .")
        return _turn(
            "Add the task.",
            [{"tool": "add_task", "args": {"title": title[:1].upper() + title[1:]}}],
            done=True,
            reply=f"Added the task **{title}**.",
        )
    if m := re.search(r"(?:complete|finish|done with) task #?(\d+)", low):
        return _turn("Complete the task.", [{"tool": "complete_task", "args": {"task_id": int(m.group(1))}}])
    if m := re.search(r"delete task #?(\d+)", low):
        return _turn("Delete the task (needs confirmation).", [{"tool": "delete_task", "args": {"task_id": int(m.group(1))}}])
    if "task" in low or "to-do" in low or "todo" in low:
        return _turn("List the open tasks.", [{"tool": "list_tasks", "args": {"status": "open"}}])
    if file_match and re.search(r"\b(delete|remove)\b", low):
        return _turn("Delete the file (needs confirmation).", [{"tool": "delete_file", "args": {"path": file_match.group(1)}}])
    if file_match and re.search(r"\b(read|open|show|summari[sz]e|what's in)\b", low):
        return _turn("Read the file.", [{"tool": "read_file", "args": {"path": file_match.group(1)}}])
    if file_match and re.search(r"\b(write|create|save|make)\b", low):
        return _turn(
            "Create the file.",
            [{"tool": "write_file", "args": {"path": file_match.group(1), "content": f"# {file_match.group(1)}\n\nCreated by StudyPilot.\n"}}],
        )
    if "file" in low:
        return _turn("List the workspace files.", [{"tool": "list_files", "args": {}}])
    return _turn("I don't recognise this in demo mode.", done=True, reply=HELP)


def _summarise(observations_text: str) -> str:
    try:
        payload = observations_text.split("\n", 1)[1].rsplit("\nRespond with", 1)[0]
        observations = json.loads(payload)
    except (IndexError, json.JSONDecodeError):
        return _turn("Done.", done=True, reply="Done.")
    lines = []
    for obs in observations:
        if obs.get("status") != "ok":
            lines.append(f"- {obs.get('tool')}: {obs.get('error') or obs.get('status')}")
            continue
        result = obs.get("result")
        tool = obs.get("tool")
        if tool == "read_file":
            content = result["content"]
            lines.append(f"Here's the start of **{result['path']}**:\n\n{content[:600]}")
        elif tool == "list_files":
            lines.append("Your files:\n" + "\n".join(f"- {f['path']} ({f['size']} bytes)" for f in result))
        elif tool == "list_tasks":
            lines.append(
                "Open tasks:\n" + "\n".join(f"- #{x['id']} {x['title']}" + (f" (due {x['due']})" if x["due"] else "") for x in result)
                if result
                else "You have no open tasks."
            )
        elif tool == "get_weather":
            now = result["now"]
            lines.append(f"It's {now['temperature']} and {now['conditions']} in {result['location']}.")
        elif tool == "calculate":
            lines.append(f"{result['expression']} = **{result['result']}**")
        elif tool == "run_command":
            lines.append(f"```\n{result['stdout'].strip()}\n```")
        else:
            lines.append(f"- {obs.get('summary') or tool + ' succeeded'}")
    return _turn("Report the results.", done=True, reply="\n".join(lines) or "Done.")


class MockLLM:
    name = "demo-mode (no LLM)"

    async def chat(self, messages: list[dict]) -> LLMResult:
        last = messages[-1]["content"]
        if last.startswith("TOOL RESULTS"):
            text = _summarise(last)
        elif last.startswith("FORMAT ERROR"):
            text = _turn("Recover.", done=True, reply="Sorry, something went wrong. Please rephrase.")
        else:
            user = last.split("User:", 1)[-1].strip()
            text = _plan(user)
        return LLMResult(text=text, model=self.name)


class ScriptedLLM:
    """Test double that returns pre-written replies in order and records what it was sent."""

    name = "scripted"

    def __init__(self, replies: list[str]):
        self.replies = list(replies)
        self.calls: list[list[dict]] = []

    async def chat(self, messages: list[dict]) -> LLMResult:
        self.calls.append([dict(m) for m in messages])
        if not self.replies:
            raise AssertionError("ScriptedLLM ran out of replies")
        return LLMResult(text=self.replies.pop(0), model=self.name, prompt_tokens=100, completion_tokens=20)
