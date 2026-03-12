# Nova Features Design
_2026-03-03_

## Overview

Three sequential feature phases to strengthen the NovaSync hackathon submission across
Agentic AI and Multimodal Understanding categories.

| Phase | Feature | Nova model |
|---|---|---|
| 1 | Map view + geocoding worker | Nominatim geocoding API |
| 2 | Replan Day agent + Local Events Discovery agent | Nova Lite (Bedrock) |
| 3 | Photo vibe input + per-card vibe match | Nova multimodal embeddings |

---

## Phase 1 — Map View + Geocoding Worker

### Problem
`lat`/`long` fields on `ItineraryNode` are currently model-generated and frequently
hallucinated. Coordinates must come from a real geocoding API before a map is useful.

### Backend

**New file: `backend/services/workers/geocoding_worker.py`**

- Input: `list[ItineraryNode]`, `trip_location: str`
- For each node, fires a Nominatim query: `"{node.title} {trip_location}"`.
  E.g. `"Fushimi Inari Shrine Kyoto"`.
- Returns nodes with `lat`/`long` overwritten with verified values.
- Any node that fails geocoding (no result or low confidence) keeps `lat=null, long=null`
  and is excluded from the map — no fallback to model coordinates.
- Rate limit: sleep 1 second between requests (Nominatim requirement).
- Runs as a post-processing step inside `orchestrate_extraction()` after nodes are
  returned, before they are persisted to Supabase.

**No new endpoint required.** The geocoding worker is wired into the existing extraction
pipeline and its output is stored on the node objects written to Supabase.

### Frontend

- Leaflet.js (no API key, open source) map panel rendered alongside `ResultDisplay`.
- Appears only once an itinerary is generated.
- Markers are colour-coded by day (Day 1 = blue, Day 2 = orange, Day 3 = green, etc.).
- A polyline connects each day's activities in order to show the route.
- Clicking a marker scrolls to and highlights the corresponding activity card.
- Nodes with `lat=null` are silently skipped.

### Out of scope
- Turn-by-turn routing or transit directions.
- Satellite / terrain map styles.
- Clustering markers when zoomed out.

---

## Phase 2 — Agentic AI: Replan Day + Local Events Discovery

### Overview

Two independent per-day agents surfaced via action buttons on each day group header
in `ResultDisplay`. Each operates only on the selected day and upserts its result
back to Supabase, leaving all other days untouched.

### UI entry point

Each day group header gains two small buttons:
```
[ Day 1 — March 5 ]   [ Replan ▾ ]  [ Find Events ]
  └ Activity card
  └ Activity card
```

**Replan ▾** opens a small dropdown:
- "It's raining"
- "I'm tired — lighter day"
- "Make it budget-friendly"
- "Custom…" (free text input)

**Find Events** fires immediately with no additional input.

Each button has an independent per-day loading state. The rest of the UI stays
interactive while a day is being replanned.

### Backend — Replan Day

**New endpoint: `POST /api/trips/{trip_id}/replan-day`**

Request body:
```json
{
  "date": "2026-03-05",
  "reason": "raining",
  "nodes": [ ...existing ItineraryNode list for that day... ]
}
```

- Builds a prompt giving Nova the existing day's activities plus the replan reason.
  E.g. for `"raining"`: "Replace outdoor activities with indoor alternatives. Keep meal
  times roughly the same. Respect the same start time and overall pace."
- Calls `extract_itinerary()` using the same tool-call pattern already in
  `openrouter.py` / `bedrock.py`.
- Bulk-upserts the new nodes to Supabase via the existing `update_nodes` helper.
- Returns the new node list for that day only.

### Backend — Local Events Discovery

**New endpoint: `POST /api/trips/{trip_id}/discover-events`**

Request body:
```json
{
  "date": "2026-03-05",
  "trip_location": "Kyoto"
}
```

- Constructs targeted web search queries, e.g.:
  `"events festivals Kyoto March 5 2026"`, `"things happening Kyoto this weekend"`
- Runs them through the existing `web_research_worker`.
- Filters results to only real dated events (Nova reviews snippets and decides which
  are genuine events vs generic listings).
- Nova proposes 1–3 event insertions with suggested times that fit gaps in the
  existing day.
- Returns proposed nodes with a `proposed: true` flag — not yet persisted.

### Frontend — proposed event cards

Proposed event cards from "Find Events" render inline with a distinct "Suggested"
badge. Each card has **Accept** and **Dismiss** controls. Accepting calls
`POST /api/trips/{trip_id}/nodes` to persist that node; dismissing removes it from
the UI.

### Out of scope
- Replanning multiple days at once.
- Undo/redo for replan actions.
- Conflict detection between accepted events and existing activities.

---

## Phase 3 — Multimodal Embeddings: Photo Vibe Input + Per-Card Vibe Match

### Overview

User uploads 1–5 inspiration photos ("the kind of trip I want"). Nova multimodal
embeddings encode each photo. Each activity's title + description is also embedded.
Cosine similarity is computed between every photo–activity pair; each activity's final
score is the **max** across all photos (not the average), so an activity scores high
if it resonates with _any_ of the user's photos.

### Why max-pooling, not averaging
Averaging embedding vectors of semantically diverse photos (e.g. a hiking trail and a
temple) produces a centroid that corresponds to neither concept. Max-pooling preserves
the signal: a hiking activity scores high because it matches the hiking photo,
regardless of the temple photo. This naturally handles mixed vibe intentions without
any special-case logic.

### Backend

**New endpoint: `POST /api/trips/{trip_id}/vibe-embed`**

- Accepts 1–5 uploaded images (multipart form).
- Sends each to `amazon.nova-embed-image-v1` via Bedrock to produce a fixed-size
  embedding vector per photo.
- Embeds each activity's `"{title}. {description}"` text using Nova's text embedding.
- Computes cosine similarity between every (photo, activity) pair.
- For each activity, records:
  - `vibe_score`: max cosine similarity across all photos
  - `best_photo_index`: which photo index produced the max score
- Caches photo embeddings and activity embeddings in `session_cache` keyed by
  `trip_id` (same 15-min TTL as scaffold sessions).
- Returns `{ node_id → { vibe_score, best_photo_index } }` map.

**New endpoint: `POST /api/trips/{trip_id}/vibe-scores`**

Lightweight re-score: if the user adds or removes a photo, recomputes scores using
cached activity embeddings without re-embedding activities. Accepts new photo set,
re-embeds only the photos, returns updated scores.

### Frontend

- A compact **"Vibe photos"** drop zone appears above the result cards (3–5 photo
  slots). Labeled "Drop photos of the kind of trip you want."
- Once photos are uploaded and the itinerary is visible, each activity card shows a
  small coloured vibe match bar:
  - Green: score ≥ 0.70
  - Amber: score 0.40–0.69
  - Grey: score < 0.40
- Hovering the bar shows a tooltip: "Matches photo 2" (thumbnail preview of the
  matched photo).
- Scores update live when photos are added or removed.

### Out of scope
- Using vibe scores to automatically reorder or filter activities.
- Storing vibe embeddings to Supabase (session cache only).
- Generating activity images from embeddings.

---

## Shared constraints

- All new backend endpoints follow the existing FastAPI + Pydantic pattern in
  `backend/routes/ideas.py`.
- All new workers follow the existing worker pattern in `backend/services/workers/`.
- No new database schema changes required beyond what already exists in Supabase.
- Frontend changes are additive — no existing components are rewritten.
