# NovaSync

**AI-powered travel planning with agentic research, human-in-the-loop review, and autonomous booking.**

NovaSync turns a freeform travel idea into a structured, day-by-day itinerary. An AI agent autonomously researches activities, checks weather, finds local events, and validates venues — then presents a draft plan for your approval before locking anything in. Once approved, Nova Act can complete restaurant bookings on your behalf, navigating reservation platforms end-to-end.

---

## How It Works

```
User submits idea
      │
      ▼
Nova 2 Lite agent (AWS Bedrock)
  ├── search_activities
  ├── get_local_events (Ticketmaster)
  ├── get_weather (Open-Meteo)
  └── validate_place (Nominatim / Google Places)
      │
      ▼
Scaffold generated → Human review (revise or approve)
      │
      ▼
Kimi K2.5 (OpenRouter) extracts structured itinerary JSON
      │
      ▼
Day-by-day itinerary on TripPage
      │
      ▼
Nova Act books restaurants autonomously (optional)
```

---

## Features

- **Agentic research** — Nova 2 Lite runs up to 20 tool-use iterations to gather real-world evidence before planning
- **Human-in-the-loop scaffold review** — inspect the draft plan, request one revision, then approve to extract
- **Live action feed** — watch every tool call stream in real time as the agent works
- **Group trip planning** — multiple travellers submit preferences; NovaSync builds a consensus itinerary
- **Autonomous booking** — Nova Act opens Tabelog (or other platforms), fills in date/time/party size, and completes the reservation without a login
- **Multi-modal input** — attach Instagram links, TikTok videos, or uploaded images as planning context
- **Lifestyle interview** — dietary needs, wake time, fitness level, accommodation style all feed into the plan

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Frontend | React 19 + TypeScript, Vite, React Router 7 |
| Backend | FastAPI (Python 3.10+), Uvicorn |
| Database + Auth | Supabase (PostgreSQL + Google OAuth) |
| Agentic planning | Amazon Nova Lite v1 via AWS Bedrock |
| LLM extraction | Moonshot Kimi K2.5 via OpenRouter |
| Browser automation | Nova Act |
| Web search | Tavily / DuckDuckGo fallback |
| Events | Ticketmaster API |
| Weather | Open-Meteo + Nominatim |
| Places | Google Places API |

---

## Project Structure

```
new_nova_hackathon/
├── backend/                    # FastAPI app
│   ├── main.py                 # App entry point + router registration
│   ├── config.py               # Env vars, Supabase + Bedrock clients
│   ├── models.py               # Pydantic schemas
│   ├── routes/
│   │   ├── ideas.py            # /api/ideas/plan, /revise, /extract (SSE)
│   │   ├── group_trips.py      # Group trip CRUD
│   │   ├── group_plan.py       # Group consensus planning (SSE)
│   │   └── bookings.py         # Nova Act booking endpoints
│   └── services/
│       ├── orchestrator.py     # Planning pipeline orchestration
│       ├── nova_agent.py       # Nova 2 Lite agentic loop
│       ├── openrouter.py       # LLM calls (scaffold, critique, revise, extract)
│       ├── session_cache.py    # 15-min in-memory HITL session store
│       ├── nova_act/           # Browser automation (booking agent)
│       ├── tools/              # Agent tool implementations
│       └── workers/            # Parallel research workers
├── frontend/                   # React app
│   └── src/
│       ├── pages/              # LoginPage, DashboardPage, PlanPage, TripPage, GroupPlanPage
│       ├── components/         # AgentActionFeed, ScaffoldReviewCard, BookingPanel, …
│       ├── services/api.ts     # API client
│       └── types.ts            # Shared TypeScript types
└── docs/plans/                 # Architecture design documents
```

---

## Getting Started

### Prerequisites

- Python 3.10+
- Node.js 18+
- AWS account with Bedrock access (Nova Lite model enabled in `us-east-1`)
- Supabase project
- OpenRouter API key

### 1. Environment variables

Create a `.env` file at the project root:

```bash
# Supabase
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_ANON_KEY=
SUPABASE_SERVICE_ROLE_KEY=
SUPABASE_JWT_SECRET=

# OpenRouter (LLM)
OPENROUTER_API_KEY=
OPENROUTER_MODEL=moonshotai/kimi-k2.5

# AWS Bedrock (Nova agent)
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
AWS_DEFAULT_REGION=us-east-1

# Nova Act (browser automation)
NOVA_ACT_API_KEY=
NOVA_AGENT_ENABLED=true

# Web search
TAVILY_API_KEY=                 # optional; falls back to DuckDuckGo
WEB_RESEARCH_PROVIDER=duckduckgo

# Optional integrations
TICKETMASTER_API_KEY=           # event discovery
GOOGLE_PLACES_API_KEY=          # place validation

# CORS
ALLOWED_ORIGINS=http://localhost:5173
```

Frontend picks up `VITE_API_BASE` (defaults to `http://localhost:8000/api`).

### 2. Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install fastapi uvicorn pydantic supabase boto3 httpx python-dotenv nova-act tavily-python
uvicorn main:app --reload --port 8000
```

### 3. Frontend

```bash
cd frontend
npm install
npm run dev          # http://localhost:5173
```

---

## API Overview

| Endpoint | Type | Description |
|----------|------|-------------|
| `POST /api/ideas/plan` | SSE | Submit trip idea → agent research → scaffold |
| `POST /api/ideas/revise` | JSON | Revise scaffold with user feedback |
| `POST /api/ideas/extract` | SSE | Approve scaffold → extract itinerary nodes |
| `POST /api/group-trips/create` | JSON | Create a group trip |
| `POST /api/group/:id/plan` | SSE | Run group consensus planning |
| `POST /api/bookings/start` | JSON | Start Nova Act booking session |

SSE events: `planning_stage` · `agent_action` · `scaffold_ready` · `extracted` · `error`

---

## Key Design Decisions

**Feature flag** — Set `NOVA_AGENT_ENABLED=false` to revert to the OpenRouter-only pipeline (no Bedrock required).

**Session cache** — The HITL flow (plan → revise → extract) uses a 15-minute in-memory session keyed on `session_id`. This avoids re-running expensive research between steps.

**Agent tool deduplication** — The Nova agent caches tool results within a session so identical `search_activities` calls don't trigger redundant API requests.

**Immutable evidence** — All research findings are typed `EvidenceItem` objects. The planner prompt receives a scored, deduplicated list; nothing is mutated in place.
