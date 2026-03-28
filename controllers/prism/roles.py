"""Compatibility wrapper.

Canonical module moved to app.modules.prism.roles.
"""

from importlib import import_module
import sys

sys.modules[__name__] = import_module("app.modules.prism.roles")
