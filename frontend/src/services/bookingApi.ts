// NovaSync — Booking API service
// Handles SSE streaming connection for automated restaurant bookings

import type { NeedsAuthEvent, NeedsCourseReviewEvent } from "../types";

const API_BASE = import.meta.env.VITE_API_BASE ?? "http://localhost:8000/api";

export interface BookingRequest {
    restaurant_name: string;
    restaurant_description?: string | null;
    trip_location?: string | null;
    restaurant_url?: string | null;
    date: string;
    time: string;
    party_size: number;
    phone_number: string;
}

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

export interface BookingStepEvent {
    action: "navigate" | "phase" | "log" | "click" | "type";
    text: string;
}

interface BookingStreamHandlers {
    onLog?: (event: BookingLogEvent) => void;
    onScreenshot?: (screenshotData: string) => void;
    onResult?: (result: BookingResult) => void;
    onError?: (error: string) => void;
    onStep?: (event: BookingStepEvent) => void;
    onNeedsAuth?: (event: NeedsAuthEvent) => void;
    onNeedsCourseReview?: (event: NeedsCourseReviewEvent) => void;
    onConnected?: (bookingId: string) => void;
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

    if (dataLines.length === 0) {
        return null;
    }

    const raw = dataLines.join("\n");
    try {
        return {
            event: eventName,
            data: JSON.parse(raw) as Record<string, unknown>,
        };
    } catch {
        // If JSON parsing fails, treat it as a plain text message
        return {
            event: eventName,
            data: { message: raw },
        };
    }
}

/**
 * Initiates a booking via SSE connection to /api/bookings/book
 * Streams logs and screenshots from the Nova Act agent in real-time
 */
export async function startBookingStream(
    request: BookingRequest,
    handlers: BookingStreamHandlers = {},
    signal?: AbortSignal
): Promise<void> {
    const url = `${API_BASE}/bookings/book`;

    const res = await fetch(url, {
        method: "POST",
        headers: {
            "Content-Type": "application/json",
            Accept: "text/event-stream",
        },
        body: JSON.stringify(request),
        signal,
    });

    if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(err.detail ?? "Booking request failed");
    }

    if (!res.body) {
        throw new Error("Streaming response body is missing");
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let hasReceivedResult = false;

    try {
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

                switch (parsed.event) {
                    case "log": {
                        const logEvent: BookingLogEvent = {
                            message: String(parsed.data.message ?? ""),
                            type: (parsed.data.type as BookingLogEvent["type"]) ?? "info",
                            timestamp: parsed.data.timestamp as string | undefined,
                        };
                        handlers.onLog?.(logEvent);
                        break;
                    }

                    case "screenshot": {
                        const screenshotData = parsed.data.image_data as string;
                        if (screenshotData) {
                            handlers.onScreenshot?.(screenshotData);
                        }
                        break;
                    }

                    case "result": {
                        hasReceivedResult = true;
                        const result: BookingResult = {
                            success: Boolean(parsed.data.success),
                            error: parsed.data.error as string | undefined,
                            details: parsed.data.details as BookingResult["details"],
                        };
                        handlers.onResult?.(result);
                        break;
                    }

                    case "error": {
                        const errorMessage = String(parsed.data.message ?? "Unknown error");
                        handlers.onError?.(errorMessage);
                        break;
                    }

                    case "step": {
                        handlers.onStep?.({
                            action: parsed.data.action as BookingStepEvent["action"],
                            text: String(parsed.data.text ?? ""),
                        });
                        break;
                    }

                    case "connected": {
                        const bookingId = parsed.data.booking_id as string | undefined;
                        if (bookingId) {
                            handlers.onConnected?.(bookingId);
                        }
                        handlers.onLog?.({
                            message: "Connected to booking agent",
                            type: "info",
                        });
                        break;
                    }

                    case "needs_auth": {
                        const authEvent: NeedsAuthEvent = {
                            type: "needs_auth",
                            message: String(parsed.data.message ?? ""),
                            auth_url: parsed.data.auth_url as string | null | undefined,
                        };
                        handlers.onNeedsAuth?.(authEvent);
                        break;
                    }

                    case "needs_course_review": {
                        const reviewEvent: NeedsCourseReviewEvent = {
                            type: "needs_course_review",
                            message: String(parsed.data.message ?? ""),
                            summary: String(parsed.data.summary ?? ""),
                        };
                        handlers.onNeedsCourseReview?.(reviewEvent);
                        break;
                    }

                    default:
                        // Unknown event type - log for debugging
                        handlers.onLog?.({
                            message: `Received event: ${parsed.event}`,
                            type: "info",
                        });
                }
            }
        }

        // Handle any trailing data in buffer
        if (buffer.trim().length > 0) {
            const trailing = parseSseFrame(buffer.trim());
            if (trailing) {
                switch (trailing.event) {
                    case "result": {
                        hasReceivedResult = true;
                        const result: BookingResult = {
                            success: Boolean(trailing.data.success),
                            error: trailing.data.error as string | undefined,
                            details: trailing.data.details as BookingResult["details"],
                        };
                        handlers.onResult?.(result);
                        break;
                    }
                    case "error": {
                        const errorMessage = String(trailing.data.message ?? "Unknown error");
                        handlers.onError?.(errorMessage);
                        break;
                    }
                }
            }
        }

        // If stream ended without a result, treat as error
        if (!hasReceivedResult) {
            handlers.onError?.("Stream ended without result");
        }
    } finally {
        reader.releaseLock();
    }
}

/**
 * Signal a paused booking agent to resume after the user has signed in.
 * Calls POST /api/bookings/{bookingId}/resume which sets the server-side
 * threading.Event, unblocking the Nova Act agent thread.
 */
export async function resumeBooking(bookingId: string): Promise<void> {
    const url = `${API_BASE}/bookings/${bookingId}/resume`;
    const res = await fetch(url, { method: "POST" });
    if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(err.detail ?? "Failed to resume booking");
    }
}
