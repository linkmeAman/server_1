"""Compatibility wrapper.

Canonical module moved to app.shared.response_normalization.
"""

from importlib import import_module
import sys

sys.modules[__name__] = import_module("app.shared.response_normalization")
