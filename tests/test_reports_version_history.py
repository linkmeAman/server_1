import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.modules.reports.schemas.models import ReportDefinition
from app.modules.reports.services.admin import ReportAdminService


class _FakeResult:
    def __init__(self, *, rows=None, row=None, lastrowid=None):
        self._rows = rows or []
        self._row = row
        self.lastrowid = lastrowid

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._row


def _row(mapping):
    return SimpleNamespace(_mapping=mapping)


def _definition_payload(**overrides):
    payload = {
        "slug": "jaan-e-aman",
        "name": "Jaaneaman",
        "description": "Historic snapshot",
        "category": "Reports",
        "status": "published",
        "kind": "table",
        "legacy_report_id": None,
        "prism_resource_code": "reports.jaaneaman",
        "legacy_view_action": None,
        "source": {
            "type": "table",
            "database": "analytics",
            "table": "historic_report_view",
            "route_path": None,
            "id_column": "id",
            "date_column": None,
            "branch_column": None,
        },
        "columns": [
            {
                "key": "id",
                "label": "ID",
                "type": "number",
                "visible": True,
                "sortable": True,
                "searchable": False,
                "exportable": True,
                "width": None,
            }
        ],
        "filters": [],
        "default_sort": [],
        "search_columns": [],
        "date_range": {"enabled": False, "default_days": 30, "column": None},
        "branch_scope": {"mode": "all", "column": None},
        "actions": [],
        "route_path": "/reports/jaan-e-aman",
        "version": 6,
        "source_label": "custom-admin",
    }
    payload.update(overrides)
    return payload


class TestReportVersionHistory(unittest.IsolatedAsyncioTestCase):
    async def test_list_report_versions_returns_saved_snapshots(self):
        service = ReportAdminService()
        service._fetch_report_row = AsyncMock(
            return_value={"id": 9, "slug": "jaan-e-aman", "status": "published", "active_version_id": 101}
        )

        session = AsyncMock()
        session.execute = AsyncMock(
            return_value=_FakeResult(
                rows=[
                    _row(
                        {
                            "slug": "jaan-e-aman",
                            "active_version_id": 101,
                            "id": 101,
                            "version": 7,
                            "status": "draft",
                            "definition_json": ReportDefinition.model_validate(
                                _definition_payload(version=7, status="draft")
                            ).model_dump_json(),
                            "created_by_user_id": 55,
                            "created_at": "2026-05-10T10:00:00",
                        }
                    ),
                    _row(
                        {
                            "slug": "jaan-e-aman",
                            "active_version_id": 101,
                            "id": 88,
                            "version": 6,
                            "status": "published",
                            "definition_json": ReportDefinition.model_validate(
                                _definition_payload(version=6, status="published")
                            ).model_dump_json(),
                            "created_by_user_id": 22,
                            "created_at": "2026-05-01T09:00:00",
                        }
                    ),
                ]
            )
        )

        versions = await service.list_report_versions(session, "jaan-e-aman")

        self.assertEqual([7, 6], [item.version for item in versions])
        self.assertTrue(versions[0].is_active)
        self.assertFalse(versions[1].is_active)
        self.assertEqual("historic_report_view", versions[1].report.source.table)
        self.assertEqual("Jaaneaman", versions[1].report.name)

    async def test_restore_report_version_uses_saved_snapshot_definition(self):
        service = ReportAdminService()
        service._fetch_report_row = AsyncMock(
            return_value={"id": 9, "slug": "jaan-e-aman", "status": "published", "active_version_id": 101}
        )
        service._save_report = AsyncMock(
            return_value=ReportDefinition.model_validate(_definition_payload(version=8, status="draft"))
        )

        session = AsyncMock()
        session.execute = AsyncMock(
            return_value=_FakeResult(
                row=_row(
                    {
                        "definition_json": ReportDefinition.model_validate(
                            _definition_payload(version=4, name="Historic V4")
                        ).model_dump_json()
                    }
                )
            )
        )

        restored = await service.restore_report_version(session, "jaan-e-aman", 4, user_id=99)

        self.assertEqual(8, restored.version)
        self.assertEqual("draft", restored.status)
        self.assertTrue(service._save_report.await_args.kwargs["report_id"] == 9)
        payload = service._save_report.await_args.args[1]
        self.assertEqual("Historic V4", payload.name)
        self.assertEqual("historic_report_view", payload.source.table)


if __name__ == "__main__":
    unittest.main()
