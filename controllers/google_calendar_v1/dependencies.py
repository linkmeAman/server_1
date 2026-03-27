"""Compatibility wrapper.

Canonical module moved to app.modules.google_calendar_v1.dependencies.
"""

from importlib import import_module
import sys

sys.modules[__name__] = import_module("app.modules.google_calendar_v1.dependencies")
