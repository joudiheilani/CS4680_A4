import asyncio
import json

from app.agent import Agent
from app.audit import AuditLog
from app.mock_llm import ScriptedLLM


def turn(thought="", actions=None, done=False, reply=None):
    return json.dumps({"thought": thought, "actions": actions or [], "done": done, "reply": reply})


def make_agent(settings, audit, replies):
    llm = ScriptedLLM(replies)
    return Agent(llm, settings, audit), llm


def test_read_then_answer(settings, audit, session):
    agent, llm = make_agent(
        settings, audit,
        [turn("read", [{"tool": "read_file", "args": {"path": "welcome.md"}}]), turn("answer", reply="It's a welcome file.")],
    )
    res = asyncio.run(agent.start(session, "what's in welcome.md?"))
    assert res["ok"] and res["reply"] == "It's a welcome file."
    actions = [e for e in res["events"] if e["type"] == "action"]
    assert actions[0]["status"] == "success"
    # The second LLM call saw the file contents as labelled tool results.
    assert llm.calls[1][-1]["content"].startswith("TOOL RESULTS")
    assert "Welcome to StudyPilot" in llm.calls[1][-1]["content"]


def test_done_shortcut_skips_second_call(settings, audit, session):
    agent, llm = make_agent(
        settings, audit,
        [turn("add", [{"tool": "add_task", "args": {"title": "Read ch. 4"}}], done=True, reply="Added it.")],
    )
    res = asyncio.run(agent.start(session, "add a task to read ch. 4"))
    assert res["reply"] == "Added it."
    assert len(llm.calls) == 1
    assert any(t["title"] == "Read ch. 4" for t in res["workspace"]["tasks"])


def test_invalid_output_is_repaired(settings, audit, session):
    agent, llm = make_agent(settings, audit, ["I think you should list files", turn("ok", reply="Fixed.")])
    res = asyncio.run(agent.start(session, "hi"))
    assert res["reply"] == "Fixed."
    assert llm.calls[1][-1]["content"].startswith("FORMAT ERROR")
    assert any(e["type"] == "notice" for e in res["events"])


def test_hallucinated_tool_is_blocked_not_run(settings, audit, session):
    agent, llm = make_agent(
        settings, audit,
        [turn("x", [{"tool": "send_email", "args": {"to": "prof@school.edu"}}]), turn("y", reply="I can't send email.")],
    )
    res = asyncio.run(agent.start(session, "email my professor"))
    blocked = [e for e in res["events"] if e.get("status") == "blocked"]
    assert blocked and "Unknown tool" in blocked[0]["summary"]
    assert res["reply"] == "I can't send email."


def test_delete_requires_confirmation_approve(settings, audit, session):
    agent, llm = make_agent(
        settings, audit,
        [turn("del", [{"tool": "delete_file", "args": {"path": "welcome.md"}}], done=True, reply="Deleted welcome.md.")],
    )
    res = asyncio.run(agent.start(session, "delete welcome.md"))
    assert res["pending"] and res["reply"] is None
    assert (session.files_dir / "welcome.md").exists()  # nothing happens before approval

    res2 = asyncio.run(agent.confirm(session, res["pending"]["id"], approve=True))
    assert res2["reply"] == "Deleted welcome.md."
    assert not (session.files_dir / "welcome.md").exists()
    assert session.run is None


def test_delete_rejected_keeps_file(settings, audit, session):
    agent, llm = make_agent(
        settings, audit,
        [turn("del", [{"tool": "delete_file", "args": {"path": "welcome.md"}}], done=True, reply="Deleted."),
         turn("ack", reply="Okay, I left it alone.")],
    )
    res = asyncio.run(agent.start(session, "delete welcome.md"))
    res2 = asyncio.run(agent.confirm(session, res["pending"]["id"], approve=False))
    assert (session.files_dir / "welcome.md").exists()
    assert res2["reply"] == "Okay, I left it alone."
    assert "rejected_by_user" in llm.calls[1][-1]["content"]


def test_step_limit(settings, audit, session):
    settings.max_steps = 2
    replies = [turn("again", [{"tool": "list_files", "args": {}}])] * 2
    agent, _ = make_agent(settings, audit, replies)
    res = asyncio.run(agent.start(session, "loop forever"))
    assert "stopped after 2 steps" in res["reply"]


def test_audit_log_written(settings, audit, session):
    agent, _ = make_agent(settings, audit, [turn("hi", reply="Hello!")])
    asyncio.run(agent.start(session, "hello"))
    kinds = [e["kind"] for e in AuditLog.read(session.log_path)]
    assert kinds == ["user_request", "llm_call", "reply"]


def test_follow_up_sees_history(settings, audit, session):
    agent, llm = make_agent(settings, audit, [turn("a", reply="First."), turn("b", reply="Second.")])
    asyncio.run(agent.start(session, "one"))
    asyncio.run(agent.start(session, "two"))
    contents = [m["content"] for m in llm.calls[1]]
    assert any("User: one" in c for c in contents)
