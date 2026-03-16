import { type FormEvent, useEffect, useRef, useState } from "react";
import { useParams, useNavigate, useLocation } from "react-router-dom";
import { useAuth } from "../contexts/AuthContext";
import AgentActionFeed from "../components/AgentActionFeed";
import BookingPanel from "../components/BookingPanel";
import { getActivityStyle } from "../lib/activityStyle";
import type { AgentActionEvent, ItineraryNode } from "../types";
import "../styles/sky-theme.css";
import "../styles/trip-page.css";

const API_BASE = import.meta.env.VITE_API_BASE ?? "http://localhost:8000/api";

// ── Helpers ────────────────────────────────────────────────────────────────────

function nodeIdentity(node: ItineraryNode): string {
    return node.id ?? [node.date_local, node.start_time_local, node.title].join("|");
}

function groupByDate(nodes: ItineraryNode[]): { date: string; nodes: ItineraryNode[] }[] {
    const map = new Map<string, ItineraryNode[]>();
    for (const node of nodes) {
        const key = node.date_local ?? "Unscheduled";
        const existing = map.get(key) ?? [];
        map.set(key, [...existing, node]);
    }
    return Array.from(map.entries())
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([date, dayNodes]) => ({ date, nodes: dayNodes }));
}

function formatDate(dateStr: string): string {
    if (dateStr === "Unscheduled") return "Unscheduled";
    try {
        return new Date(dateStr + "T12:00:00").toLocaleDateString("en-US", {
            weekday: "long",
            month: "long",
            day: "numeric",
        });
    } catch {
        return dateStr;
    }
}

function formatDuration(mins: number | null): string {
    if (mins === null || mins <= 0) return "";
    if (mins < 60) return `${mins}m`;
    const h = Math.floor(mins / 60);
    const m = mins % 60;
    return m > 0 ? `${h}h ${m}m` : `${h}h`;
}

function isBookable(activityType: string): boolean {
    const t = activityType.toLowerCase();
    return (
        t.includes("food") ||
        t.includes("restaurant") ||
        t.includes("dining") ||
        t.includes("cafe") ||
        t.includes("café") ||
        t.includes("izakaya") ||
        t.includes("sushi") ||
        t.includes("ramen")
    );
}

function isTransferNode(node: ItineraryNode): boolean {
    return (
        node.segment_kind === "transfer" ||
        node.segment_kind === "buffer"
    );
}

// ── Scratchpad parsing ─────────────────────────────────────────────────────────

interface ChecklistItem {
    text: string;
    checked: boolean;
}

function parseChecklist(scratchpad: string): ChecklistItem[] {
    return scratchpad
        .split("\n")
        .filter((line) => /^\[[ xX]\]/i.test(line.trim()))
        .map((line) => {
            const trimmed = line.trim();
            const checked = /^\[[xX]\]/i.test(trimmed);
            const text = trimmed.replace(/^\[[ xX]\]\s*/i, "").trim();
            return { text, checked };
        });
}

function parseConsiderations(scratchpad: string): string[] {
    return scratchpad
        .split("\n")
        .filter((line) => {
            const lower = line.toLowerCase();
            return (
                lower.startsWith("note:") ||
                lower.includes("budget") ||
                lower.includes("pace") ||
                lower.includes("dietary")
            );
        })
        .map((line) => line.trim())
        .filter(Boolean);
}

// ── State types ────────────────────────────────────────────────────────────────

interface ChatState {
    selectedNodeId: string | null;
    message: string;
    isSending: boolean;
}

interface SidebarSections {
    checklist: boolean;
    considerations: boolean;
    toolCalls: boolean;
}

// ── Component ─────────────────────────────────────────────────────────────────

interface NavigateState {
    initialNodes?: ItineraryNode[];
    agentScratchpad?: string;
    agentActions?: AgentActionEvent[];
    tripLocation?: string | null;
    groupSize?: number | null;
}

export default function TripPage() {
    const { id: tripId } = useParams<{ id: string }>();
    const { session } = useAuth();
    const navigate = useNavigate();
    const location = useLocation();

    // Read initial data passed via navigate state (from PlanPage after extraction)
    const locState = (location.state ?? null) as NavigateState | null;
    const hasInitialNodes = Boolean(locState?.initialNodes?.length);

    const [nodes, setNodes] = useState<ItineraryNode[]>(locState?.initialNodes ?? []);
    const [loading, setLoading] = useState(!hasInitialNodes);
    const [error, setError] = useState<string | null>(null);

    const [agentActions, setAgentActions] = useState<AgentActionEvent[]>(locState?.agentActions ?? []);
    const [agentScratchpad, setAgentScratchpad] = useState<string>(locState?.agentScratchpad ?? "");

    // Booking panel state
    const [bookingNode, setBookingNode] = useState<ItineraryNode | null>(null);
    const [tripLocation, setTripLocation] = useState<string | null>(locState?.tripLocation ?? null);

    const [chat, setChat] = useState<ChatState>({
        selectedNodeId: null,
        message: "",
        isSending: false,
    });
    const [chatError, setChatError] = useState<string | null>(null);

    const [sidebarSections, setSidebarSections] = useState<SidebarSections>({
        checklist: true,
        considerations: false,
        toolCalls: false,
    });

    // ── Load nodes from API ───────────────────────────────────────────────────
    // Skip the fetch if we already have nodes from navigate state (just navigated from PlanPage).
    // This avoids a round-trip and prevents the empty-state flash.

    const retryCountRef = useRef(0);
    const MAX_RETRIES = 2;
    const RETRY_DELAY_MS = 3000;

    useEffect(() => {
        if (!tripId) return;
        if (hasInitialNodes) return; // already populated from navigate state

        let cancelled = false;
        const token = session?.access_token;
        const headers: Record<string, string> = token
            ? { Authorization: `Bearer ${token}` }
            : {};

        const fetchNodes = () => {
            fetch(`${API_BASE}/trips/${tripId}/nodes`, { headers })
                .then(async (res) => {
                    if (!res.ok) {
                        const err = await res.json().catch(() => ({ detail: res.statusText }));
                        throw new Error(err.detail ?? "Failed to load trip");
                    }
                    return res.json();
                })
                .then((data: ItineraryNode[]) => {
                    if (cancelled) return;
                    if (data.length === 0 && retryCountRef.current < MAX_RETRIES) {
                        // Nodes may not have been committed yet — retry after a delay
                        retryCountRef.current += 1;
                        console.log(`[TripPage] 0 nodes for ${tripId}, retry ${retryCountRef.current}/${MAX_RETRIES} in ${RETRY_DELAY_MS}ms`);
                        setTimeout(() => { if (!cancelled) fetchNodes(); }, RETRY_DELAY_MS);
                        return;
                    }
                    setNodes(data);
                    setLoading(false);
                })
                .catch((err) => {
                    if (cancelled) return;
                    setError(err instanceof Error ? err.message : "Failed to load trip");
                    setLoading(false);
                });
        };

        fetchNodes();
        return () => { cancelled = true; };
    }, [tripId, session, hasInitialNodes]);

    // ── Fetch trip metadata (trip_location) when not in navigate state ───────
    useEffect(() => {
        if (!tripId || tripLocation !== null) return;
        const token = session?.access_token;
        const headers: Record<string, string> = token ? { Authorization: `Bearer ${token}` } : {};
        fetch(`${API_BASE}/trips/${tripId}`, { headers })
            .then(res => res.ok ? res.json() : null)
            .then(data => { if (data?.trip_location) setTripLocation(data.trip_location); })
            .catch(() => { /* non-critical */ });
    }, [tripId, session, tripLocation]);

    // ── Load agent research data from sessionStorage ──────────────────────────
    // Only reads if navigate state didn't already supply these values.

    useEffect(() => {
        if (!tripId) return;
        // If navigate state already gave us scratchpad/actions, skip sessionStorage
        if (locState?.agentScratchpad || locState?.agentActions?.length) return;
        try {
            const raw = sessionStorage.getItem(`nova_session_${tripId}`);
            if (raw) {
                const parsed = JSON.parse(raw);
                setAgentActions(parsed.agentActions ?? []);
                setAgentScratchpad(parsed.agentScratchpad ?? "");
            }
        } catch {
            /* ignore parse errors */
        }
    }, [tripId]);

    // ── Chat submit ───────────────────────────────────────────────────────────

    const handleChatSubmit = async (e: FormEvent) => {
        e.preventDefault();
        if (!chat.message.trim() || !tripId) return;
        setChatError(null);
        setChat((prev) => ({ ...prev, isSending: true }));
        try {
            const token = session?.access_token;
            const res = await fetch(`${API_BASE}/trips/${tripId}/chat`, {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    ...(token ? { Authorization: `Bearer ${token}` } : {}),
                },
                body: JSON.stringify({
                    message: chat.message,
                    selected_node_id: chat.selectedNodeId,
                    nodes,
                }),
            });
            if (!res.ok) {
                const err = await res.json().catch(() => ({ detail: "Chat request failed." }));
                throw new Error(err.detail ?? "Chat request failed.");
            }
            const data = await res.json();
            if (data.updated_nodes) {
                setNodes(data.updated_nodes);
            }
            // Functional update preserves any in-flight typing the user did while waiting
            setChat((prev) => ({ ...prev, selectedNodeId: null, message: "", isSending: false }));
        } catch (err) {
            setChatError(err instanceof Error ? err.message : "Something went wrong. Please try again.");
            setChat((prev) => ({ ...prev, isSending: false }));
        }
    };

    // ── Sidebar toggle ────────────────────────────────────────────────────────

    const toggleSection = (key: keyof SidebarSections) => {
        setSidebarSections((prev) => ({ ...prev, [key]: !prev[key] }));
    };

    // ── Derived data ──────────────────────────────────────────────────────────

    const dayGroups = groupByDate(nodes);
    const dayCount = dayGroups.length;
    const nodeCount = nodes.filter((n) => !isTransferNode(n)).length;
    const checklist = parseChecklist(agentScratchpad);
    const considerations = parseConsiderations(agentScratchpad);

    const selectedNodeTitle = chat.selectedNodeId
        ? nodes.find((n) => nodeIdentity(n) === chat.selectedNodeId)?.title ?? null
        : null;

    // ── Sky decorations (shared) ──────────────────────────────────────────────

    const SkyDecorations = () => (
        <>
            <div className="sky-bg" />
            <div className="sky-arc sky-arc-1" />
            <div className="sky-arc sky-arc-2" />
            <div className="sky-cloud sky-cloud-1" />
            <div className="sky-cloud sky-cloud-2" />
            <div className="sky-cloud sky-cloud-3" />
            <div className="sky-cloud sky-cloud-4" />
            <div className="sky-cloud sky-cloud-5" />
            <button
                className="sky-wordmark"
                onClick={() => navigate("/dashboard")}
                style={{ background: "none", border: "none", cursor: "pointer" }}
            >
                <span className="sky-wordmark-star">✦</span>
                NovaSync
            </button>
        </>
    );

    // ── Loading state ─────────────────────────────────────────────────────────

    if (loading) {
        return (
            <>
                <SkyDecorations />
                <div className="trip-loading-inner">
                    <div style={{ display: "inline-flex", alignItems: "center", gap: 10 }}>
                        <span className="trip-loading-spinner" />
                        <span
                            style={{
                                fontSize: "1rem",
                                color: "#0b1f38",
                                fontFamily: "'DM Sans', sans-serif",
                            }}
                        >
                            Loading trip...
                        </span>
                    </div>
                </div>
            </>
        );
    }

    // ── Error state ───────────────────────────────────────────────────────────

    if (error) {
        return (
            <>
                <SkyDecorations />
                <div className="trip-error-inner">
                    <div className="trip-error-box">{error}</div>
                    <button
                        onClick={() => navigate("/dashboard")}
                        className="sky-btn-secondary"
                        style={{ marginTop: 20 }}
                    >
                        Back to dashboard
                    </button>
                </div>
            </>
        );
    }

    // ── Empty-state guard ─────────────────────────────────────────────────────

    if (nodes.length === 0) {
        return (
            <>
                <SkyDecorations />
                <div className="trip-empty-state">
                    <span className="trip-empty-icon">✦</span>
                    <h2 className="trip-empty-heading">No itinerary found</h2>
                    <p className="trip-empty-body">
                        Nova wasn't able to generate a plan for this trip. This can
                        happen if planning timed out or was interrupted.
                    </p>
                    <button className="trip-empty-back" onClick={() => navigate("/dashboard")}>
                        Back to dashboard
                    </button>
                </div>
            </>
        );
    }

    // ── Main render ───────────────────────────────────────────────────────────

    return (
        <>
            <SkyDecorations />

            <div className="trip-page-shell">
                {/* Top bar */}
                <div className="trip-topbar">
                    <button
                        className="trip-topbar-back"
                        onClick={() => navigate("/dashboard")}
                    >
                        <span>←</span> Back
                    </button>
                    <h1 className="trip-topbar-title">Your Trip</h1>
                    <div className="trip-topbar-stats">
                        <span className="trip-topbar-stat">
                            {dayCount} day{dayCount !== 1 ? "s" : ""}
                        </span>
                        <span className="trip-topbar-stat">
                            {nodeCount} activit{nodeCount !== 1 ? "ies" : "y"}
                        </span>
                    </div>
                </div>

                {/* Split layout */}
                <div className="trip-split-layout">

                    {/* ── Left sidebar ─────────────────────────────────────── */}
                    <aside className="trip-sidebar">

                        {/* Research Checklist */}
                        <div className="sidebar-section">
                            <div
                                className="sidebar-section-header"
                                onClick={() => toggleSection("checklist")}
                            >
                                <span className="sidebar-section-title">
                                    <span className="sidebar-section-title-icon">✦</span>
                                    Research Checklist
                                </span>
                                <span
                                    className={`sidebar-section-chevron ${sidebarSections.checklist ? "open" : ""}`}
                                >
                                    ▸
                                </span>
                            </div>
                            {sidebarSections.checklist && (
                                <div className="sidebar-section-body">
                                    {checklist.length === 0 ? (
                                        <span className="sidebar-placeholder">
                                            No research data available.
                                        </span>
                                    ) : (
                                        checklist.map((item, i) => (
                                            <div
                                                key={i}
                                                className={`checklist-item ${item.checked ? "checked" : ""}`}
                                            >
                                                <span
                                                    className={`checklist-icon ${item.checked ? "checked" : ""}`}
                                                >
                                                    {item.checked ? "✓" : ""}
                                                </span>
                                                <span>{item.text}</span>
                                            </div>
                                        ))
                                    )}
                                </div>
                            )}
                        </div>

                        {/* Trip Considerations */}
                        <div className="sidebar-section">
                            <div
                                className="sidebar-section-header"
                                onClick={() => toggleSection("considerations")}
                            >
                                <span className="sidebar-section-title">
                                    <span className="sidebar-section-title-icon">◈</span>
                                    Trip Considerations
                                </span>
                                <span
                                    className={`sidebar-section-chevron ${sidebarSections.considerations ? "open" : ""}`}
                                >
                                    ▸
                                </span>
                            </div>
                            {sidebarSections.considerations && (
                                <div className="sidebar-section-body">
                                    {considerations.length === 0 ? (
                                        <span className="sidebar-placeholder">
                                            No considerations noted.
                                        </span>
                                    ) : (
                                        considerations.map((item, i) => (
                                            <div key={i} className="consideration-item">
                                                {item}
                                            </div>
                                        ))
                                    )}
                                </div>
                            )}
                        </div>

                        {/* Agent Tool Calls */}
                        {agentActions.length > 0 && (
                            <div className="sidebar-section">
                                <div
                                    className="sidebar-section-header"
                                    onClick={() => toggleSection("toolCalls")}
                                >
                                    <span className="sidebar-section-title">
                                        <span className="sidebar-section-title-icon">⚙</span>
                                        Agent Tool Calls
                                    </span>
                                    <span
                                        className={`sidebar-section-chevron ${sidebarSections.toolCalls ? "open" : ""}`}
                                    >
                                        ▸
                                    </span>
                                </div>
                                {sidebarSections.toolCalls && (
                                    <div className="sidebar-section-body">
                                        <AgentActionFeed
                                            actions={agentActions}
                                            isActive={false}
                                            scratchpad={agentScratchpad}
                                        />
                                    </div>
                                )}
                            </div>
                        )}
                    </aside>

                    {/* ── Right panel ──────────────────────────────────────── */}
                    <div className="trip-main">
                        <div className="trip-main-scroll">
                            {dayGroups.length === 0 ? (
                                <div className="timeline-empty">No itinerary yet.</div>
                            ) : (
                                dayGroups.map(({ date, nodes: dayNodes }, di) => (
                                    <div key={date} className="timeline-day">
                                        <div className="trip-day-header">
                                            <span className="trip-day-header-num">
                                                Day {di + 1}
                                            </span>
                                            {formatDate(date)}
                                        </div>

                                        <div className="timeline-line-wrap">
                                            {dayNodes.map((node) => {
                                                const identity = nodeIdentity(node);
                                                const selected = chat.selectedNodeId === identity;

                                                if (isTransferNode(node)) {
                                                    return (
                                                        <div key={identity} className="timeline-node-wrap">
                                                            <div className="timeline-transfer">
                                                                <span className="timeline-transfer-arrow">→</span>
                                                                <span className="timeline-transfer-label">
                                                                    {node.title}
                                                                </span>
                                                            </div>
                                                        </div>
                                                    );
                                                }

                                                const style = getActivityStyle(node.activity_type);
                                                const hasTime =
                                                    node.start_time_local || node.end_time_local;
                                                const timeLabel =
                                                    node.start_time_local && node.end_time_local
                                                        ? `${node.start_time_local} – ${node.end_time_local}`
                                                        : node.start_time_local ?? node.end_time_local ?? "";

                                                return (
                                                    <div key={identity} className="timeline-node-wrap">
                                                        <span className="timeline-dot" />
                                                        <div
                                                            className={`trip-activity-card${selected ? " selected" : ""}`}
                                                            style={{ borderLeftColor: style.color }}
                                                            onClick={() =>
                                                                setChat((prev) => ({
                                                                    ...prev,
                                                                    selectedNodeId:
                                                                        prev.selectedNodeId === identity
                                                                            ? null
                                                                            : identity,
                                                                }))
                                                            }
                                                        >
                                                            {hasTime && (
                                                                <div className="time-chip">{timeLabel}</div>
                                                            )}
                                                            <div className="trip-card-header">
                                                                <span className="trip-card-emoji">
                                                                    {style.emoji}
                                                                </span>
                                                                <div className="trip-card-body">
                                                                    <div className="trip-card-title">
                                                                        {node.title}
                                                                    </div>
                                                                    <div className="trip-card-meta">
                                                                        <span className="trip-card-type">
                                                                            {node.activity_type.replace(/_/g, " ")}
                                                                        </span>
                                                                        {node.duration_mins !== null &&
                                                                            node.duration_mins > 0 && (
                                                                                <span className="duration-badge">
                                                                                    {formatDuration(node.duration_mins)}
                                                                                </span>
                                                                            )}
                                                                    </div>
                                                                    {node.description && (
                                                                        <div className="trip-card-desc">
                                                                            {node.description}
                                                                        </div>
                                                                    )}
                                                                </div>
                                                            </div>
                                                            {isBookable(node.activity_type) && (
                                                                <div className="trip-card-actions">
                                                                    <button
                                                                        className="trip-book-btn"
                                                                        onClick={(e) => {
                                                                            e.stopPropagation();
                                                                            setBookingNode(node);
                                                                        }}
                                                                    >
                                                                        Book
                                                                    </button>
                                                                </div>
                                                            )}
                                                        </div>
                                                    </div>
                                                );
                                            })}
                                        </div>
                                    </div>
                                ))
                            )}
                        </div>

                        {/* Chat bar */}
                        <div className="trip-chat-bar">
                            {chatError && (
                                <div className="trip-chat-error">{chatError}</div>
                            )}
                            {selectedNodeTitle && (
                                <div className="trip-chat-reference">
                                    <span>@{selectedNodeTitle}</span>
                                    <button
                                        className="trip-chat-reference-dismiss"
                                        onClick={() =>
                                            setChat((prev) => ({ ...prev, selectedNodeId: null }))
                                        }
                                        aria-label="Clear reference"
                                    >
                                        ×
                                    </button>
                                </div>
                            )}
                            <form className="trip-chat-form" onSubmit={handleChatSubmit}>
                                <input
                                    className="trip-chat-input"
                                    placeholder="Chat with your plan... (e.g. 'Make this vegan-friendly')"
                                    value={chat.message}
                                    onChange={(e) =>
                                        setChat((prev) => ({ ...prev, message: e.target.value }))
                                    }
                                    disabled={chat.isSending}
                                />
                                <button
                                    type="submit"
                                    className="trip-chat-submit"
                                    disabled={!chat.message.trim() || chat.isSending}
                                    aria-label="Send message"
                                >
                                    {chat.isSending ? "…" : "↵"}
                                </button>
                            </form>
                        </div>
                    </div>
                </div>
            </div>

            {/* ── Booking Panel overlay ───────────────────────────────────── */}
            {bookingNode && (
                <div
                    className="booking-overlay"
                    onClick={() => setBookingNode(null)}
                    style={{
                        position: "fixed",
                        inset: 0,
                        background: "rgba(11, 31, 56, 0.55)",
                        backdropFilter: "blur(4px)",
                        zIndex: 100,
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                        padding: "1.5rem",
                    }}
                >
                    {/* Positioning anchor for close button — overflow: visible so the
                        button is never clipped when the inner scroll box is active */}
                    <div
                        style={{ width: "100%", maxWidth: 540, position: "relative" }}
                        onClick={(e) => e.stopPropagation()}
                    >
                        <button
                            style={{
                                position: "absolute",
                                top: -12,
                                right: -12,
                                width: 32,
                                height: 32,
                                borderRadius: "50%",
                                background: "#fff",
                                border: "1px solid #e2e8f0",
                                cursor: "pointer",
                                fontSize: "1rem",
                                display: "flex",
                                alignItems: "center",
                                justifyContent: "center",
                                zIndex: 1,
                            }}
                            onClick={() => setBookingNode(null)}
                            aria-label="Close booking panel"
                        >
                            ×
                        </button>
                        {/* Scroll container — constrained to viewport height so the
                            panel never grows off-screen */}
                        <div
                            style={{
                                maxHeight: "calc(100vh - 3rem)",
                                overflowY: "auto",
                                borderRadius: "26px",
                            }}
                        >
                            <BookingPanel
                                restaurantName={bookingNode.title}
                                restaurantDescription={bookingNode.description}
                                tripLocation={tripLocation}
                                date={bookingNode.date_local ?? ""}
                                time={bookingNode.start_time_local ?? "19:00"}
                                partySize={locState?.groupSize ?? 2}
                            />
                        </div>
                    </div>
                </div>
            )}
        </>
    );
}
