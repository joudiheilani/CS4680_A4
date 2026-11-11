import pytest

from app.parser import ParseError, parse_turn


def test_plain_json():
    turn = parse_turn('{"thought": "t", "actions": [{"tool": "list_files", "args": {}}], "done": false, "reply": null}')
    assert turn.actions[0].tool == "list_files"
    assert turn.done is False


def test_markdown_fence_and_prose():
    text = 'Sure! Here you go:\n```json\n{"thought": "x", "actions": [], "reply": "hi"}\n```\nHope that helps.'
    assert parse_turn(text).reply == "hi"


def test_json_buried_in_prose():
    text = 'I will do it. {"thought": "x", "actions": [{"tool": "read_file", "args": {"path": "a {b}.md"}}]} ok'
    assert parse_turn(text).actions[0].args == {"path": "a {b}.md"}


def test_code_block_inside_reply_string():
    text = '{"thought": "x", "actions": [], "reply": "Output:\\n```\\n117 notes.md\\n```"}'
    assert "117 notes.md" in parse_turn(text).reply


def test_fenced_json_with_prose_after():
    text = 'Here:\n```json\n{"reply": "ok"}\n```\nanything else?'
    assert parse_turn(text).reply == "ok"


def test_aliases_are_normalised():
    text = '{"action": {"name": "add_task", "arguments": "{\\"title\\": \\"x\\"}"}}'
    turn = parse_turn(text)
    assert turn.actions[0].tool == "add_task"
    assert turn.actions[0].args == {"title": "x"}


def test_final_alias():
    assert parse_turn('{"thought": "", "final": "all done"}').reply == "all done"


@pytest.mark.parametrize(
    "text",
    [
        "no json here",
        "[1, 2, 3]",
        '{"thought": "nothing to do"}',
        '{"actions": "list_files"}',
        '{"actions": [' + ",".join(['{"tool": "list_files"}'] * 6) + "]}",
    ],
)
def test_invalid_outputs_raise(text):
    with pytest.raises(ParseError):
        parse_turn(text)
