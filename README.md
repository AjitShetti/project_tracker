# Side Project Tracker API

A lightweight, serverless single-user API for tracking personal side projects and the work invested into them. Built with **FastAPI**, backed by a single-table **DynamoDB** design, and deployed to **AWS Lambda** behind **API Gateway (HTTP API)** using **AWS SAM**.

---

## Architecture Overview

```
   HTTP Request
        │
        ▼
┌───────────────────┐
│  API Gateway      │  HTTP API — Routes requests to Lambda
│  (HTTP API)       │
└─────────┬─────────┘
          │  Lambda Event (Payload v2.0)
          ▼
┌───────────────────┐
│  Lambda Function  │
│  ┌─────────────┐  │
│  │   Mangum    │  │  Adapts AWS Lambda event to ASGI scope
│  ├─────────────┤  │
│  │   FastAPI   │  │  Routing, Pydantic validation, Auth dependency
│  ├─────────────┤  │
│  │ Repository  │  │  DynamoDB GetItem & Query calls (Zero Scans)
│  └─────────────┘  │
└────┬──────────┬───┘
     │          │
     │ IAM Role │ CloudWatch Logs (14-day retention)
     ▼          ▼
┌──────────┐  ┌──────────────┐
│ DynamoDB │  │ CloudWatch   │
│  Table   │  │    Logs      │
└──────────┘  └──────────────┘
```

---

## Data Model (Single-Table DynamoDB)

Table name: `side-project-tracker` (or CloudFormation generated `<stack-name>-table`).  
Billing Mode: `PAY_PER_REQUEST` (scales to zero, free tier friendly).

### Key Structure

| Entity | PK | SK | GSI1PK | GSI1SK | Attributes |
|---|---|---|---|---|---|
| **Project** | `PROJECT#<id>` | `METADATA` | `PROJECT` | `<status>#<updated_at>` | `project_id`, `name`, `description`, `status`, `tech_stack`, `repo_url`, `live_url`, `created_at`, `updated_at` |
| **Log Entry**| `PROJECT#<id>` | `LOG#<created_at>#<id>` | *(omitted)* | *(omitted)* | `log_id`, `project_id`, `note`, `hours_spent`, `created_at` |

### Access Patterns (100% Query & GetItem — No Scans)

1. **Get project with full log history**: `Query` PK = `PROJECT#<id>` (retrieves metadata item and all logs in 1 round trip).
2. **Get project metadata only**: `GetItem` PK = `PROJECT#<id>`, SK = `METADATA`.
3. **List logs newest-first**: `Query` PK = `PROJECT#<id>`, SK `begins_with("LOG#")`, `ScanIndexForward=False`.
4. **List all projects**: `Query` GSI1 with GSI1PK = `PROJECT`.
5. **List projects filtered by status**: `Query` GSI1 with GSI1PK = `PROJECT`, GSI1SK `begins_with("<status>#")`.

---

## API Surface

Base URL: `/api/v1`

| Method | Endpoint | Description | Auth Required |
|---|---|---|:---:|
| `GET` | `/health` | Liveness check | No |
| `POST` | `/api/v1/projects` | Create a new project | Yes (`X-API-Key`) |
| `GET` | `/api/v1/projects` | List projects (optional `?status=active`) | Yes (`X-API-Key`) |
| `GET` | `/api/v1/projects/{id}` | Get project (optional `?include_logs=true`) | Yes (`X-API-Key`) |
| `PATCH`| `/api/v1/projects/{id}` | Partial update (name, status, links) | Yes (`X-API-Key`) |
| `DELETE`| `/api/v1/projects/{id}` | Cascade delete project and its logs | Yes (`X-API-Key`) |
| `POST` | `/api/v1/projects/{id}/logs` | Append a log entry | Yes (`X-API-Key`) |
| `GET` | `/api/v1/projects/{id}/logs` | List logs (newest first) | Yes (`X-API-Key`) |

---

## Authentication & Security

All routes under `/api/v1` require the `X-API-Key` HTTP header. The secret is checked using constant-time comparison (`hmac.compare_digest`) to prevent timing side-channel attacks.

Exempted endpoints:
- `GET /health` (liveness monitoring)
- `/docs` and `/redoc` (OpenAPI documentation) — **served only when `ENVIRONMENT != prod`.**
  The docs cannot sit behind the `X-API-Key` dependency without breaking the UI, so rather
  than publish the whole API surface they are disabled on the deployed stack.

The shared secret is passed to Lambda as an environment variable. That is adequate for a
single-user project but is **not** a secret store: anyone with `lambda:GetFunctionConfiguration`
can read it in plaintext. The production-grade version keeps it in SSM Parameter Store
(SecureString) or Secrets Manager and fetches it once per cold start.

---

## Local Development & Testing

### 1. Prerequisites
- Python 3.12+ (or [uv](https://github.com/astral-sh/uv))
- Docker (optional, for running DynamoDB Local)

### 2. Install Dependencies
```bash
# Using uv (recommended)
uv sync --all-extras

# Or using pip in a virtual environment
pip install -e ".[dev]"
```

### 3. Run Automated Tests
Unit and integration tests use `moto` to mock DynamoDB completely in memory without requiring live AWS credentials or Docker:

```bash
uv run pytest -v
```

### 4. Run Locally with DynamoDB Local

Start DynamoDB Local using Docker:
```bash
docker compose up -d
```

Initialize the table and GSI1 on the local instance:
```bash
uv run python scripts/create_local_table.py
```

Copy the environment configuration:
```bash
cp .env.example .env
```

Start the FastAPI server:
```bash
uv run uvicorn app.main:app --app-dir src --reload --port 8001
```

Explore the interactive API docs at [http://localhost:8001/docs](http://localhost:8001/docs). Click **Authorize** and input the default key: `dev-secret-key`.

---

## AWS Deployment with SAM

### 0. Refresh the Lambda dependency manifest
Dependencies are declared in `pyproject.toml` and managed with uv, but SAM builds from a
`requirements.txt` inside `CodeUri`. Regenerate it whenever dependencies change:
```bash
uv export --no-dev --no-emit-project --no-hashes --format requirements-txt -o src/requirements.txt
```

### 1. Build the Lambda Package
```bash
sam build
```
`CodeUri` is `src/`, which contains only `app/` and `requirements.txt` — the virtualenv, tests
and caches at the repo root are deliberately outside the build root, since SAM copies the whole
`CodeUri` directory and does not honour `.gitignore`.

Confirm what was actually packaged before deploying:
```bash
ls .aws-sam/build/ProjectTrackerFunction
```

### 2. Deploy
For first-time deployment:
```bash
sam deploy --guided
```
SAM prompts for the stack name, region (e.g. `ap-south-1`), and whether to allow unauthenticated
API Gateway invocations (FastAPI handles authentication via `X-API-Key`).

`ApiKey` has no default and a 16-character minimum, so the deploy will fail rather than quietly
ship a publicly-known secret. Generate one and pass it explicitly — do not commit it to
`samconfig.toml`:
```bash
sam deploy --parameter-overrides ApiKey=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
```

Subsequent deployments:
```bash
sam deploy --parameter-overrides ApiKey=<your-key>
```

### 3. Teardown / Cleanup
To destroy all created AWS resources (Lambda, DynamoDB table, API Gateway, Log Groups):
```bash
sam delete
```
