import unittest

from app.modules.reports.schemas.models import ReportDefinition, ReportQueryRequest


def _definition_payload():
    return {
        "slug": "sales-pipeline",
        "name": "Sales Pipeline",
        "description": "Business-facing report",
        "category": "Sales",
        "status": "draft",
        "kind": "table",
        "legacy_report_id": None,
        "prism_resource_code": "reports.sales_pipeline",
        "legacy_view_action": None,
        "source": {
            "type": "table",
            "database": "analytics",
            "table": "sales_pipeline_view",
            "route_path": None,
            "id_column": "id",
            "date_column": "created_at",
            "branch_column": None,
        },
        "columns": [
            {
                "key": "created_at",
                "label": "Creation Date",
                "type": "datetime",
                "visible": True,
                "sortable": True,
                "searchable": False,
                "exportable": True,
                "width": None,
            }
        ],
        "filters": [
            {
                "key": "inq_source",
                "label": "Inquiry Source",
                "column": "inq_source",
                "operators": ["eq", "is_null", "not_null"],
                "type": "text",
                "options": [{"label": "Meta", "value": "meta"}],
            }
        ],
        "default_sort": [],
        "search_columns": [],
        "date_range": {"enabled": False, "default_days": 30, "column": None},
        "branch_scope": {"mode": "all", "column": None},
        "actions": [],
        "route_path": "/reports/sales-pipeline",
        "version": 1,
        "source_label": "custom-admin",
    }


class TestReportBusinessPresentation(unittest.TestCase):
    def test_definition_defaults_display_labels_for_older_snapshots(self):
        definition = ReportDefinition.model_validate(_definition_payload())

        self.assertEqual("Creation Date", definition.columns[0].display_label)
        self.assertEqual("Creation Date", definition.columns[0].label)
        self.assertEqual("Inquiry Source", definition.filters[0].display_label)
        self.assertEqual("Inquiry Source", definition.filters[0].label)

    def test_query_request_accepts_business_friendly_null_operator_aliases(self):
        request = ReportQueryRequest.model_validate(
            {
                "filters": [
                    {"column": "inq_source", "operator": "is_empty"},
                    {"column": "created_at", "operator": "has_any_value"},
                ]
            }
        )

        self.assertEqual("is_null", request.filters[0].operator)
        self.assertEqual("not_null", request.filters[1].operator)


if __name__ == "__main__":
    unittest.main()
