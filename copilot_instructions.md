This backend now uses explicit FastAPI routers only.

Canonical structure:
- `app/api/v1/router.py` for route registration
- `app/core/*` for shared runtime services
- `app/modules/*` for domain modules
- `tests/*` for test coverage

Guidance for code generation:
- Do not create or reference dynamic `/py/{controller}/{function}` routes.
- Do not create or reference `controllers/`, `core/`, or `api/` compatibility packages.
- Add new endpoints under `app/modules/<domain>/router.py` and include them from `app/api/v1/router.py`.
- Put shared DB, auth, PRISM, SQL gateway, and middleware logic under `app/core/*`.
- Keep imports canonical: `app.*` only.
