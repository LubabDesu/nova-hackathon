"""
NovaSync — Supabase service-role admin client.

Use this client for operations that bypass Row Level Security
(e.g. inserting group preferences from anonymous joiners).
Do NOT expose this key to the frontend.
"""

from __future__ import annotations

import os

from supabase import create_client, Client

_admin_client: Client | None = None


def get_admin_client() -> Client:
    """Return a singleton Supabase client using the service-role key."""
    global _admin_client
    if _admin_client is None:
        url = os.environ.get("SUPABASE_URL") or os.environ.get("VITE_SUPABASE_URL")
        service_role_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        if not url or not service_role_key:
            raise RuntimeError(
                "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set"
            )
        _admin_client = create_client(url, service_role_key)
    return _admin_client
