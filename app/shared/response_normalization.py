"""Response normalization helpers for explicit routers."""

from typing import Any

from fastapi import Response

from app.core.response import APIResponse, success_response


def normalize_result(result: Any) -> Any:
    """Mirror legacy dynamic response wrapping for explicit routes."""
    if isinstance(result, Response):
        return result

    if isinstance(result, APIResponse):
        return result.model_dump(mode="json")

    if isinstance(result, dict) and "success" in result:
        return result

    return success_response(data=result).model_dump(mode="json")

