"""FastAPI application factory and process entry point."""

from fastapi import FastAPI

from app.api.routes.evidence import router as evidence_router
from app.api.routes.health import router as health_router
from app.api.routes.questionnaires import router as questionnaires_router
from app.api.routes.workspaces import router as workspaces_router
from app.config import Settings, get_settings
from app.db import create_db_engine, create_session_factory


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create the API application with explicit runtime dependencies."""

    runtime_settings = settings or get_settings()
    application = FastAPI(
        title=runtime_settings.app_name,
        version="0.1.0",
        description="EvidenceForge Phase 1B security boundary.",
    )
    application.state.settings = runtime_settings
    application.state.engine = create_db_engine(runtime_settings)
    application.state.session_factory = create_session_factory(application.state.engine)

    application.include_router(health_router)
    application.include_router(workspaces_router)
    application.include_router(evidence_router)
    application.include_router(questionnaires_router)

    @application.get("/", tags=["service"])
    def service_info() -> dict[str, str]:
        """Expose only static service metadata for local smoke checks."""

        return {"service": runtime_settings.app_name, "version": "0.1.0"}

    return application


app = create_app()