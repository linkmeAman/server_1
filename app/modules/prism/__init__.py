"""PRISM — Policy-driven Role & Identity Security Manager

Access control module.  All routes require supreme-user authentication.

Sub-modules:
  roles        → role registry CRUD
  policies     → policy document + statement CRUD
  assignments  → attach/detach roles to users, policies to roles/users, boundaries
  attributes   → user and resource ABAC attribute management
  registry     → resource and action catalog CRUD (drives UI dropdowns)
"""

from .router import router

__all__ = ["router"]
