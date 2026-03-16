"""
NovaSync — Supabase persistence helpers.

All write and read operations use the service-role admin client so that
Row Level Security (RLS) policies on `trips` and `itinerary_nodes` do not
block server-side inserts.  Never expose the admin client to the frontend.
"""

from __future__ import annotations

import logging

from models import ItineraryNode

logger = logging.getLogger(__name__)
SCHEDULE_COLUMNS = ("date_local", "start_time_local", "end_time_local")


def _admin():
    """Return the singleton Supabase admin (service-role) client."""
    from services.supabase_admin import get_admin_client
    return get_admin_client()


def create_trip(name: str, user_id: str | None = None) -> dict:
    """Insert a new trip and return the row (including its generated UUID)."""
    row: dict = {"name": name}
    if user_id:
        row["user_id"] = user_id
    result = _admin().table("trips").insert(row).execute()
    return result.data[0]


def _build_node_rows(trip_id: str, nodes: list[ItineraryNode]) -> list[dict]:
    return [
        {
            "trip_id": trip_id,
            "title": n.title,
            "activity_type": n.activity_type,
            "duration_mins": n.duration_mins,
            "date_local": n.date_local,
            "start_time_local": n.start_time_local,
            "end_time_local": n.end_time_local,
            "lat": n.lat,
            "long": n.long,
            "description": n.description,
            "for_travelers": n.for_travelers,
        }
        for n in nodes
    ]


def _strip_schedule_columns(rows: list[dict]) -> list[dict]:
    return [
        {
            key: value
            for key, value in row.items()
            if key not in SCHEDULE_COLUMNS
        }
        for row in rows
    ]


def _is_schedule_column_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return (
        "column" in text
        and any(column in text for column in SCHEDULE_COLUMNS)
    ) or "failed to parse columns parameter" in text


def insert_nodes(trip_id: str, nodes: list[ItineraryNode]) -> list[dict]:
    """Bulk-insert itinerary nodes linked to a trip and return the rows."""
    rows = _build_node_rows(trip_id, nodes)

    if not rows:
        logger.info("No nodes to insert for trip %s; skipping DB insert", trip_id)
        return []

    try:
        result = _admin().table("itinerary_nodes").insert(rows).execute()
        logger.info(
            "Inserted %d nodes for trip %s (with schedule columns)",
            len(result.data),
            trip_id,
        )
        return result.data
    except Exception as exc:  # noqa: BLE001
        if not _is_schedule_column_error(exc):
            raise

        logger.warning(
            (
                "itinerary_nodes appears to be missing schedule columns. "
                "Retrying insert without date/time fields. Error: %s"
            ),
            exc,
        )
        legacy_rows = _strip_schedule_columns(rows)
        legacy_result = _admin().table("itinerary_nodes").insert(legacy_rows).execute()
        logger.info(
            "Inserted %d nodes for trip %s (legacy schema fallback)",
            len(legacy_result.data),
            trip_id,
        )
        return legacy_result.data


def update_nodes(nodes: list[ItineraryNode], trip_id: str) -> list[dict]:
    """Bulk-upsert itinerary nodes by id. Only updates editable fields."""
    if not nodes:
        return []
    rows = [
        {
            "id": n.id,
            "trip_id": trip_id,
            "title": n.title,
            "description": n.description,
            "duration_mins": n.duration_mins,
            "date_local": n.date_local,
            "start_time_local": n.start_time_local,
            "end_time_local": n.end_time_local,
        }
        for n in nodes
        if n.id is not None
    ]
    if not rows:
        logger.warning("update_nodes called but no nodes had ids; skipping")
        return []
    result = _admin().table("itinerary_nodes").upsert(rows, on_conflict="id").execute()
    logger.info("Upserted %d nodes", len(result.data))
    return result.data


def get_trip_nodes(trip_id: str) -> list[dict]:
    """Return all itinerary nodes for a trip, ordered by date then start time."""
    result = (
        _admin()
        .table("itinerary_nodes")
        .select("*")
        .eq("trip_id", trip_id)
        .order("date_local", desc=False)
        .order("start_time_local", desc=False)
        .execute()
    )
    return result.data or []
