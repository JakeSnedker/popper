"""Attach isolated auth, diagnostic, and Strategy Forge features to FastAPI."""

from fastapi import FastAPI

from popper.api.auth_router import router as auth_router
from popper.api.diagnostics_router import router as diagnostics_router
from popper.api.fleet_router import router as fleet_router
from popper.api.forge_router import router as forge_router
from popper.security import TeamAuthMiddleware


def install_extensions(app: FastAPI) -> None:
    app.add_middleware(TeamAuthMiddleware)
    app.include_router(auth_router)
    app.include_router(diagnostics_router)
    app.include_router(fleet_router)
    app.include_router(forge_router)
