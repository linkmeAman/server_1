"""Auto-discovery for explicit APIRouter modules."""

import importlib
import logging
import pkgutil

from fastapi import APIRouter, FastAPI

logger = logging.getLogger(__name__)


def _is_private_module(module_name: str, package: str) -> bool:
    """Return True when any module path component is private (starts with '_')."""
    if module_name == package:
        return False

    if module_name.startswith(f"{package}."):
        relative_name = module_name[len(package) + 1 :]
    else:
        relative_name = module_name

    return any(part.startswith("_") for part in relative_name.split("."))


def include_routers(app: FastAPI, package: str = "controllers") -> None:
    """
    Include APIRouter instances discovered under a package.

    The function imports every module/submodule under ``package`` and includes
    ``router`` if it exists and is an APIRouter instance.
    """
    try:
        package_module = importlib.import_module(package)
    except Exception:
        logger.exception("Could not import router package '%s'", package)
        return

    package_router = getattr(package_module, "router", None)
    if isinstance(package_router, APIRouter):
        app.include_router(package_router)
        logger.info("Included APIRouter from module: %s", package)

    package_paths = getattr(package_module, "__path__", None)
    if package_paths is None:
        logger.warning("Package '%s' has no __path__; skipping auto-discovery", package)
        return

    for module_info in pkgutil.walk_packages(package_paths, f"{package}."):
        module_name = module_info.name

        if _is_private_module(module_name, package):
            logger.debug("Skipping private router module: %s", module_name)
            continue

        try:
            module = importlib.import_module(module_name)
        except Exception:
            logger.exception("Failed importing router module '%s'", module_name)
            continue

        module_router = getattr(module, "router", None)
        if isinstance(module_router, APIRouter):
            app.include_router(module_router)
            logger.info("Included APIRouter from module: %s", module_name)
