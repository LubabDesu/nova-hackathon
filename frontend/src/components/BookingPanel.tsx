// NovaSync — Booking Panel component
// Shows restaurant booking form with live agent progress via SSE.
// Connects to /api/bookings/book for automated reservation handling.

import { useState, useRef, useCallback, useEffect } from "react";
import { createPortal } from "react-dom";
import { startBookingStream, resumeBooking, type BookingLogEvent, type BookingResult, type BookingStepEvent } from "../services/bookingApi";
import type { NeedsAuthEvent, NeedsCourseReviewEvent } from "../types";
import BookingStatusBadge from "./BookingStatusBadge";

interface BookingPanelProps {
    restaurantName: string;
    restaurantDescription?: string | null;
    tripLocation?: string | null;
    restaurantUrl?: string | null;
    date: string;
    time: string;
    partySize: number;
}

function stepIcon(action: BookingStepEvent["action"]): string {
    const icons: Record<string, string> = {
        navigate: "🌐",
        phase: "▶",
        log: "·",
        click: "🖱",
        type: "⌨",
    };
    return icons[action] ?? "·";
}

interface LogEntry {
    id: number;
    timestamp: Date;
    message: string;
    type: "info" | "action" | "success" | "error";
}

export default function BookingPanel({
    restaurantName,
    restaurantDescription,
    tripLocation,
    restaurantUrl,
    date,
    time,
    partySize,
}: BookingPanelProps) {
    const [phoneNumber, setPhoneNumber] = useState("");
    const [guestCount, setGuestCount] = useState(partySize);
    const [isLoading, setIsLoading] = useState(false);
    const [logs, setLogs] = useState<LogEntry[]>([]);
    const [screenshot, setScreenshot] = useState<string | null>(null);
    const [result, setResult] = useState<BookingResult | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [steps, setSteps] = useState<BookingStepEvent[]>([]);
    const [bookingId, setBookingId] = useState<string | null>(null);
    const [needsAuth, setNeedsAuth] = useState<NeedsAuthEvent | null>(null);
    const [needsCourseReview, setNeedsCourseReview] = useState<NeedsCourseReviewEvent | null>(null);
    const [isScreenshotExpanded, setIsScreenshotExpanded] = useState(false);
    const [currentUrl, setCurrentUrl] = useState("");
    const [elapsedSeconds, setElapsedSeconds] = useState(0);
    const [screenshotFlash, setScreenshotFlash] = useState(false);
    const logIdRef = useRef(0);
    const abortControllerRef = useRef<AbortController | null>(null);
    const stepsListRef = useRef<HTMLDivElement | null>(null);
    const sidebarRef = useRef<HTMLDivElement | null>(null);
    const theaterFeedRef = useRef<HTMLDivElement | null>(null);

    // Auto-scroll the step feed to the bottom whenever a new step arrives.
    useEffect(() => {
        const el = stepsListRef.current;
        if (el) el.scrollTop = el.scrollHeight;
    }, [steps]);

    // Auto-scroll the fullscreen sidebar to the bottom on new steps/logs.
    useEffect(() => {
        const el = sidebarRef.current;
        if (el) el.scrollTop = el.scrollHeight;
    }, [steps, logs]);

    // Auto-scroll the theater feed to the bottom on new steps/logs.
    useEffect(() => {
        const el = theaterFeedRef.current;
        if (el) el.scrollTop = el.scrollHeight;
    }, [steps, logs]);

    // Elapsed timer — ticks while loading, resets on stop.
    useEffect(() => {
        if (!isLoading) { setElapsedSeconds(0); return; }
        const interval = setInterval(() => setElapsedSeconds(s => s + 1), 1000);
        return () => clearInterval(interval);
    }, [isLoading]);

    // Close fullscreen modal on Escape key.
    useEffect(() => {
        if (!isScreenshotExpanded) return;
        const handler = (e: KeyboardEvent) => {
            if (e.key === "Escape") setIsScreenshotExpanded(false);
        };
        window.addEventListener("keydown", handler);
        return () => window.removeEventListener("keydown", handler);
    }, [isScreenshotExpanded]);

    const addLog = useCallback((message: string, type: LogEntry["type"] = "info") => {
        if (message.startsWith("Task:")) return;
        const id = ++logIdRef.current;
        setLogs((prev) => [...prev, { id, timestamp: new Date(), message, type }]);
    }, []);

    const formatTime = (date: Date): string => {
        return date.toLocaleTimeString("en-US", {
            hour: "2-digit",
            minute: "2-digit",
            second: "2-digit",
            hour12: false,
        });
    };

    const formatElapsed = (s: number) =>
        `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;

    const handleStartBooking = async () => {
        if (!phoneNumber.trim()) {
            setError("Please enter a phone number for the booking");
            return;
        }

        setIsScreenshotExpanded(true);

        // Reset state
        setIsLoading(true);
        setLogs([]);
        setScreenshot(null);
        setResult(null);
        setError(null);
        setSteps([]);
        setBookingId(null);
        setNeedsAuth(null);
        setNeedsCourseReview(null);
        logIdRef.current = 0;

        addLog("Initializing Nova Act booking agent...", "info");

        // Create abort controller for cancellation
        abortControllerRef.current = new AbortController();

        try {
            await startBookingStream(
                {
                    restaurant_name: restaurantName,
                    restaurant_description: restaurantDescription,
                    trip_location: tripLocation,
                    restaurant_url: restaurantUrl,
                    date,
                    time,
                    party_size: guestCount,
                    phone_number: phoneNumber,
                },
                {
                    onLog: (event: BookingLogEvent) => {
                        addLog(event.message, event.type);
                    },
                    onScreenshot: (screenshotData: string) => {
                        setScreenshot(screenshotData);
                        setScreenshotFlash(true);
                        setTimeout(() => setScreenshotFlash(false), 250);
                    },
                    onConnected: (id: string) => {
                        setBookingId(id);
                    },
                    onResult: (bookingResult: BookingResult) => {
                        setResult(bookingResult);
                        setNeedsAuth(null);
                        setNeedsCourseReview(null);
                        setIsLoading(false);
                        if (bookingResult.success) {
                            addLog("✅ Booking completed successfully!", "success");
                        } else {
                            addLog(`❌ Booking failed: ${bookingResult.error || "Unknown error"}`, "error");
                        }
                    },
                    onError: (err: string) => {
                        setError(err);
                        setIsLoading(false);
                        addLog(`❌ Error: ${err}`, "error");
                    },
                    onStep: (event) => {
                        setSteps(prev => [...prev, event]);
                        if (event.action === "navigate") {
                            const urlMatch = event.text?.match(/https?:\/\/[^\s]+/);
                            if (urlMatch) setCurrentUrl(urlMatch[0]);
                        }
                    },
                    onNeedsAuth: (event: NeedsAuthEvent) => {
                        setNeedsAuth(event);
                        addLog("🔐 Sign-in required — waiting for user action…", "action");
                    },
                    onNeedsCourseReview: (event: NeedsCourseReviewEvent) => {
                        setNeedsCourseReview(event);
                        addLog("📋 Course review required — waiting for user confirmation…", "action");
                    },
                },
                abortControllerRef.current.signal
            );
        } catch (err) {
            const errorMessage = err instanceof Error ? err.message : "Booking request failed";
            setError(errorMessage);
            setIsLoading(false);
            addLog(`❌ Error: ${errorMessage}`, "error");
        }
    };

    const handleCancel = () => {
        abortControllerRef.current?.abort();
        setIsLoading(false);
        addLog("Booking cancelled by user", "info");
    };

    const getStatus = (): "pending" | "confirmed" | "failed" => {
        if (result?.success) return "confirmed";
        if (result && !result.success) return "failed";
        if (isLoading) return "pending";
        return "pending";
    };

    return (
        <div className="glass-card booking-panel">
            <div className="booking-panel-header">
                <div className="booking-panel-title-row">
                    <h3 className="booking-panel-title">🍽️ Book a Table</h3>
                    <BookingStatusBadge status={getStatus()} />
                </div>
                <p className="booking-panel-subtitle">
                    Nova Act will automate the reservation for you
                </p>
            </div>

            {/* Restaurant Details */}
            <div className="booking-restaurant-info">
                <div className="booking-info-row">
                    <span className="booking-info-label">Restaurant</span>
                    <span className="booking-info-value">{restaurantName}</span>
                </div>
                <div className="booking-info-row">
                    <span className="booking-info-label">Date & Time</span>
                    <span className="booking-info-value">
                        {date} at {time}
                    </span>
                </div>
                <div className="booking-info-row" style={{ alignItems: "center" }}>
                    <span className="booking-info-label">Party Size</span>
                    <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
                        <button
                            type="button"
                            onClick={() => setGuestCount((n) => Math.max(1, n - 1))}
                            disabled={isLoading || guestCount <= 1}
                            style={{ width: 28, height: 28, borderRadius: "50%", border: "1.5px solid currentColor", background: "none", cursor: "pointer", fontSize: "1rem", lineHeight: 1 }}
                        >−</button>
                        <span className="booking-info-value">{guestCount} {guestCount === 1 ? "person" : "people"}</span>
                        <button
                            type="button"
                            onClick={() => setGuestCount((n) => Math.min(20, n + 1))}
                            disabled={isLoading || guestCount >= 20}
                            style={{ width: 28, height: 28, borderRadius: "50%", border: "1.5px solid currentColor", background: "none", cursor: "pointer", fontSize: "1rem", lineHeight: 1 }}
                        >+</button>
                    </div>
                </div>
                {restaurantDescription && (
                    <div className="booking-info-row" style={{ flexDirection: "column", gap: "0.25rem" }}>
                        <span className="booking-info-label">Looking for</span>
                        <span className="booking-info-value" style={{ fontStyle: "italic", fontSize: "0.85em", opacity: 0.85 }}>
                            {restaurantDescription}
                        </span>
                    </div>
                )}
                {restaurantUrl && (
                    <a
                        href={restaurantUrl}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="booking-url-link"
                    >
                        View restaurant page →
                    </a>
                )}
            </div>

            {/* Phone Input */}
            <div className="booking-phone-section">
                <label className="booking-phone-label" htmlFor="phone-input">
                    Phone Number <span className="booking-required">*</span>
                    <span className="booking-phone-hint">(required by Japanese restaurants)</span>
                </label>
                <input
                    id="phone-input"
                    type="tel"
                    className="sky-input"
                    placeholder="+81 90 1234 5678"
                    value={phoneNumber}
                    onChange={(e) => setPhoneNumber(e.target.value)}
                    disabled={isLoading}
                />
                {error && !phoneNumber.trim() && (
                    <span className="booking-error-text">{error}</span>
                )}
            </div>

            {/* Action Buttons */}
            <div className="booking-actions">
                {isLoading ? (
                    <button
                        type="button"
                        className="sky-btn-danger"
                        onClick={handleCancel}
                    >
                        ⏹ Cancel Booking
                    </button>
                ) : (
                    <button
                        type="button"
                        className="sky-btn-primary"
                        onClick={handleStartBooking}
                        disabled={!phoneNumber.trim()}
                    >
                        🤖 Book with Nova Act
                    </button>
                )}
            </div>

            {/* Auth Pause Card — shown when Nova Act hits a sign-in wall */}
            {needsAuth && (
                <div className="booking-auth-card">
                    <span>🔐 Sign-in Required</span>
                    <p>{needsAuth.message}</p>
                    <p style={{ fontSize: "0.8em", opacity: 0.75, margin: "0.25rem 0 0" }}>
                        Sign in directly in the Nova Act browser window on your screen.
                    </p>
                    <button
                        type="button"
                        onClick={async () => {
                            if (!bookingId) return;
                            await resumeBooking(bookingId);
                            setNeedsAuth(null);
                            addLog("Resuming booking after sign-in…", "info");
                        }}
                    >
                        I've signed in — Continue
                    </button>
                </div>
            )}

            {/* Course Review Card — shown when Nova Act has filled all fields and awaits human confirm */}
            {needsCourseReview && (
                <div className="booking-auth-card">
                    <span>📋 Review &amp; Confirm Booking</span>
                    <p>{needsCourseReview.message}</p>
                    {needsCourseReview.summary && (
                        <details style={{ marginTop: "0.5rem", fontSize: "0.82em", opacity: 0.85 }}>
                            <summary style={{ cursor: "pointer" }}>What Nova Act filled in</summary>
                            <pre style={{ whiteSpace: "pre-wrap", marginTop: "0.4rem", fontFamily: "inherit" }}>
                                {needsCourseReview.summary}
                            </pre>
                        </details>
                    )}
                    <button
                        type="button"
                        onClick={async () => {
                            if (!bookingId) return;
                            await resumeBooking(bookingId);
                            setNeedsCourseReview(null);
                            addLog("Resuming after booking confirmation…", "info");
                        }}
                    >
                        I've confirmed — Continue
                    </button>
                </div>
            )}

            {/* Error Message */}
            {error && phoneNumber.trim() && (
                <div className="booking-error-banner">{error}</div>
            )}

            {/* Live Screenshot */}
            {(screenshot || isLoading) && (
                <div className="booking-screenshot-section">
                    <div className="booking-section-header">
                        <span className="booking-section-title">🖥️ Live Browser View</span>
                        {isLoading && <span className="agent-feed-pulse" />}
                        {screenshot && (
                            <button
                                type="button"
                                className="booking-screenshot-expand-btn"
                                onClick={() => setIsScreenshotExpanded(true)}
                                title="Expand to fullscreen"
                            >
                                ⛶ Expand
                            </button>
                        )}
                    </div>
                    <div className="booking-screenshot-container">
                        {screenshot ? (
                            <img
                                src={`data:image/jpeg;base64,${screenshot}`}
                                alt="Current browser state"
                                className="booking-screenshot"
                                onClick={() => setIsScreenshotExpanded(true)}
                            />
                        ) : (
                            <div className="booking-screenshot-placeholder">
                                <span className="booking-screenshot-spinner">⟳</span>
                                <span>Waiting for first screenshot...</span>
                            </div>
                        )}
                    </div>
                </div>
            )}

            {/* Operations Theater — full-viewport portal */}
            {isScreenshotExpanded && createPortal(
                <div className="bt-overlay" onClick={() => setIsScreenshotExpanded(false)}>
                    {/* Top bar */}
                    <div className="bt-topbar" onClick={e => e.stopPropagation()}>
                        {isLoading && (
                            <span className="bt-live-badge">
                                <span className="bt-live-dot" />
                                LIVE
                            </span>
                        )}
                        <span className="bt-topbar-brand">Nova Act</span>
                        <span className="bt-topbar-sep">·</span>
                        <span className="bt-topbar-restaurant">{restaurantName}</span>
                        <span className="bt-topbar-meta">
                            {guestCount} {guestCount === 1 ? "guest" : "guests"} · {date} {time}
                        </span>
                        {isLoading && (
                            <span className="bt-elapsed">{formatElapsed(elapsedSeconds)}</span>
                        )}
                        <button
                            type="button"
                            className="bt-close"
                            onClick={() => setIsScreenshotExpanded(false)}
                            title="Close (Esc)"
                        >✕</button>
                    </div>

                    {/* Split body */}
                    <div className="bt-body" onClick={e => e.stopPropagation()}>
                        {/* Left: browser frame + screenshot */}
                        <div className="bt-viewport">
                            <div className="bt-browser-chrome">
                                <span className="bt-traffic-lights">
                                    <span /><span /><span />
                                </span>
                                <span className="bt-url-bar">
                                    {currentUrl || "nova-act://initializing…"}
                                </span>
                                {isLoading && <span className="bt-loading-ring" />}
                            </div>
                            <div className={`bt-screenshot-wrap${screenshotFlash ? " bt-screenshot-wrap--flash" : ""}`}>
                                {screenshot ? (
                                    <img
                                        src={`data:image/jpeg;base64,${screenshot}`}
                                        alt="Live browser view"
                                        className="bt-screenshot"
                                    />
                                ) : (
                                    <div className="bt-placeholder">
                                        <div className="bt-shimmer" />
                                        <span>Waiting for first frame…</span>
                                    </div>
                                )}
                            </div>
                        </div>

                        {/* Right: activity feed */}
                        <div className="bt-feed">
                            <div className="bt-feed-header">
                                <span>⚡ Live Actions</span>
                                {isLoading && <span className="bt-feed-pulse" />}
                            </div>
                            <div className="bt-feed-entries" ref={theaterFeedRef}>
                                {steps.map((step, i) => (
                                    <div key={i} className={`bt-entry bt-entry--${step.action}`}>
                                        <span className="bt-entry-icon">{stepIcon(step.action)}</span>
                                        <span className="bt-entry-text">{step.text}</span>
                                    </div>
                                ))}
                                {logs.map(log => (
                                    <div key={log.id} className={`bt-entry bt-entry--log bt-entry--${log.type}`}>
                                        <span className="bt-entry-time">{formatTime(log.timestamp)}</span>
                                        <span className="bt-entry-text">{log.message}</span>
                                    </div>
                                ))}
                                {isLoading && (
                                    <div className="bt-entry bt-entry--thinking">
                                        <span className="bt-entry-icon">
                                            <span className="bt-dots"><span/><span/><span/></span>
                                        </span>
                                        <span className="bt-entry-text">Processing…</span>
                                    </div>
                                )}
                            </div>

                            {/* HITL cards at feed bottom */}
                            {needsAuth && (
                                <div className="bt-hitl-card">
                                    <div className="bt-hitl-title">🔐 Sign-in Required</div>
                                    <p className="bt-hitl-msg">{needsAuth.message}</p>
                                    <p className="bt-hitl-msg" style={{ fontSize: "0.8em", opacity: 0.75, marginTop: "0.25rem" }}>
                                        The Nova Act browser window is open on your screen — sign in directly there.
                                    </p>
                                    <button type="button" className="bt-hitl-btn"
                                        onClick={async () => {
                                            if (!bookingId) return;
                                            await resumeBooking(bookingId);
                                            setNeedsAuth(null);
                                            addLog("Resuming after sign-in…", "info");
                                        }}>
                                        I've signed in — Continue
                                    </button>
                                </div>
                            )}
                            {needsCourseReview && (
                                <div className="bt-hitl-card">
                                    <div className="bt-hitl-title">📋 Review Booking</div>
                                    <p className="bt-hitl-msg">{needsCourseReview.message}</p>
                                    <button type="button" className="bt-hitl-btn"
                                        onClick={async () => {
                                            if (!bookingId) return;
                                            await resumeBooking(bookingId);
                                            setNeedsCourseReview(null);
                                            addLog("Resuming after confirmation…", "info");
                                        }}>
                                        Confirmed — Continue
                                    </button>
                                </div>
                            )}
                        </div>
                    </div>
                </div>,
                document.body
            )}

            {/* Live Actions Step Feed */}
            {steps.length > 0 && (
                <div className="booking-steps-section">
                    <div className="booking-section-header">
                        <span className="booking-section-title">⚡ Live Actions</span>
                        {isLoading && <span className="agent-feed-pulse" />}
                    </div>
                    <div className="booking-steps-list" ref={stepsListRef}>
                        {steps.map((step, i) => (
                            <div
                                key={i}
                                className={`booking-step booking-step--${step.action}`}
                            >
                                <span className="booking-step-icon">
                                    {stepIcon(step.action)}
                                </span>
                                <span className="booking-step-text">{step.text}</span>
                            </div>
                        ))}
                    </div>
                </div>
            )}

            {/* Live Logs Feed */}
            {(logs.length > 0 || isLoading) && (
                <div className="booking-logs-section">
                    <div className="booking-section-header">
                        <span className="booking-section-title">
                            {isLoading ? "🤖 Agent is working..." : "Agent activity log"}
                        </span>
                        {isLoading && <span className="agent-feed-pulse" />}
                        {!isLoading && logs.length > 0 && (
                            <span className="agent-feed-badge agent-feed-badge--count">
                                {logs.length} entries
                            </span>
                        )}
                    </div>
                    <div className="booking-logs-list">
                        {logs.map((log) => (
                            <div
                                key={log.id}
                                className={`booking-log-item booking-log-item--${log.type}`}
                            >
                                <span className="booking-log-time">{formatTime(log.timestamp)}</span>
                                <span className="booking-log-message">{log.message}</span>
                            </div>
                        ))}
                        {isLoading && (
                            <div className="booking-log-item booking-log-item--thinking">
                                <span className="booking-log-time">{formatTime(new Date())}</span>
                                <span className="booking-log-message booking-log-thinking">
                                    💭 Processing...
                                </span>
                            </div>
                        )}
                    </div>
                </div>
            )}

            {/* Final Result */}
            {result && (
                <div className={`booking-result booking-result--${result.success ? "success" : "error"}`}>
                    <div className="booking-result-header">
                        <span className="booking-result-icon">
                            {result.success ? "✅" : "❌"}
                        </span>
                        <span className="booking-result-title">
                            {result.success ? "Booking Confirmed!" : "Booking Failed"}
                        </span>
                    </div>
                    {result.success && result.details && (
                        <div className="booking-result-details">
                            <div className="booking-result-row">
                                <span className="booking-result-label">Confirmation #</span>
                                <span className="booking-result-value">
                                    {result.details.confirmation_number || "N/A"}
                                </span>
                            </div>
                            <div className="booking-result-row">
                                <span className="booking-result-label">Booking Time</span>
                                <span className="booking-result-value">
                                    {result.details.booking_time || "N/A"}
                                </span>
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
                        <div className="booking-result-error">
                            <p>{result.error}</p>
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}
