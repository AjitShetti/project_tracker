"""Tests for the health endpoint's optional deep mode.

The shallow response is a contract: monitoring parses it, so it must stay
exactly {"status": "ok"}. test_projects.py::test_health_endpoints pins that on
both routes without auth; the cases here cover the deep probe.
"""

import boto3
from fastapi.testclient import TestClient
from moto import mock_aws

from app.main import app

ROUTES = ("/health", "/api/v1/health")


def test_shallow_health_body_is_unchanged_by_the_deep_flag(client):
    """Adding a query parameter must not add a field to the liveness response."""
    for route in ROUTES:
        for url in (route, f"{route}?deep=false"):
            resp = client.get(url)
            assert resp.status_code == 200, url
            assert resp.json() == {"status": "ok"}, url


def test_deep_health_reports_the_table_reachable(client, auth_headers):
    """With a valid key, the deep probe proves GSI1 actually answers."""
    for route in ROUTES:
        resp = client.get(f"{route}?deep=true", headers=auth_headers)
        assert resp.status_code == 200, route
        assert resp.json() == {"status": "ok", "table": "reachable"}, route


def test_deep_health_requires_the_api_key(client):
    """The deep probe reads DynamoDB, so it may not be left open to scanners."""
    for route in ROUTES:
        unauthenticated = client.get(f"{route}?deep=true")
        assert unauthenticated.status_code == 401, route
        assert "Invalid or missing API key" in unauthenticated.json()["detail"]

        wrong_key = client.get(f"{route}?deep=true", headers={"X-API-Key": "wrong-key"})
        assert wrong_key.status_code == 401, route

    # Liveness itself stays reachable without a key, for monitoring.
    assert client.get("/health").status_code == 200


def test_deep_health_returns_503_when_the_table_is_missing(auth_headers):
    """A broken table must surface as "do not flush yet", not a 500 traceback.

    Deliberately no table in the mock: this is the shape of both a missing table
    and a revoked IAM policy, which is exactly what the shallow check cannot see.
    """
    with mock_aws():
        with TestClient(app) as unbacked_client:
            resp = unbacked_client.get("/health?deep=true", headers=auth_headers)

            assert resp.status_code == 503
            assert "ResourceNotFoundException" in resp.json()["detail"]

            # Liveness still answers while the table is gone - the process is up.
            assert unbacked_client.get("/health").json() == {"status": "ok"}


def test_deep_health_recovers_once_the_table_exists(auth_headers):
    """503 is a live reading, not a cached verdict."""
    with mock_aws():
        with TestClient(app) as unbacked_client:
            assert unbacked_client.get("/health?deep=true", headers=auth_headers).status_code == 503

            table = boto3.resource("dynamodb", region_name="ap-south-1").create_table(
                TableName="test-side-project-tracker",
                KeySchema=[
                    {"AttributeName": "PK", "KeyType": "HASH"},
                    {"AttributeName": "SK", "KeyType": "RANGE"},
                ],
                AttributeDefinitions=[
                    {"AttributeName": "PK", "AttributeType": "S"},
                    {"AttributeName": "SK", "AttributeType": "S"},
                    {"AttributeName": "GSI1PK", "AttributeType": "S"},
                    {"AttributeName": "GSI1SK", "AttributeType": "S"},
                ],
                GlobalSecondaryIndexes=[
                    {
                        "IndexName": "GSI1",
                        "KeySchema": [
                            {"AttributeName": "GSI1PK", "KeyType": "HASH"},
                            {"AttributeName": "GSI1SK", "KeyType": "RANGE"},
                        ],
                        "Projection": {"ProjectionType": "ALL"},
                    }
                ],
                BillingMode="PAY_PER_REQUEST",
            )
            table.wait_until_exists()

            recovered = unbacked_client.get("/health?deep=true", headers=auth_headers)
            assert recovered.status_code == 200
            assert recovered.json() == {"status": "ok", "table": "reachable"}
