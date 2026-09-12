import hmac
from typing import Any

import boto3
from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader

from app.config import Settings, get_settings

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def verify_api_key(
    api_key: str | None = Security(api_key_header),
    settings: Settings = Depends(get_settings),
) -> str:
    """Verify X-API-Key header using constant-time comparison."""
    if not api_key or not hmac.compare_digest(api_key, settings.api_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
        )
    return api_key


def get_dynamodb_resource(settings: Settings = Depends(get_settings)) -> Any:
    """Initialize and return boto3 DynamoDB resource."""
    kwargs: dict[str, Any] = {"region_name": settings.aws_region}
    if settings.dynamodb_endpoint_url:
        kwargs["endpoint_url"] = settings.dynamodb_endpoint_url
    return boto3.resource("dynamodb", **kwargs)


def get_table(
    settings: Settings = Depends(get_settings),
    dynamodb: Any = Depends(get_dynamodb_resource),
) -> Any:
    """Return DynamoDB Table resource."""
    return dynamodb.Table(settings.table_name)
