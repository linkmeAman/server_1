"""Compatibility wrapper.

Canonical module moved to app.modules.employee_events_v1.services.google_payload_builder.
"""

from importlib import import_module
import sys

sys.modules[__name__] = import_module("app.modules.employee_events_v1.services.google_payload_builder")
