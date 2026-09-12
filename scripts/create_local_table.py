"""Script to create the DynamoDB table and GSI1 on DynamoDB Local or AWS.

Run this script to initialize the table structure before running the application
locally.
"""

import os
import sys

import boto3
from botocore.exceptions import ClientError

TABLE_NAME = os.environ.get("TABLE_NAME", "side-project-tracker")
ENDPOINT_URL = os.environ.get("DYNAMODB_ENDPOINT_URL", "http://localhost:8000")
REGION_NAME = os.environ.get("AWS_REGION", "ap-south-1")


def create_table():
    print(f"Connecting to DynamoDB at {ENDPOINT_URL} (Region: {REGION_NAME})...")
    dynamodb = boto3.resource(
        "dynamodb",
        endpoint_url=ENDPOINT_URL,
        region_name=REGION_NAME,
        aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID", "local"),
        aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY", "local"),
    )

    try:
        table = dynamodb.create_table(
            TableName=TABLE_NAME,
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
        print(f"Creating table '{TABLE_NAME}'...")
        table.wait_until_exists()
        print(f"Table '{TABLE_NAME}' created successfully!")
    except ClientError as e:
        error_code = e.response["Error"]["Code"]
        if error_code == "ResourceInUseException":
            print(f"Table '{TABLE_NAME}' already exists.")
        else:
            print(f"Error creating table: {e}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    create_table()
