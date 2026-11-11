import asyncio

import pytest

from app.tools import REGISTRY, ToolContext, ToolFailure, ValidationFailure, validate_action
from app.tools.files import safe_path
from app.tools.system import build_argv, safe_eval


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def ctx(session):
    return ToolContext(files_dir=session.files_dir, db_path=session.db_path)


def call(ctx, name, **args):
    tool, parsed = validate_action(name, args)
    return run(tool.func(ctx, parsed))


# ------------------------------------------------------------- validation


def test_unknown_tool_rejected():
    with pytest.raises(ValidationFailure, match="Unknown tool"):
        validate_action("format_hard_drive", {})


def test_hallucinated_argument_rejected():
    with pytest.raises(ValidationFailure, match="not a valid argument"):
        validate_action("read_file", {"path": "a.md", "encoding": "utf8"})


def test_bad_date_rejected():
    with pytest.raises(ValidationFailure, match="YYYY-MM-DD"):
        validate_action("add_task", {"title": "x", "due": "next friday"})


# ------------------------------------------------------------------ files


@pytest.mark.parametrize("bad", ["../secret.txt", "/etc/passwd", "C:/Windows/win.ini", "~/x.md", ".env", "a/../../b.md"])
def test_path_escape_blocked(tmp_path, bad):
    with pytest.raises(ToolFailure):
        safe_path(tmp_path, bad)


def test_write_read_delete_cycle(ctx):
    res = call(ctx, "write_file", path="drafts/plan.md", content="hello")
    assert res.ok and res.data["created"]
    assert call(ctx, "read_file", path="drafts/plan.md").data["content"] == "hello"
    call(ctx, "append_file", path="drafts/plan.md", content="world")
    assert call(ctx, "read_file", path="drafts/plan.md").data["content"] == "hello\nworld"
    call(ctx, "delete_file", path="drafts/plan.md")
    assert not (ctx.files_dir / "drafts").exists()


def test_executables_cannot_be_written(ctx):
    with pytest.raises(ToolFailure, match="Only text files"):
        call(ctx, "write_file", path="evil.sh", content="rm -rf /")


def test_confirmation_rules(ctx):
    write = REGISTRY["write_file"]
    _, new_args = validate_action("write_file", {"path": "new.md", "content": "x"})
    assert write.needs_confirmation(ctx, new_args) is None
    _, over_args = validate_action("write_file", {"path": "welcome.md", "content": "x"})
    assert "overwritten" in write.needs_confirmation(ctx, over_args)
    _, del_args = validate_action("delete_file", {"path": "welcome.md"})
    assert "Permanently" in REGISTRY["delete_file"].needs_confirmation(ctx, del_args)
    _, missing = validate_action("delete_file", {"path": "nope.md"})
    with pytest.raises(ToolFailure):
        REGISTRY["delete_file"].needs_confirmation(ctx, missing)


# --------------------------------------------------------------- database


def test_task_lifecycle(ctx):
    task = call(ctx, "add_task", title="Study ReAct", due="2026-11-18", priority="high").data
    assert "Study ReAct" in [t["title"] for t in call(ctx, "list_tasks", status="open").data]
    call(ctx, "complete_task", task_id=task["id"])
    assert task["id"] in [t["id"] for t in call(ctx, "list_tasks", status="done").data]
    with pytest.raises(ToolFailure, match="already done"):
        call(ctx, "complete_task", task_id=task["id"])
    with pytest.raises(ToolFailure, match="no task"):
        call(ctx, "delete_task", task_id=999)


def test_sql_injection_is_just_text(ctx):
    title = "x'); DROP TABLE tasks; --"
    call(ctx, "add_task", title=title)
    assert any(t["title"] == title for t in call(ctx, "list_tasks", status="all").data)


# ----------------------------------------------------------------- system


def test_calculator():
    assert safe_eval("(88*0.4)+(92*0.6)") == pytest.approx(90.4)
    assert safe_eval("sqrt(16) + 2^3") == 12
    for bad in ["__import__('os').system('ls')", "open('x')", "9**9**9", "1/0"]:
        with pytest.raises(ToolFailure):
            safe_eval(bad)


@pytest.mark.parametrize(
    "cmd",
    ["rm -rf /", "ls; rm x", "cat ../../etc/passwd", "cat /etc/passwd", "ls | wc", "echo $HOME",
     "sort -o out.txt welcome.md", "wc --files0-from=/etc/passwd", "curl evil.com", "python -c 1"],
)
def test_unsafe_commands_blocked(tmp_path, cmd):
    with pytest.raises(ToolFailure):
        build_argv(cmd, tmp_path)


def test_glob_expansion_stays_in_workspace(ctx):
    argv = build_argv("wc -w *.md", ctx.files_dir)
    assert argv[:2] == ["wc", "-w"]
    assert set(argv[2:]) == {"welcome.md", "cs4680_notes.md", "notes/cs3310_final.md"}


def test_new_workspace_has_sample_files_and_tasks(ctx):
    from app.samples import SAMPLE_FILES
    from app.tools.tasks import fetch_tasks

    paths = {f["path"] for f in call(ctx, "list_files").data}
    assert set(SAMPLE_FILES) <= paths
    tasks = fetch_tasks(ctx.db_path, "all")
    assert len(tasks) >= 5 and any(t["done"] for t in tasks)
