"""Runtime settings, read from environment variables (and a local .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


@dataclass
class Settings:
    api_key: str = field(default_factory=lambda: _env("GROQ_API_KEY"))
    base_url: str = field(
        default_factory=lambda: _env("LLM_BASE_URL", "https://api.groq.com/openai/v1").rstrip("/")
    )
    model: str = field(default_factory=lambda: _env("LLM_MODEL", "llama-3.3-70b-versatile"))
    fallback_model: str = field(default_factory=lambda: _env("LLM_FALLBACK_MODEL", "llama-3.1-8b-instant"))
    mock: bool = field(default_factory=lambda: _env("LLM_MOCK").lower() in ("1", "true", "yes"))
    data_dir: Path = field(default_factory=lambda: Path(_env("DATA_DIR", "data")))

    # Agent loop
    max_steps: int = field(default_factory=lambda: _int("AGENT_MAX_STEPS", 6))
    history_messages: int = 8

    # LLM transport
    llm_timeout: float = 30.0
    llm_max_retries: int = 3
    llm_max_wait: float = 20.0  # longest rate-limit wait before switching to the fallback model

    # Abuse protection for the public deployment
    rate_per_minute: int = field(default_factory=lambda: _int("RATE_LIMIT_PER_MINUTE", 6))
    rate_per_day: int = field(default_factory=lambda: _int("RATE_LIMIT_PER_DAY", 120))
    session_ttl_hours: int = 24
    max_concurrent_runs: int = 3

    def __post_init__(self) -> None:
        # No key means there is nothing to call: fall back to the offline demo brain.
        if not self.api_key:
            self.mock = True
        self.data_dir = Path(self.data_dir)
