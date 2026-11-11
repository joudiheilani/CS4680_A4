"""Importing this package registers every tool in REGISTRY."""

from . import files, system, tasks, web  # noqa: F401  (imported for their @register side effects)
from .base import (
    DOMAIN_LABELS,
    REGISTRY,
    Tool,
    ToolContext,
    ToolFailure,
    ToolResult,
    ValidationFailure,
    validate_action,
)

__all__ = [
    "DOMAIN_LABELS",
    "REGISTRY",
    "Tool",
    "ToolContext",
    "ToolFailure",
    "ToolResult",
    "ValidationFailure",
    "validate_action",
]
