from typing import Any

from boto3.dynamodb.conditions import Key
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import Depends, FastAPI, HTTPException, Security, status
from mangum import Mangum

from app.config import Settings, get_settings
from app.deps import api_key_header, get_table, verify_api_key
from app.keys import gsi1_pk
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
def get_health(
    deep: bool = False,
    api_key: str | None = Security(api_key_header),
    settings: Settings = Depends(get_settings),
    table: Any = Depends(get_table),
):
    """Liveness by default; ?deep=true also proves the table is reachable.

    X-API-Key is required only for ?deep=true. OpenAPI cannot express a security
    requirement conditional on a query parameter, so the schema below lists the
    key against this route as though it always applied - plain GET /health does
    not need it, and monitoring should keep calling it without one.

    The shallow answer says only that the process is serving requests, which is
    what container and uptime monitoring needs. A client deciding whether to
    flush pending writes needs more than that: a missing table or a revoked IAM
    policy leaves the process perfectly healthy and every write failing, and
    without the deep probe the client discovers that one lost write at a time.
    """
    if not deep:
        return {"status": "ok"}

    # Liveness stays open so monitoring can reach it without a secret, but the
    # deep probe reads DynamoDB - unauthenticated, that is a free way for a
    # scanner to hold the Lambda warm and query the table on our bill. Reuse
    # verify_api_key rather than repeat its constant-time comparison here.
    verify_api_key(api_key, settings)

    try:
        table.query(
            IndexName="GSI1",
            KeyConditionExpression=Key("GSI1PK").eq(gsi1_pk()),
            Limit=1,
        )
    except (ClientError, BotoCoreError) as exc:
        # ClientError carries a code worth reporting - ResourceNotFoundException
        # for a missing table, AccessDeniedException for a broken IAM policy.
        # BotoCoreError covers the call never reaching DynamoDB at all, which is
        # what a stale DYNAMODB_ENDPOINT_URL produces. Both mean "do not flush
        # yet", which the client can act on; a 500 traceback it cannot.
        code = exc.response["Error"]["Code"] if isinstance(exc, ClientError) else type(exc).__name__
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"table unreachable: {code}",
        ) from exc
    return {"status": "ok", "table": "reachable"}


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
