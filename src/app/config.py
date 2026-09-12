import os
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

# Lambda injects AWS_LAMBDA_FUNCTION_NAME into every execution environment and
# nothing else does, so it is a reliable "am I running in Lambda?" probe.
# In Lambda the only configuration source is the function's environment
# variables; a .env accidentally bundled into the deployment package must never
# be read, or a local value like DYNAMODB_ENDPOINT_URL=http://localhost:8000
# would silently redirect every DynamoDB call to nowhere.
IN_LAMBDA = bool(os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))


class Settings(BaseSettings):
    table_name: str = "side-project-tracker"
    dynamodb_endpoint_url: str | None = None
    api_key: str = "dev-secret-key"
    aws_region: str = "ap-south-1"
    environment: str = "local"

    model_config = SettingsConfigDict(
        env_file=None if IN_LAMBDA else ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
