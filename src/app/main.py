from fastapi import Depends, FastAPI
from mangum import Mangum

from app.config import get_settings
from app.deps import verify_api_key
from app.routers import logs, projects

settings = get_settings()

# The interactive docs expose the whole API surface to anyone who finds the
# URL, and they cannot sit behind the X-API-Key dependency without breaking the
# UI. Serve them everywhere except production.
_docs_enabled = settings.environment != "prod"

app = FastAPI(
    title="Side Project Tracker",
    description="API for tracking side projects and the work put into them",
    version="1.0.0",
    docs_url="/docs" if _docs_enabled else None,
    redoc_url="/redoc" if _docs_enabled else None,
    openapi_url="/openapi.json" if _docs_enabled else None,
)


@app.get("/health", tags=["health"], summary="Liveness check")
@app.get("/api/v1/health", tags=["health"], summary="Liveness check")
def get_health():
    """Unauthenticated health check endpoint for monitoring and container health."""
    return {"status": "ok"}


# Protected routes under /api/v1 requiring X-API-Key
app.include_router(
    projects.router,
    prefix="/api/v1",
    dependencies=[Depends(verify_api_key)],
)
app.include_router(
    logs.router,
    prefix="/api/v1",
    dependencies=[Depends(verify_api_key)],
)

handler = Mangum(app)
