from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import web
from app.config import Settings, get_settings
from app.api import errors as api_errors
from app.api.cohorts import router as cohorts_router
from app.api.consent_routes import router as consent_router
from app.api.demo_routes import router as demo_router
from app.api.insights_routes import router as insights_router
from app.api.pathway_routes import router as pathway_router
from app.api.profile_routes import router as profile_router
from app.api.profile_reset_routes import router as profile_reset_router
from app.api.entry_routes import router as entry_router
from app.api.capability_routes import router as capability_router
from app.api.transition_routes import router as transition_router
from app.api.company_routes import router as company_router
from app.api.career_origins_routes import router as career_origins_router
from app.api.real_resume_routes import router as real_resume_router
from app.api.resume_import_routes import router as resume_import_router
from app.api.routes import router as api_router
from app.api.sharing_routes import router as sharing_router
from app.routers import health


def create_app() -> FastAPI:
    settings = get_settings()
    docs = settings.trace_api_docs_enabled                     # off unless explicitly enabled (local development)
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        worker = None
        current = _app.dependency_overrides.get(get_settings, Settings)()          # read at startup, not at import
        if current.trace_contribution_worker and current.database_runtime_url is not None:
            from app.services.contributions.worker import ContributionWorker
            worker = ContributionWorker(current, current.trace_contribution_worker_interval)
            worker.start()
        try:
            yield
        finally:
            if worker:
                worker.stop()

    app = FastAPI(title=settings.app_name, docs_url="/docs" if docs else None, redoc_url="/redoc" if docs else None,
                  openapi_url="/openapi.json" if docs else None, lifespan=lifespan)
    app.include_router(health.router)
    app.include_router(api_router)
    app.include_router(profile_reset_router)
    app.include_router(profile_router)
    app.include_router(entry_router)
    app.include_router(capability_router)
    app.include_router(pathway_router)
    app.include_router(transition_router)
    app.include_router(company_router)
    app.include_router(career_origins_router)
    app.include_router(insights_router)
    app.include_router(demo_router)
    app.include_router(consent_router)
    app.include_router(cohorts_router)
    app.include_router(real_resume_router)
    app.include_router(resume_import_router)
    app.include_router(sharing_router)
    api_errors.register(app)
    web.install(app)
    return app


app = create_app()
