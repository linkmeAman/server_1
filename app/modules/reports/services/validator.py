"""Business validation for report draft and publish flows."""

from __future__ import annotations

import re

from app.modules.reports.schemas.models import (
    FilterOperator,
    ReportDefinition,
    ReportFieldError,
)

from .errors import ReportValidationException

IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class ReportDefinitionValidator:
    """Validate cross-field consistency that Pydantic shape checks cannot cover."""

    _OPERATORS_BY_TYPE: dict[str, set[FilterOperator]] = {
        "text": {"eq", "ne", "contains", "starts_with", "in", "is_null", "not_null"},
        "string": {"eq", "ne", "contains", "starts_with", "in", "is_null", "not_null"},
        "number": {"eq", "ne", "in", "between", "gte", "lte", "gt", "lt", "is_null", "not_null"},
        "integer": {"eq", "ne", "in", "between", "gte", "lte", "gt", "lt", "is_null", "not_null"},
        "float": {"eq", "ne", "in", "between", "gte", "lte", "gt", "lt", "is_null", "not_null"},
        "currency": {"eq", "ne", "in", "between", "gte", "lte", "gt", "lt", "is_null", "not_null"},
        "date": {"eq", "ne", "between", "gte", "lte", "gt", "lt", "is_null", "not_null"},
        "datetime": {"eq", "ne", "between", "gte", "lte", "gt", "lt", "is_null", "not_null"},
        "boolean": {"eq", "ne", "is_null", "not_null"},
    }

    def validate_draft(self, definition: ReportDefinition) -> None:
        errors = self._collect_errors(definition, publish=False)
        if errors:
            raise ReportValidationException(errors)

    def validate_publish(self, definition: ReportDefinition) -> None:
        errors = self._collect_errors(definition, publish=True)
        if errors:
            raise ReportValidationException(errors)

    def _collect_errors(self, definition: ReportDefinition, *, publish: bool) -> list[ReportFieldError]:
        errors: list[ReportFieldError] = []
        columns_by_key: dict[str, tuple[int, str]] = {}
        filters_by_key: set[str] = set()
        action_keys: set[str] = set()

        if definition.source.type != definition.kind:
            errors.append(
                self._error(
                    "source.type",
                    "invalid_value",
                    "Source type must match the selected report kind.",
                )
            )

        self._validate_kind_requirements(definition, errors, publish=publish)

        for index, column in enumerate(definition.columns):
            key = column.key.strip()
            if not self._is_identifier(key):
                errors.append(
                    self._error(
                        f"columns.{index}.key",
                        "invalid_value",
                        "Column keys must use letters, numbers, and underscores only.",
                    )
                )
                continue
            if key in columns_by_key:
                errors.append(
                    self._error(
                        f"columns.{index}.key",
                        "duplicate",
                        "Column keys must be unique within a report definition.",
                    )
                )
                continue
            columns_by_key[key] = (index, column.type.strip().lower() or "text")

        for index, report_filter in enumerate(definition.filters):
            filter_key = report_filter.key.strip().lower()
            if filter_key in filters_by_key:
                errors.append(
                    self._error(
                        f"filters.{index}.key",
                        "duplicate",
                        "Filter keys must be unique within a report definition.",
                    )
                )
            else:
                filters_by_key.add(filter_key)

            if not self._is_identifier(report_filter.column):
                errors.append(
                    self._error(
                        f"filters.{index}.column",
                        "invalid_value",
                        "Filter columns must use letters, numbers, and underscores only.",
                    )
                )
                continue

            column_meta = columns_by_key.get(report_filter.column)
            if column_meta is None:
                errors.append(
                    self._error(
                        f"filters.{index}.column",
                        "invalid_reference",
                        "Filters must reference a declared column.",
                    )
                )
                continue

            column_index, column_type = column_meta
            filter_type = report_filter.type.strip().lower() or column_type
            if filter_type != column_type:
                errors.append(
                    self._error(
                        f"filters.{index}.type",
                        "invalid_value",
                        f"Filter type must match the referenced column type ({column_type}).",
                    )
                )

            invalid_operators = [
                operator
                for operator in report_filter.operators
                if operator not in self._allowed_operators(column_type)
            ]
            if invalid_operators:
                errors.append(
                    self._error(
                        f"filters.{index}.operators",
                        "unsupported",
                        f"Operators {', '.join(invalid_operators)} are not allowed for column type {column_type}.",
                    )
                )

            if not definition.columns[column_index].visible and publish:
                # Hidden filter-only columns are valid, so publish should not fail here.
                pass

        for index, column_key in enumerate(definition.search_columns):
            if not self._is_identifier(column_key):
                errors.append(
                    self._error(
                        f"search_columns.{index}",
                        "invalid_value",
                        "Search columns must use letters, numbers, and underscores only.",
                    )
                )
                continue
            column_meta = columns_by_key.get(column_key)
            if column_meta is None:
                errors.append(
                    self._error(
                        f"search_columns.{index}",
                        "invalid_reference",
                        "Search columns must reference a declared column.",
                    )
                )
                continue
            column_index, _ = column_meta
            if not definition.columns[column_index].searchable:
                errors.append(
                    self._error(
                        f"search_columns.{index}",
                        "invalid_state",
                        "Search columns must point to columns marked searchable.",
                    )
                )

        for index, sort in enumerate(definition.default_sort):
            if not self._is_identifier(sort.column):
                errors.append(
                    self._error(
                        f"default_sort.{index}.column",
                        "invalid_value",
                        "Sort columns must use letters, numbers, and underscores only.",
                    )
                )
                continue
            column_meta = columns_by_key.get(sort.column)
            if column_meta is None:
                errors.append(
                    self._error(
                        f"default_sort.{index}.column",
                        "invalid_reference",
                        "Default sort must reference a declared column.",
                    )
                )
                continue
            column_index, _ = column_meta
            if not definition.columns[column_index].sortable:
                errors.append(
                    self._error(
                        f"default_sort.{index}.column",
                        "invalid_state",
                        "Default sort columns must be marked sortable.",
                    )
                )

        if definition.date_range.enabled:
            date_column = (definition.date_range.column or definition.source.date_column or "").strip()
            if not date_column:
                errors.append(
                    self._error(
                        "date_range.column",
                        "required",
                        "Enable date range only when a date column is configured.",
                    )
                )
            elif not self._is_identifier(date_column):
                errors.append(
                    self._error(
                        "date_range.column",
                        "invalid_value",
                        "Date range column must use letters, numbers, and underscores only.",
                    )
                )
            elif date_column not in columns_by_key:
                errors.append(
                    self._error(
                        "date_range.column",
                        "invalid_reference",
                        "Date range column must reference a declared column.",
                    )
                )

        if definition.branch_scope.mode == "token_branch":
            branch_column = (definition.branch_scope.column or definition.source.branch_column or "").strip()
            if not branch_column:
                errors.append(
                    self._error(
                        "branch_scope.column",
                        "required",
                        "Token-branch scope requires a branch column.",
                    )
                )
            elif not self._is_identifier(branch_column):
                errors.append(
                    self._error(
                        "branch_scope.column",
                        "invalid_value",
                        "Branch scope column must use letters, numbers, and underscores only.",
                    )
                )
            elif branch_column not in columns_by_key:
                errors.append(
                    self._error(
                        "branch_scope.column",
                        "invalid_reference",
                        "Branch scope column must reference a declared column.",
                    )
                )

        for index, action in enumerate(definition.actions):
            action_key = action.key.strip().lower()
            if action_key in action_keys:
                errors.append(
                    self._error(
                        f"actions.{index}.key",
                        "duplicate",
                        "Action keys must be unique within a report definition.",
                    )
                )
            else:
                action_keys.add(action_key)

            if action.route_template and not action.route_template.startswith("/"):
                errors.append(
                    self._error(
                        f"actions.{index}.route_template",
                        "invalid_value",
                        "Action routes must start with '/'.",
                    )
                )

        if publish and definition.kind == "table" and not any(column.visible for column in definition.columns):
            errors.append(
                self._error(
                    "columns",
                    "required",
                    "Publish requires at least one visible column.",
                )
            )

        return errors

    def _validate_kind_requirements(
        self,
        definition: ReportDefinition,
        errors: list[ReportFieldError],
        *,
        publish: bool,
    ) -> None:
        if definition.kind == "table":
            table_name = (definition.source.table or "").strip()
            if not table_name:
                errors.append(
                    self._error(
                        "source.table",
                        "required",
                        "Table reports require a source table.",
                    )
                )
            elif not self._is_identifier(table_name):
                errors.append(
                    self._error(
                        "source.table",
                        "invalid_value",
                        "Source table must use letters, numbers, and underscores only.",
                    )
                )

            for path, value in {
                "source.id_column": definition.source.id_column,
                "source.date_column": definition.source.date_column,
                "source.branch_column": definition.source.branch_column,
            }.items():
                if value and not self._is_identifier(value):
                    errors.append(
                        self._error(
                            path,
                            "invalid_value",
                            "Column names must use letters, numbers, and underscores only.",
                        )
                    )

            route_path = (definition.route_path or f"/reports/{definition.slug}").strip()
            if publish and not route_path.startswith("/"):
                errors.append(
                    self._error(
                        "route_path",
                        "invalid_value",
                        "Report routes must start with '/'.",
                    )
                )

        if definition.kind == "route":
            route_path = (definition.route_path or definition.source.route_path or "").strip()
            if not route_path:
                errors.append(
                    self._error(
                        "route_path",
                        "required",
                        "Route-backed reports require a route path.",
                    )
                )
            elif not route_path.startswith("/"):
                errors.append(
                    self._error(
                        "route_path",
                        "invalid_value",
                        "Route-backed report paths must start with '/'.",
                    )
                )

            if definition.source.table:
                errors.append(
                    self._error(
                        "source.table",
                        "invalid_state",
                        "Route-backed reports cannot define a source table.",
                    )
                )

            if publish and definition.source.route_path and not definition.source.route_path.startswith("/"):
                errors.append(
                    self._error(
                        "source.route_path",
                        "invalid_value",
                        "Route-backed source paths must start with '/'.",
                    )
                )

    def _allowed_operators(self, column_type: str) -> set[FilterOperator]:
        normalized = column_type.strip().lower()
        return self._OPERATORS_BY_TYPE.get(
            normalized,
            {"eq", "ne", "in", "is_null", "not_null"},
        )

    @staticmethod
    def _is_identifier(value: str) -> bool:
        return bool(value) and bool(IDENTIFIER_RE.match(value))

    @staticmethod
    def _error(path: str, code: str, message: str) -> ReportFieldError:
        return ReportFieldError(path=path, code=code, message=message)
