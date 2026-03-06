<p align="center">
  <img src="public/logo.svg" alt="Faultline" width="80" height="80">
</p>

# Faultline

An open-source AI agent for infrastructure debugging. Ask questions about your infrastructure in plain English — it queries your monitoring tools, cross-references the data, and comes back with a likely root cause.

We built this at [Chatwoot](https://www.chatwoot.com) to speed up our own incident investigations. Read more at [faultline.chatwoot.com](https://faultline.chatwoot.com).

<p align="center">
  Here is a video on how it works.<br><br>
  <a href="https://youtu.be/S1-pW_wD2uA">
    <img src=".github/video-thumbnail.png" alt="Watch the demo" width="600">
  </a>
</p>

## How It Works

You ask a question. The agent uses MCP (Model Context Protocol) servers and custom tools to query your integrations, iterates through the data autonomously, and returns a complete analysis.

```
                          ┌─────────────────────────────────┐
                          │  Frontend (Vue 3 + Vite)        │
                          │  Served as static assets by     │
                          │  Rails in production            │
                          └──────────┬──────────────────────┘
                                     │ HTTP / WebSocket (ActionCable)
                                     ▼
                          ┌─────────────────────────────────┐
                          │  Rails API (Puma)               │
                          │  Auth, conversations, settings  │
                          ├─────────────────────────────────┤
                          │  Sidekiq Worker                 │
                          │  Background agent runs          │
                          └──────┬───────────┬──────────────┘
                                 │           │
                    ┌────────────┘           └────────────┐
                    ▼                                     ▼
          ┌──────────────────┐                 ┌──────────────────┐
          │  PostgreSQL      │                 │  Redis           │
          │  Data store      │                 │  Jobs + Cable    │
          └──────────────────┘                 └──────────────────┘
                                                        │
                          ┌─────────────────────────────┘
                          ▼
                ┌──────────────────────────┐
                │  Python Agent Service    │
                │  FastAPI + OpenAI        │
                │  MCP client connections  │
                ├──────────────────────────┤
                │  ┌─────────┐ ┌────────┐  │
                │  │New Relic│ │ Sentry │  │
                │  └─────────┘ └────────┘  │
                │  ┌────────┐ ┌─────────┐  │
                │  │  AWS   │ │ GitHub  │  │
                │  └────────┘ └─────────┘  │
                │  ┌──────────┐            │
                │  │PagerDuty │            │
                │  └──────────┘            │
                └──────────────────────────┘
```

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Frontend | Vue 3, Vite, Tailwind CSS, Pinia |
| Backend | Ruby on Rails 8, Puma, Sidekiq |
| Agent | Python, FastAPI, OpenAI SDK |
| Database | PostgreSQL 17 |
| Queue / PubSub | Redis 7 (Sidekiq + ActionCable) |

## Production Deployment (Docker Compose)

### Services

The Docker Compose setup runs 5 containers:

| Service | Image | Purpose |
|---------|-------|---------|
| **web** | Rails (multi-stage build) | Puma web server, serves API + frontend assets |
| **worker** | Same Rails image | Sidekiq background job processor |
| **agent** | Python (FastAPI) | AI agent orchestration service |
| **postgres** | postgres:17-alpine | Primary data store |
| **redis** | redis:7-alpine | Job queue + ActionCable pub/sub |

### Quick Start

```bash
# 1. Clone the repo
git clone https://github.com/chatwoot/faultline.git
cd faultline

# 2. Configure environment
cp .env.production.example .env
# Edit .env — set SECRET_KEY_BASE, JWT_SECRET, POSTGRES_PASSWORD

# 3. Create data directories
sudo mkdir -p /var/data/faultline/{postgres,redis}

# 4. Build and start
docker compose build
docker compose up -d
```

The `web` service automatically runs `db:prepare` on startup (creates the database and runs migrations). Check http://localhost:3000 and create an account.

### Health Checks

```bash
# Rails
curl http://localhost:3000/api/health

# Agent
curl http://localhost:8000/health
```

### Data Persistence

PostgreSQL and Redis data is stored on the host at `/var/data/faultline/`. This survives container restarts, `docker compose down`, and even full Docker reinstalls.

### Environment Variables

See [`.env.production.example`](.env.production.example) for the full list. Key variables:

| Variable | Required | Description |
|----------|----------|-------------|
| `SECRET_KEY_BASE` | Yes | Rails secret (`openssl rand -hex 64`) |
| `JWT_SECRET` | Yes | JWT signing key (`openssl rand -hex 32`) |
| `POSTGRES_PASSWORD` | Yes | Database password |
| `OPENAI_API_KEY` | Yes | OpenAI API key for the agent |
| `CORS_ORIGIN` | Yes | Your production domain (e.g. `https://faultline.example.com`) |
| `VITE_WS_URL` | Yes | WebSocket URL (e.g. `wss://faultline.example.com`) |

## Development Setup

### Prerequisites

- Ruby 3.4+
- Node.js 25+ and pnpm
- Python 3.11+ and [uv](https://docs.astral.sh/uv/)
- PostgreSQL and Redis running locally

### Install

```bash
# Ruby gems
bundle install

# Node packages
pnpm install

# Python agent dependencies
cd agent_service && uv sync && cd ..
```

### Configure

```bash
cp .env.example .env
```

Defaults work for local development. Integration API keys (New Relic, Sentry, etc.) are configured through the Settings UI, not environment variables.

### Run

```bash
# Start all services (Rails, Sidekiq, Vite dev server, Python agent)
pnpm dev
```

- App: http://localhost:5173 (Vite dev server proxies to Rails)
- API: http://localhost:3000
- Agent: http://localhost:8000

## Integrations

All integrations are configured in **Settings** from the UI. Credentials are encrypted at rest.

| Integration | How It Connects | What It Provides |
|-------------|----------------|------------------|
| **OpenAI** | Direct API | LLM powering the agent (required) |
| **New Relic** | Native (NerdGraph API) | APM metrics, NRQL queries, golden signals |
| **Sentry** | MCP — `@sentry/mcp-server` (stdio) | Error tracking, stack traces, issue search |
| **AWS** | Native (boto3) | CloudWatch, EC2, Lambda, RDS, ECS |
| **GitHub** | MCP — `@modelcontextprotocol/server-github` (stdio) | Code search, file content, commits, blame |
| **PagerDuty** | Native + MCP — `mcp.pagerduty.com` (streamable-http) | Incidents, services, escalation policies, on-call schedules |
| **Hetzner Cloud** | Native (REST API v1) | Servers, load balancers, firewalls, networks, volumes, metrics |

All integrations except OpenAI support multiple instances (e.g. multiple AWS accounts).

## Features

- **Autonomous agent loop** — iterates through tool calls, drilling deeper each round
- **Model selection** — pick any OpenAI model from the dropdown, fetched live from your API key
- **Conversation threads** — persistent threads with auto-generated titles
- **Streaming responses** — real-time text and tool call visibility via SSE + ActionCable
- **Multi-tenant workspaces** — team accounts with role-based access (Pundit)
- **Resource maps** — visual infrastructure topology with Vue Flow
- **Dark mode** — follows system preference

## Project Structure

```
faultline/
├── app/                    # Rails application
│   ├── channels/           #   ActionCable WebSocket channels
│   ├── controllers/api/    #   REST API controllers
│   ├── jobs/               #   Sidekiq background jobs
│   ├── models/             #   ActiveRecord models
│   ├── policies/           #   Pundit authorization policies
│   ├── services/           #   Business logic
│   └── views/              #   ERB layouts (minimal — SPA)
├── agent_service/          # Python agent service
│   ├── agent/
│   │   ├── main.py         #   FastAPI app entry point
│   │   ├── orchestrator.py #   Agent loop + OpenAI calls
│   │   ├── integrations/   #   Tool registry + MCP clients
│   │   ├── sub_agents/     #   Specialized sub-agents
│   │   └── prompts/        #   System prompts
│   ├── pyproject.toml
│   └── Dockerfile
├── frontend/               # Vue 3 SPA
│   ├── components/         #   UI components
│   ├── composables/        #   useAgent, useWebSocket
│   ├── stores/             #   Pinia state management
│   └── main.ts             #   App entry point
├── config/                 # Rails configuration
├── db/                     # Migrations and schema
├── Dockerfile              # Multi-stage Rails + frontend build
├── docker-compose.yml      # Production deployment
└── .env.production.example # Environment variable template
```

## License

MIT
