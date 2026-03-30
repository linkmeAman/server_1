"""Compatibility wrapper.

Canonical module moved to app.core.middleware.
"""

from importlib import import_module
import sys

sys.modules[__name__] = import_module("app.core.middleware")
