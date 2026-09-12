"""Exercise the real Lambda entrypoint.

TestClient speaks ASGI directly, so it never touches Mangum. These tests feed
the handler an API Gateway HTTP API payload-format-2.0 event, which is what
actually arrives in production.
"""

import json

from app.main import handler


def _event(method: str, path: str, headers: dict | None = None, body: str | None = None) -> dict:
    return {
        "version": "2.0",
        "routeKey": f"{method} {path}",
        "rawPath": path,
        "rawQueryString": "",
        "headers": {"host": "api.example.com", **(headers or {})},
        "requestContext": {
            "accountId": "123456789012",
            "apiId": "abc123",
            "domainName": "api.example.com",
            "http": {
                "method": method,
                "path": path,
                "protocol": "HTTP/1.1",
                "sourceIp": "203.0.113.1",
                "userAgent": "pytest",
            },
            "requestId": "req-1",
            "stage": "$default",
            "time": "12/Sep/2026:00:00:00 +0000",
            "timeEpoch": 1789200000,
        },
        "body": body,
        "isBase64Encoded": False,
    }


class _Context:
    function_name = "project-tracker"
    memory_limit_in_mb = 512
    invoked_function_arn = "arn:aws:lambda:ap-south-1:123456789012:function:project-tracker"
    aws_request_id = "req-1"

    def get_remaining_time_in_millis(self):
        return 15000


def test_handler_serves_health_through_mangum():
    """The Mangum adapter must translate a v2.0 event into an ASGI request."""
    response = handler(_event("GET", "/health"), _Context())

    assert response["statusCode"] == 200
    assert json.loads(response["body"]) == {"status": "ok"}


def test_handler_enforces_api_key():
    """Auth must work over the Lambda path, not just the ASGI path."""
    unauthorized = handler(_event("GET", "/api/v1/projects"), _Context())
    assert unauthorized["statusCode"] == 401


def test_handler_round_trips_a_project(dynamodb_mock):
    """Full create-then-read cycle driven entirely through Lambda events."""
    create = handler(
        _event(
            "POST",
            "/api/v1/projects",
            headers={"x-api-key": "test-secret-key", "content-type": "application/json"},
            body=json.dumps({"name": "Via Lambda", "status": "active"}),
        ),
        _Context(),
    )
    assert create["statusCode"] == 201, create
    project_id = json.loads(create["body"])["project_id"]

    fetched = handler(
        _event(
            "GET",
            f"/api/v1/projects/{project_id}",
            headers={"x-api-key": "test-secret-key"},
        ),
        _Context(),
    )
    assert fetched["statusCode"] == 200
    assert json.loads(fetched["body"])["name"] == "Via Lambda"
