"""
NovaSync — FastAPI application entry point.
"""

import logging
import os
import ssl
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from routes.bookings import router as bookings_router
from routes.ideas import router as ideas_router
from routes.group_trips import router as group_trips_router
from routes.group_plan import router as group_plan_router
# skip certificate check
ssl._create_default_https_context = ssl._create_unverified_context
logging.basicConfig(level=logging.INFO)

# Load .env from repo root so model/env settings are applied consistently.
load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)

app = FastAPI(title="NovaSync", version="0.1.0")

# ── CORS ────────────────────────────────────────────────────────────────────
# ALLOWED_ORIGINS env var is a comma-separated list, e.g.:
#   "http://localhost:5173,https://nova-sync.vercel.app"
_raw_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:5173")
_allowed_origins = [o.strip() for o in _raw_origins.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ─────────────────────────────────────────────────────────────────
app.include_router(bookings_router)
app.include_router(ideas_router)
app.include_router(group_trips_router)
app.include_router(group_plan_router)


@app.get("/")
def health():
    return {"status": "NovaSync backend is live!"}
