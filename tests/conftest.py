import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.audit import AuditLog  # noqa: E402
from app.config import Settings  # noqa: E402
from app.sessions import SessionStore  # noqa: E402


@pytest.fixture
def settings(tmp_path):
    return Settings(api_key="", data_dir=tmp_path, max_steps=6)


@pytest.fixture
def store(settings):
    return SessionStore(settings)


@pytest.fixture
def session(store):
    return store.get(store.new_id())


@pytest.fixture
def audit(settings):
    return AuditLog(settings.data_dir)
