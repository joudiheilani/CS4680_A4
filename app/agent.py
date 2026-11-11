"""The agent loop: LLM output -> parse -> validate -> (confirm) -> execute -> observe -> repeat."""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from .audit import AuditLog
from .config import Settings
from .llm import ChatModel, LLMError
from .parser import ParseError, parse_turn
from .prompts import build_system_prompt, build_user_message, format_observations, repair_message
from .sessions import Session
from .tools import DOMAIN_LABELS, Tool, ToolContext, ToolFailure, ToolResult, ValidationFailure, validate_action
from .tools.files import list_workspace
from .tools.tasks import fetch_tasks

log = logging.getLogger("studypilot.agent")

MAX_REPAIRS = 2


class AgentBusy(Exception):
    pass


@dataclass
class Pending:
    id: str
    tool: str
    domain: str
    args: dict
    reason: str


@dataclass
class Run:
    user_text: str
    messages: list[dict]
    events: list[dict] = field(default_factory=list)
    sent: int = 0  # how many events the UI has already received
    step: int = 0
    queue: list[tuple[Tool, BaseModel]] = field(default_factory=list)
    observations: list[dict] = field(default_factory=list)
    turn_done: bool = False
    turn_reply: str | None = None
    repairs: int = 0
    pending: Pending | None = None
    actions_log: list[str] = field(default_factory=list)
    started: float = field(default_factory=time.monotonic)


def _short_args(args: dict) -> str:
    parts = []
    for k, v in args.items():
        s = json.dumps(v, ensure_ascii=False, default=str)
        parts.append(f"{k}={s[:40] + '...' if len(s) > 40 else s}")
    return ", ".join(parts)


class Agent:
    def __init__(self, llm: ChatModel, settings: Settings, audit: AuditLog, http=None):
        self.llm = llm
        self.s = settings
        self.audit = audit
        self.http = http
        self.system_prompt = build_system_prompt()

    # ----------------------------------------------------------- helpers

    def ctx(self, sess: Session) -> ToolContext:
        sess.files_dir.mkdir(parents=True, exist_ok=True)
        return ToolContext(files_dir=sess.files_dir, db_path=sess.db_path, http=self.http)

    def _audit(self, sess: Session, kind: str, **fields: Any) -> None:
        self.audit.record(sess.id, sess.log_path, kind, **fields)

    def workspace(self, sess: Session) -> dict:
        return {"files": list_workspace(sess.files_dir), "tasks": fetch_tasks(sess.db_path, "all")}

    # -------------------------------------------------------- public API

    async def start(self, sess: Session, text: str) -> dict:
        if sess.run is not None and sess.run.pending is not None:
            raise AgentBusy("Approve or reject the pending action first.")
        self._audit(sess, "user_request", text=text)
        files = list_workspace(sess.files_dir)
        open_tasks = len(fetch_tasks(sess.db_path, "open"))
        messages = [
            {"role": "system", "content": self.system_prompt},
            *sess.history[-self.s.history_messages :],
            {"role": "user", "content": build_user_message(text, files, open_tasks)},
        ]
        run = Run(user_text=text, messages=messages)
        sess.transcript.append({"role": "user", "text": text})
        return await self._drive(sess, run)

    async def confirm(self, sess: Session, pending_id: str, approve: bool) -> dict:
        run = sess.run
        if run is None or run.pending is None or run.pending.id != pending_id:
            raise AgentBusy("There is no pending action with that id (it may have been handled already).")
        pending, run.pending = run.pending, None
        tool, args = run.queue.pop(0)
        # A reply the model wrote before the user decided can't describe the outcome
        # (it tends to say "please confirm"), so always ask for a fresh final reply.
        run.turn_done = False
        self._audit(sess, "confirmation", tool=tool.name, args=pending.args, approved=approve)
        for event in run.events:  # update the card the UI already shows (and the saved transcript)
            if event.get("pending_id") == pending.id:
                event["status"] = "approved" if approve else "rejected"
        if approve:
            await self._execute(sess, run, tool, args, confirmed=True)
        else:
            run.observations.append(
                {"tool": tool.name, "status": "rejected_by_user",
                 "error": "The user declined this action. Do not retry it."}
            )
            run.actions_log.append(f"{tool.name}({_short_args(pending.args)}) -> rejected by user")
        return await self._drive(sess, run)

    # ---------------------------------------------------------- the loop

    async def _drive(self, sess: Session, run: Run) -> dict:
        try:
            return await self._loop(sess, run)
        except LLMError as exc:
            self._audit(sess, "llm_error", kind_=exc.kind, error=exc.message)
            run.events.append({"type": "error", "text": exc.message})
            return self._finish(sess, run, None, error=exc.message)
        except Exception as exc:  # never leave the session stuck on an unexpected bug
            log.exception("agent crashed")
            self._audit(sess, "agent_error", error=f"{type(exc).__name__}: {exc}")
            msg = "Something went wrong inside the agent. The error was logged; please try again."
            run.events.append({"type": "error", "text": msg})
            return self._finish(sess, run, None, error=msg)

    async def _loop(self, sess: Session, run: Run) -> dict:
        ctx = self.ctx(sess)
        while True:
            # 1. Execute queued actions, pausing for confirmation where required.
            while run.queue:
                tool, args = run.queue[0]
                raw = args.model_dump()
                try:
                    reason = tool.needs_confirmation(ctx, args)
                except ToolFailure as exc:
                    run.queue.pop(0)
                    self._blocked(sess, run, tool.name, raw, str(exc), domain=tool.domain)
                    continue
                if reason:
                    run.pending = Pending(uuid.uuid4().hex[:12], tool.name, tool.domain, raw, reason)
                    run.events.append(
                        {"type": "action", "tool": tool.name, "domain": tool.domain, "args": raw,
                         "status": "pending", "summary": reason, "pending_id": run.pending.id}
                    )
                    self._audit(sess, "confirmation_requested", tool=tool.name, args=raw, reason=reason)
                    sess.run = run
                    return self._respond(sess, run, reply=None)
                run.queue.pop(0)
                await self._execute(sess, run, tool, args)

            # 2. Feed results back. Finish early if the model already wrote the final reply.
            if run.observations:
                all_ok = all(o["status"] == "ok" for o in run.observations)
                run.messages.append({"role": "user", "content": format_observations(run.observations)})
                run.observations = []
                if run.turn_done and all_ok and run.turn_reply:
                    return self._finish(sess, run, run.turn_reply)
            run.turn_done, run.turn_reply = False, None

            # 3. Ask the LLM what to do next.
            if run.step >= self.s.max_steps:
                msg = (
                    f"I stopped after {self.s.max_steps} steps to stay within the free API limits. "
                    "Here's what I completed so far. Ask me to continue if needed."
                )
                return self._finish(sess, run, msg)
            run.step += 1
            result = await self.llm.chat(run.messages)
            for notice in result.notices:
                run.events.append({"type": "notice", "text": notice})
            self._audit(
                sess, "llm_call", model=result.model, step=run.step, attempts=result.attempts,
                latency_ms=result.latency_ms, prompt_tokens=result.prompt_tokens,
                completion_tokens=result.completion_tokens, output=result.text,
            )

            try:
                turn = parse_turn(result.text)
            except ParseError as exc:
                run.repairs += 1
                self._audit(sess, "parse_error", error=str(exc), output=result.text)
                if run.repairs > MAX_REPAIRS:
                    raise LLMError(
                        "The model kept replying in an invalid format, so I stopped to stay safe. Please rephrase."
                    ) from None
                run.events.append({"type": "notice", "text": f"Model output was invalid ({exc}). Asking it to fix it."})
                run.messages.append({"role": "assistant", "content": result.text})
                run.messages.append({"role": "user", "content": repair_message(str(exc))})
                continue

            run.messages.append({"role": "assistant", "content": result.text})
            if turn.thought:
                run.events.append({"type": "thought", "text": turn.thought})

            if not turn.actions:
                return self._finish(sess, run, turn.reply or "Done.")

            for call in turn.actions:
                try:
                    tool, args = validate_action(call.tool, call.args)
                except ValidationFailure as exc:
                    self._blocked(sess, run, call.tool, call.args, str(exc))
                    continue
                run.queue.append((tool, args))
            run.turn_done, run.turn_reply = turn.done, turn.reply

    async def _execute(self, sess: Session, run: Run, tool: Tool, args: BaseModel, confirmed: bool = False) -> None:
        raw = args.model_dump()
        started = time.monotonic()
        try:
            result = await tool.func(self.ctx(sess), args)
        except ToolFailure as exc:
            result = ToolResult(False, str(exc))
        except Exception as exc:
            log.exception("tool %s crashed", tool.name)
            result = ToolResult(False, f"{tool.name} failed unexpectedly ({type(exc).__name__}).")
        ms = int((time.monotonic() - started) * 1000)
        status = "success" if result.ok else "error"
        run.events.append(
            {"type": "action", "tool": tool.name, "domain": tool.domain, "args": raw, "status": status,
             "summary": result.summary, "result": result.data, "confirmed": confirmed, "ms": ms}
        )
        observation: dict[str, Any] = {"tool": tool.name, "status": "ok" if result.ok else "error"}
        if result.ok:
            observation["result"] = result.data if result.data is not None else result.summary
        else:
            observation["error"] = result.summary
        run.observations.append(observation)
        run.actions_log.append(f"{tool.name}({_short_args(raw)}) -> {status}")
        self._audit(sess, "action", tool=tool.name, domain=tool.domain, args=raw, status=status,
                    summary=result.summary, confirmed=confirmed, ms=ms)

    def _blocked(self, sess: Session, run: Run, tool: str, args: Any, error: str, domain: str | None = None) -> None:
        """Record an action that failed validation. It is reported to the LLM but never executed."""
        run.events.append(
            {"type": "action", "tool": tool, "domain": domain or "unknown",
             "args": args if isinstance(args, dict) else {}, "status": "blocked", "summary": error}
        )
        run.observations.append({"tool": tool, "status": "blocked", "error": error})
        run.actions_log.append(f"{tool} -> blocked")
        self._audit(sess, "action_blocked", tool=tool, args=args, error=error)

    # ------------------------------------------------------- responses

    def _respond(self, sess: Session, run: Run, reply: str | None, error: str | None = None) -> dict:
        new_events = run.events[run.sent :]
        run.sent = len(run.events)
        pending = run.pending.__dict__ if run.pending else None
        entry = {"role": "agent", "events": new_events, "reply": reply, "error": error, "pending": pending}
        for item in sess.transcript:  # only the newest entry may still be waiting for a decision
            item["pending"] = None
        sess.transcript.append(entry)
        sess.transcript[:] = sess.transcript[-60:]
        return {**entry, "ok": error is None, "workspace": self.workspace(sess), "model": self.llm.name}

    def _finish(self, sess: Session, run: Run, reply: str | None, error: str | None = None) -> dict:
        sess.run = None
        run.pending = None
        if reply:
            summary = "; ".join(run.actions_log) or "none"
            sess.history.append({"role": "user", "content": f"User: {run.user_text}"})
            sess.history.append(
                {"role": "assistant",
                 "content": json.dumps({"thought": f"(Earlier turn. Actions taken: {summary})",
                                        "actions": [], "done": True, "reply": reply}, ensure_ascii=False)}
            )
            sess.history[:] = sess.history[-self.s.history_messages :]
            elapsed = int((time.monotonic() - run.started) * 1000)
            self._audit(sess, "reply", text=reply, steps=run.step, actions=len(run.actions_log), ms=elapsed)
        return self._respond(sess, run, reply, error)


def domain_label(domain: str) -> str:
    return DOMAIN_LABELS.get(domain, domain)
