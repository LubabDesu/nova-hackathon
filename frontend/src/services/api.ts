// NovaSync — API service layer

import type {
    InputDirectives,
    ItineraryNode,
    ProcessIdeaApiResponse,
    ScaffoldReadyEvent,
    UrlScraperDebugResponse,
} from "../types";
import { supabase } from "../lib/supabase";

async function getAuthHeaders(): Promise<Record<string, string>> {
    const { data: { session } } = await supabase.auth.getSession();
    return session?.access_token
        ? { Authorization: `Bearer ${session.access_token}` }
        : {};
}

const API_BASE = import.meta.env.VITE_API_BASE ?? "http://localhost:8000/api";

interface ProcessIdeaOptions {
    tripId?: string;
    tripLocation?: string;
    startDate?: string;
    endDate?: string;
    tripWindowMode?: "fixed" | "not_decided";
    tripDays?: number;
    files?: File[];
    links?: string[];
    inputDirectives?: InputDirectives;
    debug?: boolean;
}

export interface StreamEvent {
    event: string;
    data: Record<string, unknown>;
}

interface ProcessIdeaStreamHandlers {
    onEvent?: (evt: StreamEvent) => void;
}

function buildFormData(idea: string, options: ProcessIdeaOptions): FormData {
    const {
        tripId,
        tripLocation,
        startDate,
        endDate,
        tripWindowMode = "fixed",
        tripDays,
        files = [],
        links = [],
        inputDirectives = {
            hard_constraints: [],
            soft_preferences: [],
            must_include: [],
            avoid: [],
        },
    } = options;

    const formData = new FormData();
    formData.append("idea", idea);
    formData.append("links", JSON.stringify(links));
    formData.append("input_directives", JSON.stringify(inputDirectives));

    if (tripId) {
        formData.append("trip_id", tripId);
    }
    if (tripLocation) {
        formData.append("trip_location", tripLocation);
    }
    if (startDate) {
        formData.append("start_date", startDate);
    }
    if (endDate) {
        formData.append("end_date", endDate);
    }
    formData.append("trip_window_mode", tripWindowMode);
    if (typeof tripDays === "number" && Number.isFinite(tripDays)) {
        formData.append("trip_days", String(Math.trunc(tripDays)));
    }

    for (const file of files) {
        formData.append("files", file);
    }
    return formData;
}

function parseSseFrame(frame: string): StreamEvent | null {
    const lines = frame.split("\n");
    let eventName = "message";
    const dataLines: string[] = [];

    for (const line of lines) {
        if (line.startsWith("event:")) {
            eventName = line.slice("event:".length).trim();
        } else if (line.startsWith("data:")) {
            dataLines.push(line.slice("data:".length).trimStart());
        }
    }

    if (dataLines.length === 0) {
        return null;
    }

    const raw = dataLines.join("\n");
    return {
        event: eventName,
        data: JSON.parse(raw) as Record<string, unknown>,
    };
}

export async function processIdea(
    idea: string,
    options: ProcessIdeaOptions = {},
): Promise<ProcessIdeaApiResponse> {
    const { debug = false } = options;
    const formData = buildFormData(idea, options);

    const url = new URL(`${API_BASE}/process-idea`);
    if (debug) {
        url.searchParams.set("debug", "true");
    }

    const res = await fetch(url.toString(), {
        method: "POST",
        body: formData,
    });

    if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(err.detail ?? "Request failed");
    }

    return res.json() as Promise<ProcessIdeaApiResponse>;
}

export async function processIdeaStream(
    idea: string,
    options: ProcessIdeaOptions = {},
    handlers: ProcessIdeaStreamHandlers = {},
): Promise<ProcessIdeaApiResponse> {
    const { debug = false } = options;
    const formData = buildFormData(idea, options);

    const url = new URL(`${API_BASE}/process-idea/stream`);
    if (debug) {
        url.searchParams.set("debug", "true");
    }

    const res = await fetch(url.toString(), {
        method: "POST",
        body: formData,
        headers: {
            Accept: "text/event-stream",
        },
    });

    if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(err.detail ?? "Streaming request failed");
    }

    if (!res.body) {
        throw new Error("Streaming response body is missing");
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let finalPayload: ProcessIdeaApiResponse | null = null;
    let streamError: string | null = null;

    while (true) {
        const { done, value } = await reader.read();
        if (done) {
            break;
        }
        buffer += decoder.decode(value, { stream: true });
        const frames = buffer.split("\n\n");
        buffer = frames.pop() ?? "";

        for (const frame of frames) {
            const parsed = parseSseFrame(frame);
            if (!parsed) continue;
            handlers.onEvent?.(parsed);

            if (parsed.event === "final") {
                finalPayload = parsed.data as unknown as ProcessIdeaApiResponse;
            } else if (parsed.event === "error") {
                streamError = String(parsed.data.message ?? "Streaming failed");
            }
        }
    }

    if (buffer.trim().length > 0) {
        const trailing = parseSseFrame(buffer.trim());
        if (trailing) {
            handlers.onEvent?.(trailing);
            if (trailing.event === "final") {
                finalPayload = trailing.data as unknown as ProcessIdeaApiResponse;
            } else if (trailing.event === "error") {
                streamError = String(trailing.data.message ?? "Streaming failed");
            }
        }
    }

    if (streamError) {
        throw new Error(streamError);
    }

    if (!finalPayload) {
        throw new Error("Stream ended without final payload");
    }

    return finalPayload;
}

/**
 * Calls /api/ideas/plan (SSE). Returns when scaffold_ready is received.
 * Streams stage events through onEvent for progress UI.
 */
export async function planIdeaStream(
    idea: string,
    options: ProcessIdeaOptions = {},
    handlers: ProcessIdeaStreamHandlers = {},
): Promise<ScaffoldReadyEvent> {
    const formData = buildFormData(idea, options);
    const res = await fetch(`${API_BASE}/ideas/plan`, {
        method: "POST",
        body: formData,
        headers: { Accept: "text/event-stream", ...(await getAuthHeaders()) },
    });

    if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(err.detail ?? "Plan request failed");
    }
    if (!res.body) throw new Error("Streaming response body is missing");

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let scaffoldPayload: ScaffoldReadyEvent | null = null;
    let streamError: string | null = null;

    while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const frames = buffer.split("\n\n");
        buffer = frames.pop() ?? "";
        for (const frame of frames) {
            const parsed = parseSseFrame(frame);
            if (!parsed) continue;
            handlers.onEvent?.(parsed);
            if (parsed.event === "scaffold_ready") {
                scaffoldPayload = parsed.data as unknown as ScaffoldReadyEvent;
            } else if (parsed.event === "error") {
                streamError = String(parsed.data.message ?? "Planning failed");
            }
        }
    }

    if (buffer.trim().length > 0) {
        const trailing = parseSseFrame(buffer.trim());
        if (trailing) {
            handlers.onEvent?.(trailing);
            if (trailing.event === "scaffold_ready") {
                scaffoldPayload = trailing.data as unknown as ScaffoldReadyEvent;
            } else if (trailing.event === "error") {
                streamError = String(trailing.data.message ?? "Planning failed");
            }
        }
    }

    if (streamError) throw new Error(streamError);
    if (!scaffoldPayload) throw new Error("Stream ended without scaffold_ready event");
    return scaffoldPayload;
}

/**
 * Cancel an in-progress /api/ideas/plan generation by request_id.
 * The request_id comes from the 'accepted' SSE event.
 */
export async function cancelPlanningRequest(requestId: string): Promise<void> {
    const headers = await getAuthHeaders();
    await fetch(`${API_BASE}/ideas/plan/cancel`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...headers },
        body: JSON.stringify({ request_id: requestId }),
    });
}

/**
 * Calls /api/ideas/revise (JSON). Returns revised scaffold text + updated revision count.
 */
export async function reviseScaffold(
    sessionId: string,
    scaffoldText: string,
    userFeedback: string,
): Promise<{ scaffold_text: string; revision_count: number }> {
    const res = await fetch(`${API_BASE}/ideas/revise`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...(await getAuthHeaders()) },
        body: JSON.stringify({
            session_id: sessionId,
            scaffold_text: scaffoldText,
            user_feedback: userFeedback,
        }),
    });
    if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        const detail = err.detail ?? "Revision failed";
        throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    return res.json() as Promise<{ scaffold_text: string; revision_count: number }>;
}

/**
 * Calls /api/ideas/extract (SSE). Returns full itinerary result when done.
 * Streams node_batch events through onEvent for progressive display.
 */
export async function extractIdeaStream(
    sessionId: string,
    approvedScaffold: string,
    handlers: ProcessIdeaStreamHandlers = {},
    debug = false,
): Promise<ProcessIdeaApiResponse> {
    const res = await fetch(`${API_BASE}/ideas/extract`, {
        method: "POST",
        headers: {
            "Content-Type": "application/json",
            Accept: "text/event-stream",
            ...(await getAuthHeaders()),
        },
        body: JSON.stringify({
            session_id: sessionId,
            approved_scaffold: approvedScaffold,
            debug,
        }),
    });

    if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(err.detail ?? "Extract request failed");
    }
    if (!res.body) throw new Error("Streaming response body is missing");

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let finalPayload: ProcessIdeaApiResponse | null = null;
    let streamError: string | null = null;

    while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const frames = buffer.split("\n\n");
        buffer = frames.pop() ?? "";
        for (const frame of frames) {
            const parsed = parseSseFrame(frame);
            if (!parsed) continue;
            handlers.onEvent?.(parsed);
            if (parsed.event === "final") {
                finalPayload = parsed.data as unknown as ProcessIdeaApiResponse;
            } else if (parsed.event === "error") {
                streamError = String(parsed.data.message ?? "Extraction failed");
            }
        }
    }

    if (buffer.trim().length > 0) {
        const trailing = parseSseFrame(buffer.trim());
        if (trailing) {
            handlers.onEvent?.(trailing);
            if (trailing.event === "final") {
                finalPayload = trailing.data as unknown as ProcessIdeaApiResponse;
            } else if (trailing.event === "error") {
                streamError = String(trailing.data.message ?? "Extraction failed");
            }
        }
    }

    if (streamError) throw new Error(streamError);
    if (!finalPayload) throw new Error("Stream ended without final payload");
    return finalPayload;
}

export async function debugUrlScraper(
    links: string[],
): Promise<UrlScraperDebugResponse> {
    const res = await fetch(`${API_BASE}/debug/url-scraper`, {
        method: "POST",
        headers: {
            "Content-Type": "application/json",
        },
        body: JSON.stringify({ links }),
    });

    if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(err.detail ?? "URL scraper debug request failed");
    }

    return res.json() as Promise<UrlScraperDebugResponse>;
}

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

export async function createGroupTrip(data: {
    name: string;
    trip_location?: string | null;
    trip_days?: number;
    max_travelers?: number;
}): Promise<{ group_id: string; join_url: string }> {
    const headers = { ...(await getAuthHeaders()), "Content-Type": "application/json" };
    const res = await fetch(`${API_BASE}/group-trips/create`, {
        method: "POST",
        headers,
        body: JSON.stringify(data),
    });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
}

export async function getGroupTripStatus(groupId: string): Promise<import("../types").GroupPlanStatus> {
    const headers = await getAuthHeaders();
    const res = await fetch(`${API_BASE}/group-trips/${groupId}/status`, { headers });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
}

export async function joinGroupTrip(groupId: string, data: {
    nickname: string;
    free_text?: string;
    input_directives?: Record<string, unknown>;
}): Promise<void> {
    const res = await fetch(`${API_BASE}/group-trips/${groupId}/join`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(data),
    });
    if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: "Unknown error" }));
        throw new Error(err.detail);
    }
}
