# Report Platform Backend Changes

This document summarizes the current backend report platform implementation in `server_1`.

## Summary

The backend report platform under `/api/reports` now supports:

- report catalog discovery
- PRISM-backed report view and management authorization
- generic table report metadata lookup and querying
- canonical admin draft lifecycle routes
- permissive draft saves with publish-readiness validation issues
- structured admin validation errors for the frontend builder
- legacy report inventory discovery for admins
- single and batch legacy report import into editable drafts
- structured legacy import issues for recoverable and unrecoverable import results
- report definition versioning
- report version-history and restore APIs for saved draft/published snapshots
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
| `GET` | `/api/reports/catalog` | Return reports visible to the authenticated user |
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
| `GET` | `/api/reports/admin/reports/{slug}/versions` | Return saved report-version snapshots, including each stored definition payload |
| `POST` | `/api/reports/admin/reports/{slug}/versions/{version}/restore` | Create a new active draft from a saved version snapshot |
| `GET` | `/api/reports/admin/legacy/reports` | List admin-visible legacy report import candidates and current migration state |
| `POST` | `/api/reports/admin/legacy/import` | Import multiple legacy reports and return per-item results |
| `POST` | `/api/reports/admin/legacy/{report_id}/import` | Import legacy metadata into a draft and return the editable report |
| `GET` | `/api/reports/admin/discovery/databases` | List accessible source databases for report builder |
| `GET` | `/api/reports/admin/discovery/tables?db=...` | List accessible tables for the selected database |
| `GET` | `/api/reports/admin/discovery/columns?db=...&table=...` | Describe source table columns with inferred report field types |

Compatibility alias:

| Method | Route | Purpose |
| --- | --- | --- |
| `GET` | `/api/reports` | Hidden legacy alias for the catalog while clients migrate to `/api/reports/catalog` |
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
- `LegacyReportCandidate`
- `LegacyImportIssue`
- `LegacyImportItemResult`
- `LegacyImportBatchRequest`
- `LegacyImportBatchResponse`

Important response behavior:

- create/update draft saves return `validation_issues` for fields that must be fixed before publish
- publish validation failures return `422`
- conflict failures return `409`
- missing reports return `404`
- permission failures return `403`

The API contract remains structured and technical internally, but the intended frontend direction is that business users should not be required to author raw JSON, reason about database-style null semantics, or read raw schema names directly in the builder.

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

Draft saves are permissive so admins can preserve incomplete work and continue cleanup later. Publish validation now enforces:

- source type must match report kind
- table reports must declare a valid source database
- table reports must declare a valid source table
- route-backed reports must declare a route path
- duplicate column, filter, and action keys are rejected
- filters must reference declared columns
- search columns must reference declared searchable columns
- default sort must reference declared sortable columns
- date-range and branch-scope columns must reference declared columns
- filter operators must be compatible with the referenced column type
- publish requires a complete, runnable definition

Published reports remain live while a newer draft version is edited. Public catalog and report-view loading select the latest published `report_versions` row; admin edit/list loading uses the active draft version.

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

- list admin-visible import candidates from legacy metadata
- mark already migrated legacy reports as unavailable for re-import in the admin inventory response
- build a draft-safe `ReportDefinition`
- persist `source.database` alongside `source.table` for admin-created drafts so the builder can restore the original source selection
- allow recoverable imports to persist drafts even when manual cleanup is still required
- return structured issues for manual review and next-step guidance
- keep unsafe or ambiguous behavior out of automatic publication
- allow the frontend builder to continue the workflow from the imported draft

The importer does not publish automatically.

Recoverable import issues now include examples such as:

- missing source table
- no visible columns mapped
- dynamic SQL review required
- legacy button predicate review required

Unrecoverable import failures remain item-level failures in batch responses instead of failing the whole request.

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
- dedicated legacy import inventory and batch import routes also require `reports:manage` or `reports:write`

Builder discovery behavior:

- discovery endpoints are protected by report manage/write authorization
- database and table names are validated through the DB explorer security helpers
- source columns include inferred `report_type` metadata (`text`, `integer`, `number`, `currency`, `date`, `datetime`, `boolean`) for frontend defaults

Target presentation-layer support:

- the backend should continue returning structured canonical field metadata
- the frontend should be able to layer business-facing labels and display-format choices on top of raw schema names
- option-list payloads should stay structured in the contract, even when the UI replaces raw JSON entry with a visual list builder
- operator contracts may stay technical internally, while the UI maps them to business copy such as `is exactly`, `is one of`, `Is empty`, and `Has any value`

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
| `tests/test_reports_admin_routes.py` | Route-level and service-level coverage for admin list/create/import/archive, legacy inventory, batch import, and structured error responses |

## Verification Notes

Targeted backend verification passed with a test-safe `DEBUG=true` override:

```bash
$env:DEBUG='true'
.\.venv\Scripts\python.exe -m unittest tests.test_reports_validator tests.test_reports_admin_routes
```

If the local Python environment is missing backend dependencies such as `fastapi` or `pydantic`, these suites will not run until the project environment is restored.

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
- version-history snapshot retrieval and restore routes
- typed admin request/response contracts
- structured field-level validation errors
- archive and single-report admin retrieval support
- legacy import into the same editable draft workflow

## Next Backend Steps

- Support optional field-presentation metadata so the frontend can map raw columns such as `created_at`, `employee_id`, and `inq_source` to business-friendly labels
- Support business-friendly filter/operator vocabulary in admin contracts while keeping structured operators internal
- Keep option-list payloads structured in the API, but avoid requiring raw JSON entry in normal builder workflows
- Keep the admin flow compatible with a staged wizard: basic info, data connection, field translation, visual filter design, default behavior, and publish validation
- Add broader integration coverage once full end-to-end admin QA is available
- Expand legacy import fidelity only where real migration cases require it
- Add rollout gating only if environment-based exposure is needed beyond PRISM authorization
