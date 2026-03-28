"""Compatibility wrapper.

Canonical module moved to app.modules.orders.router.
"""

from importlib import import_module
import sys

sys.modules[__name__] = import_module("app.modules.orders.router")
