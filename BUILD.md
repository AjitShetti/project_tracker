# Side Project Tracker API — Build Guide

A single-user API for tracking your side projects and the work you put into them.
Built with FastAPI, stored in DynamoDB, deployed to AWS Lambda behind API Gateway.

**Primary goal:** ship a working API.
**Secondary goal (the real one):** learn how AWS fits together — IAM, Lambda, API Gateway, DynamoDB, CloudWatch, and infrastructure-as-code.

---

## 1. What we're building

You start side projects. Some ship, most stall. This API is the system of record for them:
what you're working on, what state each project is in, and a running log of what you did and when.

Two things exist in the system:

- **Project** — the thing you're building. Has a name, a status, a tech stack, links.
- **Log entry** — a dated note attached to a project. "Got auth working, 2 hours."

That's it. Resisting a third entity is deliberate — the AWS learning is the hard part, and a
bloated data model will bury it.

### Scope

In scope:
- CRUD for projects
- Append + list log entries per project
- Filter projects by status
- Deployed, publicly reachable, protected by a shared secret
- Defined entirely in code (infrastructure included)

Explicitly out of scope for v1:
- Multi-user accounts, login, Cognito
- A frontend
- File uploads, notifications, background jobs
- CI/CD pipelines

---

## 2. The stack, and why

### Compute: AWS Lambda + API Gateway (HTTP API)

You asked for a recommendation. This is it, for three reasons:

**Cost.** This is the one that matters most when you're new to AWS. Lambda scales to zero — if
nobody calls your API, you pay nothing. The alternatives don't:

| Option | Idle cost | Notes |
|---|---|---|
| Lambda + API Gateway | ~$0 | Pay per request; free tier covers personal use |
| App Runner | ~$5–7/mo | Charges for a provisioned container even when idle |
| ECS Fargate + ALB | ~$25–35/mo | Task runs 24/7, plus load balancer charges |

For an API you'll hit a few dozen times a day, paying $30/month is absurd.

**It pairs naturally with DynamoDB.** Both are serverless, both authenticate via IAM roles rather
than connection strings and passwords, and neither needs a VPC. You'll never configure a subnet or
a security group in this project — which, as a first AWS build, is a gift.

**It teaches the most transferable fundamentals.** IAM roles and policies, CloudWatch logs,
CloudFormation stacks, per-function permissions. These show up in every AWS job regardless of
compute choice.

**The honest tradeoffs:**
- *Cold starts.* First request after idle takes roughly 1–3 seconds while Lambda boots your
  Python runtime. Subsequent requests are fast. For a personal tracker this is a non-issue; for a
  latency-sensitive product it would matter.
- *An adapter sits in the middle.* Lambda speaks events, not HTTP. Mangum translates between them.
  It's ~1 line of code, but it means your local server and your deployed function aren't running
  through quite the same path.
- *15-minute execution ceiling.* Irrelevant here, but worth knowing.

### API framework: FastAPI

Pydantic gives you request validation for free, and `/docs` gives you an interactive OpenAPI UI
without writing any of it.

### Database: DynamoDB

Your call, and a good one to pair with Lambda. Be aware that DynamoDB is not a relational database
and punishes you for treating it like one. Section 5 covers the modelling approach.

### Infrastructure as code: AWS SAM

Given zero AWS background, SAM (Serverless Application Model) is the gentlest real on-ramp:

- It's CloudFormation with serverless shortcuts. Your whole stack fits in ~70 lines of YAML.
- `sam deploy --guided` walks you through your first deploy interactively.
- `sam local start-api` runs the real Lambda path on your machine.
- `sam delete` tears everything down in one command — which is how you avoid a surprise bill.

Why not the others:
- **CDK** is more powerful, but adds a compile step (Python → CloudFormation), an `npm`
  dependency, and a bootstrap process. More concepts before your first deploy.
- **Terraform** is a genuinely valuable skill, but Lambda packaging in Terraform is clunky and
  there's no local-invoke equivalent.
- **Clicking through the console** feels faster on day one and is a trap. You'll create resources
  you forget about, can't reproduce your setup, and can't reliably delete it. Use the console to
  *look* at what SAM created — not to create things.

---

## 3. Architecture

```
   HTTP request
        │
        ▼
┌───────────────────┐
│  API Gateway      │  HTTP API — routes everything to one Lambda
│  (HTTP API)       │
└─────────┬─────────┘
          │  Lambda event (payload format 2.0)
          ▼
┌───────────────────┐
│  Lambda Function  │
│  ┌─────────────┐  │
│  │   Mangum    │  │  event ──▶ ASGI scope
│  ├─────────────┤  │
│  │   FastAPI   │  │  routing, validation, serialization
│  ├─────────────┤  │
│  │ repository  │  │  boto3 calls
│  └─────────────┘  │
└────┬──────────┬───┘
     │          │
     │ IAM role │ logs
     ▼          ▼
┌──────────┐  ┌──────────────┐
│ DynamoDB │  │ CloudWatch   │
│  table   │  │    Logs      │
└──────────┘  └──────────────┘
```

Note what's *absent*: no VPC, no load balancer, no servers, no connection pool, no database
password. Lambda assumes an IAM role that grants it access to exactly one DynamoDB table, and
credentials are injected and rotated automatically.

---

## 4. What this will actually cost

Worth internalizing before you deploy anything, because "I'm scared of the bill" is the single
biggest thing that stops people from learning AWS.

At personal-project volume (a few hundred requests a day, a few MB of data), you land inside the
free tier on every service here and the bill rounds to **$0.00**. Roughly:

- **Lambda** — perpetual free tier of 1M requests and 400,000 GB-seconds per month.
- **API Gateway (HTTP API)** — 1M requests/month free for your first 12 months, then ~$1 per
  million. HTTP API is roughly a third the price of the older REST API; use HTTP API.
- **DynamoDB** — 25 GB of storage free. On-demand billing charges per request, at fractions of a
  cent per million for a table this small.
- **CloudWatch Logs** — 5 GB of ingestion free per month.

Pricing changes; check the AWS pricing pages rather than trusting these numbers a year from now.

**Three things that actually protect you:**

1. Set a **zero-spend budget alert** before you deploy (Section 8). Free, takes two minutes.
2. Set **log retention** on your CloudWatch log group. The default is *never expire*, and logs you
   forget about are the most common way a "free" project starts costing money.
3. Run **`sam delete`** when you're done experimenting. One command, everything gone.

---

## 5. Data model

### The mental shift

In Postgres you model your data, then write whatever queries you need. In DynamoDB you **enumerate
your queries first, then design the table to serve them.** Ad-hoc querying isn't a thing. A `Scan`
(read every item, filter in memory) is the escape hatch, and it's the thing you're learning to
avoid.

### Access patterns

Write these down before writing any code. This list *is* the design.

| # | I need to... | How |
|---|---|---|
| 1 | Get one project with all its log entries | `Query` PK = `PROJECT#<id>` |
| 2 | Get one project's metadata only | `GetItem` PK = `PROJECT#<id>`, SK = `METADATA` |
| 3 | List a project's log entries, newest first | `Query` PK = `PROJECT#<id>`, SK `begins_with('LOG#')`, descending |
| 4 | List all projects | `Query` GSI1, GSI1PK = `PROJECT` |
| 5 | List projects with a given status | `Query` GSI1, GSI1PK = `PROJECT`, GSI1SK `begins_with('<status>#')` |

### Table design (single-table)

One table holds both entity types. Table name: `side-project-tracker`.

- **Partition key:** `PK` (string)
- **Sort key:** `SK` (string)
- **Billing mode:** on-demand (`PAY_PER_REQUEST`) — no capacity planning, scales to zero

**Project item:**

```
PK      = PROJECT#<project_id>
SK      = METADATA
GSI1PK  = PROJECT
GSI1SK  = <status>#<updated_at>

project_id, name, description, status, tech_stack[], repo_url, live_url,
created_at, updated_at
```

**Log entry item:**

```
PK  = PROJECT#<project_id>
SK  = LOG#<created_at_iso>#<short_uuid>

log_id, project_id, note, hours_spent, created_at
```

`status` is one of: `idea`, `active`, `paused`, `shipped`, `abandoned`.

### Why this shape

Both item types share the partition key `PROJECT#<id>`, so one `Query` returns the project and its
entire log history in a single round trip — pattern 1, for free. The `LOG#` prefix on the sort key
means `begins_with` cleanly separates logs from metadata, and because the sort key starts with an
ISO-8601 timestamp, lexicographic ordering *is* chronological ordering. Reverse the scan direction
and you get newest-first with no sorting in your application code.

**GSI1** exists purely to answer "list my projects." Every project item carries the literal string
`PROJECT` as `GSI1PK`, which puts all of them in one partition you can query. `GSI1SK` is
`<status>#<updated_at>`, so `begins_with('active#')` filters by status and results come back
ordered by recency within each status.

Be aware this is the "constant partition key" pattern, and at scale it's an antipattern — every
write hits one partition, which becomes a hot spot. With a few dozen projects it's completely
fine, and you should know *why* it's fine so you recognize when it stops being fine.

Note also that log entries have no GSI attributes at all. Global secondary indexes are sparse: an
item missing the index keys simply isn't in the index. That's a feature, and it keeps GSI1 small.

---

## 6. API surface

Base path: `/api/v1`

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Liveness check (unauthenticated) |
| `POST` | `/projects` | Create a project |
| `GET` | `/projects` | List projects, optional `?status=active` |
| `GET` | `/projects/{id}` | Get one project |
| `GET` | `/projects/{id}?include_logs=true` | Project plus full log history |
| `PATCH` | `/projects/{id}` | Partial update (name, status, links...) |
| `DELETE` | `/projects/{id}` | Delete project and all its logs |
| `POST` | `/projects/{id}/logs` | Append a log entry |
| `GET` | `/projects/{id}/logs` | List log entries, newest first |

Conventions worth holding to:
- Timestamps are ISO-8601 UTC strings, always.
- `404` when a project doesn't exist; don't return an empty `200`.
- `PATCH` semantics — only fields present in the body get written. Pydantic's
  `model_dump(exclude_unset=True)` gives you this.
- `DELETE` cascades: query all items under `PK = PROJECT#<id>` and `BatchWriteItem` them away.

---

## 7. Project structure

```
side-project-tracker/
├── BUILD.md
├── README.md
├── template.yaml              # SAM: all AWS infrastructure
├── samconfig.toml             # generated by `sam deploy --guided`
├── pyproject.toml
├── .env.example
├── docker-compose.yml         # DynamoDB Local for development
├── scripts/
│   └── create_local_table.py  # creates the table in DynamoDB Local
├── app/
│   ├── __init__.py
│   ├── main.py                # FastAPI app + Mangum handler
│   ├── config.py              # settings from env vars
│   ├── deps.py                # API key check, DynamoDB client
│   ├── models.py              # Pydantic request/response schemas
│   ├── keys.py                # PK/SK construction — one place only
│   ├── repository.py          # every boto3 call lives here
│   └── routers/
│       ├── projects.py
│       └── logs.py
└── tests/
    ├── conftest.py
    ├── test_projects.py
    └── test_logs.py
```

Two structural rules that will save you pain:

**All key construction lives in `keys.py`.** Functions like `project_pk(project_id)` and
`log_sk(created_at, log_id)`. The moment you have f-strings building `PROJECT#...` scattered across
three files, a typo in one becomes an item you can write but never read.

**All boto3 lives in `repository.py`.** Routers call repository functions, never `boto3` directly.
This keeps DynamoDB's quirks — `ExpressionAttributeNames`, `Decimal` types, pagination — contained,
and makes the routers testable.

### Dependencies

```toml
[project]
requires-python = ">=3.12"
dependencies = [
    "fastapi",
    "mangum",
    "pydantic",
    "pydantic-settings",
    "boto3",
]

[project.optional-dependencies]
dev = ["uvicorn", "pytest", "httpx", "moto[dynamodb]", "ruff"]
```

`boto3` ships in the Lambda runtime, but pin it here anyway so local and deployed behaviour match.

### The handler

The entire Lambda adapter:

```python
# app/main.py
from fastapi import FastAPI
from mangum import Mangum

app = FastAPI(title="Side Project Tracker", docs_url="/docs")
# ... include routers ...

handler = Mangum(app)
```

`handler` is what you point SAM at. Locally, `uvicorn app.main:app` ignores it entirely.

---

## 8. Build plan

Four phases. Each ends somewhere you could stop and still have something working.

### Phase 0 — FastAPI, in memory (no AWS at all)

Goal: a complete, working API backed by a Python dict.

1. Scaffold the project, install dependencies.
2. Write `models.py` — `ProjectCreate`, `ProjectUpdate`, `ProjectOut`, `LogCreate`, `LogOut`.
3. Implement every endpoint against a module-level dict.
4. Open `http://localhost:8000/docs` and exercise all of them by hand.
5. Write tests against the in-memory implementation.

Debugging API design and debugging AWS at the same time is miserable. Get the API right first.

### Phase 1 — DynamoDB, locally

Goal: same API, real DynamoDB semantics, still zero AWS account involvement.

1. Run DynamoDB Local:

   ```yaml
   # docker-compose.yml
   services:
     dynamodb:
       image: amazon/dynamodb-local
       ports: ["8000:8000"]
       command: ["-jar", "DynamoDBLocal.jar", "-sharedDb", "-inMemory"]
   ```

   (It defaults to port 8000, same as uvicorn — run uvicorn on 8001, or remap.)

2. `scripts/create_local_table.py` — create the table and GSI1 via boto3.
3. Write `keys.py`, then `repository.py`, one access pattern at a time.
4. Swap the routers from the dict to the repository.
5. Re-run your Phase 0 tests. They should pass unchanged — that's the payoff of having written
   them against the API surface rather than the storage layer.

Things that will bite you here, so expect them:
- DynamoDB returns numbers as `Decimal`, and `json` can't serialize them. Convert at the
  repository boundary.
- Empty strings were historically rejected in key attributes. Don't store `""` in `PK`/`SK`.
- Reserved words (`status`, `name`, `data`) can't appear bare in expressions — you need
  `ExpressionAttributeNames` with `#status` style placeholders. You *will* hit this on `status`.
- Use `boto3.resource('dynamodb').Table(...)` rather than the low-level client. It handles type
  marshalling for you.

### Phase 2 — AWS account setup

Nothing gets deployed in this phase. This is pure groundwork, and skipping it is how people get
bad first experiences with AWS.

1. **Create an AWS account.** Requires a credit card even for free-tier usage.
2. **Enable MFA on the root user, then stop using it.** The root account can close your account and
   change your billing. It is not for daily work.
3. **Create an admin identity.** AWS's recommended path is IAM Identity Center (SSO). The simpler
   path is an IAM user with `AdministratorAccess`, MFA enabled, and an access key pair. For a solo
   learning project the IAM user is fine — just know it's the less-recommended option and the
   access keys are long-lived secrets you must not commit.
4. **Set a zero-spend budget.** Billing → Budgets → use the "Zero spend budget" template. It emails
   you the moment anything costs more than $0. Do this now, not later.
5. **Install and configure the AWS CLI.** `aws configure`, then verify with
   `aws sts get-caller-identity` — it should print your account ID and IAM identity.
6. **Install the SAM CLI** and **Docker Desktop**. SAM uses Docker for `sam build --use-container`
   and `sam local`.
7. **Pick a region and stick to it.** `ap-south-1` (Mumbai) is closest to you. The console shows
   resources for one region at a time, and forgetting which region you deployed to is a rite of
   passage you can skip.

### Phase 3 — Deploy

1. **Write `template.yaml`.** Roughly:

   - `AWS::Serverless::Function` — runtime `python3.12`, handler `app.main.handler`, memory 512 MB,
     timeout 15s. An `HttpApi` event with `Path: /{proxy+}` and `Method: ANY` routes every request
     to FastAPI, which does its own routing.
   - `AWS::DynamoDB::Table` — `PK`/`SK` keys, GSI1, `BillingMode: PAY_PER_REQUEST`.
   - The function's `Policies:` block using SAM's `DynamoDBCrudPolicy` template scoped to your
     table. This is least-privilege in one line: the function can read and write *that one table*
     and nothing else.
   - `Environment: Variables:` passing the table name into the function.
   - An `AWS::Logs::LogGroup` with `RetentionInDays: 14`, so logs don't accumulate forever.
   - An `Outputs:` section exporting the API URL.

2. **Test the Lambda path locally:** `sam local start-api`. This runs your function in a container
   the way Lambda will. It catches import errors and handler misconfiguration before they become a
   cryptic 502.

3. **Deploy:** `sam build` then `sam deploy --guided`. The guided run asks for a stack name, region,
   and confirmation, then writes your answers to `samconfig.toml` so future deploys are just
   `sam deploy`.

4. **Verify:** hit the output URL's `/health`, then `/docs`.

5. **Go look at what you made.** Open the CloudFormation console and view the stack's Resources tab
   — every resource SAM created, in one list. Then look at the Lambda function's configuration, its
   execution role in IAM, and its log group in CloudWatch. This is the most valuable fifteen minutes
   in the whole project.

### Phase 4 — Harden

- **Protect the API** (see below).
- **Add structured JSON logging.** CloudWatch Logs Insights can query JSON fields; it can't query
  your prose. Log a request ID, the route, and the outcome.
- **Add `X-Ray` tracing** (`Tracing: Active` in SAM) to see the Lambda → DynamoDB call breakdown.
- **Tighten CORS** if you ever add a frontend.

---

## 9. Security: the "no auth" problem

You chose single-user with no auth, which is the right call for scope. But an API Gateway endpoint
is public — anyone who has the URL can write to your table. URLs leak, and the internet scans
everything.

The fix costs about fifteen lines and isn't really "auth":

1. Generate a long random secret.
2. Store it in **AWS Systems Manager Parameter Store** as a `SecureString` (free tier covers this)
   and reference it from `template.yaml`. Don't hardcode it, and don't commit it.
3. Add a FastAPI dependency that compares an `X-API-Key` header against it and returns `401` if it
   doesn't match.
4. Compare using `hmac.compare_digest`, not `==`, to avoid a timing side channel.
5. Exempt `/health` so you can check liveness without the key.

This is a shared secret, not identity. It doesn't tell you *who* is calling, only that they know
the password. That's exactly the right amount of security for a single-user tracker — and when you
later want real multi-user auth, the upgrade path is API Gateway's JWT authorizer with Cognito.

---

## 10. Cleaning up

```bash
sam delete
```

Deletes the CloudFormation stack and every resource in it. Two caveats: check whether your
DynamoDB table has a `DeletionPolicy: Retain` (SAM sometimes sets this), and log groups created
outside the template survive. Confirm in the console that the stack is gone and the table is gone.

Get comfortable deploying and deleting repeatedly. Being able to destroy and recreate your entire
infrastructure with two commands is the actual point of infrastructure-as-code, and feeling that
is worth more than reading about it.

---

## 11. Stretch goals

Roughly in order of learning value per unit of effort:

- **GitHub Actions CI/CD** — run tests, then `sam deploy` on push to `main`. Uses OIDC to assume an
  IAM role, so no long-lived keys in GitHub.
- **A second environment** — `dev` and `prod` stacks from the same template via SAM parameters.
- **Weekly digest** — an EventBridge scheduled rule triggering a second Lambda that emails you a
  summary via SES. Teaches event-driven architecture.
- **DynamoDB Streams** — react to writes; maintain a denormalized stats item.
- **Custom domain** — Route 53 + ACM certificate on API Gateway. (Costs a few dollars a year for
  the domain.)
- **A tiny frontend** — static site on S3 + CloudFront hitting this API.

---

## 12. AWS glossary

Terms you'll hit in the first week, in plain language.

| Term | What it actually is |
|---|---|
| **IAM** | Identity and Access Management. Who can do what to which resources. |
| **IAM role** | A set of permissions a *service* assumes. Your Lambda has one; that's how it reaches DynamoDB without credentials. |
| **IAM policy** | A JSON document listing allowed (or denied) actions on resources. Attached to users and roles. |
| **Region** | A geographic cluster of data centers, e.g. `ap-south-1`. Resources live in exactly one. |
| **CloudFormation** | AWS's native infrastructure-as-code. You describe resources in YAML; it creates and tracks them. |
| **Stack** | One deployed CloudFormation template and all the resources it manages, as a single unit. |
| **SAM** | A thin layer over CloudFormation with shortcuts for serverless, plus a CLI for build/deploy/local-run. |
| **Lambda** | Run a function in response to an event. No server to manage; billed per millisecond. |
| **Cold start** | The delay when Lambda has to boot a new execution environment for your function. |
| **API Gateway** | The managed front door that turns HTTP requests into Lambda invocations. |
| **HTTP API vs REST API** | Two API Gateway flavours. HTTP API is newer, cheaper, simpler — use it. |
| **DynamoDB** | Managed NoSQL key-value store. Single-digit-millisecond reads, no servers, no SQL. |
| **Partition key (PK)** | Determines which physical partition an item lives in. Every query must specify it exactly. |
| **Sort key (SK)** | Orders items within a partition and enables range queries like `begins_with`. |
| **GSI** | Global Secondary Index. An alternate PK/SK view of your table, for queries the main keys can't serve. |
| **Query vs Scan** | `Query` reads one partition using the key. `Scan` reads the whole table. Prefer `Query`, always. |
| **On-demand** | DynamoDB billing mode where you pay per request rather than reserving capacity. |
| **CloudWatch** | Logs and metrics. Every `print()` in your Lambda lands here. |
| **Parameter Store** | Part of Systems Manager. Stores config and secrets; `SecureString` values are encrypted. |
| **boto3** | The AWS SDK for Python. |
| **Mangum** | Adapter translating Lambda events into ASGI calls, so FastAPI can run on Lambda. |

---

## 13. Definition of done

- [ ] All nine endpoints work against deployed AWS infrastructure
- [ ] Every read is a `Query` or `GetItem` — no `Scan` anywhere
- [ ] `X-API-Key` enforced on everything except `/health`
- [ ] No secrets in the repository; `.env` is gitignored
- [ ] All infrastructure defined in `template.yaml` — nothing created by hand in the console
- [ ] Zero-spend budget alert is active
- [ ] Log group has a retention period set
- [ ] Tests pass locally against DynamoDB Local or `moto`
- [ ] `sam delete` then `sam deploy` recreates everything cleanly
- [ ] README documents setup, local dev, and deploy in a way that works after six months away
