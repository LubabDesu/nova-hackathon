// NovaSync — Booking API service
// Session-based HITL flow: start → stream → resume → result

import type { BookingStartRequest, BookingResumeData } from "../types";

const API_BASE = import.meta.env.VITE_API_BASE ?? "http://localhost:8000/api";

export interface BookingLogEvent {
    message: string;
    type: "info" | "action" | "success" | "error";
    timestamp?: string;
}

export interface BookingResult {
    success: boolean;
    error?: string;
    details?: {
        confirmation_number?: string;
        booking_time?: string;
        notes?: string;
    };
}

interface BookingStreamHandlers {
    onLog?: (event: BookingLogEvent) => void;
    onScreenshot?: (screenshotData: string) => void;
    onResult?: (result: BookingResult) => void;
    onError?: (error: string) => void;
    onNeedsUserInput?: (event: { fields: string[] }) => void;
}

interface SSEEvent {
    event: string;
    data: Record<string, unknown>;
}

function parseSseFrame(frame: string): SSEEvent | null {
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

    if (dataLines.length === 0) return null;

    const raw = dataLines.join("\n");
    try {
        return { event: eventName, data: JSON.parse(raw) as Record<string, unknown> };
    } catch {
        return { event: eventName, data: { message: raw } };
    }
}

/**
 * Start a booking session. Returns session_id.
 */
export async function startBooking(req: BookingStartRequest): Promise<{ session_id: string }> {
    const res = await fetch(`${API_BASE}/bookings/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(req),
    });
    if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(err.detail ?? "Failed to start booking");
    }
    return res.json();
}

/**
 * Stream SSE events from a booking session.
 */
export async function streamBooking(
    sessionId: string,
    handlers: BookingStreamHandlers = {},
    signal?: AbortSignal
): Promise<void> {
    const res = await fetch(`${API_BASE}/bookings/${sessionId}/stream`, {
        method: "GET",
        headers: { Accept: "text/event-stream" },
        signal,
    });

    if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(err.detail ?? "Streaming failed");
    }
    if (!res.body) throw new Error("Streaming response body is missing");

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let hasReceivedResult = false;

    try {
        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            const frames = buffer.split("\n\n");
            buffer = frames.pop() ?? "";

            for (const frame of frames) {
                if (frame.startsWith(":")) continue; // keepalive comment
                const parsed = parseSseFrame(frame);
                if (!parsed) continue;

                switch (parsed.event) {
                    case "log":
                        handlers.onLog?.({
                            message: String(parsed.data.message ?? ""),
                            type: (parsed.data.type as BookingLogEvent["type"]) ?? "info",
                            timestamp: parsed.data.timestamp as string | undefined,
                        });
                        break;
                    case "screenshot":
                        if (parsed.data.image_data) {
                            handlers.onScreenshot?.(parsed.data.image_data as string);
                        }
                        break;
                    case "needs_user_input":
                        handlers.onNeedsUserInput?.({
                            fields: (parsed.data.fields as string[]) ?? [],
                        });
                        break;
                    case "result":
                        hasReceivedResult = true;
                        handlers.onResult?.({
                            success: Boolean(parsed.data.success),
                            error: parsed.data.error as string | undefined,
                            details: parsed.data.details as BookingResult["details"],
                        });
                        break;
                    case "error":
                        handlers.onError?.(String(parsed.data.message ?? "Unknown error"));
                        break;
                    case "connected":
                        handlers.onLog?.({ message: "Connected to booking agent", type: "info" });
                        break;
                }
            }
        }

        if (!hasReceivedResult) {
            handlers.onError?.("Stream ended without result");
        }
    } finally {
        reader.releaseLock();
    }
}

/**
 * Resume a paused booking session with user-supplied sensitive data.
 */
export async function resumeBooking(sessionId: string, data: BookingResumeData): Promise<void> {
    const res = await fetch(`${API_BASE}/bookings/${sessionId}/resume`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(data),
    });
    if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(err.detail ?? "Failed to resume booking");
    }
}

/**
 * Cancel and clean up a booking session.
 */
export async function cancelBooking(sessionId: string): Promise<void> {
    await fetch(`${API_BASE}/bookings/${sessionId}`, { method: "DELETE" }).catch(() => {
        // Best-effort cancellation
    });
}
