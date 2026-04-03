"""Workforce endpoint dependencies."""

from app.core.prism_guard import CallerContext, require_any_caller

__all__ = ["CallerContext", "require_any_caller"]
