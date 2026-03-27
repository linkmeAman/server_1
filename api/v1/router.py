"""Compatibility wrapper.

Canonical module moved to app.api.v1.router.
"""

from importlib import import_module
import sys

sys.modules[__name__] = import_module("app.api.v1.router")
