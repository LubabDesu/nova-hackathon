// NovaSync — Booking Panel component
// Session-based HITL flow: start → stream → pause for phone → resume → result

import { useState, useRef, useCallback, useEffect } from "react";
import {
    startBooking,
    streamBooking,
    resumeBooking,
    cancelBooking,
    type BookingLogEvent,
    type BookingResult,
} from "../services/bookingApi";
import type { BookingStartRequest, BookingResumeData } from "../types";
import BookingStatusBadge from "./BookingStatusBadge";

type BookingStatus = "idle" | "starting" | "streaming" | "waiting_for_user" | "done" | "error";

interface BookingPanelProps {
    restaurantName: string;
    city: string;
    date: string;
    time: string;
    partySize: number;
    onClose?: () => void;
}

interface LogEntry {
    id: number;
    timestamp: Date;
    message: string;
    type: "info" | "action" | "success" | "error";
}

export default function BookingPanel({
    restaurantName,
    city,
    date,
    time,
    partySize,
    onClose: _onClose,
}: BookingPanelProps) {
    const [status, setStatus] = useState<BookingStatus>("idle");
    const [sessionId, setSessionId] = useState<string | null>(null);
    const [logs, setLogs] = useState<LogEntry[]>([]);
    const [screenshot, setScreenshot] = useState<string | null>(null);
    const [result, setResult] = useState<BookingResult | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [waitingFields, setWaitingFields] = useState<string[]>([]);

    // Sensitive input state — kept local, never in logs
    const [phone, setPhone] = useState("");
    const [resumeEmail, setResumeEmail] = useState("");

    const logIdRef = useRef(0);
    const abortControllerRef = useRef<AbortController | null>(null);
    const sessionIdRef = useRef<string | null>(null);

    const addLog = useCallback((message: string, type: LogEntry["type"] = "info") => {
        const id = ++logIdRef.current;
        setLogs((prev) => [...prev, { id, timestamp: new Date(), message, type }]);
    }, []);

    const formatTime = (d: Date): string =>
        d.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });

    // Cleanup on unmount
    useEffect(() => {
        return () => {
            abortControllerRef.current?.abort();
            if (sessionIdRef.current) {
                cancelBooking(sessionIdRef.current);
            }
        };
    }, []);

    const handleStart = async () => {
        setStatus("starting");
        setLogs([]);
        setScreenshot(null);
        setResult(null);
        setError(null);
        setWaitingFields([]);
        logIdRef.current = 0;

        try {
            const req: BookingStartRequest = {
                restaurant_name: restaurantName,
                city,
                date,
                time,
                party_size: partySize,
            };
            addLog("Starting booking session...", "info");
            const { session_id } = await startBooking(req);
            setSessionId(session_id);
            sessionIdRef.current = session_id;

            setStatus("streaming");
            addLog("Connected to Nova Act agent", "info");

            abortControllerRef.current = new AbortController();
            await streamBooking(
                session_id,
                {
                    onLog: (event: BookingLogEvent) => addLog(event.message, event.type),
                    onScreenshot: (data: string) => setScreenshot(data),
                    onNeedsUserInput: ({ fields }) => {
                        setWaitingFields(fields);
                        setStatus("waiting_for_user");
                        addLog("Agent is waiting for your input...", "info");
                    },
                    onResult: (r: BookingResult) => {
                        setResult(r);
                        setStatus("done");
                        sessionIdRef.current = null;
                        if (r.success) {
                            addLog("Booking completed successfully!", "success");
                        } else {
                            addLog(`Booking failed: ${r.error ?? "Unknown error"}`, "error");
                        }
                    },
                    onError: (msg: string) => {
                        setError(msg);
                        setStatus("error");
                        sessionIdRef.current = null;
                        addLog(`Error: ${msg}`, "error");
                    },
                },
                abortControllerRef.current.signal
            );
        } catch (err) {
            const msg = err instanceof Error ? err.message : "Booking failed";
            setError(msg);
            setStatus("error");
            addLog(`Error: ${msg}`, "error");
        }
    };

    const handleResume = async () => {
        if (!sessionId) return;
        const data: BookingResumeData = {
            phone: phone.trim() || undefined,
            email: resumeEmail.trim() || undefined,
        };
        try {
            await resumeBooking(sessionId, data);
            setStatus("streaming");
            setPhone("");
            setResumeEmail("");
            addLog("Sensitive info submitted, agent resuming...", "info");
        } catch (err) {
            const msg = err instanceof Error ? err.message : "Failed to resume";
            setError(msg);
            setStatus("error");
        }
    };

    const handleCancel = async () => {
        abortControllerRef.current?.abort();
        if (sessionIdRef.current) {
            await cancelBooking(sessionIdRef.current);
            sessionIdRef.current = null;
        }
        setSessionId(null);
        setStatus("idle");
        addLog("Booking cancelled", "info");
    };

    const getBadgeStatus = (): "pending" | "confirmed" | "failed" => {
        if (result?.success) return "confirmed";
        if (status === "error" || (result && !result.success)) return "failed";
        return "pending";
    };

    const isActive = status === "starting" || status === "streaming" || status === "waiting_for_user";

    return (
        <div className="glass-card booking-panel">
            <div className="booking-panel-header">
                <div className="booking-panel-title-row">
                    <h3 className="booking-panel-title">🍽️ Book a Table</h3>
                    {status !== "idle" && <BookingStatusBadge status={getBadgeStatus()} />}
                </div>
                <p className="booking-panel-subtitle">Nova Act will automate the reservation for you</p>
            </div>

            {/* Restaurant info */}
            <div className="booking-restaurant-info">
                <div className="booking-info-row">
                    <span className="booking-info-label">Restaurant</span>
                    <span className="booking-info-value">{restaurantName}</span>
                </div>
                <div className="booking-info-row">
                    <span className="booking-info-label">Location</span>
                    <span className="booking-info-value">{city}</span>
                </div>
                <div className="booking-info-row">
                    <span className="booking-info-label">Date & Time</span>
                    <span className="booking-info-value">{date} at {time}</span>
                </div>
                <div className="booking-info-row">
                    <span className="booking-info-label">Party Size</span>
                    <span className="booking-info-value">{partySize} people</span>
                </div>
            </div>

            {/* Idle: start button */}
            {status === "idle" && (
                <div className="booking-actions">
                    <button type="button" className="sky-btn-primary" onClick={handleStart}>
                        🤖 Book with Nova Act
                    </button>
                </div>
            )}

            {/* Starting spinner */}
            {status === "starting" && (
                <div className="booking-actions">
                    <span style={{ color: "#4a6a8a", fontSize: "0.9rem" }}>Initializing agent...</span>
                </div>
            )}

            {/* Active: cancel button */}
            {(status === "streaming" || status === "waiting_for_user") && (
                <div className="booking-actions">
                    <button type="button" className="sky-btn-danger" onClick={handleCancel}>
                        ⏹ Cancel Booking
                    </button>
                </div>
            )}

            {/* HITL: sensitive fields form */}
            {status === "waiting_for_user" && (
                <div className="booking-phone-section" style={{ marginTop: 16, padding: "1rem", background: "rgba(74,141,196,0.08)", borderRadius: 8, border: "1px solid rgba(74,141,196,0.2)" }}>
                    {waitingFields.includes("manual_required") ? (
                        <p style={{ color: "#4a6a8a", fontSize: "0.9rem", margin: 0 }}>
                            ⚠️ Agent couldn't complete this step automatically. Please finish the booking manually in the browser window.
                        </p>
                    ) : (
                        <>
                            <p style={{ color: "#0b1f38", fontSize: "0.88rem", fontWeight: 600, marginBottom: 12 }}>
                                🔒 Agent needs your contact details to proceed:
                            </p>
                            {waitingFields.includes("phone") && (
                                <div style={{ marginBottom: 10 }}>
                                    <label className="booking-phone-label" htmlFor="resume-phone">
                                        Phone Number <span className="booking-required">*</span>
                                        <span className="booking-phone-hint">(required by Japanese restaurants)</span>
                                    </label>
                                    <input
                                        id="resume-phone"
                                        type="tel"
                                        className="sky-input"
                                        placeholder="+81 90 1234 5678"
                                        value={phone}
                                        onChange={(e) => setPhone(e.target.value)}
                                    />
                                </div>
                            )}
                            {waitingFields.includes("email") && (
                                <div style={{ marginBottom: 10 }}>
                                    <label className="booking-phone-label" htmlFor="resume-email">Email</label>
                                    <input
                                        id="resume-email"
                                        type="email"
                                        className="sky-input"
                                        placeholder="you@example.com"
                                        value={resumeEmail}
                                        onChange={(e) => setResumeEmail(e.target.value)}
                                    />
                                </div>
                            )}
                            <button
                                type="button"
                                className="sky-btn-primary"
                                style={{ marginTop: 8 }}
                                onClick={handleResume}
                                disabled={waitingFields.includes("phone") && !phone.trim()}
                            >
                                Continue Booking →
                            </button>
                        </>
                    )}
                </div>
            )}

            {/* Error banner */}
            {error && status === "error" && (
                <div className="booking-error-banner" style={{ marginTop: 12 }}>
                    {error}
                    <button type="button" className="sky-btn-secondary" style={{ marginTop: 8, display: "block" }} onClick={() => setStatus("idle")}>
                        Try Again
                    </button>
                </div>
            )}

            {/* Live screenshot */}
            {(screenshot || isActive) && (
                <div className="booking-screenshot-section">
                    <div className="booking-section-header">
                        <span className="booking-section-title">🖥️ Live Browser View</span>
                        {isActive && <span className="agent-feed-pulse" />}
                    </div>
                    <div className="booking-screenshot-container">
                        {screenshot ? (
                            <img src={`data:image/png;base64,${screenshot}`} alt="Current browser state" className="booking-screenshot" />
                        ) : (
                            <div className="booking-screenshot-placeholder">
                                <span className="booking-screenshot-spinner">⟳</span>
                                <span>Waiting for first screenshot...</span>
                            </div>
                        )}
                    </div>
                </div>
            )}

            {/* Live logs */}
            {logs.length > 0 && (
                <div className="booking-logs-section">
                    <div className="booking-section-header">
                        <span className="booking-section-title">
                            {isActive ? "🤖 Agent is working..." : "Agent activity log"}
                        </span>
                        {isActive && <span className="agent-feed-pulse" />}
                        {!isActive && <span className="agent-feed-badge agent-feed-badge--count">{logs.length} entries</span>}
                    </div>
                    <div className="booking-logs-list">
                        {logs.map((log) => (
                            <div key={log.id} className={`booking-log-item booking-log-item--${log.type}`}>
                                <span className="booking-log-time">{formatTime(log.timestamp)}</span>
                                <span className="booking-log-message">{log.message}</span>
                            </div>
                        ))}
                        {isActive && (
                            <div className="booking-log-item booking-log-item--thinking">
                                <span className="booking-log-time">{formatTime(new Date())}</span>
                                <span className="booking-log-message booking-log-thinking">💭 Processing...</span>
                            </div>
                        )}
                    </div>
                </div>
            )}

            {/* Result */}
            {result && (
                <div className={`booking-result booking-result--${result.success ? "success" : "error"}`}>
                    <div className="booking-result-header">
                        <span className="booking-result-icon">{result.success ? "✅" : "❌"}</span>
                        <span className="booking-result-title">{result.success ? "Booking Confirmed!" : "Booking Failed"}</span>
                    </div>
                    {result.success && result.details && (
                        <div className="booking-result-details">
                            <div className="booking-result-row">
                                <span className="booking-result-label">Confirmation #</span>
                                <span className="booking-result-value">{result.details.confirmation_number ?? "N/A"}</span>
                            </div>
                            <div className="booking-result-row">
                                <span className="booking-result-label">Booking Time</span>
                                <span className="booking-result-value">{result.details.booking_time ?? "N/A"}</span>
                            </div>
                            {result.details.notes && (
                                <div className="booking-result-notes">
                                    <span className="booking-result-label">Notes</span>
                                    <p>{result.details.notes}</p>
                                </div>
                            )}
                        </div>
                    )}
                    {!result.success && result.error && (
                        <div className="booking-result-error"><p>{result.error}</p></div>
                    )}
                </div>
            )}
        </div>
    );
}
