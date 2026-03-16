"""
NovaSync — Group trip management routes.

Handles creating group trips, sharing join links, submitting preferences,
and checking participant status.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException

from models import (
    CreateGroupTripRequest,
    CreateGroupTripResponse,
    GroupPlanStatusResponse,
    JoinGroupTripRequest,
)
from services.auth import get_current_user
from services.supabase_admin import get_admin_client

router = APIRouter(prefix="/api/group-trips", tags=["group-trips"])


@router.post("/create", response_model=CreateGroupTripResponse)
async def create_group_trip(
    body: CreateGroupTripRequest,
    user: dict = Depends(get_current_user),
):
    """Create a new group trip. Returns a join URL to share with travelers."""
    admin = get_admin_client()

    row_data: dict = {
        "name": body.name,
        "trip_type": "group",
        "user_id": user["sub"],
        "max_travelers": body.max_travelers,
    }
    if body.trip_location:
        row_data["trip_location"] = body.trip_location
    if body.start_date:
        row_data["start_date"] = body.start_date.isoformat()
    if body.end_date:
        row_data["end_date"] = body.end_date.isoformat()
    if body.trip_days:
        row_data["trip_days"] = body.trip_days

    result = admin.table("trips").insert(row_data).execute()
    row = result.data[0]
    group_id = str(row["id"])
    return CreateGroupTripResponse(
        group_id=group_id,
        join_url=f"/join/{group_id}",
    )


@router.get("/{group_id}")
async def get_group_trip(group_id: str):
    """Public endpoint — return trip metadata for the join page."""
    admin = get_admin_client()
    result = (
        admin.table("trips")
        .select("id, name, trip_location, start_date, end_date, trip_days, trip_type, max_travelers")
        .eq("id", group_id)
        .maybe_single()
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="Trip not found")
    return result.data


@router.post("/{group_id}/join")
async def join_group_trip(group_id: str, body: JoinGroupTripRequest):
    """Anonymous endpoint — submit traveler preferences for a group trip."""
    admin = get_admin_client()

    # Load trip
    trip_result = (
        admin.table("trips")
        .select("id, trip_type, max_travelers")
        .eq("id", group_id)
        .maybe_single()
        .execute()
    )
    if not trip_result.data:
        raise HTTPException(status_code=404, detail="Trip not found")
    trip = trip_result.data
    if trip.get("trip_type") != "group":
        raise HTTPException(status_code=400, detail="This is not a group trip")

    # Check capacity
    count_result = (
        admin.table("group_preferences")
        .select("id", count="exact")
        .eq("group_id", group_id)
        .execute()
    )
    current_count = count_result.count or 0
    if current_count >= trip["max_travelers"]:
        raise HTTPException(status_code=409, detail="This trip is full")

    # Check nickname uniqueness
    nick_result = (
        admin.table("group_preferences")
        .select("id", count="exact")
        .eq("group_id", group_id)
        .eq("nickname", body.nickname)
        .execute()
    )
    if (nick_result.count or 0) > 0:
        raise HTTPException(status_code=409, detail="Nickname already taken for this trip")

    # Insert preferences
    admin.table("group_preferences").insert(
        {
            "group_id": group_id,
            "nickname": body.nickname,
            "free_text": body.free_text,
            "input_directives": body.input_directives.model_dump(),
        }
    ).execute()

    return {"ok": True}


@router.get("/{group_id}/status", response_model=GroupPlanStatusResponse)
async def get_group_trip_status(
    group_id: str,
    user: dict = Depends(get_current_user),
):
    """Creator-only: list participants and slots remaining."""
    admin = get_admin_client()

    # Load trip
    trip_result = (
        admin.table("trips")
        .select("id, trip_location, max_travelers")
        .eq("id", group_id)
        .maybe_single()
        .execute()
    )
    if not trip_result.data:
        raise HTTPException(status_code=404, detail="Trip not found")
    trip = trip_result.data

    # Load participants with their preference summary
    prefs_result = (
        admin.table("group_preferences")
        .select("nickname, submitted_at, free_text, input_directives")
        .eq("group_id", group_id)
        .order("submitted_at", desc=False)
        .execute()
    )
    participants = prefs_result.data or []
    max_travelers = trip["max_travelers"]
    slots_remaining = max(0, max_travelers - len(participants))

    return {
        "group_id": group_id,
        "destination": trip.get("trip_location"),
        "participants": participants,
        "slots_remaining": slots_remaining,
        "max_travelers": max_travelers,
    }
