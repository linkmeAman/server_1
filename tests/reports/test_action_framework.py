from app.modules.reports.schemas.models import (
    ReportAction,
    ReportActionBinding,
    ReportActionRule,
    ReportActionVisibility,
)
from app.modules.reports.services.action_framework import ReportActionFramework


def test_legacy_navigate_action_is_normalized_into_config() -> None:
    action = ReportAction(
        key="open-profile",
        label="Open profile",
        route_template="/crm/profile/{{contact_id}}",
    )

    assert action.type == "navigate"
    assert action.config["url_template"] == "/crm/profile/{{contact_id}}"
    assert action.config["open_in"] == "same_tab"


def test_action_framework_validates_column_bindings_and_resolves_visible_actions() -> None:
    framework = ReportActionFramework()
    action = ReportAction(
        key="approve",
        label="Approve {{case_name}}",
        type="api",
        config={
            "endpoint_template": "/api/cases/{{case_id}}/approve",
            "method": "POST",
            "success_message": "Approved {{case_name}}",
        },
        bindings=[
            ReportActionBinding(key="case_id", source="column", value="case_id"),
            ReportActionBinding(key="note", source="literal", value="Approved from report"),
        ],
        visibility=ReportActionVisibility(
            match="all",
            rules=[ReportActionRule(column="status", operator="eq", value="pending")],
        ),
    )

    errors = framework.validate_action(action=action, index=0, known_columns={"case_id", "case_name", "status"})
    assert errors == []

    resolved = framework.resolve_actions(
        actions=[action],
        row={"case_id": 42, "case_name": "Northwind Upgrade", "status": "pending"},
    )

    assert len(resolved) == 1
    assert resolved[0].endpoint == "/api/cases/42/approve"
    assert resolved[0].payload == {"case_id": 42, "note": "Approved from report"}
    assert resolved[0].success_message == "Approved Northwind Upgrade"

    hidden = framework.resolve_actions(
        actions=[action],
        row={"case_id": 42, "case_name": "Northwind Upgrade", "status": "closed"},
    )
    assert hidden == []
