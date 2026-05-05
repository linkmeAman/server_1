# Report Platform Backend Changes

This document summarizes the current backend report platform implementation in `server_1`.

## Summary

The backend report platform under `/api/reports` now supports:

- report catalog discovery
- PRISM-backed report view and management authorization
- generic table report metadata lookup and querying
- canonical admin draft lifecycle routes
- structured admin validation errors for the frontend builder
- legacy report import into editable drafts
- report definition versioning
- report run audit logs

The implementation keeps the public read/query surface separate from the admin write surface.

## Backend Module

Main module:

```text
app/modules/reports/
```

Important files:

| File | Purpose |
| --- | --- |
| `router.py` | Public and admin report routes |
| `schemas/models.py` | Pydantic DTOs for viewer and admin contracts |
| `services/definition.py` | Public definition loading for certified and DB-backed reports |
| `services/admin.py` | Create, update, get, list, publish, archive, and legacy-import orchestration |
| `services/validator.py` | Cross-field report definition validation |
| `services/errors.py` | Stable API exceptions for validation/conflict flows |
| `services/catalog.py` | Visible report catalog assembly |
| `services/permission.py` | PRISM-backed view/manage/action checks |
| `services/query.py` | Structured query execution and audit logging |
| `services/legacy_import.py` | Legacy CRM report discovery and draft import shaping |

The router remains registered from:

```text
app/api/v1/router.py
```

## Route Surface

Base prefix:

```text
/api/reports
```

Public routes:

| Method | Route | Purpose |
| --- | --- | --- |
| `GET` | `/api/reports` | Return reports visible to the authenticated user |
| `GET` | `/api/reports/{slug}` | Return report definition metadata |
| `POST` | `/api/reports/{slug}/query` | Run a structured query for table-backed reports |

Admin routes:

| Method | Route | Purpose |
| --- | --- | --- |
| `GET` | `/api/reports/admin/reports?status=...` | List DB-backed admin reports, optionally filtered by `draft`, `published`, `archived`, or `all` |
| `GET` | `/api/reports/admin/reports/{slug}` | Load a single admin report definition |
| `POST` | `/api/reports/admin/reports` | Create a new draft |
| `PUT` | `/api/reports/admin/reports/{slug}` | Update an existing draft |
| `POST` | `/api/reports/admin/reports/{slug}/publish` | Publish the active draft version |
| `POST` | `/api/reports/admin/reports/{slug}/archive` | Archive a report |
| `POST` | `/api/reports/admin/legacy/{report_id}/import` | Import legacy metadata into a draft and return the editable report |
| `GET` | `/api/reports/admin/discovery/databases` | List accessible source databases for report builder |
| `GET` | `/api/reports/admin/discovery/tables?db=...` | List accessible tables for the selected database |
| `GET` | `/api/reports/admin/discovery/columns?db=...&table=...` | Describe source table columns with inferred report field types |

Compatibility alias:

| Method | Route | Purpose |
| --- | --- | --- |
| `GET` | `/api/reports/admin/drafts` | Legacy alias for draft-only admin list responses |

All report routes require an authenticated bearer token. Admin routes additionally require `reports:manage` or `reports:write`.

## Admin Contract

The admin surface no longer relies on a loose `definition: dict[str, Any]` payload.

Key DTOs now include:

- `ReportDraftUpsertRequest`
- `ReportDraftResponse`
- `ReportAdminListResponse`
- `ReportImportDraftResponse`
- `ReportValidationErrorResponse`

Important response behavior:

- validation failures return `422`
- conflict failures return `409`
- missing reports return `404`
- permission failures return `403`

### Validation Error Shape

Validation failures return field-level details shaped for frontend form mapping:

```json
{
  "success": false,
  "error": "ReportValidationError",
  "message": "Report definition validation failed",
  "data": {
    "field_errors": [
      {
        "path": "filters.0.column",
        "code": "invalid_reference",
        "message": "Filters must reference a declared column."
      }
    ]
  }
}
```

The `path` field uses dot notation compatible with `react-hook-form`.

## Validation Rules

Cross-field business rules live in `ReportDefinitionValidator`.

Validation now enforces:

- source type must match report kind
- table reports must declare a valid source table
- route-backed reports must declare a route path
- duplicate column, filter, and action keys are rejected
- filters must reference declared columns
- search columns must reference declared searchable columns
- default sort must reference declared sortable columns
- date-range and branch-scope columns must reference declared columns
- filter operators must be compatible with the referenced column type
- publish requires a complete, runnable definition

This keeps shape validation in Pydantic and business consistency validation in one dedicated service.

## Service Split

The backend now separates read/public concerns from admin/write concerns:

- `ReportDefinitionService` remains focused on loading certified and DB-backed definitions for catalog/view/query flows
- `ReportAdminService` owns persistence and lifecycle transitions
- `ReportDefinitionValidator` owns report definition consistency checks

This prevents the public loader from turning into a catch-all admin workflow service.

## Database Model

The existing Alembic migration remains:

```text
alembic/versions/20260428_007_report_platform.py
```

It creates:

- `report_definitions`
- `report_versions`
- `report_run_logs`

No new schema migration was required for the admin builder contract updates.

## Certified Reports

The platform still seeds certified definitions in code through `ReportDefinitionService`.

Current seeded slugs:

- `inquiry-411`
- `top-summary`
- `source-breakdown`
- `center-performance`
- `funnel-tracking`
- `campaign-performance`
- `heard-from-performance`
- `event-calendar`

Certified route-backed reports remain discoverable in the catalog but continue to render through their existing frontend pages.

## Legacy Import Behavior

`LegacyReportImportService` still reads from:

- `report`
- `report_column`
- `report_button`

Current behavior:

- build a draft-safe `ReportDefinition`
- keep unsafe or ambiguous behavior out of automatic publication
- return warnings for manual review when needed
- allow the frontend builder to continue the workflow from the imported draft

The importer does not publish automatically.

## Catalog and PRISM Behavior

Catalog behavior is unchanged in principle:

- public catalog responses are filtered through PRISM view checks
- super users can still see pending legacy report inventory rows
- pending legacy rows are marked unavailable and are not runnable until migrated

Permission behavior:

- view checks still use PRISM first
- super users still bypass PRISM checks
- legacy compatibility actions such as `report:read` and `top-summary:read` remain accepted where configured
- admin routes require `reports:manage` or `reports:write`

Builder discovery behavior:

- discovery endpoints are protected by report manage/write authorization
- database and table names are validated through the DB explorer security helpers
- source columns include inferred `report_type` metadata (`text`, `integer`, `number`, `currency`, `date`, `datetime`, `boolean`) for frontend defaults

## Query Safety

The query endpoint still accepts only structured payloads, never raw SQL from the browser.

Server-side protections still enforce:

- identifier validation for table and column names
- allowed-column checks
- allowed filter operator checks
- parameterized SQL values
- page size limit of `100`
- deterministic sort behavior

## Tests Added

New report-focused backend tests:

| File | Purpose |
| --- | --- |
| `tests/test_reports_validator.py` | Unit coverage for kind-specific and cross-field validation rules |
| `tests/test_reports_admin_routes.py` | Route-level coverage for admin list/create/import/archive and structured error responses |

## Verification Notes

Targeted backend verification passed with a test-safe `DEBUG=true` override:

```bash
$env:DEBUG='true'
.\.venv\Scripts\python.exe -m unittest tests.test_reports_validator tests.test_reports_admin_routes
```

The override was needed because the local environment in this workspace exposes `DEBUG=release`, which is not parseable as a boolean by the backend settings model.

## What Was Not Changed

The following are still intentionally out of scope for this slice:

- modifying legacy CRM report tables
- automatic migration of old report data
- export-job redesign
- compare/revert version history APIs
- broader payment report migration

## Current Backend Status

The backend report platform is now ready for the dedicated frontend builder flow.

It provides:

- canonical admin lifecycle routes
- typed admin request/response contracts
- structured field-level validation errors
- archive and single-report admin retrieval support
- legacy import into the same editable draft workflow

## Next Backend Steps

- Add broader integration coverage once full end-to-end admin QA is available
- Expand legacy import fidelity only where real migration cases require it
- Add rollout gating only if environment-based exposure is needed beyond PRISM authorization
