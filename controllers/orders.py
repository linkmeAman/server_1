"""Example explicit APIRouter endpoints for new development."""

from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/orders", tags=["orders"])


class CreateOrderRequest(BaseModel):
    customer: str
    amount: float


@router.get("/list")
async def list_orders(page: int = 1, status: Optional[str] = None):
    """List sample orders."""
    return {
        "items": [],
        "page": page,
        "status": status,
    }


@router.get("/get/{id}")
async def get_order(id: int):
    """Get a sample order by ID."""
    return {
        "id": id,
        "name": "sample order",
    }


@router.post("/create")
async def create_order(payload: CreateOrderRequest):
    """Create a sample order."""
    return {
        "created": True,
        "customer": payload.customer,
        "amount": payload.amount,
    }
