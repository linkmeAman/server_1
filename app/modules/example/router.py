"""Standard APIRouter endpoints mapped from legacy example controller."""

from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from app.modules.example import service
from app.shared.response_normalization import normalize_result

router = APIRouter(prefix="/api/example", tags=["example-standard"])


class CalculateRequest(BaseModel):
    operation: str
    a: float
    b: float


class CreateUserRequest(BaseModel):
    name: str
    email: str
    status: str = "active"


class AsyncTaskRequest(BaseModel):
    duration: float = 1.0


@router.get("/hello")
async def hello(name: str = "World"):
    return normalize_result(service.hello(name=name))


@router.get("/echo")
async def echo(message: str):
    return normalize_result(service.echo(message=message))


@router.post("/calculate")
async def calculate(payload: CalculateRequest):
    return normalize_result(
        service.calculate(
            operation=payload.operation,
            a=payload.a,
            b=payload.b,
        )
    )


@router.get("/users")
async def users(page: int = 1, per_page: int = 10, status: Optional[str] = None):
    return normalize_result(
        service.users(page=page, per_page=per_page, status=status)
    )


@router.get("/user/{id}")
async def user(id: int):
    return normalize_result(service.user(id=id))


@router.post("/create_user")
async def create_user(payload: CreateUserRequest):
    return normalize_result(
        service.create_user(
            name=payload.name,
            email=payload.email,
            status=payload.status,
        )
    )


@router.get("/random_data")
async def random_data(count: int = 5):
    return normalize_result(service.random_data(count=count))


@router.post("/async_task")
async def async_task(payload: AsyncTaskRequest):
    return normalize_result(await service.async_task(duration=payload.duration))


@router.get("/status")
async def status():
    return normalize_result(service.status())

