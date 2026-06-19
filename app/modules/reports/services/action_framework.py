"""Shared validation and row-resolution logic for report actions."""

from __future__ import annotations

import re
from typing import Any

from ..schemas import ReportAction, ReportFieldError, ReportRowAction

TEMPLATE_PATTERN = re.compile(r"\{\{\s*(?:row\.)?([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
ACTION_TYPES = {"navigate", "modal", "popup", "workflow", "api"}
ACTION_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
ACTION_RULE_OPERATORS = {"eq", "ne", "in", "not_in", "is_truthy", "is_falsy", "is_empty", "not_empty"}


class ReportActionFramework:
    """Central registry and runtime helpers for report row actions."""

    def validate_action(self, *, action: ReportAction, index: int, known_columns: set[str]) -> list[ReportFieldError]:
        errors: list[ReportFieldError] = []
        action_type = (action.type or "").strip().lower()
        if action_type not in ACTION_TYPES:
            errors.append(
                ReportFieldError(
                    path=f"actions.{index}.type",
                    code="invalid_value",
                    message="Unsupported action type.",
                )
            )
            return errors

        config = action.config or {}
        required_fields = {
            "navigate": ("url_template",),
            "modal": ("modal_key",),
            "popup": ("url_template",),
            "workflow": ("workflow_key",),
            "api": ("endpoint_template",),
        }[action_type]
        for field_name in required_fields:
            if not str(config.get(field_name) or "").strip():
                errors.append(
                    ReportFieldError(
                        path=f"actions.{index}.config.{field_name}",
                        code="required",
                        message="This field is required for the selected action type.",
                    )
                )

        if action_type in {"navigate", "popup"}:
            url_template = str(config.get("url_template") or "")
            if url_template and not self._is_url_template(url_template):
                errors.append(
                    ReportFieldError(
                        path=f"actions.{index}.config.url_template",
                        code="invalid_value",
                        message="Action URLs must start with '/', 'http://', 'https://', or a row template.",
                    )
                )

        if action_type == "api":
            endpoint_template = str(config.get("endpoint_template") or "")
            if endpoint_template and not endpoint_template.startswith("/"):
                errors.append(
                    ReportFieldError(
                        path=f"actions.{index}.config.endpoint_template",
                        code="invalid_value",
                        message="API endpoints must be relative paths starting with '/'.",
                    )
                )

        if action_type == "workflow":
            endpoint_template = str(config.get("endpoint_template") or "")
            if endpoint_template and not endpoint_template.startswith("/"):
                errors.append(
                    ReportFieldError(
                        path=f"actions.{index}.config.endpoint_template",
                        code="invalid_value",
                        message="Workflow endpoints must be relative paths starting with '/'.",
                    )
                )

        method = str(config.get("method") or "").upper()
        if method and method not in ACTION_METHODS:
            errors.append(
                ReportFieldError(
                    path=f"actions.{index}.config.method",
                    code="invalid_value",
                    message="Unsupported HTTP method.",
                )
            )

        binding_keys: set[str] = set()
        for binding_index, binding in enumerate(action.bindings):
            binding_key = binding.key.strip().lower()
            if binding_key in binding_keys:
                errors.append(
                    ReportFieldError(
                        path=f"actions.{index}.bindings.{binding_index}.key",
                        code="duplicate",
                        message="Action payload keys must be unique.",
                    )
                )
            else:
                binding_keys.add(binding_key)
            if binding.source == "column" and binding.value not in known_columns:
                errors.append(
                    ReportFieldError(
                        path=f"actions.{index}.bindings.{binding_index}.value",
                        code="invalid_reference",
                        message="Mapped column must reference a configured report column.",
                    )
                )

        for rule_index, rule in enumerate(action.visibility.rules if action.visibility else []):
            if rule.column not in known_columns:
                errors.append(
                    ReportFieldError(
                        path=f"actions.{index}.visibility.rules.{rule_index}.column",
                        code="invalid_reference",
                        message="Visibility rules must reference a configured report column.",
                    )
                )
            if rule.operator not in ACTION_RULE_OPERATORS:
                errors.append(
                    ReportFieldError(
                        path=f"actions.{index}.visibility.rules.{rule_index}.operator",
                        code="invalid_value",
                        message="Unsupported visibility operator.",
                    )
                )
        return errors

    def resolve_actions(self, *, actions: list[ReportAction], row: dict[str, Any]) -> list[ReportRowAction]:
        resolved: list[ReportRowAction] = []
        for action in actions:
            if not self._is_visible(action, row):
                continue
            payload = self._resolve_payload(action, row)
            config = action.config or {}
            common = {
                "key": action.key,
                "label": self._resolve_text(action.label, row) or action.label,
                "type": action.type,
                "icon": action.icon,
                "confirm": action.confirm,
            }

            if action.type == "navigate":
                resolved.append(
                    ReportRowAction(
                        **common,
                        href=self._resolve_text(str(config.get("url_template") or action.route_template or ""), row) or None,
                        open_in=str(config.get("open_in") or "same_tab"),
                    )
                )
            elif action.type == "modal":
                resolved.append(
                    ReportRowAction(
                        **common,
                        modal_key=str(config.get("modal_key") or "").strip() or None,
                        title=self._resolve_text(str(config.get("title_template") or action.label), row) or action.label,
                        description=self._resolve_text(str(config.get("description_template") or ""), row) or None,
                        payload=payload or None,
                    )
                )
            elif action.type == "popup":
                resolved.append(
                    ReportRowAction(
                        **common,
                        href=self._resolve_text(str(config.get("url_template") or ""), row) or None,
                        popup_title=self._resolve_text(str(config.get("window_title") or action.label), row) or action.label,
                        popup_width=self._coerce_int(config.get("width")),
                        popup_height=self._coerce_int(config.get("height")),
                    )
                )
            elif action.type == "workflow":
                resolved.append(
                    ReportRowAction(
                        **common,
                        workflow_key=str(config.get("workflow_key") or "").strip() or None,
                        endpoint=self._resolve_text(str(config.get("endpoint_template") or ""), row) or None,
                        method=str(config.get("method") or "POST"),
                        payload=payload or None,
                        success_message=self._resolve_text(str(config.get("success_message") or ""), row) or None,
                        failure_message=self._resolve_text(str(config.get("failure_message") or ""), row) or None,
                        refresh_on_success=bool(config.get("refresh_on_success", True)),
                    )
                )
            elif action.type == "api":
                resolved.append(
                    ReportRowAction(
                        **common,
                        endpoint=self._resolve_text(str(config.get("endpoint_template") or ""), row) or None,
                        method=str(config.get("method") or "POST"),
                        payload=payload or None,
                        success_message=self._resolve_text(str(config.get("success_message") or ""), row) or None,
                        failure_message=self._resolve_text(str(config.get("failure_message") or ""), row) or None,
                        refresh_on_success=bool(config.get("refresh_on_success", True)),
                    )
                )
        return resolved

    def _resolve_payload(self, action: ReportAction, row: dict[str, Any]) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        for binding in action.bindings:
            if binding.source == "column":
                payload[binding.key] = row.get(binding.value)
            else:
                payload[binding.key] = self._resolve_text(binding.value, row)
        return payload

    def _resolve_text(self, value: str, row: dict[str, Any]) -> str:
        if not value:
            return ""
        return TEMPLATE_PATTERN.sub(lambda match: self._stringify(row.get(match.group(1))), value)

    def _is_visible(self, action: ReportAction, row: dict[str, Any]) -> bool:
        visibility = action.visibility
        if not visibility or not visibility.rules:
            return True
        matches = [self._rule_matches(rule.column, rule.operator, rule.value, row.get(rule.column)) for rule in visibility.rules]
        return all(matches) if visibility.match != "any" else any(matches)

    def _rule_matches(self, column: str, operator: str, value: Any, actual: Any) -> bool:
        normalized_actual = self._stringify(actual).strip()
        if operator == "eq":
            return normalized_actual == self._stringify(value).strip()
        if operator == "ne":
            return normalized_actual != self._stringify(value).strip()
        if operator == "in":
            candidates = value if isinstance(value, list) else str(value or "").split(",")
            return normalized_actual in {self._stringify(item).strip() for item in candidates}
        if operator == "not_in":
            candidates = value if isinstance(value, list) else str(value or "").split(",")
            return normalized_actual not in {self._stringify(item).strip() for item in candidates}
        if operator == "is_truthy":
            return bool(actual)
        if operator == "is_falsy":
            return not bool(actual)
        if operator == "is_empty":
            return normalized_actual == ""
        if operator == "not_empty":
            return normalized_actual != ""
        return True

    def _is_url_template(self, value: str) -> bool:
        stripped = value.strip()
        return stripped.startswith("/") or stripped.startswith("http://") or stripped.startswith("https://") or "{{" in stripped

    def _coerce_int(self, value: Any) -> int | None:
        try:
            if value in (None, ""):
                return None
            return int(value)
        except (TypeError, ValueError):
            return None

    def _stringify(self, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value)
