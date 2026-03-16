# AGENTS.md — NovaSync Project Guide

This document provides essential context for AI coding agents working on NovaSync.

---

## Project Overview

**NovaSync** is an AI-powered travel planning application that helps users create personalized itineraries. The system uses a multi-agent orchestration approach to research destinations, validate places, check weather/events, and generate structured travel plans.

### Key Features

- **AI-Powered Planning**: Uses LLMs (via OpenRouter/AWS Bedrock) to extract and plan travel itineraries
- **Multi-Modal Input**: Accepts text ideas, URLs, and image/video uploads
- **Group Trip Planning**: Supports collaborative trip planning with consensus building
- **Human-in-the-Loop**: Interactive scaffold review before final plan generation
- **Real-time Research**: Web search, events lookup, weather checks, and place validation
- **Agentic UI Automation** : Booking of restaurants, accomodations, transportation modes

---

## Architecture

### Tech Stack

| Component      | Technology                                   |
| -------------- | -------------------------------------------- |
| **Backend**    | Python 3.x, FastAPI                          |
| **Frontend**   | React 19, TypeScript, Vite                   |
| **Database**   | Supabase (PostgreSQL)                        |
| **AI/LLM**     | OpenRouter API, AWS Bedrock (Nova), Nova Act |
| **Auth**       | Supabase Auth (Google OAuth)                 |
| **Additional** | Claud-ometer (Next.js dashboard for metrics) |

### Directory Structure

```
new_nova_hackathon/
├── backend/                 # FastAPI Python backend
│   ├── main.py             # Application entry point
│   ├── config.py           # Centralized config (Supabase, AWS Bedrock)
│   ├── models.py           # Pydantic models for request/response
│   ├── routes/             # API route handlers
│   │   ├── ideas.py        # Main trip planning endpoints
│   │   ├── group_trips.py  # Group trip management
│   │   └── group_plan.py   # Group planning orchestration
│   ├── services/           # Business logic
│   │   ├── orchestrator.py # Main planning orchestration
│   │   ├── openrouter.py   # LLM integration (primary)
│   │   ├── nova_agent.py   # AWS Nova Lite agent
│   │   ├── bedrock.py      # AWS Bedrock integration
│   │   ├── tools/          # Research tools (events, places, weather, search)
│   │   ├── workers/        # Background workers (media, URL scraping, web research)
│   │   └── nova_act/       # Nova Act browser automation (WIP)
│   └── sql/                # Database migrations
├── frontend/               # React TypeScript frontend
│   ├── src/
│   │   ├── pages/          # Route pages (Login, Dashboard, Plan, Trip, Group)
│   │   ├── components/     # React components
│   │   ├── services/       # API client (api.ts, bookingApi.ts)
│   │   ├── contexts/       # Auth context
│   │   ├── lib/            # Utilities (Supabase client)
│   │   ├── types.ts        # Shared TypeScript types
│   │   └── App.tsx         # Main app component
│   └── supabase/           # Supabase local config
├── Claud-ometer/           # Next.js dashboard (separate app)
├── docs/plans/             # Architecture/design documents
└── .env                    # Environment variables (root level)
```

---

## Environment Variables

The project uses a single `.env` file at the project root. All backend and frontend configs point to this file.

### Required Variables

```bash
# AWS / Bedrock
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
AWS_DEFAULT_REGION=us-east-1

# Supabase
SUPABASE_URL=
SUPABASE_ANON_KEY=
SUPABASE_SERVICE_ROLE_KEY=
SUPABASE_JWT_SECRET=

# OpenRouter (primary LLM provider)
OPENROUTER_API_KEY=
OPENROUTER_MODEL=moonshotai/kimi-k2.5
OPENROUTER_COT_MODEL=moonshotai/kimi-k2.5
OPENROUTER_IMAGE_MODEL=qwen/qwen3-vl-30b-a3b-thinking

# Search
TAVILY_API_KEY=
WEB_RESEARCH_PROVIDER=duckduckgo  # or tavily

# Google OAuth
CLIENT_ID_GOOGLE_OAUTH=
CLIENT_SECRET=

# Nova Act (browser automation)
NOVA_ACT_API_KEY=
NOVA_AGENT_ENABLED=True

# Timeouts and Limits
ORCHESTRATION_TIMEOUT_SECONDS=420
DB_TIMEOUT_SECONDS=300
PLANNER_EVIDENCE_MAX_ITEMS=12
PLANNER_EVIDENCE_MAX_CHARS=55000
```

---

## Build and Development Commands

### Backend

```bash
cd backend

# Setup (one-time)
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt  # if exists, or install individually:
# pip install fastapi uvicorn pydantic supabase boto3 httpx python-dotenv nova-act

# Run development server
uvicorn main:app --reload --port 8000

# The backend will auto-load .env from parent directory
```

Key backend dependencies (inferred from imports):

- `fastapi` - Web framework
- `uvicorn` - ASGI server
- `pydantic` - Data validation
- `supabase` - Database client
- `boto3` - AWS SDK
- `httpx` - HTTP client
- `python-dotenv` - Environment loading
- `nova-act` - Browser automation (optional)

### Frontend

```bash
cd frontend

# Install dependencies
npm install

# Development server
npm run dev
# Vite dev server runs on http://localhost:5173

# Build for production
npm run build

# Preview production build
npm run preview

# Lint
npm run lint
```

### Claud-ometer (Separate Dashboard)

```bash
cd Claud-ometer
npm install
npm run dev  # Next.js dev server
```

---

## Testing

### Manual Test Scripts

```bash
# Test Nova Act/AWS access
python test_nova_access.py

# Test Nova Act with browser automation
python test_nova_smoke.py
```

### No Automated Test Suite

The project currently does not have pytest or Jest test suites. Testing is primarily manual through the UI and test scripts.

---

## Code Style Guidelines

### Python (Backend)

- **Docstrings**: Use triple-quoted docstrings at module and function level
- **Type Hints**: Use `from __future__ import annotations` and modern typing (`str | None`)
- **Imports**: Group as: stdlib → third-party → local (with blank lines between)
- **Constants**: Use `UPPER_SNAKE_CASE` for module-level constants
- **Private functions**: Prefix with underscore (`_helper_function`)
- **Models**: Use Pydantic `BaseModel` for all request/response schemas
- **Logging**: Use `logging.getLogger(__name__)` pattern

Example:

```python
"""
NovaSync — module description here.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel

from models import ItineraryNode

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30

class MyRequest(BaseModel):
    idea: str
```

### TypeScript/React (Frontend)

- **Types**: Define interfaces in `types.ts`, use explicit return types
- **Comments**: Use `//` for single-line, `/* */` for multi-line
- **File naming**: PascalCase for components (`MyComponent.tsx`), camelCase for utilities
- **API calls**: Centralize in `services/api.ts`
- **Environment**: Use `import.meta.env.VITE_*` for env vars

---

## Key Architectural Patterns

### 1. Orchestration Flow

The main planning flow is in `services/orchestrator.py`:

1. **Scaffold Phase**: LLM generates a high-level plan outline
2. **Critique Phase**: Another LLM pass reviews the scaffold
3. **Revision Phase**: Scaffold is refined based on critique
4. **Extraction Phase**: Final itinerary nodes are extracted

### 2. Worker Pattern

Background workers in `services/workers/`:

- `url_worker.py` - Scrapes URLs for context
- `media_worker.py` - Processes image/video uploads
- `web_research_worker.py` - Performs web searches
- `web_grounding_worker.py` - Validates information
- `preference_extraction_worker.py` - Extracts user preferences

### 3. Evidence System

All research findings are stored as `EvidenceItem` objects with:

- Source tracking (URL, media type, etc.)
- Confidence scores
- Structured facts (locations, activities, constraints)

### 4. Streaming Responses

The `/api/process-idea` endpoint uses SSE (Server-Sent Events) to stream progress:

- `planning_stage` events
- `agent_action` events (for Nova Agent)
- `scaffold_ready` events
- `extracted` events (final result)

---

## Database Schema (Supabase)

Key tables (inferred from code):

- `trips` - Trip metadata
- `itinerary_nodes` - Individual activities (with date_local, start_time_local, end_time_local)
- `group_trips` - Group trip organization
- `travelers` - Group trip participants

Migration files in `backend/sql/`

---

## Security Considerations

- **Never commit `.env`** - It contains API keys and secrets
- **CORS** - Backend uses `ALLOWED_ORIGINS` env var for CORS configuration
- **Auth** - JWT tokens from Supabase, validated via `services/auth.py`
- **File uploads** - Size limits enforced (8MB images, 20MB videos)

---

## Common Development Tasks

### Adding a New API Endpoint

1. Add Pydantic model to `backend/models.py`
2. Add route handler to appropriate file in `backend/routes/`
3. Register router in `backend/main.py`
4. Add TypeScript types to `frontend/src/types.ts`
5. Add API function to `frontend/src/services/api.ts`

### Adding a New Worker

1. Create file in `backend/services/workers/`
2. Follow the `run_*_worker` naming convention
3. Return `EvidenceItem` list
4. Integrate into `orchestrator.py`

### Adding a New Tool (for Nova Agent)

1. Add tool definition to `backend/services/tools/`
2. Define JSON schema for parameters
3. Implement handler function
4. Register in `nova_agent.py` tool list

---

## Deployment Notes

- Frontend is configured for Vercel (has `vercel-deploy` skill)
- Backend can be deployed to Render (has `render-deploy` skill)
- Supabase is used as hosted service
- Environment variables must be configured on deployment platform

---

## External API Dependencies

| Service       | Purpose              | Fallback   |
| ------------- | -------------------- | ---------- |
| OpenRouter    | Primary LLM provider | -          |
| AWS Bedrock   | Nova models          | OpenRouter |
| Supabase      | Database, Auth       | -          |
| Tavily        | Web search           | DuckDuckGo |
| Ticketmaster  | Event lookup         | -          |
| Google Places | Place validation     | -          |

---

## Documentation

- Architecture plans in `docs/plans/`
- Component documentation in `Claud-ometer/CLAUDE.md`
- This file (`AGENTS.md`) for agent context

---

## Security Constraints (Critical)

- **NEVER execute `pkill`, `kill`, or `killall` commands** without explicit, individual user approval
- **Do not include process termination commands** in "Approve for this session" workflows
- If a process needs to be restarted, always ask the user to manually close it first
- If unsure, ask a clarifying question instead of guessing
- If a test fails twice, stop and propose a plan to the user

---

_Last updated: 2026-03-11_
