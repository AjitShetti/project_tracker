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

### Status Vocabulary

`status` is stored as exactly one of five values — `idea`, `active`, `paused`, `shipped`,
`abandoned` — because `GSI1SK` is `<status>#<updated_at>` and every stored state becomes a
partition of the status filter. Adding a sixth state is a data-model change; adding a *word*
for an existing state is not.

So the API accepts synonyms on input and normalises them before anything is written. Matching
folds case, spaces and hyphens, which means `"under review"`, `"Under Review"` and
`"under-review"` are the same word. This applies to `POST /projects`, `PATCH /projects/{id}`
and the `?status=` list filter alike, so a voice client, a phone shortcut and curl all speak
the same vocabulary:

| Stored value | Also accepted |
|---|---|
| `idea` | `to_build`, `under_review`, `someday`, `maybe`, `backlog`, `planned` |
| `active` | `building`, `in_progress`, `wip`, `started` |
| `paused` | `parked`, `shelved`, `on_hold` |
| `shipped` | `done`, `live`, `launched`, `complete` |
| `abandoned` | `dead`, `dropped`, `killed` |

An unrecognised word is still a `422` — this widens the accepted vocabulary, it does not turn
`status` into a free-text field. New synonyms go in `_STATUS_SYNONYMS` in `src/app/models.py`,
which is the single place any client's wording is taught.

### Access Patterns (100% Query & GetItem — No Scans)

1. **Get project with full log history**: `Query` PK = `PROJECT#<id>` (retrieves metadata item and all logs in 1 round trip).
2. **Get project metadata only**: `GetItem` PK = `PROJECT#<id>`, SK = `METADATA`.
3. **List logs newest-first**: `Query` PK = `PROJECT#<id>`, SK `begins_with("LOG#")`, `ScanIndexForward=False`.
4. **List all projects**: `Query` GSI1 with GSI1PK = `PROJECT`.
5. **List projects filtered by status**: `Query` GSI1 with GSI1PK = `PROJECT`, GSI1SK `begins_with("<status>#")`.

`GET /health?deep=true` reuses pattern 4 with `Limit=1` (~0.5 RCU) purely to prove the table is
reachable. A missing table or a revoked IAM policy leaves the process healthy and every write
failing, so the probe answers `503 table unreachable: <code>` — something a client can act on
before it flushes pending writes.

---

## API Surface

Base URL: `/api/v1`

| Method | Endpoint | Description | Auth Required |
|---|---|---|:---:|
| `GET` | `/health` | Liveness check | No |
| `GET` | `/health?deep=true` | Liveness **and** a `Limit=1` GSI1 query proving the table answers | Yes (`X-API-Key`) |
| `POST` | `/api/v1/projects` | Create a new project | Yes (`X-API-Key`) |
| `GET` | `/api/v1/projects` | List projects (optional `?status=active`) | Yes (`X-API-Key`) |
| `GET` | `/api/v1/projects/{id}` | Get project (optional `?include_logs=true`) | Yes (`X-API-Key`) |
| `PATCH`| `/api/v1/projects/{id}` | Partial update (name, status, links) | Yes (`X-API-Key`) |
| `DELETE`| `/api/v1/projects/{id}` | Cascade delete project and its logs | Yes (`X-API-Key`) |
| `POST` | `/api/v1/projects/{id}/logs` | Append a log entry | Yes (`X-API-Key`) |
| `GET` | `/api/v1/projects/{id}/logs` | List logs (newest first) | Yes (`X-API-Key`) |

### Log Entry Body

`note` is the only required field. `hours_spent` defaults to `0.0` and `created_at` to now:

```jsonc
// Time worked
{ "note": "Wired up the GSI1 status filter", "hours_spent": 2.5 }

// A thought, with no time attached — omitting hours_spent beats sending 0,
// which would record a measurement that was never taken.
{ "note": "Idea: cache the table resource across warm invocations" }
```

`hours_spent` must be `>= 0` when sent, and always comes back on the way out.

---

## Authentication & Security

All routes under `/api/v1` require the `X-API-Key` HTTP header. The secret is checked using constant-time comparison (`hmac.compare_digest`) to prevent timing side-channel attacks.

Exempted endpoints:
- `GET /health` (liveness monitoring) — **except `?deep=true`**, which reads DynamoDB and so
  takes the key. Unauthenticated, the deep probe would be a free way for a scanner to hold the
  Lambda warm and query the table; the shallow path stays open so uptime monitoring needs no
  secret. The key is an optional dependency checked inside the handler rather than a route
  dependency, because one route serves both depths.
- `/docs` and `/redoc` (OpenAPI documentation) — **served only when `ENVIRONMENT != prod`.**
  The docs cannot sit behind the `X-API-Key` dependency without breaking the UI, so rather
  than publish the whole API surface they are disabled on the deployed stack.

### Disabling the check locally

Setting `API_KEY=""` disables the header check entirely, so a local run against DynamoDB Local
needs no header at all:

```bash
API_KEY= uv run uvicorn app.main:app --app-dir src --reload --port 8001
```

With an empty key, `verify_api_key` returns before it compares anything — no header, a wrong
header and a valid header all succeed. The default stays `dev-secret-key`, so this is opt-in.

**This is deliberately impossible on a deployed stack.** An API Gateway URL is public, and an
open one leaves every project readable, rewritable and `DELETE`-able by anyone who finds it. So
the `ApiKey` parameter in `template.yaml` keeps `MinLength: 16` and there is no condition
permitting an empty value: CloudFormation rejects the deploy rather than shipping an open API.
Local can be open, deployed cannot — that asymmetry is the whole design, so don't "fix" the
template to make a deploy easier.

One gap worth knowing: the guard covers the *deploy* path. Editing `API_KEY` to empty directly
on the Lambda function (console or `update-function-configuration`) would still disable auth on
a live public URL, and nothing in the app refuses to serve in that state.

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

Deploy the first time with `Environment=dev`. `/docs` is disabled whenever `ENVIRONMENT=prod`,
and the interactive docs are the fastest way to confirm routes and auth while wiring up a
client:
```bash
sam deploy --parameter-overrides ApiKey=<your-key> Environment=dev
```

Once the client works, redeploy with `Environment=prod` to take the docs back down — they
otherwise publish the whole API surface to anyone who finds the URL:
```bash
sam deploy --parameter-overrides ApiKey=<your-key> Environment=prod
```

Subsequent deployments:
```bash
sam deploy --parameter-overrides ApiKey=<your-key> Environment=prod
```

### 3. Point a client at the deployment
The `ApiUrl` output is the **bare base URL, with no path suffix** — the HTTP API uses the
`$default` stage, so there is no stage segment either:

```
https://<api-id>.execute-api.<region>.amazonaws.com
```

Give a client exactly that. It appends `/api/v1/...` itself, so a base URL that already ends in
`/api/v1` produces `/api/v1/api/v1/projects` and a 404 that looks like a routing bug:
```bash
sam list stack-outputs --stack-name <stack-name> --output json
```

### 4. Teardown / Cleanup
To destroy all created AWS resources (Lambda, DynamoDB table, API Gateway, Log Groups):
```bash
sam delete
```
