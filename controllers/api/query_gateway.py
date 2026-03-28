"""Compatibility wrapper.

Canonical module moved to app.modules.query_gateway.router.
"""

from importlib import import_module
import sys

sys.modules[__name__] = import_module("app.modules.query_gateway.router")
