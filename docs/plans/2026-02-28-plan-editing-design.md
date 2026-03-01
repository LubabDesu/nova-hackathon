# Plan Editing — Design Document
_2026-02-28_

## Overview

Allow users to edit a generated itinerary in-place: reorder activities via drag-and-drop (within and across days), and inline-edit title, description, start time, and duration per card. All changes are local until the user explicitly saves.

---

## User Flow

1. User generates a plan → result cards appear in read mode.
2. User clicks **Edit Plan** → enters edit mode.
   - Auto-filled transport/filler nodes are hidden (they'd be orphaned by any reorder).
   - "Edit Plan" button label becomes **Save Changes**.
   - A **Re-optimize timings** button appears alongside it.
3. User drags cards to reorder within a day or across days.
4. User clicks into a card to edit title, description, start time, or duration.
5. User clicks **Save Changes** → validation runs → bulk-upserts to Supabase.
   - Or clicks **Re-optimize timings** to get AI-suggested times before saving.

---

## Edit Mode Behaviour

### Drag and Drop

- Library: `@dnd-kit/core` (modern React DnD, replaces deprecated `react-beautiful-dnd`).
- Each day group is a `Droppable` container; each activity card is a `Draggable` item.
- On every drop, `repackDay()` runs on both the source and destination day.

### Repack Algorithm

```
repackDay(nodes, dayStartTime):
  sorted = nodes in their new drop order
  t = dayStartTime (= start_time of the first node before edits)
  for each node in sorted:
    node.start_time_local = t
    node.end_time_local   = t + node.duration_mins
    t = node.end_time_local
  return sorted
```

The same function is called whenever a start time or duration is edited inline, so the day always stays consistent and chronological.

### Inline Card Editing

Three fields are exposed directly on the card (no modal):

| Field | Input | Side effect |
|---|---|---|
| Title | Text input | None |
| Description | Textarea (expands on focus) | None |
| Start time | Time picker (HH:MM) | `repackDay()` from this event onward |
| Duration | Number input (minutes) | `repackDay()` from this event onward |

### Save Validation

Before writing to Supabase, validate:
- No day has activities ending past 23:00 (warn but don't block — user may intend late night).
- All days are chronologically ordered (guaranteed by repack, but assert defensively).

On success: bulk-upsert via `PATCH /api/trips/{trip_id}/nodes`.

---

## Re-optimize Timings

Sends the current activity list (title, activity_type, duration_mins per day — no existing times) to `liquid/lfm-2.5-1.2b-instruct:free` (already configured as `OPENROUTER_URL_SUMMARY_MODEL`). The model assigns plausible start times (museums in morning, food at meal times, etc.). Response is parsed and `repackDay()` is run from the model's suggested anchor times. User reviews the result and then saves.

---

## New Backend Endpoints

### `PATCH /api/trips/{trip_id}/nodes`
Bulk upsert for edited nodes. Body: `{ nodes: ItineraryNode[] }`. Each node must have `id` (for upsert key). Returns updated nodes.

### `POST /api/trips/{trip_id}/reoptimize-timings`
Body: `{ days: [{ date: string, activities: [{ title, activity_type, duration_mins }] }] }`.
Returns: `{ days: [{ date, activities: [{ title, start_time_local, end_time_local }] }] }`.
Uses lightweight free model — fast, essentially free to call.

---

## New Frontend Pieces

| Component / util | Description |
|---|---|
| Edit mode toggle in `ResultDisplay` | State: `isEditing`, button label swap, filler node filter |
| `repackDay(nodes)` utility | Pure function, used on drop + time/duration edit |
| DnD setup with `@dnd-kit/core` | `DndContext`, `SortableContext` per day, sensors |
| Inline edit fields on activity cards | Conditional render in `ResultDisplay` node cards |
| `PATCH /save` call in `api.ts` | Bulk upsert on Save Changes |
| `POST /reoptimize` call in `api.ts` | Re-optimize timings endpoint |

---

## Out of Scope (this iteration)

- Adding brand-new activities from scratch (no empty card creation).
- Editing activity type / badge.
- Undo/redo.
- Multi-select drag.
