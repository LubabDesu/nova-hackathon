"""
NovaSync — FastAPI application entry point.
"""

import logging
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from routes.ideas import router as ideas_router

logging.basicConfig(level=logging.INFO)

# Load .env from repo root so model/env settings are applied consistently.
load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)

app = FastAPI(title="NovaSync", version="0.1.0")

# ── CORS ────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ─────────────────────────────────────────────────────────────────
app.include_router(ideas_router)


@app.get("/")
def health():
    return {"status": "NovaSync backend is live!"}
