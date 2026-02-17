"""
NovaSync — centralised configuration.
Loads environment variables and exposes singleton clients for Supabase and Bedrock.
"""

import os
from pathlib import Path

import boto3
from dotenv import load_dotenv
from supabase import create_client, Client

# Load the .env that lives at the repo root (one level up from backend/)
_env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_env_path)

# ── Supabase ────────────────────────────────────────────────────────────────
SUPABASE_URL: str = os.environ["SUPABASE_URL"]
SUPABASE_ANON_KEY: str = os.environ["SUPABASE_ANON_KEY"]

supabase: Client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)

# ── AWS / Bedrock ───────────────────────────────────────────────────────────
AWS_REGION: str = os.getenv("AWS_DEFAULT_REGION", "us-east-1")

bedrock_runtime = boto3.client(
    "bedrock-runtime",
    region_name=AWS_REGION,
    aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
)
