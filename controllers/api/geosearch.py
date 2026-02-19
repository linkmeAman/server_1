"""Standard APIRouter endpoints mapped from legacy geosearch controller."""

from typing import Optional

from fastapi import APIRouter

from controllers import geosearch as legacy_geosearch

from ._responses import normalize_result

router = APIRouter(prefix="/api/geosearch", tags=["geosearch-standard"])


@router.get("/search")
async def search(
    location: Optional[str] = None,
    lat: Optional[float] = None,
    lng: Optional[float] = None,
    radius: float = 10.0,
    limit: int = 50,
):
    return normalize_result(
        legacy_geosearch.search(
            location=location,
            lat=lat,
            lng=lng,
            radius=radius,
            limit=limit,
        )
    )


@router.get("/health")
async def health():
    return normalize_result(legacy_geosearch.health())

