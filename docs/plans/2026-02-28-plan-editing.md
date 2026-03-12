# Plan Editing Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Let users enter an edit mode on a generated itinerary to drag-and-drop reorder activities (within and across days), inline-edit title/description/start time/duration per card, and save changes back to Supabase — with an optional "Re-optimize timings" AI call.

**Architecture:** All edits are local React state until "Save Changes" is clicked. Auto-filled filler nodes (synthetic) are hidden in edit mode. A `repackDay()` pure function maintains chronological order after every drag or time edit. Two new backend endpoints handle bulk-save and AI time re-optimization.

**Tech Stack:** `@dnd-kit/core` + `@dnd-kit/sortable` for DnD, FastAPI for two new endpoints, `supabase-py` upsert for persistence, `liquid/lfm-2.5-1.2b-instruct:free` (already in env as `OPENROUTER_URL_SUMMARY_MODEL`) for re-optimize.

---

## Task 1: Add `id` to `ItineraryNode` and wire it through the full stack

The DB assigns a UUID to every node on insert; the frontend needs it to upsert edits later. Currently `ItineraryNode` has no `id` field anywhere in the stack.

**Files:**
- Modify: `backend/models.py` (around line 100 — the `ItineraryNode` class)
- Modify: `frontend/src/types.ts:3-15`

**Step 1: Add `id` to the backend Pydantic model**

Open `backend/models.py`. Find the `ItineraryNode` class and add `id` as an optional field:

```python
class ItineraryNode(BaseModel):
    id: str | None = None          # ← add this line (DB-assigned UUID)
    title: str
    activity_type: str
    duration_mins: int | None = None
    date_local: str | None = None
    start_time_local: str | None = None
    end_time_local: str | None = None
    lat: float | None = None
    long: float | None = None
    description: str | None = None
    segment_origin: str | None = None
    segment_kind: str | None = None
```

**Step 2: Add `id` to the TypeScript type**

In `frontend/src/types.ts`, add `id` to `ItineraryNode`:

```typescript
export interface ItineraryNode {
    id?: string | null;            // ← add this line
    title: string;
    activity_type: string;
    // ... rest unchanged
```

**Step 3: Ensure the API response includes IDs**

Open `backend/routes/ideas.py`. Find the function `_build_response_nodes` or wherever `insert_nodes` result is mapped back to `ItineraryNode` objects. The `insert_nodes` helper in `supabase_client.py` returns the raw Supabase rows (which include `id`). Make sure those rows are used to populate `ItineraryNode.id`.

Search for `insert_nodes(` in `routes/ideas.py`. After that call, the returned `data` list should be mapped like:

```python
# When building nodes for the final response, merge DB ids back in:
saved_rows = insert_nodes(trip_id, nodes)
id_map = {r["title"]: r["id"] for r in saved_rows}
for node in nodes:
    node.id = id_map.get(node.title)
```

(Title-based matching is a rough heuristic sufficient for now since all nodes are freshly inserted from this same call.)

**Step 4: Commit**

```bash
git add backend/models.py frontend/src/types.ts backend/routes/ideas.py
git commit -m "feat: add id field to ItineraryNode and propagate through API response"
```

---

## Task 2: Add `update_nodes` to `supabase_client.py`

**Files:**
- Modify: `backend/services/supabase_client.py`

**Step 1: Add the update helper**

Append to `backend/services/supabase_client.py`:

```python
def update_nodes(nodes: list[ItineraryNode]) -> list[dict]:
    """Bulk-upsert itinerary nodes by id. Only updates editable fields."""
    if not nodes:
        return []
    rows = [
        {
            "id": n.id,
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
    result = supabase.table("itinerary_nodes").upsert(rows, on_conflict="id").execute()
    logger.info("Upserted %d nodes", len(result.data))
    return result.data
```

**Step 2: Commit**

```bash
git add backend/services/supabase_client.py
git commit -m "feat: add update_nodes upsert helper to supabase_client"
```

---

## Task 3: Add two new backend endpoints

**Files:**
- Modify: `backend/routes/ideas.py`
- Modify: `backend/models.py` (add request models)

**Step 1: Add Pydantic request models in `backend/models.py`**

Append to `models.py`:

```python
class BulkUpdateNodesRequest(BaseModel):
    nodes: list[ItineraryNode]

class ReoptimizeDay(BaseModel):
    date: str
    activities: list[dict]  # [{title, activity_type, duration_mins}]

class ReoptimizeTimingsRequest(BaseModel):
    days: list[ReoptimizeDay]
    wake_time: str = "09:00"  # default day start
```

**Step 2: Add the save endpoint to `backend/routes/ideas.py`**

Import `update_nodes` at the top of `routes/ideas.py`:
```python
from services.supabase_client import create_trip, insert_nodes, update_nodes
```

Add the endpoint (after the existing `/extract` route):

```python
@router.patch("/trips/{trip_id}/nodes")
async def bulk_update_nodes(trip_id: str, body: BulkUpdateNodesRequest):
    """Bulk-upsert edited itinerary nodes for a trip."""
    try:
        saved = await asyncio.get_event_loop().run_in_executor(
            None, update_nodes, body.nodes
        )
        return JSONResponse({"updated": len(saved), "nodes": saved})
    except Exception as exc:
        logger.exception("bulk_update_nodes failed for trip %s", trip_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
```

**Step 3: Add the re-optimize endpoint**

Add this endpoint immediately after the one above:

```python
@router.post("/trips/{trip_id}/reoptimize-timings")
async def reoptimize_timings(trip_id: str, body: ReoptimizeTimingsRequest):
    """Use a lightweight model to assign plausible times to activities."""
    import os, httpx, json as _json

    api_key = os.getenv("OPENROUTER_API_KEY", "")
    if not api_key:
        raise HTTPException(status_code=500, detail="OPENROUTER_API_KEY not set")

    model = os.getenv("OPENROUTER_URL_SUMMARY_MODEL", "liquid/lfm-2.5-1.2b-instruct:free")

    day_lines = []
    for day in body.days:
        acts = "; ".join(
            f"{a.get('title','?')} ({a.get('activity_type','?')}, {a.get('duration_mins',60)}min)"
            for a in day.activities
        )
        day_lines.append(f"Date {day.date}: {acts}")

    prompt = (
        f"Day starts at {body.wake_time}. Assign realistic start times to these activities.\n"
        "Rules: sightseeing/culture in morning, food at meal times (lunch 12:00, dinner 18:30), "
        "accommodation/rest in evening. No gaps between consecutive activities.\n"
        "Return ONLY valid JSON, no prose:\n"
        '{"days":[{"date":"YYYY-MM-DD","activities":[{"title":"...","start_time_local":"HH:MM","end_time_local":"HH:MM"}]}]}\n\n'
        + "\n".join(day_lines)
    )

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": model, "messages": [{"role": "user", "content": prompt}]},
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"].strip()

    # Strip markdown fences if present
    if content.startswith("```"):
        content = content.split("```")[1]
        if content.startswith("json"):
            content = content[4:]

    try:
        result = _json.loads(content)
    except _json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail=f"Model returned invalid JSON: {content[:200]}") from exc

    return JSONResponse(result)
```

**Step 4: Import `BulkUpdateNodesRequest` and `ReoptimizeTimingsRequest` in routes**

In `routes/ideas.py`, update the models import:
```python
from models import (
    BulkUpdateNodesRequest,
    ExtractRequest,
    InputDirectives,
    ItineraryNode,
    ProcessIdeaRequest,
    ProcessIdeaResponse,
    ReoptimizeTimingsRequest,
    ReviseRequest,
)
```

**Step 5: Test the endpoints manually**

Start the backend: `cd backend && uvicorn main:app --reload --port 8000`

```bash
# Test save endpoint (replace TRIP_ID with a real one from a previous run)
curl -X PATCH http://localhost:8000/api/trips/TEST_TRIP_ID/nodes \
  -H "Content-Type: application/json" \
  -d '{"nodes": [{"id": "some-uuid", "title": "Test", "activity_type": "culture", "duration_mins": 60, "date_local": "2026-03-01", "start_time_local": "09:00", "end_time_local": "10:00"}]}'
# Expected: {"updated": 1, "nodes": [...]}

# Test reoptimize endpoint
curl -X POST http://localhost:8000/api/trips/TEST_TRIP_ID/reoptimize-timings \
  -H "Content-Type: application/json" \
  -d '{"days": [{"date": "2026-03-01", "activities": [{"title": "Fushimi Inari", "activity_type": "sightseeing", "duration_mins": 120}, {"title": "Lunch", "activity_type": "food", "duration_mins": 60}]}], "wake_time": "09:00"}'
# Expected: {"days": [{"date": "2026-03-01", "activities": [...with times...]}]}
```

**Step 6: Commit**

```bash
git add backend/models.py backend/routes/ideas.py
git commit -m "feat: add bulk-update-nodes and reoptimize-timings endpoints"
```

---

## Task 4: Install `@dnd-kit` and create `repackDay` utility

**Files:**
- Create: `frontend/src/utils/itineraryUtils.ts`

**Step 1: Install DnD libraries**

```bash
cd frontend && npm install @dnd-kit/core @dnd-kit/sortable @dnd-kit/utilities
```

Expected: packages added to `node_modules` and `package.json`.

**Step 2: Create `frontend/src/utils/itineraryUtils.ts`**

```typescript
import type { ItineraryNode } from "../types";

/** Add HH:MM + minutes → HH:MM string */
function addMins(time: string, mins: number): string {
    const [h, m] = time.split(":").map(Number);
    const total = h * 60 + m + mins;
    const hh = String(Math.floor(total / 60) % 24).padStart(2, "0");
    const mm = String(total % 60).padStart(2, "0");
    return `${hh}:${mm}`;
}

/**
 * Given an ordered list of nodes for a single day, re-assigns start/end times
 * sequentially from the first node's current start time, preserving durations.
 * If the first node has no start time, defaults to "09:00".
 */
export function repackDay(nodes: ItineraryNode[]): ItineraryNode[] {
    if (nodes.length === 0) return [];
    let cursor = nodes[0].start_time_local ?? "09:00";
    return nodes.map((node) => {
        const duration = node.duration_mins ?? 60;
        const start = cursor;
        const end = addMins(start, duration);
        cursor = end;
        return { ...node, start_time_local: start, end_time_local: end };
    });
}

/** Returns warning strings if any day has activities ending past 23:00. */
export function validatePlan(
    dayGroups: Array<{ key: string; items: ItineraryNode[] }>,
): string[] {
    const warnings: string[] = [];
    for (const group of dayGroups) {
        const last = group.items[group.items.length - 1];
        if (last?.end_time_local && last.end_time_local > "23:00") {
            warnings.push(
                `Day ${group.key}: activities end at ${last.end_time_local}, past 23:00.`,
            );
        }
    }
    return warnings;
}

/** True if a node is an auto-generated filler (hidden in edit mode). */
export function isFillerNode(node: ItineraryNode): boolean {
    return node.segment_origin === "synthetic";
}
```

**Step 3: Verify TypeScript compiles**

```bash
cd frontend && npx tsc --noEmit
```

Expected: no errors.

**Step 4: Commit**

```bash
git add frontend/src/utils/itineraryUtils.ts frontend/package.json frontend/package-lock.json
git commit -m "feat: add repackDay utility and install @dnd-kit"
```

---

## Task 5: Add edit mode state and API functions

**Files:**
- Modify: `frontend/src/services/api.ts`
- Modify: `frontend/src/types.ts`

**Step 1: Add API functions to `frontend/src/services/api.ts`**

Append to the end of `api.ts`:

```typescript
/** Bulk-upsert edited nodes back to Supabase via the backend. */
export async function saveEditedNodes(
    tripId: string,
    nodes: ItineraryNode[],
): Promise<{ updated: number }> {
    const res = await fetch(`${API_BASE}/trips/${tripId}/nodes`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ nodes }),
    });
    if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(err.detail ?? "Save failed");
    }
    return res.json() as Promise<{ updated: number }>;
}

export interface ReoptimizeResult {
    days: Array<{
        date: string;
        activities: Array<{ title: string; start_time_local: string; end_time_local: string }>;
    }>;
}

/** Ask the lightweight model to assign plausible times to the current activity list. */
export async function reoptimizeTimings(
    tripId: string,
    dayGroups: Array<{ date: string; activities: Array<{ title: string; activity_type: string; duration_mins: number | null }> }>,
    wakeTime = "09:00",
): Promise<ReoptimizeResult> {
    const res = await fetch(`${API_BASE}/trips/${tripId}/reoptimize-timings`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ days: dayGroups, wake_time: wakeTime }),
    });
    if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(err.detail ?? "Re-optimize failed");
    }
    return res.json() as Promise<ReoptimizeResult>;
}
```

**Step 2: Verify TypeScript compiles**

```bash
cd frontend && npx tsc --noEmit
```

Expected: no errors.

**Step 3: Commit**

```bash
git add frontend/src/services/api.ts
git commit -m "feat: add saveEditedNodes and reoptimizeTimings API functions"
```

---

## Task 6: Edit mode UI in `ResultDisplay`

This is the largest task. Add edit mode toggle, edit state, and the "Edit Plan" / "Save Changes" header buttons.

**Files:**
- Modify: `frontend/src/components/ResultDisplay.tsx`

**Step 1: Add imports at the top of `ResultDisplay.tsx`**

Add to the existing import block (around line 3):

```typescript
import {
    DndContext,
    closestCenter,
    KeyboardSensor,
    PointerSensor,
    useSensor,
    useSensors,
    type DragEndEvent,
} from "@dnd-kit/core";
import {
    SortableContext,
    sortableKeyboardCoordinates,
    useSortable,
    verticalListSortingStrategy,
    arrayMove,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { repackDay, validatePlan, isFillerNode } from "../utils/itineraryUtils";
import { saveEditedNodes, reoptimizeTimings } from "../services/api";
```

**Step 2: Add props to `ResultDisplayProps`**

In the `ResultDisplayProps` interface (around line 6), add:

```typescript
interface ResultDisplayProps {
    // ... existing props ...
    tripId: string | null;
    onNodesChange?: (nodes: ItineraryNode[]) => void; // called after successful save
}
```

**Step 3: Add edit mode state inside the `ResultDisplay` function**

After the existing `useState` calls (around line 43), add:

```typescript
const [isEditing, setIsEditing] = useState(false);
const [editedNodes, setEditedNodes] = useState<ItineraryNode[]>([]);
const [isSaving, setIsSaving] = useState(false);
const [isReoptimizing, setIsReoptimizing] = useState(false);
const [saveError, setSaveError] = useState<string | null>(null);

// When editing starts, populate editedNodes from current nodes (excluding fillers)
function enterEditMode() {
    setEditedNodes(nodes.filter((n) => !isFillerNode(n)));
    setIsEditing(true);
    setSaveError(null);
}

function cancelEditMode() {
    setIsEditing(false);
    setEditedNodes([]);
    setSaveError(null);
}
```

**Step 4: Add `editedDayGroups` derived from `editedNodes` during edit mode**

After the existing `dayGroups` useMemo (around line 107), add:

```typescript
const editedDayGroups = useMemo(() => {
    if (!isEditing) return [];
    const buckets = new Map<string, ItineraryNode[]>();
    for (const node of editedNodes) {
        const key = node.date_local ?? "unscheduled";
        const bucket = buckets.get(key) ?? [];
        bucket.push(node);
        buckets.set(key, bucket);
    }
    const sortedKeys = [...buckets.keys()].sort((a, b) => {
        if (a === "unscheduled") return 1;
        if (b === "unscheduled") return -1;
        return a.localeCompare(b);
    });
    return sortedKeys.map((key) => ({ key, items: buckets.get(key) ?? [] }));
}, [isEditing, editedNodes]);
```

**Step 5: Add drag-end handler**

After `editedDayGroups`, add:

```typescript
const sensors = useSensors(
    useSensor(PointerSensor),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
);

function handleDragEnd(event: DragEndEvent) {
    const { active, over } = event;
    if (!over || active.id === over.id) return;

    // Each draggable id is `${date}::${nodeIndex}` — parse to find source day
    const [activeDate, activeIdxStr] = String(active.id).split("::");
    const [overDate, overIdxStr] = String(over.id).split("::");
    const activeIdx = parseInt(activeIdxStr, 10);
    const overIdx = parseInt(overIdxStr, 10);

    setEditedNodes((prev) => {
        // Split into per-day buckets
        const byDay = new Map<string, ItineraryNode[]>();
        for (const n of prev) {
            const k = n.date_local ?? "unscheduled";
            const b = byDay.get(k) ?? [];
            b.push(n);
            byDay.set(k, b);
        }

        if (activeDate === overDate) {
            // Same day: reorder within day
            const dayNodes = byDay.get(activeDate) ?? [];
            const reordered = arrayMove(dayNodes, activeIdx, overIdx);
            byDay.set(activeDate, repackDay(reordered));
        } else {
            // Cross-day: move node to new day
            const srcNodes = byDay.get(activeDate) ?? [];
            const [moved] = srcNodes.splice(activeIdx, 1);
            byDay.set(activeDate, repackDay(srcNodes));

            const dstNodes = byDay.get(overDate) ?? [];
            const movedToDst = { ...moved, date_local: overDate };
            dstNodes.splice(overIdx, 0, movedToDst);
            byDay.set(overDate, repackDay(dstNodes));
        }

        // Flatten back to ordered list
        const sortedKeys = [...byDay.keys()].sort((a, b) => a.localeCompare(b));
        return sortedKeys.flatMap((k) => byDay.get(k) ?? []);
    });
}
```

**Step 6: Add save handler**

```typescript
async function handleSave() {
    if (!tripId) return;
    const warnings = validatePlan(editedDayGroups);
    // Warn but don't block
    if (warnings.length > 0) {
        console.warn("Plan warnings:", warnings);
    }
    setIsSaving(true);
    setSaveError(null);
    try {
        await saveEditedNodes(tripId, editedNodes);
        onNodesChange?.(editedNodes);
        setIsEditing(false);
        setEditedNodes([]);
    } catch (err) {
        setSaveError(err instanceof Error ? err.message : "Save failed");
    } finally {
        setIsSaving(false);
    }
}
```

**Step 7: Add re-optimize handler**

```typescript
async function handleReoptimize() {
    if (!tripId) return;
    setIsReoptimizing(true);
    setSaveError(null);
    try {
        const dayPayload = editedDayGroups.map((g) => ({
            date: g.key,
            activities: g.items.map((n) => ({
                title: n.title,
                activity_type: n.activity_type,
                duration_mins: n.duration_mins,
            })),
        }));
        const result = await reoptimizeTimings(tripId, dayPayload);

        // Merge new times back into editedNodes
        const timeMap = new Map<string, { start: string; end: string }>();
        for (const day of result.days) {
            for (const act of day.activities) {
                timeMap.set(act.title, {
                    start: act.start_time_local,
                    end: act.end_time_local,
                });
            }
        }
        setEditedNodes((prev) =>
            prev.map((n) => {
                const t = timeMap.get(n.title);
                return t
                    ? { ...n, start_time_local: t.start, end_time_local: t.end }
                    : n;
            }),
        );
    } catch (err) {
        setSaveError(err instanceof Error ? err.message : "Re-optimize failed");
    } finally {
        setIsReoptimizing(false);
    }
}
```

**Step 8: Update the result header JSX**

Find the `result-header` div (around line 704):

```tsx
<div className="result-header">
    <h2 className="zone-title">
        <span className="zone-icon">📋</span> Extracted Itinerary
    </h2>
    <div style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
        {isEditing && (
            <button
                type="button"
                className="scaffold-btn scaffold-btn-revise"
                onClick={handleReoptimize}
                disabled={isReoptimizing || isSaving}
            >
                {isReoptimizing ? "Optimizing…" : "Re-optimize timings"}
            </button>
        )}
        {nodes.length > 0 && tripId && (
            isEditing ? (
                <>
                    <button
                        type="button"
                        className="scaffold-btn scaffold-btn-revise"
                        onClick={cancelEditMode}
                        disabled={isSaving}
                    >
                        Cancel
                    </button>
                    <button
                        type="button"
                        className="scaffold-btn scaffold-btn-approve"
                        onClick={handleSave}
                        disabled={isSaving}
                    >
                        {isSaving ? "Saving…" : "Save Changes"}
                    </button>
                </>
            ) : (
                <button
                    type="button"
                    className="scaffold-btn scaffold-btn-revise"
                    onClick={enterEditMode}
                    disabled={loading}
                >
                    Edit Plan
                </button>
            )
        )}
        {tripId && (
            <span className="trip-badge">Trip: {tripId.slice(0, 8)}…</span>
        )}
    </div>
</div>
{saveError && (
    <p style={{ color: "#dc2626", fontSize: "0.8rem", marginBottom: "0.5rem" }}>
        {saveError}
    </p>
)}
```

**Step 9: Commit**

```bash
git add frontend/src/components/ResultDisplay.tsx
git commit -m "feat: add edit mode state, drag handlers, save/reoptimize logic to ResultDisplay"
```

---

## Task 7: Render editable day groups with DnD

**Files:**
- Modify: `frontend/src/components/ResultDisplay.tsx`

This step replaces the read-mode `day-groups` render with a conditional that shows either read mode or edit mode.

**Step 1: Create a `SortableCard` sub-component at the top of `ResultDisplay.tsx`**

Add this before the `ResultDisplay` function definition:

```tsx
function SortableCard({
    id,
    node,
    onNodeChange,
}: {
    id: string;
    node: ItineraryNode;
    onNodeChange: (updated: ItineraryNode) => void;
}) {
    const { attributes, listeners, setNodeRef, transform, transition, isDragging } =
        useSortable({ id });

    const style: React.CSSProperties = {
        transform: CSS.Transform.toString(transform),
        transition,
        opacity: isDragging ? 0.5 : 1,
        cursor: "grab",
    };

    return (
        <div
            ref={setNodeRef}
            style={style}
            className="node-card day-node-card"
        >
            {/* Drag handle */}
            <div
                {...attributes}
                {...listeners}
                style={{ cursor: "grab", fontSize: "0.75rem", color: "#94a3b8", marginBottom: "0.4rem", userSelect: "none" }}
            >
                ⠿ drag to reorder
            </div>

            {/* Time row */}
            <div className="schedule-row" style={{ gap: "0.5rem", flexWrap: "wrap" }}>
                <div style={{ display: "flex", alignItems: "center", gap: "0.3rem" }}>
                    <label style={{ fontSize: "0.72rem", color: "#64748b" }}>Start</label>
                    <input
                        type="time"
                        value={node.start_time_local ?? ""}
                        onChange={(e) => onNodeChange({ ...node, start_time_local: e.target.value })}
                        style={{ fontSize: "0.78rem", border: "1px solid #e2e8f0", borderRadius: "4px", padding: "0.1rem 0.3rem" }}
                    />
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: "0.3rem" }}>
                    <label style={{ fontSize: "0.72rem", color: "#64748b" }}>Duration</label>
                    <input
                        type="number"
                        min={5}
                        step={5}
                        value={node.duration_mins ?? ""}
                        onChange={(e) => onNodeChange({ ...node, duration_mins: parseInt(e.target.value, 10) || null })}
                        style={{ width: "4rem", fontSize: "0.78rem", border: "1px solid #e2e8f0", borderRadius: "4px", padding: "0.1rem 0.3rem" }}
                    />
                    <span style={{ fontSize: "0.72rem", color: "#64748b" }}>min</span>
                </div>
            </div>

            {/* Editable title */}
            <input
                type="text"
                value={node.title}
                onChange={(e) => onNodeChange({ ...node, title: e.target.value })}
                style={{
                    width: "100%",
                    fontWeight: 600,
                    fontSize: "1rem",
                    border: "1px solid #e2e8f0",
                    borderRadius: "6px",
                    padding: "0.3rem 0.5rem",
                    marginBottom: "0.3rem",
                    fontFamily: "inherit",
                }}
            />

            {/* Editable description */}
            <textarea
                value={node.description ?? ""}
                onChange={(e) => onNodeChange({ ...node, description: e.target.value })}
                rows={2}
                style={{
                    width: "100%",
                    fontSize: "0.85rem",
                    color: "#475569",
                    border: "1px solid #e2e8f0",
                    borderRadius: "6px",
                    padding: "0.3rem 0.5rem",
                    fontFamily: "inherit",
                    resize: "vertical",
                }}
            />
        </div>
    );
}
```

**Step 2: Add a helper to update a single node in editedNodes**

Inside the `ResultDisplay` function, after the reoptimize handler, add:

```typescript
function updateEditedNode(updated: ItineraryNode, dayKey: string, indexInDay: number) {
    setEditedNodes((prev) => {
        // Rebuild: find this node by dayKey + index, update it, then repack the day
        const byDay = new Map<string, ItineraryNode[]>();
        for (const n of prev) {
            const k = n.date_local ?? "unscheduled";
            const b = byDay.get(k) ?? [];
            b.push(n);
            byDay.set(k, b);
        }
        const dayNodes = [...(byDay.get(dayKey) ?? [])];
        dayNodes[indexInDay] = updated;
        // Repack from the changed node onward (anchor the changed node's start time)
        const before = dayNodes.slice(0, indexInDay);
        const from = dayNodes.slice(indexInDay);
        byDay.set(dayKey, [...before, ...repackDay(from)]);

        const sortedKeys = [...byDay.keys()].sort((a, b) => a.localeCompare(b));
        return sortedKeys.flatMap((k) => byDay.get(k) ?? []);
    });
}
```

**Step 3: Replace the `day-groups` render with conditional read/edit mode**

Find the JSX block starting with `<div className="day-groups">` (around line 715). Replace it with:

```tsx
{isEditing ? (
    <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
        <div className="day-groups">
            {editedDayGroups.map((group, dayIndex) => {
                const ids = group.items.map((_, i) => `${group.key}::${i}`);
                return (
                    <section className="day-group" key={group.key}>
                        <header className="day-group-header">
                            <div>
                                <p className="day-group-kicker">Day {dayIndex + 1}</p>
                                <h3 className="day-group-title">{group.key}</h3>
                                <p className="day-group-meta">{group.items.length} activities</p>
                            </div>
                        </header>
                        <SortableContext items={ids} strategy={verticalListSortingStrategy}>
                            <div className="nodes-grid day-nodes-grid">
                                {group.items.map((node, i) => (
                                    <SortableCard
                                        key={`${group.key}-${i}`}
                                        id={`${group.key}::${i}`}
                                        node={node}
                                        onNodeChange={(updated) => updateEditedNode(updated, group.key, i)}
                                    />
                                ))}
                            </div>
                        </SortableContext>
                    </section>
                );
            })}
        </div>
    </DndContext>
) : (
    <div className="day-groups">
        {/* existing read-mode day group render — leave completely unchanged */}
        {dayGroups.map((group, dayIndex) => (
            /* ... existing code ... */
        ))}
    </div>
)}
```

**Step 4: TypeScript check**

```bash
cd frontend && npx tsc --noEmit
```

Fix any type errors (most likely around `React.CSSProperties` — add `import type { CSSProperties } from "react"` if needed).

**Step 5: Commit**

```bash
git add frontend/src/components/ResultDisplay.tsx
git commit -m "feat: render editable DnD day groups with SortableCard in edit mode"
```

---

## Task 8: Wire `onNodesChange` in `App.tsx`

**Files:**
- Modify: `frontend/src/App.tsx`

**Step 1: Find where `ResultDisplay` is rendered (around line 1551) and add `onNodesChange`**

```tsx
<ResultDisplay
    nodes={nodes}
    tripId={tripId}
    onNodesChange={(updated) => {
        // Replace the nodes in App state with the saved version
        // Use the same mergeNodesByIdentity helper already in App.tsx
        setNodes(updated);
    }}
    // ... rest of existing props unchanged
/>
```

**Step 2: Verify the app compiles and runs**

```bash
cd frontend && npx tsc --noEmit
```

Then open the browser, generate a plan, and:
1. Click "Edit Plan" — filler nodes disappear, edit inputs appear on each card
2. Drag a card to reorder — times update automatically
3. Edit a title/description — text updates inline
4. Edit a start time — subsequent times cascade
5. Click "Re-optimize timings" — times are reassigned by the model
6. Click "Save Changes" — should succeed if `tripId` is set

**Step 3: Commit**

```bash
git add frontend/src/App.tsx
git commit -m "feat: wire onNodesChange from ResultDisplay to App state"
```

---

## Done

The full edit flow is now:

```
Generate plan → nodes render in read mode
  ↓ click "Edit Plan"
Edit mode: filler nodes hidden, cards are draggable + inline-editable
  ↓ drag cards (within day or across days) → repackDay() fires automatically
  ↓ edit title/description/start time/duration → repackDay() fires on time/duration change
  ↓ (optional) click "Re-optimize timings" → lightweight model reassigns times
  ↓ click "Save Changes"
validatePlan() → PATCH /api/trips/{tripId}/nodes → Supabase upsert
onNodesChange() → App state updated → back to read mode
```
