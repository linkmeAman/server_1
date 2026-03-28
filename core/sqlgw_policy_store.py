"""Compatibility wrapper.

Canonical module moved to app.core.sqlgw_policy_store.
"""

from importlib import import_module
import sys

sys.modules[__name__] = import_module("app.core.sqlgw_policy_store")
