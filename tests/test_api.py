"""HTTP-level tests for the manual task editing endpoints."""

import os
import tempfile

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="studypilot-api-"))
os.environ["LLM_MOCK"] = "1"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


def _client():
    c = TestClient(app)
    c.post("/api/reset")
    return c


def _task(c, title_part):
    return next(t for t in c.get("/api/state").json()["workspace"]["tasks"] if title_part in t["title"])


def test_edit_task_fields_and_log():
    c = _client()
    t = _task(c, "lab notebook")
    res = c.patch(f"/api/tasks/{t['id']}", json={"title": "Buy two lab notebooks", "due": "2026-12-01", "priority": "high"})
    assert res.status_code == 200
    assert res.json()["task"] == {**t, "title": "Buy two lab notebooks", "due": "2026-12-01", "priority": "high"}
    log = c.get("/api/log").json()["entries"]
    assert any(e["kind"] == "user_edit" and e["task_id"] == t["id"] for e in log)


def test_edit_can_clear_due_and_toggle_done():
    c = _client()
    t = _task(c, "research report")
    after = c.patch(f"/api/tasks/{t['id']}", json={"due": "", "done": True}).json()["task"]
    assert after["due"] is None and after["done"] is True
    assert c.patch(f"/api/tasks/{t['id']}", json={"done": False}).json()["task"]["done"] is False


def test_edit_rejects_bad_input():
    c = _client()
    t = _task(c, "research report")
    assert c.patch(f"/api/tasks/{t['id']}", json={"due": "next friday"}).status_code == 422
    assert c.patch(f"/api/tasks/{t['id']}", json={"priority": "urgent"}).status_code == 422
    assert c.patch(f"/api/tasks/{t['id']}", json={"title": "   "}).status_code == 422
    assert c.patch(f"/api/tasks/{t['id']}", json={"owner": "x"}).status_code == 422
    assert c.patch("/api/tasks/9999", json={"title": "x"}).status_code == 404


def test_delete_task():
    c = _client()
    t = _task(c, "Email lab partner")
    assert c.delete(f"/api/tasks/{t['id']}").status_code == 200
    ids = [x["id"] for x in c.get("/api/state").json()["workspace"]["tasks"]]
    assert t["id"] not in ids
    assert c.delete(f"/api/tasks/{t['id']}").status_code == 404
